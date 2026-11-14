"""``Executor.edit_in_place``: change ops IN PLACE in the CAD instead of undoing and re-running everything after them
Over the fake adapter, whose replay-with-stable-ids edit has the same eligibility policy as the
FreeCAD worker's."""

import math
from typing import Any

import pytest

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import (
    Adapter,
    AdapterError,
    EditFeatureRequest,
    EditResult,
    ErrorCode,
    ErrorInfo,
    MassRequest,
)
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.errors import ExecutorStateError
from cotaforge.executor.executor import Executor, _named_refs, _refs_to_probe_after_an_edit
from cotaforge.executor.trace import EditRecord, ExecutionTrace, ExecutorState
from cotaforge.ir import parse_plan
from cotaforge.verification.verifier import Verifier


def plan_dict(**overrides: dict[str, Any]) -> dict[str, Any]:
    """block 100 x 60 x 20, a D30 through cylinder, the bore (block - cylinder), then the bore moved +10 along X."""
    ops: list[dict[str, Any]] = [
        {"id": "block", "op": "box", "intent": "Block", "size": [100, 60, 20]},
        {
            "id": "tool",
            "op": "cylinder",
            "intent": "Bore tool",
            "diameter_mm": 30,
            "height_mm": 20,
            "axis": "Z",
            "origin": [50, 30, 0],
        },
        {
            "id": "bore",
            "op": "boolean_subtract",
            "intent": "Bore",
            "target": {"kind": "body", "from_op": "block"},
            "tools": [{"kind": "body", "from_op": "tool"}],
        },
        {
            "id": "place",
            "op": "translate",
            "intent": "Place",
            "target": {"kind": "body", "from_op": "bore"},
            "offset": [10, 0, 0],
        },
    ]
    ops = [{**op, **overrides.get(op["id"], {})} for op in ops]
    return {"id": "block_plan", "kind": "part", "operations": ops}


def run(adapter: Adapter | None = None) -> Executor:
    executor = Executor(parse_plan(plan_dict()), adapter or FakeAdapter(), Verifier(), ExecutorConfig())
    executor.start()
    executor.run_to_end()
    assert executor.state is ExecutorState.completed
    return executor


def volume(executor: Executor) -> float:
    return executor.adapter.mass_properties(MassRequest(operation_id="q")).volume_mm3


def edited(**overrides: dict[str, Any]) -> Any:
    return parse_plan(plan_dict(**overrides))


BLOCK = 100 * 60 * 20


def test_an_applied_edit_changes_the_part_the_plan_and_the_trace_without_rerunning_anything() -> None:
    executor = run()
    steps_before = executor.trace().steps
    assert volume(executor) == pytest.approx(BLOCK - math.pi * 15**2 * 20)

    record = executor.edit_in_place(edited(tool={"diameter_mm": 40}), ["tool"], by="agent", reason="wider bore")

    assert isinstance(record, EditRecord) and record.status == "applied"
    assert record.changes == {"tool": ("diameter_mm",)}
    assert record.recomputed_ops == ("tool", "bore", "place")  # the dependents: recomputed, not re-verified
    assert "not re-verified" in record.verification
    assert volume(executor) == pytest.approx(BLOCK - math.pi * 20**2 * 20)
    assert executor.plan.operations[1].diameter_mm == 40  # type: ignore[union-attr]
    # nothing was undone or re-run: the same frozen step records, the cursor where it was, no revert
    trace = executor.trace()
    assert trace.steps == steps_before and trace.reverts == () and executor.cursor == 4
    assert executor.state is ExecutorState.completed
    assert trace.edits == (record,)
    # the part was re-measured: the trace's final mass is the edited part's
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(volume(executor))


def test_a_batch_of_edits_is_one_adapter_call() -> None:
    executor = run()
    calls: list[EditFeatureRequest] = []
    original = executor.adapter.edit_feature

    def spy(request: EditFeatureRequest) -> EditResult:
        calls.append(request)
        return original(request)

    executor.adapter.edit_feature = spy  # type: ignore[method-assign]
    record = executor.edit_in_place(edited(block={"size": [120, 60, 20]}, tool={"diameter_mm": 20}), ["block", "tool"])
    assert record.status == "applied"
    assert len(calls) == 1 and [e.kind for e in calls[0].edits] == ["box", "cylinder"]
    assert volume(executor) == pytest.approx(120 * 60 * 20 - math.pi * 10**2 * 20)


def test_the_requests_are_the_ones_the_builders_make() -> None:
    executor = run()
    seen: list[EditFeatureRequest] = []
    original = executor.adapter.edit_feature
    executor.adapter.edit_feature = lambda r: (seen.append(r), original(r))[1]  # type: ignore[method-assign]
    executor.edit_in_place(edited(tool={"origin": [60, 30, 0]}), ["tool"])
    (edit,) = seen[0].edits
    assert edit.kind == "cylinder" and edit.request.axis.origin == (60.0, 30.0, 0.0)  # type: ignore[attr-defined]
    assert edit.feature == executor.feature_for("tool")


def test_a_change_the_cad_never_sees_adopts_the_plan_without_calling_the_adapter() -> None:
    executor = run()
    executor.adapter.edit_feature = lambda r: pytest.fail("the adapter must not be called")  # type: ignore[method-assign]
    record = executor.edit_in_place(edited(tool={"intent": "A better intent"}), ["tool"])
    assert record.status == "unchanged" and record.result is None
    assert executor.plan.operations[1].intent == "A better intent"
    assert executor.trace().edits == (record,)


def test_a_refusal_changes_nothing_and_is_not_recorded() -> None:
    executor = run()
    before = executor.plan
    # the boolean itself is not an op the adapter edits in place (yet)
    plan = edited(bore={"keep_tools": True})
    refused = executor.edit_in_place(plan, ["bore"])
    assert refused.status == "refused" and refused.failure and "not supported yet" in refused.failure
    assert refused.failure_kind == "technical"  # the mechanism cannot, the value is not at fault
    assert (
        executor.plan is before
        and executor.trace().edits == ()
        and volume(executor) == pytest.approx(BLOCK - math.pi * 15**2 * 20)
    )


def test_an_adapter_that_cannot_edit_in_place_refuses() -> None:
    class NoEdit(FakeAdapter):
        edit_feature = Adapter.edit_feature  # not overridden: ``capabilities()`` drops the tool

    executor = run(NoEdit())
    plan = edited(tool={"diameter_mm": 40})
    refused = executor.edit_in_place(plan, ["tool"])
    assert refused.status == "refused" and refused.failure and "cannot edit features in place" in refused.failure


def test_a_failed_recompute_is_rolled_back_and_recorded() -> None:
    executor = run()
    before = volume(executor)
    # the fake's containment rules reject a cylinder laid across the block
    plan = edited(tool={"axis": "X", "origin": [0, 30, 10]})
    record = executor.edit_in_place(plan, ["tool"])
    assert record.status == "rolled_back" and record.failure and "bore" in record.failure
    assert record.failure_kind == "semantic"  # the part would break
    assert record.result is not None and not record.result.rebuild_ok
    assert volume(executor) == pytest.approx(before)
    assert executor.plan.operations[1].axis.value == "Z"  # type: ignore[union-attr]
    assert executor.trace().edits == (record,)


def test_the_executor_refuses_inconsistent_calls() -> None:
    executor = run()
    with pytest.raises(ExecutorStateError, match="not among the ops to edit"):
        executor.edit_in_place(edited(tool={"diameter_mm": 40}), ["block"])
    shorter = parse_plan({**plan_dict(), "operations": plan_dict()["operations"][:2]})
    with pytest.raises(ExecutorStateError, match="same operations"):
        executor.edit_in_place(shorter, ["tool"])
    fresh = Executor(parse_plan(plan_dict()), FakeAdapter(), Verifier(), ExecutorConfig())
    with pytest.raises(ExecutorStateError, match="idle"):
        fresh.edit_in_place(edited(tool={"diameter_mm": 40}), ["tool"])


def test_an_op_that_has_not_run_cannot_be_edited_in_place() -> None:
    executor = Executor(parse_plan(plan_dict()), FakeAdapter(), Verifier(), ExecutorConfig())
    executor.start()
    executor.next_operation()
    executor.next_operation()
    executor.pause_now()
    record = executor.edit_in_place(edited(bore={"intent": "x"}), ["bore"])
    assert record.status == "refused" and "has not been executed" in (record.failure or "")


def test_the_session_goes_on_after_an_edit_and_checks_the_next_op_against_the_edited_part() -> None:
    """The verifier's analytic shapes describe the part as it WAS: they are forgotten, so an op appended afterwards is
    checked against what the adapter reports, not against a stale model."""
    executor = run()
    assert executor.edit_in_place(edited(tool={"diameter_mm": 40}), ["tool"]).status == "applied"
    executor.extend(
        [
            {"id": "pad", "op": "box", "intent": "A pad", "size": [10, 10, 10], "placement": {"origin": [300, 0, 0]}},
            {
                "id": "joined",
                "op": "boolean_union",
                "intent": "Join",
                "target": {"kind": "body", "from_op": "place"},
                "tools": [{"kind": "body", "from_op": "pad"}],
            },
        ]
    )
    executor.run_to_end()
    failed = [s.op_id for s in executor.trace().active_steps() if s.status.value != "success"]
    assert failed == [] and executor.state is ExecutorState.completed


def test_undo_to_after_an_edit_restores_the_verifier_without_stale_shapes() -> None:
    executor = run()
    executor.edit_in_place(edited(tool={"diameter_mm": 40}), ["tool"])
    executor.undo_to("tool")
    assert executor.state is ExecutorState.paused
    executor.resume()
    executor.run_to_end()  # re-runs bore and place with the edited plan
    assert executor.state is ExecutorState.completed
    assert volume(executor) == pytest.approx(BLOCK - math.pi * 20**2 * 20)


def test_a_trace_without_edits_still_loads_and_one_with_edits_round_trips() -> None:
    executor = run()
    plain = executor.trace().model_dump(mode="json")
    plain.pop("edits")
    assert ExecutionTrace.model_validate(plain).edits == ()
    executor.edit_in_place(edited(tool={"diameter_mm": 40}), ["tool"])
    again = ExecutionTrace.model_validate_json(executor.trace().model_dump_json())
    assert again.edits[0].status == "applied" and again.edits[0].recomputed_ops == ("tool", "bore", "place")


def test_a_reference_that_resolves_to_another_face_after_the_edit_rolls_it_back() -> None:
    """The CAD recomputed fine, but a hole selected "the top face" and that selector now lands on another handle: the
    edit is taken back (old requests sent again) and reported as reference drift."""

    class Scripted(FakeAdapter):
        def __init__(self) -> None:
            super().__init__()
            self.calls: list[EditFeatureRequest] = []

        def edit_feature(self, request: EditFeatureRequest) -> EditResult:  # type: ignore[override]
            self.calls.append(request)
            if len(self.calls) == 1:  # the edit: the top face of the plate gets a new handle (same geometry)
                doc = self._active
                assert doc is not None
                top = next(f for f in doc.faces.values() if f.normal == (0.0, 0.0, 1.0) and f.center[2] > 7)
                old = top.id
                doc.faces.pop(old)
                top.id = "face:999"
                doc.faces[top.id] = top
                for feature in doc.features:
                    feature.created_faces = [top.id if f == old else f for f in feature.created_faces]
                for edge in doc.edges.values():
                    edge.faces = tuple(top.id if f == old else f for f in edge.faces)  # type: ignore[assignment]
            return EditResult(rebuild_ok=True, applied=True, recomputed=(request.edits[0].feature,))

    plate = {
        "id": "plate",
        "kind": "part",
        "operations": [
            {
                "id": "sk",
                "op": "sketch",
                "intent": "Outline",
                "on": {"kind": "plane", "name": "XY"},
                "entities": [{"kind": "rect", "name": "o", "center": [50, 30], "width": 100, "height": 60}],
            },
            {"id": "base", "op": "extrude", "intent": "Thickness", "sketch": "sk", "depth_mm": 8},
            {
                "id": "hole_1",
                "op": "hole",
                "intent": "Hole",
                "face": {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}},
                "position": [-40, -20],
                "standard": "iso_clearance",
                "size": "M6",
                "diameter_mm": 6.6,
                "end_condition": "through_all",
            },
        ],
    }
    adapter = Scripted()
    executor = Executor(parse_plan(plate), adapter, Verifier(), ExecutorConfig())
    executor.start()
    executor.run_to_end()
    assert executor.state is ExecutorState.completed

    changed = parse_plan(
        {
            **plate,
            "operations": [plate["operations"][0], {**plate["operations"][1], "depth_mm": 9}, plate["operations"][2]],
        }
    )
    record = executor.edit_in_place(changed, ["base"])

    assert record.status == "rolled_back" and record.reference_drift
    assert record.failure_kind == "technical"  # the mechanism's own invariant, not the part
    assert "hole_1.face" in record.reference_drift[0] and "face:999" in record.reference_drift[0]
    assert len(adapter.calls) == 2 and adapter.calls[1].edits[0].request.depth_mm == 8  # type: ignore[attr-defined]
    assert executor.plan.operations[1].depth_mm == 8  # type: ignore[union-attr]


NOTHING_TO_CHANGE = EditResult(
    rebuild_ok=True, applied=False, warnings=("nothing changed: the new requests equal the values the document has",)
)


def test_a_worker_that_found_nothing_to_change_is_unchanged_not_a_failure() -> None:
    """The document already carries the new values (set by hand, say): the adapter answers ok and not applied. That is
    a no-op to adopt, never a rolled-back edit that makes the caller rebuild the part for nothing."""
    executor = run()
    executor.adapter.edit_feature = lambda r: NOTHING_TO_CHANGE  # type: ignore[method-assign]
    before = volume(executor)
    record = executor.edit_in_place(edited(tool={"diameter_mm": 40}), ["tool"], by="agent")
    assert record.status == "unchanged" and record.failure is None and record.failure_kind is None
    assert record.result == NOTHING_TO_CHANGE  # what the adapter said stays as evidence
    assert executor.plan.operations[1].diameter_mm == 40  # type: ignore[union-attr]
    assert executor.trace().edits == (record,) and executor.trace().reverts == ()
    assert volume(executor) == pytest.approx(before) and executor.state is ExecutorState.completed
    # the adopted request is the one a later edit compares against: asking for the old value again is a real change
    seen: list[EditFeatureRequest] = []
    executor.adapter.edit_feature = lambda r: (seen.append(r), NOTHING_TO_CHANGE)[1]  # type: ignore[method-assign]
    executor.edit_in_place(edited(tool={"diameter_mm": 30}), ["tool"])
    assert len(seen) == 1 and seen[0].edits[0].request.diameter_mm == 30.0  # type: ignore[attr-defined]


def test_after_an_edit_the_verifier_forgets_the_analytic_shapes_of_the_old_part() -> None:
    """The verifier tracks an analytic shape per body; after an in-place edit they describe the part as it WAS, and a
    later boolean would be checked against them. They are dropped, in the live verifier and in the copies kept for
    undo_to, so the next check downgrades to what the adapter reports."""
    short = {**plan_dict(), "operations": plan_dict()["operations"][:2]}  # a boolean or a move would already drop them
    executor = Executor(parse_plan(short), FakeAdapter(), Verifier(), ExecutorConfig())
    executor.start()
    executor.run_to_end()
    computer = executor.verifier.expected_computer
    assert any(shape is not None for shape in computer._shape_by_body.values())  # the premise: shapes are tracked
    assert executor._verifier_states, "undo_to keeps copies of the tracked state"
    assert any(
        shape is not None for saved in executor._verifier_states.values() for shape in saved["_shape_by_body"].values()
    )
    wider = parse_plan(
        {**plan_dict(tool={"diameter_mm": 40}), "operations": plan_dict(tool={"diameter_mm": 40})["operations"][:2]}
    )
    assert executor.edit_in_place(wider, ["tool"]).status == "applied"
    assert all(shape is None for shape in computer._shape_by_body.values())
    assert all(
        shape is None for saved in executor._verifier_states.values() for shape in saved["_shape_by_body"].values()
    )


def test_the_record_keeps_the_old_and_the_new_request_of_every_edited_op() -> None:
    """An audit trail: what each op asked for before and after, as the adapter received it (not only which fields of the
    plan changed), so the trace alone says what the CAD was given."""
    executor = run()
    record = executor.edit_in_place(edited(block={"size": [120, 60, 20]}, tool={"diameter_mm": 40}), ["block", "tool"])
    assert record.status == "applied" and record.plan_revision is None  # the executor does not number revisions
    assert set(record.old_requests) == set(record.new_requests) == {"block", "tool"}
    assert record.old_requests["block"]["size"] == [100.0, 60.0, 20.0]
    assert record.new_requests["block"]["size"] == [120.0, 60.0, 20.0]
    assert record.old_requests["tool"]["diameter_mm"] == 30.0 and record.new_requests["tool"]["diameter_mm"] == 40.0
    # the second edit starts from the first one's result: its "old" is the standing value, not the original plan's
    again = executor.edit_in_place(edited(block={"size": [120, 60, 20]}, tool={"diameter_mm": 50}), ["tool"])
    assert again.old_requests["tool"]["diameter_mm"] == 40.0 and again.new_requests["tool"]["diameter_mm"] == 50.0
    # a change the CAD never sees has no request on either side
    plain = executor.edit_in_place(
        edited(block={"size": [120, 60, 20]}, tool={"diameter_mm": 50, "intent": "x"}), ["tool"]
    )
    assert plain.status == "unchanged" and plain.old_requests == {} and plain.new_requests == {}
    # and the audit trail survives the trace's JSON
    reloaded = ExecutionTrace.model_validate_json(executor.trace().model_dump_json())
    assert (
        reloaded.edits[0].old_requests == record.old_requests and reloaded.edits[0].new_requests == record.new_requests
    )


@pytest.mark.parametrize("code", [ErrorCode.degraded_session, ErrorCode.timeout, ErrorCode.session_lost])
def test_an_error_that_says_the_worker_is_gone_degrades_the_executor_and_is_re_raised(code: ErrorCode) -> None:
    executor = run()

    def gone(request: EditFeatureRequest) -> EditResult:
        raise AdapterError(ErrorInfo(code=code, message="no worker", recoverable=False))

    executor.adapter.edit_feature = gone  # type: ignore[method-assign]
    with pytest.raises(AdapterError):
        executor.edit_in_place(edited(tool={"diameter_mm": 40}), ["tool"])
    assert executor.state is ExecutorState.degraded and executor.trace().edits == ()


def test_an_adapter_that_does_not_say_what_kind_of_failure_it_was_is_not_trusted_to_fall_back() -> None:
    executor = run()
    executor.adapter.edit_feature = lambda r: EditResult(rebuild_ok=False, applied=False, failure="it broke")  # type: ignore[method-assign]
    record = executor.edit_in_place(edited(tool={"diameter_mm": 40}), ["tool"])
    assert record.status == "rolled_back" and record.failure == "it broke"
    assert record.failure_kind == "semantic"  # a rebuild would skip the very checks that just said no


def test_an_ok_answer_that_carries_a_failure_is_not_a_no_op() -> None:
    executor = run()
    executor.adapter.edit_feature = lambda r: EditResult(rebuild_ok=True, applied=False, failure="odd")  # type: ignore[method-assign]
    plan_before = executor.plan
    record = executor.edit_in_place(edited(tool={"diameter_mm": 40}), ["tool"])
    assert record.status == "rolled_back" and record.failure == "odd"
    assert executor.plan is plan_before  # not adopted


def test_the_edges_a_fillet_rounded_are_not_probed_after_an_edit() -> None:
    """A fillet consumes the edges it names: after the edit they are not in the document, and that is no drift."""
    corner = {
        "kind": "edge",
        "between": [
            {"kind": "face", "from_op": "b", "select": {"normal": [1, 0, 0]}},
            {"kind": "face", "from_op": "b", "select": {"normal": [0, 1, 0]}},
        ],
    }
    plan = parse_plan(
        {
            "id": "p",
            "kind": "part",
            "operations": [
                {"id": "b", "op": "box", "intent": "Block", "size": [10, 10, 10]},
                {"id": "f", "op": "fillet", "intent": "Round", "edges": [corner], "radius_mm": 1},
            ],
        }
    )
    assert _refs_to_probe_after_an_edit(plan.operations[1]) == []
    assert _named_refs(plan.operations[1])  # and the refs are there: it is the filter that drops them


def test_the_step_after_an_in_place_edit_takes_the_edited_part_as_its_before_state() -> None:
    """The measure the last step left is not the part's any more once an edit changed it in place."""
    executor = Executor(parse_plan(plan_dict()), FakeAdapter(), Verifier(), ExecutorConfig())
    executor.start()
    for _ in range(3):  # block, tool, bore: the last one leaves its measure to be reused by the next step
        executor.next_operation()
    executor.pause_now()
    assert executor.state is ExecutorState.paused
    assert executor.last_measure() is not None
    stale = volume(executor)

    record = executor.edit_in_place(edited(tool={"diameter_mm": 40}), ["tool"])

    assert record.status == "applied" and volume(executor) != pytest.approx(stale)
    assert executor.last_measure() is None  # nothing is known about the part until something measures it
    step = executor.next_operation()  # the move of the bore
    assert step.mass_before is not None
    assert step.mass_before.volume_mm3 == pytest.approx(BLOCK - math.pi * 20**2 * 20)
    assert step.mass_before.volume_mm3 != pytest.approx(stale)
    assert step.mass_after is not None and step.mass_after.volume_mm3 == pytest.approx(step.mass_before.volume_mm3)
