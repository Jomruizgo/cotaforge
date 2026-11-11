"""The display tools (style, section, fit, every standard view) and the drawing sheet against the REAL visible worker
(``FreeCADSession(visible=True)``: the FreeCAD GUI binary).

These need a display (on Windows the GUI binary opens its own window); they are skipped without one, carry the
``visible`` marker (run those serially, never with xdist) and are meant to be run on Windows. The mechanisms they
check could not be verified without a window: ``ViewObject`` properties of bodies, the ``SoClipPlane`` of a section,
TechDraw computing views in a background thread (the wait must hold until they are done, and rushing the next steps
must not crash FreeCAD), and the 3D view still being reachable (capture, set_view) while a TechDraw sheet window is
in front.

Needs ``enable_test_hooks=True`` for ``test_view_state`` (``worker/queries.py``). ``conftest.py`` already tags this
module ``freecad`` and skips it without the FreeCAD root.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    AnnotateDrawingRequest,
    Axis,
    BodyHandle,
    BooleanKind,
    BooleanRequest,
    BoxRequest,
    CanaryResult,
    CaptureDrawingRequest,
    CaptureRequest,
    CylinderRequest,
    DescribeDrawingRequest,
    DocumentHandle,
    DocumentInfo,
    DocumentKind,
    DrawingRequest,
    Empty,
    ExportDrawingRequest,
    InsertComponentRequest,
    MassRequest,
    NewDocumentRequest,
    SaveDocumentRequest,
    SectionRequest,
    StyleRequest,
    Transform,
    ViewRequest,
)
from tests.integration.freecad.worker_tools import stamp_for

pytestmark = [
    pytest.mark.freecad,
    pytest.mark.visible,
    pytest.mark.skipif(
        sys.platform != "win32" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
        reason="the visible worker needs a display (DISPLAY / WAYLAND_DISPLAY)",
    ),
]

PNG = b"\x89PNG\r\n\x1a\n"
DRAWING_BUDGET_S = 90.0  # the worker's own wait (worker/drawing.py DRAWING_WAIT_S)


@pytest.fixture
def visible(freecad_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[FreeCADAdapter]:
    with FreeCADAdapter(
        freecad_root=freecad_root,
        timeout_s=60.0,
        log_dir=tmp_path_factory.mktemp("view-drawing-logs"),
        visible=True,
        enable_test_hooks=True,
    ) as adapter:
        yield adapter


def _state(adapter: FreeCADAdapter, doc: DocumentInfo, what: str, **extra: Any) -> Any:
    rpc = adapter._rpc  # noqa: SLF001 - integration test, same seam test_visible.py uses
    assert rpc is not None
    params = {"operation_id": "state", "what": what, "document": doc.name, **extra}
    return rpc.call("test_view_state", params, timeout_s=30.0)["value"]


def _property(adapter: FreeCADAdapter, doc: DocumentInfo, obj: str, prop: str) -> Any:
    return _state(adapter, doc, "property", object=obj, property=prop)


def _two_boxes(adapter: FreeCADAdapter, name: str) -> tuple[DocumentInfo, BodyHandle, BodyHandle]:
    """A document with two SEPARATE bodies, ``Box`` (10 x 20 x 30) and ``Box001`` (20 x 10 x 5), both at the origin:
    they overlap in the 10 x 10 x 5 corner (an L seen from above). Two bodies on purpose: style, fit and hide act on one
    of them and must leave the other alone; the overlap does not matter for that."""
    doc = adapter.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name=name))
    first = adapter.box(BoxRequest(operation_id="b1", size=(10.0, 20.0, 30.0)))
    second = adapter.box(BoxRequest(operation_id="b2", size=(20.0, 10.0, 5.0)))
    return doc, first.created_bodies[0], second.created_bodies[0]


def _png(adapter: FreeCADAdapter, tmp_path: Path, name: str = "shot.png") -> bytes:
    target = tmp_path / name
    adapter.capture(CaptureRequest(operation_id="cap", path=str(target), width=640, height=400))
    data = target.read_bytes()
    assert data.startswith(PNG) and len(data) > 1000
    return data


# --- style: transparency, colour, hide / show per body ----------------------------------------------------------------


def test_style_sets_transparency_colour_and_visibility_of_the_named_body_only(visible: FreeCADAdapter) -> None:
    doc, first, _second = _two_boxes(visible, "style")
    assert "style_bodies" in visible.capabilities().tools

    visible.style_bodies(StyleRequest(operation_id="s", bodies=(first,), transparency=60, color="#ff0000"))
    assert _property(visible, doc, "Box", "Transparency") == 60
    red = _property(visible, doc, "Box", "ShapeColor")
    assert red[0] == pytest.approx(1.0, abs=0.01) and red[1] == pytest.approx(0.0, abs=0.01)
    assert _property(visible, doc, "Box001", "Transparency") == 0  # the other body is untouched

    visible.style_bodies(StyleRequest(operation_id="s", bodies=(first,), visible=False))
    assert _property(visible, doc, "Box", "Visibility") is False
    assert _property(visible, doc, "Box001", "Visibility") is True
    visible.style_bodies(StyleRequest(operation_id="s", bodies=(first,), visible=True))
    assert _property(visible, doc, "Box", "Visibility") is True


def test_a_styled_view_still_captures_and_the_model_is_untouched(visible: FreeCADAdapter, tmp_path: Path) -> None:
    doc, first, second = _two_boxes(visible, "styled_shot")
    before = visible.mass_properties(MassRequest(operation_id="m")).volume_mm3
    visible.style_bodies(StyleRequest(operation_id="s", bodies=(first, second), transparency=70, color="blue"))
    _png(visible, tmp_path)
    assert visible.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(before)
    assert _property(visible, doc, "Box", "Transparency") == 70


# --- section plane ----------------------------------------------------------------------------------------------------


def test_a_section_plane_is_installed_moved_and_removed(visible: FreeCADAdapter, tmp_path: Path) -> None:
    doc, _first, _second = _two_boxes(visible, "section")
    assert _state(visible, doc, "clip_planes") == []
    visible.section_plane(SectionRequest(operation_id="s", axis="z", offset_mm=5.0))
    (plane,) = _state(visible, doc, "clip_planes")
    assert plane[:3] == pytest.approx([0.0, 0.0, -1.0], abs=1e-6) and plane[3] == pytest.approx(-5.0)
    _png(visible, tmp_path, "cut.png")  # the view renders with the plane in the scene
    visible.section_plane(SectionRequest(operation_id="s", axis="x", offset_mm=2.0))
    assert len(_state(visible, doc, "clip_planes")) == 1  # one plane at a time: the old one is replaced
    visible.section_plane(SectionRequest(operation_id="s"))
    assert _state(visible, doc, "clip_planes") == []


# --- orientation and fit ----------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", ["iso", "front", "top", "right", "rear", "left", "bottom", "dimetric", "trimetric"])
def test_every_standard_view_orients_and_captures(visible: FreeCADAdapter, tmp_path: Path, name: str) -> None:
    _two_boxes(visible, f"view_{name}")
    visible.set_view(ViewRequest(operation_id="v", name=name))
    _png(visible, tmp_path, f"{name}.png")


def test_fit_frames_the_named_bodies_and_puts_the_others_back(visible: FreeCADAdapter, tmp_path: Path) -> None:
    doc, first, second = _two_boxes(visible, "fit")
    visible.set_view(ViewRequest(operation_id="v", bodies=(second,)))  # no orientation: only the fit
    assert _property(visible, doc, "Box", "Visibility") is True  # hidden for the fit, shown again after it
    assert _property(visible, doc, "Box001", "Visibility") is True
    _png(visible, tmp_path, "fit_one.png")
    visible.set_view(ViewRequest(operation_id="v", name="front", bodies=(first,)))
    visible.set_view(ViewRequest(operation_id="v"))  # everything
    _png(visible, tmp_path, "fit_all.png")


# --- drawing ----------------------------------------------------------------------------------------------------------


def test_the_drawing_is_built_exported_and_captured_from_the_visible_worker(
    visible: FreeCADAdapter, tmp_path: Path
) -> None:
    _two_boxes(visible, "sheet")
    result = visible.create_drawing(DrawingRequest(operation_id="dr", template="A4", title="Dos cajas"))
    assert result.views == ("front", "top", "right", "isometric") and len(result.dimensions) == 3
    assert result.waited_s < DRAWING_BUDGET_S
    pdf, dxf, png = tmp_path / "sheet.pdf", tmp_path / "sheet.dxf", tmp_path / "sheet.png"
    visible.export_drawing(ExportDrawingRequest(operation_id="e", path=str(pdf), format="pdf"))
    visible.export_drawing(ExportDrawingRequest(operation_id="e", path=str(dxf), format="dxf"))
    visible.capture_drawing(CaptureDrawingRequest(operation_id="c", path=str(png), width=1200))
    data = pdf.read_bytes()
    assert data.startswith(b"%PDF") and len(data) > 20000  # not the blank page an unopened sheet used to give
    assert dxf.stat().st_size > 2000 and png.read_bytes().startswith(PNG)


def test_rushing_the_drawing_steps_never_crashes_the_cad(visible: FreeCADAdapter, tmp_path: Path) -> None:
    """The real-use crash: steps fired while TechDraw was still computing a view. Every call here returns only when its
    views are done, so building the sheet again and again and exporting at once must leave a healthy worker."""
    _two_boxes(visible, "rush")
    for index in range(3):
        visible.create_drawing(DrawingRequest(operation_id=f"dr{index}", template="A4" if index % 2 == 0 else "A3"))
    visible.export_drawing(ExportDrawingRequest(operation_id="e", path=str(tmp_path / "rush.pdf"), format="pdf"))
    visible.capture_drawing(CaptureDrawingRequest(operation_id="c", path=str(tmp_path / "rush.png")))
    assert visible.session_alive()
    canary = visible.canary(Empty(operation_id="canary"))
    assert isinstance(canary, CanaryResult) and canary.healthy


def test_the_3d_view_is_still_captured_and_oriented_with_the_sheet_window_in_front(
    visible: FreeCADAdapter, tmp_path: Path
) -> None:
    """Capture used to fail with a TechDraw sheet active: the sheet's window has no ``saveImage`` and no
    ``viewIsometric``."""
    doc, first, _second = _two_boxes(visible, "sheet_front")
    visible.create_drawing(DrawingRequest(operation_id="dr"))
    in_front = _state(visible, doc, "open_sheet")
    assert "View3DInventor" not in in_front, in_front  # the sheet's own window is the active one now
    visible.set_view(ViewRequest(operation_id="v", name="iso"))
    _png(visible, tmp_path, "while_sheet_open.png")
    visible.style_bodies(StyleRequest(operation_id="s", bodies=(first,), transparency=40))
    visible.export_drawing(ExportDrawingRequest(operation_id="e", path=str(tmp_path / "open.pdf"), format="pdf"))
    assert (tmp_path / "open.pdf").read_bytes().startswith(b"%PDF")


def test_an_annotated_drawing_is_built_changed_and_written_from_the_visible_worker(
    visible: FreeCADAdapter, tmp_path: Path
) -> None:
    """The sheet items, several sheets and the update (the sheet is rebuilt while its window may be open) with the GUI
    worker: nothing may crash it and every file is written from the same sheet model as without a window."""
    doc = visible.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="placa"))
    plate = visible.box(BoxRequest(operation_id="b", size=(100.0, 60.0, 20.0)))
    tools = []
    for index, x in enumerate((15.0, 85.0)):
        axis = Axis(origin=(x, 12.0, 0.0), direction=(0.0, 0.0, 1.0))
        tools.append(
            visible.cylinder(CylinderRequest(operation_id=f"c{index}", diameter_mm=8.0, height_mm=20.0, axis=axis))
        )
    visible.boolean(
        BooleanRequest(
            operation_id="s",
            kind=BooleanKind.subtract,
            target=plate.created_bodies[0],
            tools=tuple(t.created_bodies[0] for t in tools),
        )
    )
    created = visible.create_drawing(DrawingRequest(operation_id="dr", template="A3", title="Placa"))
    assert created.waited_s < DRAWING_BUDGET_S
    added = visible.annotate_drawing(
        AnnotateDrawingRequest(
            operation_id="a",
            action="add",
            items=(
                {"type": "hole_dim", "all": True},
                {"type": "datum_dims"},
                {"type": "dim", "kind": "distance", "from": {"at": [15, 12, 10]}, "to": {"at": [85, 12, 10]}},
                {"type": "note", "text": "Sin rebabas"},
            ),
        )
    )
    mine = [i for i in added.items if i.id != "loc"]  # loc: the default location of the holes
    assert [i.status for i in mine][2:] == ["ok", "ok", "ok"] and mine[1].type == "hole_dim"
    visible.create_drawing(DrawingRequest(operation_id="dr2", template="A4", sheet="Detalle", scale=1.0))
    _state(visible, doc, "open_sheet")  # the engineer opens the sheet window: the update below rebuilds it under him
    again = visible.annotate_drawing(AnnotateDrawingRequest(operation_id="u", action="update"))
    assert [i.type for i in again.items if i.id != "loc"] == ["overall", "hole_dim", "datum_dims", "dim", "note"]
    pdf, dxf, png = tmp_path / "annotated.pdf", tmp_path / "annotated.dxf", tmp_path / "annotated.png"
    visible.export_drawing(ExportDrawingRequest(operation_id="e", path=str(pdf), format="pdf", sheet="Drawing"))
    one = visible.export_drawing(ExportDrawingRequest(operation_id="e", path=str(dxf), format="dxf", sheet="Drawing"))
    visible.capture_drawing(CaptureDrawingRequest(operation_id="c", path=str(png), width=1200))
    assert pdf.read_bytes().startswith(b"%PDF") and one.paths == (str(dxf),) and png.read_bytes().startswith(PNG)
    assert b"%%c8 pasante" in dxf.read_bytes()
    canary = visible.canary(Empty(operation_id="canary"))
    assert isinstance(canary, CanaryResult) and canary.healthy


def test_special_views_and_the_review_from_the_visible_worker(visible: FreeCADAdapter, tmp_path: Path) -> None:
    """Section, detail, hidden / centre lines and the review with the GUI worker: they are computed by function calls
    (no TechDraw view object to wait for), so the sheet window being open must not matter and nothing may crash."""
    doc = visible.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="soporte"))
    plate = visible.box(BoxRequest(operation_id="b", size=(80.0, 40.0, 20.0)))
    tools = []
    for index, x in enumerate((20.0, 60.0)):
        axis = Axis(origin=(x, 20.0, 0.0), direction=(0.0, 0.0, 1.0))
        tools.append(
            visible.cylinder(CylinderRequest(operation_id=f"c{index}", diameter_mm=8.0, height_mm=20.0, axis=axis))
        )
    visible.boolean(
        BooleanRequest(
            operation_id="s",
            kind=BooleanKind.subtract,
            target=plate.created_bodies[0],
            tools=tuple(t.created_bodies[0] for t in tools),
        )
    )
    visible.create_drawing(DrawingRequest(operation_id="dr", template="A3", title="Soporte", scale=1.0))
    _state(visible, doc, "open_sheet")  # the sheet window is open while the derived views are computed
    added = visible.annotate_drawing(
        AnnotateDrawingRequest(
            operation_id="a",
            action="add",
            items=(
                {"type": "view_lines"},
                {"type": "section", "holes": "all", "view": "top"},
                {"type": "detail", "view": "front", "at": [80, 20, 0], "radius_mm": 5},
            ),
        )
    )
    # the detail circle of this plate sits on the edge the height dimension stands at: the sheet says so (se solapa) and
    # nothing else is wrong with the items
    rows = added.items[1:]
    assert [i.status for i in rows][:2] == ["ok", "ok"] and rows[2].status in ("ok", "warning"), added.items
    assert rows[2].status == "ok" or "se solapa" in rows[2].detail, rows[2]
    review = visible.describe_drawing(DescribeDrawingRequest(operation_id="r", review=True))
    assert {f.code for f in review.sheets[0].findings} >= {"hole_no_callout"}
    png = tmp_path / "views.png"
    visible.capture_drawing(CaptureDrawingRequest(operation_id="c", path=str(png), width=1200))
    assert png.read_bytes().startswith(PNG)


def test_an_assembly_drawing_with_parts_list_balloons_and_an_exploded_view_from_the_visible_worker(
    visible: FreeCADAdapter, tmp_path: Path
) -> None:
    """The assembly sheet with the GUI worker: the views are computed in the background, the list, the balloons (rays
    against the components) and the exploded view (a projection of displaced components) are plain calls, and the
    sheet window being open must not matter."""
    ids: dict[str, str] = {}
    for name, size in (("base", (100.0, 60.0, 8.0)), ("block", (20.0, 14.0, 30.0)), ("pin", (4.0, 4.0, 4.0))):
        doc = visible.new_document(NewDocumentRequest(operation_id=f"n_{name}", kind=DocumentKind.part, name=name))
        visible.box(BoxRequest(operation_id=f"b_{name}", size=size))
        visible.save_document(
            SaveDocumentRequest(
                operation_id=f"s_{name}", path=str(tmp_path / f"{name}.FCStd"), stamp=stamp_for("p", 1, save_seq=1)
            )
        )
        ids[name] = doc.handle.id
    asm = visible.new_document(NewDocumentRequest(operation_id="n_asm", kind=DocumentKind.assembly, name="conjunto"))
    visible.save_document(
        SaveDocumentRequest(
            operation_id="s_asm", path=str(tmp_path / "conjunto.FCStd"), stamp=stamp_for("p", 1, save_seq=1)
        )
    )
    for name, origin in (("base", (0, 0, 0)), ("block", (10, 10, 8)), ("block", (70, 10, 8)), ("pin", (50, 30, 2))):
        visible.insert_component(
            InsertComponentRequest(
                operation_id=f"i_{name}", part=DocumentHandle(id=ids[name]), transform=Transform(origin=origin)
            )
        )
    created = visible.create_drawing(
        DrawingRequest(operation_id="dr", template="A3", title="Conjunto", isometric=False)
    )
    assert created.waited_s < 300.0
    _state(visible, asm, "open_sheet")  # the sheet window is open while the items are computed
    added = visible.annotate_drawing(
        AnnotateDrawingRequest(
            operation_id="a",
            action="add",
            items=({"type": "parts_list"}, {"type": "exploded", "factor": 0.5}, {"type": "balloons"}),
        )
    )
    by_type = {i.type: i for i in added.items}
    assert by_type["parts_list"].status == "ok" and by_type["exploded"].status in ("ok", "warning"), added.items
    review = visible.describe_drawing(DescribeDrawingRequest(operation_id="r", review=True))
    codes = {f.code for f in review.sheets[0].findings}
    assert "instance_hidden" in codes and "position_without_balloon" in codes, codes  # the pin sits inside the base
    pdf, dxf, png = tmp_path / "asm.pdf", tmp_path / "asm.dxf", tmp_path / "asm.png"
    visible.export_drawing(ExportDrawingRequest(operation_id="e", path=str(pdf), format="pdf"))
    visible.export_drawing(ExportDrawingRequest(operation_id="e", path=str(dxf), format="dxf"))
    visible.capture_drawing(CaptureDrawingRequest(operation_id="c", path=str(png), width=1200))
    assert pdf.read_bytes().startswith(b"%PDF") and png.read_bytes().startswith(PNG)
    assert b"Plano de conjunto" in dxf.read_bytes()
    canary = visible.canary(Empty(operation_id="canary"))
    assert isinstance(canary, CanaryResult) and canary.healthy
