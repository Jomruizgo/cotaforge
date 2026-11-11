"""What an adapter's own operations needed: a read is not a step, its text is not a warning, a step with no feature is
undone through the adapter's method, a component is named by the step that inserted it, and a mass the CAD measured is
the one the agent reads."""

from typing import Any

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import (
    BodiesResult,
    ComponentHandle,
    Empty,
    EndCondition,
    ExtensionRequest,
    FaceHandle,
    HoleRequest,
    HoleStandard,
    OperationResult,
    OperationSpec,
    UndoResult,
)
from cotaforge.ir import ComponentRef, FaceRef
from pydantic import BaseModel

from cotaforge_mcp import catalog
from tests.conftest import TOP, Caller, MakeSession, plate


class Peek(BaseModel):
    """Reads a list."""

    face: FaceRef | None = None
    component: ComponentRef | None = None


class Mark(BaseModel):
    """Changes the model without a feature."""

    label: str = "m"


class Use(BaseModel):
    """Takes a component."""

    component: ComponentRef


class Seat(BaseModel):
    """Drills and makes a component."""

    face: FaceRef


class ReadFake(FakeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.seen: list[ExtensionRequest] = []
        self.marks: list[str] = []

    def extra_operations(self) -> tuple[OperationSpec, ...]:
        return (
            OperationSpec(
                name="peek",
                params=Peek,
                purpose="list the things",
                guide="",
                method="peek",
                read_only=True,
                example={"face": TOP},
            ),
            OperationSpec(
                name="mark",
                params=Mark,
                purpose="mark",
                guide="",
                method="mark",
                makes_feature=False,
                undo_method="unmark",
            ),
            OperationSpec(name="seat", params=Seat, purpose="drill + component", guide="", method="seat"),
            OperationSpec(
                name="use", params=Use, purpose="take a component", guide="", method="use", makes_feature=False
            ),
        )

    def peek(self, request: ExtensionRequest) -> OperationResult:
        self.seen.append(request)
        return OperationResult(
            operation_id=request.operation_id,
            feature=None,
            rebuild_ok=True,
            text="item one\nitem two",
            warnings=("careful",),
        )

    def mark(self, request: ExtensionRequest) -> OperationResult:
        self.marks.append(request.params["label"])
        return OperationResult(operation_id=request.operation_id, feature=None, rebuild_ok=True, text="marked")

    def unmark(self, request: Empty) -> UndoResult:
        self.marks.pop()
        return UndoResult(reverted=None)

    def use(self, request: ExtensionRequest) -> OperationResult:
        self.seen.append(request)
        return OperationResult(operation_id=request.operation_id, feature=None, rebuild_ok=True)

    def seat(self, request: ExtensionRequest) -> OperationResult:
        made = self.hole(
            HoleRequest(
                operation_id=request.operation_id,
                face=FaceHandle(**request.params["face"]),
                position=(10.0, 10.0),
                standard=HoleStandard.simple,
                diameter_mm=6.0,
                end_condition=EndCondition.blind,
                depth_mm=3.0,
            )
        )
        return made.model_copy(update={"component": ComponentHandle(id="comp:7")})


def reader(make: MakeSession) -> tuple[Caller, ReadFake]:
    adapter = ReadFake()
    call = Caller(make(adapter))
    plate(call)
    return call, adapter


# --- reads -----------------------------------------------------------------------------------------------------------


def test_a_read_prints_its_text_as_what_it_is_and_leaves_no_step(make: MakeSession) -> None:
    call, adapter = reader(make)
    call.ok("doc", action="save")
    steps = call.ok("doc", action="status")
    reply = call.ok("op", name="peek", params={"face": TOP})
    lines = reply.splitlines()
    assert lines[0] == "ok: peek (read: not a step, the document is unchanged)"
    assert lines[1:3] == ["item one", "item two"]
    assert "warning: careful" in lines
    assert "warnings:" not in reply
    assert "item one" not in " ".join(line for line in lines if line.startswith("warning"))
    assert len(adapter.seen) == 1 and adapter.seen[0].params["face"]["id"].startswith("face:")
    assert call.ok("doc", action="status") == steps  # same steps, and the save is still current
    assert not call.session.dirty
    assert len(call.session.executor.plan.operations) == 2


def test_undo_after_a_read_takes_back_the_step_before_it_not_the_read(make: MakeSession) -> None:
    call, _ = reader(make)
    call.ok("op", name="peek", params={})
    assert call.ok("undo").startswith("undone: s2 extrude")


def test_a_read_takes_no_id_and_no_role(make: MakeSession) -> None:
    call, adapter = reader(make)
    assert "only reads" in call.err("op", name="peek", params={}, id="mine")
    assert "takes no id" in call.err("op", name="peek", params={"id": "mine"})
    assert "takes no role" in call.err("op", name="peek", params={"role": "frame"})
    assert adapter.seen == []


def test_a_read_checks_its_arguments_and_its_references_like_a_step(make: MakeSession) -> None:
    call, adapter = reader(make)
    bad = call.err("op", name="peek", params={"face": "face:4"})
    assert "is an id; an op names a face by the step that made it" in bad
    wrong = {"kind": "face", "from_op": "nope", "select": {"normal": [0, 0, 1]}}
    text = call.err("op", name="peek", params={"face": wrong})
    assert "unresolved_reference" in text or "nope" in text
    assert adapter.seen == []


def test_the_guide_marks_a_read_and_does_not_offer_to_edit_it(make: MakeSession) -> None:
    call, _ = reader(make)
    assert "peek(): list the things (read-only)" in call.ok("cad_guide", topic="ops")
    reference = call.ok("cad_guide", topic="op:peek")
    assert reference.splitlines()[0] == "peek: list the things (read-only)"
    assert "read-only: answers now and changes nothing" in reference
    assert "op(edit=" not in reference
    assert "op(edit=" in call.ok("cad_guide", topic="op:mark")
    assert catalog.purpose("mark", {s.name: s for s in ReadFake().extra_operations()}) == "mark"


def test_the_text_of_a_step_is_printed_too(make: MakeSession) -> None:
    call, _ = reader(make)
    reply = call.ok("op", name="mark", params={"label": "a"})
    assert reply.splitlines()[:2] == ["ok: s3 mark", "marked"]


# --- undo of a step with no feature ----------------------------------------------------------------------------------


def test_undo_takes_back_a_step_with_no_feature_through_the_adapter(make: MakeSession) -> None:
    call, adapter = reader(make)
    call.ok("op", name="mark", params={"label": "a"})
    assert adapter.marks == ["a"]
    assert call.ok("undo").startswith("undone: s3 mark")
    assert adapter.marks == []
    assert call.ok("op", name="mark", params={"label": "b"}).startswith("ok: s4 mark")
    assert adapter.marks == ["b"]


# --- component references --------------------------------------------------------------------------------------------


def test_a_component_is_named_by_the_step_that_made_it_or_by_its_id(make: MakeSession) -> None:
    call, adapter = reader(make)
    call.ok("op", name="seat", params={"face": TOP})
    call.ok("op", name="use", params={"component": "s3"})
    call.ok("op", name="use", params={"component": "comp:7"})
    call.ok("op", name="use", params={"component": {"kind": "component", "from_op": "s3"}})
    assert [r.params["component"] for r in adapter.seen] == [{"id": "comp:7"}] * 3
    text = call.err("op", name="use", params={"component": "comp:99"})
    assert "is not a component made by a step in effect" in text
    assert len(adapter.seen) == 3


def test_a_step_that_is_not_a_component_is_refused_by_the_plan_rules(make: MakeSession) -> None:
    call, adapter = reader(make)
    text = call.err("op", name="use", params={"component": "s2"})
    assert "wrong operation type for reference: component ref to 's2'" in text
    assert adapter.seen == []


# --- a mass the CAD measured -----------------------------------------------------------------------------------------


class Weighing(FakeAdapter):
    """A CAD that knows the mass of what it holds."""

    def mass_properties(self, request: Any) -> Any:
        mass = super().mass_properties(request)
        return mass.model_copy(update={"mass_kg": 4.5, "mass_source": "ExternalCAD"})

    def bodies_of(self, request: Any) -> Any:
        found = super().bodies_of(request)
        weighed = tuple(b.model_copy(update={"mass_kg": 4.5, "mass_source": "ExternalCAD"}) for b in found.bodies)
        return BodiesResult(bodies=weighed)


def test_a_mass_the_cad_measured_is_the_one_the_agent_reads(make: MakeSession) -> None:
    call = Caller(make(Weighing()))
    plate(call)
    properties = call.ok("inspect", action="properties")
    assert "mass: 4.5 kg (ExternalCAD measured it)" in properties.splitlines()
    assert "not available" not in properties
    bom = call.ok("inspect", action="bom")
    assert "mass 4.5 kg each (ExternalCAD measured it)" in bom
    assert "total mass: 4.5 kg (ExternalCAD measured it)" in bom
    edges = [{"kind": "edges", "from_op": "s2", "where": {"parallel_to": "z"}}]
    reply = call.ok("op", name="fillet", params={"edges": edges, "radius_mm": 1})
    assert "mass: 4.5 kg (ExternalCAD measured it)" in reply.splitlines()


def test_without_a_measured_mass_the_replies_are_as_they_were(make: MakeSession) -> None:
    call = Caller(make(FakeAdapter()))
    plate(call)
    assert "measured it" not in call.ok("inspect", action="properties")
    assert "measured it" not in call.ok("inspect", action="bom")
    assert "mass:" not in "\n".join(call.session.state_lines())
