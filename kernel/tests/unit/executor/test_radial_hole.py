"""The executor on a hole across a tube wall (position = [axial, angle]) and on a tapped hole, on the StubAdapter.

The stub answers like a kernel that cut the exact radial hole 1e-3 relative short (what OCC does on cylinder-cylinder
cuts): the asserted rows (axis, radius, bound) must hold and the reported volume must not matter.
"""

import math
from typing import Any

import pytest

from cotaforge.contract import (
    Axis,
    BodyHandle,
    BoundingBox,
    FaceHandle,
    FeatureHandle,
    HoleRequest,
    MassProperties,
    OperationResult,
)
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification.formulas import radial_hole_volume
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier
from tests.unit.verification.helpers import cylindrical_face, planar_face

from .stub import Step, StubAdapter

TUBE_BBOX = BoundingBox(min=(-20.0, -20.0, 0.0), max=(20.0, 20.0, 100.0))
TUBE_VOLUME = math.pi * 20.0**2 * 100.0
WALL_VOLUME = radial_hole_volume(20.0, 0.0, 3.0, 8.0)


def _plan(position: list[float], *, standard: str = "simple", **extra: Any) -> dict[str, Any]:
    hole: dict[str, Any] = {
        "id": "cross",
        "op": "hole",
        "intent": "cross hole through the tube wall",
        "face": {"kind": "face", "from_op": "tube", "select": {"surface": "cylinder", "radius": 20}},
        "position": position,
        "standard": standard,
        "end_condition": "blind",
        "depth_mm": 8,
        **extra,
    }
    if standard == "simple":
        hole["diameter_mm"] = 6.0
    return {
        "id": "tube_plan",
        "kind": "part",
        "operations": [
            {"id": "tube", "op": "cylinder", "intent": "tube blank", "diameter_mm": 40, "height_mm": 100},
            hole,
        ],
    }


def _stub(
    *, wall_axis: tuple[float, float, float] = (1.0, 0.0, 0.0), removed: float = WALL_VOLUME * 0.999
) -> StubAdapter:
    stub = StubAdapter()
    stub.add_face(
        cylindrical_face(
            "face:tube",
            Axis(origin=(0.0, 0.0, 50.0), direction=(0.0, 0.0, 1.0)),
            20.0,
            (0.0, 0.0, 50.0),
            TUBE_BBOX,
            created_by="feature:1",
        ),
        feature_id="feature:1",
    )
    for cap, z in (("face:cap_top", 100.0), ("face:cap_bottom", 0.0)):
        stub.add_face(
            planar_face(
                cap,
                (0.0, 0.0, 1.0 if z else -1.0),
                (0.0, 0.0, z),
                math.pi * 400.0,
                BoundingBox(min=(-20.0, -20.0, z), max=(20.0, 20.0, z)),
                created_by="feature:1",
            ),
            feature_id="feature:1",
        )
    tube = stub.faces["face:tube"]
    stub.faces["face:tube"] = tube.model_copy(
        update={"provenance": tube.provenance.model_copy(update={"modified_by": (FeatureHandle(id="feature:2"),)})}
    )
    stub.faces_of_feature["feature:1"] = [stub.faces[f.handle.id] for f in stub.faces_of_feature["feature:1"]]
    stub.add_face(
        cylindrical_face(
            "face:wall",
            Axis(origin=(14.0, 0.0, 60.0), direction=wall_axis),
            3.0,
            (16.0, 0.0, 60.0),
            BoundingBox(min=(12.0, -3.0, 57.0), max=(20.0, 3.0, 63.0)),
            created_by="feature:2",
        ),
        feature_id="feature:2",
    )
    stub.faces_of_feature["feature:2"].append(stub.faces["face:tube"])
    stub.script(
        "tube",
        Step(
            result=OperationResult(
                operation_id="tube",
                feature=FeatureHandle(id="feature:1"),
                rebuild_ok=True,
                created_faces=tuple(FaceHandle(id=name) for name in ("face:tube", "face:cap_top", "face:cap_bottom")),
                created_bodies=(BodyHandle(id="body:1"),),
            ),
            mass_after=MassProperties(volume_mm3=TUBE_VOLUME, centroid=(0.0, 0.0, 50.0)),
            bbox_after=TUBE_BBOX,
        ),
    )
    stub.script(
        "cross",
        Step(
            result=OperationResult(
                operation_id="cross",
                feature=FeatureHandle(id="feature:2"),
                rebuild_ok=True,
                created_faces=(FaceHandle(id="face:wall"),),
                modified_faces=(FaceHandle(id="face:tube"),),
            ),
            mass_after=MassProperties(volume_mm3=TUBE_VOLUME - removed, centroid=(0.0, 0.0, 50.0)),
            bbox_after=TUBE_BBOX,
        ),
    )
    return stub


def _run(plan: dict[str, Any], stub: StubAdapter) -> ExecutionTrace:
    executor = Executor(parse_plan(plan), stub, Verifier(), ExecutorConfig(max_retries=0))
    executor.start()
    return executor.run_to_end()


def _asserted_ok(trace: ExecutionTrace, op_id: str) -> set[str]:
    step = trace.step(op_id)
    assert step is not None
    return {c.name for c in step.last_level2() if c.kind == CheckKind.asserted and c.ok}


def _failed(trace: ExecutionTrace, op_id: str) -> set[str]:
    step = trace.step(op_id)
    assert step is not None and step.attempts
    return {c.name for c in step.last_level2() if c.ok is False}


def test_a_radial_hole_runs_green_and_the_request_carries_the_angular_position() -> None:
    stub = _stub()
    trace = _run(_plan([10, 0]), stub)
    assert trace.final_state is ExecutorState.completed
    request = stub.calls_to("hole")[0]
    assert isinstance(request, HoleRequest)
    assert request.face == FaceHandle(id="face:tube") and request.position == (10.0, 0.0)
    assert {
        "hole_axis_direction",
        "hole_axis_position",
        "hole_radius",
        "hole_entry_face_modified",
        "bbox_within",
        "cut_volume_bound",
    } <= _asserted_ok(trace, "cross")
    step = trace.step("cross")
    assert step is not None
    volume = next(c for c in step.last_level2() if c.name == "volume_delta")
    assert volume.kind is CheckKind.reported and volume.ok is None  # never blocks, whatever the kernel measured


def test_a_hole_whose_cylinder_points_the_wrong_way_fails_level_2() -> None:
    trace = _run(_plan([10, 0]), _stub(wall_axis=(0.0, 1.0, 0.0)))
    assert trace.final_state is ExecutorState.failed
    assert trace.step("cross").status is StepStatus.failed  # type: ignore[union-attr]
    assert "hole_axis_direction" in _failed(trace, "cross")


def test_a_radial_hole_that_removed_nothing_fails_the_volume_bound() -> None:
    trace = _run(_plan([10, 0]), _stub(removed=0.0))
    assert trace.final_state is ExecutorState.failed
    assert "cut_volume_bound" in _failed(trace, "cross")


def test_a_radial_hole_that_removed_more_than_its_disc_can_sweep_fails_the_volume_bound() -> None:
    trace = _run(_plan([10, 0]), _stub(removed=math.pi * 9.0 * 8.0 * 1.5))
    assert "cut_volume_bound" in _failed(trace, "cross")


def test_a_hole_off_the_requested_axis_point_fails_hole_axis_position() -> None:
    """The plan asks for the hole 10 mm up the tube, but the kernel drilled 20 mm up."""
    stub = _stub()
    wall = stub.faces["face:wall"]
    stub.faces["face:wall"] = wall.model_copy(
        update={"axis": Axis(origin=(14.0, 0.0, 70.0), direction=(1.0, 0.0, 0.0))}
    )
    stub.faces_of_feature["feature:2"][0] = stub.faces["face:wall"]
    assert "hole_axis_position" in _failed(_run(_plan([10, 0]), stub), "cross")


def test_a_tapped_radial_hole_derives_the_tap_drill_and_carries_the_thread() -> None:
    plan = _plan([10, 0], standard="iso_tapped", size="M6", thread_depth_mm=6)
    stub = _stub(removed=radial_hole_volume(20.0, 0.0, 2.5, 8.0) * 0.999)
    stub.faces["face:wall"] = stub.faces["face:wall"].model_copy(update={"radius": 2.5})
    stub.faces_of_feature["feature:2"][0] = stub.faces["face:wall"]
    trace = _run(plan, stub)
    request = stub.calls_to("hole")[0]
    assert isinstance(request, HoleRequest)
    assert request.diameter_mm == 5.0 and request.thread is not None and request.thread.size == "M6"
    assert trace.final_state is ExecutorState.completed
    step = trace.step("cross")
    assert step is not None
    meta = next(c for c in step.last_level2() if c.name == "hole_thread_metadata")
    assert meta.kind is CheckKind.reported and meta.ok is None


def test_a_hole_on_a_face_that_is_not_the_tube_is_a_plan_error_before_the_adapter() -> None:
    plan = _plan([10, 0])
    plan["operations"][1]["face"]["select"] = {"surface": "cylinder", "radius": 99}
    stub = _stub()
    trace = _run(plan, stub)
    step = trace.step("cross")
    assert step is not None and step.status is StepStatus.plan_error
    assert not stub.calls_to("hole")


def test_the_axial_position_and_the_angle_reach_the_adapter_unchanged() -> None:
    stub = _stub()
    _run(_plan([-12.5, 135]), stub)
    request = stub.calls_to("hole")[0]
    assert isinstance(request, HoleRequest) and request.position == (-12.5, 135.0)
    assert request.diameter_mm == pytest.approx(6.0)
