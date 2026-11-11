"""After deleting or editing a feature the window shows the body's Tip and no sketch, read from the GUI's own
``ViewObject`` (not only from the ``Visibility`` property of the document object), on the REAL visible worker.

Needs a display (Windows opens its own window); carries the ``visible`` marker. ``COTAFORGE_TEST_SHOTS`` names a folder
that receives a screenshot of the window after each step.
"""

import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    CaptureRequest,
    DocumentKind,
    Empty,
    NewDocumentRequest,
    OpenDocumentRequest,
    RemoveFeatureRequest,
    RunNativeRequest,
    SetFeaturePropertiesRequest,
)
from tests.integration.freecad.test_feature_edit import PLATE
from tests.integration.freecad.worker_tools import call_hook

pytestmark = [
    pytest.mark.freecad,
    pytest.mark.visible,
    pytest.mark.skipif(
        sys.platform != "win32" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
        reason="the visible worker needs a display (DISPLAY / WAYLAND_DISPLAY)",
    ),
]


@pytest.fixture
def window(freecad_root: Path, tmp_path: Path) -> Iterator[FreeCADAdapter]:
    with FreeCADAdapter(
        freecad_root=freecad_root, timeout_s=120.0, log_dir=tmp_path / "logs", visible=True, enable_test_hooks=True
    ) as adapter:
        adapter.new_document(NewDocumentRequest(operation_id="n", kind=DocumentKind.part, name="made_elsewhere"))
        assert adapter.run_native(RunNativeRequest(operation_id="r", code=PLATE, intent="a plate")).ok
        path = tmp_path / "plate.FCStd"
        call_hook(adapter, "test_save_document", {"path": str(path)})
        adapter.close_document(Empty(operation_id="close"))
        adapter.open_document(OpenDocumentRequest(operation_id="open", path=str(path)))
        yield adapter


def gui_visible(adapter: FreeCADAdapter, name: str) -> bool:
    return bool(
        call_hook(adapter, "test_view_state", {"what": "property", "object": name, "property": "Visibility"})["value"]
    )


def shot(adapter: FreeCADAdapter, tmp_path: Path, name: str) -> None:
    folder = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{name}.png"
    adapter.capture(CaptureRequest(operation_id="cap", path=str(target), width=800, height=500))
    assert target.stat().st_size > 1000


def assert_tip_only(adapter: FreeCADAdapter, tip: str, hidden: tuple[str, ...]) -> None:
    states: dict[str, Any] = {n: gui_visible(adapter, n) for n in (tip, *hidden)}
    assert states[tip], states
    assert not any(states[n] for n in hidden), states


def test_the_window_shows_the_tip_and_no_sketch_after_a_delete_and_after_an_edit(
    window: FreeCADAdapter, tmp_path: Path
) -> None:
    shot(window, tmp_path, "0_opened")

    edited = window.set_feature_properties(
        SetFeaturePropertiesRequest(operation_id="edit", feature="Fillet", properties={"Radius": 4})
    )
    assert edited.ok
    assert_tip_only(window, "Chamfer", ("Sketch", "Sketch001", "Pad", "Pocket", "Fillet"))
    shot(window, tmp_path, "1_fillet_edited")

    deleted = window.remove_feature(RemoveFeatureRequest(operation_id="del", feature="Chamfer"))
    assert deleted.ok
    assert_tip_only(window, "Fillet", ("Sketch", "Sketch001", "Pad", "Pocket"))
    shot(window, tmp_path, "2_chamfer_deleted")

    window.undo_last(Empty(operation_id="undo"))
    assert_tip_only(window, "Chamfer", ("Sketch", "Sketch001", "Pocket", "Fillet"))
