# ruff: noqa: E501
"""What an item refers to (``sheet_refs``), the names of the views, what a symbol shows, and the geometric link from a line
of a view to a model edge (``view_match``): plain data, no document needed."""

import math

import sheet_model as model
import sheet_refs as refs
import sheet_svg
import sheet_units as su
import view_match as vm
from sheet_context import HoleInfo

HOLES = [
    HoleInfo(0, (15.0, 12.0, 0.0), (0.0, 0.0, 1.0), 6.0, 19.0, True, ("face:7",)),
    HoleInfo(1, (85.0, 12.0, 0.0), (0.0, 0.0, 1.0), 6.0, 19.0, True, ("face:8",)),
    HoleInfo(2, (50.0, 30.0, 0.0), (0.0, 0.0, 1.0), 9.0, 19.0, True, ("face:9", "face:10")),
]


# --- what an item names -------------------------------------------------------------------------------------------------


def check_a_distance_between_two_points_on_hole_axes_names_the_two_holes() -> None:
    spec = {"id": "d3", "type": "dim", "kind": "distance", "from": {"at": [15, 12, 7]}, "to": {"at": [85, 12, 0]}}
    found = refs.refs_of(spec, HOLES)
    assert found.holes == [0, 1] and found.faces == [] and found.points == [] and not found.whole
    assert refs.faces_of_refs(found, HOLES) == ["face:7", "face:8"]


def check_faces_edges_and_free_points_of_a_dimension_are_kept_apart() -> None:
    spec = {"id": "d1", "type": "dim", "kind": "distance", "from": {"face": "face:2"}, "to": {"at": [3, 3, 3]}}
    found = refs.refs_of(spec, HOLES)
    assert found.faces == ["face:2"] and found.points == [(3.0, 3.0, 3.0)] and found.holes == []
    edge = refs.refs_of(
        {"type": "dim", "kind": "distance", "from": {"edge": "edge:4"}, "to": {"face": "face:1"}}, HOLES
    )
    assert edge.edges == ["edge:4"] and edge.faces == ["face:1"]
    assert refs.refs_of({"type": "dim", "kind": "angle", "faces": ["face:1", "face:2"]}, HOLES).faces == [
        "face:1",
        "face:2",
    ]
    assert refs.refs_of({"type": "dim", "kind": "radius", "face": ["face:5"]}, HOLES).faces == ["face:5"]


def check_a_hole_callout_names_the_holes_by_point_by_face_or_all() -> None:
    assert refs.refs_of({"type": "hole_dim", "at": [[15, 12, 0], [50, 30, 3]]}, HOLES).holes == [0, 2]
    assert refs.refs_of({"type": "hole_dim", "all": True}, HOLES).holes == [0, 1, 2]
    by_face = refs.refs_of({"type": "hole_dim", "face": ["face:10"]}, HOLES)
    assert by_face.holes == [2] and by_face.faces == ["face:10"]


def check_symbols_on_a_face_and_the_overall_dimensions() -> None:
    for kind in ("datum", "gdt", "finish", "aux_view"):
        assert refs.refs_of({"type": kind, "face": ["face:3"]}, HOLES).faces == ["face:3"]
    assert refs.refs_of({"type": "overall"}, HOLES).whole
    assert refs.refs_of({"type": "note", "text": "x"}, HOLES).faces == []
    assert refs.refs_of({"type": "datum_dims", "mode": "table"}, HOLES).holes == [
        0,
        1,
        2,
    ]  # no holes field: all of them
    assert refs.refs_of({"type": "datum_dims", "holes": [[85, 12, 0]], "x_from": "face:4"}, HOLES).holes == [1]
    assert refs.refs_of({"type": "datum_dims", "holes": "all", "x_from": "min"}, HOLES).faces == []
    assert refs.refs_of({"type": "section", "holes": [[15, 12, 5]]}, HOLES).holes == [0]
    assert refs.refs_of({"type": "detail", "at": [15, 12, 0]}, HOLES).holes == [0]
    assert refs.refs_of({"type": "no_such_type"}, HOLES).holes == []


def check_an_item_refers_to_a_face_through_its_hole_a_rim_or_an_edge_id() -> None:
    callout = refs.refs_of({"type": "hole_dim", "at": [[15, 12, 0]]}, HOLES)
    assert refs.refers_to(callout, HOLES, faces=["face:7"])
    assert not refs.refers_to(callout, HOLES, faces=["face:8"])
    assert refs.refers_to(callout, HOLES, edges=["edge:99"], rim_faces=["face:1", "face:7"])  # the rim of its hole
    assert not refs.refers_to(callout, HOLES, edges=["edge:99"], rim_faces=["face:1", "face:8"])
    on_edge = refs.refs_of(
        {"type": "dim", "kind": "distance", "from": {"edge": "edge:4"}, "to": {"edge": "edge:5"}}, HOLES
    )
    assert refs.refers_to(on_edge, HOLES, edges=["edge:5"]) and not refs.refers_to(on_edge, HOLES, edges=["edge:6"])


def check_what_a_dimension_measures_is_said_by_the_names_the_agent_knows() -> None:
    spec = {"type": "dim", "kind": "distance", "along": "x", "from": {"face": "face:1"}, "to": {"edge": "edge:2"}}
    line = refs.describe_measure(spec, HOLES, lambda ref: next(iter(ref.values())))
    assert line == "distance along x from face:1 to edge:2"
    assert (
        refs.describe_measure({"type": "dim", "kind": "angle", "faces": ["face:1", "face:2"]}, HOLES, str)
        == "angle between face:1, face:2"
    )
    assert refs.describe_measure({"type": "note"}, HOLES, str) == ""


# --- the views and what a symbol shows ------------------------------------------------------------------------------------


def check_the_views_are_named_whatever_made_them() -> None:
    assert refs.view_role("TechDraw::DrawProjGroupItem", "ProjItem001", "Top", "Top") == "top"
    assert refs.view_role("TechDraw::DrawProjGroupItem", "ProjItem", "Front", "Front") == "front"
    assert refs.view_role("TechDraw::DrawViewPart", "DrawingIsometric", "Isometric") == "isometric"
    assert refs.view_role("TechDraw::DrawViewPart", "View_front", "x") == "front"  # the native sheet's own names
    assert refs.view_role("TechDraw::DrawViewSection", "SectionA", "Section A-A") == "Section A-A"
    assert refs.view_role("TechDraw::DrawViewDimension", "Dim", "Dim") is None
    assert refs.parse_key("view:front") == ("view", "front") and refs.parse_key("threads") == ("extra", "threads")
    assert refs.parse_key("d3") == ("item", "d3")


def check_a_symbol_tells_its_texts_and_the_box_of_what_it_draws() -> None:
    entities = (
        model.Line(10.0, 20.0, 50.0, 20.0, model.DIMENSIONS),
        model.Line(10.0, 20.0, 10.0, 26.0, model.DIMENSIONS),
        model.Circle(30.0, 40.0, 3.0, model.CONTOUR),
        model.Text(30.0, 22.0, "Ø6 H7", 3.5, model.DIMENSIONS, "center"),
    )
    unit = su.Unit("h1", "h1 hole_dim", su.KIND_ITEM, entities, "hole_dim")
    rect = su.padded(unit.rect)
    svg = sheet_svg.fragment(unit.entities, 297.0, rect, unit.label)
    assert refs.texts_of(svg) == ["Ø6 H7"]
    assert refs.number_in("Ø6 H7") == 6.0 and refs.number_in("12,5 mm") == 12.5 and refs.number_in("no") is None
    x, y, w, h = refs.view_box(svg)  # type: ignore[misc]
    assert abs(x - rect[0]) < 1e-3 and abs(w - (rect[2] - rect[0])) < 1e-3
    x0, y0, x1, y1 = refs.content_bounds(svg)  # type: ignore[misc]
    assert (
        abs(x0 - 10.0) < 1e-3 and abs(x1 - 50.0) < 1e-3
    )  # the lines, not the margin of the box (the text is narrower)
    assert abs(y0 - (297.0 - 43.0)) < 1e-3  # the circle's top (y 43), svg y runs down
    assert y1 >= 297.0 - 20.0 - 1e-6  # the dimension line is at y 20
    assert refs.content_bounds('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1 1"></svg>') is None
    assert refs.content_bounds("not svg") is None and refs.texts_of("not svg") == []


# --- the geometry of a view -------------------------------------------------------------------------------------------------

TOP = vm.Frame((50.0, 30.0, 9.5), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
FRONT = vm.Frame((50.0, 30.0, 9.5), (1.0, 0.0, 0.0), (0.0, -1.0, 0.0))


def _circle3d(cx: float, cy: float, z: float, r: float, n: int = 72) -> vm.EdgeCand:
    pts = tuple(
        (cx + r * math.cos(2 * math.pi * i / n), cy + r * math.sin(2 * math.pi * i / n), z) for i in range(n + 1)
    )
    return vm.EdgeCand(f"rim@{z:g}", pts)


def check_a_frame_puts_a_model_point_where_techdraw_does() -> None:
    # verified in FreeCAD 1.1.3: the hole at model (15, 12) of a plate centred on (50, 30) is the circle centred on (-35, -18)
    assert TOP.flat((15.0, 12.0, 19.0)) == (-35.0, -18.0)
    x, y, depth = FRONT.project((15.0, 12.0, 19.0))
    assert (x, round(y, 9), depth) == (-35.0, 9.5, -12.0)  # to the right, up by z, and 12 mm AWAY from a viewer at -y
    assert TOP.ydir == (0.0, 1.0, 0.0) and FRONT.ydir == (0.0, 0.0, 1.0)


def check_a_circle_under_two_rims_is_the_one_nearest_the_viewer_and_a_hidden_one_the_farthest() -> None:
    top_rim, bottom_rim = _circle3d(15.0, 12.0, 19.0, 3.0), _circle3d(15.0, 12.0, 0.0, 3.0)
    samples = [TOP.flat(p) for p in top_rim.points[::6]]
    projected = vm.project_edges(TOP, [bottom_rim, top_rim])
    seen = vm.match_edge(TOP, samples, projected)
    assert seen.edges == ["rim@19"] and seen.behind == ["rim@0"]
    hidden = vm.match_edge(TOP, samples, projected, hidden=True)
    assert hidden.edges == ["rim@0"] and hidden.behind == ["rim@19"]


def check_a_stretch_of_a_longer_edge_is_that_edge_and_a_parallel_one_is_not() -> None:
    long = vm.EdgeCand("long", ((0.0, 0.0, 19.0), (100.0, 0.0, 19.0)))
    other = vm.EdgeCand("other", ((0.0, 5.0, 19.0), (100.0, 5.0, 19.0)))
    a, b = TOP.flat((20.0, 0.0, 19.0)), TOP.flat((60.0, 0.0, 19.0))
    found = vm.match_edge(TOP, [a, ((a[0] + b[0]) / 2, a[1]), b], vm.project_edges(TOP, [other, long]))
    assert found.edges == ["long"] and found.behind == []
    off = vm.match_edge(TOP, [a, (a[0], a[1] + 0.2), b], vm.project_edges(TOP, [long]))
    assert off.edges == []  # 0.2 mm off the line is not on it


def check_a_cylinder_seen_across_its_axis_draws_two_silhouette_lines() -> None:
    shaft = vm.CylinderCand("face:2", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 6.0, 0.0, 40.0)
    view = vm.Frame((20.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, -1.0, 0.0))  # from the front: the shaft lies along x
    upper = [view.flat((x, 0.0, 6.0)) for x in (0.0, 20.0, 40.0)]
    lower = [view.flat((x, 0.0, -6.0)) for x in (0.0, 20.0, 40.0)]
    for line in (upper, lower):
        assert vm.match_edge(view, line, [], [shaft]).faces == ["face:2"]
    middle = [view.flat((x, 0.0, 3.0)) for x in (0.0, 20.0, 40.0)]
    assert vm.match_edge(view, middle, [], [shaft]).faces == []
    along = vm.Frame((20.0, 0.0, 0.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0))  # looking along the axis: no silhouette
    assert vm.match_edge(along, [along.flat((0.0, 0.0, 6.0)), along.flat((40.0, 0.0, 6.0))], [], [shaft]).faces == []
    bore = vm.CylinderCand("face:3", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 6.0, 0.0, 40.0, convex=False)
    assert vm.match_edge(view, upper, [], [bore]).faces == []  # a hole's wall is not on a visible line ...
    assert vm.match_edge(view, upper, [], [bore], hidden=True).faces == ["face:3"]  # ... but on a hidden one


def check_a_cylinder_seen_obliquely_draws_its_silhouette_tilted() -> None:
    root3, root2 = math.sqrt(3.0), math.sqrt(2.0)
    iso = vm.Frame((20.0, 0.0, 0.0), (1 / root2, 1 / root2, 0.0), (1 / root3, -1 / root3, 1 / root3))
    shaft = vm.CylinderCand("face:2", (0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 6.0, 0.0, 40.0)
    side = (0.0, -1 / root2, -1 / root2)  # axis x direction, unit: where the surface turns away from the viewer
    for sign in (1.0, -1.0):
        line = [iso.flat((x, sign * 6.0 * side[1], sign * 6.0 * side[2])) for x in (0.0, 20.0, 40.0)]
        assert vm.match_edge(iso, line, [], [shaft]).faces == ["face:2"], sign
        off = [(px + 0.2, py) for px, py in line]  # 0.2 mm beside the generatrix
        assert vm.match_edge(iso, off, [], [shaft]).faces == []
    assert vm.match_edge(iso, [iso.flat((x, 0.0, 0.0)) for x in (0.0, 20.0, 40.0)], [], [shaft]).faces == []  # the axis


def check_a_vertex_of_a_view_is_the_model_vertex_nearest_the_viewer() -> None:
    near, far = vm.VertexCand("vertex:1", (0.0, 0.0, 19.0)), vm.VertexCand("vertex:2", (0.0, 0.0, 0.0))
    point = TOP.flat((0.0, 0.0, 19.0))
    found = vm.match_vertex(TOP, point, [far, near])
    assert found.edges == ["vertex:1"] and found.behind == ["vertex:2"]
    assert vm.match_vertex(TOP, point, [far, near], hidden=True).edges == ["vertex:2"]
    assert vm.match_vertex(TOP, (point[0] + 1.0, point[1]), [far, near]).edges == []
