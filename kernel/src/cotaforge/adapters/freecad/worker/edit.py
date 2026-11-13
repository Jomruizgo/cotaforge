"""In-place editing of features (``edit_feature``): the FreeCAD side of the tool.

Changing a value of an EARLIER op is, in FreeCAD, setting a property on the object that already exists and letting
``recompute()`` update what depends on it: ~1.5 s on a 78-op plan instead of ~46 s of undoing and re-running it. FreeCAD
tells the harness almost nothing about what that recompute did (it never raises, its return value is a count, and six of
the nine failure classes of the experiment leave every object ``Valid``), so the edit is wrapped in the nine guards the
experiment found necessary:

1. SCOPE. ``box``, ``cylinder`` and ``move_body`` are edited, and only when every op that depends on them is a live
   Part object (``box``/``cylinder``/``move_body``/``boolean`` without ``keep_tools``). A static copy (``copy_body``,
   ``mirror_body``, ``split``, a ``keep_tools`` subtract), PartDesign and assembly ops among the dependents (or
   after the edited op, where an implicit dependency cannot be ruled out) REFUSE the edit with a reason per
   feature; nothing is touched and the caller rebuilds instead. The logical closure comes from the registry
   (body ids in the recorded requests); it is cross-checked against FreeCAD's own dependency graph
   (``InListRecursive``): a dependent the graph does not reach is a static copy.
   RELAXED for PartDesign (``dim_edit.FEATURE_KINDS``): a feature whose change is only a DIMENSION (a pad's length, a
   hole's diameter, a fillet's radius, a pattern's count, a datum of the sketch it is built on) is edited in place,
   and its dependents are the features that work on its bodies (the BaseFeature chain). The scope guard forbade every
   PartDesign op because it could not tell which ops follow an edit; the body ids of the recorded requests now say.
   Static copies and ops of other workbenches among those dependents still refuse. What a dimension cannot say (another
   number of entities, another plane, another end condition, other edges) refuses with the reason too.
2. NEVER TRUST ``recompute()``. Every object's ``State`` is scanned afterwards (Invalid, Error, still Touched).
3. SEMANTIC CHECKS on the dependents: per body, solids, volume, box, centroid and face/edge counts against
   what they were (NOT "one solid": a frame can legitimately be two); a Cut must not raise volume; a union
   must not split; a body must not vanish; a cut that stops cutting and a new interference between bodies are reported.
4. PLACEMENT DRIFT: no object that was not edited may move.
5. ATOMICITY. Edit and recompute run in one transaction; on any failed check the transaction is aborted, the document
   recomputed, and the registry (snapshot, ``FeatureRec.params``, the undo journals) restored. The result says why.
6. EXPRESSIONS. A property bound to an expression would be overwritten by the next recompute: the edit is refused.
7. REGISTRY REBIND. Every dependent body is re-resolved (``registry.refresh``) and the faces created, removed (their
   handles are STALE from now on) and modified are reported.
8. COUNTS. Face and edge counts before and after are part of the per-body delta.
9. (Optional, not done here) comparison with a fresh rebuild in a second document.

A sketch dimension is set with ``setDatum`` on the sketch the feature already has, at the index of the constraint the
harness made: two sketches are built from scratch beside it (the old dimensions and the new ones) and the datums that
differ between them are the ones set, so a value the engineer changed by hand and the edit does not touch is never put
back, and the result is checked against the sketch built from scratch with the new dimensions (``dim_edit``). A sketch
placed on a face (and the sketch of a hole) is placed again on the face as it stands after the recompute, exactly as
a rebuild would place it.

Undo stays consistent: after an applied edit the ``FeatureRec`` requests and the Placement entries of the undo journals
(``journal.set_pose``) follow the new chain of moves, so ``undo_last`` of the edited op or of the ops after it restores
exactly what a fresh document of the surviving prefix would hold.
"""

import math
import time
from typing import Any

import dim_edit
import fc
import geometry as g
import journal
import ops_common as oc
import Part
import provenance as prov
import registry as reg
import sketch_defined
from FreeCAD import Placement, Rotation, Vector
from protocol import WorkerError, error, geometry_failed, invalid_request
from queries import face_descriptor, face_is_planar

PRIMITIVE_TYPES = {"box": "Part::Box", "cylinder": "Part::Cylinder"}
EDITABLE_KINDS = frozenset({"box", "cylinder", "move_body"})
LIVE_KINDS = frozenset({"box", "cylinder", "move_body", "boolean"})
EXPLICIT_INPUT_KINDS = frozenset({"box", "cylinder", "boolean", "move_body", "copy_body", "mirror_body", "split"})
EDITED_PROPERTIES = {
    "box": ("Length", "Width", "Height", "Placement"),
    "cylinder": ("Radius", "Height", "Placement"),
    "move_body": ("Placement",),
}
POSE_TOL = 1e-6  # mm / rad: the tolerance of ``journal`` (a recompute moves a pose by float noise)
DRIFT_TOL = 1e-9  # a pose of an object nobody edited that moves by more is a drift
VOLUME_TOL = 1e-6  # mm3: volumes closer than this are the same
REL_TOL = 1e-9
MAX_LISTED = 8  # objects/pairs named in a message (the structured result carries them all)
BAD_STATES = ("Invalid", "Error")
PENDING_STATES = ("Touched", "Recompute", "Recompute2")
TECHNICAL_FLAGS = frozenset({"placement_drift", "volume_changed"})  # invariants of the mechanism, not of the part


# --- small helpers ----------------------------------------------------------------------------------------------


def _num(value: Any) -> float:
    """A property value as a float (lengths come back as Quantities)."""
    return float(getattr(value, "Value", value))


def _pose_key(placement: Any) -> tuple[float, ...]:
    return tuple(float(x) for x in placement.toMatrix().A)


def _same_pose(a: Any, b: Any, tol: float) -> bool:
    return max(abs(x - y) for x, y in zip(_pose_key(a), _pose_key(b), strict=True)) <= tol


def _handles(ids: Any) -> list[dict[str, str]]:
    return [{"id": i} for i in ids]


def _names(items: Any) -> str:
    items = list(items)
    shown = ", ".join(str(i) for i in items[:MAX_LISTED])
    return shown + (f" and {len(items) - MAX_LISTED} more" if len(items) > MAX_LISTED else "")


# --- the request: what an edit asks of one feature ------------------------------------------------------------------


class Edit:
    """One feature to edit with its new request."""

    def __init__(
        self, rec: reg.FeatureRec, kind: str, request: dict[str, Any], obj: Any, sketch: dict[str, Any] | None = None
    ) -> None:
        self.rec = rec
        self.kind = kind
        self.request = request
        self.obj = obj
        self.sketch = sketch  # the NEW request of the sketch step the feature consumes (a dimension of it changed)
        self.sketch_obj = _profile_sketch(obj) if sketch is not None and obj is not None else None


def _profile_sketch(obj: Any) -> Any:
    """The sketch object a feature is built on (its ``Profile``)."""
    try:
        profile = obj.Profile
    except Exception:  # noqa: BLE001 - a feature without a profile has no sketch
        return None
    if isinstance(profile, (tuple, list)):
        return profile[0] if profile else None
    return profile


def _non_finite(value: Any, path: str) -> str | None:
    """The path of the first nan/inf number inside ``value`` (dicts, lists, tuples), or None."""
    if isinstance(value, float):
        return None if math.isfinite(value) else path
    if isinstance(value, dict):
        items = [(f"{path}.{key}", item) for key, item in value.items()]
    elif isinstance(value, (list, tuple)):
        items = [(f"{path}[{i}]", item) for i, item in enumerate(value)]
    else:
        return None
    return next((hit for sub, item in items if (hit := _non_finite(item, sub)) is not None), None)


def _validate(kind: str, request: dict[str, Any], operation_id: str | None) -> None:
    """Refuse a request the op itself would refuse, BEFORE anything is touched (the IR rejects them earlier, but a
    direct
        caller must not be able to hand FreeCAD a zero or negative dimension)."""
    where = _non_finite(request, kind)
    if where is not None:  # nan and inf pass every ``<= 0`` / ``> 0`` test below: none may reach a recompute
        raise invalid_request(f"{where} must be a finite number (nan and inf are refused)", operation_id)
    try:
        if kind == "box":
            size = [float(s) for s in request["size"]]
            if len(size) != 3 or not all(math.isfinite(s) for s in size) or min(size) <= 0:
                raise invalid_request("box size must be three positive numbers", operation_id)
            g.placement_from_transform(request.get("transform") or {})
        elif kind == "cylinder":
            diameter, height = float(request["diameter_mm"]), float(request["height_mm"])
            if not (math.isfinite(diameter) and math.isfinite(height)) or diameter <= 0 or height <= 0:
                raise invalid_request("cylinder dimensions must be positive", operation_id)
            axis = request["axis"]
            g.unit(g.vec(axis["direction"]))
            g.vec(axis["origin"])
        elif kind == "move_body":
            g.placement_from_transform(request.get("transform") or {})
            reg.handle_id(request.get("body"), operation_id, "body")
    except WorkerError:
        raise
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise invalid_request(f"invalid {kind} request ({type(exc).__name__}: {exc})", operation_id) from exc


def _parse(doc: reg.DocumentRec, params: dict[str, Any]) -> list[Edit]:
    operation_id = params.get("operation_id")
    raw = params.get("edits")
    if not isinstance(raw, list) or not raw:
        raise invalid_request("edit_feature needs at least one edit", operation_id)
    edits: list[Edit] = []
    seen: set[str] = set()
    for item in raw:
        feature_id = reg.handle_id(item.get("feature"), operation_id, "feature")
        if feature_id in seen:
            raise invalid_request(f"feature {feature_id} appears more than once in the edits", operation_id)
        seen.add(feature_id)
        rec = doc.feature(feature_id, operation_id)
        kind = str(item.get("kind", ""))
        request = item.get("request")
        if not isinstance(request, dict):
            raise invalid_request(f"the edit of {feature_id} carries no request", operation_id)
        _validate(kind, request, operation_id)
        sketch = item.get("sketch")
        if sketch is not None:
            if not isinstance(sketch, dict):
                raise invalid_request(f"the sketch of the edit of {feature_id} is not a request", operation_id)
            where = _non_finite(sketch, "sketch")
            if where is not None:
                raise invalid_request(f"{where} must be a finite number (nan and inf are refused)", operation_id)
        obj = doc.fc_doc.getObject(rec.fc_main) if rec.fc_main else None
        edits.append(Edit(rec, kind, request, obj, sketch))
    return edits


# --- the registry's view: which feature reads which body -------------------------------------------------------------


def _inputs(rec: reg.FeatureRec) -> set[str]:
    """The body ids an op reads, from its recorded request (complete for ``EXPLICIT_INPUT_KINDS``)."""
    found: set[str] = set()
    for name in ("target", "body", "source"):
        value = rec.params.get(name)
        if isinstance(value, dict) and isinstance(value.get("id"), str):
            found.add(value["id"])
    for tool in rec.params.get("tools") or ():
        if isinstance(tool, dict) and isinstance(tool.get("id"), str):
            found.add(tool["id"])
    return found


def _outputs(rec: reg.FeatureRec) -> set[str]:
    found = set(rec.created_bodies) | set(rec.modified_bodies)
    if rec.kind in ("boolean", "move_body"):
        found |= _inputs(rec)
    return found


def _dependents(doc: reg.DocumentRec, edit: Edit) -> tuple[list[reg.FeatureRec], set[str], str | None]:
    """``(features that depend on the edited one, bodies that depend on it, the first blocker as a reason or None)``.
    The edited feature itself is not in the list; its own bodies are in the set."""
    bodies = _outputs(edit.rec)
    found: list[reg.FeatureRec] = []
    blocker: str | None = None
    seen_edited = False
    for rec in doc.features:
        if rec is edit.rec:
            seen_edited = True
            continue
        if not seen_edited:
            continue
        explicit = rec.kind in EXPLICIT_INPUT_KINDS
        if explicit and not (_inputs(rec) & bodies):
            continue
        found.append(rec)
        if blocker is None:
            if rec.kind not in LIVE_KINDS:
                what = "a static copy" if explicit else "an op that may depend on it"
                blocker = f"{rec.label} ({rec.kind}) comes after and is {what}: it would not follow the edit"
            elif rec.kind == "boolean" and rec.params.get("keep_tools"):
                blocker = (
                    f"{rec.label} (boolean with keep_tools) keeps a static copy of its tool: it would not follow "
                    "the edit"
                )
        bodies |= _outputs(rec)
    return found, bodies, blocker


# --- poses: the chain of placements the recorded requests produce -----------------------------------------------------


def _primitive_pose(kind: str, request: dict[str, Any]) -> Any:
    if kind == "cylinder":
        axis = request["axis"]
        origin = g.vec(axis["origin"])
        direction = g.unit(g.vec(axis["direction"]))
        return Placement(g.fcvec(origin), Rotation(Vector(0, 0, 1), g.fcvec(direction)))
    return g.placement_from_transform(request.get("transform") or {})


def chain(doc: reg.DocumentRec, obj_name: str, params_of: Any) -> list[tuple[reg.FeatureRec | None, Any]]:
    """The poses ``obj_name`` goes through, recomposed from the RECORDED requests exactly as the ops composed them:
        the base pose of the box/cylinder that made it, then every ``move_body`` on it as ``T.multiply(previous)`` (what
        ``ops_primitives.move_body`` does). ``params_of(rec)`` yields the request to use for a feature (the new one for
    an edited
        feature). The last pose is the Placement the object must carry."""
    creator = next((f for f in doc.features if f.fc_main == obj_name and f.kind in PRIMITIVE_TYPES), None)
    pose = _primitive_pose(creator.kind, params_of(creator)) if creator is not None else Placement()
    poses: list[tuple[reg.FeatureRec | None, Any]] = [(creator, pose)]
    for rec in doc.features:
        if rec.kind == "move_body" and rec.fc_main == obj_name:
            pose = g.placement_from_transform(params_of(rec).get("transform") or {}).multiply(pose)
            poses.append((rec, pose))
    return poses


# --- eligibility ------------------------------------------------------------------------------------------------------


def _expression_paths(obj: Any) -> list[str]:
    try:
        return [str(entry[0]).lstrip(".") for entry in obj.ExpressionEngine]
    except Exception:  # noqa: BLE001 - an object without an expression engine has none
        return []


def _wanted(edit: Edit) -> dict[str, Any]:
    """The property values the edited request asks for (``Placement`` is decided later, from the whole chain)."""
    request = edit.request
    if edit.kind == "box":
        length, width, height = (float(s) for s in request["size"])
        return {"Length": length, "Width": width, "Height": height}
    if edit.kind == "cylinder":
        return {"Radius": float(request["diameter_mm"]) / 2.0, "Height": float(request["height_mm"])}
    return {}


def _changed_properties(edit: Edit, placement_changes: bool) -> list[str]:
    obj = edit.obj
    changed = [name for name, value in _wanted(edit).items() if abs(_num(getattr(obj, name)) - value) > 1e-12]
    if placement_changes:
        changed.append("Placement")
    return changed


def _live_closure(edits: list[Edit]) -> set[str]:
    """Object names FreeCAD's own graph reaches from the edited objects (themselves included)."""
    names: set[str] = set()
    for edit in edits:
        names.add(edit.obj.Name)
        names.update(o.Name for o in edit.obj.InListRecursive)
        if edit.sketch_obj is not None:
            names.add(edit.sketch_obj.Name)
            names.update(o.Name for o in edit.sketch_obj.InListRecursive)
    return names


class Analysis:
    def __init__(self) -> None:
        self.refused: dict[str, str] = {}
        self.dependents: dict[str, list[reg.FeatureRec]] = {}
        self.bodies: set[str] = set()
        self.objects: dict[str, str] = {}  # object name to re-pose -> why (the feature that owns it)
        self.recomputed: list[reg.FeatureRec] = []
        self.pd = False  # some edit is a PartDesign feature (its dimensions or those of its sketch)
        self.sets: dict[str, list[tuple[Any, str, Any]]] = {}  # feature id -> (object, property, value) to set
        self.moves: dict[str, list[tuple[int, str, float]]] = {}  # hole id -> datums of its sketch that move it
        self.keeps_places = False  # only holes moved, nothing refers to an edge: no sketch or edge is placed again


def analyse(doc: reg.DocumentRec, edits: list[Edit]) -> Analysis:
    """Why each edit cannot be done in place (``refused``), and what a real edit would touch."""
    result = Analysis()
    if doc.kind != "part":
        reason = f"in-place editing works on part documents, this one is an {doc.kind}"
        result.refused = {e.rec.id: reason for e in edits}
        return result
    pending = _scan_objects(doc.fc_doc)
    if pending:
        # The edit recomputes dependents of the document graph: an object that is already invalid, or Touched by a
        # change nobody recomputed, would be recomputed along with it (a hand change baked in, or a "drift" that
        # fails the edit and a rollback that cannot give the document back as it was). Say so before touching.
        listed = ", ".join(
            f"{name} ({'/'.join(state) or status})" for name, (state, status) in list(pending.items())[:MAX_LISTED]
        )
        reason = (
            f"the document has objects that are invalid or have a change that was never recomputed: {listed}"
            f"{' ...' if len(pending) > MAX_LISTED else ''}; recompute or revert them first (an in-place edit "
            "recomputes the document and would carry them along)"
        )
        result.refused = {e.rec.id: reason for e in edits}
        return result
    old_of = {e.rec.id: e.rec.params for e in edits}
    new_of = {e.rec.id: e.request for e in edits}
    graph = _live_closure([e for e in edits if e.obj is not None])
    result.pd = any(e.kind in dim_edit.FEATURE_KINDS for e in edits)
    for edit in edits:
        rec, obj = edit.rec, edit.obj
        reason: str | None = None
        if edit.kind in dim_edit.FEATURE_KINDS:
            reason = _analyse_feature(doc, edit, graph, result)
            if reason is not None:
                result.refused[rec.id] = reason
            continue
        if rec.kind != edit.kind:
            reason = f"the feature is a {rec.kind}, not a {edit.kind}"
        elif edit.kind not in EDITABLE_KINDS:
            reason = f"an in-place edit of a {edit.kind} is not supported yet"
        elif obj is None:
            reason = "the FreeCAD object of the feature no longer exists"
        elif edit.kind in PRIMITIVE_TYPES and obj.TypeId != PRIMITIVE_TYPES[edit.kind]:
            reason = f"the object {obj.Name} is a {obj.TypeId}, not a {PRIMITIVE_TYPES[edit.kind]}"
        elif edit.kind == "move_body" and not obj.TypeId.startswith("Part::"):
            reason = f"the moved object {obj.Name} is a {obj.TypeId} (PartDesign is not edited in place yet)"
        elif edit.kind == "move_body" and edit.request.get("body") != rec.params.get("body"):
            reason = "retargeting a move to another body is not an in-place edit"
        if reason is None:
            assert obj is not None
            old_poses = chain(doc, obj.Name, lambda f: old_of.get(f.id, f.params))
            if not _same_pose(old_poses[-1][1], obj.Placement, POSE_TOL):
                reason = (
                    f"the Placement of {obj.Name} no longer matches the moves recorded on it "
                    "(it was changed outside the harness)"
                )
        if reason is None:
            assert obj is not None
            new_poses = chain(doc, obj.Name, lambda f: new_of.get(f.id, f.params))
            moves = not _same_pose(new_poses[-1][1], obj.Placement, DRIFT_TOL)
            bound = [
                p
                for p in _changed_properties(edit, moves)
                for path in _expression_paths(obj)
                if path == p or path.startswith(p + ".")
            ]
            if bound:
                reason = (
                    f"the {', '.join(sorted(set(bound)))} of {obj.Name} is bound to an expression: a value set "
                    "directly "
                    "would be overwritten by the next recompute"
                )
        if reason is None:
            dependents, bodies, blocker = _dependents(doc, edit)
            if blocker is None:
                for dep in dependents:
                    dep_obj = doc.fc_doc.getObject(dep.fc_main) if dep.fc_main else None
                    if dep_obj is None or (dep_obj.Name not in graph and dep.kind != "move_body"):
                        blocker = f"{dep.label} ({dep.kind}) is not linked to {obj.Name} in the document graph"
                        break
            if blocker is not None:
                reason = blocker
            else:
                result.dependents[rec.id] = dependents
                result.bodies |= bodies
        if reason is not None:
            result.refused[rec.id] = reason
    if result.refused:
        return result
    for edit in edits:
        assert edit.obj is not None
        if edit.kind not in dim_edit.FEATURE_KINDS:
            result.objects[edit.obj.Name] = edit.rec.id
    reached = set(graph)
    wanted = {e.rec.id for e in edits}
    for rec in doc.features:
        in_graph = (rec.fc_main in reached) or any(n in reached for n in rec.fc_names)
        logical = rec.id in wanted or any(rec in deps for deps in result.dependents.values())
        if in_graph or logical:
            result.recomputed.append(rec)
    # a pure move of holes: no plane of any face changes (a cut leaves planes where they are), so every sketch on a
    # face keeps its frame, and with no fillet or chamfer among the features nothing refers to a numbered edge
    result.keeps_places = (
        bool(result.moves)
        and all(e.kind == "hole" and e.sketch is None and not result.sets.get(e.rec.id) for e in edits)
        and not any(_edge_objects(doc, rec) for rec in result.recomputed)
    )
    return result


def _named_objects(doc: reg.DocumentRec, rec: reg.FeatureRec) -> list[Any]:
    """The objects of ``rec`` that carry a dimension besides its main one, in the order the op made them (the chamfers
    of several orientations, the two linear patterns of a multi-direction pattern)."""
    wanted = {"chamfer": "PartDesign::Chamfer", "linear_pattern": "PartDesign::LinearPattern"}.get(rec.kind)
    if wanted is None:
        return []
    objs = [doc.fc_doc.getObject(name) for name in rec.fc_names]
    return [o for o in objs if o is not None and o.TypeId == wanted]


def _feature_dependents(doc: reg.DocumentRec, edit: Edit) -> tuple[list[reg.FeatureRec], set[str], str | None]:
    """The features that follow an edit of a PartDesign feature: those that work on its bodies (the BaseFeature chain of
    a body), the explicit readers of those bodies (a boolean, a move) and the first one that would not follow it."""
    bodies = _outputs(edit.rec)
    found: list[reg.FeatureRec] = []
    blocker: str | None = None
    seen_edited = False
    for rec in doc.features:
        if rec is edit.rec:
            seen_edited = True
            continue
        if not seen_edited:
            continue
        if rec.kind in EXPLICIT_INPUT_KINDS:
            if not (_inputs(rec) & bodies):
                continue
            if blocker is None and rec.kind not in LIVE_KINDS:
                blocker = f"{rec.label} ({rec.kind}) comes after and is a static copy: it would not follow the edit"
            elif blocker is None and rec.kind == "boolean" and rec.params.get("keep_tools"):
                blocker = f"{rec.label} (boolean with keep_tools) keeps a static copy of its tool: not followed"
        else:
            if not ((set(rec.created_bodies) | set(rec.modified_bodies)) & bodies):
                continue  # a feature of another body does not read this one
            if blocker is None and rec.kind not in dim_edit.FEATURE_LIVE_KINDS:
                blocker = f"{rec.label} ({rec.kind}) comes after and may depend on it: it would not follow the edit"
        found.append(rec)
        bodies |= _outputs(rec)
    return found, bodies, blocker


def _analyse_feature(doc: reg.DocumentRec, edit: Edit, graph: set[str], result: Analysis) -> str | None:
    """Why a PartDesign feature (or the sketch it is built on) cannot be edited in place, or ``None`` and what a real
    edit would set and recompute."""
    rec, obj = edit.rec, edit.obj
    if rec.kind != edit.kind:
        return f"the feature is a {rec.kind}, not a {edit.kind}"
    if obj is None:
        return "the FreeCAD object of the feature no longer exists"
    if obj.TypeId not in dim_edit.FEATURE_TYPES[edit.kind]:
        return f"the object {obj.Name} is a {obj.TypeId}, not a {' or '.join(dim_edit.FEATURE_TYPES[edit.kind])}"
    other = dim_edit.non_dimension_changes(edit.kind, rec.params, edit.request)
    if other:
        return f"{', '.join(other)} is not a dimension of the {edit.kind}"
    if edit.sketch is not None:
        sk_rec = doc.sketches.get(rec.sketch or "")
        if edit.kind not in dim_edit.SKETCH_CONSUMERS or sk_rec is None or edit.sketch_obj is None:
            return f"the sketch of {rec.label} is not one the harness made"
        why = dim_edit.sketch_refusal(sk_rec.spec, edit.sketch)
        if why is not None:
            return why
    try:
        sets = dim_edit.property_sets(edit.kind, rec.params, edit.request, obj, _named_objects(doc, rec))
    except dim_edit.NotInPlaceError as exc:
        return str(exc)
    bound = [
        f"{o.Name}.{prop}"
        for o, prop, _ in sets
        for path in _expression_paths(o)
        if path == prop or path.startswith(prop + ".")
    ]
    if bound:
        return f"{', '.join(bound)} is bound to an expression: a value set directly would be overwritten"
    moves: list[tuple[int, str, float]] = []
    if edit.kind == "hole":
        try:
            moves = _hole_moves(doc, edit)
        except dim_edit.NotInPlaceError as exc:
            return str(exc)
        held = dim_edit.bound_by_expression(_expression_paths(_profile_sketch(obj)), moves)
        if held:
            return f"{', '.join(held)} of the sketch of {rec.label} is bound to an expression"
    dependents, bodies, blocker = _feature_dependents(doc, edit)
    if blocker is None:
        for dep in dependents:
            dep_obj = doc.fc_doc.getObject(dep.fc_main) if dep.fc_main else None
            if dep_obj is None or (dep_obj.Name not in graph and dep.kind != "move_body"):
                blocker = f"{dep.label} ({dep.kind}) is not linked to {obj.Name} in the document graph"
                break
    if blocker is not None:
        return blocker
    result.dependents[rec.id] = dependents
    result.bodies |= bodies
    result.sets[rec.id] = sets
    if moves:
        result.moves[rec.id] = moves
    return None


def _hole_moves(doc: reg.DocumentRec, edit: Edit) -> list[tuple[int, str, float]]:
    """The datums of the sketch of a hole that put its circles at the new points (empty: they do not move). The hole
    stays where the recorded origin puts it: only the distances from that origin change, so the feature is edited in
    place and the document is recomputed once. A hole across a tube wall is placed by its entry point, not by a
    distance: only a rebuild moves it."""
    old, new = edit.rec.params, edit.request
    if dim_edit.hole_points(old) == dim_edit.hole_points(new):
        return []
    face = old.get("face")
    if not isinstance(face, dict) or not isinstance(face.get("id"), str):
        raise dim_edit.NotInPlaceError("the face of the hole is not recorded")
    try:
        rec, comp = reg.resolve_face(doc, face["id"], None)
        planar = face_is_planar(doc, rec, comp)
    except WorkerError as exc:
        raise dim_edit.NotInPlaceError(f"the face {face['id']} the hole sits on no longer exists") from exc
    if not planar:
        raise dim_edit.NotInPlaceError("the position of a hole across a curved face is set by its entry point")
    sk = _profile_sketch(edit.obj)
    if sk is None:
        raise dim_edit.NotInPlaceError(f"the hole {edit.rec.label} has no sketch")
    return dim_edit.hole_position_changes(old, new, _rows(sk), [float(c.Value) for c in sk.Constraints])


# --- measures ---------------------------------------------------------------------------------------------------------


def _solid_list(shape: Any) -> list[Any]:
    if shape.isNull():
        return []
    return [shape] if shape.ShapeType == "Solid" else list(shape.Solids)


def measure(shape: Any, full: bool = True) -> dict[str, Any]:
    """What the tools measure of one body's shape; ``full=False`` skips the (costly) exact box and centroid."""
    if shape.isNull():
        return {
            "volume_mm3": 0.0,
            "bbox": dict(g.ZERO_BBOX),
            "centroid": None,
            "solids": 0,
            "faces": 0,
            "edges": 0,
            "valid": True,
        }
    solids = _solid_list(shape)
    volume = sum(float(s.Volume) for s in solids)
    out: dict[str, Any] = {
        "volume_mm3": volume,
        "bbox": g.shape_bbox(shape) if full else dict(g.ZERO_BBOX),
        "centroid": None,
        "solids": len(solids),
        "faces": len(shape.Faces),
        "edges": len(shape.Edges),
        "valid": bool(shape.isValid()) if full else True,
    }
    if full and solids and abs(volume) > 1e-12:
        weighted = g.ORIGIN
        for s in solids:
            weighted = g.add(weighted, g.scale(g.vec(s.CenterOfMass), float(s.Volume)))
        out["centroid"] = g.clean(g.scale(weighted, 1.0 / volume))
    return out


def _same_measure(a: dict[str, Any], b: dict[str, Any]) -> bool:
    if (a["solids"], a["faces"], a["edges"]) != (b["solids"], b["faces"], b["edges"]):
        return False
    if abs(a["volume_mm3"] - b["volume_mm3"]) > max(VOLUME_TOL, REL_TOL * abs(b["volume_mm3"])):
        return False
    return all(
        abs(x - y) <= 1e-7
        for x, y in zip((*a["bbox"]["min"], *a["bbox"]["max"]), (*b["bbox"]["min"], *b["bbox"]["max"]), strict=True)
    )


def _body_shape(doc: reg.DocumentRec, body: reg.BodyRec) -> Any:
    return reg.reread_shape(doc, body)


def _measure_body(doc: reg.DocumentRec, body: reg.BodyRec, shape: Any, full: bool) -> dict[str, Any]:
    """``measure`` of a body's world shape, taken through the measures the registry keeps with the body: a shape
    that did not change since it was last measured (the state before an edit is the state the last edit, or the last
    reading of the part, left) costs nothing, and what is measured here is what ``mass_properties``,
    ``bounding_box`` and ``is_valid`` find kept afterwards."""
    if shape.isNull():
        return measure(shape, full)
    volumes = reg.body_volumes(doc, body)
    volume = sum(volumes)
    out: dict[str, Any] = {
        "volume_mm3": volume,
        "bbox": dict(g.ZERO_BBOX),
        "centroid": None,
        "solids": len(volumes),
        "faces": shape.countElement("Face"),
        "edges": shape.countElement("Edge"),
        "valid": True,
    }
    if not full:
        return out
    out["bbox"] = reg.body_bbox(doc, body)
    if body._valid is None:
        body._valid = bool(shape.isValid())
    out["valid"] = body._valid
    if volumes and abs(volume) > 1e-12:
        weighted = g.ORIGIN
        for v, centre in reg.body_parts(doc, body):
            weighted = g.add(weighted, g.scale(centre, v))
        out["centroid"] = g.clean(g.scale(weighted, 1.0 / volume))
    return out


def _object_volume(
    fc_doc: Any, name: str | None, kept: dict[str, tuple[Any, float, int]] | None = None
) -> tuple[float, int]:
    """``(volume, solids)`` of one object. ``kept`` holds the shape each object had when it was measured: the same shape
    (same ``TShape`` and location) has the same volume and is not measured again."""
    obj = fc_doc.getObject(name) if name else None
    if obj is None:
        return 0.0, 0
    # the shape the object holds is the key (``Part.getShape`` places a copy of it: never the same one twice)
    held = getattr(obj, "Shape", None)
    if held is not None and held.isNull():
        held = None
    if kept is not None and held is not None and name in kept and kept[name][0].isSame(held):
        return kept[name][1], kept[name][2]
    shape = Part.getShape(obj)
    if shape.isNull():
        return 0.0, 0
    solids = _solid_list(shape)
    volume = sum(float(s.Volume) for s in solids)
    if kept is not None and held is not None and name is not None:
        kept[name] = (held, volume, len(solids))
    return volume, len(solids)


def _interference(doc: reg.DocumentRec, bodies: set[str]) -> dict[tuple[str, str], float]:
    """Common volume of every pair of live bodies with one in ``bodies`` (box prefilter, exact common after)."""
    live = [(b, _body_shape(doc, b)) for b in doc.live_bodies()]
    boxes = {b.id: (g.exact_box(s) if not s.isNull() else None) for b, s in live}
    out: dict[tuple[str, str], float] = {}
    for i in range(len(live)):
        for j in range(i + 1, len(live)):
            (a, sa), (b, sb) = live[i], live[j]
            if a.id not in bodies and b.id not in bodies:
                continue
            if sa.isNull() or sb.isNull() or boxes[a.id] is None or boxes[b.id] is None:
                continue
            if not boxes[a.id].intersect(boxes[b.id]):
                continue
            try:
                out[(a.id, b.id)] = float(sa.common(sb).Volume)
            except Exception:  # noqa: BLE001 - OCC refuses some pairs; no number is better than a wrong one
                continue
    return out


class State:
    """Everything the checks compare, captured before and after the edit."""

    def __init__(self) -> None:
        self.bodies: dict[str, dict[str, Any]] = {}
        self.cuts: dict[str, float] = {}  # cut feature -> volume it removes
        self.results: dict[str, tuple[float, int]] = {}  # dependent feature -> (volume, solids) of its object
        self.poses: dict[str, tuple[float, ...]] = {}
        self.bad: dict[str, tuple[list[str], str]] = {}
        self.interference: dict[tuple[str, str], float] = {}


def _scan_objects(fc_doc: Any) -> dict[str, tuple[list[str], str]]:
    """Objects in a state a healthy document does not have (``State`` flags: Invalid, Error, still Touched)."""
    bad: dict[str, tuple[list[str], str]] = {}
    for obj in fc_doc.Objects:
        state = [str(s) for s in obj.State]
        if any(s in BAD_STATES or s in PENDING_STATES for s in state) or not _is_valid(obj):
            try:
                status = str(obj.getStatusString())
            except Exception:  # noqa: BLE001
                status = "unknown"
            bad[obj.Name] = (state, status)
    return bad


def _is_valid(obj: Any) -> bool:
    try:
        return bool(obj.isValid())
    except Exception:  # noqa: BLE001
        return True


def _inside_a_body(obj: Any) -> bool:
    try:
        return obj.getParentGeoFeatureGroup() is not None
    except Exception:  # noqa: BLE001 - an object that cannot say is judged as a loose one
        return False


def capture(doc: reg.DocumentRec, analysis: Analysis, *, interference: bool) -> State:
    state = State()
    fc_doc = doc.fc_doc
    for body in doc.live_bodies():
        # (measured through the registry's own caches: the validity, volumes and box land with the body and the
        # readings that follow the edit find them there)
        state.bodies[body.id] = _measure_body(doc, body, _body_shape(doc, body), body.id in analysis.bodies)
    # The document does not change while it is captured, and the volume a feature leaves is the base of the next one
    # (a chain of holes asks for each object twice or more): every object is measured once, and an object whose shape
    # did not change since the last capture (the state before an edit is the one the last edit left) is not measured
    # again.
    volumes: dict[str | None, tuple[float, int]] = {}

    def volume_of(name: str | None) -> tuple[float, int]:
        if name not in volumes:
            volumes[name] = _object_volume(fc_doc, name, doc.object_volumes)
        return volumes[name]

    dependent = {rec.id: rec for rec in analysis.recomputed}
    for rec in dependent.values():
        if rec.kind in ("boolean", "box", "cylinder") or rec.kind in dim_edit.FEATURE_KINDS:
            state.results[rec.id] = volume_of(rec.fc_main)
        obj = fc_doc.getObject(rec.fc_main) if rec.fc_main else None
        if rec.kind == "boolean" and rec.params.get("kind") == "subtract":
            base = getattr(obj, "Base", None) if obj is not None else None
            base_volume, _ = volume_of(base.Name if base is not None else None)
            result_volume, _ = volume_of(rec.fc_main)
            state.cuts[rec.id] = base_volume - result_volume
        elif rec.kind in ("cut", "hole") and obj is not None:  # a pocket or a hole removes what its base feature had
            base = getattr(obj, "BaseFeature", None)
            if base is not None:
                base_volume, _ = volume_of(base.Name)
                result_volume, _ = volume_of(rec.fc_main)
                state.cuts[rec.id] = base_volume - result_volume
    for gone in [name for name in doc.object_volumes if name not in volumes]:
        del doc.object_volumes[gone]  # an object no capture reads any more keeps no shape alive
    # (a feature inside a body has a Placement the recompute derives; the bodies and the loose objects are what moves)
    state.poses = {
        o.Name: _pose_key(o.Placement)
        for o in fc_doc.Objects
        if "Placement" in o.PropertiesList and not (analysis.pd and _inside_a_body(o))
    }
    state.bad = _scan_objects(fc_doc)
    if interference:
        state.interference = _interference(doc, analysis.bodies)
    return state


def measure_bodies(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """READ-ONLY reading of the whole part (the optional ``measure_bodies`` tool): every live body measured, the volume
    each subtraction removes, and every pair of bodies that share volume. It changes nothing in the document or the
    registry (only the per-body shape cache is refreshed), so a caller can read the part before and after a rebuild and
    compare what the per-operation steps of the rebuild cannot see."""
    started = time.perf_counter()
    try:
        bodies = []
        for body in doc.live_bodies():
            reading = measure(_body_shape(doc, body), full=True)
            valid = reading.pop("valid")
            bodies.append(
                {"body": {"id": body.id}, "label": _owner_label(doc, body), "measure": reading, "valid": valid}
            )
        cuts = []
        for rec in doc.features:
            if rec.kind == "boolean" and rec.params.get("kind") == "subtract" and rec.fc_main:
                obj = doc.fc_doc.getObject(rec.fc_main)
                base = getattr(obj, "Base", None) if obj is not None else None
                base_volume, _ = _object_volume(doc.fc_doc, base.Name if base is not None else None)
                result_volume, _ = _object_volume(doc.fc_doc, rec.fc_main)
                cuts.append({"feature": {"id": rec.id}, "label": rec.label, "removed_mm3": base_volume - result_volume})
        overlaps = [
            {"a": {"id": a}, "b": {"id": b}, "volume_mm3": volume}
            for (a, b), volume in _interference(doc, {b.id for b in doc.live_bodies()}).items()
            if volume > VOLUME_TOL
        ]
    except WorkerError:
        raise
    except Exception as exc:  # noqa: BLE001 - a reading must never take the session down: say what failed
        raise error(
            "internal", f"measure_bodies failed: {type(exc).__name__}: {exc}", params.get("operation_id")
        ) from exc
    return {
        "bodies": bodies,
        "cuts": cuts,
        "overlaps": overlaps,
        "duration_ms": (time.perf_counter() - started) * 1000.0,
        "warnings": [],
    }


# --- the checks -------------------------------------------------------------------------------------------------------


def _feature_of(doc: reg.DocumentRec, obj_name: str) -> reg.FeatureRec | None:
    for rec in doc.features:
        if rec.fc_main == obj_name or obj_name in rec.fc_names:
            return rec
    return None


def _owner_label(doc: reg.DocumentRec, body: reg.BodyRec) -> str:
    for rec in reversed(doc.features):
        if rec.fc_main == body.result_obj:
            return rec.label
    owner = next((f for f in doc.features if f.id == body.created_by), None)
    return owner.label if owner is not None else ""


def evaluate(
    doc: reg.DocumentRec, analysis: Analysis, edits: list[Edit], before: State, after: State
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """``(health issues, per-body deltas, flags)``: what the edit did, from FreeCAD's own states and from measures. A
    flag with severity ``failure`` fails the edit."""
    health: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    deltas: list[dict[str, Any]] = []
    by_id = {rec.id: rec for rec in doc.features}

    def handle(rec: reg.FeatureRec | None) -> dict[str, str] | None:
        return {"id": rec.id} if rec is not None else None

    # guard 2: the CAD's own states (only what the edit made bad)
    for name, (state, status) in after.bad.items():
        if name in before.bad:
            continue
        health.append({"feature": handle(_feature_of(doc, name)), "object": name, "state": state, "message": status})

    # guard 3a: a hole that was moved (and every hole after it) must still start on the face it is drilled in
    if analysis.moves:
        for rec in analysis.recomputed:
            if rec.kind == "hole":
                why = _hole_off_face(doc, rec)
                if why is not None:
                    flags.append(
                        {
                            "kind": "hole_off_face",
                            "severity": "failure",
                            "message": f"{rec.label}: {why}",
                            "features": [{"id": rec.id}],
                            "bodies": [],
                            "value": None,
                        }
                    )

    # guard 3: semantic checks on the dependents
    for rec in analysis.recomputed:
        was, now = before.results.get(rec.id), after.results.get(rec.id)
        if was is None or now is None:
            continue
        label = rec.label
        if was[0] > VOLUME_TOL and (now[1] == 0 or now[0] <= VOLUME_TOL):
            flags.append(
                {
                    "kind": "body_disappeared",
                    "severity": "failure",
                    "message": f"{label} has no material left after the edit",
                    "features": [{"id": rec.id}],
                    "bodies": [],
                    "value": None,
                }
            )
            continue
        if rec.kind == "boolean" and rec.params.get("kind") == "union" and now[1] > was[1]:
            flags.append(
                {
                    "kind": "solids_increased",
                    "severity": "failure",
                    "message": (
                        f"the union {label} is now {now[1]} solids (it was {was[1]}): its parts no longer touch"
                    ),
                    "features": [{"id": rec.id}],
                    "bodies": [],
                    "value": None,
                }
            )
    for rec_id, removed_before in before.cuts.items():
        removed_after = after.cuts.get(rec_id)
        if removed_after is None:
            continue
        rec = by_id[rec_id]
        # (a subtraction cannot add material: base - result is never negative, so there is no flag for it)
        if removed_before > VOLUME_TOL and removed_after <= VOLUME_TOL:
            # a pocket or a hole that no longer cuts is what a rebuild refuses ("removes no material"): not a wish
            flags.append(
                {
                    "kind": "cut_without_effect",
                    "severity": "failure" if rec.kind in ("cut", "hole") else "warning",
                    "message": f"the cut {rec.label} no longer removes any material (its tool misses the body)",
                    "features": [{"id": rec_id}],
                    "bodies": [],
                    "value": removed_after,
                }
            )

    bodies = {b.id: b for b in doc.live_bodies()}
    for body_id, old in before.bodies.items():
        new = after.bodies.get(body_id)
        body = bodies.get(body_id)
        if new is None:
            continue
        label = _owner_label(doc, body) if body is not None else ""
        if body_id not in analysis.bodies:
            if (old["solids"], old["faces"], old["edges"]) != (new["solids"], new["faces"], new["edges"]) or abs(
                old["volume_mm3"] - new["volume_mm3"]
            ) > max(VOLUME_TOL, REL_TOL * abs(old["volume_mm3"])):
                flags.append(
                    {
                        "kind": "volume_changed",
                        "severity": "failure",
                        "message": f"body {label or body_id} changed although nothing it depends on was edited",
                        "features": [],
                        "bodies": [{"id": body_id}],
                        "value": new["volume_mm3"] - old["volume_mm3"],
                    }
                )
            continue
        if old["valid"] and not new["valid"]:
            flags.append(
                {
                    "kind": "shape_invalid",
                    "severity": "failure",
                    "message": f"the shape of body {label or body_id} is not valid after the edit",
                    "features": [],
                    "bodies": [{"id": body_id}],
                    "value": None,
                }
            )
        if old["solids"] > 0 and new["solids"] == 0:
            flags.append(
                {
                    "kind": "body_disappeared",
                    "severity": "failure",
                    "message": f"body {label or body_id} has no material after the edit",
                    "features": [],
                    "bodies": [{"id": body_id}],
                    "value": None,
                }
            )
        elif new["solids"] > old["solids"]:
            flags.append(
                {
                    "kind": "solids_increased",
                    "severity": "failure",  # a body in several pieces is never what a parametric edit meant
                    "message": f"body {label or body_id} is now {new['solids']} solids (it was {old['solids']})",
                    "features": [],
                    "bodies": [{"id": body_id}],
                    "value": float(new["solids"] - old["solids"]),
                }
            )
        elif new["solids"] < old["solids"]:
            flags.append(
                {
                    "kind": "solids_decreased",
                    "severity": "warning",
                    "message": f"body {label or body_id} is now {new['solids']} solids (it was {old['solids']})",
                    "features": [],
                    "bodies": [{"id": body_id}],
                    "value": float(new["solids"] - old["solids"]),
                }
            )
        if not _same_measure(old, new):
            deltas.append({"body": {"id": body_id}, "label": label, "before": old, "after": new})

    # new interference between bodies (a pair that did not overlap, or overlaps more)
    for (a, b), volume in after.interference.items():
        was = before.interference.get((a, b), 0.0)
        if volume > was + max(VOLUME_TOL, REL_TOL * volume):
            la = _owner_label(doc, bodies[a]) if a in bodies else a
            lb = _owner_label(doc, bodies[b]) if b in bodies else b
            flags.append(
                {
                    "kind": "new_interference",
                    "severity": "warning",
                    "message": f"{la or a} and {lb or b} now overlap by {volume:.3f} mm3 (before: {was:.3f})",
                    "features": [],
                    "bodies": [{"id": a}, {"id": b}],
                    "value": volume,
                }
            )

    # guard 4: nothing that was not edited may move
    moved = [
        name
        for name, key in after.poses.items()
        if name not in analysis.objects
        and name in before.poses
        and max(abs(x - y) for x, y in zip(key, before.poses[name], strict=True)) > DRIFT_TOL
    ]
    if moved:
        flags.append(
            {
                "kind": "placement_drift",
                "severity": "failure",
                "message": f"objects that were not edited moved: {_names(moved)}",
                "features": [h for h in (handle(_feature_of(doc, n)) for n in moved[:MAX_LISTED]) if h is not None],
                "bodies": [],
                "value": None,
            }
        )
    return health, deltas, flags


# --- the edit ---------------------------------------------------------------------------------------------------------


def _restore_journals(
    saved: dict[str, tuple[dict[str, Any], dict[str, Any]]], by_id: dict[str, reg.FeatureRec]
) -> None:
    for feature_id, (previous, after) in saved.items():
        j = by_id[feature_id].journal
        j.previous, j.after = previous, after


def _sync_journals(
    doc: reg.DocumentRec, objects: set[str], params_of: Any, saved: dict[str, tuple[dict[str, Any], dict[str, Any]]]
) -> None:
    """Make the Placement entries of the undo journals follow the new chains of moves (see the module docstring)."""
    for name in objects:
        poses = chain(doc, name, params_of)
        previous = poses[0][1]
        for rec, pose in poses[1:]:
            assert rec is not None
            j = rec.journal
            if j is not None:
                saved.setdefault(
                    rec.id, ({k: dict(v) for k, v in j.previous.items()}, {k: dict(v) for k, v in j.after.items()})
                )
                journal.set_pose(j, name, previous, pose)
            previous = pose


def _is_rigid_move(body: reg.BodyRec, shape: Any, edits: list[Edit]) -> bool:
    """True when the body is the result of an edited object and every edit on that object is a ``move_body``: the shape
    was re-posed, not rebuilt, so its elements keep their order and each handle must follow the element it named (as
    ``move_body`` does), not whichever element of the new shape looks most like it."""
    on_body = [e for e in edits if e.obj is not None and e.obj.Name == body.result_obj]
    if not on_body or any(e.kind != "move_body" for e in on_body):
        return False
    live_faces = [r for r in body.faces.values() if not r.stale]
    live_edges = [r for r in body.edges.values() if not r.stale]
    return (
        len(shape.Faces) == len(live_faces)
        and all(r.index < len(shape.Faces) for r in live_faces)
        and all(r.index < len(shape.Edges) for r in live_edges)
    )


def _rebind(
    doc: reg.DocumentRec,
    analysis: Analysis,
    edits: list[Edit],
    saved_created: dict[str, tuple[list[str], list[str]]],
    follow: frozenset[str] = frozenset(),
) -> dict[str, list[str]]:
    """Re-resolve every dependent body against its recomputed shape (guard 7). New faces and edges belong to the feature
    that built the body as it stands; nothing is attributed as a modification (no feature made one). The lists it
    extends are kept in ``saved_created`` (before the first change to each) so a rollback can give them back.
    ``follow``: the faces sketches sit on, which keep their record when the edit slides them along their normal.
    """
    out: dict[str, list[str]] = {"created": [], "removed": [], "modified": []}
    fallback = edits[0].rec
    for body in doc.live_bodies():
        kept = body.measures()  # the shape the checks just measured: its measures survive when the shape is still it
        body.invalidate()
        if body.id not in analysis.bodies:
            reg.restore_measures(doc, body, kept)
            continue
        shape = reg.topo_shape(doc, body)
        if shape.isNull():
            continue
        owner = next((r for r in reversed(doc.features) if r.fc_main == body.result_obj), fallback)
        diff = (
            reg.refresh_by_index(doc, body, shape, owner.id)
            if _is_rigid_move(body, shape, edits)
            else reg.refresh(doc, body, shape, owner.id, mark=False, follow=follow)
        )
        reg.restore_measures(doc, body, kept)
        saved_created.setdefault(owner.id, (list(owner.created_faces), list(owner.created_edges)))
        # a face the edit made again (a bore resized, a feature a hair off) stays with the feature of the face it
        # replaces: its colour and its record are that feature's, not the edited one's
        replaced = dim_edit.replaced_faces(
            {fid: body.faces[fid].sig for fid in diff.removed_faces if fid in body.faces},
            {fid: body.faces[fid].sig for fid in diff.created_faces},
        )
        for new_id in diff.created_faces:
            heirs = [r for r in doc.features if replaced.get(new_id) in r.created_faces] or [owner]
            for heir in heirs:
                saved_created.setdefault(heir.id, (list(heir.created_faces), list(heir.created_edges)))
                if new_id not in heir.created_faces:
                    heir.created_faces.append(new_id)
        owner.created_edges.extend(e for e in diff.created_edges if e not in owner.created_edges)
        out["created"].extend(diff.created_faces)
        out["removed"].extend(diff.removed_faces)
        out["modified"].extend(diff.modified_faces)
    return out


def _rebind_quietly(doc: reg.DocumentRec, analysis: Analysis) -> None:
    """After a rollback: point the (restored) records at the shapes the recompute made again, changing nothing else."""
    for body in doc.live_bodies():
        body.invalidate()
        if body.id not in analysis.bodies:
            continue
        shape = reg.topo_shape(doc, body)
        if not shape.isNull():
            reg.refresh(doc, body, shape, body.created_by, mark=False)


# --- the dimensions of a sketch ---------------------------------------------------------------------------------------


class SketchPlan:
    """One sketch to edit: the datums to set (from the sketches built from scratch) and what the sketch built with the
    new dimensions looks like (the check after the edit)."""

    def __init__(self, edit: Edit, sk_rec: reg.SketchRec, changes: list[tuple[int, str, float]]) -> None:
        self.edit = edit
        self.sk_rec = sk_rec
        self.changes = changes
        self.new_spec: dict[str, Any] = edit.sketch or {}
        self.points: dict[tuple[int, int], tuple[float, float]] = {}
        self.named: dict[str, float] = {}


def _rows(sk: Any) -> list[tuple[Any, ...]]:
    """What identifies each constraint of a sketch (not its value): the same sketch built twice gives the same rows."""
    return [
        (
            str(c.Type),
            int(c.First),
            int(c.FirstPos),
            int(c.Second),
            int(c.SecondPos),
            int(c.Third),
            int(c.ThirdPos),
            bool(c.Driving),
            str(c.Name),
        )
        for c in sk.Constraints
    ]


def _geo_points(sk: Any) -> dict[tuple[int, int], tuple[float, float]]:
    out: dict[tuple[int, int], tuple[float, float]] = {}
    for index in range(len(sk.Geometry)):
        for pos in (1, 2, 3):
            try:
                point = sk.getPoint(index, pos)
                out[(index, pos)] = (float(point.x), float(point.y))
            except Exception:  # noqa: BLE001 - a geometry has no such point
                continue
    return out


def _scratch(
    doc: reg.DocumentRec, spec: dict[str, Any], tag: str
) -> tuple[list[tuple[Any, ...]], list[float], dict, dict]:
    """The sketch ``spec`` describes, built from scratch beside the real ones as the harness builds it, read and
    removed: ``(constraint rows, their values, the points of its geometry, its named dimensions)``."""
    mark = len(fc.recorder.lines)
    rec = reg.SketchRec(f"scratch:{tag}", spec, g.frame_for_plane("XY"), [])
    sk = oc.materialise_sketch(doc, None, rec, "DimensionScratch")
    try:
        rows, values = _rows(sk), [float(c.Value) for c in sk.Constraints]
        points, named = _geo_points(sk), dict(rec.named_dimensions)
    finally:
        fc.remove_object(doc.fc_doc, sk.Name)
        del fc.recorder.lines[mark:]
    return rows, values, points, named


def _plan_sketches(doc: reg.DocumentRec, edits: list[Edit]) -> list[SketchPlan]:
    plans: list[SketchPlan] = []
    old_specs: dict[str, dict[str, Any]] = {}
    for edit in edits:
        if edit.sketch is None:
            continue
        sk_rec = doc.sketches.get(edit.rec.sketch or "")
        if sk_rec is None or edit.sketch_obj is None:
            raise dim_edit.NotInPlaceError(f"the sketch of {edit.rec.label} is not registered")
        old_spec = old_specs.setdefault(sk_rec.id, sk_rec.spec)
        old = _scratch(doc, old_spec, "old")
        new = _scratch(doc, edit.sketch, "new")
        changes = dim_edit.datum_changes((old[0], old[1]), (new[0], new[1]), _rows(edit.sketch_obj))
        bound = dim_edit.bound_by_expression(_expression_paths(edit.sketch_obj), changes)
        if bound:
            raise dim_edit.NotInPlaceError(
                f"{', '.join(bound)} of {edit.sketch_obj.Name} is bound to an expression: a value set directly "
                "would be overwritten by the next recompute"
            )
        plan = SketchPlan(edit, sk_rec, changes)
        plan.points, plan.named = new[2], new[3]
        plans.append(plan)
    return plans


def _apply_sketch(
    doc: reg.DocumentRec, plan: SketchPlan, saved_features: dict[str, tuple[dict[str, float], int | None]]
) -> None:
    """Set the datums, solve, and check the sketch is what a fresh one with the new dimensions is, and fully defined."""
    sk, op = plan.edit.sketch_obj, plan.new_spec.get("operation_id")
    for index, name, value in plan.changes:
        try:
            fc.call(sk, "setDatum", index, float(value))
        except Exception as exc:  # noqa: BLE001 - FreeCAD names why (zero, unsolvable...)
            raise geometry_failed(
                f"{sk.Name}: {name or f'constraint {index}'} = {value:g} mm is refused ({type(exc).__name__}: {exc})",
                op,
            ) from exc
    if plan.changes and fc.call(sk, "solve") != 0:
        raise geometry_failed(f"the sketch {sk.Name} does not solve with the new dimensions", op)
    if plan.changes:
        live = _geo_points(sk)
        off = [key for key, point in plan.points.items() if key not in live or math.dist(live[key], point) > POSE_TOL]
        if off:
            raise dim_edit.NotInPlaceError(
                f"the sketch {sk.Name} edited in place is not the sketch built from scratch with the new dimensions "
                f"(geometry {off[0][0]})"
            )
    verdict = sketch_defined.judge(sk)
    if verdict is not None:
        raise error(
            "invalid_request", verdict.text(f"the sketch {sk.Name}"), op, reason=verdict.reason, dof=verdict.dof
        )
    saved_features.setdefault(plan.edit.rec.id, (dict(plan.edit.rec.named_dimensions), plan.edit.rec.instance_count))
    plan.edit.rec.named_dimensions = dict(plan.named)
    if (
        plan.sk_rec.spec is not plan.new_spec
    ):  # the registry keeps the sketch as the CAD has it now (the snapshot gives it back)
        plan.sk_rec.spec = plan.new_spec
        plan.sk_rec.profiles = oc.sketch_profiles([dict(e) for e in plan.new_spec.get("entities", ())], op)
        plan.sk_rec.named_dimensions = dict(plan.named)


def _sync_hole_sketch(edit: Edit) -> None:
    """A hole keeps its diameter in the feature and in the circle of its own sketch: both follow."""
    sk = _profile_sketch(edit.obj)
    found = False
    for index, c in enumerate(sk.Constraints if sk is not None else ()):  # a multi-hole feature has one per circle
        if str(c.Type) == "Diameter" and bool(c.Driving):
            fc.call(sk, "setDatum", index, float(edit.request["diameter_mm"]))
            found = True
    if found:
        return
    raise dim_edit.NotInPlaceError(f"the sketch of {edit.rec.label} has no diameter to set")


# --- sketches that sit on a face ---------------------------------------------------------------------------------


def _centre_before(consumer: Any, normal: g.Vec3, centre: g.Vec3) -> g.Vec3:
    """The centre of the planar face as the feature that sits on it saw it: the frame of its sketch was taken from the
    face before that feature (and the ones after it) cut into it, and the centre of a face with holes is not the centre
    of the face without them. The face of the shape the feature was built on that lies in the same plane, nearest."""
    base = getattr(consumer, "BaseFeature", None) if consumer is not None else None
    shape = getattr(base, "Shape", None)
    if shape is None or shape.isNull():
        return centre
    best: tuple[float, g.Vec3] | None = None
    for face in shape.Faces:
        if g.surface_kind(face) != "plane":
            continue
        face_normal = prov.outward_normal(face)
        offset = g.sub(g.vec(face.CenterOfMass), centre)
        if g.dot(face_normal, normal) <= prov.PLANE_DOT or abs(g.dot(offset, normal)) > 1e-6:
            continue
        aside = g.length(offset)
        if best is None or aside < best[0]:
            best = (aside, g.vec(face.CenterOfMass))
    return best[1] if best is not None else centre


def _stays_put(sketch: Any, normal: g.Vec3, centre: g.Vec3) -> g.Vec3 | None:
    """Where the origin of ``sketch`` goes when its face is in the plane through ``centre`` with ``normal``: where it
    is, slid along the normal onto that plane. The first build took the origin at the centre of mass of the face, but
    that point moves with every hole in the face (and with any feature on it), so taking it again would move a feature
    that nothing asked to move: it keeps its place, and follows the face only across the plane (a plate made thicker).
    ``None`` when the sketch does not lie parallel to that plane (the face turned): only the face can say."""
    if sketch is None:
        return None
    pose = sketch.getGlobalPlacement()
    n = g.unit(normal)
    if g.dot(g.vec(pose.Rotation.multVec(Vector(0, 0, 1))), n) <= 1.0 - ANCHOR_TOL:
        return None
    here = g.vec(pose.Base)
    return g.add(here, g.scale(n, g.dot(g.sub(centre, here), n)))


def _face_frame(
    doc: reg.DocumentRec, face_id: str, label: str, consumer: Any = None, sketch: Any = None
) -> dict[str, Any] | None:
    """The frame a sketch on ``face_id`` gets now (``None``: the face is not planar)."""
    try:
        rec, comp = reg.resolve_face(doc, face_id, None)
        desc = face_descriptor(doc, rec, comp)
    except WorkerError as exc:
        raise dim_edit.NotInPlaceError(
            f"the face {face_id} that the sketch of {label} sits on no longer exists"
        ) from exc
    if desc["normal"] is None:
        return None
    normal, centre = tuple(desc["normal"]), tuple(desc["center"])
    anchored = _stays_put(sketch, normal, centre)
    return g.frame_for_face(anchored if anchored is not None else _centre_before(consumer, normal, centre), normal)


def _placed_sketches(
    doc: reg.DocumentRec, analysis: Analysis
) -> list[tuple[reg.FeatureRec, Any, str, reg.SketchRec | None]]:
    """Per recomputed feature whose sketch sits on a face: ``(feature, sketch object, face id, sketch record)``."""
    out: list[tuple[reg.FeatureRec, Any, str, reg.SketchRec | None]] = []
    for rec in analysis.recomputed:
        obj = doc.fc_doc.getObject(rec.fc_main) if rec.fc_main else None
        sketch = _profile_sketch(obj) if obj is not None else None
        if sketch is None:
            continue
        if rec.kind in dim_edit.SKETCH_CONSUMERS:
            sk_rec = doc.sketches.get(rec.sketch or "")
            if sk_rec is not None and sk_rec.on_face:
                out.append((rec, sketch, sk_rec.on_face, sk_rec))
        elif rec.kind == "hole":
            face = rec.params.get("face")
            if isinstance(face, dict) and isinstance(face.get("id"), str):
                out.append((rec, sketch, face["id"], None))
    return out


def _frame_on(doc: reg.DocumentRec, face_id: str, base: Any, sketch: Any = None) -> dict[str, Any] | None:
    """The frame of the sketch on ``face_id`` from the shape of ``base`` (the feature its feature is built
    on), before the rest of the document is recomputed: the planar face of that shape with the normal the face had,
    nearest to where it stood in its plane (it may have slid along its normal). ``None``: not planar, or not found."""
    try:
        rec, _ = reg.resolve_face(doc, face_id, None)
    except WorkerError:
        return None
    old = rec.sig
    shape = getattr(base, "Shape", None)
    if old.surface != "plane" or old.direction is None or shape is None or shape.isNull():
        return None
    reach = 0.5 * math.sqrt(max(float(getattr(rec.shape, "Area", 0.0) or 0.0), 1e-9))
    best: tuple[float, float, Any] | None = None
    for face in shape.Faces:
        if g.surface_kind(face) != "plane" or g.dot(prov.outward_normal(face), old.direction) <= prov.PLANE_DOT:
            continue
        centre = g.vec(face.CenterOfMass)
        offset = g.sub(centre, old.center)
        along = g.dot(offset, old.direction)
        aside = g.length(g.sub(offset, g.scale(old.direction, along)))
        if aside <= max(reach, 1e-6) and (best is None or (aside, abs(along)) < best[:2]):
            best = (aside, abs(along), (centre, prov.outward_normal(face)))
    if best is None:
        return None
    centre, normal = best[2]
    anchored = _stays_put(sketch, normal, centre)
    return g.frame_for_face(anchored if anchored is not None else centre, normal)


EDGE_FEATURE_TYPES = ("PartDesign::Fillet", "PartDesign::Chamfer")
EDGE_TOL = 1e-6  # mm: an edge on the line (or circle) it was on
ANCHOR_TOL = 1e-9  # 1 - cos: a sketch parallel to the plane of its face


def _describe_edge(edge: Any) -> tuple[Any, ...] | None:
    """What identifies an edge across a recompute that stretches it: its line or its circle (None: another curve)."""
    kind = g.curve_kind(edge)
    if kind == "line":
        p0, p1 = prov.edge_ends(edge)
        return ("line", p0, g.unit(g.sub(p1, p0)), g.vec(edge.CenterOfMass))
    if kind == "circle":
        return ("circle", g.vec(edge.Curve.Center), g.unit(g.vec(edge.Curve.Axis)), float(edge.Curve.Radius))
    return None


def _edge_distance(old: tuple[Any, ...], edge: Any) -> float | None:
    """How far ``edge`` is from the edge ``old`` describes (0: the same line or circle); ``None``: not the same sort."""
    new = _describe_edge(edge)
    if new is None or new[0] != old[0]:
        return None
    if g.dot(old[2], new[2]) ** 2 < 1.0 - 1e-9:
        return None
    if old[0] == "line":
        offset = g.sub(new[1], old[1])
        return g.length(g.sub(offset, g.scale(old[2], g.dot(offset, old[2]))))
    if abs(new[3] - old[3]) > 1e-4:
        return None
    return g.dist(new[1], old[1])


def _find_edge(shape: Any, old: tuple[Any, ...]) -> int | None:
    """The index of the edge of ``shape`` that is ``old`` after the edit: on the same line or circle (the nearest
    centre when several lie on it), else the nearest parallel one when it is clearly the nearest."""
    found: list[tuple[float, float, int]] = []
    for index, edge in enumerate(shape.Edges):
        distance = _edge_distance(old, edge)
        if distance is not None:
            found.append((distance, g.dist(g.vec(edge.CenterOfMass), old[3 if old[0] == "line" else 1]), index))
    found.sort()
    if not found:
        return None
    if found[0][0] <= EDGE_TOL:
        exact = [f for f in found if f[0] <= EDGE_TOL]
        return exact[0][2]
    if len(found) == 1 or found[1][0] >= 2.0 * found[0][0]:
        return found[0][2]
    return None


def _edge_objects(doc: reg.DocumentRec, rec: reg.FeatureRec) -> list[Any]:
    names = dict.fromkeys([*([rec.fc_main] if rec.fc_main else []), *rec.fc_names])
    objects = [doc.fc_doc.getObject(n) for n in names]
    return [o for o in objects if o is not None and o.TypeId in EDGE_FEATURE_TYPES and getattr(o, "Base", None)]


def _capture_edges(doc: reg.DocumentRec, analysis: Analysis) -> dict[str, list[tuple[Any, ...] | None]]:
    """The edges every fillet and chamfer of the recomputed features holds, as they are before the edit: FreeCAD
    keeps them by name, and the name of an edge of a shape that is recomputed with other dimensions may not resolve."""
    out: dict[str, list[tuple[Any, ...] | None]] = {}
    for rec in analysis.recomputed:
        for obj in _edge_objects(doc, rec):
            feature, names = obj.Base
            try:
                out[obj.Name] = [_describe_edge(feature.Shape.getElement(name)) for name in names]
            except Exception as exc:  # noqa: BLE001 - a name the shape cannot give: only a rebuild knows these edges
                raise dim_edit.NotInPlaceError(f"the edges of {obj.Label} cannot be read before the edit") from exc
    return out


def _place_in_order(doc: reg.DocumentRec, analysis: Analysis, edges: dict[str, list[tuple[Any, ...] | None]]) -> None:
    """Put what refers to a shape where the edit leaves that shape, in the order of the features, recomputing only up
    to the feature each one is built on: a full recompute with the sketches still where they were, and the fillets
    still pointing at the edges as they were numbered, would hand FreeCAD references it cannot resolve (and it rewrites
    such a reference as missing for good). A sketch on a face is placed on that face; a fillet or a chamfer is given the
    edges of the new shape that are the ones it had."""
    placed = {item[0].id: item for item in _placed_sketches(doc, analysis)}
    wanted_ids = {rec.id for rec in analysis.recomputed}
    for rec in doc.features:
        if rec.id not in wanted_ids:
            continue
        if rec.id in placed:
            _, sketch, face_id, _ = placed[rec.id]
            consumer = doc.fc_doc.getObject(rec.fc_main) if rec.fc_main else None
            base = getattr(consumer, "BaseFeature", None)
            if base is not None:
                fc.recompute_objects(doc.fc_doc, [base])
                frame = _frame_on(doc, face_id, base, sketch)
                if frame is not None:
                    body = sketch.getParentGeoFeatureGroup()
                    frame_placement = g.frame_placement(frame)
                    wanted = oc.to_body_frame(body, frame_placement) if body is not None else frame_placement
                    if not _same_pose(wanted, sketch.Placement, POSE_TOL):
                        fc.set_prop(sketch, "Placement", wanted)
        for obj in _edge_objects(doc, rec):
            described = edges.get(obj.Name)
            feature = obj.Base[0]
            if not described or any(d is None for d in described):
                continue
            fc.recompute_objects(doc.fc_doc, [feature])
            indices = [_find_edge(feature.Shape, d) for d in described if d is not None]
            if any(i is None for i in indices):
                raise dim_edit.NotInPlaceError(f"an edge of {obj.Label} is not on the shape the edit leaves")
            fc.set_prop(obj, "Base", (feature, [f"Edge{i + 1}" for i in indices if i is not None]))


def _settle(
    doc: reg.DocumentRec, analysis: Analysis, edits: list[Edit], saved_created: dict[str, tuple[list[str], list[str]]]
) -> dict[str, list[str]]:
    """Rebind the faces to the recomputed shapes and put every sketch that sits on a face back on it, as a rebuild
    would place it (the origin of the face frame is the centre of the face, which moves with the edit); repeat until
    nothing moves (a sketch on a face of a feature that sits on a moved face)."""
    refs: dict[str, list[str]] = {"created": [], "removed": [], "modified": []}
    placed = [] if analysis.keeps_places else _placed_sketches(doc, analysis)
    follow = frozenset(face_id for _, _, face_id, _ in placed)
    for _ in range(len(placed) + 1):
        diff = _rebind(doc, analysis, edits, saved_created, follow)
        for key, ids in diff.items():
            refs[key].extend(ids)
        moved = False
        for rec, sketch, face_id, sk_rec in placed:
            frame = _face_frame(
                doc, face_id, rec.label, doc.fc_doc.getObject(rec.fc_main) if rec.fc_main else None, sketch
            )
            if frame is None:  # a curved face: its sketch cannot be placed again here, so the face must be as it was
                if face_id in diff["modified"] or face_id in diff["removed"]:
                    raise dim_edit.NotInPlaceError(
                        f"{rec.label} sits on the curved face {face_id}, which the edit changed: only a rebuild "
                        "places it again"
                    )
                continue
            body = sketch.getParentGeoFeatureGroup()
            wanted = oc.to_body_frame(body, g.frame_placement(frame)) if body is not None else g.frame_placement(frame)
            if _same_pose(wanted, sketch.Placement, POSE_TOL):
                continue
            fc.set_prop(sketch, "Placement", wanted)
            if sk_rec is not None:
                sk_rec.frame = frame
            moved = True
        if not moved:
            return refs
        fc.recompute(doc.fc_doc)
    raise dim_edit.NotInPlaceError("the sketches placed on faces did not settle after the edit")


def _result(**fields: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "rebuild_ok": True,
        "applied": False,
        "dry_run": False,
        "recomputed": [],
        "refused": {},
        "health": [],
        "delta": [],
        "flags": [],
        "refs": {"created": [], "removed": [], "modified": []},
        "failure": None,
        "duration_ms": 0.0,
        "warnings": [],
    }
    base.update(fields)
    return base


def edit_feature(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    operation_id = params.get("operation_id")
    dry_run = bool(params.get("dry_run", False))
    edits = _parse(doc, params)
    analysis = analyse(doc, edits)

    def finish(**fields: Any) -> dict[str, Any]:
        return _result(dry_run=dry_run, duration_ms=(time.perf_counter() - started) * 1000.0, **fields)

    if analysis.refused:
        return finish(refused=analysis.refused)
    recomputed = _handles(rec.id for rec in analysis.recomputed)
    if dry_run:
        return finish(recomputed=recomputed)

    fc_doc = doc.fc_doc
    new_of = {e.rec.id: e.request for e in edits}
    old_of = {e.rec.id: e.rec.params for e in edits}
    by_id = {rec.id: rec for rec in doc.features}

    def params_new(rec: reg.FeatureRec) -> dict[str, Any]:
        return new_of.get(rec.id, rec.params)

    # what each edited object must carry
    plan: list[tuple[Any, list[tuple[str, Any]]]] = []
    for name in sorted(analysis.objects):
        obj = fc_doc.getObject(name)
        sets: list[tuple[str, Any]] = []
        for edit in edits:
            if edit.obj.Name == name:
                sets.extend((p, v) for p, v in _wanted(edit).items() if abs(_num(getattr(obj, p)) - v) > 1e-12)
        pose = chain(doc, name, params_new)[-1][1]
        if not _same_pose(pose, obj.Placement, DRIFT_TOL):
            sets.append(("Placement", pose))
        plan.append((obj, sets))
    has_feature_change = (
        any(analysis.sets.values()) or any(analysis.moves.values()) or any(e.sketch is not None for e in edits)
    )
    if not has_feature_change and not any(sets for _, sets in plan):
        return finish(warnings=["nothing changed: the new requests equal the values the document already has"])

    snapshot = doc.snapshot()
    saved_journals: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    saved_created: dict[str, tuple[list[str], list[str]]] = {}
    saved_features: dict[str, tuple[dict[str, float], int | None]] = {}
    recorded = len(fc.recorder.lines)
    before = capture(doc, analysis, interference=True)
    fc.open_transaction(fc_doc, f"edit {', '.join(e.rec.label for e in edits)}")
    failure: str | None = None
    health: list[dict[str, Any]] = []
    deltas: list[dict[str, Any]] = []
    flags: list[dict[str, Any]] = []
    refs: dict[str, list[str]] = {"created": [], "removed": [], "modified": []}
    try:
        # the sketches are planned first: the scratch sketches they are compared with are built while the document is
        # still as it was (their own recompute must not carry the edit)
        edge_refs = _capture_edges(doc, analysis) if analysis.pd else {}
        sketch_plans = _plan_sketches(doc, edits)
        for edit in edits:
            edit.rec.params = edit.request
        for obj, sets in plan:
            for prop, value in sets:
                fc.set_prop(obj, prop, value)
        for edit in edits:
            for obj, prop, value in analysis.sets.get(edit.rec.id, ()):
                fc.set_prop(obj, prop, value)
            if edit.kind == "hole" and any(p == "Diameter" for _, p, _ in analysis.sets.get(edit.rec.id, ())):
                _sync_hole_sketch(edit)
            for index, _, value in analysis.moves.get(edit.rec.id, ()):
                fc.call(_profile_sketch(edit.obj), "setDatum", index, float(value))
            if edit.kind == "linear_pattern" and edit.sketch is None:
                saved_features.setdefault(edit.rec.id, (dict(edit.rec.named_dimensions), edit.rec.instance_count))
                edit.rec.instance_count = int(edit.request["count"]) * int(edit.request.get("count2") or 1)
        for sketch_plan in sketch_plans:
            _apply_sketch(doc, sketch_plan, saved_features)
        if (
            analysis.pd and not analysis.keeps_places
        ):  # sketches on faces are placed one by one, each on the shape it was built on
            _place_in_order(doc, analysis, edge_refs)
        fc.recompute(fc_doc)
        if analysis.pd:  # the faces the later sketches sit on moved: place them again before anything is measured
            refs = _settle(doc, analysis, edits, saved_created)
        after = capture(doc, analysis, interference=True)
        health, deltas, flags = evaluate(doc, analysis, edits, before, after)
        failures = [f for f in flags if f["severity"] == "failure"]
        if health:
            failure = (
                f"FreeCAD reports {_names(h['object'] + ' (' + ', '.join(h['state']) + ')' for h in health)} "
                f"after the recompute: {health[0]['message']}"
            )
        elif failures:
            failure = failures[0]["message"]
        if failure is None:
            _sync_journals(doc, set(analysis.objects), params_new, saved_journals)
            if not analysis.pd:
                refs = _rebind(doc, analysis, edits, saved_created)
    except dim_edit.NotInPlaceError as exc:
        failure = str(exc)  # the value is not at fault: the caller rebuilds (health and flags are empty: 'technical')
    except WorkerError:
        _rollback(
            doc, analysis, edits, old_of, saved_journals, saved_created, by_id, snapshot, recorded, saved_features
        )
        raise
    except Exception as exc:  # noqa: BLE001 - whatever FreeCAD or the checks raised, give the document back as it was
        _rollback(
            doc, analysis, edits, old_of, saved_journals, saved_created, by_id, snapshot, recorded, saved_features
        )
        # ``internal``, not ``rebuild_failed``: this is a bug of the mechanism (or of FreeCAD), and the caller must not
        # read it as "the value does not build" -- a WorkerError from the recompute keeps its own code above.
        raise error(
            "internal",
            f"edit_feature could not edit {_names(e.rec.label for e in edits)}: {type(exc).__name__}: {exc}; "
            "the document and the registry are unchanged (the edit was rolled back)",
            operation_id,
        ) from exc
    if failure is not None:
        _rollback(
            doc, analysis, edits, old_of, saved_journals, saved_created, by_id, snapshot, recorded, saved_features
        )
        restored = {b.id: measure(_body_shape(doc, b), full=False) for b in doc.live_bodies()}
        for body_id, old in before.bodies.items():
            if body_id in restored and not _same_light(old, restored[body_id]):
                raise error(
                    "degraded_session",
                    f"edit_feature was rolled back but body {body_id} is not as it was before the edit: "
                    "the document may be inconsistent",
                    operation_id,
                )
        return finish(
            rebuild_ok=False,
            applied=False,
            recomputed=recomputed,
            health=health,
            delta=deltas,
            flags=flags,
            failure=failure,
            failure_kind=_failure_kind(health, flags),
        )
    fc.commit_transaction(fc_doc)
    warnings: list[str] = []
    try:
        fc.clear_undos(fc_doc)  # the edit is the harness's own: a later Ctrl+Z must not undo it behind the registry
    except Exception as exc:  # noqa: BLE001 - the edit IS applied: carry on, but say what that leaves behind
        warnings.append(f"the undo stack could not be cleared ({exc}): a later Ctrl+Z in FreeCAD could undo the edit")
    warnings.extend(_redraw_sheets(doc, operation_id))
    return finish(
        rebuild_ok=True,
        applied=True,
        recomputed=recomputed,
        health=health,
        delta=deltas,
        flags=flags,
        refs={k: _handles(dict.fromkeys(v)) for k, v in refs.items()},
        warnings=warnings,
    )


def _redraw_sheets(doc: reg.DocumentRec, operation_id: str | None) -> list[str]:
    """Draw every sheet of the document again from the model as the edit left it. TechDraw measures its own dimensions
    live, but the text a callout prints (``4x D6 H7``) and the symbols of the sheet are made from the model when the
    sheet is composed: left alone, the window would show the old value next to a model that changed. The edit is applied
    already, so a sheet that cannot be redrawn is a warning, never a failure."""
    if not any(str(o.TypeId) == "TechDraw::DrawPage" for o in doc.fc_doc.Objects):
        return []
    import drawing  # noqa: PLC0415 - the drawing module is heavy and only a document with sheets needs it

    pages = drawing.pages(doc.fc_doc)
    notes: list[str] = []
    for number, page in enumerate(pages, start=1):
        label = str(page.Label)
        try:
            sheet, _, ctx = drawing.compose(doc, page, drawing.spec_of(page), number, len(pages), operation_id)
            _, failed = drawing.mirror(doc, page, ctx, sheet)
        except Exception as exc:  # noqa: BLE001 - whatever FreeCAD or the composer raised
            notes.append(
                f"the sheet {label} could not be redrawn after the edit ({type(exc).__name__}: {exc}); it may show the "
                "old values: export it or add an item to draw it again"
            )
            continue
        notes.extend(failed)
    return notes


def _failure_kind(health: list[dict[str, Any]], flags: list[dict[str, Any]]) -> str:
    """'technical' only when EVERY failed check is about the mechanism (a pose or an untouched body moved): then the
    value is not at fault. Anything about the part itself (CAD health, a body that vanished or split, an invalid shape)
    is 'semantic', and it wins when both are present: a rebuild would apply the change without these checks."""
    failed = [f["kind"] for f in flags if f["severity"] == "failure"]
    if health or any(kind not in TECHNICAL_FLAGS for kind in failed):
        return "semantic"
    return "technical"


def _same_light(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return (a["solids"], a["faces"], a["edges"]) == (b["solids"], b["faces"], b["edges"]) and abs(
        a["volume_mm3"] - b["volume_mm3"]
    ) <= max(VOLUME_TOL, REL_TOL * abs(a["volume_mm3"]))


def _rollback(
    doc: reg.DocumentRec,
    analysis: Analysis,
    edits: list[Edit],
    old_of: dict[str, Any],
    saved_journals: dict[str, tuple[dict[str, Any], dict[str, Any]]],
    saved_created: dict[str, tuple[list[str], list[str]]],
    by_id: dict[str, reg.FeatureRec],
    snapshot: reg.Snapshot,
    recorded: int,
    saved_features: dict[str, tuple[dict[str, float], int | None]] | None = None,
) -> None:
    """Guard 5: abort the transaction, recompute, and give the registry back (``FeatureRec.params``, the journals, the
    snapshot, the faces and edges the rebind added to the owner features, the shapes the records point at). Raises
    ``degraded_session`` only when the rollback itself fails."""
    try:
        fc.abort_transaction(doc.fc_doc)
        fc.recompute(doc.fc_doc)
        for edit in edits:
            edit.rec.params = old_of[edit.rec.id]
        _restore_journals(saved_journals, by_id)
        for feature_id, (faces, edges) in saved_created.items():  # what the rebind added to the owners
            by_id[feature_id].created_faces, by_id[feature_id].created_edges = list(faces), list(edges)
        for feature_id, (named, count) in (saved_features or {}).items():
            by_id[feature_id].named_dimensions, by_id[feature_id].instance_count = dict(named), count
        doc.restore(snapshot)
        _rebind_quietly(doc, analysis)
    except Exception as exc:
        raise error(
            "degraded_session",
            f"edit_feature failed and its rollback failed too ({type(exc).__name__}: {exc}): the document may be "
            "half edited",
        ) from exc
    finally:
        del fc.recorder.lines[recorded:]


HOLE_FACE_TOL = 1e-4  # mm: the same tolerance the hole op uses to say a point is on its face


def _hole_off_face(doc: reg.DocumentRec, rec: reg.FeatureRec) -> str | None:
    """Why a hole of a planar face no longer starts ON the face (``None``: it does), as the hole op says it when a
    rebuild drills it: every centre must lie on the face of the shape the hole is built on (the feature before it), not
    merely in its box, so a point over a hole drilled before it, or off the edge of the plate, is refused."""
    obj = doc.fc_doc.getObject(rec.fc_main) if rec.fc_main else None
    sk = _profile_sketch(obj) if obj is not None else None
    base = getattr(obj, "BaseFeature", None) if obj is not None else None
    shape = getattr(base, "Shape", None)
    if sk is None or shape is None or shape.isNull():
        return None
    face = rec.params.get("face")
    if not isinstance(face, dict) or not isinstance(face.get("id"), str):
        return None
    try:
        frec, comp = reg.resolve_face(doc, face["id"], None)
        if not face_is_planar(doc, frec, comp):
            return None  # across a curved face: its sketch is placed by its entry point
    except WorkerError:
        return None
    pose = sk.getGlobalPlacement()
    normal = g.vec(pose.Rotation.multVec(Vector(0, 0, 1)))
    here = g.vec(pose.Base)
    planes = []
    for f in shape.Faces:  # the plane of a face is read off its surface: no mass properties, no normals per face
        surface = f.Surface
        if type(surface).__name__ != "Plane":
            continue
        axis = g.vec(surface.Axis)
        if (
            abs(abs(g.dot(axis, normal)) - 1.0) < 1e-9
            and abs(g.dot(g.sub(g.vec(surface.Position), here), normal)) <= 1e-6
        ):
            planes.append(f)
    for u, v in dim_edit.hole_points(rec.params):
        point = pose.multVec(Vector(u, v, 0))
        if not any(f.distToShape(Part.Vertex(point))[0] <= HOLE_FACE_TOL for f in planes):
            return (
                f"position {[u, v]} is not on face {face['id']}: the point ({point.x:.3f}, {point.y:.3f}, "
                f"{point.z:.3f}) lies in the bounding box of the face but outside the face itself (a hole in it, or "
                "a feature standing on it covers that spot). To drill the top of a boss select the "
                "boss's own top face, not the face it stands on"
            )
    return None
