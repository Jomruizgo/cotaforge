"""The reads of one step before the CAD is asked to change anything are made once (``StepReads``), several faces or
edges are asked for in one batch (``Adapter.faces_info`` / ``edges_info``), and none of it outlives the call."""

from collections import Counter
from typing import Any

import pytest

from cotaforge.contract import (
    AdapterError,
    EdgesBetweenRequest,
    EdgesInfoRequest,
    FaceHandle,
    FaceRequest,
    FacesInfoRequest,
    FeatureRequest,
)
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.reads import StepReads
from cotaforge.executor.trace import StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification.verifier import Verifier
from tests.unit.executor.test_reads_per_step import _Counting
from tests.unit.executor.test_with_fake import _fake

CHAMFER = {
    "id": "cham",
    "op": "chamfer",
    "intent": "break the edge between the bottom and a side",
    "edges": [
        {
            "kind": "edge",
            "between": [
                {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, -1]}},
                {"kind": "face", "from_op": "base", "select": {"normal": [1, 0, 0]}},
            ],
        }
    ],
    "distance1_mm": 1,
}


def _plate(plate_plan: dict[str, Any], *extra: dict[str, Any]) -> tuple[Executor, _Counting]:
    plate_plan["operations"] = [*plate_plan["operations"][:2], *extra]
    adapter = _Counting(_fake())
    executor = Executor(parse_plan(plate_plan), adapter, Verifier(), ExecutorConfig(), {})  # type: ignore[arg-type]
    executor.start()
    executor.next_operation()
    executor.next_operation()
    return executor, adapter


def _reads(adapter: _Counting, step: Any) -> Counter[str]:
    before = Counter(adapter.calls)
    step()
    return adapter.calls - before


# --- the stand-in -----------------------------------------------------------------------------------------------------


def _faces(executor: Executor, adapter: Any) -> tuple[FaceHandle, ...]:
    feature = executor.context.feature_by_op["base"]  # type: ignore[union-attr]
    return tuple(f.handle for f in adapter.faces_of(FeatureRequest(operation_id="t", feature=feature)).faces)


def test_a_face_asked_twice_is_read_once(plate_plan: dict[str, Any]) -> None:
    executor, adapter = _plate(plate_plan)
    handle = _faces(executor, adapter)[0]
    reads = StepReads(adapter)  # type: ignore[arg-type]
    first = reads.face_info(FaceRequest(operation_id="t", face=handle))
    again = reads.face_info(FaceRequest(operation_id="t", face=handle))
    assert again is first
    assert reads.hits == 1


def test_a_listing_gives_the_descriptors_to_the_single_reads(plate_plan: dict[str, Any]) -> None:
    executor, adapter = _plate(plate_plan)
    feature = executor.context.feature_by_op["base"]  # type: ignore[union-attr]
    reads = StepReads(adapter)  # type: ignore[arg-type]
    listed = reads.faces_of(FeatureRequest(operation_id="t", feature=feature))
    assert reads.faces_of(FeatureRequest(operation_id="t", feature=feature)) is listed
    before = Counter(adapter.calls)
    for face in listed.faces:
        assert reads.face_info(FaceRequest(operation_id="t", face=face.handle)) is face
    assert adapter.calls - before == Counter()  # not one more read of the adapter


def test_a_batch_asks_the_adapter_only_for_what_it_has_not_read(plate_plan: dict[str, Any]) -> None:
    executor, adapter = _plate(plate_plan)
    handles = _faces(executor, adapter)
    reads = StepReads(adapter)  # type: ignore[arg-type]
    one = reads.face_info(FaceRequest(operation_id="t", face=handles[0]))
    before = Counter(adapter.calls)
    got = reads.faces_info(FacesInfoRequest(operation_id="t", faces=(handles[1], handles[0], handles[1], handles[2])))
    assert adapter.calls - before == Counter({"faces_info": 1})  # one batch, of the two it did not have
    assert [f.handle for f in got.faces] == [handles[1], handles[0], handles[1], handles[2]]
    assert got.faces[1] is one


def test_edges_between_gives_the_descriptors_to_edge_info(plate_plan: dict[str, Any]) -> None:
    executor, adapter = _plate(plate_plan)
    faces = {
        f.normal: f
        for f in adapter.faces_of(
            FeatureRequest(operation_id="t", feature=executor.context.feature_by_op["base"])
        ).faces
    }  # type: ignore[union-attr]
    a, b = faces[(0.0, 0.0, -1.0)].handle, faces[(1.0, 0.0, 0.0)].handle
    reads = StepReads(adapter)  # type: ignore[arg-type]
    (edge,) = reads.edges_between(EdgesBetweenRequest(operation_id="t", a=a, b=b)).edges
    before = Counter(adapter.calls)
    assert reads.edges_info(EdgesInfoRequest(operation_id="t", edges=(edge.handle,))).edges == (edge,)
    assert adapter.calls - before == Counter()


# --- the default of the batch hooks -----------------------------------------------------------------------------------


def test_the_default_batch_asks_one_by_one_in_order_and_fails_as_the_single_read_does(
    plate_plan: dict[str, Any],
) -> None:
    executor, adapter = _plate(plate_plan)
    handles = _faces(executor, adapter)
    fake = adapter._inner  # noqa: SLF001
    got = fake.faces_info(FacesInfoRequest(operation_id="t", faces=tuple(reversed(handles))))
    assert [f.handle for f in got.faces] == list(reversed(handles))
    assert fake.faces_info(FacesInfoRequest(operation_id="t", faces=())).faces == ()
    with pytest.raises(AdapterError):
        fake.faces_info(FacesInfoRequest(operation_id="t", faces=(handles[0], FaceHandle(id="face:nope"))))


# --- the step ---------------------------------------------------------------------------------------------------------


def test_a_chamfer_reads_each_face_listing_and_each_edge_once(plate_plan: dict[str, Any]) -> None:
    executor, adapter = _plate(plate_plan, CHAMFER)
    records = []
    reads = _reads(adapter, lambda: records.append(executor.next_operation()))
    # before: faces_of 2 (one per face the edge sits between), edge_info 1 (the edge just described), face_info 2
    assert reads["faces_of"] == 1 and reads["edges_between"] == 1
    assert reads["edge_info"] == 0 and reads["face_info"] == 0 and reads["edges_info"] == 0 and reads["faces_info"] == 0
    assert records[0].status is StepStatus.success


def test_what_was_read_before_the_call_is_not_used_after_it(plate_plan: dict[str, Any]) -> None:
    executor, adapter = _plate(plate_plan, CHAMFER)
    resolver = executor._resolver  # noqa: SLF001
    assert resolver is not None
    executor.next_operation()
    assert resolver.adapter is adapter  # the stand-in is gone: the next reader asks the adapter itself


def test_the_stand_in_is_closed_when_the_step_cannot_run(plate_plan: dict[str, Any]) -> None:
    bad = {**CHAMFER, "edges": [{"kind": "edge", "between": [CHAMFER["edges"][0]["between"][0]] * 2}]}
    executor, adapter = _plate(plate_plan, bad)
    resolver = executor._resolver  # noqa: SLF001
    assert resolver is not None
    record = executor.next_operation()
    assert record.status is not StepStatus.success
    assert resolver.adapter is adapter
