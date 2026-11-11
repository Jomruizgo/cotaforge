"""Drawing sheets (TechDraw) in the headless worker: the sheet is built from the model, the views are waited for, the
PDF / DXF / PNG are written without any window, a rebuilt sheet replaces the old one and removing a sheet leaves the
model alone. The wait itself is checked against stand-in views."""

import os
import tempfile
import xml.etree.ElementTree as ET
from typing import Any

import drawing
import main
import sheet_model as model
import sheet_svg
from harness import build_part03, call, expect_error, new_document, op, volume
from protocol import WorkerError

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _fc_doc() -> Any:
    return main.WORKER.active.fc_doc


def _techdraw_objects() -> list[Any]:
    return [o for o in _fc_doc().Objects if str(o.TypeId).startswith("TechDraw::")]


def _tmp(name: str) -> str:
    return os.path.join(tempfile.mkdtemp(prefix="cadh_drawing_"), name)


def check_create_builds_the_sheet_and_waits_for_every_view() -> None:
    build_part03()  # the L-bracket: 60 x 40 x 20 mm
    result = call(
        "create_drawing",
        op("d", template="A4", title="Soporte L", author="Ana", date="2026-10-24", material="Acero 1045", scale=None),
    )
    assert result["sheet"] == "Drawing" and result["template"] == "A4" and result["page_mm"] == [297.0, 210.0]
    assert result["views"] == ["front", "top", "right", "isometric"]
    assert result["dimensions"] == ["width 60 mm", "height 20 mm", "depth 40 mm"], result["dimensions"]
    assert result["scale"] == 1.0 and result["warnings"] == []
    doc = _fc_doc()
    page = doc.getObject("Drawing")
    views = drawing.part_views(page)
    assert len(views) == 4
    for view in views:  # nothing is returned before every view has its geometry
        assert len(view.getVisibleEdges()) > 0, view.Label
        assert "Touched" not in list(view.State) and "Invalid" not in list(view.State), (view.Label, list(view.State))
    extents = [o for o in _techdraw_objects() if "DimExtent" in str(o.TypeId)]
    assert len(extents) == 3
    fields = dict(page.Template.EditableTexts)
    assert fields["title"] == "Soporte L" and fields["creator"] == "Ana" and fields["date_of_issue"] == "2026-10-24"
    assert fields["part_material"] == "Acero 1045" and fields["scale"] == "1 : 1"
    assert fields["approval_person"] == "" and fields["drawing_number"] == ""  # the template's sample text is gone


def check_the_title_defaults_to_the_document_label_and_the_date_to_today() -> None:
    build_part03()
    call("create_drawing", op("d", template="A4"))
    fields = dict(_fc_doc().getObject("Drawing").Template.EditableTexts)
    assert fields["title"] == "bracket"
    assert len(fields["date_of_issue"]) == 10 and fields["date_of_issue"][4] == "-"


def check_a_rebuilt_sheet_replaces_the_old_one() -> None:
    build_part03()
    call("create_drawing", op("d", template="A4", title="first"))
    second = call("create_drawing", op("d", template="A3", title="second", scale=0.5))
    assert second["scale"] == 0.5 and second["page_mm"] == [420.0, 297.0]
    pages = [o for o in _techdraw_objects() if str(o.TypeId) == "TechDraw::DrawPage"]
    assert len(pages) == 1
    assert dict(pages[0].Template.EditableTexts)["title"] == "second"
    assert dict(pages[0].Template.EditableTexts)["scale"] == "1 : 2"
    assert len([o for o in _techdraw_objects() if "DimExtent" in str(o.TypeId)]) == 3  # not 6


def check_a_scale_that_does_not_fit_is_reported_not_hidden() -> None:
    build_part03()
    result = call("create_drawing", op("d", template="A4", scale=10.0))
    assert result["scale"] == 10.0
    assert any("do not fit" in w for w in result["warnings"]), result["warnings"]


def check_removing_the_sheet_never_touches_the_model() -> None:
    build_part03()
    before = volume()
    objects_before = sorted(o.Name for o in _fc_doc().Objects if not str(o.TypeId).startswith("TechDraw::"))
    call("create_drawing", op("d", template="A4"))
    assert _techdraw_objects()
    drawing.remove_sheet(_fc_doc())  # the order that does not crash FreeCAD
    assert _techdraw_objects() == []
    assert sorted(o.Name for o in _fc_doc().Objects) == objects_before
    assert abs(volume() - before) < 1e-6
    call("create_drawing", op("d", template="A4"))  # and a sheet can be made again afterwards
    assert len(drawing.part_views(_fc_doc().getObject("Drawing"))) == 4


def check_a_document_without_bodies_has_nothing_to_draw() -> None:
    new_document("empty")
    exc = expect_error("invalid_request", "create_drawing", op("d", template="A4"))
    assert "no body" in exc.message


def check_exporting_or_capturing_without_a_sheet_says_how_to_make_one() -> None:
    build_part03()
    exc = expect_error("invalid_request", "export_drawing", op("e", path=_tmp("a.pdf"), format="pdf"))
    assert "create it first" in exc.message
    expect_error("invalid_request", "capture_drawing", op("c", path=_tmp("a.png"), width=800))


def check_the_pdf_dxf_and_png_are_written_without_any_window() -> None:
    build_part03()
    call("create_drawing", op("d", template="A4", title="Soporte L"))
    assert main.WORKER.hello()["gui"] is False  # no sheet window exists: this is the route that does not need one
    pdf, dxf, png = _tmp("sheet.pdf"), _tmp("sheet.dxf"), _tmp("sheet.png")
    call("export_drawing", op("e", path=pdf, format="pdf"))
    call("export_drawing", op("e", path=dxf, format="dxf"))
    call("capture_drawing", op("c", path=png, width=1200))
    with open(pdf, "rb") as handle:
        data = handle.read()
    assert data.startswith(b"%PDF") and data.rstrip().endswith(b"%%EOF") and len(data) > 20000, len(data)
    assert os.path.getsize(dxf) > 2000
    with open(png, "rb") as handle:
        assert handle.read(8) == PNG_SIGNATURE
    # not blank: the sheet has ink (frame, views, title block) on white paper
    from PySide import QtGui  # noqa: PLC0415

    image = QtGui.QImage(png)
    assert image.width() == 1200 and image.height() == round(1200 * 210 / 297)
    dark = sum(
        1
        for x in range(0, image.width(), 3)
        for y in range(0, image.height(), 3)
        if QtGui.qGray(image.pixel(x, y)) < 100
    )
    assert dark > 1500, dark


def check_the_sheet_svg_carries_the_views_the_dimensions_and_the_title_block() -> None:
    build_part03()
    call("create_drawing", op("d", template="A3", title="Soporte L", scale=0.5))
    rec = main.WORKER.active
    page = _fc_doc().getObject("Drawing")
    sheet, results = drawing.compose_sheet(rec, page, drawing.spec_of(page), 1, 1, "t")
    assert (sheet.width, sheet.height, sheet.title) == (420.0, 297.0, "Soporte L")
    assert [(r.type, r.status) for r in results] == [("overall", "ok")]
    text = sheet_svg.to_svg(sheet)
    assert "Soporte L" in text and "1 : 2" in text
    for value in ("60", "40", "20"):  # the overall dimensions, in part millimetres, not scaled
        assert f">{value}</text>" in text, value
    root = ET.fromstring(text)
    layers = {g.get("id") for g in root.iter("{http://www.w3.org/2000/svg}g")}
    assert {"layer-CONTORNO", "layer-COTAS", "layer-ROTULO"} <= layers
    # the four views (front, top, right, isometric) are drawn: their edges are in the contour layer, inside the frame
    contour = [e for e in sheet.entities if getattr(e, "layer", None) == "CONTORNO" and not isinstance(e, model.Text)]
    assert len(contour) > 25
    xs = [p for e in contour if isinstance(e, model.Line) for p in (e.x1, e.x2)]
    assert 20.0 < min(xs) and max(xs) < 410.0


def check_the_pdf_refuses_a_bad_target() -> None:
    build_part03()
    call("create_drawing", op("d", template="A4"))
    expect_error("invalid_request", "export_drawing", op("e", path=_tmp("a.svg"), format="svg"))
    expect_error("invalid_request", "export_drawing", op("e", path="", format="pdf"))


# --- the wait, against stand-in views (a real FreeCAD computes synchronously headless: the wait must still be exact) --


class _View:
    TypeId = "TechDraw::DrawViewPart"

    def __init__(self, label: str, ready_after: int, state: list[str] | None = None) -> None:
        self.Label = label
        self.polls = 0
        self.ready_after = ready_after
        self.State = state or ["Up-to-date"]

    def getVisibleEdges(self) -> list[object]:  # noqa: N802 - TechDraw's own method name
        self.polls += 1
        if self.polls <= self.ready_after:
            return []  # the computation is still running: nothing to read yet
        return [object()]


class _Page:
    def __init__(self, *views: _View) -> None:
        self.Views = list(views)


class _Doc:
    def __init__(self) -> None:
        self.recomputes = 0

    def recompute(self) -> None:
        self.recomputes += 1


def check_the_wait_polls_until_every_view_has_geometry() -> None:
    slow, fast = _View("Front", ready_after=5), _View("Top", ready_after=1)
    doc = _Doc()
    waited = drawing.wait_views(doc, _Page(slow, fast), "w", timeout_s=10.0)
    assert slow.polls > 5 and fast.polls >= 2, "it kept polling until the slowest view had its geometry"
    assert waited > 0 and doc.recomputes >= 1


def check_the_wait_names_the_views_that_never_finish() -> None:
    stuck = _View("Isometric", ready_after=10**9)
    done = _View("Front", ready_after=0)
    try:
        drawing.wait_views(_Doc(), _Page(done, stuck), "w", timeout_s=0.3)
    except WorkerError as exc:
        assert exc.code == "timeout" and "Isometric" in exc.message and "Front" not in exc.message
        assert "nothing was done" in exc.message
    else:
        raise AssertionError("the wait returned with a view still being computed")


def check_a_view_that_failed_to_compute_fails_the_wait_at_once() -> None:
    broken = _View("Top", ready_after=0, state=["Invalid"])
    try:
        drawing.wait_views(_Doc(), _Page(broken), "w", timeout_s=30.0)
    except WorkerError as exc:
        assert exc.code == "geometry_failed" and "Top" in exc.message
    else:
        raise AssertionError("an invalid view did not fail the wait")


class _SettlingDoc(_Doc):
    """A document whose second recompute is the one that finally settles the touched view."""

    def __init__(self, view: _View) -> None:
        super().__init__()
        self.view = view

    def recompute(self) -> None:
        super().recompute()
        if self.recomputes >= 2:
            self.view.State = ["Up-to-date"]


def check_a_touched_view_is_recomputed_before_it_is_trusted() -> None:
    touched = _View("Front", ready_after=0, state=["Touched"])
    doc = _SettlingDoc(touched)
    drawing.wait_views(doc, _Page(touched), "w", timeout_s=10.0)
    assert doc.recomputes >= 2 and touched.State == ["Up-to-date"]
