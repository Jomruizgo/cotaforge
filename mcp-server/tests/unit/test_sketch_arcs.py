"""Arcs and slots through the ``op`` tool: what the agent writes, what it reads back, and how it finds them."""

import math

import pytest

from tests.conftest import Caller, plate

XY = {"kind": "plane", "name": "XY"}
SLOT = {"kind": "slot", "name": "s", "start": [30, 30], "end": [70, 30], "width": 10}
D_SHAPE = [
    {"kind": "line", "name": "base", "start": [-5, 0], "end": [5, 0]},
    {"kind": "arc3", "name": "dome", "start": [5, 0], "through": [0, 5], "end": [-5, 0]},
]


def volume(call: Caller) -> float:
    return call.session.measure()[0].volume_mm3


def test_a_slot_sketch_reports_its_exact_area_and_cuts_the_part(call: Caller) -> None:
    plate(call)
    text = call.ok("op", name="sketch", params={"on": XY, "entities": [SLOT]})
    area = 40 * 10 + math.pi * 25
    assert "area 478.54" in text
    cut = call.ok("op", name="cut", params={"sketch": "s3", "end_condition": "through_all", "direction": "positive"})
    assert volume(call) == pytest.approx(100 * 60 * 8 - area * 8, rel=1e-4)
    assert "volume: " in cut and "(-" in cut


def test_lines_and_arcs_chain_into_a_profile_and_extrude(call: Caller) -> None:
    call.ok("doc", action="new", name="d")
    text = call.ok("op", name="sketch", params={"on": XY, "entities": D_SHAPE})
    assert "1 closed" in text and "area 39.27" in text
    call.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 4})
    assert volume(call) == pytest.approx(math.pi * 25 / 2 * 4, rel=1e-4)


def test_an_impossible_arc_is_refused_with_the_reason_and_the_shape(call: Caller) -> None:
    call.ok("doc", action="new", name="d")
    flat = {"kind": "arc3", "name": "a", "start": [0, 0], "through": [5, 0], "end": [10, 0]}
    text = call.err("op", name="sketch", params={"on": XY, "entities": [flat]})
    assert "collinear" in text and "Arc3Entity" in text  # the usage line lists the arc forms
    off_circle = {"kind": "arc_ends", "name": "a", "center": [0, 0], "start": [5, 0], "end": [0, 6]}
    assert "same distance from center" in call.err("op", name="sketch", params={"on": XY, "entities": [off_circle]})


def test_an_open_chain_with_an_arc_fails_naming_the_break_and_changes_nothing(call: Caller) -> None:
    call.ok("doc", action="new", name="d")
    open_chain = [
        {"kind": "line", "name": "l", "start": [0, 0], "end": [10, 0]},
        {"kind": "arc3", "name": "a", "start": [10, 0], "through": [12, 3], "end": [10, 6]},
    ]
    text = call.err("op", name="sketch", id="open", params={"on": XY, "entities": open_chain})
    assert "open chain" in text and "(10.0, 6.0)" in text and "state: unchanged" in text


def test_sketch_status_counts_a_slot_and_its_named_dimensions(call: Caller) -> None:
    call.ok("doc", action="new", name="d")
    dims = [{"name": "w", "entity": "s", "attribute": "width"}, {"name": "l", "entity": "s", "attribute": "length"}]
    call.ok("op", name="sketch", params={"on": XY, "entities": [SLOT], "named_dimensions": dims})
    assert "2 degrees of freedom left" in call.ok("inspect", action="sketch_status", sketch="s1")


def test_a_slot_width_is_edited_by_its_dimension_name(call: Caller) -> None:
    plate(call)
    dims = [{"name": "slot_w", "entity": "s", "attribute": "width"}]
    call.ok("op", name="sketch", params={"on": XY, "entities": [SLOT], "named_dimensions": dims})
    call.ok("op", name="cut", params={"sketch": "s3", "end_condition": "through_all", "direction": "positive"})
    text = call.ok("op", edit="s3", params={"dimensions": {"slot_w": 14}})
    assert "slot_w 10 -> 14" in text
    assert volume(call) == pytest.approx(100 * 60 * 8 - (40 * 14 + math.pi * 49) * 8, rel=1e-4)


def test_an_arc_radius_is_edited_by_name_only_for_the_centre_radius_form(call: Caller) -> None:
    call.ok("doc", action="new", name="d")
    dims = [{"name": "r", "entity": "dome", "attribute": "radius"}]
    call.ok("op", name="sketch", params={"on": XY, "entities": D_SHAPE, "named_dimensions": dims})
    assert "change the entity itself" in call.err("op", edit="s1", params={"dimensions": {"r": 6}})


def test_the_guide_and_the_vocabulary_tell_where_arcs_and_slots_are(call: Caller) -> None:
    guide = call.ok("cad_guide", topic="op:sketch")
    for word in ("arc3", "arc_ends", "slot", "ccw", "clockwise"):
        assert word in guide
    terms = call.ok("cad_guide", topic="terms")
    assert "ranura" in terms and "arco por 3 puntos" in terms
