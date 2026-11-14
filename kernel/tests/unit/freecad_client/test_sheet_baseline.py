# ruff: noqa: E501
"""``baseline_dims``: the lengths of a turned part counted from ONE datum face as parallel dimensions (no FreeCAD). The datum is
the one the engineer or the intent file names: with none, nothing is made."""

import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import pytest  # noqa: E402
import sheet_keyway as kw  # noqa: E402
import sheet_turned as turned  # noqa: E402

X = (1.0, 0.0, 0.0)
MX = (-1.0, 0.0, 0.0)


def plane(face_id: str, x: float, normal: tuple[float, float, float] = X) -> kw.Flat:
    return kw.Flat(normal, (x, 0.0, 6.0), face_id)


# the bench drive shaft along x: M20 end 0..28, seat 28..43, body 43..103, seat 103..118, pulley end 118..168 (chamfered ends)
PLANES = [
    plane("face:1", 0.0, MX),
    plane("face:4", 28.0, MX),
    plane("face:6", 43.0, MX),
    plane("face:8", 103.0),
    plane("face:10", 118.0),
    plane("face:13", 168.0),
    plane("face:99", 122.0),  # the end of the keyway
    plane("face:77", 60.0),  # the bottom of a drilled hole: not a step
]
SPANS = [  # the convex cylinders: their axis and where they start and end along it
    (X, 1.5, 28.0),  # the end is chamfered: no plane at 1.5
    (X, 28.0, 43.0),
    (X, 43.0, 103.0),
    (X, 103.0, 118.0),
    (X, 118.0, 167.0),
]
SKIP = {"face:99"}


def to_ids(items: list[dict[str, object]]) -> list[str]:
    return [str(i["to"]["face"]) for i in items]  # type: ignore[index]


def test_every_step_and_both_ends_are_dimensioned_from_the_named_face_nearest_first() -> None:
    items = turned.baseline_dims("face:6", PLANES, SPANS, SKIP, "bl")
    assert to_ids(items) == ["face:4", "face:1", "face:8", "face:10", "face:13"]  # 15, 43, 60, 75, 125 mm from x = 43
    assert {i["from"]["face"] for i in items} == {"face:6"}  # type: ignore[index]
    assert all(i["type"] == "dim" and i["kind"] == "distance" and i["along"] == "x" for i in items)
    assert [i["id"] for i in items] == ["bl1", "bl2", "bl3", "bl4", "bl5"]


def test_a_chamfered_end_is_dimensioned_at_its_end_face_not_where_the_cylinder_ends() -> None:
    assert "face:1" in to_ids(turned.baseline_dims("face:13", PLANES, SPANS, SKIP, "bl"))


def test_the_end_of_a_keyway_and_the_bottom_of_a_hole_are_not_steps() -> None:
    found = to_ids(turned.baseline_dims("face:1", PLANES, SPANS, SKIP, "bl"))
    assert "face:99" not in found and "face:77" not in found


def test_view_and_side_are_passed_on_and_left_out_when_not_given() -> None:
    plain = turned.baseline_dims("face:1", PLANES, SPANS, SKIP, "bl")[0]
    assert "view" not in plain and "side" not in plain
    asked = turned.baseline_dims("face:1", PLANES, SPANS, SKIP, "bl", "front", "below")[0]
    assert asked["view"] == "front" and asked["side"] == "below"


def test_a_datum_that_is_not_a_plane_of_the_part_is_refused_in_words() -> None:
    with pytest.raises(turned.BaselineError, match="not a planar face"):
        turned.baseline_dims("face:50", PLANES, SPANS, SKIP, "bl")


def test_a_datum_not_square_to_an_axis_is_refused() -> None:
    slanted = [*PLANES, kw.Flat((0.7071, 0.7071, 0.0), (50.0, 0.0, 0.0), "face:55")]
    with pytest.raises(turned.BaselineError, match="not square to the x, y or z axis"):
        turned.baseline_dims("face:55", slanted, SPANS, SKIP, "bl")


def test_a_part_with_nothing_else_square_to_that_axis_has_nothing_to_dimension() -> None:
    with pytest.raises(turned.BaselineError, match="nothing to dimension"):
        turned.baseline_dims("face:1", [plane("face:1", 0.0, MX)], [], SKIP, "bl")
