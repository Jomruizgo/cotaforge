"""``show_entities`` against the REAL visible worker: the three
scenarios that can only be verified with actual FreeCAD --

1. a highlight is restored BEFORE the document is written to disk, and the SAVED FILE (reopened fresh)
   never carries the temporary colour (``gui._HighlightWatcher.slotStartSaveDocument``);
2. two documents that happen to have an identically-named object never leak or clobber each other's
   highlight, and both remain independently clearable (``highlight.HighlightState``'s
   ``(document_name, object_name)`` keying);
3. a topology change between ``highlight_entities`` and the restore (a fillet added a face) never raises
   and never leaves the object stuck highlighted (``highlight.restore_value``'s
   safe fallback).

Needs a display and ``enable_test_hooks=True`` for ``test_save_document``/
``test_reopen_document``/``test_view_color`` (``worker/queries.py``, gated by ``TEST_HOOKS_ENABLED`` --
never present on a production worker). ``conftest.py`` already tags this module ``freecad`` and skips it
without the FreeCAD root.

NOTE on document identity: a harness document handle (``DocumentInfo.handle``, e.g. ``"doc:1"``) is a
synthetic id used ONLY by the harness's own RPC methods (``set_active_document``, ...); the three
test-only hooks here work at the raw FreeCAD level (``App.getDocument``) and need the REAL FreeCAD
document name instead -- ``DocumentInfo.name``, exactly the ``name=`` this file passed to
``NewDocumentRequest`` (``worker/main.py.new_document`` creates the FreeCAD document with that name
verbatim). Never conflate the two: always pass ``.handle`` to a harness RPC and ``.name`` to a test hook.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    BoxRequest,
    DocumentInfo,
    DocumentKind,
    FaceHandle,
    FaceRequest,
    FilletRequest,
    NewDocumentRequest,
    SetActiveDocumentRequest,
    ShowEntitiesRequest,
)

pytestmark = [
    pytest.mark.freecad,
    pytest.mark.skipif(
        sys.platform != "win32" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
        reason="the visible worker needs a display (DISPLAY / WAYLAND_DISPLAY)",
    ),
]

ORANGE = (1.0, 0.55, 0.0, 0.0)  # must match worker/gui.py's HIGHLIGHT_COLOR


def _is_orange(color: list[float]) -> bool:
    """FreeCAD round-trips colours through 32-bit floats (``0.55`` comes back ``0.550000011920929``), so
    an exact tuple comparison against the Python ``ORANGE`` literal is never reliable -- compare with a
    tolerance instead."""
    return len(color) == len(ORANGE) and all(abs(a - b) < 1e-5 for a, b in zip(color, ORANGE, strict=True))


def _same_colors(left: list[list[float]], right: list[list[float]]) -> bool:
    """FreeCAD 1.0.x saves and reloads colours quantized to 8 bits per channel (``0.9019610285758972``
    comes back ``0.9019607901573181``), so two colour lists match when every channel agrees to within
    half a step of 1/255."""
    return len(left) == len(right) and all(
        len(a) == len(b) and all(abs(x - y) < 0.5 / 255 for x, y in zip(a, b, strict=True))
        for a, b in zip(left, right, strict=True)
    )


@pytest.fixture
def visible(freecad_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[FreeCADAdapter]:
    """Function-scoped (unlike ``test_visible.py``'s module-scoped one): every test here creates its own
    document(s) and needs a clean worker so leftover documents from a previous test can never explain a
    pass or a failure."""
    with FreeCADAdapter(
        freecad_root=freecad_root,
        timeout_s=60.0,
        log_dir=tmp_path_factory.mktemp("show-entities-logs"),
        visible=True,
        enable_test_hooks=True,
    ) as adapter:
        yield adapter


def _rpc(adapter: FreeCADAdapter, method: str, params: dict[str, object], *, timeout_s: float = 15.0) -> dict:
    rpc = adapter._rpc  # noqa: SLF001 - integration test, same seam test_visible.py already uses
    assert rpc is not None
    return rpc.call(method, {"operation_id": method, **params}, timeout_s=timeout_s)


def _view_color(adapter: FreeCADAdapter, document_name: str, obj: str, prop: str = "DiffuseColor") -> list[list[float]]:
    return _rpc(adapter, "test_view_color", {"document": document_name, "object": obj, "property": prop})["value"]


def _new_box(adapter: FreeCADAdapter, name: str) -> tuple[DocumentInfo, tuple[FaceHandle, ...]]:
    """A new document named ``name`` (made active) with one box; returns (document info, created faces)."""
    doc = adapter.new_document(NewDocumentRequest(operation_id=f"new_{name}", kind=DocumentKind.part, name=name))
    box = adapter.box(BoxRequest(operation_id=f"box_{name}", size=(10.0, 20.0, 30.0)))
    return doc, box.created_faces


# --- scenario 1: highlight -> save -> reopen -> original colours persisted ----------------------------------------


def test_highlight_is_cleared_before_save_and_never_persists_in_the_saved_file(
    visible: FreeCADAdapter, tmp_path: Path
) -> None:
    doc, faces = _new_box(visible, "save_test")
    face = faces[0]
    baseline = _view_color(visible, doc.name, "Box")
    assert not any(_is_orange(c) for c in baseline)  # sanity: nothing orange before we ever highlight

    reply = visible.show_entities(ShowEntitiesRequest(operation_id="show", entities=(face,)))
    assert reply.shown == "highlight"
    highlighted = _view_color(visible, doc.name, "Box")
    assert any(_is_orange(c) for c in highlighted)

    path = str(tmp_path / "save_test.FCStd")
    _rpc(visible, "test_save_document", {"path": path})

    # slotStartSaveDocument must have restored the colours on the LIVE document before the write landed.
    after_save = _view_color(visible, doc.name, "Box")
    assert _same_colors(after_save, baseline)

    reopened_name = _rpc(visible, "test_reopen_document", {"name": doc.name, "path": path})["document"]
    persisted = _view_color(visible, reopened_name, "Box")
    assert _same_colors(persisted, baseline)  # the FILE ITSELF never carried the highlight colour


# --- scenario 2: two documents, same object name, never interfere --------------------------------------------------


def test_two_documents_with_the_same_object_name_never_interfere(visible: FreeCADAdapter) -> None:
    """Fresh review CRITICAL #1: FreeCAD auto-names each document's first box ``"Box"`` independently, so
    two fresh documents naturally share the object name -- exactly the collision the ``(document_name,
    object_name)`` keying in ``highlight.HighlightState`` exists to prevent."""
    doc_a, faces_a = _new_box(visible, "docA")
    doc_b, faces_b = _new_box(visible, "docB")
    baseline_a = _view_color(visible, doc_a.name, "Box")
    baseline_b = _view_color(visible, doc_b.name, "Box")

    visible.set_active_document(SetActiveDocumentRequest(operation_id="activate_a", document=doc_a.handle))
    visible.show_entities(ShowEntitiesRequest(operation_id="show_a", entities=(faces_a[0],)))

    assert _view_color(visible, doc_a.name, "Box") != baseline_a
    assert _view_color(visible, doc_b.name, "Box") == baseline_b  # doc B untouched by doc A's highlight

    visible.set_active_document(SetActiveDocumentRequest(operation_id="activate_b", document=doc_b.handle))
    visible.show_entities(ShowEntitiesRequest(operation_id="show_b", entities=(faces_b[0],)))

    # doc A's highlight (made while a DIFFERENT document was active) must still be exactly as it was --
    # untouched by doc B's own show_entities call.
    assert _view_color(visible, doc_a.name, "Box") != baseline_a
    assert _view_color(visible, doc_b.name, "Box") != baseline_b

    # Both remain independently clearable (CRITICAL #1's other half: "doc A's highlight must still be
    # clearable" even after doc B was highlighted and cleared in between).
    visible.show_entities(ShowEntitiesRequest(operation_id="clear_b", clear=True))
    assert _view_color(visible, doc_b.name, "Box") == baseline_b

    visible.set_active_document(SetActiveDocumentRequest(operation_id="activate_a2", document=doc_a.handle))
    visible.show_entities(ShowEntitiesRequest(operation_id="clear_a", clear=True))
    assert _view_color(visible, doc_a.name, "Box") == baseline_a


# --- scenario 3: topology change between highlight and clear --------------------------------------------------------


def test_topology_change_between_highlight_and_clear_is_a_safe_fallback_not_a_crash(visible: FreeCADAdapter) -> None:
    """Fresh review CRITICAL #2: a plan operation between ``highlight_entities`` and the restore can change
    the object's face count (here, a fillet adds one). The old length-``N`` colour list can no longer be
    reapplied verbatim -- ``highlight.restore_value`` must fall back to something FreeCAD accepts (the
    empty-list "use the base colour" convention) instead of raising or leaving the object highlighted."""
    doc, faces = _new_box(visible, "topology_test")
    top = faces[0]
    reply = visible.show_entities(ShowEntitiesRequest(operation_id="show", entities=(top,)))
    assert reply.shown == "highlight"
    before_fillet = _view_color(visible, doc.name, "Box")
    assert len(before_fillet) == 6  # a box has 6 faces before the fillet

    edges = visible.edges_of(FaceRequest(operation_id="edges", face=top)).edges
    visible.fillet(FilletRequest(operation_id="fillet", edges=(edges[0].handle,), radius_mm=1.0))

    # Must not raise (AdapterError or otherwise): this is the whole point of the fallback.
    clear_reply = visible.show_entities(ShowEntitiesRequest(operation_id="clear", clear=True))
    assert clear_reply.shown == "highlight"

    after = _view_color(visible, doc.name, "Box")
    # Confirmed against real FreeCAD: an empty-list assignment reads back as a length-1 list holding the
    # object's base colour (FreeCAD's own "uniform colour" normalisation) -- what must NEVER happen is
    # the old (now wrong-length, partially-orange) 6-entry list surviving unrestored.
    assert after != before_fillet
    assert not any(_is_orange(c) for c in after)
