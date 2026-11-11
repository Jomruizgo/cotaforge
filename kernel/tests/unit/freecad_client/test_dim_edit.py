"""``worker/dim_edit.py`` in isolation: no FreeCAD. What counts as a dimension, which properties a feature edit sets and
which datums a sketch edit sets, over stand-ins."""

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, WORKER_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


de = _load("dim_edit")


class Obj:
    def __init__(self, name: str) -> None:
        self.Name = name


PAD = {"operation_id": "e1", "sketch": {"id": "sketch:1"}, "depth_mm": 8.0, "direction": "positive", "merge": True}
HOLE = {
    "operation_id": "h1",
    "face": {"id": "face:1"},
    "position": [-30, -20],
    "standard": "simple",
    "diameter_mm": 6.0,
    "end_condition": "blind",
    "depth_mm": 5.0,
    "thread": None,
    "counterbore": {"diameter_mm": 10.0, "depth_mm": 2.0},
    "countersink": None,
    "drill_point_angle_deg": 118.0,
}


def test_only_a_dimension_of_a_feature_is_a_dimension() -> None:
    assert de.non_dimension_changes("extrude", PAD, {**PAD, "depth_mm": 12.0}) == []
    assert de.non_dimension_changes("extrude", PAD, {**PAD, "direction": "negative"}) == ["direction"]
    assert de.non_dimension_changes("extrude", PAD, {**PAD, "merge": False, "depth_mm": 2.0}) == ["merge"]
    assert de.non_dimension_changes("hole", HOLE, {**HOLE, "position": [0, 0]}) == ["position"]
    nested = {**HOLE, "counterbore": {"diameter_mm": 12.0, "depth_mm": 3.0}}
    assert de.non_dimension_changes("hole", HOLE, nested) == []
    appears = {**HOLE, "countersink": {"diameter_mm": 9.0, "angle_deg": 90.0}}
    assert de.non_dimension_changes("hole", HOLE, appears) == ["countersink.diameter_mm", "countersink.angle_deg"]
    gone = {**HOLE, "counterbore": None}
    assert de.non_dimension_changes("hole", HOLE, gone) == ["counterbore.diameter_mm", "counterbore.depth_mm"]


def test_what_the_convention_derives_is_not_a_change_but_the_fit_and_the_thread_are() -> None:
    coloured = {**HOLE, "fit": "H7", "hole_class": "precision", "colour": [255, 128, 128], "precision_grade": 8}
    uncoloured = {**HOLE, "fit": "H7", "hole_class": None, "colour": None, "precision_grade": None}
    assert de.non_dimension_changes("hole", coloured, {**uncoloured, "diameter_mm": 12.0}) == []
    assert de.non_dimension_changes("hole", uncoloured, {**coloured, "colour": [1, 2, 3]}) == []
    assert de.non_dimension_changes("hole", coloured, {**coloured, "fit": "H9"}) == ["fit"]
    assert de.non_dimension_changes("hole", coloured, {**coloured, "thread": {"size": "M6"}}) == ["thread.size"]


def test_the_worker_table_is_the_one_of_the_contract() -> None:
    from cotaforge.contract import dimensions

    assert de.DIMENSION_FIELDS == dimensions.DIMENSION_FIELDS
    assert de.DERIVED_FIELDS == dimensions.DERIVED_FIELDS


def _sig(surface: str, anchor: tuple[float, float, float], direction: tuple[float, float, float] = (0, 0, 1)) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(surface=surface, direction=direction, anchor=anchor, center=(anchor[0], anchor[1], 5.0))


def test_a_face_made_again_by_an_edit_goes_back_to_the_feature_of_the_face_it_replaces() -> None:
    stale = {
        "bore": _sig("cylinder", (20.0, 10.0, 0.0)),
        "copy": _sig("cylinder", (32.0, 10.0, 0.0)),
        "later": _sig("cylinder", (80.06, 40.04, 0.0)),
    }
    new = {  # the first two are resized, the third moved a hair
        "n1": _sig("cylinder", (20.0, 10.0, 0.0)),
        "n2": _sig("cylinder", (32.0, 10.0, 0.0)),
        "n3": _sig("cylinder", (80.25, 40.17, 0.0)),
    }
    assert de.replaced_faces(stale, new) == {"n1": "bore", "n2": "copy", "n3": "later"}
    # nothing left to replace, another kind of surface, or another axis: the edited feature's own
    assert de.replaced_faces({"bore": stale["bore"]}, {"a": new["n1"], "b": new["n2"]}) == {"a": "bore"}
    assert de.replaced_faces(stale, {"p": _sig("plane", (1.0, 1.0, 0.0))}) == {}
    assert de.replaced_faces(stale, {"x": _sig("cylinder", (20.0, 10.0, 0.0), (1, 0, 0))}) == {}


def test_a_sketch_changes_by_its_numbers_only() -> None:
    rect = {"kind": "rect", "name": "r", "center": [50, 30], "width": 100, "height": 60}
    old = {"on": "XY", "entities": [rect], "constraints": [], "named_dimensions": []}
    assert de.sketch_refusal(old, {**old, "entities": [{**rect, "width": 120}]}) is None
    assert "other entities" in de.sketch_refusal(old, {**old, "entities": [rect, rect]})
    assert "other entities" in de.sketch_refusal(old, {**old, "entities": [{**rect, "chamfer_mm": 2}]})
    assert "plane" in de.sketch_refusal(old, {**old, "on": "XZ"})


def test_a_feature_edit_sets_only_what_differs_from_the_recorded_request() -> None:
    pad = Obj("Pad")
    assert de.property_sets("extrude", PAD, {**PAD, "depth_mm": 12.0}, pad, []) == [(pad, "Length", 12.0)]
    assert de.property_sets("extrude", PAD, PAD, pad, []) == []  # nothing changed: a hand-made Length stays
    hole = Obj("Hole")
    sets = de.property_sets("hole", HOLE, {**HOLE, "diameter_mm": 8.0, "depth_mm": 7.0}, hole, [])
    assert sets == [(hole, "Diameter", 8.0), (hole, "Depth", 7.0)]
    sets = de.property_sets("hole", HOLE, {**HOLE, "counterbore": {"diameter_mm": 12.0, "depth_mm": 2.0}}, hole, [])
    assert sets == [(hole, "HoleCutDiameter", 12.0)]


def test_a_threaded_hole_keeps_the_tap_drill_of_its_size() -> None:
    threaded = {**HOLE, "thread": {"size": "M6"}}
    with pytest.raises(de.NotInPlaceError, match="tap drill"):
        de.property_sets("hole", threaded, {**threaded, "diameter_mm": 7.0}, Obj("Hole"), [])


def test_patterns_set_length_and_occurrences_of_each_direction() -> None:
    lp = {"count": 3, "spacing_mm": 10.0, "direction": [1, 0, 0]}
    one = Obj("LinearPattern")
    sets = de.property_sets("linear_pattern", lp, {**lp, "count": 4, "spacing_mm": 12.0}, one, [])
    assert sets == [(one, "Length", 36.0), (one, "Occurrences", 4)]
    two = {**lp, "direction2": [0, 1, 0], "spacing2_mm": 5.0, "count2": 2}
    a, b = Obj("LP1"), Obj("LP2")
    sets = de.property_sets("linear_pattern", two, {**two, "count2": 3}, a, [a, b])
    assert sets == [(b, "Length", 10.0), (b, "Occurrences", 3)]
    polar = {"count": 4, "angle_deg": 360.0, "equal_spacing": True}
    pp = Obj("PolarPattern")
    assert de.property_sets("circular_pattern", polar, {**polar, "count": 6}, pp, []) == [(pp, "Occurrences", 6)]


def row(kind: str, name: str = "", driving: bool = True, first: int = 0) -> tuple[Any, ...]:
    return (kind, first, 1, -1, 1, -2000, 0, driving, name)


def test_the_datums_that_differ_between_the_two_scratch_sketches_are_the_ones_set() -> None:
    rows = [row("DistanceX", "W"), row("DistanceY"), row("DistanceX", first=1), row("Coincident")]
    old = (rows, [100.0, 60.0, 0.0, 0.0])
    new = (rows, [120.0, 60.0, -10.0, 0.0])
    assert de.datum_changes(old, new, rows) == [(0, "W", 120.0), (2, "", -10.0)]
    # the engineer typed 61 by hand on the second: it is not among the changes, so it is not put back
    assert [i for i, _, _ in de.datum_changes(old, new, rows)] == [0, 2]


def test_reference_dimensions_follow_the_solver_and_are_not_set() -> None:
    rows = [row("DistanceX", "W"), row("DistanceX", "centre", driving=False)]
    assert de.datum_changes((rows, [100.0, 50.0]), (rows, [120.0, 60.0]), rows) == [(0, "W", 120.0)]


def test_another_structure_or_a_sketch_changed_by_hand_or_an_angle_is_not_in_place() -> None:
    rows = [row("DistanceX", "W")]
    other = [row("DistanceX", "W"), row("Coincident")]
    with pytest.raises(de.NotInPlaceError, match="another set of constraints"):
        de.datum_changes((rows, [1.0]), (other, [1.0, 0.0]), rows)
    with pytest.raises(de.NotInPlaceError, match="changed by hand"):
        de.datum_changes((rows, [1.0]), (rows, [2.0]), other)
    angle = [row("Angle", "A")]
    with pytest.raises(de.NotInPlaceError, match="only lengths"):
        de.datum_changes((angle, [0.5]), (angle, [0.6]), angle)


def test_a_datum_bound_to_an_expression_is_named() -> None:
    changes = [(0, "W", 120.0), (2, "", 4.0)]
    assert de.bound_by_expression(["Constraints.W"], changes) == ["W"]
    assert de.bound_by_expression(["Constraints[2]"], changes) == ["constraint 2"]
    assert de.bound_by_expression(["Constraints.other"], changes) == []
