"""An adapter declares operations of its own and they go through the same ``op`` tool: schema, guide, validation, run,
undo, plan record, save and resume. An adapter that declares none sees what it always saw."""

import json
from typing import Any

import pytest
from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import (
    DocumentKind,
    EditResult,
    EndCondition,
    ExtensionRequest,
    FaceHandle,
    HoleRequest,
    HoleStandard,
    OperationSpec,
)
from cotaforge.ir import EdgeSet, ExtensionOperation, FaceRef, FeatureStep, SketchStep
from pydantic import BaseModel, Field

from cotaforge_mcp import catalog
from cotaforge_mcp.guide import guide, profile_text, server_instructions
from cotaforge_mcp.schemas import TOOLS_BY_NAME, _compact, input_schema
from cotaforge_mcp.session import Session
from tests.conftest import PLATE_SKETCH, TOP, Caller, MakeSession, plate


class ClearanceHole(BaseModel):
    """A hole drilled by the CAD's own wizard: a clearance for a bolt, from a face of the part."""

    face: FaceRef
    position: tuple[float, float]
    diameter_mm: float = Field(gt=0)
    depth_mm: float | None = Field(default=None, gt=0)


class Probe(BaseModel):
    """Takes every kind of reference, to see what the adapter receives."""

    face: FaceRef
    edges: EdgeSet | None = None
    sketch: SketchStep | None = None
    feature: FeatureStep | None = None
    depth_mm: float = Field(default=2.0, gt=0)


class OwnHole(BaseModel):
    """A richer hole than the common one."""

    face: FaceRef
    position: tuple[float, float]
    diameter_mm: float = Field(gt=0)
    style: str = "simple"


class WizardFake(FakeAdapter):
    """A CAD with an operation of its own that drills through ``hole``, one with every kind of reference, one that
    replaces the common ``hole`` and one that only works in assemblies."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[ExtensionRequest] = []

    def extra_operations(self) -> tuple[OperationSpec, ...]:
        return (
            OperationSpec(
                name="clearance_hole",
                params=ClearanceHole,
                purpose="bolt clearance hole from the CAD's wizard",
                guide="Ask the engineer for the bolt before drilling.\nThe size comes from the bolt.",
                method="clearance_hole",
                example={"face": TOP, "position": [10, 10], "diameter_mm": 6},
            ),
            OperationSpec(name="probe", params=Probe, purpose="every kind of reference", guide="", method="probe"),
            OperationSpec(
                name="hole",
                params=OwnHole,
                purpose="the CAD's own hole, with a style",
                guide="Own text of hole.",
                method="own_hole",
                example={"face": TOP, "position": [5, 5], "diameter_mm": 4},
            ),
            OperationSpec(
                name="only_assembly",
                params=Probe,
                purpose="assemblies only",
                guide="",
                method="probe",
                kinds=(DocumentKind.assembly,),
            ),
        )

    def _drill(self, request: ExtensionRequest, depth: float | None) -> Any:
        params = request.params
        return self.hole(
            HoleRequest(
                operation_id=request.operation_id,
                face=FaceHandle(**params["face"]),
                position=tuple(params["position"]),
                standard=HoleStandard.simple,
                diameter_mm=params["diameter_mm"],
                end_condition=EndCondition.blind,
                depth_mm=depth or 3.0,
            )
        )

    def clearance_hole(self, request: ExtensionRequest) -> Any:
        self.seen.append(request)
        return self._drill(request, request.params.get("depth_mm"))

    def own_hole(self, request: ExtensionRequest) -> Any:
        self.seen.append(request)
        return self._drill(request, None)

    def probe(self, request: ExtensionRequest) -> Any:
        self.seen.append(request)
        face = FaceHandle(**request.params["face"])
        return self.hole(
            HoleRequest(
                operation_id=request.operation_id,
                face=face,
                position=(0.0, 0.0),
                standard=HoleStandard.simple,
                diameter_mm=3.0,
                end_condition=EndCondition.blind,
                depth_mm=request.params["depth_mm"],
            )
        )


HOLE = {"face": TOP, "position": [10, 10], "diameter_mm": 6}


def wizard(make: MakeSession) -> tuple[Caller, WizardFake]:
    adapter = WizardFake()
    call = Caller(make(adapter))
    plate(call)
    return call, adapter


# --- the surface the agent reads --------------------------------------------------------------------------------------


def test_the_op_schema_names_the_operations_of_this_cad_and_only_of_this_cad(make: MakeSession) -> None:
    tool = TOOLS_BY_NAME["op"]
    own = input_schema(tool, make(WizardFake()))["properties"]["name"]["description"]
    assert own.endswith("this CAD also has: clearance_hole, probe, hole, only_assembly")
    plain = input_schema(tool, make(FakeAdapter()))
    assert "this CAD also" not in json.dumps(plain)


def test_an_adapter_that_declares_nothing_sees_the_same_schema_and_guide_text_as_before(session: Session) -> None:
    tool = TOOLS_BY_NAME["op"]
    legacy = _compact(tool.model.model_json_schema())
    legacy.pop("$defs", None)
    legacy.pop("additionalProperties", None)
    assert json.dumps(input_schema(tool, session), sort_keys=True) == json.dumps(legacy, sort_keys=True)
    names = catalog.available_ops(session.capabilities)
    assert session.available_ops() == names
    assert guide(session, "ops").startswith(catalog.ops_table(names))
    for name in names:
        assert guide(session, f"op:{name}") == catalog.op_reference(name, session.adapter.guide_topic)
    assert "also has" not in server_instructions(session)
    assert len(server_instructions(session)) <= 4096


def test_the_guide_lists_the_operation_and_gives_its_reference(make: MakeSession) -> None:
    call, _ = wizard(make)
    ops = call.ok("cad_guide", topic="ops")
    assert "clearance_hole(face, position, diameter_mm): bolt clearance hole from the CAD's wizard" in ops
    assert "clearance_hole" in profile_text(call.session)
    reference = call.ok("cad_guide", topic="op:clearance_hole")
    assert reference.startswith("clearance_hole: bolt clearance hole from the CAD's wizard")
    assert "about: A hole drilled by the CAD's own wizard" in reference
    assert "diameter_mm: number  [>0 REQUIRED]" in reference
    assert "face: FaceRef  [REQUIRED]" in reference
    assert "note: Ask the engineer for the bolt before drilling. The size comes from the bolt." in reference
    assert "op(edit=<id>, params={...}) with only the fields to change" in reference
    assert 'example: op(name="clearance_hole", params=' in reference
    assert len(reference) <= 6000


def test_an_operation_of_the_adapter_replaces_the_common_one_of_its_name(make: MakeSession) -> None:
    call, _ = wizard(make)
    reference = call.ok("cad_guide", topic="op:hole")
    assert reference.startswith("hole: the CAD's own hole, with a style")
    assert "style: string" in reference
    assert "counterbore" not in reference
    assert "Own text of hole." in reference
    ops = call.ok("cad_guide", topic="ops")
    assert ops.count("\nhole(") + ops.startswith("hole(") == 1
    plain = Caller(make(FakeAdapter()))
    assert "counterbore" in plain.ok("cad_guide", topic="op:hole")


def test_an_unknown_name_is_refused_with_the_names_of_this_cad(make: MakeSession) -> None:
    call, _ = wizard(make)
    text = call.err("op", name="clearence_hole", params={})
    assert "clearance_hole" in text


# --- validation -------------------------------------------------------------------------------------------------------


def test_a_bad_argument_is_refused_with_the_field_the_shape_and_an_example(make: MakeSession) -> None:
    call, adapter = wizard(make)
    text = call.err("op", name="clearance_hole", params={**HOLE, "diameter_mm": -2})
    assert "field: params.diameter_mm" in text
    assert 'usage: op(name="clearance_hole", params={face: FaceRef; position: [number x2]; diameter_mm: number' in text
    assert 'example: op(name="clearance_hole"' in text
    assert adapter.seen == []
    missing = call.err("op", name="clearance_hole", params={"face": TOP})
    assert "field: params.position" in missing
    assert "field: params.diameter_mm" in missing


def test_a_face_named_by_its_live_id_is_refused_like_in_a_common_operation(make: MakeSession) -> None:
    call, _ = wizard(make)
    text = call.err("op", name="clearance_hole", params={**HOLE, "face": "face:4"})
    assert "is an id; an op names a face by the step that made it" in text


def test_an_operation_of_the_adapter_refuses_a_document_kind_it_did_not_declare(make: MakeSession) -> None:
    call, _ = wizard(make)
    assert "works in documents of kind assembly" in call.err("op", name="only_assembly", params={"face": TOP})
    call.ok("doc", action="new", name="frame", kind="assembly")
    assert "works in documents of kind part" in call.err("op", name="clearance_hole", params=HOLE)


# --- run, ids, undo, record ------------------------------------------------------------------------------------


def test_the_operation_runs_through_the_adapter_method_with_its_references_resolved(make: MakeSession) -> None:
    call, adapter = wizard(make)
    reply = call.ok("op", name="clearance_hole", params=HOLE, intent="M6 bolt")
    assert reply.startswith("ok: s3 clearance_hole")
    assert "created:" in reply
    (request,) = adapter.seen
    assert request.name == "clearance_hole"
    assert request.operation_id == "s3"
    face = request.params["face"]
    assert set(face) == {"id"} and face["id"].startswith("face:")
    assert request.params["position"] == [10.0, 10.0]
    assert "steps: 3, last s3 clearance_hole" in call.ok("doc", action="status")


def test_every_kind_of_reference_arrives_as_the_json_of_its_handle(make: MakeSession) -> None:
    call, adapter = wizard(make)
    call.ok("op", name="sketch", params=PLATE_SKETCH, id="outline")
    call.ok(
        "op",
        name="probe",
        params={
            "face": TOP,
            "edges": {"kind": "edges", "from_op": "s2", "where": {"parallel_to": "z"}},
            "sketch": "outline",
            "feature": "s2",
        },
    )
    (request,) = adapter.seen
    assert request.params["sketch"] == call.session.executor.context.sketch_by_op["outline"].model_dump()
    assert request.params["feature"] == call.session.executor.context.feature_by_op["s2"].model_dump()
    assert len(request.params["edges"]) == 4
    assert all(set(edge) == {"id"} for edge in request.params["edges"])


def test_a_step_that_is_not_a_sketch_is_refused_naming_it(make: MakeSession) -> None:
    call, adapter = wizard(make)
    text = call.err("op", name="probe", params={"face": TOP, "sketch": "missing"})
    assert "sketch: step 'missing' has not produced a sketch" in text
    assert adapter.seen == []
    assert "steps: 2, last s2 extrude" in call.ok("doc", action="status")


def test_the_new_ids_of_the_operation_serve_the_next_step(make: MakeSession) -> None:
    call, _ = wizard(make)
    call.ok("op", name="clearance_hole", params=HOLE)
    # the faces the wizard made are named by the step that made it, like those of any operation
    floor = {"kind": "face", "from_op": "s3", "select": {"normal": [0, 0, 1], "smallest": True}}
    reply = call.ok("op", name="probe", params={"face": floor})
    assert reply.startswith("ok: s4 probe")


def test_undo_takes_the_operation_back_like_any_other(make: MakeSession) -> None:
    call, _ = wizard(make)
    before = call.ok("inspect", action="properties")
    call.ok("op", name="clearance_hole", params=HOLE)
    assert call.ok("inspect", action="properties") != before
    text = call.ok("undo")
    assert text.startswith("undone: s3 clearance_hole")
    assert call.ok("inspect", action="properties") == before
    assert call.ok("op", name="clearance_hole", params=HOLE).startswith("ok: s4 clearance_hole")


def test_the_plan_keeps_the_operation_and_the_check_report_covers_it(make: MakeSession) -> None:
    call, _ = wizard(make)
    call.ok("op", name="clearance_hole", params=HOLE, intent="M6 bolt")
    operation = call.session.executor.plan.operations[-1]
    assert isinstance(operation, ExtensionOperation)
    assert operation.op == "ext:clearance_hole"
    assert operation.intent == "M6 bolt"
    assert operation.params["diameter_mm"] == 6.0
    call.ok("check")


def test_edit_reaches_an_operation_of_the_adapter(make: MakeSession) -> None:
    call, adapter = wizard(make)
    call.ok("op", name="clearance_hole", params=HOLE, intent="M6 bolt")
    text = call.ok("op", edit="s3", params={"diameter_mm": 8})
    assert text.startswith("ok: s3 edited (ext:clearance_hole): diameter_mm 6 -> 8")
    # this adapter cannot change it in place, so the step is rebuilt with the new value (and says so)
    assert "method: rebuilt from s3" in text
    operation = call.session.executor.plan.operations[-1]
    assert isinstance(operation, ExtensionOperation) and operation.id == "s3"
    assert operation.params["diameter_mm"] == 8.0 and operation.intent == "M6 bolt"
    assert adapter.seen[-1].params["diameter_mm"] == 8.0 and adapter.seen[-1].operation_id == "s3"
    assert "steps: 3, last s3 clearance_hole" in call.ok("doc", action="status")
    call.ok("op", edit="s2", params={"depth_mm": 10})  # a common step before it can still be edited
    assert call.session.executor.plan.operations[-1].op == "ext:clearance_hole"


def test_edit_of_an_adapter_operation_checks_the_arguments_with_the_adapters_model(make: MakeSession) -> None:
    call, adapter = wizard(make)
    call.ok("op", name="clearance_hole", params=HOLE)
    seen = len(adapter.seen)
    bad = call.err("op", edit="s3", params={"diameter_mm": -1})
    assert "field: params.diameter_mm" in bad
    unknown = call.err("op", edit="s3", params={"width_mm": 4})
    assert "clearance_hole has no parameter width_mm; parameters: face, position, diameter_mm, depth_mm" in unknown
    same = call.err("op", edit="s3", params={"diameter_mm": 6})
    assert "nothing to change" in same
    assert len(adapter.seen) == seen
    assert call.session.executor.plan.operations[-1].params["diameter_mm"] == 6.0  # type: ignore[attr-defined]


def test_edit_in_place_of_an_adapter_operation_when_the_adapter_answers(make: MakeSession) -> None:
    class Answering(WizardFake):
        def edit_feature(self, request: Any) -> Any:
            self.edited = request
            return EditResult(rebuild_ok=True, applied=True, recomputed=(request.edits[0].feature,))

    adapter = Answering()
    call = Caller(make(adapter))
    plate(call)
    call.ok("op", name="clearance_hole", params=HOLE)
    text = call.ok("op", edit="s3", params={"diameter_mm": 8})
    assert "method: changed in place; recomputed: s3" in text
    (edit,) = adapter.edited.edits
    assert edit.kind == "clearance_hole" and edit.request.params["diameter_mm"] == 8.0
    assert call.session.executor.plan.operations[-1].params["diameter_mm"] == 8.0  # type: ignore[attr-defined]


def test_a_common_operation_still_works_beside_the_adapter_ones(make: MakeSession) -> None:
    call, _ = wizard(make)
    call.ok("op", name="clearance_hole", params=HOLE)
    edges = [{"kind": "edges", "from_op": "s2", "where": {"parallel_to": "z"}}]
    assert call.ok("op", name="fillet", params={"edges": edges, "radius_mm": 1}).startswith("ok: s4 fillet")


# --- save and resume -------------------------------------------------------------------------------------------


def test_save_and_resume_keep_the_operation_and_the_steps_after_it(make: MakeSession) -> None:
    call, _ = wizard(make)
    call.ok("op", name="clearance_hole", params=HOLE)
    call.ok("doc", action="save")
    call.ok("doc", action="close")
    reopened = Caller(make(WizardFake()))
    path = call.session.settings.workspace / f"plate{FakeAdapter.document_suffix}"
    assert "s3: clearance_hole" in reopened.ok("doc", action="open", path=str(path))
    operation = reopened.session.executor.plan.operations[-1]
    assert isinstance(operation, ExtensionOperation) and operation.op == "ext:clearance_hole"
    # the operation keeps working after the reopen: its adapter declares it again
    floor = {"kind": "face", "from_op": "s3", "select": {"normal": [0, 0, 1], "smallest": True}}
    assert reopened.ok("op", name="probe", params={"face": floor}).startswith("ok: s4 probe")
    assert reopened.ok("op", name="clearance_hole", params={**HOLE, "position": [-20, -10]}).startswith("ok: s5")


@pytest.mark.parametrize("name", ["clearance_hole", "probe"])
def test_a_declared_name_cannot_repeat(make: MakeSession, name: str) -> None:
    class Twice(FakeAdapter):
        def extra_operations(self) -> tuple[OperationSpec, ...]:
            spec = OperationSpec(name=name, params=Probe, purpose="x", guide="", method="probe")
            return (spec, spec)

    call = Caller(make(Twice()))
    call.ok("doc", action="new", name="plate")
    assert "declares the operation" in call.err("op", name=name, params={})


def test_a_spec_with_a_bad_name_is_refused_when_it_is_declared() -> None:
    with pytest.raises(ValueError, match="lowercase"):
        OperationSpec(name="Bad Name", params=Probe, purpose="x", guide="", method="m")
    with pytest.raises(ValueError, match="purpose"):
        OperationSpec(name="ok", params=Probe, purpose=" ", guide="", method="m")


def test_the_instructions_name_the_operations_within_their_limit_and_a_long_guide_is_clipped(make: MakeSession) -> None:
    class Wordy(WizardFake):
        def extra_operations(self) -> tuple[OperationSpec, ...]:
            spec = super().extra_operations()[0]
            return (OperationSpec(**{**spec.__dict__, "guide": "word " * 3000}),)

    call = Caller(make(Wordy()))
    instructions = server_instructions(call.session)
    assert "ops (other): clearance_hole" in instructions
    assert len(instructions) <= 4096
    assert len(call.ok("cad_guide", topic="op:clearance_hole")) <= 6000


def test_a_field_with_a_default_factory_is_described_not_crashed_on() -> None:
    from pydantic import BaseModel, Field  # noqa: PLC0415

    from cotaforge_mcp import catalog  # noqa: PLC0415

    class Params(BaseModel):
        values: dict[str, float] = Field(default_factory=dict)
        names: list[str] = Field(default_factory=list)

    assert catalog._default_text(Params.model_fields["values"]) == "default {}"  # noqa: SLF001
    assert catalog._default_text(Params.model_fields["names"]) == "default []"  # noqa: SLF001
