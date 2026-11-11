"""Soundness of the verification against the real FakeAdapter: an asserted boolean volume must never be wrong on a
correct model. Every scenario below was a false failure (asserted value != observed value) before the edit tracking
attributed edits to the right bodies."""

from typing import Any

import pytest

from cotaforge.executor.trace import ExecutorState
from cotaforge.verification.results import CheckKind
from tests.unit.executor.test_with_fake import _fake, _run


def _plan(*ops: dict[str, Any]) -> dict[str, Any]:
    return {"id": "soundness", "kind": "part", "operations": list(ops)}


def _box(op_id: str, size: list[float], origin: list[float] | None = None) -> dict[str, Any]:
    op: dict[str, Any] = {"id": op_id, "op": "box", "intent": "box", "size": size}
    if origin is not None:
        op["placement"] = {"origin": origin}
    return op


def _face(from_op: str, normal: list[float]) -> dict[str, Any]:
    return {"kind": "face", "from_op": from_op, "select": {"normal": normal}}


def _rect(op_id: str, on: dict[str, Any], center: list[float], width: float, height: float) -> dict[str, Any]:
    entity = {"kind": "rect", "name": "r", "center": center, "width": width, "height": height}
    return {"id": op_id, "op": "sketch", "intent": "i", "on": on, "entities": [entity]}


def _extrude(op_id: str, sketch: str, depth: float) -> dict[str, Any]:
    return {"id": op_id, "op": "extrude", "intent": "i", "sketch": sketch, "depth_mm": depth}


def _union(target: str, *tools: str) -> dict[str, Any]:
    return {
        "id": "both",
        "op": "boolean_union",
        "intent": "fuse",
        "target": {"kind": "body", "from_op": target},
        "tools": [{"kind": "body", "from_op": t} for t in tools],
    }


def _run_plan(*ops: dict[str, Any]) -> Any:
    traces, _ = _run([_plan(*ops)], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, [(s.op_id, s.status, s.plan_error) for s in trace.steps]
    return trace


def _delta(trace: Any) -> Any:
    step = trace.step("both")
    assert step is not None
    return next(c for c in step.last_level2() if c.name == "volume_delta")


def _never_a_false_assertion(delta: Any) -> None:
    if delta.kind is CheckKind.asserted:
        assert delta.ok is True, (delta.expected, delta.observed)


def test_a_feature_on_the_face_of_a_boss_edits_the_body_the_boss_merged_into() -> None:
    """Block 10 x 20 x 30, a 6 x 6 boss on its top face, a 2 x 2 boss on the boss top (x 4..6, y 9..11, z 35..40) and
    a 2 mm cube inside that second boss: the union with the block overlaps the boss by 8 mm3, not 0."""
    trace = _run_plan(
        _box("block", [10, 20, 30]),
        _rect("s1", _face("block", [0, 0, 1]), [0, 0], 6, 6),
        _extrude("e1", "s1", 5),
        _rect("s2", {"kind": "face", "from_op": "e1", "select": {"smallest": True, "normal": [0, 0, 1]}}, [0, 0], 2, 2),
        _extrude("e2", "s2", 5),
        _box("bb", [2, 2, 2], [4, 9, 37]),
        _union("block", "bb"),
    )
    delta = _delta(trace)
    assert delta.observed == pytest.approx(-8.0)
    _never_a_false_assertion(delta)
    assert delta.kind is CheckKind.reported


def test_a_merged_extrude_that_meets_no_tracked_body_still_edits_the_body_it_merged_into() -> None:
    """The adapter merges a plane-sketch extrude into the only body (a 10 mm cube) although the extrusion floats at
    (50, 50): a cube placed in it overlaps the merged body by 64 mm3."""
    trace = _run_plan(
        _box("aa", [10, 10, 10]),
        _rect("s1", {"kind": "plane", "name": "XY"}, [50, 50], 10, 10),
        _extrude("e1", "s1", 10),
        _box("bb", [4, 4, 4], [48, 48, 2]),
        _union("aa", "bb"),
    )
    delta = _delta(trace)
    assert delta.observed == pytest.approx(-64.0)
    _never_a_false_assertion(delta)


def test_identical_tools_do_not_double_count_their_overlap_with_the_target() -> None:
    """A 10 mm cube target and two identical 10 mm cubes placed 5 mm along X: each overlaps the target by 500 but the
    tools overlap each other (a real CAD loses 1500, the pairwise sum says 1000), so the delta must not be asserted."""
    trace = _run_plan(
        _box("target", [10, 10, 10]),
        _box("t1", [10, 10, 10], [5, 0, 0]),
        {"id": "t2", "op": "copy_body", "intent": "same again", "source": {"kind": "body", "from_op": "t1"}},
        _union("target", "t1", "t2"),
    )
    delta = _delta(trace)
    _never_a_false_assertion(delta)
    assert delta.kind is CheckKind.reported


def test_disjoint_tools_keep_the_union_analytic() -> None:
    trace = _run_plan(
        _box("target", [10, 10, 10]),
        _box("t1", [10, 10, 10], [5, 0, 0]),
        _box("t2", [10, 10, 10], [-5, 0, 0]),
        _union("target", "t1", "t2"),
    )
    delta = _delta(trace)
    assert delta.kind is CheckKind.asserted and delta.ok is True
    assert delta.expected == pytest.approx(-1000.0)


def test_a_through_all_cut_from_a_plane_outside_the_body_has_a_correct_extent() -> None:
    """A 20 mm cube at z = 100 and a through-all cut sketched on the XY plane (z = 0) pointing +Z: the cut crosses
    20 mm of material, not the 120 mm from the plane to the far side."""
    cut = {
        "id": "drill",
        "op": "cut",
        "intent": "i",
        "sketch": "s1",
        "end_condition": "through_all",
        "direction": "positive",
    }
    trace = _run_plan(
        _box("block", [20, 20, 20], [0, 0, 100]), _rect("s1", {"kind": "plane", "name": "XY"}, [10, 10], 4, 4), cut
    )
    step = trace.step("drill")
    assert step is not None
    for check in step.last_level2():
        if check.kind is CheckKind.asserted:
            assert check.ok is True, (check.name, check.expected, check.observed)
    extent = next(c for c in step.last_level2() if c.name == "cut_extent_along_n")
    assert extent.expected == pytest.approx(20.0)


def test_a_through_all_hole_is_judged_against_its_own_body_not_the_whole_document() -> None:
    """Two bodies: a 20 x 20 x 10 block at the origin and a 20 mm cube at z = 100. A through hole on the cube top is
    20 mm long; the document box (120 mm tall) must not be the yardstick."""
    hole = {
        "id": "bore",
        "op": "hole",
        "intent": "bore",
        "face": _face("yy", [0, 0, 1]),
        "position": [0, 0],
        "standard": "simple",
        "diameter_mm": 4,
        "end_condition": "through_all",
    }
    trace = _run_plan(_box("xx", [20, 20, 10]), _box("yy", [20, 20, 20], [0, 0, 100]), hole)
    step = trace.step("bore")
    assert step is not None
    for check in step.last_level2():
        if check.kind is CheckKind.asserted:
            assert check.ok is True, (check.name, check.expected, check.observed)
    extent = next(c for c in step.last_level2() if c.name == "cut_extent_along_n")
    assert extent.kind is CheckKind.asserted and extent.expected == pytest.approx(20.0)


def test_a_through_all_cut_from_a_plane_in_a_multibody_document_is_reported_not_asserted() -> None:
    """With two bodies and a sketch on a plane, which body is cut is not in the plan: the extent is not asserted."""
    cut = {
        "id": "drill",
        "op": "cut",
        "intent": "i",
        "sketch": "s1",
        "end_condition": "through_all",
        "direction": "positive",
    }
    trace = _run_plan(
        _box("xx", [20, 20, 10]),
        _box("yy", [20, 20, 20], [0, 0, 100]),
        _rect("s1", {"kind": "plane", "name": "XY"}, [10, 10], 4, 4),
        cut,
    )
    step = trace.step("drill")
    assert step is not None
    for check in step.last_level2():
        if check.kind is CheckKind.asserted:
            assert check.ok is True, (check.name, check.expected, check.observed)
    extent = next(c for c in step.last_level2() if c.name == "cut_extent_along_n")
    assert extent.kind is CheckKind.reported
