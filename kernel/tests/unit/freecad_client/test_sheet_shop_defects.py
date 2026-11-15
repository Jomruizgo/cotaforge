# ruff: noqa: E501
"""What a shop reader found wrong on the first native sheets, as review checks (no FreeCAD): a length dimension whose end
stands on no line of the part, a centre line that breaks where a cylinder stands, and the two seats of one diameter that
were taken for one cylinder."""

import sys
from pathlib import Path
from typing import Any

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_centre as centre  # noqa: E402
import sheet_context as sc  # noqa: E402
import sheet_flats as flats  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_review as review  # noqa: E402
import sheet_shopchecks as shop  # noqa: E402

AXIS_Y = 150.0
# a shaft along x (page mm, 1:1, axis at y 150): Ø20 0..26, Ø25 26..41, Ø32 41..101, Ø25 101..116, Ø22 116..168 with a keyway on top
# whose floor runs from x 125 to 144 and whose ends are half circles (122..125 and 144..147)
STEPS = ((0.0, 26.0, 10.0), (26.0, 41.0, 12.5), (41.0, 101.0, 16.0), (101.0, 116.0, 12.5), (116.0, 168.0, 11.0))


def shaft_ink() -> list[Any]:
    out: list[Any] = []
    for lo, hi, r in STEPS:
        out += [model.Line(lo, AXIS_Y + r, hi, AXIS_Y + r), model.Line(lo, AXIS_Y - r, hi, AXIS_Y - r)]
    for x, r0, r1 in ((26.0, 10.0, 12.5), (41.0, 12.5, 16.0), (101.0, 16.0, 12.5), (116.0, 12.5, 11.0)):  # the shoulders
        out += [model.Line(x, AXIS_Y + min(r0, r1), x, AXIS_Y + max(r0, r1)), model.Line(x, AXIS_Y - max(r0, r1), x, AXIS_Y - min(r0, r1))]
    out += [model.Line(0.0, AXIS_Y - 10.0, 0.0, AXIS_Y + 10.0), model.Line(168.0, AXIS_Y - 11.0, 168.0, AXIS_Y + 11.0)]  # the end faces
    out += [model.Line(125.0, AXIS_Y + 9.25, 144.0, AXIS_Y + 9.25)]  # the keyway floor
    out += [model.Polyline(((144.0, AXIS_Y + 9.25), (145.5, AXIS_Y + 10.5), (146.5, AXIS_Y + 10.9), (147.0, AXIS_Y + 11.0)))]  # its end, a round going up
    out += [model.Polyline(((125.0, AXIS_Y + 9.25), (123.5, AXIS_Y + 10.5), (122.5, AXIS_Y + 10.9), (122.0, AXIS_Y + 11.0)))]
    return out


def context(ends: list[tuple[str, str, str, tuple[float, float]]], lines: list[Any] | None = None) -> sc.Context:
    front = sc.ViewInfo("front", 0.0, AXIS_Y, 1.0, (0, -1, 0), (1, 0, 0), (0.0, 0.0, 0.0), (0.0, AXIS_Y - 16.0, 168.0, AXIS_Y + 16.0))

    class Nothing(sc.Geometry):
        pass

    ctx = sc.Context(sheet=model.Sheet(420.0, 297.0), views={"front": front}, holes=[], geometry=Nothing(), area=(-30.0, 40.0, 400.0, 282.0), language="en")
    ctx.sheet.entities.extend(shaft_ink())
    ctx.extras["face_ends"] = ends
    if lines is not None:
        ctx.extras["centre_lines"] = lines
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


def test_an_overall_length_that_stops_short_of_the_part_is_a_blocking_finding() -> None:
    """The first native shaft sheet: the 144 and the right-hand 26 ended at 144 mm and the shaft is 168 mm long."""
    ctx = context([("l168", "front", "h", (0.0, 144.0)), ("l50", "front", "h", (116.0, 144.0))])
    out = found(ctx)
    assert [f.code for f in out] == ["dimension_off_part", "dimension_off_part"]
    assert out[0].items == ("l168",) and "144" in out[0].message
    from sheet_checks import NON_BLOCKING  # noqa: PLC0415

    assert "dimension_off_part" not in NON_BLOCKING


def test_the_right_lengths_raise_nothing() -> None:
    ctx = context([("l168", "front", "h", (0.0, 168.0)), ("l50", "front", "h", (116.0, 168.0)), ("l60", "front", "h", (41.0, 101.0))])
    assert found(ctx) == []


def test_a_dimension_in_the_other_direction_reads_the_other_coordinate() -> None:
    ctx = context([("h32", "front", "v", (AXIS_Y - 16.0, AXIS_Y + 16.0))])
    assert found(ctx) == []
    ctx = context([("h30", "front", "v", (AXIS_Y - 15.0, AXIS_Y + 15.0))])
    assert [f.code for f in found(ctx)] == ["dimension_off_part"]


# --- one continuous centre line per axis --------------------------------------------------------------------------------------------


def axis_line(x0: float, x1: float, view: str = "front") -> Any:
    class Rec:
        pass

    rec = Rec()
    rec.view = view  # type: ignore[attr-defined]
    rec.line = model.Line(x0 - 2.5, AXIS_Y, x1 + 2.5, AXIS_Y, model.AXES)  # type: ignore[attr-defined]
    return rec


def test_a_gap_between_two_centre_lines_where_a_cylinder_stands_is_found() -> None:
    """The right-hand Ø25 seat (101..116) had no centre line: the line broke over it."""
    ctx = context([], [axis_line(0.0, 41.0), axis_line(41.0, 101.0), axis_line(118.0, 168.0)])
    out = found(ctx)
    assert [f.code for f in out] == ["centre_line_gap"]
    assert "101" in out[0].message or "103" in out[0].message


def test_lines_that_meet_end_to_end_are_one_line() -> None:
    ctx = context([], [axis_line(0.0, 41.0), axis_line(41.0, 101.0), axis_line(101.0, 116.0), axis_line(116.0, 168.0)])
    assert found(ctx) == []


def test_a_gap_where_the_part_has_no_cylinder_is_not_a_finding() -> None:
    gap = centre.CentreLine  # noqa: F841 - the module is the one under test elsewhere
    lines = [("front", 0.0, AXIS_Y, 30.0, AXIS_Y), ("front", 60.0, AXIS_Y, 90.0, AXIS_Y)]
    assert shop.centre_gaps(lines, {"front": []}) == []


# --- the two seats of one diameter are two cylinders ---------------------------------------------------------------------------------


def test_the_halves_of_one_cylinder_are_one_cylinder() -> None:
    assert flats.separate_cylinders([("face:1", 0.0, 26.0), ("face:2", 0.0, 26.0)]) == [["face:1", "face:2"]]


def test_two_seats_of_one_diameter_and_axis_are_two_cylinders() -> None:
    seats = flats.separate_cylinders([("face:5", 26.0, 41.0), ("face:9", 101.0, 116.0)])
    assert seats == [["face:5"], ["face:9"]]


def test_the_seats_stay_in_the_order_of_the_axis_whatever_the_order_given() -> None:
    assert flats.separate_cylinders([("b", 101.0, 116.0), ("a", 26.0, 41.0), ("c", 26.0, 41.0)]) == [["a", "c"], ["b"]]


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


def test_a_section_that_cut_a_shaft_lengthwise_is_a_blocking_finding() -> None:
    ctx = assembly_context([("comp:1", "Carcasa", False), ("comp:2", "Eje de transmisión", True), ("comp:3", "screw M8", False)])
    out = found(ctx)
    assert [f.code for f in out] == ["assembly_solid_cut"] and out[0].key == "B-B"
    assert "Eje de transmisión (shaft)" in out[0].message and "screw M8 (screw)" in out[0].message and "Carcasa" not in out[0].message
    from sheet_checks import NON_BLOCKING  # noqa: PLC0415

    assert "assembly_solid_cut" not in NON_BLOCKING


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
    ctx.extras["facts"] = type("Facts", (), {"keyways": keyways, "flats": []})()
    return ctx


def test_the_plain_side_view_of_a_turned_part_with_an_axial_section_is_a_blocking_finding() -> None:
    hole = sc.HoleInfo(0, (0.0, 28.0, 28.0), (1.0, 0.0, 0.0), 9.0, 10.0, True)  # a hole along the axis: the face view shows it, the side view does not
    out = found(flange_context([hole]))
    assert [f.code for f in out] == ["side_view_repeats_section"]
    assert "front" in out[0].message and "A-A" in out[0].message
    from sheet_checks import NON_BLOCKING  # noqa: PLC0415

    assert "side_view_repeats_section" not in NON_BLOCKING


def test_a_cross_hole_or_a_keyway_is_seen_from_the_side_so_the_side_view_stays() -> None:
    cross = sc.HoleInfo(0, (0.0, 0.0, 10.0), (0.0, 0.0, 1.0), 6.0, 20.0, True)
    assert found(flange_context([cross])) == []
    assert found(flange_context(keyways=1)) == []


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
