"""``Executor.recorded_selection_origins``: which op made the entity a ``from_selection`` token named when the op ran,
read from the step records (never from the live document, where a consumed entity is gone)."""

from __future__ import annotations

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import EntityRequest, FaceRequest, FeatureRequest
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutorState
from cotaforge.ir import parse_plan
from cotaforge.verification.verifier import Verifier
from tests.conftest import plate_plan_dict


def _executor_with_a_fillet_on_a_picked_edge() -> tuple[Executor, str]:
    """``base_sketch``, ``base`` run; an edge of the top face is picked (a persistent token); ``f1`` fillets it."""
    adapter = FakeAdapter()
    base_ops = plate_plan_dict()["operations"][:2]
    executor = Executor(
        parse_plan({**plate_plan_dict(), "operations": base_ops}), adapter, Verifier(), ExecutorConfig()
    )
    executor.start()
    executor.run_to_end()
    base = executor.feature_for("base")
    assert base is not None
    top = next(
        f
        for f in adapter.faces_of(FeatureRequest(operation_id="q", feature=base)).faces
        if f.normal is not None and f.normal[2] > 0.99
    )
    edge = adapter.edges_of(FaceRequest(operation_id="q", face=top.handle)).edges[0]
    token = adapter.persistent_reference(EntityRequest(operation_id="q", entity=edge.handle)).token
    fillet = {
        "id": "f1",
        "op": "fillet",
        "intent": "round the picked edge",
        "edges": [{"kind": "edge", "from_selection": token}],
        "radius_mm": 2,
    }
    executor.extend([fillet])
    executor.run_to_end()
    assert executor.state is ExecutorState.completed
    return executor, token


def test_the_maker_of_the_picked_entity_is_read_from_the_records() -> None:
    executor, token = _executor_with_a_fillet_on_a_picked_edge()
    assert executor.recorded_selection_origins("f1") == {token: "base"}


def test_an_entity_no_step_lists_has_an_unknown_origin() -> None:
    executor, token = _executor_with_a_fillet_on_a_picked_edge()
    step = next(s for s in executor.trace().active_steps() if s.op_id == "f1")
    step.resolved_refs["edges[0]"] = "edge:not-listed-anywhere"  # an adapter that never reported that entity
    assert executor.recorded_selection_origins("f1") == {token: None}


def test_an_op_that_did_not_run_or_has_no_selection_ref_has_no_origins() -> None:
    executor, _ = _executor_with_a_fillet_on_a_picked_edge()
    assert executor.recorded_selection_origins("base") == {}  # ran, but carries no from_selection ref
    assert executor.recorded_selection_origins("no-such-op") == {}
    executor.undo_to("base")
    assert executor.recorded_selection_origins("f1") == {}  # reverted: not an active step any more


def test_the_insert_that_made_a_picked_component_face_is_its_origin_though_the_insert_lists_no_face() -> None:
    """A component's faces are the part's, counted by the insert and not listed in its result: the origin of a picked
    face of a component is the insert of that component."""
    from tests.unit.executor.test_with_fake import ASSEMBLY, BLOCK, PIN  # noqa: PLC0415

    adapter = FakeAdapter()
    documents = {}
    for plan_dict in (BLOCK, PIN):
        part = Executor(parse_plan(plan_dict), adapter, Verifier(), ExecutorConfig(), documents)
        part.start()
        part.run_to_end()
        documents[plan_dict["id"]] = part.document()
    ops = ASSEMBLY["operations"][:3]  # ib, fx, ip
    executor = Executor(parse_plan({**ASSEMBLY, "operations": ops}), adapter, Verifier(), ExecutorConfig(), documents)
    executor.start()
    executor.run_to_end()
    block = executor.feature_for("ib")
    assert block is not None
    bore = next(
        f
        for f in adapter.faces_of(FeatureRequest(operation_id="q", feature=block)).faces
        if f.surface is not None and f.surface.value == "cylinder"
    )
    token = adapter.persistent_reference(EntityRequest(operation_id="q", entity=bore.handle)).token
    executor.extend(
        [
            {
                "id": "m1",
                "op": "mate_concentric",
                "intent": "pin in the picked bore",
                "a": {"kind": "face", "from_selection": token},
                "b": {"kind": "face", "from_op": "ip", "select": {"surface": "cylinder"}},
            }
        ]
    )
    executor.run_to_end()
    assert executor.state is ExecutorState.completed
    assert executor.recorded_selection_origins("m1") == {token: "ib"}
