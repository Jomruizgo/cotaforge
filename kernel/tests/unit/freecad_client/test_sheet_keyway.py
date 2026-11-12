"""Keyways read from the shape (``worker/sheet_keyway.py``): a slot with parallel flat walls along the axis of a cylindrical
face, in a shaft or in a hub; no FreeCAD."""

import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_checks as checks  # noqa: E402
import sheet_keyway as kw  # noqa: E402

Z = (0.0, 0.0, 1.0)
SHAFT = kw.Cylinder(Z, (0.0, 0.0, 0.0), 15.0, False, 0.0, 80.0)  # Ø30 x 80 along z
HUB = kw.Cylinder(Z, (0.0, 0.0, 0.0), 15.0, True, 0.0, 40.0)  # a bore of Ø30


def walls(width: float, radial: float, z: float = 40.0, shift: float = 0.0) -> list[kw.Flat]:
    """Two walls ``width`` apart, centred on the axis (``shift`` moves them off it), at ``radial`` mm from the axis."""
    half = width / 2
    return [kw.Flat((1.0, 0.0, 0.0), (half + shift, radial, z)), kw.Flat((-1.0, 0.0, 0.0), (-half + shift, radial, z))]


def test_a_slot_cut_into_a_shaft_is_a_keyway() -> None:
    assert kw.count_keyways([SHAFT], walls(6.0, 13.0)) == 1


def test_a_slot_in_the_bore_of_a_hub_is_a_keyway() -> None:
    assert kw.count_keyways([HUB], walls(6.0, 16.5, z=20.0)) == 1


def test_a_shaft_and_the_hub_on_it_count_the_one_keyway_once() -> None:
    flats = [*walls(6.0, 14.9, z=30.0)]
    assert kw.count_keyways([SHAFT, kw.Cylinder(Z, (0.0, 0.0, 0.0), 15.0, True, 0.0, 80.0)], flats) == 1


def test_a_slot_a_little_off_the_axis_is_still_a_keyway() -> None:
    assert kw.count_keyways([SHAFT], walls(4.0, 12.0, shift=-0.4)) == 1  # the sketch was placed from a face centre


def test_two_keyways_on_two_sides_are_two() -> None:
    other = [kw.Flat((1.0, 0.0, 0.0), (3.0, -13.0, 40.0)), kw.Flat((-1.0, 0.0, 0.0), (-3.0, -13.0, 40.0))]
    assert kw.count_keyways([SHAFT], [*walls(6.0, 13.0), *other]) == 2


def test_what_is_not_a_slot_around_an_axis_is_not_a_keyway() -> None:
    assert kw.count_keyways([SHAFT], walls(6.0, 13.0)[:1]) == 0  # one wall: a flat
    assert kw.count_keyways([SHAFT], walls(6.0, 13.0, shift=5.0)) == 0  # not centred on the axis
    assert kw.count_keyways([SHAFT], walls(30.0, 13.0)) == 0  # as wide as the shaft: its sides
    assert kw.count_keyways([SHAFT], walls(6.0, 13.0, z=200.0)) == 0  # not along the cylinder
    assert kw.count_keyways([SHAFT], walls(6.0, 40.0)) == 0  # far from the cylinder
    slanted = [kw.Flat((0.0, 0.0, 1.0), (3.0, 13.0, 40.0)), kw.Flat((0.0, 0.0, -1.0), (-3.0, 13.0, 40.0))]
    assert kw.count_keyways([SHAFT], slanted) == 0  # the walls are not parallel to the axis
    assert kw.count_keyways([], walls(6.0, 13.0)) == 0


def test_the_walls_of_a_plate_around_a_small_hole_are_not_a_keyway() -> None:
    hole = kw.Cylinder(Z, (0.0, 0.0, 0.0), 3.3, True, 0.0, 8.0)
    sides = [kw.Flat((1.0, 0.0, 0.0), (50.0, 0.0, 4.0)), kw.Flat((-1.0, 0.0, 0.0), (-50.0, 0.0, 4.0))]
    assert kw.count_keyways([hole], sides) == 0


def test_the_part_facts_carry_what_the_shape_says() -> None:
    assert checks.PartFacts(keyways=1, thread_features=1).keyways == 1


# --- the dimensions of a keyway ------------------------------------------------------------------------------------------------


def keyed_shaft(closed: bool = True) -> tuple[list[kw.Cylinder], list[kw.Flat]]:
    """A Ø30 x 80 shaft along z with a keyway 6 wide on its +y side: walls at x = +-3, bottom at y = 10 (depth 5), running
    z = 20 to 50 (closed) or z = 50 to 80 (out through the end)."""
    lo, hi = (20.0, 50.0) if closed else (50.0, 80.0)
    mid = (lo + hi) / 2
    flats = [
        kw.Flat((1.0, 0.0, 0.0), (3.0, 12.5, mid), "face:a"),
        kw.Flat((-1.0, 0.0, 0.0), (-3.0, 12.5, mid), "face:b"),
        kw.Flat((0.0, 1.0, 0.0), (0.0, 10.0, mid), "face:bottom"),
        kw.Flat((0.0, 0.0, -1.0), (0.0, 0.0, 0.0), "face:cap0"),
        kw.Flat((0.0, 0.0, 1.0), (0.0, 0.0, 80.0), "face:cap1"),
        kw.Flat((0.0, 0.0, 1.0), (0.0, 12.5, hi if closed else lo), "face:end_hi" if closed else "face:end_lo"),
    ]
    if closed:
        flats.append(kw.Flat((0.0, 0.0, -1.0), (0.0, 12.5, lo), "face:end_lo"))
    return [SHAFT], flats


def by_id(specs: list[dict]) -> dict[str, dict]:
    return {str(spec["id"]): spec for spec in specs}


def test_the_slot_knows_its_bottom_its_ends_and_the_caps_of_the_shaft() -> None:
    cylinders, flats = keyed_shaft()
    (slot,) = kw.find_slots(cylinders, flats)
    assert slot.bottom is not None and slot.bottom.face_id == "face:bottom" and slot.reach == 10.0
    assert sorted(f.face_id for f in slot.ends) == ["face:end_hi", "face:end_lo"]
    assert sorted(f.face_id for f in slot.caps) == ["face:cap0", "face:cap1"]
    assert slot.up == (0.0, 1.0, 0.0) and slot.width == 6.0


def test_a_closed_keyway_is_dimensioned_by_width_depth_length_and_position() -> None:
    cylinders, flats = keyed_shaft()
    (slot,) = kw.find_slots(cylinders, flats)
    dims = by_id(kw.dimension_specs(slot, "rq_keyway", ("front", "top")))
    assert set(dims) == {"rq_keyway_w", "rq_keyway_t", "rq_keyway_l", "rq_keyway_p"}
    width = dims["rq_keyway_w"]
    assert {width["from"]["face"], width["to"]["face"]} == {"face:a", "face:b"} and width["along"] == "x" and width["view"] == "top"
    depth = dims["rq_keyway_t"]  # d - t1: from the bottom to the opposite side of the shaft
    assert depth["from"] == {"face": "face:bottom"} and depth["to"] == {"at": [0.0, -15.0, 35.0]} and depth["along"] == "y"
    length = dims["rq_keyway_l"]
    assert {length["from"]["face"], length["to"]["face"]} == {"face:end_lo", "face:end_hi"} and length["along"] == "z"
    position = dims["rq_keyway_p"]
    assert position["from"] == {"face": "face:cap0"} and position["to"] == {"face": "face:end_lo"}


def test_a_keyway_open_through_the_end_has_a_length_to_that_end_and_no_position() -> None:
    cylinders, flats = keyed_shaft(closed=False)
    (slot,) = kw.find_slots(cylinders, flats)
    dims = by_id(kw.dimension_specs(slot, "k"))
    assert set(dims) == {"k_w", "k_t", "k_l"}
    assert {dims["k_l"]["from"]["face"], dims["k_l"]["to"]["face"]} == {"face:end_lo", "face:cap1"}
    assert "view" not in dims["k_w"]  # the sheet has no view that looks along the axis: the default one


def test_a_dimension_whose_faces_are_not_found_is_left_out() -> None:
    cylinders, flats = keyed_shaft()
    nameless = [kw.Flat(f.normal, f.centre, "" if f.face_id == "face:bottom" else f.face_id) for f in flats]
    (slot,) = kw.find_slots(cylinders, nameless)
    assert "k_t" not in by_id(kw.dimension_specs(slot, "k"))  # no id for the bottom: no depth, nothing guessed
    slanted = [kw.Flat(f.normal, f.centre, f.face_id) for f in flats]
    inclined = kw.Cylinder((0.0, 0.6, 0.8), (0.0, 0.0, 0.0), 15.0, False, 0.0, 80.0)
    assert kw.axis_name(inclined.axis) is None and slanted  # an axis that is no model axis is not dimensioned lengthwise
