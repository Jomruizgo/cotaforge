"""Drawing sheets through the adapter against the REAL headless worker: built, waited for, captured and exported (PDF,
DXF) without a window, and the window-only display tools refused with a clear reason. ``conftest.py`` tags this
module ``freecad`` and skips it without the FreeCAD root."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    AdapterError,
    AnnotateDrawingRequest,
    Axis,
    BodyHandle,
    BooleanKind,
    BooleanRequest,
    BoxRequest,
    CaptureDrawingRequest,
    CylinderRequest,
    DescribeDrawingRequest,
    DocumentKind,
    DocumentStamp,
    DrawingRequest,
    Empty,
    ErrorCode,
    ExportDrawingRequest,
    NewDocumentRequest,
    RunNativeRequest,
    SaveDocumentRequest,
    SectionRequest,
    StyleRequest,
    ViewRequest,
)

PNG = b"\x89PNG\r\n\x1a\n"


@pytest.fixture
def part(adapter: FreeCADAdapter) -> FreeCADAdapter:
    """A fresh document with a 60 x 40 x 20 plate."""
    adapter.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="plate"))
    adapter.box(BoxRequest(operation_id="b", size=(60.0, 40.0, 20.0)))
    return adapter


def test_the_drawing_tools_are_offered_headless_and_the_window_ones_are_not(adapter: FreeCADAdapter) -> None:
    tools = adapter.capabilities().tools
    assert {"create_drawing", "capture_drawing", "export_drawing"} <= tools
    assert not {"style_bodies", "section_plane", "set_view", "capture"} & tools


def test_create_returns_the_finished_sheet(part: FreeCADAdapter) -> None:
    result = part.create_drawing(
        DrawingRequest(operation_id="dr", template="A3", title="Placa", author="Ana", material="Acero 1045", scale=0.5)
    )
    assert (result.sheet, result.template, result.page_mm, result.scale) == ("Drawing", "A3", (420.0, 297.0), 0.5)
    assert result.views == ("front", "top", "right", "isometric")
    assert result.dimensions == ("width 60 mm", "height 20 mm", "depth 40 mm")
    assert result.waited_s < 90 and result.warnings == ()


def test_the_pdf_dxf_and_image_are_written_without_the_sheet_being_open(part: FreeCADAdapter, tmp_path: Path) -> None:
    part.create_drawing(DrawingRequest(operation_id="dr", title="Placa"))
    pdf, dxf, png = tmp_path / "sheet.pdf", tmp_path / "out" / "sheet.dxf", tmp_path / "sheet.png"
    part.export_drawing(ExportDrawingRequest(operation_id="e", path=str(pdf), format="pdf"))
    part.export_drawing(ExportDrawingRequest(operation_id="e", path=str(dxf), format="dxf"))  # the folder is created
    part.capture_drawing(CaptureDrawingRequest(operation_id="c", path=str(png), width=900))
    data = pdf.read_bytes()
    assert data.startswith(b"%PDF") and len(data) > 20000
    assert dxf.stat().st_size > 2000
    assert png.read_bytes().startswith(PNG)


def test_a_sheet_of_two_bodies_with_a_round_one_still_exports(adapter: FreeCADAdapter, tmp_path: Path) -> None:
    adapter.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="two"))
    adapter.box(BoxRequest(operation_id="b", size=(30.0, 30.0, 10.0)))
    axis = Axis(origin=(50.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0))
    adapter.cylinder(CylinderRequest(operation_id="c", diameter_mm=10.0, height_mm=40.0, axis=axis))
    result = adapter.create_drawing(DrawingRequest(operation_id="dr"))
    assert result.dimensions[1] == "height 40 mm"  # the rod is the tallest body
    target = tmp_path / "two.pdf"
    adapter.export_drawing(ExportDrawingRequest(operation_id="e", path=str(target), format="pdf"))
    assert target.read_bytes().startswith(b"%PDF")


def test_a_sheet_is_built_after_save_undo_and_more_ops_without_reopening(part: FreeCADAdapter, tmp_path: Path) -> None:
    """FreeCAD's global active document is not the session's document handle: TechDraw must not depend on it."""
    stamp = DocumentStamp(
        design_id="d",
        plan_id="p",
        plan_revision=1,
        save_seq=1,
        last_step_index=1,
        harness_version="0",
        plan_sha256="a" * 64,
    )
    part.save_document(SaveDocumentRequest(operation_id="save", path=str(tmp_path / "plate.FCStd"), stamp=stamp))
    part.cylinder(
        CylinderRequest(
            operation_id="c",
            diameter_mm=10.0,
            height_mm=30.0,
            axis=Axis(origin=(80.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0)),
        )
    )
    assert part.undo_last(Empty(operation_id="u")).reverted is not None
    part.box(BoxRequest(operation_id="b2", size=(10.0, 10.0, 10.0)))
    lose_active = "t = App.newDocument('scratch')\nApp.closeDocument('scratch')\nprint(App.ActiveDocument)"
    gone = part.run_native(RunNativeRequest(operation_id="n", code=lose_active, intent="leave no active document"))
    assert gone.stdout.strip() == "None", gone.stdout  # the state the real session reached
    result = part.create_drawing(DrawingRequest(operation_id="dr"))
    assert result.views == ("front", "top", "right", "isometric")
    target = tmp_path / "sheet.pdf"
    part.export_drawing(ExportDrawingRequest(operation_id="e", path=str(target), format="pdf"))
    assert target.read_bytes().startswith(b"%PDF")


def test_exporting_without_a_sheet_says_how_to_make_one(part: FreeCADAdapter, tmp_path: Path) -> None:
    with pytest.raises(AdapterError) as info:
        part.export_drawing(ExportDrawingRequest(operation_id="e", path=str(tmp_path / "x.pdf"), format="pdf"))
    assert info.value.info.code is ErrorCode.invalid_request and "create it first" in info.value.info.message
    assert not (tmp_path / "x.pdf").exists()


def test_a_document_without_bodies_has_nothing_to_draw(adapter: FreeCADAdapter) -> None:
    adapter.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="empty"))
    with pytest.raises(AdapterError) as info:
        adapter.create_drawing(DrawingRequest(operation_id="dr"))
    assert info.value.info.code is ErrorCode.invalid_request and "no body" in info.value.info.message


@pytest.mark.parametrize("name", ["iso", "rear", "left", "bottom", "dimetric", "trimetric"])
def test_every_view_name_is_refused_headless_with_the_reason(part: FreeCADAdapter, name: str) -> None:
    with pytest.raises(AdapterError) as info:
        part.set_view(ViewRequest(operation_id="v", name=name))
    assert info.value.info.code is ErrorCode.not_supported and "headless" in info.value.info.message


def test_style_and_section_are_refused_headless_with_the_reason(part: FreeCADAdapter) -> None:
    with pytest.raises(AdapterError) as info:
        part.style_bodies(StyleRequest(operation_id="s", bodies=(BodyHandle(id="body:1"),), transparency=50))
    assert info.value.info.code is ErrorCode.not_supported and "headless" in info.value.info.message
    with pytest.raises(AdapterError) as info:
        part.section_plane(SectionRequest(operation_id="s", axis="z", offset_mm=5.0))
    assert info.value.info.code is ErrorCode.not_supported and "headless" in info.value.info.message


# --- production drawings: items, several sheets, the files ------------------------------------------------------------


@pytest.fixture
def drilled(adapter: FreeCADAdapter) -> FreeCADAdapter:
    """A 100 x 60 x 20 plate with two D8 through holes (cut by cylinders: no hole operation made them)."""
    adapter.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="placa"))
    plate = adapter.box(BoxRequest(operation_id="b", size=(100.0, 60.0, 20.0)))
    tools = []
    for index, x in enumerate((15.0, 85.0)):
        axis = Axis(origin=(x, 12.0, 0.0), direction=(0.0, 0.0, 1.0))
        tools.append(
            adapter.cylinder(CylinderRequest(operation_id=f"c{index}", diameter_mm=8.0, height_mm=20.0, axis=axis))
        )
    adapter.boolean(
        BooleanRequest(
            operation_id="s",
            kind=BooleanKind.subtract,
            target=plate.created_bodies[0],
            tools=tuple(t.created_bodies[0] for t in tools),
        )
    )
    return adapter


def test_the_annotation_tools_are_offered_headless(adapter: FreeCADAdapter) -> None:
    assert {"annotate_drawing", "describe_drawing"} <= adapter.capabilities().tools


def test_items_are_added_listed_drawn_and_written(drilled: FreeCADAdapter, tmp_path: Path) -> None:
    drilled.create_drawing(DrawingRequest(operation_id="dr", template="A3", title="Placa", drawing_number="PL-1"))
    result = drilled.annotate_drawing(
        AnnotateDrawingRequest(
            operation_id="a",
            action="add",
            items=(
                {"type": "hole_dim", "all": True},
                {"type": "datum_dims"},
                {"type": "dim", "kind": "distance", "from": {"at": [15, 12, 10]}, "to": {"at": [85, 12, 10]}},
                {"type": "note", "text": "Aristas sin rebabas"},
            ),
        )
    )
    mine = [i for i in result.items if i.id != "loc"]  # loc: the default location of the holes
    assert [(i.type, i.status) for i in mine][:2] == [("overall", "ok"), ("hole_dim", "ok")]
    assert "rosca" not in mine[1].detail  # D8 holes of a native cut: no hole operation, but no tap drill either
    assert [i.status for i in mine[2:]] == ["ok", "ok", "ok"]
    assert "70 mm along x" in mine[3].detail
    listed = drilled.describe_drawing(DescribeDrawingRequest(operation_id="l"))
    sheet = listed.sheets[0]
    assert (sheet.sheet, sheet.template, sheet.language) == ("Drawing", "A3", "es")
    assert sheet.fields["drawing_number"] == "PL-1" and sheet.fields["general_tolerances"] == "ISO 2768-mK"
    assert [i.spec["type"] for i in sheet.items if i.id != "loc"] == [
        "overall",
        "hole_dim",
        "datum_dims",
        "dim",
        "note",
    ]
    annotated = tmp_path / "annotated.png"
    drilled.capture_drawing(CaptureDrawingRequest(operation_id="c", path=str(annotated), width=1200))
    assert annotated.read_bytes().startswith(PNG)
    pdf = tmp_path / "placa.pdf"
    dxf = tmp_path / "placa.dxf"
    assert drilled.export_drawing(ExportDrawingRequest(operation_id="e", path=str(pdf), format="pdf")).paths == (
        str(pdf),
    )
    assert drilled.export_drawing(ExportDrawingRequest(operation_id="e", path=str(dxf), format="dxf")).paths == (
        str(dxf),
    )
    assert pdf.read_bytes().startswith(b"%PDF")
    data = dxf.read_bytes()
    assert b"AC1009" in data and b"%%c8 pasante" in data and "Título:".encode("cp1252") in data


@pytest.mark.parametrize("projection", ["techdraw", "hlr"])
def test_a_sheet_in_each_projection_is_built_annotated_described_and_written(
    drilled: FreeCADAdapter, tmp_path: Path, projection: str
) -> None:
    """``techdraw`` is FreeCAD's own default; ``hlr`` projects the standard views itself. Both give the same sheet."""
    ezdxf = pytest.importorskip("ezdxf")
    made = drilled.create_drawing(DrawingRequest(operation_id="dr", template="A3", projection=projection))  # type: ignore[arg-type]
    assert made.projection == projection and made.views == ("front", "top", "right", "isometric")
    assert made.dimensions == ("width 100 mm", "height 20 mm", "depth 60 mm")
    result = drilled.annotate_drawing(
        AnnotateDrawingRequest(
            operation_id="a",
            action="add",
            items=({"type": "hole_dim", "all": True}, {"type": "datum_dims"}, {"type": "note", "text": "Sin rebabas"}),
        )
    )
    assert result.projection == projection and [i.status for i in result.items][2:] == ["ok", "ok"]
    assert drilled.annotate_drawing(AnnotateDrawingRequest(operation_id="u", action="update")).projection == projection
    sheet = drilled.describe_drawing(DescribeDrawingRequest(operation_id="l")).sheets[0]
    assert sheet.projection == projection
    png, pdf, dxf = tmp_path / "s.png", tmp_path / "s.pdf", tmp_path / "s.dxf"
    drilled.capture_drawing(CaptureDrawingRequest(operation_id="c", path=str(png), width=1200))
    drilled.export_drawing(ExportDrawingRequest(operation_id="e", path=str(pdf), format="pdf"))
    drilled.export_drawing(ExportDrawingRequest(operation_id="e", path=str(dxf), format="dxf"))
    assert png.read_bytes().startswith(PNG) and pdf.read_bytes().startswith(b"%PDF")
    doc = ezdxf.readfile(str(dxf))
    audit = doc.audit()
    assert not audit.has_errors and not audit.has_fixes, [e.message for e in audit.errors]
    space = doc.modelspace()
    holes = [c for c in space.query("CIRCLE") if round(c.dxf.radius, 2) == 4.0]  # the D8 holes; the block has others
    assert len(holes) == 2 and any("pasante" in t.dxf.text for t in space.query("TEXT"))


def test_both_projections_give_the_same_hole_positions_and_callouts(drilled: FreeCADAdapter, tmp_path: Path) -> None:
    ezdxf = pytest.importorskip("ezdxf")
    found = {}
    for projection in ("techdraw", "hlr"):
        drilled.create_drawing(DrawingRequest(operation_id="dr", template="A3", projection=projection))  # type: ignore[arg-type]
        drilled.annotate_drawing(
            AnnotateDrawingRequest(operation_id="a", action="add", items=({"type": "hole_dim", "all": True},))
        )
        path = tmp_path / f"{projection}.dxf"
        drilled.export_drawing(ExportDrawingRequest(operation_id="e", path=str(path), format="dxf"))
        space = ezdxf.readfile(str(path)).modelspace()
        found[projection] = (
            sorted(
                (round(c.dxf.center.x, 2), round(c.dxf.center.y, 2), round(c.dxf.radius, 2))
                for c in space.query("CIRCLE")
                if round(c.dxf.radius, 2) == 4.0  # the D8 holes; the title block has circles of its own
            ),
            sorted(t.dxf.text for t in space.query("TEXT")),
        )
    assert found["techdraw"] == found["hlr"] and len(found["hlr"][0]) == 2


def test_switching_the_projection_is_creating_the_sheet_again(drilled: FreeCADAdapter) -> None:
    drilled.create_drawing(DrawingRequest(operation_id="dr", projection="hlr"))
    drilled.annotate_drawing(
        AnnotateDrawingRequest(operation_id="a", action="add", items=({"type": "note", "text": "uno"},))
    )
    again = drilled.create_drawing(DrawingRequest(operation_id="dr"))  # FreeCAD's default
    # the sheet is made again keeping its items: the note is still there, and the views are now TechDraw's
    assert again.projection == "techdraw" and [i.type for i in again.items if i.id != "loc"] == ["overall", "note"]
    assert drilled.describe_drawing(DescribeDrawingRequest(operation_id="l")).sheets[0].projection == "techdraw"


def test_the_dxf_of_a_sheet_is_clean_for_a_real_dxf_reader(drilled: FreeCADAdapter, tmp_path: Path) -> None:
    ezdxf = pytest.importorskip("ezdxf")
    drilled.create_drawing(DrawingRequest(operation_id="dr", template="A3", title="Placa de anclaje — ñandú"))
    drilled.annotate_drawing(
        AnnotateDrawingRequest(
            operation_id="a", action="add", items=({"type": "hole_dim", "all": True}, {"type": "datum_dims"})
        )
    )
    path = tmp_path / "clean.dxf"
    drilled.export_drawing(ExportDrawingRequest(operation_id="e", path=str(path), format="dxf"))
    doc = ezdxf.readfile(str(path))
    audit = doc.audit()
    assert not audit.has_errors and not audit.has_fixes, [e.message for e in audit.errors]
    assert {"CONTORNO", "COTAS", "TEXTO", "ROTULO", "EJES"} <= {layer.dxf.name for layer in doc.layers}
    kinds = {e.dxftype() for e in doc.modelspace()}
    assert {"LINE", "CIRCLE", "TEXT", "SOLID"} <= kinds
    assert any("ñandú" in e.dxf.text for e in doc.modelspace().query("TEXT"))


def test_several_sheets_are_independent_drawings_each_written_to_its_own_file(
    drilled: FreeCADAdapter, tmp_path: Path
) -> None:
    drilled.create_drawing(DrawingRequest(operation_id="dr", template="A3"))
    second = drilled.create_drawing(DrawingRequest(operation_id="dr", template="A4", sheet="Detalle", scale=1.0))
    assert second.sheets == ("Drawing", "Detalle")
    pdf = tmp_path / "all.pdf"
    with pytest.raises(AdapterError, match="ONE sheet to ONE file"):  # no sheet named and two to choose from
        drilled.export_drawing(ExportDrawingRequest(operation_id="e", path=str(pdf), format="pdf"))
    assert not pdf.exists()
    written = drilled.export_drawing(
        ExportDrawingRequest(operation_id="e", path=str(pdf), format="pdf", sheet="Drawing")
    )
    assert written.paths == (str(pdf),) and pdf.read_bytes().startswith(b"%PDF")
    data = pdf.read_bytes()
    assert data.count(b"/Type /Page\n") + data.count(b"/Type /Page ") == 1  # one page
    dxf = tmp_path / "d.dxf"
    only = drilled.export_drawing(ExportDrawingRequest(operation_id="e", path=str(dxf), format="dxf", sheet="Detalle"))
    assert only.paths == (str(dxf),) and dxf.stat().st_size > 2000 and not (tmp_path / "d-2.dxf").exists()
    removed = drilled.annotate_drawing(AnnotateDrawingRequest(operation_id="r", action="remove_sheet", sheet="Detalle"))
    assert removed.removed and removed.sheets == ("Drawing",)
    alone = tmp_path / "alone.pdf"  # with a single sheet no name is needed
    drilled.export_drawing(ExportDrawingRequest(operation_id="e", path=str(alone), format="pdf"))
    assert alone.read_bytes().startswith(b"%PDF")


def test_a_planned_or_unknown_item_never_reaches_the_cad() -> None:
    with pytest.raises(ValidationError, match="type must be one of"):
        AnnotateDrawingRequest(operation_id="a", action="add", items=({"type": "nothing_like_this"},))
    with pytest.raises(ValidationError, match="unknown field"):
        AnnotateDrawingRequest(operation_id="a", action="add", items=({"type": "note", "text": "x", "size": 9},))


def test_an_item_that_cannot_be_drawn_is_refused_and_not_stored(drilled: FreeCADAdapter) -> None:
    drilled.create_drawing(DrawingRequest(operation_id="dr"))
    result = drilled.annotate_drawing(
        AnnotateDrawingRequest(operation_id="a", action="add", items=({"type": "hole_dim", "at": [1, 1, 1]},))
    )
    assert result.items[-1].status == "refused" and "no hole has its axis through" in result.items[-1].detail
    listed = drilled.describe_drawing(DescribeDrawingRequest(operation_id="l")).sheets[0].items
    assert len([i for i in listed if i.id != "loc"]) == 1  # only the overall dimensions


def test_annotating_without_a_sheet_says_how_to_make_one(part: FreeCADAdapter) -> None:
    with pytest.raises(AdapterError) as info:
        part.annotate_drawing(AnnotateDrawingRequest(operation_id="a", action="update"))
    assert info.value.info.code is ErrorCode.invalid_request and "create it first" in info.value.info.message
