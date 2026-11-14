"""Plan models and plan-level validators (ir/plan.py).

``PlanBase``/``Plan``/``parse_plan`` live here. A ``Plan`` carries the full operation
vocabulary (``ops_features.Operation``) and no stage: an unknown ``op`` fails with a
pydantic "unknown discriminator" error before any validator below runs. The validators
here (V1-V6, V8-V14) run afterwards, in order, on the fully-typed operation list.
"""

import math
from collections.abc import Iterable
from typing import Annotated, Any, Literal

from pydantic import (
    AwareDatetime,
    BaseModel,
    Field,
    FieldSerializationInfo,
    TypeAdapter,
    ValidationError,
    ValidationInfo,
    ValidatorFunctionWrapHandler,
    WrapValidator,
    field_serializer,
    model_validator,
)

from cotaforge.contract import AxisName, DocumentKind, points_collinear, sketch_profiles
from cotaforge.contract.threads import thread, thread_sizes
from cotaforge.ir.errors import PlanError
from cotaforge.ir.extension import ExtensionOperation, is_extension_op
from cotaforge.ir.ops_features import Operation
from cotaforge.ir.ops_primitives import OperationBase
from cotaforge.ir.refs import AxisRef, BodyRef, ComponentRef, EdgeSet, FaceRef, IRModel, OpId, PlaneRef

Point2 = tuple[float, float]
_AXIS_SIDE_TOLERANCE_MM = 1e-6
_DATUM_ZERO_LENGTH_MM = 1e-9
_SWEEP_ZERO_LENGTH_MM = 1e-6
_SWEEP_PARALLEL_RAD = 1e-6
_TAP_DRILL_TOLERANCE_MM = 1e-6

__all__ = [
    "ALLOW_STAMP_CONTEXT",
    "EXTERNAL_IDS_CONTEXT",
    "Plan",
    "PlanAdapter",
    "PlanBase",
    "ValidationStamp",
    "parse_plan",
    "plan_json_schema",
]


class ValidationStamp(IRModel):
    """A fact the harness itself records: this plan ran to completion with every level-2 check
    asserted and green, on ``adapter``, and the trace that proves it is ``traces/<trace>.json``.

    Never written by a document (see ``parse_plan``'s ``allow_stamp``): only
    the harness derives one, from an ``ExecutionTrace``, and writes it onto a stored plan.
    """

    adapter: str = Field(min_length=1)
    at: AwareDatetime
    trace: str = Field(min_length=1)


ASSEMBLY_OP_NAMES: frozenset[str] = frozenset(
    {
        "insert_component",
        "fix_component",
        "place_component",
        "add_joint",
        "set_joint",
        "mate_concentric",
        "mate_coincident",
        "mate_distance",
        "check_interference",
    }
)

# BodyRef.from_op must target one of these (V3).
_BODY_PRODUCING_OP_NAMES: frozenset[str] = frozenset(
    {
        "box",
        "cylinder",
        "boolean_union",
        "boolean_subtract",
        "boolean_intersect",
        "translate",
        "rotate",
        "copy_body",
        "mirror_body",
        "shrink_body",
        "mould_split",
        "split",
        "extrude",
        "revolve",
        "sweep",
        "base_flange",
    }
)

# FaceRef.from_op must NOT target one of these (V3). An unfold makes a flat pattern outside the solid: no faces to name.
_FACE_FORBIDDEN_SOURCES: frozenset[str] = frozenset(
    {
        "sketch",
        "ref_plane",
        "ref_axis",
        "check_interference",
        "fix_component",
        "place_component",
        "add_joint",
        "set_joint",
        "unfold",
    }
)

# `features` entries must NOT target one of these (V3).
_FEATURE_FORBIDDEN_SOURCES: frozenset[str] = frozenset(
    {"sketch", "ref_plane", "ref_axis", "check_interference", "unfold"}
)


class PlanBase(IRModel):
    id: OpId
    kind: DocumentKind = DocumentKind.part
    units: Literal["mm"] = "mm"
    description: str = ""
    validated: ValidationStamp | None = None
    """Set only by the harness (see ``ValidationStamp``), never by a document; defaults to ``None``
    so every ``plans/*.json`` and every YAML written before this field existed keeps validating."""


def _route_extension(value: Any, handler: ValidatorFunctionWrapHandler) -> Any:
    """An operation the adapter declared (``ext:<name>``) is an ``ExtensionOperation``; every other goes to the closed
    vocabulary exactly as before, so a bad common operation fails with the same error."""
    if isinstance(value, ExtensionOperation):
        return value
    if isinstance(value, dict) and is_extension_op(value.get("op")):
        return ExtensionOperation.model_validate(value)
    return handler(value)


PlanOperation = Annotated[Operation, WrapValidator(_route_extension)]
"""The operations a plan holds: the common vocabulary plus the operations an adapter declared (``ext:<name>``)."""


class Plan(PlanBase):
    operations: tuple[PlanOperation, ...]

    @field_serializer("operations")
    def _dump_operations(self, operations: tuple[OperationBase, ...], info: FieldSerializationInfo) -> Any:
        """Each operation dumps itself: the field's own schema knows the common vocabulary only, and warns on (and may
        mis-serialize) an operation the adapter declared."""
        dumped = [
            op.model_dump(
                mode=info.mode,
                by_alias=info.by_alias,
                exclude_unset=info.exclude_unset,
                exclude_defaults=info.exclude_defaults,
                exclude_none=info.exclude_none,
                round_trip=info.round_trip,
            )
            for op in operations
        ]
        return dumped if info.mode == "json" else tuple(dumped)

    @model_validator(mode="after")
    def _validate_plan(self, info: ValidationInfo) -> "Plan":
        _run_validators(self, _external_ids(info), _allow_stamp(info))
        return self


PlanAdapter: TypeAdapter = TypeAdapter(Plan)


def plan_json_schema() -> dict:
    """JSON schema for ``Plan``."""
    return PlanAdapter.json_schema()


EXTERNAL_IDS_CONTEXT = "external_ids"
"""Validation-context key: ids a reference may target although no operation of the plan carries them.

A live session can adopt a feature the engineer made by hand in the CAD (``origin=manual``) under an alias op id; V2
then accepts ``from_op``/``features`` refs to that alias and V3/V5/V6 skip it (no operation to type-check). Only
``parse_plan(..., external_ids=...)`` sets it — a stored plan document re-parsed without the context fails V2 as it
should, because a manual feature cannot be rebuilt from the plan alone.
"""


def _external_ids(info: ValidationInfo) -> frozenset[str]:
    context = info.context or {}
    ids = context.get(EXTERNAL_IDS_CONTEXT, ()) if isinstance(context, dict) else ()
    return frozenset(str(item) for item in ids)


ALLOW_STAMP_CONTEXT = "allow_stamp"
"""Validation-context key: whether a ``validated`` stamp is allowed to be present in the parsed data
(see ``parse_plan``'s ``allow_stamp`` — a stamp is a fact the harness records, never
something a document can declare for itself)."""


def _allow_stamp(info: ValidationInfo) -> bool:
    context = info.context or {}
    return bool(context.get(ALLOW_STAMP_CONTEXT, False)) if isinstance(context, dict) else False


def parse_plan(data: dict, *, external_ids: Iterable[str] = (), allow_stamp: bool = False) -> Plan:
    """Validate ``data`` against ``Plan`` and return it, raising ``PlanError``.

    ``external_ids`` (see ``EXTERNAL_IDS_CONTEXT``) are extra ids V2 accepts as reference targets.

    ``allow_stamp`` (default ``False``): a ``validated`` stamp present in ``data`` is rejected as a
    ``PlanError`` unless ``allow_stamp=True``. A validated plan is a fact the harness itself records
    after CAD-level verification, never
    something an incoming document can claim for itself. Callers parsing user/agent input
    (``ir.loader.load_plan``) keep the default; only the
    workspace's own re-read of an already-stamped stored plan and ``ir.template`` (loading a
    ``PlanTemplate``, whose embedded plan already carries its own stamp) pass ``allow_stamp=True``.
    """
    context = {EXTERNAL_IDS_CONTEXT: frozenset(external_ids), ALLOW_STAMP_CONTEXT: allow_stamp}
    try:
        return PlanAdapter.validate_python(data, context=context)
    except ValidationError as exc:
        raise _wrap_validation_error(data, exc) from exc


def _wrap_validation_error(data: dict, exc: ValidationError) -> PlanError:
    """Unwrap a ``PlanError`` raised by one of our own validators, or build a best-effort one."""
    for error in exc.errors():
        original = error.get("ctx", {}).get("error")
        if isinstance(original, PlanError):
            return original
    return _fallback_plan_error(data, exc)


def _fallback_plan_error(data: dict, exc: ValidationError) -> PlanError:
    errors = exc.errors()
    first = errors[0] if errors else {"msg": str(exc), "loc": ()}
    plan_id = data.get("id") if isinstance(data, dict) else None
    loc = tuple(first.get("loc", ()))
    op_id = _derive_op_id(data, loc)
    message = str(first.get("msg", str(exc)))
    if loc:
        message = f"{message} at {'.'.join(str(part) for part in loc)}"
    return PlanError(plan_id=plan_id, op_id=op_id, rule="schema", message=message)


def _derive_op_id(data: object, loc: tuple) -> str | None:
    if not isinstance(data, dict):
        return None
    operations = data.get("operations")
    if not isinstance(operations, list):
        return None
    if len(loc) >= 2 and loc[0] == "operations" and isinstance(loc[1], int) and 0 <= loc[1] < len(operations):
        entry = operations[loc[1]]
        if isinstance(entry, dict):
            op_id = entry.get("id")
            return op_id if isinstance(op_id, str) else None
    return None


# --- Validators V1-V14 (V10 = datum definitions, V11 = sweep paths, V12 = hole threads) ---
# V7 (stage vocabulary) no longer exists: codes are kept stable, so V7 is intentionally a gap.


def _run_validators(plan: Plan, external_ids: frozenset[str] = frozenset(), allow_stamp: bool = False) -> None:
    _v1_unique_ids(plan)
    _v2_forward_refs(plan, external_ids)
    _v3_ref_types(plan)
    _v4_kind_ops(plan)
    _v5_revolve_axis(plan)
    _v6_sketch_profiles(plan)
    _v8_revolve_profile_side(plan)
    _v9_stamp_provenance(plan, allow_stamp)
    _v10_datum_definitions(plan)
    _v11_sweep_paths(plan)
    _v12_hole_threads(plan)
    _v13_draft_pull(plan)
    _v14_finite_numbers(plan)


def _non_finite(value: object, path: str) -> str | None:
    """The path of the first ``nan``/``inf`` reachable from ``value`` (fields, tuples, lists, dicts), or ``None``."""
    if isinstance(value, float):
        return None if math.isfinite(value) else path
    if isinstance(value, BaseModel):
        items: Iterable[tuple[str, object]] = ((name, getattr(value, name)) for name in type(value).model_fields)
        return next((hit for name, item in items if (hit := _non_finite(item, f"{path}.{name}"))), None)
    if isinstance(value, (tuple, list)):
        return next((hit for i, item in enumerate(value) if (hit := _non_finite(item, f"{path}[{i}]"))), None)
    if isinstance(value, dict):
        return next((hit for key, item in value.items() if (hit := _non_finite(item, f"{path}.{key}"))), None)
    return None


def _v14_finite_numbers(plan: Plan) -> None:
    """V14: no number of any operation may be ``nan`` or ``inf`` (a YAML ``.inf`` or a JSON ``NaN`` parse as floats).
    They pass the ``> 0`` checks of the fields (``inf > 0``) or slip through the ``<= 0`` ones (``nan <= 0`` is false),
    and a CAD must never receive them. The datum, sweep and draft validators above name their own cases first."""
    for op in plan.operations:
        for name in type(op).model_fields:
            hit = _non_finite(getattr(op, name), name)
            if hit is not None:
                raise PlanError(
                    plan.id, op.id, "V14", f"{op.id}.{hit} must be a finite number (nan and inf are refused)"
                )


def _v1_unique_ids(plan: Plan) -> None:
    seen: set[str] = set()
    for op in plan.operations:
        if op.id in seen:
            raise PlanError(plan.id, op.id, "V1", f"duplicate operation id: {op.id!r}")
        seen.add(op.id)


def _v9_stamp_provenance(plan: Plan, allow_stamp: bool) -> None:
    """A ``validated`` stamp is a fact the harness records after CAD-level verification, never
    something a document can declare for itself.
    ``parse_plan(..., allow_stamp=True)`` is the only door, used solely for the workspace's own
    re-read of a stored plan and for the plan embedded in a ``PlanTemplate``."""
    if plan.validated is not None and not allow_stamp:
        raise PlanError(plan.id, None, "V9", "a validated stamp may not be declared by an input document")


def _finite(*values: float) -> bool:
    return all(math.isfinite(value) for value in values)


def _datum_problem(op: OperationBase) -> str | None:
    """What is statically wrong with a ``ref_plane`` / ``ref_axis`` definition (numbers only: anything that
    needs resolved geometry, e.g. "the axis lies in the base plane", is checked at run time), or ``None``."""
    define = op.define  # type: ignore[attr-defined]
    how = define.how
    if op.op == "ref_plane":
        if how == "offset" and not _finite(define.distance_mm):
            return "ref_plane.define.distance_mm must be a finite number"
        if how == "angle" and not _finite(define.angle_deg):
            return "ref_plane.define.angle_deg must be a finite number"
        if how == "normal_to" and not _finite(define.at_mm):
            return "ref_plane.define.at_mm must be a finite number"
        if how == "three_points":
            if not all(_finite(*point) for point in define.points):
                return "ref_plane.define.points must be finite numbers"
            if points_collinear(*define.points):
                return "ref_plane.define.points are collinear: choose three non-collinear points"
        return None
    if how == "two_points":
        if not all(_finite(*point) for point in define.points):
            return "ref_axis.define.points must be finite numbers"
        start, end = define.points
        if math.dist(start, end) < _DATUM_ZERO_LENGTH_MM:
            return "ref_axis.define.points are coincident: choose two distinct points"
    if how == "point_direction":
        if not _finite(*define.point):
            return "ref_axis.define.point must be finite numbers"
        if not isinstance(define.direction, AxisName) and (
            not _finite(*define.direction) or math.hypot(*define.direction) < _DATUM_ZERO_LENGTH_MM
        ):
            return "ref_axis.define.direction must be a finite, non-zero vector"
    return None


def _v10_datum_definitions(plan: Plan) -> None:
    """V10: a datum whose numbers cannot define a plane/axis (collinear points, coincident points, a zero
    direction, NaN/inf) fails at plan time instead of at the adapter."""
    for op in plan.operations:
        if op.op not in ("ref_plane", "ref_axis"):
            continue
        problem = _datum_problem(op)
        if problem is not None:
            raise PlanError(plan.id, op.id, "V10", problem)


def _sweep_path_problem(op: OperationBase) -> str | None:
    """What is statically wrong with a ``sweep`` path (numbers only; whether the profile sits on the path start
    or the radius fits the section needs the resolved sketch and is checked by the executor), or ``None``.

    The corner test uses the turn angle at each vertex (the angle between the incoming and outgoing directions),
    not the angle seen from the first point of the triple, so a very short segment next to a long one is judged
    the way ``verification.sweep.analyse_path`` will judge it."""
    path = op.path  # type: ignore[attr-defined]
    points = path.points
    if not all(_finite(*point) for point in points):
        return "sweep.path.points must be finite numbers"
    if path.corner_radius_mm is not None and not _finite(path.corner_radius_mm):
        return "sweep.path.corner_radius_mm must be a finite number"
    directions = []
    for index in range(len(points) - 1):
        length = math.dist(points[index], points[index + 1])
        if length <= _SWEEP_ZERO_LENGTH_MM:
            return f"sweep.path points {index} and {index + 1} coincide: remove the duplicate point"
        directions.append(tuple((b - a) / length for a, b in zip(points[index], points[index + 1], strict=True)))
    for index in range(1, len(points) - 1):
        d_in, d_out = directions[index - 1], directions[index]
        dot = sum(a * b for a, b in zip(d_in, d_out, strict=True))
        cross = math.sqrt(
            (d_in[1] * d_out[2] - d_in[2] * d_out[1]) ** 2
            + (d_in[2] * d_out[0] - d_in[0] * d_out[2]) ** 2
            + (d_in[0] * d_out[1] - d_in[1] * d_out[0]) ** 2
        )
        turn = math.atan2(cross, dot)
        if turn <= _SWEEP_PARALLEL_RAD:
            return f"sweep.path point {index} is collinear with its neighbours: drop the redundant point"
        if math.pi - turn <= _SWEEP_PARALLEL_RAD:
            return f"sweep.path reverses on itself at point {index}: a sweep cannot fold back over a segment"
    return None


def _v11_sweep_paths(plan: Plan) -> None:
    """V11: a sweep path that cannot be swept (NaN/inf, coincident points, a reversal, a redundant collinear
    vertex) fails at plan time instead of at the adapter."""
    for op in plan.operations:
        if op.op != "sweep":
            continue
        problem = _sweep_path_problem(op)
        if problem is not None:
            raise PlanError(plan.id, op.id, "V11", problem)


def _hole_thread_problem(op: OperationBase) -> str | None:
    """What is statically wrong with a threaded hole (numbers and the thread table only), or ``None``.

    A tapped hole is DRILLED at the tap drill of its size, whatever the plan says: a different ``diameter_mm`` would
    describe a hole the CAD never makes, so it is refused here (omit it and the table fills it in)."""
    standard, size = op.standard.value, op.size  # type: ignore[attr-defined]
    thread_depth, depth = op.thread_depth_mm, op.depth_mm  # type: ignore[attr-defined]
    if thread_depth is not None and standard != "iso_tapped":
        return f"hole.thread_depth_mm only applies to standard=iso_tapped, not {standard}"
    if standard != "iso_tapped":
        return None
    spec = thread(size) if size is not None else None
    if spec is None:
        return (
            f"hole.size {size!r} is not in the ISO metric thread table (coarse: {', '.join(thread_sizes())}; "
            "fine: M<diameter>x<pitch> of ISO 261, e.g. M45x1.5)"
        )
    if abs(op.diameter_mm - spec.tap_drill_mm) > _TAP_DRILL_TOLERANCE_MM:  # type: ignore[attr-defined]
        return f"tap drill for {size} is {spec.tap_drill_mm:g} mm; omit diameter_mm (it is derived from the size)"
    if thread_depth is not None and depth is not None and thread_depth > depth:
        return f"hole.thread_depth_mm ({thread_depth:g}) cannot exceed depth_mm ({depth:g})"
    return None


def _v12_hole_threads(plan: Plan) -> None:
    """V12: a tapped hole whose diameter is not the tap drill of its size, a size outside the thread table, a thread
    longer than the hole, or a thread length on a hole that is not tapped fail at plan time."""
    for op in plan.operations:
        if op.op != "hole":
            continue
        problem = _hole_thread_problem(op)
        if problem is not None:
            raise PlanError(plan.id, op.id, "V12", problem)


def _v13_draft_pull(plan: Plan) -> None:
    """V13: a draft whose pull vector has no direction (NaN/inf or zero length) fails at plan time. The angle range
    (0 < angle <= 30) is the field's own constraint; whether the pull is perpendicular to the neutral plane needs the
    resolved plane and is checked by the executor."""
    for op in plan.operations:
        if op.op != "draft":
            continue
        pull = op.pull  # type: ignore[attr-defined]
        if isinstance(pull, AxisName):
            continue
        if not _finite(*pull) or math.hypot(*pull) < _DATUM_ZERO_LENGTH_MM:
            raise PlanError(plan.id, op.id, "V13", "draft.pull must be a finite, non-zero vector or an axis name")


def _direct_id_refs(op: OperationBase) -> list[tuple[str, str]]:
    """(referenced_id, expected_kind) pairs from direct OpId-typed fields (not Ref instances)."""
    name = op.op
    refs: list[tuple[str, str]] = []
    if name in ("extrude", "cut", "revolve", "sweep", "base_flange"):
        refs.append((op.sketch, "sketch"))  # type: ignore[attr-defined]
    if name in ("linear_pattern", "circular_pattern", "mirror"):
        refs.extend((feature_id, "feature") for feature_id in op.features)  # type: ignore[attr-defined]
    if name in ("fix_component", "place_component"):
        refs.append((op.component, "insert_component"))  # type: ignore[attr-defined]
    if name == "add_joint":
        refs.append((op.b, "insert_component"))  # type: ignore[attr-defined]
        if op.a is not None:  # type: ignore[attr-defined]
            refs.append((op.a, "insert_component"))  # type: ignore[attr-defined]
    if name == "set_joint":
        refs.append((op.joint, "add_joint"))  # type: ignore[attr-defined]
    if name == "check_interference" and op.components is not None:  # type: ignore[attr-defined]
        refs.extend((component_id, "insert_component") for component_id in op.components)  # type: ignore[attr-defined]
    return refs


def _ref_id_kinds(op: OperationBase) -> list[tuple[str, str]]:
    """(referenced_id, expected_kind) pairs from Ref.from_op fields (FaceRef, BodyRef, PlaneRef, AxisRef, ComponentRef).

    A ``FaceRef`` named by ``from_selection`` has no ``from_op`` at all — it names a live CAD
    selection directly, not a plan operation — so it must be skipped here rather than surfacing its
    ``None`` as a dangling reference to operation id ``None`` (V2/V3 would otherwise reject every
    ``from_selection`` ref outright). ``BodyRef.from_op`` has no such case: it is always required.
    """
    out: list[tuple[str, str]] = []
    for ref in op.refs():
        if isinstance(ref, FaceRef):
            if ref.from_op is not None:
                out.append((ref.from_op, "face"))
        elif isinstance(ref, PlaneRef):
            if ref.from_op is not None:
                out.append((ref.from_op, "datum_plane"))
        elif isinstance(ref, AxisRef):
            out.append((ref.from_op, "datum_axis"))
        elif isinstance(ref, BodyRef):
            out.append((ref.from_op, "body"))
        elif isinstance(ref, ComponentRef):
            out.append((ref.from_op, "component"))
        elif isinstance(ref, EdgeSet) and ref.from_op is not None:
            out.append((ref.from_op, "feature"))
    return out


def _all_referenced(op: OperationBase) -> list[tuple[str, str]]:
    return _direct_id_refs(op) + _ref_id_kinds(op)


def _v2_forward_refs(plan: Plan, external_ids: frozenset[str] = frozenset()) -> None:
    positions = {op.id: index for index, op in enumerate(plan.operations)}
    for index, op in enumerate(plan.operations):
        for ref_id, _kind in _all_referenced(op):
            if ref_id in external_ids and ref_id not in positions:
                continue  # an adopted manual feature (EXTERNAL_IDS_CONTEXT): resolved by the executor's context
            position = positions.get(ref_id)
            if position is None or position >= index:
                raise PlanError(plan.id, op.id, "V2", f"reference to unknown or later operation: {ref_id!r}")


def _v3_ref_types(plan: Plan) -> None:
    by_id = {op.id: op for op in plan.operations}
    for op in plan.operations:
        for ref_id, kind in _all_referenced(op):
            target = by_id.get(ref_id)
            if target is None:
                continue  # already reported by V2
            if kind == "face" and target.op in _FACE_FORBIDDEN_SOURCES:
                _raise_v3(plan, op, "face", ref_id, target)
            elif kind == "body" and target.op not in _BODY_PRODUCING_OP_NAMES and not is_extension_op(target.op):
                _raise_v3(plan, op, "body", ref_id, target)
            elif kind == "sketch" and target.op != "sketch":
                _raise_v3(plan, op, "sketch", ref_id, target)
            elif kind == "feature" and target.op in _FEATURE_FORBIDDEN_SOURCES:
                _raise_v3(plan, op, "feature", ref_id, target)
            elif kind == "insert_component" and target.op != "insert_component":
                _raise_v3(plan, op, "component", ref_id, target)
            elif kind == "component" and target.op != "insert_component" and not is_extension_op(target.op):
                _raise_v3(plan, op, "component", ref_id, target)  # an adapter operation may return one as well
            elif kind == "add_joint" and target.op != "add_joint":
                _raise_v3(plan, op, "joint", ref_id, target)
            elif kind == "datum_plane" and target.op != "ref_plane" and not is_extension_op(target.op):
                _raise_v3(plan, op, "datum_plane", ref_id, target)
            elif kind == "datum_axis" and target.op != "ref_axis" and not is_extension_op(target.op):
                # an adapter operation may return a plane or an axis as well (DatumPlaneResult / DatumAxisResult)
                _raise_v3(plan, op, "datum_axis", ref_id, target)
    _v3_body_sides(plan, by_id)


def _v3_body_sides(plan: Plan, by_id: dict[str, OperationBase]) -> None:
    """``BodyRef.side`` names one piece of a ``split`` that kept both: required for every body ref to such a split,
    forbidden for a ref to anything else (a split that kept one piece included: its survivor has no sibling)."""
    for op in plan.operations:
        for ref in op.refs():
            if not isinstance(ref, BodyRef):
                continue
            target = by_id.get(ref.from_op)
            if target is None:
                continue  # already reported by V2 (or an adopted manual feature)
            splits_both = target.op == "split" and target.keep == "both"  # type: ignore[attr-defined]
            if splits_both and ref.side is None:
                msg = (
                    f"body ref to split {ref.from_op!r} (keep=both) needs side 'positive' or 'negative': "
                    "the split made two bodies"
                )
                raise PlanError(plan.id, op.id, "V3", msg)
            if not splits_both and ref.side is not None:
                why = (
                    f"split {ref.from_op!r} keeps one piece"
                    if target.op == "split"
                    else f"{ref.from_op!r} is a {target.op!r}, not a split"
                )
                raise PlanError(
                    plan.id, op.id, "V3", f"body ref side {ref.side!r} only applies to a split with keep=both: {why}"
                )


def _raise_v3(plan: Plan, op: OperationBase, kind: str, ref_id: str, target: OperationBase) -> None:
    raise PlanError(
        plan.id,
        op.id,
        "V3",
        f"wrong operation type for reference: {kind} ref to {ref_id!r} (op={target.op!r})",
    )


def _v4_kind_ops(plan: Plan) -> None:
    for op in plan.operations:
        if plan.kind == DocumentKind.part and op.op in ASSEMBLY_OP_NAMES:
            raise PlanError(plan.id, op.id, "V4", f"operation not allowed in this document kind: {op.op!r} in a part")
        if (
            plan.kind == DocumentKind.assembly
            and op.op not in ASSEMBLY_OP_NAMES
            and op.op != "sketch"
            and not is_extension_op(op.op)  # the server checks the kinds the adapter declared for its own operation
        ):
            raise PlanError(
                plan.id, op.id, "V4", f"operation not allowed in this document kind: {op.op!r} in an assembly"
            )


def _v5_revolve_axis(plan: Plan) -> None:
    by_id = {op.id: op for op in plan.operations}
    for op in plan.operations:
        if op.op != "revolve":
            continue
        axis = op.axis  # type: ignore[attr-defined]
        if axis in ("u", "v"):
            continue
        sketch_op = by_id.get(op.sketch)  # type: ignore[attr-defined]
        entity = None
        if sketch_op is not None and sketch_op.op == "sketch":
            entity = next((e for e in sketch_op.entities if e.name == axis), None)  # type: ignore[attr-defined]
        if entity is None or entity.kind != "line":
            raise PlanError(plan.id, op.id, "V5", f"revolve axis entity not found: {axis!r}")


def sheet_outline_problem(profiles: list) -> str | None:
    """A sheet is its largest closed profile (the outline); every other profile of the sketch is a hole or cut-out and
    must lie inside the outline's bounding box, clear of its edges. ``None`` when the sketch reads that way."""
    outline = max(profiles, key=lambda p: p.area_mm2)
    (ou0, ov0), (ou1, ov1) = outline.bbox_uv
    for profile in profiles:
        if profile is outline:
            continue
        (u0, v0), (u1, v1) = profile.bbox_uv
        if not (u0 > ou0 and v0 > ov0 and u1 < ou1 and v1 < ov1):
            return (
                "the largest closed profile is the sheet's outline and every other one is a hole: a profile at "
                f"({u0:g}, {v0:g})-({u1:g}, {v1:g}) is not inside the outline ({ou0:g}, {ov0:g})-({ou1:g}, {ov1:g})"
            )
    return None


def _v6_sketch_profiles(plan: Plan) -> None:
    by_id = {op.id: op for op in plan.operations}
    for op in plan.operations:
        if op.op not in ("extrude", "cut", "revolve", "sweep", "base_flange"):
            continue
        sketch_op = by_id.get(op.sketch)  # type: ignore[attr-defined]
        if sketch_op is None or sketch_op.op != "sketch":
            continue  # already reported by V2/V3
        try:
            profiles = sketch_profiles(sketch_op.entities)  # type: ignore[attr-defined]
        except ValueError as exc:
            raise PlanError(plan.id, op.id, "V6", f"sketch has no closed profile: {exc}") from exc
        if not profiles:
            raise PlanError(plan.id, op.id, "V6", f"sketch has no closed profile: {op.sketch!r}")  # type: ignore[attr-defined]
        if op.op == "base_flange":
            problem = sheet_outline_problem(profiles)
            if problem is not None:
                raise PlanError(plan.id, op.id, "V6", f"base_flange sketch {op.sketch!r}: {problem}")  # type: ignore[attr-defined]
        if op.op == "sweep" and len(profiles) != 1:
            raise PlanError(
                plan.id,
                op.id,
                "V6",
                f"sweep needs exactly ONE closed profile; sketch {op.sketch!r} has {len(profiles)} "  # type: ignore[attr-defined]
                "(a ring section is two profiles: not supported in v1)",
            )


def _v8_revolve_profile_side(plan: Plan) -> None:
    """A revolve profile must lie entirely on ONE side of its axis (points on the axis are fine): a profile
    that crosses the axis would sweep through itself and no adapter revolves it meaningfully. Checked in sketch (u, v)
    coordinates: ``axis: u`` is the line v = 0, ``axis: v`` the line u = 0, a named line entity the line through its
    start and end."""
    by_id = {op.id: op for op in plan.operations}
    for op in plan.operations:
        if op.op != "revolve":
            continue
        sketch_op = by_id.get(op.sketch)  # type: ignore[attr-defined]
        if sketch_op is None or sketch_op.op != "sketch":
            continue  # already reported by V2/V3
        axis_line = _revolve_axis_line(op.axis, sketch_op.entities)  # type: ignore[attr-defined]
        if axis_line is None:
            continue  # already reported by V5
        try:
            profiles = sketch_profiles(sketch_op.entities)  # type: ignore[attr-defined]
        except ValueError:
            continue  # already reported by V6
        for profile in profiles:
            sides = {_side_of_line(point, axis_line) for point in _profile_sample_points(profile, axis_line)}
            if sides >= {-1, 1}:
                raise PlanError(
                    plan.id,
                    op.id,
                    "V8",
                    f"revolve profile crosses the axis: every point of the profile in sketch {op.sketch!r} must "  # type: ignore[attr-defined]
                    f"lie on one side of axis {op.axis!r} (points on the axis are allowed)",  # type: ignore[attr-defined]
                )


def _revolve_axis_line(axis: str, entities: tuple) -> tuple[Point2, Point2] | None:
    if axis == "u":
        return ((0.0, 0.0), (1.0, 0.0))
    if axis == "v":
        return ((0.0, 0.0), (0.0, 1.0))
    entity = next((e for e in entities if e.name == axis), None)
    if entity is None or entity.kind != "line":
        return None
    return (tuple(entity.start), tuple(entity.end))  # type: ignore[return-value]


def _side_of_line(point: Point2, line: tuple[Point2, Point2]) -> int:
    (ax, ay), (bx, by) = line
    cross = (bx - ax) * (point[1] - ay) - (by - ay) * (point[0] - ax)
    if abs(cross) <= _AXIS_SIDE_TOLERANCE_MM * max(1.0, abs(bx - ax) + abs(by - ay)):
        return 0
    return 1 if cross > 0 else -1


def _profile_sample_points(profile: object, line: tuple[Point2, Point2]) -> list[Point2]:
    """Points that bound the profile on both sides of the axis: polygon vertices, rect corners, or the two
    circle points farthest from the axis line on either side."""
    kind = getattr(profile, "kind", None)
    if kind == "circle":
        (cx, cy), radius = profile.center, profile.radius  # type: ignore[attr-defined]
        (ax, ay), (bx, by) = line
        length = ((bx - ax) ** 2 + (by - ay) ** 2) ** 0.5 or 1.0
        nx, ny = -(by - ay) / length, (bx - ax) / length
        return [(cx + nx * radius, cy + ny * radius), (cx - nx * radius, cy - ny * radius)]
    if kind == "rect":
        (u0, v0), (u1, v1) = profile.bbox_uv  # type: ignore[attr-defined]
        return [(u0, v0), (u1, v0), (u1, v1), (u0, v1)]
    return list(profile.points or ())  # type: ignore[attr-defined]
