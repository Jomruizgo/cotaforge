"""End-to-end ``mirror`` and ``mirror_body``: Executor + Verifier driving the real FakeAdapter."""

import math
from typing import Any

import pytest

from cotaforge.executor.trace import ExecutorState
from cotaforge.verification.results import CheckKind
from tests.unit.executor.test_with_fake import _asserted_ok, _fake, _run

PLATE_TOP = {"kind": "face", "from_op": "plate", "select": {"normal": [0, 0, 1]}}
YZ = {"kind": "plane", "name": "YZ"}


def _plate_ops() -> list[dict[str, Any]]:
    return [
        {
            "id": "plate_sketch",
            "op": "sketch",
            "intent": "plate outline",
            "on": {"kind": "plane", "name": "XY"},
            "entities": [{"kind": "rect", "name": "outline", "center": [50, 30], "width": 100, "height": 60}],
        },
        {"id": "plate", "op": "extrude", "intent": "plate", "sketch": "plate_sketch", "depth_mm": 8},
    ]


def _plan(*ops: dict[str, Any]) -> dict[str, Any]:
    return {"id": "bracket", "kind": "part", "operations": [*_plate_ops(), *ops]}


def _datum(distance: float) -> dict[str, Any]:
    return {
        "id": "mid",
        "op": "ref_plane",
        "intent": "mirror plane",
        "define": {"how": "offset", "base": YZ, "distance_mm": distance},
    }


def _boss(at: tuple[float, float] = (10.0, 10.0), op_id: str = "boss") -> list[dict[str, Any]]:
    return [
        {
            "id": f"{op_id}_sketch",
            "op": "sketch",
            "intent": "boss outline",
            "on": PLATE_TOP,
            "entities": [{"kind": "circle", "name": "c", "center": [at[0] - 50, at[1] - 30], "diameter": 10}],
        },
        {"id": op_id, "op": "extrude", "intent": "boss", "sketch": f"{op_id}_sketch", "depth_mm": 5},
    ]


def _mirror(plane: dict[str, Any], op_id: str = "twin_boss", features: tuple[str, ...] = ("boss",)) -> dict[str, Any]:
    return {"id": op_id, "op": "mirror", "intent": "boss on the other side", "features": list(features), "plane": plane}


def test_a_boss_mirrored_about_a_datum_plane_passes_every_check() -> None:
    traces, _ = _run([_plan(*_boss(), _datum(50), _mirror({"kind": "plane", "from_op": "mid"}))], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, [(s.op_id, s.status, s.plan_error) for s in trace.steps]
    assert _asserted_ok(trace, "twin_boss") == {
        "instance_count",
        "created_faces_count",
        "volume_delta",
        "bbox_contains",
    }
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(48000 + 2 * math.pi * 25 * 5)


def test_a_boss_mirrored_about_a_base_plane_through_the_origin_exits_the_plate_and_is_still_exact() -> None:
    """YZ is x = 0: the boss at x = 10 lands at x = -10, off the plate. Adding material off the body is exact."""
    traces, _ = _run([_plan(*_boss(), _mirror(YZ))], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, [(s.op_id, s.status, s.plan_error) for s in trace.steps]
    assert "volume_delta" in _asserted_ok(trace, "twin_boss")


def test_a_boss_that_straddles_the_plane_is_reported_not_asserted() -> None:
    traces, _ = _run([_plan(*_boss(), _datum(10), _mirror({"kind": "plane", "from_op": "mid"}))], _fake())
    trace = traces[0]
    step = trace.step("twin_boss")
    assert step is not None
    delta = next(c for c in step.last_level2() if c.name == "volume_delta")
    assert delta.kind is CheckKind.reported


def test_a_mirrored_body_passes_every_check_and_leaves_the_source() -> None:
    block = {"id": "block", "op": "box", "intent": "block", "size": [10, 20, 30]}
    mirrored = {
        "id": "twin",
        "op": "mirror_body",
        "intent": "left-hand twin",
        "source": {"kind": "body", "from_op": "block"},
        "plane": {"kind": "plane", "from_op": "mid"},
    }
    plan = {"id": "twins", "kind": "part", "operations": [block, _datum(20), mirrored]}
    traces, _ = _run([plan], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, [(s.op_id, s.status, s.plan_error) for s in trace.steps]
    assert _asserted_ok(trace, "twin") == {
        "volume_delta",
        "copy_centroid",
        "copy_surface",
        "source_centroid",
        "copy_bbox",
    }
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(12000.0)


def test_a_mirror_the_fake_cannot_model_surfaces_its_message() -> None:
    oblique = {"kind": "plane", "from_op": "tilted"}
    tilted = {
        "id": "tilted",
        "op": "ref_plane",
        "intent": "oblique plane",
        "define": {"how": "angle", "base": YZ, "about": {"origin": [0, 0, 0], "direction": [0, 0, 1]}, "angle_deg": 30},
    }
    traces, _ = _run([_plan(*_boss(), tilted, _mirror(oblique))], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.failed
    step = trace.step("twin_boss")
    assert step is not None
    assert "axis-aligned" in " | ".join(a.error.message for a in step.attempts if a.error)


# --- a reflected (or patterned) instance that lands on an earlier feature: reported, never asserted -----------------


def _bore(op_id: str, at: tuple[float, float]) -> dict[str, Any]:
    return {
        "id": op_id,
        "op": "hole",
        "intent": "bore",
        "face": PLATE_TOP,
        "position": [at[0] - 50, at[1] - 30],
        "standard": "simple",
        "diameter_mm": 6,
        "end_condition": "through_all",
    }


def _kind(trace: Any, op_id: str, name: str) -> CheckKind:
    step = trace.step(op_id)
    assert step is not None
    return next(c for c in step.last_level2() if c.name == name).kind


def test_a_mirrored_bore_that_lands_on_an_earlier_bore_reports_volume_and_faces() -> None:
    """bore2 at (72, 30) and the seed at (25, 30) about x = 50: the reflection (75, 30) overlaps bore2, so it removes
    less than the seed did. The change is REPORTED; the instance count is still certain."""
    plan = _plan(
        _bore("bore2", (72.0, 30.0)),
        _bore("bore", (25.0, 30.0)),
        _datum(50),
        _mirror({"kind": "plane", "from_op": "mid"}, features=("bore",)),
    )
    traces, _ = _run([plan], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, [(s.op_id, s.status, s.plan_error) for s in trace.steps]
    assert _kind(trace, "twin_boss", "volume_delta") is CheckKind.reported
    assert _kind(trace, "twin_boss", "created_faces_count") is CheckKind.reported
    assert "instance_count" in _asserted_ok(trace, "twin_boss")


def test_a_mirrored_boss_that_lands_on_an_earlier_boss_reports_volume_and_faces() -> None:
    plan = _plan(
        *_boss((88.0, 10.0), "boss2"), *_boss((10.0, 10.0)), _datum(50), _mirror({"kind": "plane", "from_op": "mid"})
    )
    traces, _ = _run([plan], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, [(s.op_id, s.status, s.plan_error) for s in trace.steps]
    assert _kind(trace, "twin_boss", "volume_delta") is CheckKind.reported
    assert _kind(trace, "twin_boss", "created_faces_count") is CheckKind.reported


def test_an_earlier_feature_far_from_the_reflection_keeps_the_checks_asserted() -> None:
    plan = _plan(
        _bore("bore2", (90.0, 50.0)),
        _bore("bore", (25.0, 30.0)),
        _datum(50),
        _mirror({"kind": "plane", "from_op": "mid"}, features=("bore",)),
    )
    traces, _ = _run([plan], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, [(s.op_id, s.status, s.plan_error) for s in trace.steps]
    assert {"instance_count", "created_faces_count", "volume_delta"} <= _asserted_ok(trace, "twin_boss")


def test_a_linear_pattern_instance_that_lands_on_an_earlier_bore_reports_volume_and_faces() -> None:
    pattern = {
        "id": "row",
        "op": "linear_pattern",
        "intent": "row of bores",
        "features": ["bore"],
        "direction": "X",
        "spacing_mm": 20,
        "count": 3,
    }
    plan = _plan(_bore("bore2", (45.0, 30.0)), _bore("bore", (25.0, 30.0)), pattern)
    traces, _ = _run([plan], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, [(s.op_id, s.status, s.plan_error) for s in trace.steps]
    assert _kind(trace, "row", "volume_delta") is CheckKind.reported
    assert _kind(trace, "row", "created_faces_count") is CheckKind.reported
    assert "instance_count" in _asserted_ok(trace, "row")
