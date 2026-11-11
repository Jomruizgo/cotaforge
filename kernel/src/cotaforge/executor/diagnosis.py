"""Diagnosis of an operation's level-1 failure.

``diagnose`` reads the adapter and the executor's own resolution context -- never guesses -- to classify
*why* an operation failed into one of four kinds. Only two of them are ever auto-repaired
(``underdefined_sketch`` and ``dangling_reference``); ``overdefined_sketch`` and
``rebuild_error`` are always reported with ``repairable=False`` because the only possible "fix" would
either drop a constraint the engineer declared on purpose, or paper over a rebuild failure that has no
single cause. A diagnosis is emitted even when it is not repairable: the value of this module is that the
engineer sees *why* an operation failed, not only whether it did.

``ALLOWED_IMPORTS["executor"] == {"ir", "verification", "contract"}`` (tests/unit/test_dependency_rule.py):
this module only reaches into ``contract`` (for ``ErrorInfo``, ``SketchStatusRequest``/``Result``,
``Adapter``), ``ir`` (``OperationBase.refs()``, ``NON_FEATURE_OP_NAMES``) and sibling ``executor`` modules
(``Resolver``) -- no new edge.

**Guardrail:** the harness may only ever
repair a plan operation it is currently executing, never a feature it did not build. This module never
repairs anything itself (that is ``Executor._apply_repair``'s job); its own share of the guardrail is
that a ``dangling_reference`` diagnosis is marked ``repairable=False`` whenever the implicated reference
names an op id present in ``external_features`` -- a manually-adopted feature the plan did not build.
"""

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from cotaforge.contract import (
    Adapter,
    AdapterError,
    ConstraintConflict,
    ErrorCode,
    ErrorInfo,
    FeatureHandle,
    OperationResult,
    SketchStatusRequest,
)
from cotaforge.executor.resolver import Resolver
from cotaforge.ir import NON_FEATURE_OP_NAMES
from cotaforge.ir.ops_primitives import OperationBase
from cotaforge.verification.level1 import failure_reason

__all__ = ["Diagnosis", "DiagnosisKind", "RepairAction", "diagnose"]


class DiagnosisKind(str, Enum):  # noqa: UP042
    underdefined_sketch = "underdefined_sketch"
    overdefined_sketch = "overdefined_sketch"
    dangling_reference = "dangling_reference"
    rebuild_error = "rebuild_error"
    unknown = "unknown"  # the honest case: we do not know why it failed


class RepairAction(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: Literal["add_sketch_constraint", "reresolve_reference"]
    description: str = Field(min_length=1)
    detail: dict[str, str | float | int] = Field(default_factory=dict)


class Diagnosis(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: DiagnosisKind
    op_id: str
    source: Literal["error_code", "sketch_status", "resolver", "level1"]
    evidence: str = Field(min_length=1)
    dof: int | None = None
    conflicts: tuple[ConstraintConflict, ...] = ()
    reference: str | None = None  # the from_op / alias that did not resolve
    repairable: bool = False
    repair: RepairAction | None = None  # None when repairable is False


# --- reference resolution -----------------------------------------------------------------------------


def _first_unresolved_from_op(op: OperationBase, resolver: Resolver) -> str | None:
    """The first ``from_op`` named by one of ``op``'s references that no longer resolves in the
    executor's own context -- i.e. the reference names an operation whose feature/body/sketch was never
    registered (or was dropped). ``None`` when every reference's provenance is still tracked."""
    ctx = resolver.ctx
    for ref in op.refs():
        from_op = getattr(ref, "from_op", None)
        if from_op is None:
            continue
        if (
            from_op in ctx.feature_by_op
            or from_op in ctx.body_by_op
            or from_op in ctx.sketch_by_op
            or from_op in ctx.datum_plane_by_op
            or from_op in ctx.datum_axis_by_op
        ):
            continue
        return from_op
    return None


def _candidate_reference(op: OperationBase, resolver: Resolver) -> str | None:
    """Best-effort "the" reference implicated in a dangling-reference failure.

    Prefers a ``from_op`` that is provably missing from the execution context; falls back to the op's
    only distinct ``from_op`` (a mate/hole/pattern op names exactly one upstream operation in the common
    case). Ambiguous ops (more than one distinct ``from_op``, none of them provably missing) are left
    unidentified rather than guessed at -- the diagnosis stays ``dangling_reference`` but ``repairable``
    stays ``False`` (nothing to safely re-resolve).
    """
    missing = _first_unresolved_from_op(op, resolver)
    if missing is not None:
        return missing
    from_ops = {getattr(ref, "from_op", None) for ref in op.refs()} - {None}
    if len(from_ops) == 1:
        return next(iter(from_ops))
    return None


def _diagnose_dangling_reference(
    op: OperationBase,
    error: ErrorInfo | None,
    resolver: Resolver,
    external_features: frozenset[str],
    *,
    source: Literal["error_code", "resolver"],
) -> Diagnosis:
    reference = _candidate_reference(op, resolver)
    repairable = reference is not None and reference not in external_features
    parts: list[str] = []
    if error is not None:
        parts.append(f"adapter reported {error.code.value}: {error.message}")
    if reference is not None:
        parts.append(f"reference to op {reference!r} no longer resolves in the execution context")
    else:
        parts.append("no single unresolved reference could be identified among this operation's references")
    repair = None
    if repairable:
        assert reference is not None
        repair = RepairAction(
            kind="reresolve_reference",
            description=f"re-resolve the reference to op {reference!r} by provenance",
            detail={"from_op": reference},
        )
    return Diagnosis(
        kind=DiagnosisKind.dangling_reference,
        op_id=op.id,
        source=source,
        evidence="; ".join(parts),
        reference=reference,
        repairable=repairable,
        repair=repair,
    )


# --- sketch diagnosis -----------------------------------------------------------------------------------


def _sketch_feature_handle(
    op: OperationBase, result: OperationResult | None, resolver: Resolver
) -> tuple[FeatureHandle, bool] | None:
    """The sketch handle relevant to ``op``'s failure, plus whether ``op`` itself is the sketch operation
    that produced it (the only case a repair can mechanically apply to -- see ``diagnose`` docstring).

    Direct case: ``op`` is the ``sketch`` operation and its own (failed) attempt still returned a
    ``SketchHandle`` (an adapter may materialise a degenerate sketch and still hand back a handle).
    Downstream case: ``op`` depends on a sketch built earlier in *this* execution (``op.sketch``,
    tracked in ``resolver.ctx.sketch_by_op`` -- populated only by this executor's own successful steps,
    which is what keeps this from ever pointing at a feature the plan did not build).
    """
    if op.op == "sketch" and result is not None and result.sketch is not None:
        return FeatureHandle(id=result.sketch.id), True
    sketch_op_id = getattr(op, "sketch", None)
    if isinstance(sketch_op_id, str):
        handle = resolver.ctx.sketch_by_op.get(sketch_op_id)
        if handle is not None:
            return FeatureHandle(id=handle.id), False
    return None


def _diagnose_sketch(
    op: OperationBase, result: OperationResult | None, adapter: Adapter, resolver: Resolver
) -> Diagnosis | None:
    """``None`` when ``op`` has no sketch to diagnose, or the diagnosed sketch turns out fine (fully
    constrained, no conflicts) -- the caller then knows the failure has some other cause."""
    found = _sketch_feature_handle(op, result, resolver)
    if found is None:
        return None
    handle, is_own_sketch = found
    try:
        status = adapter.sketch_status(SketchStatusRequest(operation_id=op.id, sketch=handle))
    except AdapterError as exc:
        return Diagnosis(
            kind=DiagnosisKind.unknown,
            op_id=op.id,
            source="sketch_status",
            evidence=f"sketch_status on {handle.id!r} raised {exc.info.code.value}: {exc.info.message}",
            repairable=False,
        )
    if status.over_constrained or status.conflicts:
        return Diagnosis(
            kind=DiagnosisKind.overdefined_sketch,
            op_id=op.id,
            source="sketch_status",
            evidence=(
                f"sketch {handle.id} is over-constrained: dof={status.dof}, "
                f"{len(status.conflicts)} conflict(s): {status.solver_message or 'no solver message'}"
            ),
            dof=status.dof,
            conflicts=status.conflicts,
            repairable=False,
        )
    if not status.fully_constrained and status.dof > 0:
        # Mechanically, a repair can only mutate the CURRENT op's own request (Executor._apply_repair
        # never issues a side-channel call to fix a *different*, already-executed operation -- see the
        # module docstring). A SketchRequest carries entities/constraints/named_dimensions directly, so
        # only the direct case (op.op == "sketch") can actually be repaired this way; a downstream op's
        # request (ExtrudeRequest, CutRequest, ...) has no field to carry a sketch fix, so it is reported
        # honestly with repairable=False rather than claiming a repair that cannot be applied.
        repairable = is_own_sketch
        repair = None
        if repairable:
            repair = RepairAction(
                kind="add_sketch_constraint",
                description=f"re-emit the IR's declared dimensions for {len(status.free_geometry)} free entit(y/ies)",
                detail={"free_geometry": ",".join(str(i) for i in status.free_geometry)},
            )
        return Diagnosis(
            kind=DiagnosisKind.underdefined_sketch,
            op_id=op.id,
            source="sketch_status",
            evidence=f"sketch {handle.id}: dof={status.dof}, free_geometry={list(status.free_geometry)}",
            dof=status.dof,
            repairable=repairable,
            repair=repair,
        )
    return None


# --- public API -------------------------------------------------------------------------------------


def diagnose(
    op: OperationBase,
    *,
    error: ErrorInfo | None,
    result: OperationResult | None,
    adapter: Adapter,
    resolver: Resolver,
    external_features: frozenset[str],
) -> Diagnosis:
    """Classify why ``op``'s attempt failed level 1. Never raises: an adapter that does not support
    ``sketch_status`` (``ErrorCode.not_supported``, the contract default) or any other adapter error
    while diagnosing yields ``DiagnosisKind.unknown`` with the failure named in ``evidence``, not an
    exception -- diagnosis must never crash the retry loop it feeds.
    """
    requires_feature = op.op not in NON_FEATURE_OP_NAMES
    reason = failure_reason(result, error, requires_feature=requires_feature)

    if error is not None and error.code is ErrorCode.invalid_reference:
        return _diagnose_dangling_reference(op, error, resolver, external_features, source="error_code")

    missing = _first_unresolved_from_op(op, resolver)
    if missing is not None:
        return _diagnose_dangling_reference(op, error, resolver, external_features, source="resolver")

    sketch_diagnosis = _diagnose_sketch(op, result, adapter, resolver)
    if sketch_diagnosis is not None:
        return sketch_diagnosis

    if reason == "rebuild_failed":
        message = error.message if error is not None else "rebuild failed"
        return Diagnosis(
            kind=DiagnosisKind.rebuild_error,
            op_id=op.id,
            source="level1",
            evidence=f"level1 failure_reason=rebuild_failed: {message}",
            repairable=False,
        )

    detail = error.message if error is not None else "no further signal"
    return Diagnosis(
        kind=DiagnosisKind.unknown,
        op_id=op.id,
        source="level1",
        evidence=f"level1 failure_reason={reason}: {detail}",
        repairable=False,
    )
