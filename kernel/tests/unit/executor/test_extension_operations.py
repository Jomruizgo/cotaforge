"""The operations an adapter declares: parsed, kept and re-parsed by the plan, built by the executor against the
adapter's own model, run through the adapter method it names, undone like any other step."""

from typing import Any

import pytest
from pydantic import BaseModel, Field

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import (
    ConnectRequest,
    DatumPlaneRequest,
    DatumPlaneResult,
    EditFeatureRequest,
    EditResult,
    EndCondition,
    ExtensionRequest,
    FaceHandle,
    HoleRequest,
    HoleStandard,
    OperationSpec,
    PlaneName,
)
from cotaforge.contract.geometry import frame_for_plane
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import StepStatus
from cotaforge.ir import ExtensionOperation, FaceRef, PlanError, parse_plan
from cotaforge.verification.verifier import Verifier

PLATE = {
    "id": "plate",
    "kind": "part",
    "operations": [
        {
            "id": "base_sketch",
            "op": "sketch",
            "intent": "outline",
            "on": {"kind": "plane", "name": "XY"},
            "entities": [{"kind": "rect", "name": "outline", "center": [50, 30], "width": 100, "height": 60}],
        },
        {"id": "base", "op": "extrude", "intent": "thickness", "sketch": "base_sketch", "depth_mm": 8},
    ],
}
TOP = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}


class Drill(BaseModel):
    """A hole made by the CAD's own wizard."""

    face: FaceRef
    position: tuple[float, float]
    diameter_mm: float = Field(gt=0)


def drill_op(op_id: str = "drilled", **params: Any) -> dict[str, Any]:
    arguments = {"face": TOP, "position": [-40, -20], "diameter_mm": 6.0, **params}
    return {"id": op_id, "op": "ext:drill", "intent": "wizard hole", "params": arguments}


class DrillFake(FakeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.requests: list[ExtensionRequest] = []

    def extra_operations(self) -> tuple[OperationSpec, ...]:
        return (OperationSpec(name="drill", params=Drill, purpose="wizard hole", guide="", method="drill"),)

    def drill(self, request: ExtensionRequest) -> Any:
        self.requests.append(request)
        return self.hole(
            HoleRequest(
                operation_id=request.operation_id,
                face=FaceHandle(**request.params["face"]),
                position=tuple(request.params["position"]),
                standard=HoleStandard.simple,
                diameter_mm=request.params["diameter_mm"],
                end_condition=EndCondition.blind,
                depth_mm=3.0,
            )
        )


def executor_over(adapter: FakeAdapter, plan: dict[str, Any] = PLATE) -> Executor:
    adapter.connect(ConnectRequest(operation_id="connect"))
    executor = Executor(parse_plan(plan), adapter, Verifier())
    executor.start()
    while executor.state.value == "running":
        assert executor.next_operation().status is StepStatus.success
    return executor


# --- the plan --------------------------------------------------------------------------------------------------------


def test_the_plan_keeps_an_adapter_operation_through_a_dump_and_a_parse() -> None:
    plan = parse_plan({**PLATE, "operations": [*PLATE["operations"], drill_op()]})
    operation = plan.operations[-1]
    assert isinstance(operation, ExtensionOperation)
    assert operation.op == "ext:drill"
    dumped = plan.model_dump(mode="json")
    assert dumped["operations"][-1] == {**drill_op(), "links": []}
    assert parse_plan(dumped) == plan
    assert isinstance(plan.model_dump(mode="python")["operations"], tuple)


def test_the_references_in_its_params_are_seen_by_the_plan_rules() -> None:
    operation = ExtensionOperation.model_validate(drill_op())
    assert [type(ref) for ref in operation.refs()] == [FaceRef]
    too_early = drill_op(face={"kind": "face", "from_op": "later", "select": {"normal": [0, 0, 1]}})
    with pytest.raises(PlanError) as caught:
        parse_plan({**PLATE, "operations": [*PLATE["operations"], too_early]})
    assert caught.value.rule == "V2"
    sketch_as_face = drill_op(face={"kind": "face", "from_op": "base_sketch", "select": {"normal": [0, 0, 1]}})
    with pytest.raises(PlanError) as caught:
        parse_plan({**PLATE, "operations": [*PLATE["operations"], sketch_as_face]})
    assert caught.value.rule == "V3"


def test_a_dict_that_only_looks_like_a_reference_is_left_to_the_adapters_model() -> None:
    odd = ExtensionOperation.model_validate(drill_op(note={"kind": "edge", "colour": "red"}))
    assert [type(ref) for ref in odd.refs()] == [FaceRef]


def test_an_adapter_operation_never_passes_for_a_common_one() -> None:
    clash = {"id": "x", "op": "ext:hole", "intent": "own hole", "params": {}}
    plan = parse_plan({**PLATE, "operations": [*PLATE["operations"], clash]})
    assert plan.operations[-1].op == "ext:hole"  # no common validator (V12) reads its fields
    with pytest.raises(PlanError):
        parse_plan({**PLATE, "operations": [*PLATE["operations"], {"id": "y", "op": "ext:Bad Name", "intent": "x"}]})
    with pytest.raises(PlanError):
        parse_plan({**PLATE, "operations": [*PLATE["operations"], {"id": "y", "op": "nothing", "intent": "x"}]})


# --- the executor ----------------------------------------------------------------------------------------------------


def test_the_executor_runs_the_adapter_method_with_the_references_resolved() -> None:
    adapter = DrillFake()
    executor = executor_over(adapter)
    executor.extend([drill_op()])
    record = executor.next_operation()
    assert record.status is StepStatus.success, record.plan_error
    assert record.op == "ext:drill"
    (request,) = adapter.requests
    assert request.name == "drill"
    assert request.params["face"]["id"].startswith("face:")
    assert request.params["position"] == [-40.0, -20.0]
    assert record.attempts[-1].request["params"]["diameter_mm"] == 6.0  # what ran is kept in the trace
    assert executor.context is not None and "drilled" in executor.context.feature_by_op


def test_undo_reverts_the_adapter_operation_like_any_step() -> None:
    adapter = DrillFake()
    executor = executor_over(adapter)
    volume = executor.adapter.mass_properties(_mass()).volume_mm3
    executor.extend([drill_op()])
    assert executor.next_operation().status is StepStatus.success
    assert executor.adapter.mass_properties(_mass()).volume_mm3 < volume
    assert executor.undo_to("base") == ("drilled",)
    assert executor.adapter.mass_properties(_mass()).volume_mm3 == pytest.approx(volume)


def test_a_plan_whose_operation_the_adapter_no_longer_declares_fails_that_step_with_its_name() -> None:
    adapter = FakeAdapter()
    executor = executor_over(adapter)
    executor.extend([drill_op()])
    record = executor.next_operation()
    assert record.status is StepStatus.plan_error
    assert "the adapter no longer declares the operation 'drill'" in (record.plan_error or "")


def test_arguments_that_no_longer_fit_the_model_fail_the_step_not_the_session() -> None:
    adapter = DrillFake()
    executor = executor_over(adapter)
    executor.extend([drill_op(diameter_mm=-1)])  # the plan stores it as JSON; only the adapter's model can judge it
    record = executor.next_operation()
    assert record.status is StepStatus.plan_error
    assert "drill no longer accepts its arguments (diameter_mm" in (record.plan_error or "")
    assert adapter.requests == []


class EditingDrillFake(DrillFake):
    """An adapter that answers the in-place edit of its own operation (like an external adapter's hole wizard)."""

    def __init__(self) -> None:
        super().__init__()
        self.edit_requests: list[EditFeatureRequest] = []

    def edit_feature(self, request: EditFeatureRequest) -> EditResult:
        self.edit_requests.append(request)
        return EditResult(rebuild_ok=True, applied=True, recomputed=(request.edits[0].feature,))


def _widen(executor: Executor, diameter: float) -> Any:
    data = executor.plan.model_dump(mode="python")
    data["operations"][-1]["params"]["diameter_mm"] = diameter
    return parse_plan(data)


def test_an_adapter_that_does_not_answer_the_edit_of_its_operation_refuses_it_by_name() -> None:
    adapter = DrillFake()  # the fake can edit common features, not this one: it says what the feature is
    executor = executor_over(adapter)
    executor.extend([drill_op()])
    executor.next_operation()
    record = executor.edit_in_place(_widen(executor, 8.0), ["drilled"], by="test", reason="wider")
    assert record.status == "refused"
    assert "not a drill" in (record.failure or "")
    assert executor.plan.operations[-1].params["diameter_mm"] == 6.0  # type: ignore[attr-defined]


def test_an_adapter_operation_is_edited_in_place_with_the_request_it_was_made_with() -> None:
    adapter = EditingDrillFake()
    executor = executor_over(adapter)
    executor.extend([drill_op()])
    executor.next_operation()
    record = executor.edit_in_place(_widen(executor, 8.0), ["drilled"], by="test", reason="wider")
    assert record.status == "applied", record.failure
    assert record.recomputed_ops == ("drilled",)
    assert record.changes == {"drilled": ("params.diameter_mm",)}
    (sent,) = adapter.edit_requests
    (edit,) = sent.edits
    assert edit.kind == "drill" and type(edit.request) is ExtensionRequest
    assert edit.request.name == "drill" and edit.request.operation_id == "drilled"
    assert edit.request.params["diameter_mm"] == 8.0
    assert edit.request.params["face"]["id"].startswith("face:")  # the references arrive resolved, as at the first run
    assert record.old_requests["drilled"]["params"]["diameter_mm"] == 6.0
    assert record.new_requests["drilled"]["params"]["diameter_mm"] == 8.0
    # the plan and the step stay right: undo takes it back and the step runs again with the new value
    assert executor.plan.operations[-1].params["diameter_mm"] == 8.0  # type: ignore[attr-defined]
    assert executor.undo_to("base") == ("drilled",)
    assert executor.next_operation().status is StepStatus.success
    assert adapter.requests[-1].params["diameter_mm"] == 8.0


def test_the_edit_of_an_adapter_operation_it_no_longer_declares_is_refused() -> None:
    adapter = EditingDrillFake()
    executor = executor_over(adapter)
    executor.extend([drill_op()])
    executor.next_operation()
    executor.extensions = lambda: {}  # type: ignore[method-assign]
    record = executor.edit_in_place(_widen(executor, 8.0), ["drilled"], by="test", reason="wider")
    assert record.status == "refused" and "no longer declared" in (record.failure or "")
    assert adapter.edit_requests == []


def _mass() -> Any:
    from cotaforge.contract import MassRequest

    return MassRequest(operation_id="probe")


# --- an adapter operation that returns a plane -----------------------------------------------------------------------


class Shelf(BaseModel):
    """A reference plane the CAD makes its own way."""

    height_mm: float


def shelf_op(op_id: str = "shelf", height: float = 20.0) -> dict[str, Any]:
    return {"id": op_id, "op": "ext:shelf", "intent": "plane for the boss", "params": {"height_mm": height}}


SHELF_STEPS = [
    {
        "id": "boss_sketch",
        "op": "sketch",
        "intent": "boss outline",
        "on": {"kind": "plane", "from_op": "shelf"},
        "entities": [{"kind": "circle", "name": "c", "center": [50, 30], "diameter": 20}],
    },
    {"id": "boss", "op": "extrude", "intent": "boss", "sketch": "boss_sketch", "depth_mm": 5},
]


class ShelfFake(FakeAdapter):
    """An adapter whose own operation makes a plane (like an external adapter's linked plane): no feature, a frame."""

    def extra_operations(self) -> tuple[OperationSpec, ...]:
        return (
            OperationSpec(name="shelf", params=Shelf, purpose="plane", guide="", method="shelf", makes_feature=False),
        )

    def shelf(self, request: ExtensionRequest) -> DatumPlaneResult:
        frame = frame_for_plane(PlaneName.XY)
        raised = frame.model_copy(update={"origin": (0.0, 0.0, request.params["height_mm"])})
        return self.datum_plane(DatumPlaneRequest(operation_id=request.operation_id, how="offset", frame=raised))


def _run_steps(executor: Executor) -> None:
    while executor.state.value == "running":
        record = executor.next_operation()
        assert record.status is StepStatus.success, record.plan_error or record.attempts[-1].error


def _top(executor: Executor) -> float:
    return executor.adapter.bounding_box(_mass()).max[2]


def test_a_plane_made_by_an_adapter_operation_is_accepted_by_the_plan_and_feeds_a_sketch() -> None:
    plan = parse_plan({**PLATE, "operations": [*PLATE["operations"], shelf_op(), *SHELF_STEPS]})
    assert plan.operations[2].op == "ext:shelf"
    wrong = {**SHELF_STEPS[0], "on": {"kind": "plane", "from_op": "base_sketch"}}  # a sketch is still no plane
    with pytest.raises(PlanError) as caught:
        parse_plan({**PLATE, "operations": [*PLATE["operations"], wrong]})
    assert caught.value.rule == "V3"
    executor = executor_over(ShelfFake())
    executor.extend([shelf_op(), *SHELF_STEPS])
    _run_steps(executor)
    assert executor.context is not None
    target = executor.context.datum_plane_by_op["shelf"]
    assert target.frame.origin == (0.0, 0.0, 20.0)
    assert "shelf" not in executor.context.feature_by_op
    assert _top(executor) == pytest.approx(25.0)  # the boss stands on the plane, 20 + 5


def test_the_plane_survives_an_in_place_edit_of_what_stands_on_it() -> None:
    executor = executor_over(ShelfFake())
    executor.extend([shelf_op(), *SHELF_STEPS])
    _run_steps(executor)
    data = executor.plan.model_dump(mode="python")
    data["operations"][-1]["depth_mm"] = 9
    record = executor.edit_in_place(parse_plan(data), ["boss"], by="test", reason="taller")
    assert record.status == "applied", record.failure
    assert executor.context is not None and "shelf" in executor.context.datum_plane_by_op
    assert _top(executor) == pytest.approx(29.0)
    data = executor.plan.model_dump(mode="python")
    data["operations"][2]["params"]["height_mm"] = 30.0
    refused = executor.edit_in_place(parse_plan(data), ["shelf"], by="test", reason="higher")
    assert refused.status == "refused" and "no feature" in (refused.failure or "")


def test_undo_drops_the_plane_and_a_rebuild_makes_it_again_where_the_new_plan_says() -> None:
    executor = executor_over(ShelfFake())
    executor.extend([shelf_op(), *SHELF_STEPS])
    _run_steps(executor)
    assert executor.undo_to("base") == ("boss", "boss_sketch", "shelf")
    assert executor.context is not None and executor.context.datum_plane_by_op == {}
    executor.extend([shelf_op(height=30.0), *SHELF_STEPS])
    _run_steps(executor)
    assert executor.context.datum_plane_by_op["shelf"].frame.origin == (0.0, 0.0, 30.0)
    assert _top(executor) == pytest.approx(35.0)
