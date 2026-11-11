"""``cotaforge.executor.diagnosis``.

``diagnose`` is exercised directly as a pure function -- no ``Executor`` involved -- against hand-built
``OperationBase``/``Resolver`` fixtures and a tiny ``StubAdapter`` subclass that scripts ``sketch_status``.
"""

from typing import Any

from cotaforge.contract import (
    AdapterError,
    BodyHandle,
    ConstraintConflict,
    DocumentHandle,
    ErrorCode,
    ErrorInfo,
    FeatureHandle,
    OperationResult,
    PlaneName,
    RectEntity,
    SketchHandle,
    SketchStatusRequest,
    SketchStatusResult,
)
from cotaforge.executor.context import ExecutionContext
from cotaforge.executor.diagnosis import Diagnosis, DiagnosisKind
from cotaforge.executor.diagnosis import diagnose as _diagnose_fn
from cotaforge.executor.resolver import Resolver
from cotaforge.ir.ops_features import ExtrudeOperation, SketchOperation
from cotaforge.ir.ops_primitives import BodyRef, TranslateOperation
from cotaforge.ir.refs import PlaneRef
from cotaforge.verification.thresholds import DEFAULT_THRESHOLDS

from .stub import StubAdapter


class _SketchStatusAdapter(StubAdapter):
    """A ``StubAdapter`` whose ``sketch_status`` returns (or raises) a scripted, fixed answer."""

    def __init__(self, status: SketchStatusResult | AdapterError) -> None:
        super().__init__()
        self._status = status

    def sketch_status(self, request: SketchStatusRequest) -> SketchStatusResult:  # type: ignore[override]
        if isinstance(self._status, AdapterError):
            raise self._status
        return self._status


def _resolver(adapter: StubAdapter, **ctx_kwargs: object) -> Resolver:
    ctx = ExecutionContext(document=DocumentHandle(id="doc:1"), **ctx_kwargs)  # type: ignore[arg-type]
    return Resolver(adapter, ctx, DEFAULT_THRESHOLDS)


def _diagnose(
    op: Any,
    *,
    result: OperationResult | None,
    adapter: StubAdapter,
    resolver: Resolver,
    error: ErrorInfo | None = None,
    external_features: frozenset[str] = frozenset(),
) -> Diagnosis:
    return _diagnose_fn(
        op, error=error, result=result, adapter=adapter, resolver=resolver, external_features=external_features
    )


# --- underdefined_sketch --------------------------------------------------------------------------------


def test_underdefined_sketch_direct_is_diagnosed_and_repairable() -> None:
    """A sketch operation whose own attempt still returned a handle
    and whose ``sketch_status`` shows remaining dof is ``underdefined_sketch``, repairable, with the free
    degrees of freedom named in the evidence."""
    status = SketchStatusResult(
        sketch=FeatureHandle(id="sk:1"),
        dof=3,
        geometry_count=1,
        constraint_count=0,
        fully_constrained=False,
        over_constrained=False,
        free_geometry=(0,),
    )
    adapter = _SketchStatusAdapter(status)
    resolver = _resolver(adapter)
    op = SketchOperation(
        id="base_sketch",
        intent="outline of the plate",
        on=PlaneRef(name=PlaneName.XY),
        entities=(RectEntity(name="outline", center=(0.0, 0.0), width=20.0, height=10.0),),
    )
    result = OperationResult(operation_id="base_sketch", feature=None, rebuild_ok=False, sketch=SketchHandle(id="sk:1"))
    diagnosis = _diagnose(op, result=result, adapter=adapter, resolver=resolver)
    assert diagnosis.kind is DiagnosisKind.underdefined_sketch
    assert diagnosis.dof == 3
    assert "dof=3" in diagnosis.evidence
    assert diagnosis.repairable is True
    assert diagnosis.repair is not None
    assert diagnosis.repair.kind == "add_sketch_constraint"


def test_underdefined_sketch_downstream_is_diagnosed_but_not_mechanically_repairable() -> None:
    """A feature op that merely *depends on* an already-built sketch (``op.sketch``) gets the same honest
    diagnosis, but ``_apply_repair`` can only ever mutate the CURRENT op's own request (Executor invariant):
    an ``ExtrudeRequest`` has no field to carry a sketch dimension fix, so this is reported
    with ``repairable=False`` rather than claiming a repair the executor cannot mechanically apply."""
    status = SketchStatusResult(
        sketch=FeatureHandle(id="sk:1"),
        dof=2,
        geometry_count=1,
        constraint_count=0,
        fully_constrained=False,
        over_constrained=False,
        free_geometry=(0,),
    )
    adapter = _SketchStatusAdapter(status)
    resolver = _resolver(adapter, sketch_by_op={"base_sketch": SketchHandle(id="sk:1")})
    op = ExtrudeOperation(id="base", intent="extrude the plate", sketch="base_sketch", depth_mm=8.0)
    result = OperationResult(operation_id="base", feature=FeatureHandle(id="feature:1"), rebuild_ok=False)
    diagnosis = _diagnose(op, result=result, adapter=adapter, resolver=resolver)
    assert diagnosis.kind is DiagnosisKind.underdefined_sketch
    assert diagnosis.dof == 2
    assert diagnosis.repairable is False
    assert diagnosis.repair is None


# --- overdefined_sketch: diagnosed, never repaired ------------------------------------------------


def test_overdefined_sketch_is_diagnosed_and_never_repaired() -> None:
    conflict = ConstraintConflict(kind="redundant", constraint_index=1, constraint_name="width", involved=(0,))
    status = SketchStatusResult(
        sketch=FeatureHandle(id="sk:1"),
        dof=0,
        geometry_count=1,
        constraint_count=2,
        fully_constrained=False,
        over_constrained=True,
        conflicts=(conflict,),
    )
    adapter = _SketchStatusAdapter(status)
    resolver = _resolver(adapter, sketch_by_op={"base_sketch": SketchHandle(id="sk:1")})
    op = ExtrudeOperation(id="base", intent="extrude the plate", sketch="base_sketch", depth_mm=8.0)
    result = OperationResult(operation_id="base", feature=FeatureHandle(id="feature:1"), rebuild_ok=False)
    diagnosis = _diagnose(op, result=result, adapter=adapter, resolver=resolver)
    assert diagnosis.kind is DiagnosisKind.overdefined_sketch
    assert diagnosis.repairable is False
    assert diagnosis.repair is None
    assert diagnosis.conflicts == (conflict,)


# --- rebuild_error: diagnosed, never repaired ------------------------------------------------------


def test_rebuild_error_when_no_sketch_is_involved() -> None:
    op = TranslateOperation(id="mv", intent="move the block", target=BodyRef(from_op="box"), offset=(1.0, 0.0, 0.0))
    adapter = StubAdapter()
    resolver = _resolver(adapter, body_by_op={"box": BodyHandle(id="body:1")})
    result = OperationResult(operation_id="mv", feature=FeatureHandle(id="feature:9"), rebuild_ok=False)
    diagnosis = _diagnose(op, result=result, adapter=adapter, resolver=resolver)
    assert diagnosis.kind is DiagnosisKind.rebuild_error
    assert diagnosis.repairable is False
    assert diagnosis.repair is None


# --- dangling_reference: diagnosed and repairable, except onto a foreign feature ------------------------


def test_dangling_reference_via_adapter_error_is_repairable() -> None:
    op = TranslateOperation(id="mv", intent="move the block", target=BodyRef(from_op="box"), offset=(1.0, 0.0, 0.0))
    adapter = StubAdapter()
    resolver = _resolver(adapter, body_by_op={"box": BodyHandle(id="body:1")})
    error = ErrorInfo(code=ErrorCode.invalid_reference, message="stale reference body:1", recoverable=False)
    diagnosis = _diagnose(op, result=None, adapter=adapter, resolver=resolver, error=error)
    assert diagnosis.kind is DiagnosisKind.dangling_reference
    assert diagnosis.source == "error_code"
    assert diagnosis.reference == "box"
    assert diagnosis.repairable is True
    assert diagnosis.repair is not None
    assert diagnosis.repair.kind == "reresolve_reference"
    assert "invalid_reference" in diagnosis.evidence


def test_dangling_reference_via_missing_context_is_repairable() -> None:
    """No adapter error at all: the resolver's own bookkeeping shows the referenced op was never
    registered (a manually removed feature), which alone is enough to classify the failure."""
    op = TranslateOperation(id="mv", intent="move the block", target=BodyRef(from_op="ghost"), offset=(0.0, 0.0, 1.0))
    adapter = StubAdapter()
    resolver = _resolver(adapter)  # nothing registered for "ghost"
    result = OperationResult(operation_id="mv", feature=None, rebuild_ok=True)
    diagnosis = _diagnose(op, result=result, adapter=adapter, resolver=resolver)
    assert diagnosis.kind is DiagnosisKind.dangling_reference
    assert diagnosis.source == "resolver"
    assert diagnosis.reference == "ghost"
    assert diagnosis.repairable is True


def test_dangling_reference_to_an_external_feature_is_never_repairable() -> None:
    """The guardrail test: a reference into ``external_features`` (a manually adopted alias, never built
    by this plan) must never be reported repairable, no matter what the adapter says."""
    op = TranslateOperation(
        id="mv", intent="move the manual block", target=BodyRef(from_op="manual_block"), offset=(1.0, 0.0, 0.0)
    )
    adapter = StubAdapter()
    resolver = _resolver(adapter, body_by_op={"manual_block": BodyHandle(id="body:9")})
    error = ErrorInfo(code=ErrorCode.invalid_reference, message="stale reference body:9", recoverable=False)
    diagnosis = _diagnose(
        op, result=None, adapter=adapter, resolver=resolver, error=error, external_features=frozenset({"manual_block"})
    )
    assert diagnosis.kind is DiagnosisKind.dangling_reference
    assert diagnosis.reference == "manual_block"
    assert diagnosis.repairable is False
    assert diagnosis.repair is None


# --- unknown: honest fallback, never an exception --------------------------------------------------------


def test_sketch_status_not_supported_is_unknown_not_an_exception() -> None:
    """``StubAdapter`` never overrides ``sketch_status``, so it inherits ``Adapter``'s default body that
    raises ``AdapterError(ErrorCode.not_supported)`` -- exactly the contract default. Diagnosis
    must degrade to ``unknown`` and must never let the exception escape."""
    adapter = StubAdapter()
    resolver = _resolver(adapter, sketch_by_op={"base_sketch": SketchHandle(id="sk:1")})
    op = ExtrudeOperation(id="base", intent="extrude the plate", sketch="base_sketch", depth_mm=8.0)
    result = OperationResult(operation_id="base", feature=FeatureHandle(id="feature:1"), rebuild_ok=False)
    diagnosis = _diagnose(op, result=result, adapter=adapter, resolver=resolver)
    assert diagnosis.kind is DiagnosisKind.unknown
    assert "not_supported" in diagnosis.evidence
    assert diagnosis.repairable is False


def test_no_signal_at_all_is_the_honest_unknown() -> None:
    op = TranslateOperation(id="mv", intent="move the block", target=BodyRef(from_op="box"), offset=(1.0, 0.0, 0.0))
    adapter = StubAdapter()
    resolver = _resolver(adapter, body_by_op={"box": BodyHandle(id="body:1")})
    result = OperationResult(operation_id="mv", feature=None, rebuild_ok=True)  # no_feature, not in the 4 kinds
    diagnosis = _diagnose(op, result=result, adapter=adapter, resolver=resolver)
    assert diagnosis.kind is DiagnosisKind.unknown
    assert diagnosis.repairable is False
