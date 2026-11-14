# ruff: noqa: E501
"""One truth for the sheet in the headless worker: capture and export come from the same rendering and say the same things
about the page -- its views with their boxes, the native TechDraw objects a script added and, because the headless CAD cannot
paint them, which of those the file does not draw --, a capture can zoom into a region, and the colours Qt's PDF writer cannot
print are normalised before anything is written. What the visible CAD paints is ``test_visible_sheet_output.py``."""

import os
import tempfile
from typing import Any

import drawing
import main
import sheet_colours
from harness import build_part03, call, expect_error, op

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _tmp(name: str) -> str:
    return os.path.join(tempfile.mkdtemp(prefix="cadh_output_"), name)


def _page() -> Any:
    return main.WORKER.active.fc_doc.getObject("Drawing")


def _sheet() -> None:
    build_part03()
    call("create_drawing", op("d", template="A3", title="Soporte L"))


def _add_native_note_and_cosmetic_line(colour: tuple[float, ...] | None = None) -> None:
    import FreeCAD as App  # noqa: PLC0415

    fc_doc = main.WORKER.active.fc_doc
    page = _page()
    note = fc_doc.addObject("TechDraw::DrawViewAnnotation", "NoteNative")
    page.addView(note)
    note.Text = ["nota nativa"]
    note.X, note.Y = 100, 200
    if colour is not None:
        view = drawing.part_views(page)[0]
        fc_doc.recompute()
        tag = view.makeCosmeticLine(App.Vector(-10, 0, 0), App.Vector(10, 0, 0))
        view.getCosmeticEdge(tag).Format = {"style": 1, "weight": 0.5, "color": colour, "visible": True}
    fc_doc.recompute()


def check_a_sheet_of_items_only_has_no_native_objects_and_names_its_views() -> None:
    _sheet()
    png = _tmp("a.png")
    captured = call("capture_drawing", op("c", path=png, width=900))
    assert captured["renderer"] == "compositor" and captured["native"] == [] and captured["unrendered"] == []
    names = [v["name"] for v in captured["views"]]
    assert names == ["front", "top", "right", "isometric"], names
    assert all(v["kind"] == "standard" and v["rect"][0] < v["rect"][2] for v in captured["views"])
    assert captured["page_mm"] == [420.0, 297.0] and captured["size_px"] == [900, round(900 * 297 / 420)]
    exported = call("export_drawing", op("e", path=_tmp("a.pdf"), format="pdf"))
    assert [v["name"] for v in exported["views"]] == names and exported["views"] == captured["views"]
    assert exported["page_mm"] == captured["page_mm"] and exported["renderer"] == "compositor"
    sheet = call("describe_drawing", op("r", review=True))["sheets"][0]
    assert sheet["native"] == [] and "native_unread" not in {f["code"] for f in sheet["findings"]}


def check_native_objects_are_listed_by_capture_export_list_and_review() -> None:
    _sheet()
    _add_native_note_and_cosmetic_line(colour=(0.0, 0.0, 0.0, 0.0))
    captured = call("capture_drawing", op("c", path=_tmp("n.png"), width=900))
    assert any("NoteNative [DrawViewAnnotation]: text: nota nativa" in n for n in captured["native"]), captured[
        "native"
    ]
    assert any("1 cosmetic line(s) on" in n for n in captured["native"]), captured["native"]
    # the headless CAD cannot paint them: every one is named as not drawn, none is dropped silently
    assert len(captured["unrendered"]) == len(captured["native"]) and all(
        "not drawn" in u for u in captured["unrendered"]
    )
    exported = call("export_drawing", op("e", path=_tmp("n.pdf"), format="pdf"))
    assert exported["native"] == captured["native"] and exported["unrendered"] == captured["unrendered"]
    dxf = call("export_drawing", op("x", path=_tmp("n.dxf"), format="dxf"))
    assert dxf["unrendered"], "the DXF does not draw native objects either"
    sheet = call("describe_drawing", op("r", review=True))["sheets"][0]
    assert sheet["native"] == captured["native"]
    found = [f for f in sheet["findings"] if f["code"] == "native_unread"]
    assert len(found) == 1 and found[0]["severity"] == "warning" and found[0]["blocking"] is False
    assert "NoteNative" in found[0]["message"] and "no puede leer" in found[0]["message"]


def check_a_capture_can_zoom_into_a_region_and_refuses_one_off_the_sheet() -> None:
    _sheet()
    full = call("capture_drawing", op("c", path=_tmp("f.png"), width=1000))
    zoom_path = _tmp("z.png")
    zoomed = call("capture_drawing", op("z", path=zoom_path, width=1000, region=[20, 20, 120, 80]))
    assert zoomed["region"] == [20.0, 20.0, 120.0, 80.0] and zoomed["size_px"] == [1000, 600], zoomed
    assert full["size_px"][0] == 1000 and full["region"] is None
    with open(zoom_path, "rb") as handle:
        assert handle.read(8) == PNG_SIGNATURE
    for bad in ([-5, 0, 100, 100], [0, 0, 500, 100], [50, 50, 40, 60], [0, 0, 1, 100], [1, 2, 3]):
        exc = expect_error("invalid_request", "capture_drawing", op("b", path=_tmp("b.png"), width=500, region=bad))
        assert "region" in exc.message, (bad, exc.message)


def check_a_capture_with_crops_measures_the_sheet_and_writes_labelled_zoomed_images() -> None:
    _sheet()
    plain = call("capture_drawing", op("p", path=_tmp("p.png"), width=900))
    assert plain["zoomed"] == [] and plain["findings"] == [] and plain["sheet"] == "Drawing" and plain["edits"] >= 0
    note = {"type": "note", "text": "Sin rebabas 0,3 mm"}
    items = [{**note, "id": "na", "at_mm": [150, 150]}, {**note, "id": "nb", "at_mm": [151, 151]}]
    call("annotate_drawing", op("a", action="add", items=items))
    got = call("capture_drawing", op("c", path=_tmp("c.png"), width=900, crops=True))
    assert got["edits"] == plain["edits"] + 1
    assert [f["kind"] for f in got["findings"]].count("overlap") >= 1, got["findings"]  # two notes on one another are measured
    held = [c for c in got["zoomed"] if c["findings"]]
    assert held and len(got["zoomed"]) <= 5, got["zoomed"]
    for crop in got["zoomed"]:
        with open(crop["path"], "rb") as handle:
            assert handle.read(8) == PNG_SIGNATURE
        assert crop["label"] and crop["size_px"][0] > 0 and max(crop["size_px"]) <= 1000, crop
    box = got["findings"][held[0]["findings"][0]]["box"]
    x0, y0, x1, y1 = held[0]["region"]
    assert x0 <= box[0] + 1e-6 and y0 <= box[1] + 1e-6 and x1 >= box[2] - 1e-6 and y1 >= box[3] - 1e-6, (box, held[0]["region"])
    again = call("capture_drawing", op("d", path=_tmp("d.png"), width=900, crops=True, crops_given_at=got["edits"]))
    assert again["zoomed"] == [] and again["findings"] == got["findings"]  # told it already has them: nothing is painted
    zoom = call("capture_drawing", op("z", path=_tmp("z.png"), width=900, region=[100, 100, 200, 200], crops=True))
    assert zoom["zoomed"] == []  # a region is a zoom of its own


def check_a_colour_qt_cannot_print_is_normalised_before_the_file_is_written() -> None:
    _sheet()
    _add_native_note_and_cosmetic_line(colour=(255.0, 0.0, 0.0, 0.0))
    view = drawing.part_views(_page())[0]
    assert tuple(view.getCosmeticEdge(view.CosmeticEdges[0].Tag).Format["color"])[0] == 255.0
    exported = call("export_drawing", op("e", path=_tmp("c.pdf"), format="pdf"))
    assert any("colour outside 0..1; normalised" in w for w in exported["warnings"]), exported["warnings"]
    fixed = tuple(view.CosmeticEdges[0].Format["color"])
    assert fixed == (1.0, 0.0, 0.0, 0.0), fixed  # 255 on the 0..255 scale is full red
    again = call("export_drawing", op("e2", path=_tmp("c2.pdf"), format="pdf"))
    assert not any("normalised" in w for w in again["warnings"])  # nothing left to fix


def check_the_colour_rule_is_pure_and_leaves_a_sound_colour_alone() -> None:
    assert sheet_colours.fix_colour((0.0, 0.0, 0.0, 1.0)) is None
    assert sheet_colours.fix_colour((1.0, 0.5, 0.0)) is None
    assert sheet_colours.fix_colour((255.0, 0.0, 128.0, 0.0)) == (1.0, 0.0, 128.0 / 255.0, 0.0)
    assert sheet_colours.fix_colour((-1.0, 0.0, 1.0, 0.0)) == (0.0, 0.0, 1.0, 0.0)
    assert sheet_colours.fix_colour((float("nan"), 0.2, 0.2, 0.0)) == (0.0, 0.2, 0.2, 0.0)
    assert sheet_colours.fix_colour((2.0, 300.0, 0.0, 0.0)) == (
        1.0,
        1.0,
        0.0,
        0.0,
    )  # past 255: clamped, not a byte scale
    assert sheet_colours.fix_colour("not a colour") is None


def check_a_view_the_items_count_and_the_page_lacks_is_a_warning_with_the_pdf_still_written() -> None:
    _sheet()
    group = next(o for o in _page().Views if o.TypeId == "TechDraw::DrawProjGroup")
    group.removeProjection("Right")  # the engineer deleted a view in the window; the items still say four views
    main.WORKER.active.fc_doc.recompute()
    exported = call("export_drawing", op("e", path=_tmp("v.pdf"), format="pdf"))
    assert [v["name"] for v in exported["views"]] == ["front", "top", "isometric"], exported["views"]
    assert any("the page has no right" in w for w in exported["warnings"]), exported["warnings"]


def check_an_hlr_sheet_names_its_views_too() -> None:
    build_part03()
    call("create_drawing", op("d", template="A3", title="Soporte L", projection="hlr"))
    exported = call("export_drawing", op("e", path=_tmp("h.pdf"), format="pdf"))
    assert [v["name"] for v in exported["views"]] == ["front", "top", "right", "isometric"], exported["views"]
    assert exported["native"] == [] and not any("the page has no" in w for w in exported["warnings"])
