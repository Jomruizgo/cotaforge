"""``run_native``: run a fragment of FreeCAD Python as ONE undoable step, measured, and rolled back when it fails.

Protocol of a step (see ``native_exec`` for the pure half, ``native_state`` for measuring, ``native_env`` for the
namespace the fragment sees):

1. compile (a syntax error touches nothing), snapshot the document (fingerprints, the content of every persistable
   property, solids, broken objects that already existed, the registry snapshot, the journal's tracked properties);
2. open a FreeCAD transaction, run the fragment with stdout captured and a soft deadline, ``doc.recompute()``;
3. measure again; an object is *broken by this step* when it is unusable after the step and either was not before or
   differs from its pre-step self (a problem the step did not touch is not blamed on it);
4. if the fragment raised, timed out, or broke something: ``abortTransaction`` + recompute, undo any step the fragment
   opened by itself, and verify the fingerprints equal the pre-step ones (``rolled_back`` is only true when they do);
5. otherwise seal the step's ``journal.Journal`` (objects created, tracked properties changed) plus the
   ``native_undo.Restorer`` (content of what the step changed or deleted), ``commitTransaction`` and register the step
   as a ``native`` feature: ``undo_last`` reverts it from its journal like any other op.

Why the journal and not FreeCAD's undo stack: the stack keeps 20 transactions and is cleared by every journal revert,
so an undo that relied on it would be capped at 20 steps and would break after any other undo. A failed step is
different: nothing was committed yet, so the transaction abort plus the fingerprint comparison is the proof that the
document equals its pre-step self. (A saveCopy restore would replace the document object and invalidate the registry
and every handle the other tools hold.)
"""

import contextlib
import time
from typing import Any

import fc
import gui
import journal
import native_env
import native_exec
import native_state
import native_undo
import registry as reg
import sketch_defined
from protocol import invalid_request, operation_id_of

ROLLBACK_UNDO_GUARD = 100


def _summary(name: str, fp: dict[str, Any]) -> dict[str, str]:
    return {"name": name, "label": str(fp.get("label", name)), "type_id": str(fp.get("type", ""))}


def _bbox(box: list[float] | None) -> dict[str, list[float]] | None:
    if box is None:
        return None
    return {"min": box[:3], "max": box[3:]}


def _hint_for_broken(reasons: list[str]) -> str:
    text = " | ".join(reasons)
    hints = []
    if "sketch with" in text:
        hints.append("Remove or fix the conflicting/redundant constraints.")
    if sketch_defined.NOT_DEFINED in text or sketch_defined.HAS_BLOCK in text:
        hints.append(
            "A feature needs a sketch fully defined by dimensions (DoF 0, no Block constraints): run a run_native "
            "step that adds the dimensions to that sketch (a fragment that builds the feature can add them first), "
            "then repeat this step; a sketch no feature uses yet may stay under defined."
        )
    if "boolean produced an empty result" in text:
        hints.append("The boolean operands do not overlap as intended: check positions and sizes of base and tool.")
    if "null shape" in text:
        hints.append("The object has no shape: assign a Shape or build it with a feature that produces one.")
    if "zero or negative volume" in text:
        hints.append("A solid with non-positive volume is inside-out or degenerate: check profile winding and sizes.")
    if "invalid shape" in text:
        hints.append("The geometry check failed: look for self-intersections or degenerate edges.")
    if "recompute error" in text:
        hints.append(
            "FreeCAD could not recompute the object; its message is above. "
            "A deleted base feature leaves its dependents invalid."
        )
    return " ".join(hints) or "Inspect the objects listed in 'broken'."


def _rollback(
    doc: reg.DocumentRec,
    snapshot: reg.Snapshot,
    undo_before: int,
    before_fp: dict[str, dict[str, Any]],
) -> tuple[bool, list[str]]:
    """Restore the pre-step document; returns ``(verified, differences)``."""
    fc_doc = doc.fc_doc
    try:
        if fc.transaction_open.get(fc_doc.Name):
            fc.abort_transaction(fc_doc)
        guard = 0
        while fc_doc.UndoCount > undo_before and guard < ROLLBACK_UNDO_GUARD:  # steps the fragment opened itself
            fc_doc.undo()
            guard += 1
        fc.recompute(fc_doc)
    finally:
        doc.restore(snapshot)
        for body in doc.bodies.values():
            body.invalidate()
    differences = native_state.fingerprint_diff(before_fp, native_state.document_fingerprint(fc_doc))
    return not differences, differences


def run_native(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    op = operation_id_of(params) or ""
    code = params.get("code")
    if not isinstance(code, str):
        raise invalid_request("run_native needs 'code' (a string)", op)
    intent = str(params.get("intent") or "")
    timeout_s = float(params.get("timeout_s") or 60.0)
    if timeout_s <= 0:
        raise invalid_request("run_native: timeout_s must be positive", op)
    fc_doc = doc.fc_doc
    gui_up = gui.available()  # the visible worker: FreeCADGui can be imported, so it is no trap there
    warnings = native_exec.advisories(code, intent, gui_up)

    def finish(**fields: Any) -> dict[str, Any]:
        base: dict[str, Any] = {
            "ok": False,
            "feature": None,
            "created": [],
            "modified": [],
            "removed": [],
            "volume_delta_mm3": 0.0,
            "bbox_before": _bbox(pre_box),
            "bbox_after": _bbox(pre_box),
            "broken": [],
            "stdout": "",
            "stdout_dropped_chars": 0,
            "error": None,
            "rolled_back": False,
            "warnings": warnings,
            "duration_ms": (time.perf_counter() - started) * 1000.0,
        }
        base.update(fields)
        return base

    pre_volume, pre_box = native_state.solids_summary(fc_doc)
    code_obj, syntax_error = native_exec.compile_user_code(code)
    if code_obj is None:  # nothing ran, nothing to undo: the document is, by construction, the pre-step one
        return finish(error=syntax_error, rolled_back=True)

    before_fp = native_state.document_fingerprint(fc_doc)
    before_broken = native_state.broken_objects(fc_doc)
    undo_before = int(fc_doc.UndoCount)
    snapshot = doc.snapshot()
    tracked_before = journal.capture(fc_doc)
    content_before = native_undo.capture(fc_doc)
    fc.open_transaction(fc_doc, f"run_native:{op or 'step'}")
    try:
        outcome = native_exec.execute(
            code_obj, native_env.build_namespace(fc_doc), timeout_s, native_env.NAMESPACE_NAMES, gui_up
        )
        output = {"stdout": outcome.stdout, "stdout_dropped_chars": outcome.dropped}
        if not outcome.ok:
            rolled, diffs = _rollback(doc, snapshot, undo_before, before_fp)
            return _failed(finish, outcome.error, output, rolled, diffs)
        fc.recompute(fc_doc)
        after_fp = native_state.document_fingerprint(fc_doc)
        broken = {
            name: reasons
            for name, reasons in native_state.broken_objects(fc_doc).items()
            if name not in before_broken or after_fp.get(name) != before_fp.get(name)
        }
        # a step may leave a sketch under defined while it is work in progress, never under a feature it built
        for name, reasons in sketch_defined.unfit_uses(fc_doc, before_fp, after_fp).items():
            broken.setdefault(name, []).extend(reasons)
        if broken:
            rolled, diffs = _rollback(doc, snapshot, undo_before, before_fp)
            listed = [{"name": n, "reason": "; ".join(r)} for n, r in broken.items()]
            summary = "; ".join(f"{n}: {x['reason']}" for n, x in zip(broken, listed, strict=True))
            info = {
                "type": "BrokenResult",
                "message": native_exec.capped(f"the step left {len(broken)} object(s) broken: {summary}"),
                "line": None,
                "hint": _hint_for_broken([x["reason"] for x in listed]),
            }
            return _failed(finish, info, output, rolled, diffs, broken=listed)
        # Sealed INSIDE the try, like ``ops_common.run_op``: a failure here rolls the step back instead of leaving a
        # committed step without a journal (which could never be undone).
        sealed = journal.seal(fc_doc, tracked_before)
        restorer = native_undo.diff(fc_doc, content_before, before_fp)
        sealed.extra = restorer if (sealed.removed or restorer.touched) else None
    except BaseException:
        # a bug of the harness itself (not the fragment's): leave the document as it was, then surface it
        with contextlib.suppress(Exception):
            _rollback(doc, snapshot, undo_before, before_fp)
        raise

    fc.commit_transaction(fc_doc)
    created = [n for n in after_fp if n not in before_fp]
    removed = [n for n in before_fp if n not in after_fp]
    modified = [n for n in after_fp if n in before_fp and after_fp[n] != before_fp[n]]
    changed = bool(created or removed or modified)
    registered: reg.FeatureRec | None = None
    if changed or sealed.extra is not None:
        # a step that only toggled presentation flags (Visibility) is not a model change and is not registered
        warnings.extend(restorer.irreversible())
        registered = _register_step(doc, op, intent, code, snapshot, sealed, created, modified)
    volume, box = native_state.solids_summary(fc_doc)
    warnings.extend(_no_effect_warnings([(n, after_fp[n]["type"]) for n in created], volume - pre_volume))
    return finish(
        ok=True,
        feature={"id": registered.id} if registered is not None else None,
        created=[_summary(n, after_fp[n]) for n in created],
        modified=[_summary(n, after_fp[n]) for n in modified],
        removed=[_summary(n, before_fp[n]) for n in removed],
        volume_delta_mm3=volume - pre_volume,
        bbox_before=_bbox(pre_box),
        bbox_after=_bbox(box),
        **output,
    )


_SUBTRACTIVE_TYPES = frozenset(
    {
        "PartDesign::Pocket",
        "PartDesign::Groove",
        "PartDesign::Hole",
        "PartDesign::SubtractiveBox",
        "PartDesign::SubtractiveCylinder",
        "PartDesign::SubtractiveSphere",
        "PartDesign::SubtractiveCone",
        "PartDesign::SubtractiveEllipsoid",
        "PartDesign::SubtractiveTorus",
        "PartDesign::SubtractivePrism",
        "PartDesign::SubtractiveWedge",
    }
)
_ADDITIVE_TYPES = frozenset(
    {"PartDesign::Pad", "PartDesign::Revolution", "PartDesign::AdditiveBox", "PartDesign::AdditiveCylinder"}
)


def _no_effect_warnings(created: list[tuple[str, str]], volume_delta: float) -> list[str]:
    """Features PartDesign accepts silently although they changed nothing (a pocket outside the material, a pad that
    does not touch the body): not an error of FreeCAD's, so only a warning here."""
    out: list[str] = []
    cut = [name for name, type_id in created if type_id in _SUBTRACTIVE_TYPES]
    added = [name for name, type_id in created if type_id in _ADDITIVE_TYPES]
    if cut and volume_delta >= -native_state.VOLUME_TOL and not added:
        out.append(f"Created {', '.join(cut)} but the solid volume did not decrease: the cut may miss the material.")
    if added and not cut and volume_delta <= native_state.VOLUME_TOL:
        out.append(f"Created {', '.join(added)} but the solid volume did not increase: it may not touch the body.")
    return out


def _failed(
    finish: Any,
    info: dict[str, Any] | None,
    output: dict[str, Any],
    rolled_back: bool,
    differences: list[str],
    broken: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    extra: list[str] = []
    if not rolled_back:  # every difference: the client shows the first ones and keeps the whole list in a file
        extra.append("Rollback could not be fully verified: " + "; ".join(differences))
    result = finish(error=info, rolled_back=rolled_back, broken=broken or [], **output)
    result["warnings"] = [*result["warnings"], *extra]
    return result


def _register_step(
    doc: reg.DocumentRec,
    op: str,
    intent: str,
    code: str,
    snapshot: reg.Snapshot,
    sealed: journal.Journal,
    created: list[str],
    modified: list[str],
) -> reg.FeatureRec:
    """Make the committed step visible to ``undo_last`` (through its journal, like any op) and the feature tree (its
    created objects are known to the registry, so the tree does not list them as hand-made), and re-sync registry
    records of harness bodies the step touched."""
    from ops_common import new_feature  # noqa: PLC0415 - avoids an import cycle at module load

    known = _known_result_names(doc)  # before this step's objects join the feature
    feature = new_feature(doc, "native", {"operation_id": op, "intent": intent})
    feature.journal = sealed
    feature.fc_names.extend(n for n in sealed.created if n not in feature.fc_names)
    feature.fc_names.extend(n for n in created if n not in feature.fc_names)
    doc.undo_stack.append(snapshot)
    doc.features.append(feature)
    fc.recorder.record(f"# run_native {intent or op}".rstrip())
    fc.recorder.record("# (uses the run_native namespace: doc, App, Part, Base, Vector, ...)")
    fc.recorder.record(code.rstrip("\n"))
    touched = set(modified)
    for body in doc.live_bodies():
        body.invalidate()
        obj = doc.fc_doc.getObject(body.result_obj)
        if obj is None:
            body.deleted = True
            continue
        if body.result_obj not in touched and body.fc_body not in touched:
            continue
        shape = reg.topo_shape(doc, body)
        if not shape.isNull():
            reg.refresh(doc, body, shape, feature.id)
            if feature.id not in body.modified_by:
                body.modified_by.append(feature.id)
    feature.warnings.extend(_adopt_new_results(doc, feature, known))
    return feature


def _known_result_names(doc: reg.DocumentRec) -> set[str]:
    """Objects whose solids the registry already accounts for: every body record (deleted ones too: their object stays
    in the document), the components, and what the harness's own ops made. What an earlier ``run_native`` step created
    is NOT known unless it was adopted: it may have been an empty container that a later step fills."""
    names: set[str] = set()
    for body in doc.bodies.values():
        names.update(n for n in (body.fc_body, body.result_obj) if n)
    for comp in doc.components.values():
        names.update(n for n in (comp.fc_part, comp.fc_shape, *comp.fc_links) if n)
    for feature in doc.features:
        if feature.kind != "native":
            names.update(feature.fc_names)
            if feature.fc_main:
                names.add(feature.fc_main)
    return names


def _adopt_new_results(doc: reg.DocumentRec, feature: reg.FeatureRec, known: set[str]) -> list[str]:
    """Register as bodies the solid results the registry does not know (``native_state.solid_objects``, the very set
    the step's volume change is measured over): a loose ``Part::Box``, a boolean at the top level, a
    ``PartDesign::Body`` the fragment filled. Without a body record ``mass_properties`` and ``bounding_box`` (which
    only measure registered bodies, and so do the checks and the document summary) would not see them. They belong to
    the step's own feature, so undoing the step takes them out of the registry (the snapshot) and of the document (the
    journal) together.

    A registered body such a new result consumes (``Part::Cut`` of a registered body) is superseded by it and marked
    deleted, as the harness's own booleans do with their tools: its solid is inside the new result, and measuring both
    would count it twice. Nothing without a solid (sketches, datum planes, empty containers) is a result."""
    fc_doc = doc.fc_doc
    adopted = [obj for obj in native_state.solid_objects(fc_doc) if str(obj.Name) not in known]
    notes: list[str] = []
    for obj in adopted:
        note = reg.adopt_result(doc, obj, feature)
        if note is not None:
            notes.append(note)
    if adopted:
        consumed = {str(o.Name) for new in adopted for o in new.OutListRecursive}
        for body in doc.live_bodies():
            if body.result_obj in consumed:
                body.deleted = True
    return notes


def document_state(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Test-only: the document fingerprint (``native_state``), undo depth and solids summary."""
    fc_doc = doc.fc_doc
    volume, box = native_state.solids_summary(fc_doc)
    return {
        "objects": native_state.document_fingerprint(fc_doc),
        "undo_count": int(fc_doc.UndoCount),
        "volume_mm3": volume,
        "bbox": box,
        "features": len(doc.features),
    }
