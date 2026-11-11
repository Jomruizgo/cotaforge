"""The visible worker (``FreeCADSession(visible=True)``: the FreeCAD GUI binary, accompanied mode):
``set_view`` orients the view and ``capture`` writes a PNG, the window closes cleanly on ``disconnect`` and on a
dead client (stdin EOF), and selection / hand-edit notifications arrive. Per-method latencies are collected
through a timing subclass and printed (``pytest -s``).

Needs a display (on Windows the GUI binary opens its own window): FreeCAD's GUI binary cannot run headless, so the
module is skipped without one. ``conftest.py`` already tags it ``freecad`` and skips it without the FreeCAD root.
"""

from __future__ import annotations

import os
import statistics
import sys
import time
from collections import defaultdict
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter, RpcFailure
from cotaforge.adapters.freecad.rpc import Liveness
from cotaforge.contract import (
    Ack,
    AdapterError,
    BoxRequest,
    CaptureRequest,
    ConnectRequest,
    DocumentKind,
    Empty,
    EntityRequest,
    ErrorCode,
    ExtrudeRequest,
    ManualEditNotice,
    MassRequest,
    NewDocumentRequest,
    PlaneName,
    RectEntity,
    Request,
    SelectionResult,
    SketchRequest,
    ViewRequest,
)
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutorState
from cotaforge.ir import parse_plan
from cotaforge.verification.verifier import Verifier
from tests.conftest import plate_plan_dict

pytestmark = [
    pytest.mark.freecad,
    pytest.mark.skipif(
        sys.platform != "win32" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
        reason="the visible worker needs a display (DISPLAY / WAYLAND_DISPLAY)",
    ),
]


class _TimedAdapter(FreeCADAdapter):
    """Records the client-side round trip of every RPC, per method."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.latencies_ms: dict[str, list[float]] = defaultdict(list)

    def _raw_call(
        self, method: str, request: Request, timeout_s: float | None = None, liveness: Liveness | None = None
    ) -> dict[str, Any]:
        started = time.perf_counter()
        try:
            return super()._raw_call(method, request, timeout_s, liveness)
        finally:
            self.latencies_ms[method].append((time.perf_counter() - started) * 1000.0)


def _summary(latencies: dict[str, list[float]]) -> str:
    rows = []
    for method, values in sorted(latencies.items()):
        rows.append(
            f"{method:<18} n={len(values):<3} median={statistics.median(values):8.1f} ms max={max(values):8.1f} ms"
        )
    return "\n".join(rows)


@pytest.fixture(scope="module")
def visible(freecad_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[_TimedAdapter]:
    started = time.perf_counter()
    adapter = _TimedAdapter(
        freecad_root=freecad_root,
        timeout_s=60.0,
        log_dir=tmp_path_factory.mktemp("visible-logs"),
        visible=True,
        enable_test_hooks=True,
    )
    with adapter:
        adapter.latencies_ms["connect"].append((time.perf_counter() - started) * 1000.0)
        yield adapter
    print("\nvisible worker latencies (client round trips):\n" + _summary(adapter.latencies_ms))


def test_visible_session_opens_the_gui_worker_and_offers_set_view_and_capture(visible: _TimedAdapter) -> None:
    assert visible.worker_pid is not None
    assert visible.visible is True
    assert {"set_view", "capture"} <= visible.capabilities().tools


def test_a_plan_on_the_visible_worker_matches_the_headless_volume(visible: _TimedAdapter) -> None:
    """Also leaves the plate as the active document for the capture and view tests below."""
    executor = Executor(parse_plan(plate_plan_dict()), visible, Verifier(), ExecutorConfig())
    executor.start()
    executor.run_to_end()
    trace = executor.trace()
    assert trace.final_state is ExecutorState.completed, trace.active_steps()[-1]
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(46905.2178, abs=1e-3)


def test_capture_writes_a_png_of_the_active_document(visible: _TimedAdapter, tmp_path: Path) -> None:
    target = tmp_path / "shots" / "plate.png"
    ack = visible.capture(CaptureRequest(operation_id="cap", path=str(target), width=640, height=400))
    assert ack == Ack()
    assert target.is_file()
    data = target.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(data) > 1000


@pytest.mark.parametrize("name", ["front", "top", "right", "iso"])
def test_set_view_then_capture_writes_a_png_per_view(visible: _TimedAdapter, tmp_path: Path, name: str) -> None:
    assert visible.set_view(ViewRequest(operation_id="view", name=name)) == Ack()
    target = tmp_path / f"{name}.png"
    visible.capture(CaptureRequest(operation_id="cap", path=str(target), width=640, height=400))
    data = target.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    assert len(data) > 1000


def test_headless_worker_answers_set_view_not_supported(adapter: FreeCADAdapter) -> None:
    adapter.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="headless"))
    assert "set_view" not in adapter.capabilities().tools
    with pytest.raises(AdapterError) as info:
        adapter.set_view(ViewRequest(operation_id="view", name="front"))
    assert info.value.info.code is ErrorCode.not_supported


def test_view_tools_without_a_document_are_no_active_document(freecad_root: Path, tmp_path: Path) -> None:
    with FreeCADAdapter(freecad_root=freecad_root, timeout_s=60.0, log_dir=tmp_path / "logs", visible=True) as adapter:
        with pytest.raises(AdapterError) as info:
            adapter.set_view(ViewRequest(operation_id="view", name="front"))
        assert info.value.info.code is ErrorCode.no_active_document
        with pytest.raises(AdapterError) as info:
            adapter.capture(CaptureRequest(operation_id="cap", path=str(tmp_path / "none.png")))
        assert info.value.info.code is ErrorCode.no_active_document


def test_headless_worker_answers_capture_not_supported(adapter: FreeCADAdapter, tmp_path: Path) -> None:
    adapter.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="headless"))
    assert "capture" not in adapter.capabilities().tools
    with pytest.raises(AdapterError) as info:
        adapter.capture(CaptureRequest(operation_id="cap", path=str(tmp_path / "headless.png")))
    assert info.value.info.code is ErrorCode.not_supported
    assert not (tmp_path / "headless.png").exists()


def test_visible_worker_exits_on_stdin_eof_with_a_modified_document(freecad_root: Path, tmp_path: Path) -> None:
    """A dead client (no ``disconnect``): the worker closes its documents itself and quits without the
    "Save changes?" dialog that would otherwise hold the process until the kill grace expires."""
    adapter = FreeCADAdapter(freecad_root=freecad_root, timeout_s=60.0, log_dir=tmp_path / "logs", visible=True)
    adapter.connect(ConnectRequest(operation_id="connect", timeout_s=60.0))
    adapter.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="dirty"))
    adapter.box(BoxRequest(operation_id="b", size=(10.0, 20.0, 30.0)))  # unsaved geometry: the document is modified
    rpc = adapter._rpc
    assert rpc is not None
    started = time.perf_counter()
    rpc.stop(grace_s=5.0)  # closes stdin only; a hang on the save dialog would end in SIGKILL (rc -9) after 5 s
    elapsed = time.perf_counter() - started
    print(f"\nvisible worker exit on stdin EOF with a modified document: {elapsed:.2f} s, rc {rpc.returncode}")
    assert rpc.returncode == 0, f"worker exit code {rpc.returncode} after {elapsed:.2f} s"
    assert elapsed < 4.0
    assert adapter.disconnect(Empty(operation_id="bye")) == Ack()


def _wait_until(predicate: Any, timeout_s: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.02)
    assert predicate(), "condition never became true"


def test_selection_notification_reports_a_programmatically_selected_face_by_stable_ref(
    visible: _TimedAdapter,
) -> None:
    """``Gui.Selection.addSelection`` (driven here through the test-only ``test_select_entity`` RPC hook,
    standing in for a mouse click) -> the debounced ``Gui.Selection`` observer -> ``selection_changed`` ->
    ``FreeCADAdapter._on_notification`` -> the registered listener, with the same stable ref
    ``persistent_reference`` would return for the same face."""
    visible.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="selection-test"))
    box = visible.box(BoxRequest(operation_id="b", size=(10.0, 20.0, 30.0)))
    face = box.created_faces[0]
    expected_ref = visible.persistent_reference(EntityRequest(operation_id="ref", entity=face))

    received: list[SelectionResult] = []
    visible.set_selection_listener(received.append)
    rpc = visible._rpc
    assert rpc is not None
    rpc.call("test_select_entity", {"operation_id": "sel", "entity": {"id": face.id}}, timeout_s=10.0)

    _wait_until(lambda: len(received) >= 1, timeout_s=5.0)  # > the worker's ~150ms selection debounce
    entries = received[-1].entries
    assert len(entries) == 1
    entry = entries[0]
    assert entry.kind == "face"
    assert entry.ref.token == expected_ref.token
    assert "· Face" in entry.label
    visible.set_selection_listener(lambda _result: None)  # stop appending before the next test


def test_manual_edit_notification_is_pushed_when_a_property_is_changed_outside_a_harness_operation(
    visible: _TimedAdapter,
) -> None:
    """``test_edit_property`` sets ``Box.Length`` and recomputes OUTSIDE ``fc.harness_operation()``
    (``worker/main.py``'s ``TEST_HOOK_METHODS``), standing in for the engineer editing it by hand in the
    property panel: the document-change observer must see ``fc.harness_busy() is False`` at that point and
    push a ``manual_edit`` notification, unlike every real op's own recompute (already covered by every
    other passing test in this file: none of them ever triggers one)."""
    visible.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="manual-edit-test"))
    box = visible.box(BoxRequest(operation_id="b", size=(10.0, 20.0, 30.0)))

    received: list[ManualEditNotice] = []
    visible.set_manual_edit_listener(received.append)
    rpc = visible._rpc
    assert rpc is not None
    rpc.call(
        "test_edit_property",
        {"operation_id": "edit", "object": "Box", "property": "Length", "value": 15.0},
        timeout_s=10.0,
    )

    _wait_until(lambda: len(received) >= 1, timeout_s=5.0)
    notice = received[-1]
    assert notice.artefact_kind == "feature"
    assert notice.ref == box.feature.id
    assert notice.by == "engineer"
    assert any("10" in line and "15" in line for line in notice.changes)
    visible.set_manual_edit_listener(lambda _notice: None)  # stop appending before the next test


def _hand(visible: _TimedAdapter, what: str, **params: Any) -> None:
    """One more thing the engineer does by hand in the window (``queries.test_hand_edit``)."""
    rpc = visible._rpc
    assert rpc is not None
    rpc.call("test_hand_edit", {"operation_id": "hand", "what": what, **params}, timeout_s=15.0)


def _view_hook(visible: _TimedAdapter, method: str, **params: Any) -> Any:
    rpc = visible._rpc
    assert rpc is not None
    return rpc.call(method, {"operation_id": "hook", **params}, timeout_s=15.0)


def _listening(visible: _TimedAdapter) -> list[ManualEditNotice]:
    received: list[ManualEditNotice] = []
    visible.set_manual_edit_listener(received.append)
    return received


def _settled(received: list[ManualEditNotice], seconds: float = 1.2) -> list[ManualEditNotice]:
    """Wait well past the watch's settle time, so a notice that was going to come has come."""
    time.sleep(seconds)
    return list(received)


def test_objects_added_removed_and_renamed_by_hand_are_pushed(visible: _TimedAdapter) -> None:
    visible.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="structure-test"))
    box = visible.box(BoxRequest(operation_id="b", size=(10.0, 20.0, 30.0)))
    received = _listening(visible)

    _hand(visible, "rename", object="Box", value="Base plate")
    _wait_until(lambda: len(received) >= 1, timeout_s=5.0)
    assert (received[-1].action, received[-1].ref, received[-1].name, received[-1].document) == (
        "edited",
        box.feature.id,
        "Box",
        "structure-test",
    )
    assert received[-1].changes == ("Label: 'b' -> 'Base plate'",)

    _hand(visible, "add_box", object="Extra")
    _wait_until(lambda: any(n.action == "added" for n in received), timeout_s=5.0)
    added = next(n for n in received if n.action == "added")
    assert (added.artefact_kind, added.ref, added.changes) == ("object", "Extra", ("added Part::Box",))

    _hand(visible, "remove", object="Extra")
    _wait_until(lambda: any(n.action == "removed" for n in received), timeout_s=5.0)
    removed = next(n for n in received if n.action == "removed")
    assert (removed.ref, removed.changes) == ("Extra", ("removed Part::Box",))
    visible.set_manual_edit_listener(lambda _notice: None)


def test_a_body_moved_by_hand_and_an_undo_by_the_engineer_are_pushed(visible: _TimedAdapter) -> None:
    visible.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="move-test"))
    visible.box(BoxRequest(operation_id="b", size=(10.0, 20.0, 30.0)))
    received = _listening(visible)

    _hand(visible, "move", object="Box", value=[5.0, 6.0, 7.0])
    _wait_until(lambda: len(received) >= 1, timeout_s=5.0)
    assert received[-1].changes == ("Placement: 'at (0, 0, 0) turned (0, 0, 0)' -> 'at (5, 6, 7) turned (0, 0, 0)'",)

    received.clear()
    _hand(visible, "undo")  # undoes the move
    _wait_until(lambda: len(received) >= 1, timeout_s=5.0)
    assert received[-1].reason == "undo in the FreeCAD GUI"
    assert "Placement" in received[-1].changes[0]
    visible.set_manual_edit_listener(lambda _notice: None)


def test_a_sketch_edited_by_hand_is_pushed(visible: _TimedAdapter) -> None:
    visible.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="sketch-test"))
    sketch = visible.sketch(
        SketchRequest(
            operation_id="s", on=PlaneName.XY, entities=(RectEntity(name="o", center=(0, 0), width=10, height=10),)
        )
    )
    visible.extrude(ExtrudeRequest(operation_id="e", sketch=sketch.sketch, depth_mm=5.0))
    received = _listening(visible)

    _hand(visible, "sketch_line", object="Sketch")
    _wait_until(lambda: len(received) >= 1, timeout_s=5.0)
    assert received[-1].name == "Sketch"
    assert any(line.startswith("geometry: ") for line in received[-1].changes)
    visible.set_manual_edit_listener(lambda _notice: None)


def test_nothing_is_pushed_for_what_is_not_a_change_of_the_design(visible: _TimedAdapter) -> None:
    """Camera, visibility, colour and selection are the engineer looking at the model, not changing it."""
    visible.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="look-test"))
    box = visible.box(BoxRequest(operation_id="b", size=(10.0, 20.0, 30.0)))
    received = _listening(visible)
    selected: list[SelectionResult] = []
    visible.set_selection_listener(selected.append)

    _hand(visible, "camera")
    _view_hook(visible, "test_set_view_color", object="Box", property="Visibility", value=False)
    _view_hook(visible, "test_set_view_color", object="Box", property="Visibility", value=True)
    _view_hook(visible, "test_set_view_color", object="Box", property="DiffuseColor", value=[[1.0, 0.0, 0.0, 0.0]])
    _view_hook(visible, "test_select_entity", entity={"id": box.created_faces[0].id})
    time.sleep(0.5)
    visible.set_view(ViewRequest(operation_id="v", name="top"))
    assert _settled(received) == []
    assert selected, "the selection watch itself still works (the MCP server just does not push it)"
    visible.set_selection_listener(lambda _result: None)
    visible.set_manual_edit_listener(lambda _notice: None)


def test_the_harness_own_operations_push_nothing_even_after_the_settle_time(visible: _TimedAdapter) -> None:
    visible.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="harness-test"))
    received = _listening(visible)
    visible.box(BoxRequest(operation_id="b1", size=(10.0, 20.0, 30.0)))
    visible.box(BoxRequest(operation_id="b2", size=(5.0, 5.0, 5.0)))
    assert visible.undo_last(Empty(operation_id="u")).reverted is not None
    visible.set_view(ViewRequest(operation_id="v", name="front"))
    assert _settled(received) == []
    # and the hand edit that follows is measured from where the harness left the model, not from before its ops
    _view_hook(visible, "test_edit_property", object="Box", property="Length", value=12.0)
    _wait_until(lambda: len(received) >= 1, timeout_s=5.0)
    assert received[-1].changes == ("Length: 10.0 -> 12.0",)
    visible.set_manual_edit_listener(lambda _notice: None)


def test_a_hand_edit_made_just_before_a_request_arrives_before_that_requests_reply(visible: _TimedAdapter) -> None:
    visible.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="flush-test"))
    visible.box(BoxRequest(operation_id="b", size=(10.0, 20.0, 30.0)))
    received = _listening(visible)
    _view_hook(visible, "test_edit_property", object="Box", property="Length", value=14.0)
    visible.mass_properties(MassRequest(operation_id="m"))  # well inside the settle time
    assert [n.changes for n in received] == [("Length: 10.0 -> 14.0",)]
    visible.set_manual_edit_listener(lambda _notice: None)


def test_test_hooks_are_unknown_methods_without_the_opt_in_env_var(freecad_root: Path, tmp_path: Path) -> None:
    """``test_select_entity``/``test_edit_property`` (``worker/main.py``'s ``TEST_HOOK_METHODS``) must not
    exist on a worker started the ordinary way -- only ``FreeCADAdapter(enable_test_hooks=True)`` (the
    ``visible`` fixture above) sets ``COTAFORGE_WORKER_TEST_HOOKS``. Calling either by name here must fail
    exactly like any other typo'd method, never actually select anything or edit a property."""
    with FreeCADAdapter(freecad_root=freecad_root, timeout_s=60.0, log_dir=tmp_path / "logs", visible=True) as adapter:
        adapter.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="no-hooks"))
        box = adapter.box(BoxRequest(operation_id="b", size=(10.0, 20.0, 30.0)))
        rpc = adapter._rpc
        assert rpc is not None
        with pytest.raises(RpcFailure) as info:
            rpc.call(
                "test_select_entity", {"operation_id": "sel", "entity": {"id": box.created_faces[0].id}}, timeout_s=10.0
            )
        assert info.value.error["code"] == "not_supported"
        with pytest.raises(RpcFailure) as info:
            rpc.call(
                "test_edit_property",
                {"operation_id": "edit", "object": "Box", "property": "Length", "value": 15.0},
                timeout_s=10.0,
            )
        assert info.value.error["code"] == "not_supported"
