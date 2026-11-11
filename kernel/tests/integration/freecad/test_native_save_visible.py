"""The harness's native save in the VISIBLE worker (FreeCAD GUI binary) next to the engineer's own Ctrl+S.

What runs for real here: ``_HighlightWatcher.slotStartSaveDocument`` clearing a ``show_entities`` highlight before the
harness's ``saveCopy``; ``Document.save()`` -- what FreeCAD's Ctrl+S calls once a document has a ``FileName`` --
writing the harness's own file because ``save_document`` pointed ``FileName`` at it; ``slotFinishSaveDocument``
recording that save as the engineer's, so the next harness checkpoint keeps it instead of reporting a conflict; and a
write by another program still being refused. What does NOT run: a real key press on the FreeCAD window (the save is
issued through the ``test_save_document`` hook, outside the harness's own operations exactly like a GUI command).
"""

from __future__ import annotations

import hashlib
import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    AdapterError,
    BoxRequest,
    DocumentKind,
    DocumentStamp,
    NewDocumentRequest,
    SaveDocumentRequest,
    ShowEntitiesRequest,
)
from tests.integration.freecad.test_show_entities_visible import _is_orange, _rpc, _same_colors, _view_color

pytestmark = [
    pytest.mark.freecad,
    pytest.mark.skipif(
        sys.platform != "win32" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
        reason="the visible worker needs a display (DISPLAY / WAYLAND_DISPLAY)",
    ),
]


@pytest.fixture
def visible(freecad_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[FreeCADAdapter]:
    with FreeCADAdapter(
        freecad_root=freecad_root,
        timeout_s=60.0,
        log_dir=tmp_path_factory.mktemp("native-save-visible-logs"),
        visible=True,
        enable_test_hooks=True,
    ) as adapter:
        yield adapter


def _stamp(seq: int) -> DocumentStamp:
    return DocumentStamp(
        design_id="d",
        plan_id="ctrl_s",
        plan_revision=1,
        save_seq=seq,
        last_step_index=1,
        harness_version="0.0.0",
        plan_sha256="a" * 64,
    )


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_the_engineers_ctrl_s_writes_the_harness_file_and_is_kept(visible: FreeCADAdapter, tmp_path: Path) -> None:
    doc = visible.new_document(NewDocumentRequest(operation_id="new", kind=DocumentKind.part, name="ctrl_s"))
    box = visible.box(BoxRequest(operation_id="box", size=(10.0, 20.0, 30.0)))
    baseline = _view_color(visible, doc.name, "Box")
    visible.show_entities(ShowEntitiesRequest(operation_id="show", entities=(box.created_faces[0],)))
    assert any(_is_orange(c) for c in _view_color(visible, doc.name, "Box"))

    path = tmp_path / "models" / "ctrl_s.FCStd"
    first = visible.save_document(SaveDocumentRequest(operation_id="s1", path=str(path), stamp=_stamp(1)))
    assert first.sha256 == _sha(path)
    assert _same_colors(_view_color(visible, doc.name, "Box"), baseline)  # the save never carries the highlight

    _rpc(visible, "test_save_document", {"plain_save": True})  # the engineer's Ctrl+S
    assert _sha(path) != first.sha256  # it wrote the harness's file: FileName points there
    second = visible.save_document(
        SaveDocumentRequest(operation_id="s2", path=str(path), stamp=_stamp(2), expected_sha256=first.sha256)
    )
    assert any("engineer" in w for w in second.warnings), second.warnings

    path.write_bytes(b"another program")
    with pytest.raises(AdapterError) as info:
        visible.save_document(
            SaveDocumentRequest(operation_id="s3", path=str(path), stamp=_stamp(3), expected_sha256=second.sha256)
        )
    assert info.value.info.details["reason"] == "conflict" and path.read_bytes() == b"another program"

    path.unlink()
    third = visible.save_document(SaveDocumentRequest(operation_id="s4", path=str(path), stamp=_stamp(4)))
    reopened = _rpc(visible, "test_reopen_document", {"name": doc.name, "path": str(path)})["document"]
    assert _same_colors(_view_color(visible, reopened, "Box"), baseline)
    assert third.size_bytes == path.stat().st_size
