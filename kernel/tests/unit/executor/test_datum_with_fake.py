"""Executor + Verifier + FakeAdapter with datum planes/axes: the end-to-end behaviour of ref_plane / ref_axis."""

import math
from typing import Any

import pytest

from cotaforge.adapters.fake import FakeAdapter, FaultPlan
from cotaforge.contract import ConnectRequest, Empty, ErrorCode, MassRequest
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.errors import ResolutionError
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier

RECT = {"kind": "rect", "name": "r", "center": [50, 30], "width": 100, "height": 60}
CIRCLE = {"kind": "circle", "name": "c", "center": [0, 0], "diameter": 10}


def _fake(faults: list[dict[str, Any]] | None = None) -> FakeAdapter:
    adapter = FakeAdapter(faults=FaultPlan.model_validate({"faults": faults or []}))
    adapter.connect(ConnectRequest(operation_id="connect"))
    return adapter


def _plan(*operations: dict[str, Any]) -> dict[str, Any]:
    return {"id": "datums", "operations": list(operations)}


def _run(plan: dict[str, Any], adapter: FakeAdapter | None = None) -> tuple[Executor, ExecutionTrace, FakeAdapter]:
    adapter = adapter or _fake()
    executor = Executor(parse_plan(plan), adapter, Verifier(), ExecutorConfig())
    executor.start()
    return executor, executor.run_to_end(), adapter


def _sketch(op_id: str, on: dict[str, Any], entity: dict[str, Any] = RECT) -> dict[str, Any]:
    return {"id": op_id, "op": "sketch", "intent": "i", "on": on, "entities": [entity]}


def _offset(op_id: str, base: dict[str, Any], distance: float) -> dict[str, Any]:
    return {
        "id": op_id,
        "op": "ref_plane",
        "intent": "i",
        "define": {"how": "offset", "base": base, "distance_mm": distance},
    }


XY = {"kind": "plane", "name": "XY"}
BASE = [
    _sketch("s0", XY),
    {"id": "base", "op": "extrude", "intent": "i", "sketch": "s0", "depth_mm": 8},
]


def _asserted_ok(trace: ExecutionTrace, op_id: str) -> set[str]:
    step = trace.step(op_id)
    assert step is not None, op_id
    return {c.name for c in step.last_level2() if c.kind is CheckKind.asserted and c.ok}


def _assert_all_green(trace: ExecutionTrace) -> None:
    assert trace.final_state is ExecutorState.completed, [(s.op_id, s.status, s.plan_error) for s in trace.steps]
    assert all(s.status is StepStatus.success for s in trace.steps)


# --- datum steps are geometry-neutral ----------------------------------------------------------------


def test_datum_ops_leave_volume_bbox_and_feature_tree_untouched() -> None:
    plan = _plan(
        *BASE,
        _offset("dp", XY, 20),
        {
            "id": "da",
            "op": "ref_axis",
            "intent": "i",
            "define": {"how": "two_points", "points": [[0, 0, 0], [0, 0, 5]]},
        },
    )
    executor, trace, adapter = _run(plan)
    _assert_all_green(trace)
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(48000.0)
    assert trace.final_bbox is not None and trace.final_bbox.max == pytest.approx((100.0, 60.0, 8.0))
    tree = adapter.feature_tree(Empty(operation_id="t"))
    assert [node.kind for node in tree.nodes] == ["extrude"]  # datums are not feature-tree nodes
    dp = trace.step("dp")
    assert dp is not None
    assert {"volume_delta", "datum_origin", "datum_u", "datum_normal"} <= _asserted_ok(trace, "dp")
    assert dp.attempts[0].result is not None and dp.attempts[0].result.feature is None
    assert {"datum_axis_position", "datum_axis_direction"} <= _asserted_ok(trace, "da")
    mass_before = adapter.mass_properties(MassRequest(operation_id="m"))
    assert mass_before.volume_mm3 == pytest.approx(48000.0)


# --- sketch on a datum --------------------------------------------------------------------------------


def test_sketch_and_extrude_on_an_offset_datum() -> None:
    plan = _plan(
        *BASE,
        _offset("dp", XY, 20),
        _sketch("s1", {"kind": "plane", "from_op": "dp"}),
        {"id": "boss", "op": "extrude", "intent": "i", "sketch": "s1", "depth_mm": 5},
    )
    _, trace, _ = _run(plan)
    _assert_all_green(trace)
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(48000.0 + 6000.0 * 5)
    assert trace.final_bbox is not None
    assert trace.final_bbox.min == pytest.approx((0.0, 0.0, 0.0))
    assert trace.final_bbox.max == pytest.approx((100.0, 60.0, 25.0))
    assert {"volume_delta", "bbox_after", "new_faces_count"} <= _asserted_ok(trace, "boss")


def test_offset_of_a_face_from_the_model_and_a_chain_of_datums() -> None:
    top = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}
    plan = _plan(
        *BASE,
        # a face-based datum takes the face frame: origin at the face centre, so the sketch below is centred on it
        _offset("on_top", top, 12),  # 8 + 12 = 20
        _offset("chained", {"kind": "plane", "from_op": "on_top"}, 3),
        _sketch("s1", {"kind": "plane", "from_op": "chained"}, {**RECT, "center": [0, 0], "width": 20, "height": 20}),
        {"id": "boss", "op": "extrude", "intent": "i", "sketch": "s1", "depth_mm": 2},
    )
    _, trace, _ = _run(plan)
    _assert_all_green(trace)
    assert trace.final_bbox is not None
    assert trace.final_bbox.max == pytest.approx((100.0, 60.0, 25.0))


def test_inclined_datum_gives_the_right_frame_and_an_exact_bbox() -> None:
    about = {"origin": [0, 0, 0], "direction": [1, 0, 0]}
    plan = _plan(
        {
            "id": "tilt",
            "op": "ref_plane",
            "intent": "i",
            "define": {"how": "angle", "base": XY, "about": about, "angle_deg": 30},
        },
        _sketch("s1", {"kind": "plane", "from_op": "tilt"}, CIRCLE),
        {"id": "rod", "op": "extrude", "intent": "i", "sketch": "s1", "depth_mm": 40},
    )
    _, trace, adapter = _run(plan)
    _assert_all_green(trace)
    tilt = math.radians(30)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(math.pi * 25.0 * 40.0)
    assert trace.final_bbox is not None
    # the rod runs along n = (0, -sin30, cos30) from the origin; lateral extent is the exact cylinder box
    n = (0.0, -math.sin(tilt), math.cos(tilt))
    assert trace.final_bbox.max[2] == pytest.approx(40 * n[2] + 5 * math.sqrt(1 - n[2] ** 2))
    assert trace.final_bbox.min[1] == pytest.approx(40 * n[1] - 5 * math.sqrt(1 - n[1] ** 2))
    assert {"volume_delta", "bbox_after"} <= _asserted_ok(trace, "rod")
    assert adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(math.pi * 25 * 40)


def test_cut_from_an_inclined_datum_inside_the_body() -> None:
    """A blind cut whose sketch sits on a tilted datum is modelled exactly (volume = area x depth)."""
    about = {"origin": [0, 50, 50], "direction": [1, 0, 0]}
    plan = _plan(
        _sketch("s0", XY, {"kind": "rect", "name": "r", "center": [50, 50], "width": 100, "height": 100}),
        {"id": "block", "op": "extrude", "intent": "i", "sketch": "s0", "depth_mm": 100},
        _offset("mid", XY, 50),
        {
            "id": "tilt",
            "op": "ref_plane",
            "intent": "i",
            "define": {"how": "angle", "base": {"kind": "plane", "from_op": "mid"}, "about": about, "angle_deg": 30},
        },
        _sketch("s1", {"kind": "plane", "from_op": "tilt"}, {**CIRCLE, "center": [50, 0]}),
        {"id": "pocket", "op": "cut", "intent": "i", "sketch": "s1", "depth_mm": 10},
    )
    _, trace, _ = _run(plan)
    _assert_all_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(1_000_000.0 - math.pi * 25.0 * 10.0)
    assert "volume_delta" in _asserted_ok(trace, "pocket")


def test_boolean_with_an_inclined_prism_is_an_explicit_not_supported_never_a_wrong_answer() -> None:
    """Booleans between inclined and axis-aligned prisms are outside the fake's containment rules."""
    about = {"origin": [0, 0, 0], "direction": [1, 0, 0]}
    plan = _plan(
        *BASE,
        {
            "id": "tilt",
            "op": "ref_plane",
            "intent": "i",
            "define": {"how": "angle", "base": XY, "about": about, "angle_deg": 30},
        },
        _sketch("s1", {"kind": "plane", "from_op": "tilt"}, {**CIRCLE, "center": [50, 10]}),
        {"id": "rod", "op": "extrude", "intent": "i", "sketch": "s1", "depth_mm": 40, "merge": False},
        {
            "id": "diff",
            "op": "boolean_subtract",
            "intent": "i",
            "target": {"kind": "body", "from_op": "base"},
            "tools": [{"kind": "body", "from_op": "rod"}],
        },
    )
    _, trace, _ = _run(plan)
    diff = trace.step("diff")
    assert diff is not None and diff.status is StepStatus.failed
    error = diff.attempts[-1].error
    assert error is not None and error.code is ErrorCode.not_supported
    assert "containment rules" in error.message


# --- ref_axis consumers --------------------------------------------------------------------------------


def test_normal_to_the_axis_of_an_inclined_rod_then_sketch_and_extrude_along_it() -> None:
    about = {"origin": [0, 0, 0], "direction": [1, 0, 0]}
    plan = _plan(
        {
            "id": "tilt",
            "op": "ref_plane",
            "intent": "i",
            "define": {"how": "angle", "base": XY, "about": about, "angle_deg": 30},
        },
        _sketch("s1", {"kind": "plane", "from_op": "tilt"}, CIRCLE),
        {"id": "rod", "op": "extrude", "intent": "i", "sketch": "s1", "depth_mm": 40},
        {
            "id": "rod_axis",
            "op": "ref_axis",
            "intent": "i",
            "define": {
                "how": "entity",
                "entity": {"kind": "face", "from_op": "rod", "select": {"surface": "cylinder"}},
            },
        },
        {
            "id": "section",
            "op": "ref_plane",
            "intent": "i",
            "define": {"how": "normal_to", "axis": {"kind": "axis_ref", "from_op": "rod_axis"}, "at_mm": 20},
        },
        _sketch("s2", {"kind": "plane", "from_op": "section"}, {**CIRCLE, "diameter": 4}),
        {"id": "bore", "op": "cut", "intent": "i", "sketch": "s2", "depth_mm": 5},
    )
    executor, trace, _ = _run(plan)
    _assert_all_green(trace)
    section = trace.step("section")
    assert section is not None
    frame = section.attempts[-1].request["frame"]
    tilt = math.radians(30)
    assert frame["n"] == pytest.approx([0.0, -math.sin(tilt), math.cos(tilt)])
    assert frame["origin"] == pytest.approx([0.0, -20 * math.sin(tilt), 20 * math.cos(tilt)])
    assert {"datum_origin", "datum_normal"} <= _asserted_ok(trace, "section")


# --- undo, faults, repair ------------------------------------------------------------------------------


def test_undo_to_clears_the_datum_registrations_and_the_plan_can_continue() -> None:
    plan = _plan(*BASE, _offset("dp", XY, 20), _sketch("s1", {"kind": "plane", "from_op": "dp"}))
    executor, trace, adapter = _run(plan)
    _assert_all_green(trace)
    assert executor.undo_to("base") == ("s1", "dp")
    ctx = executor._ctx
    assert ctx is not None and ctx.datum_plane_by_op == {} and "s1" not in ctx.sketch_by_op
    # redo: extend replaces the reverted tail and datum ids are fresh
    executor.extend(
        [
            _offset("dp", XY, 30),
            _sketch("s1", {"kind": "plane", "from_op": "dp"}),
            {"id": "boss", "op": "extrude", "intent": "i", "sketch": "s1", "depth_mm": 5},
        ]
    )
    again = executor.run_to_end()
    assert again.final_state is ExecutorState.completed
    assert again.final_bbox is not None and again.final_bbox.max == pytest.approx((100.0, 60.0, 35.0))


def test_injected_pose_mismatch_is_caught_by_level2() -> None:
    plan = _plan(*BASE, _offset("dp", XY, 20))
    adapter = _fake([{"operation_id": "dp", "kind": "wrong_datum_pose", "recoverable": False}])
    _, trace, _ = _run(plan, adapter)
    dp = trace.step("dp")
    assert dp is not None and dp.status is StepStatus.failed
    failing = {c.name for c in dp.last_level2() if c.ok is False}
    assert {"datum_origin", "datum_normal"} <= failing


def test_injected_pose_mismatch_on_an_axis_is_caught() -> None:
    plan = _plan(
        {"id": "da", "op": "ref_axis", "intent": "i", "define": {"how": "two_points", "points": [[0, 0, 0], [0, 0, 5]]}}
    )
    adapter = _fake([{"operation_id": "da", "kind": "wrong_datum_pose"}])
    _, trace, _ = _run(plan, adapter)
    da = trace.step("da")
    assert da is not None and da.status is StepStatus.failed
    assert "datum_axis_position" in {c.name for c in da.last_level2() if c.ok is False}


def test_dangling_reference_fault_on_a_datum_sketch_retries_and_serialises_the_request() -> None:
    """A SketchRequest.on that is a DatumPlaneTarget survives model_dump, the repair path and a retry."""
    plan = _plan(*BASE, _offset("dp", XY, 20), _sketch("s1", {"kind": "plane", "from_op": "dp"}))
    adapter = _fake([{"operation_id": "s1", "attempt": 1, "kind": "dangling_reference"}])
    _, trace, _ = _run(plan, adapter)
    _assert_all_green(trace)
    s1 = trace.step("s1")
    assert s1 is not None and len(s1.attempts) == 2
    assert s1.attempts[0].request["on"]["handle"] == {"id": "datum_plane:1"}
    assert s1.attempts[0].diagnosis is not None
    assert ExecutionTrace.model_validate(trace.model_dump(mode="json")) == trace


def test_the_pre_datum_fingerprint_of_a_document_without_datums_is_unchanged() -> None:
    adapter = _fake()
    executor, _, _ = _run(_plan(*BASE), adapter)
    doc = adapter._active
    assert doc is not None
    assert "datum" not in doc.fingerprint()


# --- probe_references walks into define -----------------------------------------------------------------


TOP = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}


def test_probe_references_flattens_the_refs_nested_in_a_datum_definition() -> None:
    executor, trace, _ = _run(_plan(*BASE))
    _assert_all_green(trace)
    resolved = executor.probe_references(
        [
            _offset("dp", TOP, 5),
            {"id": "da", "op": "ref_axis", "intent": "i", "define": {"how": "entity", "entity": {**TOP}}},
            {
                "id": "mid",
                "op": "ref_plane",
                "intent": "i",
                "define": {
                    "how": "midplane",
                    "a": TOP,
                    "b": {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, -1]}},
                },
            },
        ]
    )
    assert set(resolved) == {"dp.define.base", "da.define.entity", "mid.define.a", "mid.define.b"}


def test_an_ambiguous_face_selector_in_a_datum_definition_is_reported_at_probe_time() -> None:
    executor, trace, _ = _run(_plan(*BASE))
    _assert_all_green(trace)
    ambiguous = {"kind": "face", "from_op": "base", "select": {"surface": "plane"}}
    with pytest.raises(ResolutionError) as info:
        executor.probe_references([_offset("dp", ambiguous, 5)])
    assert info.value.path == "define.base" and info.value.candidates > 1
