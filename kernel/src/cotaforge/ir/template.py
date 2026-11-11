"""Plan templates (ir/template.py).

A ``PlanTemplate`` packages a plan that already passed level 2 (its ``validated`` stamp is mandatory:
see ``PlanTemplate`` below — there is no template of an unvalidated plan) together with a handful of
named, numeric knobs (``TemplateParam``) that each point at one field inside one operation of that
plan. ``instantiate`` substitutes chosen values for those knobs and returns a brand new plan — always
unvalidated (``instantiate(...).validated is None``, always): an instance with different dimensions has
not itself run on a CAD adapter, and proving that it still passes level 2 is exactly what a template
user has to do next.

Field-path convention (the plan is silent on the exact mechanics, so this is the implementer's choice,
kept deliberately simple): ``TemplateParam.field`` is a dotted path resolved *inside the one operation*
named by ``op_id`` — never across operations. Each segment either names a field on the current pydantic
model (``"diameter_mm"``, ``"depth_mm"``), or, when the current value is a tuple/list (e.g.
``SketchOperation.entities``), names an entry by its own ``.name`` attribute before continuing
(``"entities.plate.width"``). The terminal value must be numeric (``int``/``float``, never ``bool``).
"""

from collections.abc import Mapping
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, ValidationError, model_validator

from cotaforge.ir.errors import PlanError, TemplateError
from cotaforge.ir.ops_primitives import OperationBase
from cotaforge.ir.plan import ALLOW_STAMP_CONTEXT, Plan, ValidationStamp, parse_plan
from cotaforge.ir.refs import OpId

__all__ = ["PlanTemplate", "TemplateParam", "field_value", "instantiate", "parse_template"]

_OpIdAdapter: TypeAdapter = TypeAdapter(OpId)


class TemplateParam(BaseModel):
    """One named, numeric knob of a ``PlanTemplate``, pointing at ``field`` inside operation ``op_id``
    of the template's own ``plan``. ``default`` is read from the plan when the template is saved —
    never declared by hand — so a template can never claim a default that was never actually built and
    passed level 2 (see ``PlanTemplate`` docstring)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    op_id: OpId
    field: str = Field(min_length=1)
    default: float
    unit: Literal["mm", "deg"] = "mm"
    description: str = ""
    minimum: float | None = None
    maximum: float | None = None


class PlanTemplate(BaseModel):
    """A validated plan (``plan``) plus the params that let an engineer re-instantiate it with other
    dimensions without editing YAML by hand.

    ``validated`` is mandatory (unlike ``PlanBase.validated``, which is ``None`` by default): a plan
    that never passed level 2 on a CAD adapter cannot become a template (the converse of the stamp rule — the stamp is
    a fact the harness records, and a template is a claim built entirely on top of that fact).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schema_version: Literal[1] = 1
    template_id: str = Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")
    title: str = Field(min_length=1)
    description: str = ""
    params: tuple[TemplateParam, ...] = Field(min_length=1)
    plan: Plan
    source_plan_id: str
    validated: ValidationStamp
    created_by: str = Field(min_length=1)
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _validate_template(self) -> "PlanTemplate":
        _check_source_plan_id(self)
        _check_validated_matches_plan(self)
        _check_unique_param_names(self)
        _check_params_resolve(self)
        _check_params_not_duplicated(self)
        _check_param_bounds(self)
        return self


def parse_template(data: dict) -> PlanTemplate:
    """Validate ``data`` against ``PlanTemplate`` and return it, raising ``TemplateError``.

    Mirrors ``ir.plan.parse_plan``'s wrap/unwrap idiom :
    the model validators above raise ``TemplateError`` directly, pydantic wraps that into a
    ``ValidationError``, and this function unwraps it back. The embedded ``plan`` is parsed with
    ``ALLOW_STAMP_CONTEXT`` on (loading a stored template): a ``PlanTemplate`` requires a
    non-``None`` ``validated`` that must equal the embedded plan's own stamp (see
    ``_check_validated_matches_plan``), so the embedded plan legitimately carries one on every
    re-read from storage.
    """
    try:
        return PlanTemplate.model_validate(data, context={ALLOW_STAMP_CONTEXT: True})
    except ValidationError as exc:
        raise _wrap_template_error(exc) from exc


def _wrap_template_error(exc: ValidationError) -> TemplateError:
    for error in exc.errors():
        original = error.get("ctx", {}).get("error")
        if isinstance(original, TemplateError):
            return original
        if isinstance(original, PlanError):
            return TemplateError(f"invalid plan embedded in template: {original}")
    first = exc.errors()[0] if exc.errors() else {"msg": str(exc), "loc": ()}
    loc = ".".join(str(part) for part in first.get("loc", ()))
    message = str(first.get("msg", str(exc)))
    if loc:
        message = f"{message} at {loc}"
    return TemplateError(message)


# --- PlanTemplate validators -----------------------------------------------------


def _check_source_plan_id(template: PlanTemplate) -> None:
    if template.source_plan_id != template.plan.id:
        raise TemplateError(
            f"source_plan_id {template.source_plan_id!r} does not match the embedded plan id {template.plan.id!r}"
        )


def _check_validated_matches_plan(template: PlanTemplate) -> None:
    if template.validated != template.plan.validated:
        raise TemplateError("PlanTemplate.validated must match the embedded plan's own validated stamp")


def _check_unique_param_names(template: PlanTemplate) -> None:
    seen: set[str] = set()
    for param in template.params:
        if param.name in seen:
            raise TemplateError(f"duplicate template param name: {param.name!r}")
        seen.add(param.name)


def _check_params_resolve(template: PlanTemplate) -> None:
    by_id = {op.id: op for op in template.plan.operations}
    for param in template.params:
        op = by_id.get(param.op_id)
        if op is None:
            raise TemplateError(f"param {param.name!r}: unknown operation id {param.op_id!r}")
        value = _get_field(op, param.field)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TemplateError(
                f"param {param.name!r}: field {param.field!r} of operation {param.op_id!r} is not numeric"
            )


def _check_params_not_duplicated(template: PlanTemplate) -> None:
    seen: set[tuple[str, str]] = set()
    for param in template.params:
        key = (param.op_id, param.field)
        if key in seen:
            raise TemplateError(f"operation {param.op_id!r} field {param.field!r} is targeted by more than one param")
        seen.add(key)


def _check_param_bounds(template: PlanTemplate) -> None:
    for param in template.params:
        if param.minimum is not None and param.default < param.minimum:
            raise TemplateError(f"param {param.name!r}: default {param.default} below minimum {param.minimum}")
        if param.maximum is not None and param.default > param.maximum:
            raise TemplateError(f"param {param.name!r}: default {param.default} above maximum {param.maximum}")


# --- Field-path resolution (see module docstring) ---------------------------------


def field_value(plan: Plan, op_id: str, field: str) -> float:
    """The current numeric value at ``field`` inside operation ``op_id`` of ``plan``: what
    a caller saving a template reads as a ``TemplateParam.default`` (module docstring,
    ``PlanTemplate`` docstring — the default is read from the plan being saved, never declared by hand).
    ``TemplateError`` when ``op_id`` is unknown or the path resolves to a non-numeric leaf."""
    op = next((candidate for candidate in plan.operations if candidate.id == op_id), None)
    if op is None:
        raise TemplateError(f"unknown operation id {op_id!r}")
    value = _get_field(op, field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TemplateError(f"operation {op_id!r}: field {field!r} is not numeric")
    return float(value)


def _get_field(op: OperationBase, field: str) -> object:
    value: object = op
    for segment in field.split("."):
        value = _step_into(value, segment, field, op.id)
    return value


def _step_into(value: object, segment: str, field: str, op_id: str) -> object:
    if isinstance(value, BaseModel):
        if segment not in type(value).model_fields:
            raise TemplateError(f"operation {op_id!r} has no field {segment!r} (path {field!r})")
        return getattr(value, segment)
    if isinstance(value, (tuple, list)):
        match = next((item for item in value if getattr(item, "name", None) == segment), None)
        if match is None:
            raise TemplateError(f"operation {op_id!r}: no entry named {segment!r} at {field!r}")
        return match
    raise TemplateError(f"operation {op_id!r}: cannot navigate into {value!r} at {field!r}")


def _set_field(model: BaseModel, segments: list[str], value: float, op_id: str) -> BaseModel:
    segment, *rest = segments
    if segment not in type(model).model_fields:
        raise TemplateError(f"operation {op_id!r} has no field {segment!r}")
    current = getattr(model, segment)
    if not rest:
        return model.model_copy(update={segment: value})
    if isinstance(current, (tuple, list)):
        name, *deeper = rest
        index = next((i for i, item in enumerate(current) if getattr(item, "name", None) == name), None)
        if index is None:
            raise TemplateError(f"operation {op_id!r}: no entry named {name!r} in {segment!r}")
        if not deeper:
            path = ".".join([segment, name, *deeper])
            raise TemplateError(f"operation {op_id!r}: field path {path!r} does not reach a leaf value")
        updated_item = _set_field(current[index], deeper, value, op_id)
        sequence = list(current)
        sequence[index] = updated_item
        new_value = tuple(sequence) if isinstance(current, tuple) else sequence
        return model.model_copy(update={segment: new_value})
    if isinstance(current, BaseModel):
        updated_child = _set_field(current, rest, value, op_id)
        return model.model_copy(update={segment: updated_child})
    path = ".".join(segments)
    raise TemplateError(f"operation {op_id!r}: cannot set field path {path!r}: {segment!r} is not navigable")


# --- instantiate --------------------------------------------------------------


def instantiate(template: PlanTemplate, values: Mapping[str, float], *, plan_id: str) -> Plan:
    """Substitute each ``param`` in its ``(op_id, field)`` and return a new plan with ``id=plan_id``.

    A ``value`` outside ``[minimum, maximum]`` or an unknown param name is ``TemplateError``; a param
    absent from ``values`` uses its ``default``. The result always has ``validated=None`` — an instance
    with other dimensions is not itself validated; it has to pass level 2 on its own, which is
    literally the "hecho cuando" of this piece.
    """
    _validate_plan_id(plan_id)
    resolved = _resolve_values(template.params, values)
    updates_by_op: dict[str, list[tuple[str, float]]] = {}
    for param in template.params:
        updates_by_op.setdefault(param.op_id, []).append((param.field, resolved[param.name]))
    operations = list(template.plan.operations)
    for index, op in enumerate(operations):
        pending = updates_by_op.get(op.id)
        if pending is None:
            continue
        updated: OperationBase = op
        for field, value in pending:
            updated = _set_field(updated, field.split("."), value, op.id)
        operations[index] = updated
    new_plan = template.plan.model_copy(update={"id": plan_id, "validated": None, "operations": tuple(operations)})
    # Full re-validation (V1-V9 + every field constraint), not a raw model_copy trust-fall: a
    # substituted value can break a field constraint (e.g. `Field(gt=0)`) or a plan-level validator
    # (e.g. V6's closed-profile check) that a param's own [minimum, maximum] never claimed to guard.
    return parse_plan(new_plan.model_dump(mode="json"))


def _validate_plan_id(plan_id: str) -> None:
    try:
        _OpIdAdapter.validate_python(plan_id)
    except ValidationError as exc:
        raise TemplateError(f"invalid plan_id: {plan_id!r}") from exc


def _resolve_values(params: tuple[TemplateParam, ...], values: Mapping[str, float]) -> dict[str, float]:
    known = {param.name for param in params}
    unknown = sorted(set(values) - known)
    if unknown:
        raise TemplateError(f"unknown template param name(s): {unknown}")
    resolved: dict[str, float] = {}
    for param in params:
        raw = values[param.name] if param.name in values else param.default
        value = float(raw)
        if param.minimum is not None and value < param.minimum:
            raise TemplateError(f"param {param.name!r}: value {value} below minimum {param.minimum}")
        if param.maximum is not None and value > param.maximum:
            raise TemplateError(f"param {param.name!r}: value {value} above maximum {param.maximum}")
        resolved[param.name] = value
    return resolved
