"""What an adapter's own operations needed beyond a step that makes a feature: a read that is not a step, a free-text
answer, a step with no feature that can still be undone, a component reference, and a mass the CAD measured."""

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import (
    BodyInfo,
    ComponentHandle,
    ConnectRequest,
    Empty,
    EndCondition,
    ExtensionRequest,
    FaceHandle,
    HoleRequest,
    HoleStandard,
    MassProperties,
    OperationResult,
    OperationSpec,
    UndoResult,
)
from cotaforge.executor.errors import ExecutorStateError, ResolutionError
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import StepStatus
from cotaforge.ir import ComponentRef, ExtensionOperation, FaceRef, PlanError, parse_plan
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


class Peek(BaseModel):
    """Reads something."""

    face: FaceRef | None = None
    component: ComponentRef | None = None


class Mark(BaseModel):
    """Changes the model without making a feature."""

    label: str = "m"


class Use(BaseModel):
    """Takes a component."""

    component: ComponentRef


class Seat(BaseModel):
    """Drills and says a component was made."""

    face: FaceRef


class ReadFake(FakeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.seen: list[ExtensionRequest] = []
        self.marks: list[str] = []
        self.undo_marks = 0
        self.undo_lasts = 0

    def extra_operations(self) -> tuple[OperationSpec, ...]:
        return (
            OperationSpec(name="peek", params=Peek, purpose="reads", guide="", method="peek", read_only=True),
            OperationSpec(
                name="mark",
                params=Mark,
                purpose="marks",
                guide="",
                method="mark",
                makes_feature=False,
                undo_method="unmark",
            ),
            OperationSpec(
                name="bare", params=Mark, purpose="marks, no undo", guide="", method="mark", makes_feature=False
            ),
            OperationSpec(name="seat", params=Seat, purpose="drills, makes a component", guide="", method="seat"),
            OperationSpec(name="use", params=Use, purpose="takes one", guide="", method="use", makes_feature=False),
        )

    def peek(self, request: ExtensionRequest) -> OperationResult:
        self.seen.append(request)
        return OperationResult(operation_id=request.operation_id, feature=None, rebuild_ok=True, text="one\ntwo")

    def mark(self, request: ExtensionRequest) -> OperationResult:
        self.marks.append(request.params["label"])
        return OperationResult(operation_id=request.operation_id, feature=None, rebuild_ok=True)

    def unmark(self, request: Empty) -> UndoResult:
        self.undo_marks += 1
        self.marks.pop()
        return UndoResult(reverted=None)

    def undo_last(self, request: Empty) -> UndoResult:
        self.undo_lasts += 1
        return super().undo_last(request)

    def use(self, request: ExtensionRequest) -> OperationResult:
        self.seen.append(request)
        return OperationResult(operation_id=request.operation_id, feature=None, rebuild_ok=True)

    def seat(self, request: ExtensionRequest) -> OperationResult:
        made = self.hole(
            HoleRequest(
                operation_id=request.operation_id,
                face=FaceHandle(**request.params["face"]),
                position=(-40.0, -20.0),
                standard=HoleStandard.simple,
                diameter_mm=6.0,
                end_condition=EndCondition.blind,
                depth_mm=3.0,
            )
        )
        return made.model_copy(update={"component": ComponentHandle(id="comp:7")})


def executor_over(adapter: FakeAdapter) -> Executor:
    adapter.connect(ConnectRequest(operation_id="connect"))
    executor = Executor(parse_plan(PLATE), adapter, Verifier())
    executor.start()
    while executor.state.value == "running":
        assert executor.next_operation().status is StepStatus.success
    return executor


def ext(op_id: str, name: str, **params: Any) -> dict[str, Any]:
    return {"id": op_id, "op": f"ext:{name}", "intent": "test", "params": params}


# --- reads ---------------------------------------------------------------------------------------------------------


def test_a_read_runs_now_and_is_not_a_step() -> None:
    adapter = ReadFake()
    executor = executor_over(adapter)
    steps, operations = len(executor.trace().steps), len(executor.plan.operations)
    result = executor.read_extension(ExtensionOperation.model_validate(ext("read_1", "peek", face=TOP)))
    assert result.text == "one\ntwo"
    (request,) = adapter.seen
    assert request.params["face"]["id"].startswith("face:")  # resolved like the references of a step
    assert (len(executor.trace().steps), len(executor.plan.operations)) == (steps, operations)
    assert executor.undo_to("base") == ()  # nothing of it to take back


def test_only_a_read_the_adapter_declared_is_run_as_one() -> None:
    executor = executor_over(ReadFake())
    with pytest.raises(ExecutorStateError, match="not a read-only operation"):
        executor.read_extension(ExtensionOperation.model_validate(ext("read_1", "mark", label="x")))
    with pytest.raises(ExecutorStateError, match="not a read-only operation"):
        executor.read_extension(ExtensionOperation.model_validate(ext("read_1", "nothing")))


def test_a_read_whose_reference_names_nothing_fails_with_the_reference() -> None:
    adapter = ReadFake()
    executor = executor_over(adapter)
    wrong = {"kind": "face", "from_op": "later", "select": {"normal": [0, 0, 1]}}
    with pytest.raises(ResolutionError):
        executor.read_extension(ExtensionOperation.model_validate(ext("read_1", "peek", face=wrong)))
    assert adapter.seen == []


def test_a_read_cannot_have_an_undo_method() -> None:
    with pytest.raises(ValueError, match="only reads"):
        OperationSpec(name="x", params=Peek, purpose="p", guide="", method="m", read_only=True, undo_method="u")


# --- undo of a step with no feature --------------------------------------------------------------------------------


def test_a_step_with_no_feature_is_undone_through_the_method_the_adapter_gives() -> None:
    adapter = ReadFake()
    executor = executor_over(adapter)
    executor.extend([ext("tagged", "mark", label="a")])
    assert executor.next_operation().status is StepStatus.success
    assert adapter.marks == ["a"]
    assert executor.undo_to("base") == ("tagged",)
    assert (adapter.marks, adapter.undo_marks, adapter.undo_lasts) == ([], 1, 0)


def test_a_step_with_no_feature_and_no_undo_method_is_booked_without_a_call_to_the_adapter() -> None:
    adapter = ReadFake()
    executor = executor_over(adapter)
    executor.extend([ext("plain", "bare", label="a")])
    assert executor.next_operation().status is StepStatus.success
    assert executor.undo_to("base") == ("plain",)
    assert (adapter.undo_marks, adapter.undo_lasts) == (0, 0)


def test_a_step_that_made_a_feature_still_goes_through_undo_last() -> None:
    adapter = ReadFake()
    executor = executor_over(adapter)
    executor.extend([ext("drilled", "seat", face=TOP)])
    assert executor.next_operation().status is StepStatus.success
    assert executor.undo_to("base") == ("drilled",)
    assert (adapter.undo_marks, adapter.undo_lasts) == (0, 1)


def test_an_undo_method_that_names_another_feature_degrades_like_a_mismatch() -> None:
    from cotaforge.contract import FeatureHandle
    from cotaforge.executor.errors import UndoInterruptedError

    class Lying(ReadFake):
        def unmark(self, request: Empty) -> UndoResult:
            return UndoResult(reverted=FeatureHandle(id="feature:other"))

    executor = executor_over(Lying())
    executor.extend([ext("tagged", "mark", label="a")])
    executor.next_operation()
    with pytest.raises(UndoInterruptedError):
        executor.undo_to("base")


# --- component references ------------------------------------------------------------------------------------------


def test_a_component_reference_arrives_as_the_json_of_its_handle() -> None:
    adapter = ReadFake()
    executor = executor_over(adapter)
    executor.extend([ext("drilled", "seat", face=TOP)])
    assert executor.next_operation().status is StepStatus.success
    executor.extend([ext("taken", "use", component={"kind": "component", "from_op": "drilled"})])
    record = executor.next_operation()
    assert record.status is StepStatus.success, record.plan_error
    assert adapter.seen[-1].params["component"] == {"id": "comp:7"}


def test_a_component_reference_to_a_step_that_was_undone_is_not_sent_to_the_cad() -> None:
    adapter = ReadFake()
    executor = executor_over(adapter)
    executor.extend([ext("drilled", "seat", face=TOP)])
    executor.next_operation()
    _, resolver = executor._require_started()  # noqa: SLF001
    assert resolver.component(ComponentRef(from_op="drilled"), "taken", "component").id == "comp:7"
    executor.undo_to("base")
    with pytest.raises(ResolutionError, match="produced no component"):
        resolver.component(ComponentRef(from_op="drilled"), "taken", "component")
    # and the plan no longer has the step: the reference is refused before it gets that far
    with pytest.raises(PlanError):
        executor.extend([ext("taken", "use", component={"kind": "component", "from_op": "drilled"})])
    assert adapter.seen == []


def test_the_plan_rules_see_a_component_reference() -> None:
    operation = ExtensionOperation.model_validate(ext("taken", "use", component={"kind": "component", "from_op": "x"}))
    assert [type(ref) for ref in operation.refs()] == [ComponentRef]
    with pytest.raises(PlanError) as caught:
        parse_plan({**PLATE, "operations": [*PLATE["operations"], operation.model_dump(mode="json")]})
    assert caught.value.rule == "V2"
    wrong = ext("taken", "use", component={"kind": "component", "from_op": "base_sketch"})
    with pytest.raises(PlanError) as caught:
        parse_plan({**PLATE, "operations": [*PLATE["operations"], wrong]})
    assert caught.value.rule == "V3"
    fine = ext("taken", "use", component={"kind": "component", "from_op": "base"})  # a feature, not a component...
    with pytest.raises(PlanError) as caught:
        parse_plan({**PLATE, "operations": [*PLATE["operations"], fine]})
    assert caught.value.rule == "V3"


# --- a mass the CAD measured ---------------------------------------------------------------------------------------


def test_a_mass_carries_its_source_and_nothing_else_changes_when_it_is_absent() -> None:
    plain = MassProperties(volume_mm3=1.0, centroid=(0, 0, 0))
    assert plain.mass_kg is None and plain.mass_source == ""
    measured = MassProperties(volume_mm3=1.0, centroid=(0, 0, 0), mass_kg=2.5, mass_source="ExternalCAD")
    assert measured.mass_kg == 2.5
    with pytest.raises(ValidationError, match="mass_source"):
        MassProperties(volume_mm3=1.0, centroid=(0, 0, 0), mass_kg=2.5)
    with pytest.raises(ValidationError):
        MassProperties(volume_mm3=1.0, centroid=(0, 0, 0), mass_kg=-1.0, mass_source="x")
    with pytest.raises(ValidationError):
        MassProperties(volume_mm3=1.0, centroid=(0, 0, 0), mass_kg=float("nan"), mass_source="x")


def test_a_body_mass_needs_its_source_too() -> None:
    fields = {"handle": {"id": "body:1"}, "volume_mm3": 1.0, "bbox": {"min": (0, 0, 0), "max": (1, 1, 1)}}
    fields |= {"face_count": 6, "provenance": {"created_by": {"id": "feature:1"}}}
    assert BodyInfo.model_validate(fields).mass_kg is None
    with pytest.raises(ValidationError, match="mass_source"):
        BodyInfo.model_validate({**fields, "mass_kg": 1.0})
    assert BodyInfo.model_validate({**fields, "mass_kg": 1.0, "mass_source": "ExternalCAD"}).mass_kg == 1.0
