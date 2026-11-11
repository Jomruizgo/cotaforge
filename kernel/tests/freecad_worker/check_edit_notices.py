"""The harness's own edits never reach the agent as hand edits.

The document observer that raises ``manual_edit`` notices exists only in the visible worker
(``gui.start_document_watch`` returns at once when ``gui.ACTIVE`` is false, and ``main.WATCH`` stays ``None``
headless), so this check arms the same
policy (``watch.ManualEditWatch`` over the real ``changes.capture``) with an observer of the same shape as
``gui._DocumentWatcher`` and drives real FreeCAD events through it: a request of the harness raises events (the
observer proves it) and reports nothing; the same change made outside a request is reported. The last run needing a
window is only the Qt timer and the pipe to the server.
"""

from typing import Any

import FreeCAD as App
import main
import watch
from check_edit_dimension import _edit, _pad_edit, _plate
from harness import call, op


class _Observer:
    """The slots of ``gui._DocumentWatcher``, minus the Qt-thread test (this check runs on the main thread)."""

    def __init__(self, on_event: Any) -> None:
        self.on_event = on_event
        self.seen: list[tuple[str, str | None]] = []

    def _tell(self, doc_name: str, cause: str, name: str | None = None) -> None:
        self.seen.append((cause, name))
        self.on_event(doc_name, cause, name)

    def slotRecomputedDocument(self, doc: Any) -> None:  # noqa: N802
        self._tell(str(doc.Name), "recompute")

    def slotCreatedObject(self, obj: Any) -> None:  # noqa: N802
        self._tell(str(obj.Document.Name), "added", str(obj.Name))

    def slotDeletedObject(self, obj: Any) -> None:  # noqa: N802
        self._tell(str(obj.Document.Name), "removed", str(obj.Name))

    def slotChangedObject(self, obj: Any, prop: Any) -> None:  # noqa: N802
        if str(prop) != "Visibility":
            self._tell(str(obj.Document.Name), "changed", str(obj.Name))

    def slotUndoDocument(self, doc: Any) -> None:  # noqa: N802
        self._tell(str(doc.Name), "undo")

    def slotRedoDocument(self, doc: Any) -> None:  # noqa: N802
        self._tell(str(doc.Name), "redo")


def _arm() -> tuple[Any, _Observer, list[Any]]:
    reports: list[Any] = []
    manual = watch.ManualEditWatch(
        capture=main._capture_document,  # noqa: SLF001
        known=lambda name: main._document_rec(name) is not None,  # noqa: SLF001
        known_docs=lambda: [n for n in map(main._live_name, list(main.WORKER.documents.values())) if n],  # noqa: SLF001
        busy=main.fc.harness_busy,
        report=lambda name, edits: reports.append((name, edits)),
        schedule=lambda delay, callback: callback,
        cancel=lambda handle: None,
    )
    main.WATCH = manual
    observer = _Observer(manual.on_event)
    App.addDocumentObserver(observer)
    return manual, observer, reports


def _disarm(observer: _Observer) -> None:
    main.WATCH = None
    App.removeDocumentObserver(observer)


def check_an_in_place_edit_raises_events_but_no_hand_edit_notice() -> None:
    ids = _plate()
    manual, observer, reports = _arm()
    try:
        call("mass_properties", op("seed"))  # the request that ends gives the document its baseline
        manual.settle()
        assert not reports and not observer.seen, (reports, observer.seen)
        result = _edit([_pad_edit(ids, 12.0)])
        assert result["applied"], result
        assert any(cause == "changed" for cause, _ in observer.seen), "FreeCAD raised no event for the edit"
        manual.settle()
        assert reports == [], f"the harness's own edit was reported as a hand edit: {reports}"
    finally:
        _disarm(observer)


def check_a_refused_step_and_its_rollback_raise_events_but_no_hand_edit_notice() -> None:
    _plate()
    manual, observer, reports = _arm()
    try:
        call("mass_properties", op("seed"))
        manual.settle()
        failed = call(
            "run_native", op("boom", code="doc.addObject('Part::Box', 'Tmp')\nraise ValueError('no')", timeout_s=30)
        )
        assert failed["ok"] is False and failed["rolled_back"] is True, failed
        assert observer.seen, "FreeCAD raised no event for the step and its rollback"
        manual.settle()
        assert reports == [], f"a rolled back step was reported as a hand edit: {reports}"
    finally:
        _disarm(observer)


def check_the_same_change_made_by_hand_is_reported_and_reported_again_when_put_back() -> None:
    _plate()
    manual, observer, reports = _arm()
    try:
        call("mass_properties", op("seed"))
        manual.settle()
        doc = main.WORKER.active.fc_doc
        pad = next(o for o in doc.Objects if o.TypeId == "PartDesign::Pad")
        was = pad.Length.Value
        pad.Length = was + 4  # outside any request: the engineer
        doc.recompute()
        manual.settle()
        lines = [line for _, edits in reports for e in edits for line in e.lines]
        assert any(line.startswith("Length: ") for line in lines), reports
        pad.Length = was
        doc.recompute()
        manual.settle()
        back = [line for _, edits in reports[1:] for e in edits for line in e.lines]
        assert any(line.startswith("Length: ") for line in back), reports  # the put-back is a notice of its own
        first = next(line for line in lines if line.startswith("Length: "))
        second = next(line for line in back if line.startswith("Length: "))
        old, new = first.removeprefix("Length: ").split(" -> ")
        assert second == f"Length: {new} -> {old}", (first, second)  # the exact inverse: the server can match them
    finally:
        _disarm(observer)
