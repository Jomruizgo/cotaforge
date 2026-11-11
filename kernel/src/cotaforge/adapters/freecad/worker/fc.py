"""Thin FreeCAD wrappers: every document mutation executes the call AND records the equivalent source line.

Shape queries are not recorded. ``transaction_open`` lets ``main`` abort a dangling transaction after an
uncaught exception. ``harness_operation``/``harness_busy`` let the document-change observer (live
manual-edit seam, ``worker/gui.py``'s ``_DocumentWatcher``) tell a harness-issued recompute apart from one
the engineer triggered by hand: ``worker/main.py`` wraps every dispatched RPC in ``harness_operation()``,
so a recompute the observer sees while it is still open is never reported as a manual edit.
"""

from typing import Any

import FreeCAD as App
import Sketcher
from recorder import Recorder, doc_key, literal, register_document

recorder = Recorder()
transaction_open: dict[str, bool] = {}

_suppress_depth = 0


class _HarnessOperation:
    """Reentrant marker: nested ``harness_operation()`` blocks (there are none today, but a handler that
    called another would still suppress correctly) keep the count balanced."""

    def __enter__(self) -> None:
        global _suppress_depth  # noqa: PLW0603
        _suppress_depth += 1

    def __exit__(self, *exc: object) -> None:
        global _suppress_depth  # noqa: PLW0603
        _suppress_depth -= 1


def harness_operation() -> _HarnessOperation:
    """Context manager: while open, ``harness_busy()`` is true. Wrap every dispatched RPC method in this
    (``worker/main.py``'s ``Worker.dispatch``), not just the ones that open a ``fc.transaction`` --
    ``undo_last`` recomputes without one, and a query method recomputing nothing is a harmless no-op wrap."""
    return _HarnessOperation()


def harness_busy() -> bool:
    """True while a dispatched RPC method is running: the document-change observer uses this to skip a
    recompute the harness itself caused instead of reporting it as a hand edit."""
    return _suppress_depth > 0


def _ref(obj: Any) -> str:
    return f"O({doc_key(obj.Document)!r}, {obj.Name!r})"


def new_document(name: str, doc_id: str) -> Any:
    doc = App.newDocument(name)
    doc.UndoMode = 1
    register_document(doc, doc_id)
    recorder.record(f"docs[{doc_id!r}] = App.newDocument({name!r})")
    recorder.record(f"docs[{doc_id!r}].UndoMode = 1")
    return doc


def close_document(doc: Any) -> None:
    name = doc.Name
    recorder.record(f"App.closeDocument({name!r})")
    App.closeDocument(name)


def add_object(doc: Any, type_name: str, name: str, label: str | None = None) -> Any:
    """Create a document-level object. ``name`` is FreeCAD's internal id (what registries, selection tokens and the
    recorder use); ``label`` is what the model tree shows (see ``set_label``)."""
    obj = doc.addObject(type_name, name)
    recorder.record(f"docs[{doc_key(doc)!r}].addObject({type_name!r}, {obj.Name!r})")
    if label is not None:
        set_label(obj, label)
    return obj


def new_in_body(body: Any, type_name: str, name: str, label: str | None = None) -> Any:
    obj = body.newObject(type_name, name)
    recorder.record(f"{_ref(body)}.newObject({type_name!r}, {obj.Name!r})")
    if label is not None:
        set_label(obj, label)
    return obj


def set_label(obj: Any, label: str) -> None:
    """Set the ``Label`` (the model tree's text) of an object, recorded like any other property.

    ``Name`` is never touched: it is the identity every registry, selection token and macro line refers to, and
    FreeCAD cannot rename it anyway. FreeCAD keeps a Label unique inside its document (``DuplicateLabels`` off): if
    another object already owns ``label`` this object ends up with FreeCAD's own digit suffix (``label001``) and
    nothing raises (``obj.Label`` is the truth; ``OpContext.adopt`` carries it to the feature). A collision needs an
    object named by hand, or an op whose id equals a helper label of another (op ``base`` makes ``base_body``, a later
    op called ``base_body``): op ids are unique in a plan, and an undone or rolled-back op takes its objects, and so
    their labels, with it (same transaction), so rebuilds never collide with their own past."""
    set_prop(obj, "Label", label)


def remove_object(doc: Any, name: str) -> None:
    recorder.record(f"docs[{doc_key(doc)!r}].removeObject({name!r})")
    doc.removeObject(name)


def set_prop(obj: Any, name: str, value: Any, source: str | None = None) -> None:
    """Set a property; ``source`` overrides the recorded right-hand side (opaque values such as shapes)."""
    setattr(obj, name, value)
    recorder.record(f"{_ref(obj)}.{name} = {source if source is not None else literal(value)}")


def add_constraint(sketch: Any, *args: Any) -> int:
    index = sketch.addConstraint(Sketcher.Constraint(*args))
    recorder.record(f"{_ref(sketch)}.addConstraint(Sketcher.Constraint(" + ", ".join(literal(a) for a in args) + "))")
    return int(index)


def call(obj: Any, method: str, *args: Any) -> Any:
    result = getattr(obj, method)(*args)
    recorder.record(f"{_ref(obj)}.{method}(" + ", ".join(literal(a) for a in args) + ")")
    return result


def recompute(doc: Any) -> None:
    doc.recompute()
    recorder.record(f"docs[{doc_key(doc)!r}].recompute()")


def recompute_objects(doc: Any, objs: list[Any]) -> None:
    """Recompute only ``objs`` and what they depend on: what comes after them stays touched, so no feature that refers
    to the topology of a shape that is not placed yet is asked to resolve it (FreeCAD rewrites such a reference as
    missing, for good)."""
    doc.recompute(objs)
    key = doc_key(doc)
    listed = ", ".join(f"docs[{key!r}].getObject({o.Name!r})" for o in objs)
    recorder.record(f"docs[{key!r}].recompute([{listed}])")


def open_transaction(doc: Any, name: str) -> None:
    doc.openTransaction(name)
    transaction_open[doc.Name] = True
    recorder.record(f"docs[{doc_key(doc)!r}].openTransaction({name!r})")


def commit_transaction(doc: Any) -> None:
    doc.commitTransaction()
    transaction_open[doc.Name] = False
    recorder.record(f"docs[{doc_key(doc)!r}].commitTransaction()")


def abort_transaction(doc: Any) -> None:
    doc.abortTransaction()
    transaction_open[doc.Name] = False
    recorder.record(f"docs[{doc_key(doc)!r}].abortTransaction()")


def clear_undos(doc: Any) -> None:
    """Empty the document's undo and redo stacks (``journal.revert``: a deletion-based undo must not leave transactions
    behind that a later ``doc.undo()`` or a GUI Ctrl+Z could replay over objects that are gone)."""
    doc.clearUndos()
    recorder.record(f"docs[{doc_key(doc)!r}].clearUndos()")


def abort_open_transactions() -> None:
    for name, is_open in list(transaction_open.items()):
        if not is_open:
            continue
        doc = App.getDocument(name)
        if doc is not None:
            try:
                abort_transaction(doc)
                doc.recompute()
            except Exception:  # noqa: BLE001 - best effort cleanup after an internal error
                transaction_open[name] = False


# --- spike shims (FreeCAD 1.1 enum renames) ---------------------------------------------------------------
_PATTERN_MODE_11 = {"length": "Extent", "angle": "Extent", "offset": "Spacing"}


def set_pattern_mode(obj: Any, mode: str) -> None:
    """LinearPattern/PolarPattern ``Mode``: 1.0 uses ``length``/``angle``/``offset``; 1.1 renamed them to
    ``Extent``/``Spacing``."""
    have = set(obj.getEnumerationsOfProperty("Mode"))
    set_prop(obj, "Mode", mode if mode in have else _PATTERN_MODE_11[mode])


def _thread_numbers(designation: str) -> tuple[float, float | None]:
    """``M45x1.5`` -> (45.0, 1.5); ``M6`` -> (6.0, None): so ``M8x1`` and FreeCAD's ``M8x1.0`` are equal."""
    major, _, pitch = designation[1:].partition("x")
    return float(major), (float(pitch) if pitch else None)


def set_thread_size(obj: Any, size: str) -> bool:
    """``PartDesign::Hole.ThreadSize`` for an ISO metric ``size`` (``M6`` coarse, ``M45x1.5`` fine): 1.1 spells the
    enum ``M6x1.0`` (size and pitch), 1.0 plain ``M6``, and the list only exists once ``ThreadType`` is set (this sets
    it: ``ISOMetricProfile`` for a coarse size, ``ISOMetricFineProfile`` for one with a pitch, the coarse list as the
    second place to look for a coarse thread spelled with its pitch). Returns ``False`` when this FreeCAD has no such
    size (nothing is set)."""
    major, pitch = _thread_numbers(size)
    kinds = ("ISOMetricProfile",) if pitch is None else ("ISOMetricFineProfile", "ISOMetricProfile")
    for kind in kinds:
        set_prop(obj, "ThreadType", kind)
        for entry in obj.getEnumerationsOfProperty("ThreadSize"):
            got_major, got_pitch = _thread_numbers(entry)
            if got_major == major and (pitch is None or got_pitch == pitch or got_pitch is None):
                set_prop(obj, "ThreadSize", entry)
                return True
    return False


def set_two_sides(obj: Any) -> None:
    """Pad/Pocket with two independent lengths: ``Type='TwoLengths'`` (1.0) or ``SideType='Two sides'`` (1.1,
    where ``TwoLengths`` is hidden as ``?TwoLengths`` and produces a null shape)."""
    if "SideType" in obj.PropertiesList:
        set_prop(obj, "SideType", "Two sides")
        set_prop(obj, "Type", "Length")
        set_prop(obj, "Type2", "Length")
    else:
        set_prop(obj, "Type", "TwoLengths")
