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
