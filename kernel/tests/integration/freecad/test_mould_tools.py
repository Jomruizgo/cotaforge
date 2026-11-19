"""``draft_analysis`` and ``shrink_body`` against the real FreeCAD worker.

Every number is derived by hand: a wall drafted by 3 degrees measures 3 degrees; a plane is exact; a cylinder lying
across the pull has a line parallel to it (zero draft) that the sampled grid alone would miss by degrees and the
refinement finds; a ledge over a wall shadows the wall (an undercut); a cavity scaled by 0.6 % has a box 0.6 % bigger
and follows its source when the source is edited.
"""

import json
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    AdapterError,
    BodyHandle,
    DraftAnalysisRequest,
    EditFeatureRequest,
    ErrorCode,
    MassRequest,
    ShrinkBodyRequest,
)
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification.verifier import Verifier
from tests.integration.freecad.worker_tools import call_hook

pytestmark = pytest.mark.freecad

PULL_Z = (0.0, 0.0, 1.0)
XY = {"kind": "plane", "name": "XY"}
BOX = {"id": "block", "op": "box", "intent": "block", "size": [40, 30, 20]}
SIDES = [
    {"kind": "face", "from_op": "block", "select": {"normal": n}}
    for n in ([1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0])
]


def _run(adapter: FreeCADAdapter, *operations: dict[str, Any]) -> tuple[Executor, ExecutionTrace]:
    plan = parse_plan({"id": "mould", "kind": "part", "operations": list(operations)})
    executor = Executor(plan, adapter, Verifier(), ExecutorConfig())
    executor.start()
    return executor, executor.run_to_end()


def _green(trace: ExecutionTrace) -> None:
    detail = [
        (s.op_id, s.status.value, s.plan_error, s.attempts[-1].error if s.attempts else None) for s in trace.steps
    ]
    assert trace.final_state is ExecutorState.completed, detail
    assert all(s.status is StepStatus.success for s in trace.steps), detail


def _analyse(adapter: FreeCADAdapter, minimum: float = 1.0, pull: tuple[float, float, float] = PULL_Z) -> Any:
    return adapter.draft_analysis(DraftAnalysisRequest(operation_id="d", pull=pull, min_draft_deg=minimum))


def _by_status(result: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for face in result.faces:
        out[face.status] = out.get(face.status, 0) + 1
    return out


# --- draft_analysis ---------------------------------------------------------------------------------------------------


def test_an_undrafted_box_has_four_walls_without_draft_and_two_faces_looking_along_the_pull(
    adapter: FreeCADAdapter,
) -> None:
    _green(_run(adapter, BOX)[1])
    result = _analyse(adapter)
    assert _by_status(result) == {"zero": 4, "ok": 2}
    assert all(f.exact and f.surface == "plane" for f in result.faces)
    assert sorted(round(f.draft_deg, 6) for f in result.faces) == [0.0] * 4 + [90.0] * 2
    assert result.shadow_tested and "exact" in result.method


def test_walls_drafted_by_three_degrees_measure_three_degrees(adapter: FreeCADAdapter) -> None:
    taper = {
        "id": "taper",
        "op": "draft",
        "intent": "let the mould open",
        "faces": SIDES,
        "neutral": XY,
        "pull": "Z",
        "angle_deg": 3.0,
    }
    _green(_run(adapter, BOX, taper)[1])
    result = _analyse(adapter, minimum=2.0)
    walls = [f for f in result.faces if f.draft_deg < 45]
    assert len(walls) == 4 and all(abs(f.draft_deg - 3.0) < 1e-6 and f.status == "ok" for f in walls)
    assert _by_status(_analyse(adapter, minimum=3.5)) == {"low": 4, "ok": 2}
    assert _by_status(_analyse(adapter, minimum=3.0)) == {"ok": 6}  # drafted to exactly the minimum meets it


def test_the_opposite_sense_of_the_pull_gives_the_same_draft(adapter: FreeCADAdapter) -> None:
    _green(_run(adapter, BOX)[1])
    assert _by_status(_analyse(adapter, pull=(0.0, 0.0, -1.0))) == {"zero": 4, "ok": 2}


def test_a_cylinder_across_the_pull_has_zero_draft_the_grid_alone_would_miss(adapter: FreeCADAdapter) -> None:
    """A bar along X pulled along Z: the surface turns through every angle, so a line of it runs parallel to the pull.
    The 48-step grid lands up to 3.75 degrees from that line; the refinement must land on it."""
    bar = {
        "id": "bar",
        "op": "cylinder",
        "intent": "bar",
        "diameter_mm": 10,
        "height_mm": 40,
        "axis": "X",
    }
    _green(_run(adapter, bar)[1])
    side = next(f for f in _analyse(adapter).faces if f.surface == "cylinder")
    assert side.exact is False and side.draft_deg < 0.01 and side.status == "zero"


def test_a_ledge_over_a_wall_makes_that_wall_an_undercut_and_the_wall_beside_it_is_not(adapter: FreeCADAdapter) -> None:
    post = {"id": "post", "op": "box", "intent": "post", "size": [20, 20, 20]}
    ledge = {
        "id": "ledge",
        "op": "box",
        "intent": "ledge over the +X side of the post",
        "size": [40, 20, 5],
        "placement": {"origin": [0, 0, 10]},
    }
    union = {
        "id": "both",
        "op": "boolean_union",
        "intent": "one part",
        "target": {"kind": "body", "from_op": "post"},
        "tools": [{"kind": "body", "from_op": "ledge"}],
    }
    _green(_run(adapter, post, ledge, union)[1])
    result = _analyse(adapter)
    shadowed = [f for f in result.faces if f.status == "undercut"]
    # only the post's +X wall below the ledge cannot be reached along the pull; its -X wall is the part's own flank
    assert len(shadowed) == 1
    assert shadowed[0].surface == "plane" and shadowed[0].shadowed == pytest.approx(1.0)
    assert shadowed[0].area_mm2 == pytest.approx(20 * 10)  # 20 wide, the 10 mm below the ledge


def test_a_hole_across_the_pull_is_an_undercut(adapter: FreeCADAdapter) -> None:
    hole = {
        "id": "cross",
        "op": "boolean_subtract",
        "intent": "cross hole",
        "target": {"kind": "body", "from_op": "block"},
        "tools": [{"kind": "body", "from_op": "pin"}],
    }
    pin = {
        "id": "pin",
        "op": "cylinder",
        "intent": "tool",
        "diameter_mm": 6,
        "height_mm": 50,
        "axis": "X",
        "origin": [-5, 15, 10],
    }
    _green(_run(adapter, BOX, pin, hole)[1])
    wall = next(f for f in _analyse(adapter).faces if f.surface == "cylinder")
    assert wall.status == "undercut" and wall.shadowed > 0.9


def test_an_unknown_body_is_refused(adapter: FreeCADAdapter) -> None:
    _green(_run(adapter, BOX)[1])
    with pytest.raises(AdapterError) as failure:
        adapter.draft_analysis(DraftAnalysisRequest(operation_id="d", body=BodyHandle(id="body:99"), pull=PULL_Z))
    assert failure.value.info.code in (ErrorCode.invalid_reference, ErrorCode.invalid_request)


# --- shrink_body ---


def _cavity(percent: Any, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "id": "cavity",
        "op": "shrink_body",
        "intent": "cavity of the block",
        "source": {"kind": "body", "from_op": "block"},
        "shrinkage_percent": percent,
        "factor_source": "test value (made up)",
        **(extra or {}),
    }


def _box_of(adapter: FreeCADAdapter, body: str) -> Any:
    return adapter.bounding_box(MassRequest(operation_id="b", body=BodyHandle(id=body)))


def test_an_equal_shrinkage_scales_the_box_and_the_volume_exactly_and_keeps_the_surfaces(
    adapter: FreeCADAdapter,
) -> None:
    _, trace = _run(adapter, BOX, _cavity(0.6))
    _green(trace)
    box = _box_of(adapter, "body:2")
    assert box.max == pytest.approx((40 * 1.006, 30 * 1.006, 20 * 1.006), rel=1e-9)
    mass = adapter.mass_properties(MassRequest(operation_id="m", body=BodyHandle(id="body:2")))
    assert mass.volume_mm3 == pytest.approx(24000 * 1.006**3, rel=1e-9)
    # the source is untouched and the document holds both
    assert _box_of(adapter, "body:1").max == pytest.approx((40, 30, 20))


def test_a_shrinkage_per_axis_scales_each_axis_on_its_own_within_a_hundredth_of_a_percent(
    adapter: FreeCADAdapter,
) -> None:
    _, trace = _run(adapter, BOX, _cavity([0.3, 0.9, 0.6]))
    _green(trace)
    box = _box_of(adapter, "body:2")
    for got, size, factor in zip(box.max, (40, 30, 20), (1.003, 1.009, 1.006), strict=True):
        assert abs(got / size - factor) < 1e-4 * factor


def _edit_in_place(adapter: FreeCADAdapter, plan: dict[str, Any], changed: dict[str, Any], who: str) -> Any:
    executor = Executor(parse_plan(plan), adapter, Verifier(), ExecutorConfig())
    executor.start()
    _green(executor.run_to_end())
    record = executor.edit_in_place(parse_plan(changed), [who], by="test", reason="the part changed")
    assert record.status == "applied", (record.failure, record.result.flags if record.result else None)
    assert executor.trace().reverts == ()  # nothing was undone and run again: the cavity was recomputed by the CAD
    return executor


def test_the_cavity_is_linked_it_follows_an_edit_of_a_primitive_part_in_place(adapter: FreeCADAdapter) -> None:
    plan = {"id": "linked", "kind": "part", "operations": [BOX, _cavity(1.0)]}
    longer = {"id": "linked", "kind": "part", "operations": [{**BOX, "size": [40, 30, 30]}, _cavity(1.0)]}
    _edit_in_place(adapter, plan, longer, "block")
    assert _box_of(adapter, "body:2").max == pytest.approx((40.4, 30.3, 30.3), rel=1e-9)


def test_the_cavity_is_linked_it_follows_an_edit_of_a_pad_length_in_place(adapter: FreeCADAdapter) -> None:
    sketch = {
        "id": "s1",
        "op": "sketch",
        "intent": "footprint",
        "on": XY,
        "entities": [{"kind": "rect", "name": "r", "center": [20, 15], "width": 40, "height": 30}],
    }
    pad = {"id": "pad", "op": "extrude", "intent": "pad", "sketch": "s1", "depth_mm": 20}
    cavity = _cavity(1.0, {"source": {"kind": "body", "from_op": "pad"}})
    plan = {"id": "linked", "kind": "part", "operations": [sketch, pad, cavity]}
    longer = {"id": "linked", "kind": "part", "operations": [sketch, {**pad, "depth_mm": 30}, cavity]}
    _edit_in_place(adapter, plan, longer, "pad")
    assert _box_of(adapter, "body:2").max[2] == pytest.approx(30 * 1.01, rel=1e-9)


def test_a_scale_that_is_not_a_scale_is_refused(adapter: FreeCADAdapter) -> None:
    _green(_run(adapter, BOX)[1])
    with pytest.raises(Exception):  # noqa: B017, PT011 - the request model refuses it before any adapter call
        ShrinkBodyRequest(operation_id="x", source=BodyHandle(id="body:1"), scale=(1.0, 0.0, 1.0))


def test_the_recorded_intent_names_the_factor_and_its_source(adapter: FreeCADAdapter) -> None:
    executor, trace = _run(adapter, BOX, _cavity(0.6))
    _green(trace)
    dumped = json.dumps([op.model_dump(mode="json") for op in executor.plan.operations])
    assert "shrinkage 0.6 % isotropic (source: test value (made up))" in dumped


def test_edit_feature_request_model_knows_the_shrink_kind() -> None:
    from cotaforge.contract.edit import REQUEST_BY_KIND

    assert REQUEST_BY_KIND["shrink_body"] is ShrinkBodyRequest
    assert EditFeatureRequest is not None and call_hook is not None
