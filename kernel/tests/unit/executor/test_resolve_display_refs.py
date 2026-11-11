"""``Executor.resolve_display_refs`` (tools.impl.show_entities): resolves face/edge refs against a live
session's context without executing anything, reporting a ``ResolutionError`` per ref (0 or >1
candidates) instead of raising -- the caller turns that into a candidates report per ref."""

from typing import Any

import pytest

from cotaforge.contract import Adapter, ConnectRequest, EdgeInfo, FaceInfo
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.errors import ResolutionError
from cotaforge.executor.executor import Executor
from cotaforge.ir import parse_plan
from cotaforge.ir.refs import EdgeRef, FaceRef, FaceSelector
from cotaforge.verification.verifier import Verifier


def _fake() -> Adapter:
    fake_module = pytest.importorskip("cotaforge.adapters.fake.adapter")
    faults_module = pytest.importorskip("cotaforge.adapters.fake.faults")
    adapter = fake_module.FakeAdapter(faults=faults_module.FaultPlan.model_validate({"faults": []}))
    adapter.connect(ConnectRequest(operation_id="connect"))
    return adapter


def _started_executor(plate_plan: dict[str, Any]) -> Executor:
    adapter = _fake()
    plan = parse_plan(plate_plan)
    ex = Executor(plan, adapter, Verifier(), ExecutorConfig(), {})
    ex.start()
    ex.run_to_end()
    return ex


def test_resolves_a_single_matching_face(plate_plan: dict[str, Any]) -> None:
    ex = _started_executor(plate_plan)
    ref = FaceRef(from_op="base", select=FaceSelector(normal=(0, 0, 1)))
    (result,) = ex.resolve_display_refs([ref])
    assert isinstance(result, FaceInfo)
    assert result.normal == pytest.approx((0.0, 0.0, 1.0))


def test_zero_candidates_is_a_resolution_error_not_a_raise(plate_plan: dict[str, Any]) -> None:
    ex = _started_executor(plate_plan)
    ref = FaceRef(from_op="base", select=FaceSelector(normal=(1, 1, 0)))
    (result,) = ex.resolve_display_refs([ref])
    assert isinstance(result, ResolutionError)
    assert result.candidates == 0


def test_many_candidates_is_a_resolution_error_with_candidate_details(plate_plan: dict[str, Any]) -> None:
    ex = _started_executor(plate_plan)
    ref = FaceRef(from_op="base", select=FaceSelector(surface="plane"))
    (result,) = ex.resolve_display_refs([ref])
    assert isinstance(result, ResolutionError)
    assert result.candidates > 1
    assert result.candidate_details


def test_each_ref_is_resolved_independently_in_order(plate_plan: dict[str, Any]) -> None:
    ex = _started_executor(plate_plan)
    refs = [
        FaceRef(from_op="base", select=FaceSelector(normal=(0, 0, 1))),
        FaceRef(from_op="base", select=FaceSelector(normal=(1, 1, 0))),
        FaceRef(from_op="base", select=FaceSelector(surface="plane")),
    ]
    results = ex.resolve_display_refs(refs)
    assert len(results) == 3
    assert isinstance(results[0], FaceInfo)
    assert isinstance(results[1], ResolutionError) and results[1].candidates == 0
    assert isinstance(results[2], ResolutionError) and results[2].candidates > 1


def test_resolves_an_edge_ref(plate_plan: dict[str, Any]) -> None:
    ex = _started_executor(plate_plan)
    top = FaceRef(from_op="base", select=FaceSelector(normal=(0, 0, 1)))
    xmax = FaceRef(from_op="base", select=FaceSelector(normal=(1, 0, 0)))
    ref = EdgeRef(between=(top, xmax))
    (result,) = ex.resolve_display_refs([ref])
    assert isinstance(result, EdgeInfo)


def test_does_not_mutate_the_executor_own_resolver_state(plate_plan: dict[str, Any]) -> None:
    """A read via ``resolve_display_refs`` must not pollute ``resolver.resolved_refs`` (which
    ``begin_step``/the trace rely on) -- it always works on a fresh probe copy, like ``probe_references``."""
    ex = _started_executor(plate_plan)
    before = dict(ex._resolver.resolved_refs)  # noqa: SLF001 - white-box, this test's whole point
    ex.resolve_display_refs([FaceRef(from_op="base", select=FaceSelector(normal=(0, 0, 1)))])
    assert ex._resolver.resolved_refs == before  # noqa: SLF001
