"""The closed forms of sheet metal and the record of what is exactly known about a sheet (no CAD involved)."""

import math

import pytest

from cotaforge.contract import PlaneName, Profile, frame_for_plane
from cotaforge.verification import formulas, sheetmetal

FRAME = frame_for_plane(PlaneName.XY)


def rect(u0: float, v0: float, u1: float, v1: float) -> Profile:
    return Profile(
        kind="rect",
        area_mm2=(u1 - u0) * (v1 - v0),
        centroid=((u0 + u1) / 2, (v0 + v1) / 2),
        bbox_uv=((u0, v0), (u1, v1)),
    )


def sheet(*holes: Profile) -> sheetmetal.Sheet:
    return sheetmetal.new_sheet(FRAME, [rect(0, 0, 100, 60), *holes], 2.0, 1.0)


def add(record: sheetmetal.Sheet, y: float, **extra: object) -> float | None:
    """A flange on the long side at height y (an edge along u on the top surface z=2)."""
    options = {
        "angle_deg": 90.0,
        "radius": 3.0,
        "length": 20.0,
        "material_inside": True,
        "from_outer_sharp": True,
    } | extra
    return sheetmetal.add_flange(record, (0.0, y, 2.0), (100.0, y, 2.0), **options)  # type: ignore[arg-type]


# --- the forms ------------------------------------------------------------------------------------------------------


def test_bend_allowance_is_the_arc_of_the_neutral_line() -> None:
    assert formulas.bend_allowance(90, 2, 3, 0.4) == pytest.approx(math.pi / 2 * 3.8)
    assert formulas.bend_allowance(0.0001, 2, 3, 0.4) < 1e-3


def test_the_outside_setback_is_r_plus_t_times_the_half_angle_tangent() -> None:
    assert formulas.outside_setback(90, 2, 3) == pytest.approx(5.0)
    assert formulas.outside_setback(60, 2, 3) == pytest.approx(5 * math.tan(math.radians(30)))


def test_the_usual_bend_deduction_rule_holds_for_two_ninety_degree_legs() -> None:
    """Flat = D1 + D2 - (2 * setback - BA), with D1 and D2 the OUTER dimensions of the two legs."""
    gain = formulas.edge_flange_flat_gain(90, 2, 3, 0.4, 20, material_inside=True, from_outer_sharp=True)
    outer_sheet, outer_leg = 60.0, 20.0
    deduction = 2 * formulas.outside_setback(90, 2, 3) - formulas.bend_allowance(90, 2, 3, 0.4)
    assert outer_sheet + gain == pytest.approx(outer_sheet + outer_leg - deduction)


@pytest.mark.parametrize(
    ("inside", "outer", "expected"),
    [(True, True, 75.969026), (False, True, 80.969026), (True, False, 80.969026), (False, False, 85.969026)],
)
def test_the_flat_gain_matches_values_measured_on_the_sheetmetal_addon(
    inside: bool, outer: bool, expected: float
) -> None:
    """60 mm sheet, 2 mm thick, r=3, K=0.4, 20 mm flange at 90 degrees: the flat lengths FreeCAD's add-on gave."""
    gain = formulas.edge_flange_flat_gain(90, 2, 3, 0.4, 20, material_inside=inside, from_outer_sharp=outer)
    assert 60 + gain == pytest.approx(expected, abs=1e-4)


def test_the_bend_inside_the_footprint_takes_r_plus_t_whatever_the_angle() -> None:
    inside = formulas.edge_flange_flat_gain(120, 2, 3, 0.4, 20, material_inside=True, from_outer_sharp=True)
    outside = formulas.edge_flange_flat_gain(120, 2, 3, 0.4, 20, material_inside=False, from_outer_sharp=True)
    assert outside - inside == pytest.approx(5.0)  # r + t, not the setback (8.66 at 120 degrees)


# --- the record -----------------------------------------------------------------------------------------------------


def test_a_flange_on_a_whole_side_has_an_exact_volume() -> None:
    record = sheet()
    volume = add(record, 0.0)
    assert volume == pytest.approx(100 * 2 * (math.pi / 2 * 4 + 15 - 5))
    assert record["exact"] and record["flanges"][0]["axis"] == "v" and record["flanges"][0]["side"] == 0


def test_the_flat_size_is_the_sheet_plus_the_gain_of_each_flange_longer_side_first() -> None:
    record = sheet()
    add(record, 0.0)
    add(record, 60.0)
    gain = formulas.edge_flange_flat_gain(90, 2, 3, 0.4, 20, material_inside=True, from_outer_sharp=True)
    assert sheetmetal.flat_size(record, 0.4) == pytest.approx((100, 60 + 2 * gain))  # 91.9 < 100: the 100 side first
    assert sheetmetal.flat_size(sheet(), 0.4) == pytest.approx((100, 60))


def test_a_flange_that_is_not_a_whole_side_makes_the_sheet_inexact() -> None:
    record = sheet()
    assert sheetmetal.add_flange(
        record, (0.0, 0.0, 2.0), (50.0, 0.0, 2.0), angle_deg=90, radius=3, length=20, material_inside=True,
        from_outer_sharp=True,
    ) is None  # fmt: skip
    assert not record["exact"] and sheetmetal.flat_size(record, 0.4) is None


def test_two_flanges_on_the_same_side_make_the_sheet_inexact() -> None:
    record = sheet()
    add(record, 0.0)
    assert add(record, 0.0) is None and sheetmetal.flat_size(record, 0.4) is None


def test_a_neighbour_on_a_perpendicular_side_keeps_the_flat_size_but_not_the_volume() -> None:
    record = sheet()
    add(record, 0.0, material_inside=False)
    # the second flange stands on the 60 mm side at x=0, which an outside bend left whole
    volume = sheetmetal.add_flange(
        record, (0.0, 0.0, 2.0), (0.0, 60.0, 2.0), angle_deg=90, radius=3, length=15, material_inside=False,
        from_outer_sharp=True,
    )  # fmt: skip
    assert volume is None and record["exact"]
    assert sheetmetal.flat_size(record, 0.4) is not None


def test_a_hole_inside_the_strip_of_a_bend_inside_the_sheet_clears_exactness() -> None:
    record = sheet(rect(40, 2, 50, 8))  # its low edge is 2 mm from the side: inside the 5 mm strip
    assert add(record, 0.0) is None and not record["exact"]


def test_a_hole_clear_of_the_strip_changes_nothing() -> None:
    record = sheet(rect(40, 20, 50, 30))
    assert add(record, 0.0) == pytest.approx(100 * 2 * (math.pi / 2 * 4 + 15 - 5))
    assert record["exact"]


def test_a_flange_with_no_straight_leg_left_is_an_error_with_the_fix() -> None:
    with pytest.raises(sheetmetal.SheetMetalError, match="length_from=leg"):
        add(sheet(), 0.0, length=4.0)


def test_bends_that_eat_the_whole_sheet_are_an_error() -> None:
    with pytest.raises(sheetmetal.SheetMetalError, match="material_outside"):
        add(sheet(), 0.0, radius=70.0, from_outer_sharp=False)


def test_the_blank_area_is_the_outline_minus_the_holes() -> None:
    profiles = [rect(0, 0, 100, 60), rect(10, 10, 20, 20), rect(30, 10, 40, 20)]
    assert sheetmetal.blank_area(profiles) == pytest.approx(6000 - 200)
    assert sheetmetal.outline_of(profiles).bbox_uv == ((0, 0), (100, 60))
