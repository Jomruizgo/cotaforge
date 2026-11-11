"""``FreeCADAdapter``'s live push seams (``contract.SelectionProducer``/``ManualEditProducer``) against
``fake_worker.py``'s ``notify`` mode: no FreeCAD needed, ``adapters/rpc.py`` and the worker's real framing
do all the work, only the child process is a stand-in."""

import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter, rpc
from cotaforge.contract import (
    ConnectRequest,
    DocumentKind,
    Empty,
    ExtrudeRequest,
    ManualEditNotice,
    NewDocumentRequest,
    SelectionResult,
    SketchHandle,
)
from tests.platform_support import PLAIN_PYTHON, stub_freecad_root

FAKE_WORKER = Path(__file__).with_name("fake_worker.py")
AdapterFactory = Callable[..., FreeCADAdapter]


@pytest.fixture
def fake_root(tmp_path: Path) -> Path:
    return stub_freecad_root(tmp_path / "root")


@pytest.fixture
def make_adapter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fake_root: Path) -> Iterator[AdapterFactory]:
    created: list[FreeCADAdapter] = []

    def factory(mode: str = "notify", **kwargs: object) -> FreeCADAdapter:
        command = [PLAIN_PYTHON, str(FAKE_WORKER), mode]
        monkeypatch.setattr(rpc, "worker_command", lambda root, worker_dir, **kw: command)
        monkeypatch.setenv("FAKE_WORKER_DELAY_S", "2")
        kwargs.setdefault("log_dir", tmp_path / "logs")
        adapter = FreeCADAdapter(fake_root, **kwargs)  # type: ignore[arg-type]
        created.append(adapter)
        return adapter

    yield factory
    for adapter in created:
        adapter.disconnect(Empty(operation_id="cleanup"))


def _wait_until(predicate: Callable[[], bool], timeout_s: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    assert predicate(), "condition never became true"


def test_selection_notification_is_translated_and_forwarded_to_the_registered_listener(
    make_adapter: AdapterFactory,
) -> None:
    adapter = make_adapter()
    received: list[SelectionResult] = []
    adapter.set_selection_listener(received.append)
    adapter.connect(ConnectRequest(operation_id="connect", timeout_s=5.0))
    adapter.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="plate"))
    _wait_until(lambda: len(received) >= 1)
    result = received[0]
    assert len(result.entries) == 1
    entry = result.entries[0]
    assert entry.kind == "face"
    assert entry.ref.token == "doc:1|body:1|face:3"
    assert entry.label == "Pad · Face6"
    assert entry.owner_label == "Pad"
    assert entry.body is not None and entry.body.id == "body:1"
    assert entry.feature is not None and entry.feature.id == "feature:1"


def test_manual_edit_notification_is_translated_and_forwarded_to_the_registered_listener(
    make_adapter: AdapterFactory,
) -> None:
    adapter = make_adapter()
    received: list[ManualEditNotice] = []
    adapter.set_manual_edit_listener(received.append)
    adapter.connect(ConnectRequest(operation_id="connect", timeout_s=5.0))
    adapter.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="plate"))
    adapter.extrude(ExtrudeRequest(operation_id="e", sketch=SketchHandle(id="sketch:1"), depth_mm=8.0))
    _wait_until(lambda: len(received) >= 1)
    notice = received[0]
    assert notice.artefact_kind == "feature"
    assert notice.ref == "feature:1"
    assert notice.by == "engineer"
    assert notice.changes == ("Length: 10.0 -> 15.0",)
    assert (notice.document, notice.name, notice.action) == ("plate", "Pad", "edited")


def test_a_manual_edit_notification_from_an_older_worker_still_validates() -> None:
    """A worker that does not say which document, object or kind of change (the fields came later) is read with
    their defaults."""
    notice = ManualEditNotice.model_validate(
        {"artefact_kind": "object", "ref": "Box", "by": "engineer", "reason": "r", "changes": ["Length: 1 -> 2"]}
    )
    assert (notice.document, notice.name, notice.action) == ("", "", "edited")


def test_a_notification_never_disturbs_the_in_flight_call_it_is_interleaved_with(make_adapter: AdapterFactory) -> None:
    """``new_document``'s own reply is sent right after the ``selection_changed`` notification
    (fake_worker's ``notify`` mode) on the same channel; the call must still return the right result, and
    a follow-up call must still work -- the notification never desynchronised the response queue."""
    adapter = make_adapter()
    adapter.connect(ConnectRequest(operation_id="connect", timeout_s=5.0))
    info = adapter.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="plate"))
    assert info.name == "plate"
    canary = adapter.canary(Empty(operation_id="c"))
    assert canary.healthy is True


def test_capabilities_omit_get_selection_when_headless(make_adapter: AdapterFactory) -> None:
    """Client-side gating, no worker call needed: mirrors ``set_view``/``capture`` (``VIEW_TOOLS``)."""
    adapter = make_adapter(visible=False)
    assert "get_selection" not in adapter.capabilities().tools


def test_capabilities_offer_get_selection_when_visible(make_adapter: AdapterFactory) -> None:
    adapter = make_adapter(visible=True)
    assert "get_selection" in adapter.capabilities().tools


def test_no_listener_registered_drops_notifications_without_raising(make_adapter: AdapterFactory) -> None:
    """Neither producer method was called: the worker still pushes notifications, and the adapter must
    silently drop them (no listener) rather than raise on the reader thread."""
    adapter = make_adapter()
    adapter.connect(ConnectRequest(operation_id="connect", timeout_s=5.0))
    adapter.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="plate"))
    adapter.extrude(ExtrudeRequest(operation_id="e", sketch=SketchHandle(id="sketch:1"), depth_mm=8.0))
    # a call after the unheard notifications proves the reader thread survived them
    canary = adapter.canary(Empty(operation_id="c"))
    assert canary.healthy is True
