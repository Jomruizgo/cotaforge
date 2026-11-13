"""Production drawings with ``projection="hlr"`` in the headless worker: the standard views are projected by us
(``TechDraw.projectEx``) instead of being TechDraw view objects. What must hold: the page keeps its template and
title-block fields but has no view objects; every number (dimensions, hole positions) is the one the default sheet gives
on the same part; the mode survives ``update``, a save and a reopen; the default sheet is not touched by any of it."""

import os
import tempfile
from typing import Any

import drawing
import main
import sheet_model as model
from check_drawing_sheets import add, build_plate, composed, texts
from check_save_resume import reopen, save_and_reset, workdir
from harness import build_part03, call, expect_error, op

ITEMS = (
    {"type": "hole_dim", "all": True},
    {"type": "datum_dims"},
    {"type": "dim", "kind": "distance", "from": {"at": [15, 12, 10]}, "to": {"at": [85, 10, 10]}, "along": "x"},
    {"type": "view_lines"},
    {"type": "note", "text": "Sin rebabas"},
)
TOL = 0.02  # mm on the page
ALL_VIEWS = ["front", "top", "right", "iso"]  # a round part is drawn in fewer views unless it says so


def _rec() -> Any:
    assert main.WORKER.active is not None
    return main.WORKER.active


def _techdraw_types() -> set[str]:
    return {str(o.TypeId) for o in _rec().fc_doc.Objects if str(o.TypeId).startswith("TechDraw::")}


def _both(items: tuple[dict[str, Any], ...] = ITEMS) -> dict[str, tuple[model.Sheet, list[Any]]]:
    out = {}
    for mode in ("techdraw", "hlr"):
        call("create_drawing", op("d", template="A3", title="Placa", projection=mode, fresh=True))
        add(*items)
        out[mode] = composed()
    return out


def _circles(sheet: model.Sheet) -> list[tuple[float, float, float]]:
    found = [(e.cx, e.cy, e.r) for e in sheet.entities if isinstance(e, model.Circle) and e.layer == model.CONTOUR]
    return sorted(found, key=lambda c: tuple(round(v, 1) for v in c))  # rounded: two circles share an x to 1e-13


def _segments(sheet: model.Sheet) -> list[tuple[float, float, float, float]]:
    found = [
        (min(e.x1, e.x2), min(e.y1, e.y2), max(e.x1, e.x2), max(e.y1, e.y2))
        for e in sheet.entities
        if isinstance(e, model.Line) and e.layer == model.CONTOUR
    ]
    return sorted(found, key=lambda c: tuple(round(v, 1) for v in c))


def check_an_hlr_sheet_has_the_template_and_its_fields_but_no_view_objects() -> None:
    build_part03()
    result = call("create_drawing", op("d", template="A4", title="Soporte L", author="Ana", projection="hlr"))
    assert result["projection"] == "hlr" and result["views"] == ["front", "top", "right", "isometric"], result
    assert result["dimensions"] == ["width 60 mm", "height 20 mm", "depth 40 mm"] and result["scale"] == 1.0
    # no TechDraw view objects (they take minutes on a big assembly): the views are symbols of our own projection, so
    # the sheet window is never blank
    assert _techdraw_types() == {"TechDraw::DrawPage", "TechDraw::DrawSVGTemplate", "TechDraw::DrawViewSymbol"}
    page = _rec().fc_doc.getObject("Drawing")
    assert set(drawing.sheet_mirror.owned(page)) == {"view:front", "view:top", "view:right", "view:isometric", "o1"}
    fields = dict(page.Template.EditableTexts)
    assert fields["title"] == "Soporte L" and fields["creator"] == "Ana" and fields["scale"] == "1 : 1"
    assert drawing.spec_of(page)["projection"] == "hlr" and not drawing.part_views(page)
    sheet, results = composed()
    assert [r.status for r in results] == ["ok"] and any("60" in t for t in texts(sheet, model.DIMENSIONS))


def check_the_default_sheet_keeps_its_spec_and_its_view_objects() -> None:
    build_part03()
    result = call("create_drawing", op("d", template="A4"))
    assert result["projection"] == "techdraw"
    page = _rec().fc_doc.getObject("Drawing")
    assert "projection" not in drawing.spec_of(page) and "hlr" not in drawing.spec_of(page)  # the spec as it was
    assert {"TechDraw::DrawViewPart", "TechDraw::DrawProjGroup"} <= _techdraw_types()
    assert len(drawing.part_views(page)) == 4
    expect_error("invalid_request", "create_drawing", op("d", projection="fast"))


def check_hlr_gives_the_same_dimensions_hole_positions_and_views_as_techdraw() -> None:
    build_plate()
    both = _both()
    (td, td_results), (hlr, hlr_results) = both["techdraw"], both["hlr"]
    assert [(r.id, r.status) for r in td_results] == [(r.id, r.status) for r in hlr_results]
    assert all(r.status in ("ok", "warning") for r in hlr_results), [(r.id, r.status, r.detail) for r in hlr_results]
    assert sorted(t for t in texts(td)) == sorted(t for t in texts(hlr))  # every callout and dimension value
    positions = lambda s: sorted((e.text, e.x, e.y) for e in s.entities if isinstance(e, model.Text))  # noqa: E731
    for a, b in zip(positions(td), positions(hlr), strict=True):
        assert a[0] == b[0] and abs(a[1] - b[1]) < TOL and abs(a[2] - b[2]) < TOL, (a, b)
    circles_td, circles_hlr = _circles(td), _circles(hlr)
    assert len(circles_td) == len(circles_hlr) == 4  # the four holes, as circles, in the top view
    for a, b in zip(circles_td, circles_hlr, strict=True):
        assert all(abs(x - y) < TOL for x, y in zip(a, b, strict=True)), (a, b)
    seg_td, seg_hlr = _segments(td), _segments(hlr)  # the outlines of the four views, isometric included
    assert len(seg_td) == len(seg_hlr)
    for a, b in zip(seg_td, seg_hlr, strict=True):
        assert all(abs(x - y) < TOL for x, y in zip(a, b, strict=True)), (a, b)


def check_hlr_holds_for_a_round_part_with_arcs_and_a_hole_pattern() -> None:
    from harness import build_part04  # noqa: PLC0415 - flange: a disc, a bore, six bolt holes

    build_part04()
    both = {}
    # a techdraw sheet with a front view draws a turned part with its axis horizontal (f3dd2e3) and an hlr sheet never does
    # (``drawing.turned_of``), so the scale and the place of every view differ by design. What must still match is what the
    # sheet says: every text but the scale labels, and every circle's size in the part's own millimetres.
    scales = {}
    for mode in ("techdraw", "hlr"):
        made = call("create_drawing", op("d", template="A3", projection=mode, views=ALL_VIEWS, fresh=True))
        scales[mode] = float(made["scale"])
        add({"type": "hole_dim", "all": True})
        both[mode] = composed()[0]

    def said(sheet: model.Sheet) -> list[str]:
        return sorted(t for t in texts(sheet) if " : " not in t)  # '1 : 2', 'Isométrica 1 : 2'

    td, hlr = both["techdraw"], both["hlr"]
    assert said(td) == said(hlr), (sorted(set(said(td)) ^ set(said(hlr))), len(said(td)), len(said(hlr)))
    ctd, chlr = _circles(td), _circles(hlr)
    assert len(ctd) == len(chlr) >= 7  # bore, six bolt holes (the disc outline too)
    radii_td = sorted(round(c[2] / scales["techdraw"], 2) for c in ctd)
    radii_hlr = sorted(round(c[2] / scales["hlr"], 2) for c in chlr)
    assert radii_td == radii_hlr, (radii_td, radii_hlr)


def check_the_mode_survives_update_a_save_and_a_reopen() -> None:
    build_plate()
    call("create_drawing", op("d", template="A3", title="Placa", projection="hlr"))
    add(*ITEMS[:3])
    updated = call("annotate_drawing", op("u", action="update"))
    assert updated["projection"] == "hlr" and "TechDraw::DrawViewPart" not in _techdraw_types()
    sheet_before, results_before = composed()
    spec_before = drawing.spec_of(drawing.find_page(_rec().fc_doc))
    assert spec_before["projection"] == "hlr" and spec_before["hlr"]["scale"] > 0
    with workdir() as directory:
        path, state, _ = save_and_reset(directory, "placa")
        reopen(path, state)
        assert drawing.spec_of(drawing.find_page(_rec().fc_doc)) == spec_before
        described = call("describe_drawing", op("l"))["sheets"][0]
        assert described["projection"] == "hlr"
        assert [(i["id"], i["status"]) for i in described["items"]] == [(r.id, r.status) for r in results_before]
        sheet_after, _ = composed()
        assert texts(sheet_after) == texts(sheet_before) and len(sheet_after.entities) == len(sheet_before.entities)
        out = os.path.join(tempfile.mkdtemp(prefix="cadh_hlr_"), "hlr.pdf")
        call("export_drawing", op("e", path=out, format="pdf"))
        assert os.path.getsize(out) > 10000
        dxf = os.path.join(os.path.dirname(out), "hlr.dxf")
        call("export_drawing", op("e", path=dxf, format="dxf"))
        assert os.path.getsize(dxf) > 2000


def check_create_again_switches_the_mode_and_a_second_sheet_can_use_the_other() -> None:
    build_plate()
    call("create_drawing", op("d", template="A3", projection="hlr"))
    add({"type": "hole_dim", "all": True})
    call("create_drawing", op("d", template="A3", sheet="Otra"))  # the default: a second sheet, TechDraw's views
    listed = {s["sheet"]: s["projection"] for s in call("describe_drawing", op("l"))["sheets"]}
    assert listed == {"Drawing": "hlr", "Otra": "techdraw"}, listed
    again = call("create_drawing", op("d", template="A3", projection="techdraw"))  # the first sheet, rebuilt
    assert again["projection"] == "techdraw"
    page = drawing.find_page(_rec().fc_doc, "Drawing")
    assert (
        len(drawing.part_views(page)) == 4
        and len([i for i in drawing.spec_of(page)["items"] if not i.get("auto")]) == 2
    )  # a rebuild keeps its items: the overall dimension and the callout
    call("create_drawing", op("d", template="A3", projection="hlr"))
    assert drawing.spec_of(drawing.find_page(_rec().fc_doc, "Drawing"))["projection"] == "hlr"
    pages = [o for o in _rec().fc_doc.Objects if str(o.TypeId) == "TechDraw::DrawPage"]
    assert len(pages) == 2


def check_the_projections_are_kept_between_compositions() -> None:
    import sheet_project  # noqa: PLC0415

    build_plate()
    call("create_drawing", op("d", template="A3", projection="hlr"))
    assert len(sheet_project._VIEW_CACHE) >= 4  # front, top, right, isometric, made once by create
    kept = dict(sheet_project._VIEW_CACHE)
    add({"type": "hole_dim", "all": True})
    composed()
    assert all(sheet_project._VIEW_CACHE[k] is v for k, v in kept.items())  # the same projections, not made again
    assert len(sheet_project._VIEW_CACHE) <= sheet_project.VIEW_CACHE_MAX


def _signature(projection: Any) -> list[tuple[Any, ...]]:
    out = []
    for e in [*projection.visible, *projection.hidden]:
        if isinstance(e, model.Line):
            out.append(("L", e.layer, *sorted([(round(e.x1, 2), round(e.y1, 2)), (round(e.x2, 2), round(e.y2, 2))])))
        elif isinstance(e, model.Circle):
            out.append(("C", e.layer, round(e.cx, 2), round(e.cy, 2), round(e.r, 2)))
        elif isinstance(e, model.Arc):
            out.append(("A", e.layer, round(e.cx, 2), round(e.cy, 2), round(e.r, 2)))
        else:
            out.append(("P", e.layer, len(e.points)))
    return sorted(out, key=repr)


def check_projecting_by_clusters_gives_the_edges_of_projecting_everything_at_once() -> None:
    import FreeCAD as App  # noqa: PLC0415
    import Part  # noqa: PLC0415
    import sheet_project  # noqa: PLC0415

    shapes = []
    for n in range(14):  # a grid of drilled plates, 3 rows deep so that some overlap on the view plane
        plate = Part.makeBox(20, 20, 6, App.Vector((n % 5) * 30, (n // 5) * 30, 0))
        plate = plate.cut(Part.makeCylinder(3, 10, App.Vector((n % 5) * 30 + 10, (n // 5) * 30 + 10, -2)))
        shapes.append(plate)
    shapes.append(Part.makeBox(10, 10, 30, App.Vector(5, 5, 8)))  # a tall block over the first plates (hides some)
    compound = Part.Compound(shapes)
    centre = (0.0, 0.0, 0.0)
    for xdir, direction in (
        ((1.0, 0.0, 0.0), (0.0, -1.0, 0.0)),
        ((1.0, 0.0, 0.0), (0.0, 0.0, 1.0)),
        ((0.0, 1.0, 0.0), (1.0, 0.0, 0.0)),
    ):
        boxes = sheet_project._view_boxes(compound.Solids, centre, xdir, direction)
        assert len(sheet_project.clusters(boxes)) >= 1
        clustered = sheet_project.project([compound], centre, xdir, direction)
        saved = sheet_project.CLUSTER_MIN_SOLIDS
        sheet_project.CLUSTER_MIN_SOLIDS = 10**9
        try:
            whole = sheet_project.project([compound], centre, xdir, direction)
        finally:
            sheet_project.CLUSTER_MIN_SOLIDS = saved
        assert _signature(clustered) == _signature(whole), direction
    # the front view of the grid is several clusters (plates side by side do not overlap), the right one fewer
    front = sheet_project.clusters(
        sheet_project._view_boxes(compound.Solids, centre, (1.0, 0.0, 0.0), (0.0, -1.0, 0.0))
    )
    assert 1 < len(front) < 15
