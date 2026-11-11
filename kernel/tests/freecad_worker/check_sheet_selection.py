# ruff: noqa: E501
"""Selection on drawing sheets (``sheet_select``): what a line of a view is in the model, what an item refers to, and what
the agent points at. Headless: the sheet window is not needed to resolve a sub-element name (``Edge4`` is an index into the
geometry the view keeps), only to paint.

The accuracy of the link from a line of a view to a model edge is checked against OCC, not against our own projection:
the sight line through a sample of the line (the view's origin, x and y axes, direction) must touch the model edge the
match names (``Part.Shape.distToShape`` in 3D), and the edge it names must be the one nearest the viewer of those the
line could be.
"""

from typing import Any

import drawing
import FreeCAD as App
import main
import Part
import registry as reg
import sheet_refs
import sheet_select
from check_drawing_live import DOWELS, add, build_part, side_face
from harness import build_part02, call, face_info, op

SIGHT_TOL_MM = 0.02


def _rec() -> Any:
    assert main.WORKER.active is not None
    return main.WORKER.active


def _page() -> Any:
    page = drawing.find_page(_rec().fc_doc)
    assert page is not None
    return page


def _view(name: str) -> Any:
    view = sheet_select._find_view(sheet_select.Sheets(_rec()).pages[0], name)  # noqa: SLF001
    assert view is not None, name
    return view


def _edge_shape(edge_id: str) -> Any:
    doc = _rec()
    for body in doc.live_bodies():
        found = body.find("edges", edge_id)
        if found is not None:
            return reg.world_shape(doc, body).getElement(found.sub_name)
    raise AssertionError(edge_id)


def _sight(view: Any, point: tuple[float, float], edge_id: str) -> tuple[float, float]:
    """``(distance, depth)``: how near the sight line through ``point`` (view coordinates, scale 1, y up) passes to the model
    edge, and how far towards the viewer that nearest point of the edge is -- by OCC, not by our projection."""
    d = view.Direction.normalize() if hasattr(view.Direction, "normalize") else view.Direction
    d = App.Vector(d)
    d.normalize()
    x = App.Vector(view.XDirection)
    x.normalize()
    y = d.cross(x)
    q = App.Vector(view.getGeometricCenter()) + x * point[0] + y * point[1]
    sight = Part.makeLine(q - d * 5000.0, q + d * 5000.0)
    distance, pairs, _ = sight.distToShape(_edge_shape(edge_id))
    return distance, float(pairs[0][1].dot(d))


def _lines(view: Any) -> list[tuple[int, list[tuple[float, float]]]]:
    return [
        (i, sheet_select.edge_points(view.getEdgeByIndex(i)))
        for i in range(len(view.getVisibleEdges()) + len(view.getHiddenEdges()))
    ]


def _sheet(*items: dict[str, Any]) -> None:
    call("create_drawing", op("d", template="A3", title="Placa", scale=1.0))
    if items:
        add(*items)


DIM = {
    "type": "dim",
    "id": "d3",
    "kind": "distance",
    "from": {"at": [15, 12, 0]},
    "to": {"at": [85, 12, 0]},
    "along": "x",
}
HOLES = {"type": "hole_dim", "id": "h1", "at": [[15, 12, 0], [85, 12, 0]]}


# --- the objects of an item carry its id and its sheet --------------------------------------------------------------


def check_the_objects_of_an_item_carry_its_id_and_its_sheet() -> None:
    build_part()
    _sheet(HOLES, DIM)
    page = _page()
    by_item = {
        str(o.CotaforgeItem): o
        for o in page.Views
        if "CotaforgeItem" in o.PropertiesList and "CotaforgeNative" not in o.PropertiesList
    }  # the items' own objects: TechDraw's dimensions and annotations, and the symbols of what it has no object for
    assert set(by_item) == {"h1", "d3", "loc"}, set(by_item)  # loc: the default location of the holes
    assert all(str(o.CotaforgeSheet) == "Drawing" for o in by_item.values())
    overall = [o for o in page.Views if "CotaforgeNative" in o.PropertiesList]
    assert len(overall) == 3 and all(
        str(o.CotaforgeItem) == "o1" and str(o.CotaforgeSheet) == "Drawing" for o in overall
    )
    # a second sheet names its own objects; the first is not touched
    call("create_drawing", op("d2", template="A3", title="Segunda", scale=1.0, sheet="Hoja2"))
    add_second = call("annotate_drawing", op("a", action="add", items=[HOLES], ids=[], sheet="Hoja2"))
    assert add_second["changed"] == ["h1"]
    second = drawing.find_page(_rec().fc_doc, "Hoja2")
    tagged = [o for o in second.Views if "CotaforgeSheet" in o.PropertiesList]
    assert tagged and {str(o.CotaforgeSheet) for o in tagged} == {"Hoja2"}
    assert {str(o.CotaforgeSheet) for o in page.Views if "CotaforgeSheet" in o.PropertiesList} == {"Drawing"}


def check_an_unchanged_item_of_a_sheet_made_before_the_tags_gets_them() -> None:
    build_part()
    _sheet()
    call(
        "annotate_drawing", op("noloc", action="remove", ids=["loc"], items=[])
    )  # else loc draws the crosses of the holes
    add(HOLES)
    page = _page()
    symbol = next(
        o
        for o in page.Views
        if "CotaforgeItem" in o.PropertiesList and "Symbol" in o.PropertiesList and str(o.CotaforgeItem) == "h1"
    )
    symbol.removeProperty("CotaforgeItem")
    symbol.removeProperty("CotaforgeSheet")
    add(DIM)  # the next sync leaves h1 as it is (unchanged) and puts the tags back
    assert str(symbol.CotaforgeItem) == "h1" and str(symbol.CotaforgeSheet) == "Drawing"


# --- a line of a view is a model edge -------------------------------------------------------------------------------------


def check_every_line_of_the_standard_views_is_the_model_edge_under_it() -> None:
    build_part()
    _sheet()
    total = 0
    for name in ("front", "top", "right", "isometric"):
        view = _view(name)
        rows = sheet_select.map_view(_rec(), None, name)
        assert rows and len(rows) == len(_lines(view)), name
        samples = dict(_lines(view))
        for row in rows:
            assert row["edges"] or row["faces"], f"{name} Edge{row['index']} ({row['curve']}) matches nothing"
            for edge_id in row["edges"]:
                for point in (
                    samples[row["index"]][0],
                    samples[row["index"]][len(samples[row["index"]]) // 2],
                    samples[row["index"]][-1],
                ):
                    distance, _ = _sight(view, point, edge_id)
                    assert distance <= SIGHT_TOL_MM, (name, row["index"], edge_id, distance)
            total += 1
    assert total >= 4 + 13 + 4 + 30, total  # the lines of the four views


def check_the_edge_a_visible_line_names_is_the_one_nearest_the_viewer() -> None:
    build_part()
    _sheet()
    view = _view("top")
    samples = dict(_lines(view))
    behind_seen = 0
    for row in sheet_select.map_view(_rec(), None, "top"):
        if row["hidden"]:
            continue
        point = samples[row["index"]][len(samples[row["index"]]) // 2]
        shown = max(_sight(view, point, e)[1] for e in row["edges"])
        for other in row["behind"]:
            behind_seen += 1
            assert _sight(view, point, other)[1] <= shown + 1e-6, (row["index"], other)
    assert behind_seen >= 13, behind_seen  # every line of the top view has another edge under it (the bottom one)


def check_a_hole_circle_is_the_rim_of_its_hole_and_names_its_wall() -> None:
    build_part()
    _sheet()
    doc = _rec()
    sheets = sheet_select.Sheets(doc)
    info = sheets.pages[0]
    view = _view("top")
    circles = [
        (i, view.getEdgeByIndex(i))
        for i in range(len(view.getVisibleEdges()))
        if type(view.getEdgeByIndex(i).Curve).__name__ == "Circle"
    ]
    assert len(circles) == 13 - 4  # the plate has 4 + 2 + 2 + 1 holes: 9 circles (the counterbores make two each)
    found = 0
    for index, edge in circles:
        pick = sheet_select._edge_pick(sheets, info, view, "top", index)  # noqa: SLF001
        assert pick["kind"] == "edge" and pick["view"] == "top" and pick["edges"], pick
        if abs(2 * edge.Curve.Radius - 6.0) < 1e-6:  # a dowel: D6 through
            (hole,) = pick["holes"]
            assert hole["diameter_mm"] == 6.0 and hole["through"] is True and hole["direction"][2] in (1.0, -1.0)
            wall = hole["faces"]
            assert set(wall) <= set(pick["faces"]) and wall, pick
            centre = [round(c, 3) for c in (edge.Curve.Center.x, edge.Curve.Center.y)]
            assert (round(centre[0] + 50, 3), round(centre[1] + 30, 3)) in {
                (x, y) for x, y in DOWELS
            }  # the model's own hole centres
            assert face_info({"id": wall[0]})["surface"] == "cylinder"
            rim = _edge_shape(pick["edges"][0])
            assert (
                abs(rim.Curve.Radius - 3.0) < 1e-6 and abs(rim.Curve.Center.z - 19.0) < 1e-6
            )  # the TOP rim: nearest the viewer
            found += 1
    assert found == 4, found


def check_the_silhouette_lines_of_a_shaft_are_its_cylinder_faces() -> None:
    build_part02()
    call("create_drawing", op("d", template="A3", title="Eje", scale=1.0))
    rows = sheet_select.map_view(_rec(), None, "front")
    by_face: dict[str, list[int]] = {}
    for row in rows:
        assert row["edges"] or row["faces"], row
        for face in row["faces"]:
            by_face.setdefault(face, []).append(row["index"])
    assert len(by_face) == 2 and all(len(lines) == 2 for lines in by_face.values()), (
        by_face
    )  # two radii, two lines each
    view = _view("front")
    radii = {}
    for face, (a, b) in by_face.items():
        ya, yb = (view.getEdgeByIndex(i).BoundBox.YMin for i in (a, b))
        radii[face] = round(abs(ya - yb) / 2.0, 3)
        assert abs(face_info({"id": face})["radius"] - radii[face]) < 1e-3, (face, radii[face])
    assert sorted(radii.values()) == [6.0, 10.0]


def check_every_line_of_a_shaft_is_explained_in_all_four_views() -> None:
    build_part02()
    call("create_drawing", op("d", template="A3", title="Eje", scale=1.0, views=["front", "top", "right", "iso"]))
    lines = 0
    for name in ("front", "top", "right", "isometric"):
        view = _view(name)
        samples = dict(_lines(view))
        for row in sheet_select.map_view(_rec(), None, name):
            lines += 1
            assert row["edges"] or row["faces"], (
                name,
                row,
            )  # a rim, or the silhouette of a cylinder (tilted in the iso view)
            for edge_id in row["edges"]:
                assert all(_sight(view, p, edge_id)[0] <= SIGHT_TOL_MM for p in samples[row["index"]]), (name, row)
    assert lines == 29, lines


def check_hidden_lines_are_the_farthest_edges_and_the_walls_of_the_holes() -> None:
    build_part()
    _sheet()
    page = _page()
    group = next(o for o in page.Views if str(o.TypeId) == "TechDraw::DrawProjGroup")
    for item in group.Views:
        item.HardHidden = True
    _rec().fc_doc.recompute()
    drawing.wait_views(_rec().fc_doc, page, None)
    view = _view("front")
    visible, hidden = len(view.getVisibleEdges()), len(view.getHiddenEdges())
    assert visible == 4 and hidden > 4
    rows = sheet_select.map_view(_rec(), None, "front")
    assert [r["hidden"] for r in rows] == [False] * visible + [True] * hidden
    samples = dict(_lines(view))
    walls = set()
    for row in rows[visible:]:
        assert row["edges"] or row["faces"], row
        walls.update(row["faces"])
        for edge_id in row["edges"]:
            point = samples[row["index"]][len(samples[row["index"]]) // 2]
            far = _sight(view, point, edge_id)[1]
            assert all(_sight(view, point, other)[1] >= far - 1e-6 for other in row["behind"]), (
                row
            )  # a hidden line: the farthest
    assert len(walls) >= 7 and all(face_info({"id": f})["surface"] == "cylinder" for f in walls), (
        walls
    )  # the holes' walls


def check_a_line_that_no_model_edge_explains_says_so() -> None:
    build_part()
    _sheet()
    sheets = sheet_select.Sheets(_rec())
    view = _view("top")
    page = sheets.pages[0]
    view.makeCosmeticLine(App.Vector(0, 0, 0), App.Vector(10, 10, 0))  # a line the engineer drew by hand
    _rec().fc_doc.recompute()
    drawing.wait_views(_rec().fc_doc, page.page, None)
    last = len(view.getVisibleEdges()) + len(view.getHiddenEdges()) - 1
    pick = sheet_select._edge_pick(sheets, page, view, "top", last)  # noqa: SLF001
    assert not pick["edges"] and "no model edge" in pick["detail"], pick


# --- an item says what it measures ---------------------------------------------------------------------------------------------


def check_a_dimension_pick_says_its_id_its_text_and_the_two_holes_it_measures() -> None:
    build_part()
    _sheet(HOLES, DIM)
    sheets = sheet_select.Sheets(_rec())
    symbol = next(o for o in _page().Views if o.Name.endswith("_d3"))
    pick = sheet_select.read_pick(sheets, symbol, "")
    assert pick["kind"] == "item" and pick["item"] == "d3" and pick["item_type"] == "dim" and pick["sheet"] == "Drawing"
    assert pick["text"] == "70"
    assert [h["diameter_mm"] for h in pick["holes"]] == [6.0, 6.0]
    assert [h["origin"][:2] for h in pick["holes"]] == [[15.0, 12.0], [85.0, 12.0]]
    assert len(pick["faces"]) == 2 and all(face_info({"id": f})["surface"] == "cylinder" for f in pick["faces"])
    assert "distance along x" in pick["detail"] and "hole D6" in pick["detail"]
    callout = sheet_select.read_pick(sheets, next(o for o in _page().Views if o.Name.endswith("_h1")), "")
    assert callout["item"] == "h1" and "pasante" in callout["text"] and len(callout["holes"]) == 2
    width = next(
        o for o in _page().Views if "CotaforgeNative" in o.PropertiesList and str(o.CotaforgeNative) == "overall:width"
    )
    overall = sheet_select.read_pick(sheets, width, "")
    assert overall["item"] == "o1" and overall["item_type"] == "overall" and "width" in overall["detail"]
    assert sheet_refs.number_in(overall["text"]) == 100.0


def check_a_view_pick_says_the_view() -> None:
    build_part()
    _sheet()
    sheets = sheet_select.Sheets(_rec())
    pick = sheet_select.read_pick(sheets, _view("right"), "")
    assert pick["kind"] == "view" and pick["view"] == "right" and "looks along [1, 0, 0]" in pick["detail"]


# --- from the model to the sheet ------------------------------------------------------------------------------------------------


def check_the_items_that_refer_to_a_face_or_an_edge_are_found_through_holes_and_ids() -> None:
    base = build_part()
    datum_face = side_face(base, (0, 0, -1))
    _sheet(HOLES, DIM, {"type": "datum", "id": "dA", "letter": "A", "face": datum_face})
    sheets = sheet_select.Sheets(_rec())
    holes = sheets.holes(sheets.pages[0])
    walls = {h.diameter: h.faces[0] for h in holes if abs(h.diameter - 6.0) < 1e-6}
    wall_of = {(round(h.centre[0]), round(h.centre[1])): h.faces[0] for h in holes if abs(h.diameter - 6.0) < 1e-6}
    assert walls
    found = sheet_select.items_referring_to(
        _rec(), [("face", wall_of[(15, 12)]), ("face", wall_of[(15, 48)]), ("face", datum_face)]
    )
    assert {r["item"] for r in found[wall_of[(15, 12)]]} == {"h1", "d3", "loc"}  # loc: the default location
    assert [r["text"] for r in found[wall_of[(15, 12)]] if r["item"] == "d3"] == ["70"]
    assert [r["item"] for r in found[wall_of[(15, 48)]]] == ["loc"]  # the other dowel: only its location
    assert [r["item"] for r in found[datum_face]] == ["dA"]
    # a hole's rim edge stands for the hole
    rim = next(
        e for e in _rec().live_bodies()[0].edges.values() if wall_of[(15, 12)] in e.faces and e.sig.curve == "circle"
    )
    assert {r["item"] for r in sheet_select.items_referring_to(_rec(), [("edge", rim.id)])[rim.id]} == {
        "h1",
        "d3",
        "loc",
    }


def check_a_point_on_a_face_is_a_reference_to_that_face() -> None:
    base = build_part()
    top = side_face(base, (0, 0, 1))
    bottom = side_face(base, (0, 0, -1))
    _sheet(
        {
            "type": "dim",
            "id": "dp",
            "kind": "distance",
            "from": {"at": [0, 0, 19]},
            "to": {"at": [100, 0, 19]},
            "along": "x",
        },
    )
    found = sheet_select.items_referring_to(_rec(), [("face", top), ("face", bottom)])
    assert [r["item"] for r in found[top]] == ["dp"] and found[bottom] == []


def check_pointing_at_items_and_at_the_model_makes_the_plan_of_each_side() -> None:
    build_part()
    _sheet(HOLES, DIM)
    doc = _rec()
    sheets = sheet_select.Sheets(doc)
    walls = [h.faces[0] for h in sheets.holes(sheets.pages[0]) if abs(h.diameter - 6.0) < 1e-6]
    # an item on the sheet; "auto" paints nothing in 3D, "3d" also the faces it refers to
    plan = sheet_select.plan(doc, ["d3"], [], [], "auto", None)
    (paint,) = plan.paints.values()
    assert paint.shown == ["d3"] and plan.faces == []
    # TechDraw's own dimension is recoloured (the headless CAD has no colours to set: it says so), never boxed
    assert paint.rects == []
    assert [str(o.TypeId) for o in paint.recolor] == [
        "TechDraw::DrawViewDimension"
    ] or "cannot be coloured" in " ".join(plan.notes)
    plan = sheet_select.plan(doc, ["d3"], [], [], "3d", None)
    assert len(plan.faces) == 2 and set(plan.faces) <= set(walls)
    # a view, by its name
    plan = sheet_select.plan(doc, ["view:top"], [], [], "auto", None)
    assert plan.paints["Drawing"].shown == ["view:top"]
    # the model on the sheet: the items that refer to a hole wall, and the circles of the views that draw it
    wall = next(f for f in walls)
    plan = sheet_select.plan(doc, [], [wall], [], "sheet", None)
    (paint,) = plan.paints.values()
    assert plan.faces == [] and {"h1", "d3"} <= set(paint.shown), paint.shown
    assert len(paint.paths) >= 1 and any(s.startswith("top view") for s in paint.shown), paint.shown
    both = sheet_select.plan(doc, [], [wall], [], "both", None)
    assert both.faces == [wall]
    # nothing named that is not there
    for bad in ("zz", "view:nowhere"):
        try:
            sheet_select.plan(doc, [bad], [], [], "auto", None)
        except Exception as exc:  # noqa: BLE001
            assert bad in str(exc) and "items:" in str(exc), exc
        else:
            raise AssertionError(bad)


def check_the_lines_a_face_paints_on_the_views_are_the_ones_that_draw_it() -> None:
    build_part()
    _sheet()
    doc = _rec()
    sheets = sheet_select.Sheets(doc)
    info = sheets.pages[0]
    hole = next(
        h
        for h in sheets.holes(info)
        if abs(h.diameter - 6.0) < 1e-6 and round(h.centre[0]) == 15 and round(h.centre[1]) == 12
    )
    plan = sheet_select.Plan()
    sheet_select._paint_model_on_views(sheets, info, [hole.faces[0]], [], plan)  # noqa: SLF001
    paint = plan.paints["Drawing"]
    # seen along its axis (top) the wall is the two rims; seen across it (front, right) the two hidden sides are not drawn
    assert [s for s in paint.shown if s.startswith("top")] == ["top view: 1 line"], (
        paint.shown
    )  # one circle: the other rim is behind it
    assert not [s for s in paint.shown if s.startswith(("front", "right"))], (
        paint.shown
    )  # across the axis the wall is hidden
    top = _view("top")
    x0, y0 = drawing._page_position(info.page, top)  # noqa: SLF001
    centre = (
        x0 + 15 - 50,
        y0 + 12 - 30,
    )  # where the model's hole is on the page: the view's origin is the part's middle
    on_hole = []
    for path in paint.paths:
        xs, ys = [p[0] for p in path], [p[1] for p in path]
        if abs((max(xs) + min(xs)) / 2 - centre[0]) < 0.05 and abs((max(ys) + min(ys)) / 2 - centre[1]) < 0.05:
            on_hole.append(max(xs) - min(xs))
    assert len(on_hole) == 1 and abs(on_hole[0] - 6.0) < 0.05, on_hole  # a line on the hole, as big as the hole
