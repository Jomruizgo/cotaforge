"""The SEED may stand on an earlier feature too, and a host that moved invalidates what was recorded about its features:
in both cases the reflection (or pattern instance) cannot be promised to do what the seed did, so its volume and face
count are REPORTED; ``instance_count`` stays asserted."""

from typing import Any

from cotaforge.executor.trace import ExecutorState
from cotaforge.verification.results import CheckKind
from tests.unit.executor.test_mirror_with_fake import (
    PLATE_TOP,
    _asserted_ok,
    _boss,
    _datum,
    _kind,
    _mirror,
    _plan,
)
from tests.unit.executor.test_with_fake import _fake, _run

MID = {"kind": "plane", "from_op": "mid"}


def _post_with_bore() -> list[dict[str, Any]]:
    """A d20 post 5 high at (20, 30) and a d6 bore drilled from its top face through post and plate."""
    post_top = {"kind": "face", "from_op": "post", "select": {"normal": [0, 0, 1], "center": [20, 30, 13]}}
    return [
        {
            "id": "post_sketch",
            "op": "sketch",
            "intent": "post outline",
            "on": PLATE_TOP,
            "entities": [{"kind": "circle", "name": "c", "center": [-30, 0], "diameter": 20}],
        },
        {"id": "post", "op": "extrude", "intent": "post", "sketch": "post_sketch", "depth_mm": 5},
        {
            "id": "bore",
            "op": "hole",
            "intent": "bore through the post and the plate",
            "face": post_top,
            "position": [0, 0],
            "standard": "simple",
            "diameter_mm": 6,
            "end_condition": "through_all",
        },
    ]


def _completed(trace: Any) -> None:
    assert trace.final_state == ExecutorState.completed, [(s.op_id, s.status, s.plan_error) for s in trace.steps]


def test_a_seed_that_stands_on_an_earlier_feature_reports_the_reflection_too() -> None:
    """The bore crosses the post (13 mm of material) but its reflection at (80, 30) crosses the plate alone (8 mm):
    twice the seed's change would be wrong."""
    plan = _plan(*_post_with_bore(), _datum(50), _mirror(MID, features=("bore",)))
    trace = _run([plan], _fake())[0][0]
    _completed(trace)
    assert _kind(trace, "twin_boss", "volume_delta") is CheckKind.reported
    assert _kind(trace, "twin_boss", "created_faces_count") is CheckKind.reported
    assert "instance_count" in _asserted_ok(trace, "twin_boss")


def test_a_linear_pattern_of_a_seed_that_stands_on_an_earlier_feature_is_reported_too() -> None:
    row = {
        "id": "row",
        "op": "linear_pattern",
        "intent": "row of bores",
        "features": ["bore"],
        "direction": "X",
        "spacing_mm": 40,
        "count": 2,
    }
    trace = _run([_plan(*_post_with_bore(), row)], _fake())[0][0]
    _completed(trace)
    assert _kind(trace, "row", "volume_delta") is CheckKind.reported
    assert _kind(trace, "row", "created_faces_count") is CheckKind.reported


def test_a_host_that_moved_after_a_feature_was_recorded_makes_that_feature_unlocatable() -> None:
    """boss2 at (88, 10) is carried to x = 138 by the translate; the reflection of the boss (x ~ 135) lands on it. The
    region recorded for boss2 is stale after the move, so the change is REPORTED."""
    slide = {
        "id": "slide",
        "op": "translate",
        "intent": "carry the plate along X",
        "target": {"kind": "body", "from_op": "plate"},
        "offset": [50, 0, 0],
    }
    moved_top = {"kind": "face", "from_op": "plate", "select": {"normal": [0, 0, 1]}}
    boss = [
        {
            "id": "boss_sketch",
            "op": "sketch",
            "intent": "boss outline",
            "on": moved_top,
            "entities": [{"kind": "circle", "name": "c", "center": [65 - 100, 10 - 30], "diameter": 10}],
        },
        {"id": "boss", "op": "extrude", "intent": "boss", "sketch": "boss_sketch", "depth_mm": 5},
    ]
    plan = _plan(*_boss((88.0, 10.0), "boss2"), slide, *boss, _datum(100), _mirror(MID))
    trace = _run([plan], _fake())[0][0]
    _completed(trace)
    assert _kind(trace, "twin_boss", "volume_delta") is CheckKind.reported
    assert _kind(trace, "twin_boss", "created_faces_count") is CheckKind.reported
