"""Per-feature undo journal: what ``undo_last`` needs to take one op back WITHOUT FreeCAD's undo stack.

FreeCAD keeps at most 20 undo transactions (hardcoded in ``freecadcmd``; the preference that raises it only works in
the GUI binary), and ``doc.undo()`` past that is a silent no-op: the objects of the older reverted ops would stay in
the document while the registry forgot them. So the worker reverts by DELETION, which has no depth limit.

A faithful inverse of an op is more than "remove what it created": an op also edits objects that already existed
(``move_body`` moves a Placement, a PartDesign feature moves the Body's ``Tip`` and hides the previous tip, a boolean
hides its tools). ``run_op`` therefore takes a cheap before/after snapshot of the properties below on EVERY object of
the document and keeps the diff (``Journal``): the names it created, in creation order, and the previous value of
every tracked property it changed on an object that already existed. Reverting removes the created objects newest
first and writes the previous values back. It is generic on purpose: no op needs to say what it touches.

Tracked properties: ``Placement`` (move/rotate/translate, mates, ``ensure_partdesign``'s baked pose), ``Visibility``
(consumed or hidden tools, the previous Body tip, spines), ``Tip`` and ``BaseFeature`` (PartDesign chain). Group
membership needs no journal entry: ``removeObject`` drops an object from its Body/Part group by itself (verified on
FreeCAD 1.0.2 and 1.1.3), and no op adds an object to a group that already existed.

The whole revert runs in ONE transaction: if anything fails it is aborted, so the document is exactly as before and
the caller can keep (or retry) the registry as it was. After a successful revert the undo stack is cleared: the
deletion is itself an undo entry, and a later ``doc.undo()`` (or a Ctrl+Z in the GUI) would resurrect the deleted
objects behind the registry's back, while the stacked transactions of the reverted ops would reference objects that
are gone.
"""

import math
from typing import Any

import fc
from FreeCAD import Placement
from protocol import WorkerError, error

TRACKED = ("Visibility", "Placement", "Tip", "BaseFeature")
LINKS = ("Tip", "BaseFeature")
_MISSING = object()

Values = dict[str, Any]


def _read(obj: Any, prop: str) -> Any:
    try:
        value = getattr(obj, prop)
    except (AttributeError, RuntimeError):
        return _MISSING
    if prop in LINKS:
        return None if value is None else str(value.Name)  # a link is journaled by name, never by object
    if prop == "Placement":
        return Placement(value)  # a copy: the property object is live
    return bool(value) if prop == "Visibility" else value


POSE_TOLERANCE = 1e-6
"""mm / rad: a pose that differs by less is the same pose (a recompute or a round trip through a matrix moves a
Placement by float noise; that must not read as a hand edit)."""


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, Placement) and isinstance(b, Placement):
        if a.Base.distanceToPoint(b.Base) > POSE_TOLERANCE:
            return False
        dot = abs(sum(x * y for x, y in zip(a.Rotation.Q, b.Rotation.Q, strict=True)))  # q and -q are one rotation
        return 2.0 * math.acos(min(1.0, dot)) <= POSE_TOLERANCE
    return bool(a == b)


def capture(fc_doc: Any) -> dict[str, Values]:
    """Tracked properties of every object of the document, keyed by object name (document order)."""
    snapshot: dict[str, Values] = {}
    for obj in fc_doc.Objects:
        values = {prop: value for prop in TRACKED if (value := _read(obj, prop)) is not _MISSING}
        snapshot[obj.Name] = values
    return snapshot


class Journal:
    """What one op did to the document: created objects (creation order), objects it deleted that already existed
    (none of today's ops does; such an op cannot be reverted by deletion) and the previous values of the tracked
    properties it changed on pre-existing objects. ``after`` holds the values the op left in those same properties: at
    revert time a different current value is a hand edit the revert is about to overwrite (reported, not refused)."""

    def __init__(
        self,
        created: list[str],
        removed: list[str],
        previous: dict[str, Values],
        after: dict[str, Values] | None = None,
    ) -> None:
        self.created = created
        self.removed = removed
        self.previous = previous
        self.after = after or {}
        # ``native_undo.Restorer`` for a ``run_native`` step: the pre-step content of the properties it changed and of
        # the objects it deleted, which the four tracked properties above cannot restore. ``None`` for the catalogue.
        self.extra: Any = None


def seal(fc_doc: Any, before: dict[str, Values]) -> Journal:
    """Diff the document now against the snapshot taken before the op."""
    now = capture(fc_doc)
    created = [name for name in now if name not in before]
    removed = [name for name in before if name not in now]
    previous: dict[str, Values] = {}
    after: dict[str, Values] = {}
    for name, old in before.items():
        if name not in now:
            continue
        changed = {prop: value for prop, value in old.items() if not _same(value, now[name].get(prop, _MISSING))}
        if changed:
            previous[name] = changed
            after[name] = {prop: now[name][prop] for prop in changed if prop in now[name]}
    return Journal(created, removed, previous, after)


def set_pose(journal_: Journal, name: str, before: Any, after: Any) -> None:
    """Say that the op of ``journal_`` moved the object ``name`` from the pose ``before`` to ``after`` (as
    ``seal`` would
    have recorded it): used by an in-place edit (``worker/edit.py``) when it recomposes the chain of moves on an object,
    so reverting the op later gives the pose the CHAIN now has before it, not the one it had when the op ran. Like
    ``seal``, an op that leaves the pose unchanged carries no entry."""
    if _same(before, after):
        for table in (journal_.previous, journal_.after):
            values = table.get(name)
            if values is not None:
                values.pop("Placement", None)
                if not values:
                    del table[name]
        return
    journal_.previous.setdefault(name, {})["Placement"] = Placement(before)
    journal_.after.setdefault(name, {})["Placement"] = Placement(after)


def _resolve(fc_doc: Any, prop: str, value: Any) -> Any:
    if prop in LINKS:
        return None if value is None else fc_doc.getObject(value)
    return value


def _restore_previous(fc_doc: Any, journal: Journal, warnings: list[str]) -> set[tuple[str, str]]:
    """Write the previous values back. LENIENT: an object (or link target) the engineer deleted by hand has nothing left
    to restore, so it is skipped and reported; the revert goes on. Returns the (object, property) pairs skipped."""
    skipped: set[tuple[str, str]] = set()
    gone: set[str] = set()
    # links first (Body.Tip may move other things), then pose and visibility so nothing overwrites them
    for prop in ("BaseFeature", "Tip", "Placement", "Visibility"):
        for name, values in journal.previous.items():
            if prop not in values:
                continue
            obj = fc_doc.getObject(name)
            if obj is None:
                skipped.add((name, prop))
                if name not in gone:
                    gone.add(name)
                    props = ", ".join(sorted(p for p in values if p in TRACKED))
                    warnings.append(f"{name} no longer exists (deleted by hand): its {props} was not restored")
                continue
            wanted = _resolve(fc_doc, prop, values[prop])
            if prop in LINKS and values[prop] is not None and wanted is None:
                skipped.add((name, prop))
                warnings.append(
                    f"{name}.{prop} was {values[prop]}, which no longer exists (deleted by hand): left as it is"
                )
                continue
            if not _same(_read(obj, prop), values[prop]):
                fc.set_prop(obj, prop, wanted)
    return skipped


def verify(fc_doc: Any, journal: Journal, skipped: set[tuple[str, str]] | None = None) -> list[str]:
    """Why the document is NOT the one before the op (empty when it is). What was skipped because the engineer deleted
    its object is not a problem: there is nothing to be restored."""
    skipped = skipped or set()
    problems = [f"{name} is still in the document" for name in journal.created if fc_doc.getObject(name) is not None]
    for name, values in journal.previous.items():
        obj = fc_doc.getObject(name)
        for prop, value in values.items():
            if obj is None or (name, prop) in skipped:
                continue
            if not _same(_read(obj, prop), value):
                problems.append(f"{name}.{prop} was not restored")
    return problems


def hand_work_warnings(fc_doc: Any, journal: Journal, feature_id: str, registered: set[str]) -> list[str]:
    """What this revert is about to do to the engineer's hand work, found BEFORE anything is deleted. Never a refusal.

    * hand-made objects (unknown to the registry) that depend on an object about to be deleted: they will be left
      broken (a fillet on a deleted pad, a sketch attached to it);
    * hand-made objects inside a group about to be deleted (a sketch added by hand to a Body): they stay, orphaned;
    * hand edits of the tracked properties the revert writes back (a Placement set by hand after the op, a Tip the
      engineer moved): the value from before the op overwrites them."""
    warnings: list[str] = []
    doomed = set(journal.created)
    for name in journal.created:
        obj = fc_doc.getObject(name)
        if obj is None:
            continue
        for dependent in obj.InList:
            if dependent.Name not in doomed and dependent.Name not in registered:
                warnings.append(
                    f"hand-made {dependent.Name} depends on {name}, which undoing {feature_id} deletes: "
                    "it is left broken"
                )
        # an App::Link shows the Group of the object it points at (another document's body), which it does not own
        owned = () if str(obj.TypeId) == "App::Link" else getattr(obj, "Group", None) or ()
        for member in owned:
            if member.Name not in doomed and member.Name not in registered:
                warnings.append(
                    f"hand-made {member.Name} was inside {name}, which undoing {feature_id} deletes: "
                    "it is left outside any group"
                )
    for name, values in journal.after.items():
        obj = fc_doc.getObject(name)
        if obj is None:
            continue
        for prop, expected in values.items():
            if not _same(_read(obj, prop), expected):
                warnings.append(
                    f"hand edit of {name}.{prop} discarded: undoing {feature_id} restores the value it had before"
                )
    return list(dict.fromkeys(warnings))


def revert(
    fc_doc: Any, journal: Journal, feature_id: str, operation_id: str | None, registered: set[str] | None = None
) -> list[str]:
    """Take the op that produced ``journal`` back: delete what it created, restore what it changed, recompute. Returns
    the warnings about the engineer's hand work (see ``hand_work_warnings``, plus what could not be restored).

    Atomic: on any failure the transaction is aborted (the document is exactly as before the call, and so are the
    macro recorder's lines) and a RECOVERABLE ``rebuild_failed`` error is raised -- the caller knows nothing changed
    and may retry. Only when the rollback itself fails is the document in an unknown state: ``degraded_session``."""
    if journal.removed and journal.extra is None:
        raise error(
            "degraded_session",
            f"feature {feature_id} deleted objects that already existed ({journal.removed}); "
            "the document and the registry are unchanged, but this op cannot be reverted by deletion",
            operation_id,
        )
    warnings = hand_work_warnings(fc_doc, journal, feature_id, registered or set())
    recorded = len(fc.recorder.lines)
    fc.open_transaction(fc_doc, f"undo {feature_id}")
    try:
        for name in reversed(journal.created):
            if fc_doc.getObject(name) is not None:
                fc.remove_object(fc_doc, name)
        if journal.extra is not None:
            journal.extra.apply(fc_doc, warnings)
        skipped = _restore_previous(fc_doc, journal, warnings)
        fc.recompute(fc_doc)
        problems = verify(fc_doc, journal, skipped)
        if journal.extra is not None:
            problems.extend(journal.extra.verify(fc_doc))
        if problems:
            raise RuntimeError("; ".join(problems))
    except Exception as exc:  # noqa: BLE001 - whatever failed, give the document back as it was
        try:
            fc.abort_transaction(fc_doc)
            fc.recompute(fc_doc)
        except Exception as abort_exc:
            raise error(
                "degraded_session",
                f"undo_last could not revert feature {feature_id} ({exc}) and the rollback failed too ({abort_exc}): "
                "the document may be half reverted",
                operation_id,
                feature=feature_id,
            ) from abort_exc
        finally:
            del fc.recorder.lines[recorded:]
        if isinstance(exc, WorkerError):
            raise
        raise error(
            "rebuild_failed",
            f"undo_last could not revert feature {feature_id}: {exc}; "
            "the document and the registry are unchanged (the revert was rolled back)",
            operation_id,
            feature=feature_id,
        ) from exc
    fc.commit_transaction(fc_doc)
    try:
        fc.clear_undos(fc_doc)
    except Exception as exc:  # noqa: BLE001 - the document IS reverted: carry on, but say what that leaves behind
        warnings.append(
            f"the undo stack could not be cleared ({exc}): a later Ctrl+Z in FreeCAD could resurrect deleted objects"
        )
    return warnings
