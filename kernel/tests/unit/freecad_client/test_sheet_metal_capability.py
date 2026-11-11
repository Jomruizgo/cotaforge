"""The FreeCAD adapter offers the sheet-metal ops only when its worker said the SheetMetal add-on is there, and says how
to install it when it is not (against ``fake_worker.py``; no FreeCAD needed)."""

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter, rpc
from cotaforge.contract import ConnectRequest, Empty
from tests.platform_support import PLAIN_PYTHON, stub_freecad_root

FAKE_WORKER = Path(__file__).with_name("fake_worker.py")
SHEET_TOOLS = {"base_flange", "edge_flange", "unfold"}
AdapterFactory = Callable[[str], FreeCADAdapter]


@pytest.fixture
def make_adapter(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[AdapterFactory]:
    created: list[FreeCADAdapter] = []

    def factory(mode: str) -> FreeCADAdapter:
        command = [PLAIN_PYTHON, str(FAKE_WORKER), mode]
        monkeypatch.setattr(rpc, "worker_command", lambda root, worker_dir, **kw: command)
        adapter = FreeCADAdapter(stub_freecad_root(tmp_path / "root"), log_dir=tmp_path / "logs")
        created.append(adapter)
        return adapter

    yield factory
    for adapter in created:
        adapter.disconnect(Empty(operation_id="cleanup"))


def connect(adapter: FreeCADAdapter) -> None:
    adapter.connect(ConnectRequest(operation_id="connect", timeout_s=10.0))


def test_before_any_worker_connected_the_ops_are_offered_and_no_absence_is_claimed(
    make_adapter: AdapterFactory,
) -> None:
    """The add-on can only be found by a worker: until one has said, the static answer keeps the ops (so a server can
    publish its tools without starting FreeCAD) and claims nothing about why they would be missing."""
    adapter = make_adapter("ok")
    assert SHEET_TOOLS <= adapter.capabilities().tools
    assert adapter.unavailable_tools() == {} and adapter.sheet_metal is None


def test_a_worker_that_found_the_addon_gets_the_three_ops(make_adapter: AdapterFactory) -> None:
    adapter = make_adapter("sheet_metal")
    connect(adapter)
    assert SHEET_TOOLS <= adapter.capabilities().tools
    assert adapter.unavailable_tools() == {}
    assert adapter.sheet_metal is not None and adapter.sheet_metal["version"] == "0.8.26"


def test_a_worker_without_the_addon_hides_the_ops_and_says_how_to_install_it(make_adapter: AdapterFactory) -> None:
    adapter = make_adapter("no_sheet_metal")
    connect(adapter)
    assert not SHEET_TOOLS & adapter.capabilities().tools
    reasons = adapter.unavailable_tools()
    assert set(reasons) == SHEET_TOOLS
    text = reasons["edge_flange"]
    assert "not installed" in text and "Addon manager" in text and "restart" in text


def test_an_addon_that_is_installed_but_broken_says_so(make_adapter: AdapterFactory) -> None:
    adapter = make_adapter("broken_sheet_metal")
    connect(adapter)
    assert not SHEET_TOOLS & adapter.capabilities().tools
    assert "cannot be loaded" in adapter.unavailable_tools()["unfold"]


def test_an_old_worker_that_never_mentions_the_addon_offers_nothing(make_adapter: AdapterFactory) -> None:
    adapter = make_adapter("ok")
    connect(adapter)
    assert not SHEET_TOOLS & adapter.capabilities().tools and adapter.unavailable_tools() == {}


def test_a_reconnect_forgets_what_the_previous_worker_said(make_adapter: AdapterFactory) -> None:
    adapter = make_adapter("sheet_metal")
    connect(adapter)
    adapter.disconnect(Empty(operation_id="d"))
    assert adapter.sheet_metal is None and adapter.unavailable_tools() == {}
