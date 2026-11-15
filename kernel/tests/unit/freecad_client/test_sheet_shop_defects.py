# ruff: noqa: E501
"""What a shop reader found wrong on the first native sheets, as review checks (no FreeCAD): a length dimension whose end
stands on no line of the part, a centre line that stops short of a cylinder, and the two seats of one diameter that
were taken for one cylinder. Each check has the correct drawings it must leave alone beside the defect it finds."""

import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import items_attach as attach  # noqa: E402
import sheet_checks as sck  # noqa: E402
import sheet_context as sc  # noqa: E402
import sheet_flats as flats  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_pageread as pageread  # noqa: E402
import sheet_review as review  # noqa: E402
import sheet_shopchecks as shop  # noqa: E402

from .test_sheet_items import PlateGeometry, draw, make_context  # noqa: E402

AXIS_Y = 150.0
# a shaft along x (page mm, 1:1, axis at y 150): Ø20 0..26, Ø25 26..41, Ø32 41..101, Ø25 101..116, Ø22 116..168 with a keyway on top
# whose floor runs from x 125 to 144 and whose ends are half circles (122..125 and 144..147)
STEPS = ((0.0, 26.0, 10.0), (26.0, 41.0, 12.5), (41.0, 101.0, 16.0), (101.0, 116.0, 12.5), (116.0, 168.0, 11.0))


def shaft_ink() -> list[Any]:
    out: list[Any] = []
    for lo, hi, r in STEPS:
        out += [model.Line(lo, AXIS_Y + r, hi, AXIS_Y + r), model.Line(lo, AXIS_Y - r, hi, AXIS_Y - r)]
    for x, r0, r1 in ((26.0, 10.0, 12.5), (41.0, 12.5, 16.0), (101.0, 16.0, 12.5), (116.0, 12.5, 11.0)):  # the shoulders: the larger circle seen edge-on
        out += [model.Line(x, AXIS_Y - max(r0, r1), x, AXIS_Y + max(r0, r1))]
    out += [model.Line(0.0, AXIS_Y - 10.0, 0.0, AXIS_Y + 10.0), model.Line(168.0, AXIS_Y - 11.0, 168.0, AXIS_Y + 11.0)]  # the end faces
    out += [model.Line(125.0, AXIS_Y + 9.25, 144.0, AXIS_Y + 9.25)]  # the keyway floor
    out += [model.Polyline(((144.0, AXIS_Y + 9.25), (145.5, AXIS_Y + 10.5), (146.5, AXIS_Y + 10.9), (147.0, AXIS_Y + 11.0)))]  # its end, a round going up
    out += [model.Polyline(((125.0, AXIS_Y + 9.25), (123.5, AXIS_Y + 10.5), (122.5, AXIS_Y + 10.9), (122.0, AXIS_Y + 11.0)))]
    return out


def shaft_bosses() -> list[sck.Boss]:
    """The five cylinders of the shaft as ``sheet_facts.collect`` gives them: one boss per cylinder, its middle and its length."""
    return [sck.Boss(f"face:{n}", r, ((lo + hi) / 2, 0.0, 0.0), (1.0, 0.0, 0.0), hi - lo) for n, (lo, hi, r) in enumerate(STEPS, start=1)]


def length(item: str, a: float, b: float, traces: tuple[Any, Any] = (None, None)) -> tuple[Any, ...]:
    """A ``face_ends`` record of a length along the axis, as ``items_dims._distance`` leaves it: both ends at the faces' middles."""
    return (item, "front", "h", ((a, AXIS_Y), (b, AXIS_Y)), traces)


def context(ends: list[tuple[Any, ...]], lines: list[Any] | None = None, bosses: list[sck.Boss] | None = None) -> sc.Context:
    front = sc.ViewInfo("front", 0.0, AXIS_Y, 1.0, (0, -1, 0), (1, 0, 0), (0.0, 0.0, 0.0), (0.0, AXIS_Y - 16.0, 168.0, AXIS_Y + 16.0))

    class Nothing(sc.Geometry):
        pass

    ctx = sc.Context(sheet=model.Sheet(420.0, 297.0), views={"front": front}, holes=[], geometry=Nothing(), area=(-30.0, 40.0, 400.0, 282.0), language="en")
    ctx.sheet.entities.extend(shaft_ink())
    ctx.sheet.entities.extend(lines or [])
    ctx.extras["face_ends"] = ends
    ctx.extras["facts"] = sck.PartFacts(bosses=shaft_bosses() if bosses is None else bosses)
    return ctx


def found(ctx: sc.Context) -> list[review.Finding]:
    return shop.check(review.ReviewInput([], {}, ctx, ctx.sheet))


# --- an end of a length dimension stands on a line of the part ----------------------------------------------------------------------


def test_the_ends_of_a_shaft_are_edges_and_so_are_its_shoulders() -> None:
    outline = shop.segments_of(shaft_ink())
    assert all(shop.stands_on_edge(x, True, outline) for x in (0.0, 26.0, 41.0, 101.0, 116.0, 168.0))


def test_the_round_end_of_a_keyway_is_an_edge_where_it_turns_back() -> None:
    outline = shop.segments_of(shaft_ink())
    assert shop.stands_on_edge(122.0, True, outline) and shop.stands_on_edge(147.0, True, outline)


def test_a_place_where_the_outline_runs_through_is_no_edge() -> None:
    """The keyway floor ends at 144 and its round goes on: the outline passes through that x, it does not stand there."""
    outline = shop.segments_of(shaft_ink())
    assert not shop.stands_on_edge(144.0, True, outline)
    assert not shop.stands_on_edge(60.0, True, outline)  # the middle of a plain cylinder


def test_an_overall_length_that_stops_short_of_the_part_holds_the_export() -> None:
    """The first native shaft sheet: the 144 and the right-hand 26 ended at 144 mm and the shaft is 168 mm long."""
    ctx = context([length("l168", 0.0, 144.0), length("l50", 116.0, 144.0)])
    out = found(ctx)
    assert [f.code for f in out] == ["dimension_off_part", "dimension_off_part"]
    assert out[0].items == ("l168",) and "144" in out[0].message
    assert "dimension_off_part" not in sck.NON_BLOCKING
    assert ctx.extras["off_part_measured_on"] == {"l168": "sheet", "l50": "sheet"}  # no page was read: the composed sheet


def test_the_right_lengths_raise_nothing() -> None:
    ctx = context([length("l168", 0.0, 168.0), length("l50", 116.0, 168.0), length("l60", 41.0, 101.0)])
    assert found(ctx) == []


def test_a_dimension_in_the_other_direction_reads_the_other_coordinate() -> None:
    ctx = context([("h32", "front", "v", ((60.0, AXIS_Y - 16.0), (60.0, AXIS_Y + 16.0)), (None, None))])
    assert found(ctx) == []
    ctx = context([("h30", "front", "v", ((60.0, AXIS_Y - 15.0), (60.0, AXIS_Y + 15.0)), (None, None))])
    assert [f.code for f in found(ctx)] == ["dimension_off_part"]


def stepped_block(step: float) -> list[Any]:
    """A milled block 90 long seen from the front: 10 high up to x 40, then ``step`` lower. Its step is a face ``step`` mm high."""
    top = 105.0
    return [
        model.Line(0, top, 40, top),
        model.Line(40, top - step, 90, top - step),
        model.Line(40, top - step, 40, top),  # the step, seen edge-on
        model.Line(0, 95.0, 90, 95.0),
        model.Line(0, 95.0, 0, top),
        model.Line(90, 95.0, 90, top - step),
    ]


def test_a_real_shoulder_shorter_than_a_millimetre_on_the_paper_is_an_edge() -> None:
    """A 0,5 mm step (2,5 mm at 1:5): the old check took an edge under 0,8 mm for a point and held a correct dimension."""
    outline = shop.segments_of(stepped_block(0.5))
    assert shop.stands_on_edge(40.0, True, outline)
    assert shop.stands_on_edge(40.0, True, outline, window=(104.2, 105.3))  # within the trace of the step face
    assert shop.stands_on_edge(40.0, True, shop.segments_of(stepped_block(0.05)))  # 0,05 mm is still an edge
    assert not shop.stands_on_edge(41.0, True, outline)  # and a millimetre past it there is none


def test_a_line_at_the_same_coordinate_elsewhere_in_the_view_does_not_carry_the_end() -> None:
    """A dimension to the wrong face used to pass whenever ANY line of the view lay at its coordinate. The line must be where the
    measured face is (its trace across the dimension), or near the dimension's end when the trace is not known."""
    far = [(40.0, 300.0, 40.0, 310.0)]
    assert shop.stands_on_edge(40.0, True, far)  # asked without a window: any line
    assert not shop.stands_on_edge(40.0, True, far, window=(98.0, 102.0))
    ink = [model.Line(0, 100, 90, 100), model.Line(0, 110, 90, 110), model.Line(0, 100, 0, 110), model.Line(90, 100, 90, 110), model.Line(40, 130, 40, 140)]
    view = sc.ViewInfo("front", 0.0, 100.0, 1.0, (0, -1, 0), (1, 0, 0), (0.0, 0.0, 0.0), (0.0, 100.0, 90.0, 140.0))
    ctx = sc.Context(sheet=model.Sheet(420.0, 297.0), views={"front": view}, holes=[], geometry=sc.Geometry(), area=(-30.0, 40.0, 400.0, 282.0), language="en")
    ctx.sheet.entities.extend(ink)
    ctx.extras["face_ends"] = [("d40", "front", "h", ((0.0, 105.0), (40.0, 105.0)), ((100.0, 110.0), (100.0, 110.0)))]
    assert [f.code for f in found(ctx)] == ["dimension_off_part"]  # x = 40 has a line only at y 130..140, outside the face's trace
    ctx.extras["face_ends"] = [("d40", "front", "h", ((0.0, 105.0), (40.0, 135.0)), ((100.0, 110.0), (130.0, 140.0)))]
    assert found(ctx) == []  # the face whose trace is at y 130..140 does stand on it


def test_the_tolerance_follows_the_scale_of_the_view() -> None:
    """0,3 mm on the paper at 1:1 and above; at 1:5 it is 0,06 mm, so that a dimension 1 mm short of a shoulder does not pass."""
    assert shop.end_tolerance(1.0) == shop.END_TOL_MM == shop.end_tolerance(2.0)
    assert abs(shop.end_tolerance(0.2) - 0.06) < 1e-9
    outline = [(40.0, 95.0, 40.0, 105.0)]
    assert shop.stands_on_edge(40.2, True, outline, tol=shop.end_tolerance(1.0))
    assert not shop.stands_on_edge(40.2, True, outline, tol=shop.end_tolerance(0.2))  # 1 mm of the part short of the shoulder


def test_the_page_dimension_is_what_is_judged_when_techdraw_holds_one() -> None:
    """The composed sheet says 0..168 and the TechDraw page holds a dimension from 0 to 144 over TechDraw's own lines of the view: the
    page is what the engineer gets, so the finding stands; and the other way round nothing is found."""
    lines = shop.segments_of(shaft_ink())
    ctx = context([length("l168", 0.0, 168.0)])
    ctx.extras["page_dim_ends"] = [pageread.PageEnds("l168", "View001", True, ((0.0, AXIS_Y), (144.0, AXIS_Y)), 1.0)]
    ctx.extras["page_outlines"] = {"View001": lines}
    out = found(ctx)
    assert [f.code for f in out] == ["dimension_off_part"] and ctx.extras["off_part_measured_on"] == {"l168": "page"}
    ctx = context([length("l168", 0.0, 144.0)])
    ctx.extras["page_dim_ends"] = [pageread.PageEnds("l168", "View001", True, ((0.0, AXIS_Y), (168.0, AXIS_Y)), 1.0)]
    ctx.extras["page_outlines"] = {"View001": lines}
    assert found(ctx) == []


def test_the_ends_of_the_page_dimensions_are_read_with_the_lines_of_their_view() -> None:
    """``getLinearPoints`` answers in the view's page mm with y DOWN (measured on FreeCAD 1.1.3: the keyway depth of the bench shaft
    has its points at y 22,0 / -15,0 and its arrow tips, y up and unscaled, at 7,5 / -11,0 for a view at 2:1)."""
    view = SimpleNamespace(Name="Section", Label="C - C", getScale=lambda: 2.0)
    vec = lambda x, y: SimpleNamespace(x=x, y=y)  # noqa: E731
    depth = SimpleNamespace(
        Type="DistanceY", Label="kwd", CotaforgeItem="kwd", References2D=[(view, ("Vertex9", "Vertex7"))],
        getLinearPoints=lambda: [vec(0.0, 22.0), vec(-6.0, -15.0)], getArrowPositions=lambda: [vec(-15.95, 7.5), vec(-15.95, -11.0)],
    )
    oblique = SimpleNamespace(
        Type="Distance", Label="slanted", CotaforgeItem="s1", References2D=[(view, ("Vertex1", "Vertex2"))],
        getLinearPoints=lambda: [vec(0.0, 0.0), vec(10.0, 10.0)], getArrowPositions=lambda: [vec(0.0, 0.0), vec(5.0, 5.0)],
    )
    page = SimpleNamespace(Views=[depth, oblique, SimpleNamespace(Type="Diameter", getArrowPositions=lambda: [])])
    ends, outlines = pageread.dimension_ends(page, lambda _p, _v: (64.0, 240.5), "CotaforgeItem", lambda _v: [[(-6.0, 15.0), (6.0, 15.0), (6.0, 21.2)]])
    assert [(e.owner, e.view, e.horizontal, e.scale) for e in ends] == [("kwd", "Section", False, 2.0)]
    assert ends[0].points == ((64.0, 240.5 - 22.0), (58.0, 240.5 + 15.0))
    assert {k: [tuple(round(c, 6) for c in s) for s in v] for k, v in outlines.items()} == {"Section": [(58.0, 255.5, 70.0, 255.5), (70.0, 255.5, 70.0, 261.7)]}
    assert shop.stands_on_edge(ends[0].points[1][1], False, outlines["Section"], tol=shop.end_tolerance(2.0))  # the keyway floor


class TracedPlate(PlateGeometry):
    """The test plate with the outline of its faces (what ``LiveGeometry.face_outline`` gives: the view's own coordinates)."""

    def face_outline(self, face_id: str, centre: sc.Vec3, xdir: sc.Vec3, direction: sc.Vec3) -> list[list[sc.Point]]:  # noqa: ARG002
        x = {"face:left": -50.0, "face:right": 50.0}[face_id]
        return [[(x, -4.0), (x, 4.0)]]


def plate_front_ink(ctx: sc.Context) -> None:
    """The 100 x 8 plate in its front view (origin 150; 160, 1:1)."""
    x0, y0, x1, y1 = ctx.views["front"].box
    ctx.sheet.entities.extend([model.Line(x0, y0, x1, y0), model.Line(x0, y1, x1, y1), model.Line(x0, y0, x0, y1), model.Line(x1, y0, x1, y1)])


def test_a_distance_between_two_faces_records_its_ends_and_the_review_finds_them_on_the_part() -> None:
    """The real path: ``items_dims._distance`` leaves ``face_ends`` and ``sheet_shopchecks`` reads it."""
    ctx = make_context()
    plate_front_ink(ctx)
    (result,) = draw(ctx, {"id": "w", "type": "dim", "kind": "distance", "from": {"face": "face:left"}, "to": {"face": "face:right"}, "view": "front"})
    assert result.status == "ok"
    (record,) = ctx.extras["face_ends"]
    assert record[:3] == ("w", "front", "h") and record[3] == ((100.0, 160.0), (200.0, 160.0)) and record[4] == (None, None)
    assert [f.code for f in found(ctx) if f.code == "dimension_off_part"] == []
    # the same dimension over a part that ends 10 mm before
    ctx.sheet.entities[:] = [e for e in ctx.sheet.entities if not (isinstance(e, model.Line) and e.layer == model.CONTOUR)]
    ctx.sheet.entities.extend([model.Line(100, 156, 190, 156), model.Line(100, 164, 190, 164), model.Line(100, 156, 100, 164), model.Line(190, 156, 190, 164)])
    assert [f.items for f in found(ctx) if f.code == "dimension_off_part"] == [("w",)]


def test_the_trace_of_each_measured_face_goes_with_the_record() -> None:
    ctx = make_context()
    ctx.geometry = TracedPlate()
    plate_front_ink(ctx)
    draw(ctx, {"id": "w", "type": "dim", "kind": "distance", "from": {"face": "face:left"}, "to": {"face": "face:right"}, "view": "front"})
    (record,) = ctx.extras["face_ends"]
    assert record[4] == ((156.0, 164.0), (156.0, 164.0))
    assert [f.code for f in found(ctx) if f.code == "dimension_off_part"] == []


def test_a_distance_to_a_hole_centre_is_not_a_face_to_face_length() -> None:
    ctx = make_context()
    draw(ctx, {"type": "dim", "kind": "distance", "from": {"face": "face:left"}, "to": {"at": [90, 10, 4]}})
    assert ctx.extras.get("face_ends", []) == []


def test_the_export_waits_for_it_and_the_agent_cannot_wave_it_away() -> None:
    server = Path(__file__).resolve().parents[4] / "mcp-server" / "src" / "cotaforge_mcp" / "drawing_requirements.py"
    measured = server.read_text(encoding="utf-8").split("MEASURED_FINDINGS = frozenset(")[1].split(")")[0]
    assert '"dimension_off_part"' in measured


# --- one continuous centre line per cylinder ----------------------------------------------------------------------------------------


def axis_line(x0: float, x1: float, over: float = 2.5) -> Any:
    return model.Line(x0 - over, AXIS_Y, x1 + over, AXIS_Y, model.AXES)


def test_a_cylinder_whose_centre_line_is_missing_while_the_axis_has_one_is_found() -> None:
    """The right-hand Ø25 seat (101..116) had no centre line: the line broke over it."""
    ctx = context([], [axis_line(0.0, 41.0), axis_line(41.0, 101.0), axis_line(118.5, 168.0, over=0.0)])
    out = found(ctx)
    assert [f.code for f in out] == ["centre_line_gap", "centre_line_gap"]  # the seat, and the first 2,5 mm of the Ø22 end
    assert "Ø25" in out[0].message and "face:4" in out[0].message and out[0].key == "face:4@front"
    assert "103.5" in out[0].message and "116" in out[0].message
    assert "centre_line_gap" not in sck.NON_BLOCKING


def test_lines_that_meet_end_to_end_are_one_line() -> None:
    ctx = context([], [axis_line(0.0, 41.0, 0.0), axis_line(41.0, 101.0, 0.0), axis_line(101.0, 116.0, 0.0), axis_line(116.0, 168.0, 0.0)])
    assert found(ctx) == []


def test_the_last_cylinder_without_its_line_is_found() -> None:
    """The line stops at the first shoulder and nothing comes after it: no second line to make a 'gap between two lines' with."""
    two = [sck.Boss("face:1", 10.0, (25.0, 0.0, 0.0), (1.0, 0.0, 0.0), 50.0), sck.Boss("face:2", 8.0, (75.0, 0.0, 0.0), (1.0, 0.0, 0.0), 50.0)]
    ctx = context([], [axis_line(0.0, 50.0)], bosses=two)
    out = found(ctx)
    assert [f.key for f in out] == ["face:2@front"] and "52.5" in out[0].message and "100" in out[0].message


def test_a_rectangular_bar_with_a_hole_in_each_end_has_no_cylinder_between_its_two_centre_lines() -> None:
    """Two centre lines on one axis with solid bar between them: the old check took the bar's two edges for a cylinder."""
    ctx = context([], [model.Line(-2.5, AXIS_Y, 22.5, AXIS_Y, model.AXES), model.Line(77.5, AXIS_Y, 102.5, AXIS_Y, model.AXES)], bosses=[])
    assert found(ctx) == []
    bore = [sck.Boss("comp:1/cylinder1", 4.0, (50.0, 0.0, 0.0), (1.0, 0.0, 0.0), 100.0, bore=True)]  # the wall of a hole is not a cylinder to mark here
    ctx = context([], [model.Line(-2.5, AXIS_Y, 22.5, AXIS_Y, model.AXES)], bosses=bore)
    assert found(ctx) == []


def test_a_cylinder_with_no_line_at_all_on_its_axis_is_left_to_cylinder_no_axis() -> None:
    off_axis = [model.Line(0.0, AXIS_Y + 30.0, 168.0, AXIS_Y + 30.0, model.AXES)]
    assert found(context([], off_axis)) == []
    assert found(context([], [])) == []


def test_a_cylinder_seen_along_its_axis_or_in_a_view_a_section_stands_for_is_not_judged() -> None:
    ctx = context([], [axis_line(0.0, 41.0)])
    ctx.views["right"] = sc.ViewInfo("right", 300.0, AXIS_Y, 1.0, (1, 0, 0), (0, 1, 0), (0.0, 0.0, 0.0), (284.0, AXIS_Y - 16.0, 316.0, AXIS_Y + 16.0))
    assert {f.key.split("@")[1] for f in found(ctx)} == {"front"}
    ctx.extras["in_place_views"] = {"A-A": "front"}  # the front view is not drawn: its section is, and nothing recorded says it has a plane here
    assert found(ctx) == []


def test_the_uncovered_stretches_of_an_axis() -> None:
    gaps, on_axis = shop.axis_gaps((0.0, 0.0), (100.0, 0.0), [(-2.5, 0.0, 30.0, 0.0), (60.0, 0.0, 99.8, 0.0), (0.0, 5.0, 100.0, 5.0)])
    assert gaps == [(30.0, 60.0)] and on_axis  # 0,2 mm short of the end is no gap; the line 5 mm off is another axis
    assert shop.axis_gaps((0.0, 0.0), (0.0, 50.0), [(0.0, -2.5, 0.0, 52.5)]) == ([], True)  # a vertical axis
    assert shop.axis_gaps((0.0, 0.0), (100.0, 0.0), []) == ([(0.0, 100.0)], False)


# --- the two seats of one diameter are two cylinders ---------------------------------------------------------------------------------


def test_the_halves_of_one_cylinder_are_one_cylinder() -> None:
    assert flats.separate_cylinders([("face:1", 0.0, 26.0), ("face:2", 0.0, 26.0)]) == [["face:1", "face:2"]]


def test_two_seats_of_one_diameter_and_axis_are_two_cylinders() -> None:
    seats = flats.separate_cylinders([("face:5", 26.0, 41.0), ("face:9", 101.0, 116.0)])
    assert seats == [["face:5"], ["face:9"]]


def test_the_seats_stay_in_the_order_of_the_axis_whatever_the_order_given() -> None:
    assert flats.separate_cylinders([("b", 101.0, 116.0), ("a", 26.0, 41.0), ("c", 26.0, 41.0)]) == [["a", "c"], ["b"]]


def test_the_two_seats_of_a_shaft_come_out_as_two_whole_cylinders_each_with_its_own_stretch() -> None:
    """What ``sheet_facts.collect`` makes its bosses from: the faces that share the axis and the Ø25 of the bench shaft (each seat a
    cylinder of two halves) are two cylinders, 26..41 and 101..116; grouped by axis and radius they were one, in the middle of
    which nothing stands."""
    import math  # noqa: PLC0415

    half, full = math.pi, 2.0 * math.pi - 0.02
    members = [("face:5", 26.0, 41.0, half), ("face:9", 101.0, 116.0, half), ("face:6", 26.0, 41.0, half), ("face:10", 101.0, 116.0, half)]
    assert flats.whole_cylinders(members, full) == [("face:5", 26.0, 41.0), ("face:9", 101.0, 116.0)]
    keyed = [("face:12", 116.0, 168.0, half), ("face:13", 116.0, 168.0, half * 0.8), ("face:14", 116.0, 125.0, half * 0.2)]  # a keyway split its faces
    assert flats.whole_cylinders(keyed, full) == [("face:14", 116.0, 168.0)]  # named by its first face along the axis
    assert flats.whole_cylinders([("face:20", 0.0, 8.0, math.pi / 2)], full) == []  # a fillet: not all the way round


# --- the solid parts of an assembly are not cut by its section (owner's rule, ISO 128-34) ----------------------------------------------


def test_the_solid_parts_a_plane_would_cut_lengthwise_are_named_by_their_designation() -> None:
    import sheet_solid as solid  # noqa: PLC0415

    kinds = {
        "Eje de transmisión": "shaft",
        "screw ISO 4762 M8x35": "screw",
        "Tornillos": "screw",
        "Chaveta DIN 6885": "key",
        "Tuerca M8": "nut",
        "Arandela": "washer",
        "dowel pin": "pin",
        "Remache": "rivet",
        "steel ball": "ball",
        "Nervio": "rib",
    }
    assert {name: solid.solid_kind(name) for name in kinds} == kinds


def test_a_housing_a_cover_a_hub_and_a_bearing_are_cut() -> None:
    import sheet_solid as solid  # noqa: PLC0415

    for name in ("Carcasa", "Tapa con brida", "Cubo", "Rodamiento 6205", "Casquillo separador", "housing"):
        assert solid.solid_kind(name) is None


def test_a_part_of_revolution_with_nothing_inside_is_a_solid_part_whatever_it_is_called() -> None:
    import sheet_solid as solid  # noqa: PLC0415

    assert solid.solid_kind("Pieza 3", round_solid=True) == "shaft"
    assert solid.solid_kind("Pieza 3") is None


def assembly_context(cut: list[tuple[str, str, bool]]) -> sc.Context:
    ctx = context([])
    ctx.extras["section_cuts"] = {"B-B": cut}
    return ctx


def test_a_section_that_cut_a_solid_part_is_a_notice_that_never_holds_the_export() -> None:
    """What is recorded is WHICH parts the plane cut, not whether lengthwise (a plane square to a shaft cuts it rightly): the
    finding is listed and cannot hold anything until the direction of the cut is measured."""
    ctx = assembly_context([("comp:1", "Carcasa", False), ("comp:2", "Eje de transmisión", True), ("comp:3", "screw M8", False)])
    out = found(ctx)
    assert [f.code for f in out] == ["assembly_solid_cut"] and out[0].key == "B-B"
    assert "Eje de transmisión (shaft)" in out[0].message and "screw M8 (screw)" in out[0].message and "Carcasa" not in out[0].message
    assert "assembly_solid_cut" in sck.NON_BLOCKING


def test_a_section_that_cut_only_the_housing_the_cover_and_the_hub_is_clean() -> None:
    assert found(assembly_context([("comp:1", "Carcasa", False), ("comp:4", "Tapa", False), ("comp:5", "Cubo", False)])) == []


# --- the plain side view of a turned part with an axial section ---------------------------------------------------------------------------


class Flange(sc.Geometry):
    def round_extent(self) -> tuple[sc.Vec3, float]:
        return (1.0, 0.0, 0.0), 100.0


def flange_context(holes: list[sc.HoleInfo] | None = None, keyways: int = 0) -> sc.Context:
    """The cover: axis along x; the face view looks along x (the 'right' view), the plain side view along y (the 'front' view) and the
    section A-A cuts by the plane through the axis, looking the way the front view looks."""
    face = sc.ViewInfo("right", 0.0, 0.0, 1.0, (1, 0, 0), (0, 1, 0), (0, 0, 0), (-50.0, -50.0, 50.0, 50.0))
    side = sc.ViewInfo("front", 100.0, 0.0, 1.0, (0, -1, 0), (1, 0, 0), (0, 0, 0), (90.0, -50.0, 110.0, 50.0))
    ctx = sc.Context(sheet=model.Sheet(420.0, 297.0), views={"right": face, "front": side}, holes=holes or [], geometry=Flange(), area=(-30.0, 40.0, 400.0, 282.0), language="en")
    ctx.extras["section_planes"] = {"A-A": ((0.0, 0.0, 0.0), (0.0, 1.0, 0.0))}
    ctx.extras["facts"] = sck.PartFacts(keyways=keyways)
    return ctx


def test_the_plain_side_view_of_a_turned_part_with_an_axial_section_is_a_notice() -> None:
    hole = sc.HoleInfo(0, (0.0, 28.0, 28.0), (1.0, 0.0, 0.0), 9.0, 10.0, True)  # a hole along the axis: the face view shows it, the side view does not
    out = found(flange_context([hole]))
    assert [f.code for f in out] == ["side_view_repeats_section"]
    assert "front" in out[0].message and "A-A" in out[0].message
    assert "side_view_repeats_section" in sck.NON_BLOCKING  # a suggestion, like view_redundant: it never holds an export


def test_a_cross_hole_or_a_keyway_is_seen_from_the_side_so_the_side_view_stays() -> None:
    cross = sc.HoleInfo(0, (0.0, 0.0, 10.0), (0.0, 0.0, 1.0), 6.0, 20.0, True)
    assert found(flange_context([cross])) == []
    assert found(flange_context(keyways=1)) == []


def test_a_section_drawn_in_place_of_the_side_view_is_not_a_side_view_that_repeats_it() -> None:
    """The turned part with ONE axial section standing in its front view (``in_place``): the front view stays in the context (the
    items measure in it) and is not drawn. Nothing repeats anything: the old check told the agent to drop the only view."""
    ctx = flange_context()
    front = ctx.views["front"]
    ctx.extras["sections"] = {"A-A": sc.ViewInfo("A-A", front.x, front.y, 1.0, front.direction, front.xdir, (0, 0, 0), front.box, derived=True)}
    ctx.extras["in_place_views"] = {"A-A": "front"}
    assert found(ctx) == []
    ctx.extras["section_planes"]["B-B"] = ((0.0, 0.0, 0.0), (0.0, 1.0, 0.0))  # a second, removed, axial section beside it: still not the view
    assert found(ctx) == []


def test_a_slot_off_the_axis_or_a_radial_boss_is_seen_from_the_side_so_the_side_view_stays() -> None:
    import sheet_keyway as keyway  # noqa: PLC0415

    ctx = flange_context()
    ctx.extras["facts"].planes = [keyway.Flat((1.0, 0.0, 0.0), (0.0, 0.0, 0.0), "face:1"), keyway.Flat((0.0, 0.0, 1.0), (10.0, 30.0, 20.0), "face:7")]  # an end face; the wall of a milled slot
    assert found(ctx) == [] and shop.side_features(ctx, (1.0, 0.0, 0.0)) == "milled face"
    ctx = flange_context()
    ctx.extras["facts"].planes = [keyway.Flat((1.0, 0.0, 0.0), (0.0, 0.0, 0.0), "face:1"), keyway.Flat((-1.0, 0.0, 0.0), (20.0, 0.0, 0.0), "face:2")]
    ctx.extras["facts"].cylinders = [keyway.Cylinder((1.0, 0.0, 0.0), (0.0, 0.0, 0.0), 50.0, False, 0.0, 20.0)]
    assert [f.code for f in found(ctx)] == ["side_view_repeats_section"]  # only faces of revolution and planes square to the axis
    ctx.extras["facts"].cylinders.append(keyway.Cylinder((0.0, 0.0, 1.0), (10.0, 0.0, 0.0), 6.0, False, 40.0, 60.0))  # a radial boss
    assert found(ctx) == [] and shop.side_features(ctx, (1.0, 0.0, 0.0)) == "cylinder across the axis"


# --- a label with no side asked takes the other silhouette of a turned surface when its own has no room ------------------------------------


def _label(anchor: sc.Point, direction: tuple[float, float]) -> tuple[list[Any], tuple[float, float, float, float]]:  # noqa: ARG001
    return [model.Text(anchor[0], anchor[1], "Ra 0,8", 3.5, model.DIMENSIONS, "middle")], (anchor[0] - 6.0, anchor[1] - 2.5, anchor[0] + 6.0, anchor[1] + 2.5)


def _seat(ctx: sc.Context) -> attach.Attach:
    """A turned surface in the front view of the test context: its lower silhouette line, and the upper one as the other."""
    view = ctx.views["front"]
    near = attach.Attach(view, (150.0, 156.0), (0.0, -1.0), "surface")
    near.other = attach.Attach(view, (150.0, 164.0), (0.0, 1.0), "surface")
    return near


def test_a_label_with_no_side_asked_stands_on_the_other_silhouette_when_its_own_side_is_taken() -> None:
    """The Ra 0,8 of the first native shaft stood on the '15' under the seat. Under the view everything is taken (the stack of
    lengths): the label goes above, where nothing is, instead of landing on a dimension."""
    ctx = make_context()
    ctx.reserve((90.0, 70.0, 210.0, 156.0))  # the stack of length dimensions under the view
    att = _seat(ctx)
    entities, free = attach.place_label(ctx, att, _label)
    assert free and entities[0].y > 164.0 and att.tip == (150.0, 164.0)


def test_a_label_whose_own_side_has_room_stays_there() -> None:
    ctx = make_context()
    att = _seat(ctx)
    entities, free = attach.place_label(ctx, att, _label)
    assert free and entities[0].y < 156.0 and att.tip == (150.0, 156.0)


def test_a_label_that_asked_for_its_side_is_tried_once_there_and_the_overlap_is_left_to_the_review() -> None:
    """``side`` or ``offset_mm`` is the agent's word: one try, exactly there. It is NOT moved to the other silhouette; the review
    reports the collision (see the next test) and the agent moves it."""
    ctx = make_context()
    ctx.reserve((90.0, 70.0, 210.0, 156.0))
    att = _seat(ctx)
    entities, free = attach.place_label(ctx, att, _label, explicit=True)
    assert not free and entities[0].y < 156.0 and att.tip == (150.0, 156.0)


# --- a finish symbol on the text of a dimension is in the measured overlap list; a section's name is written once -------------------------


def test_a_finish_symbol_over_the_text_of_a_dimension_is_a_measured_collision() -> None:
    """The first native shaft sheet had its Ra 0,8 symbol standing on the '15' of the length dimension under the seat."""
    import items_finish as finish  # noqa: PLC0415
    import sheet_collide as collide  # noqa: PLC0415

    dimension = [model.Text(100.0, 50.0, "15", 3.5, model.DIMENSIONS, "middle")]
    symbol, _, _ = finish.symbol((98.0, 48.0), "required", "Ra 0,8")
    found_collisions = collide.collisions([collide.ink_of("l15a", dimension), collide.ink_of("f1", symbol)])
    assert {(c.a, c.b) for c in found_collisions} | {(c.b, c.a) for c in found_collisions} >= {("l15a", "f1")}


def test_a_symbol_clear_of_the_dimension_text_is_not_one() -> None:
    import items_finish as finish  # noqa: PLC0415
    import sheet_collide as collide  # noqa: PLC0415

    dimension = [model.Text(100.0, 50.0, "15", 3.5, model.DIMENSIONS, "middle")]
    symbol, _, _ = finish.symbol((98.0, 70.0), "required", "Ra 0,8")
    assert collide.collisions([collide.ink_of("l15a", dimension), collide.ink_of("f1", symbol)]) == []


def test_the_caption_of_a_section_spaces_its_hyphen_because_osifont_draws_the_bare_one_over_the_c() -> None:
    import sheet_units as units  # noqa: PLC0415

    assert units.caption_of("section", "C-C") == "C - C" and units.caption_of("section", "A-A") == "A - A"
    assert units.caption_of("detail", "D") == "D"
    assert units.tight_section_caption("section", "C-C", "C-C")
    assert not units.tight_section_caption("section", "C-C", "C - C")
    assert not units.tight_section_caption("detail", "D", "D")
