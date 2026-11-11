"""IR errors (ir/errors.py)."""

__all__ = ["IRError", "PlanError", "TemplateError"]


class IRError(ValueError):
    """Base class for every error raised by ``cotaforge.ir``."""


class PlanError(IRError):
    """Raised when a plan fails IR-level validation.

    Carries enough context (plan id, offending operation id if any, which
    rule fired, and a human message) for the executor to report
    precisely what is wrong with a plan. ``rule`` is one of ``"V1"``..``"V14"``
    for the plan-level validators, or ``"schema"`` for
    a generic pydantic validation error wrapped by ``parse_plan``.
    """

    def __init__(self, plan_id: str | None, op_id: str | None, rule: str, message: str) -> None:
        self.plan_id = plan_id
        self.op_id = op_id
        self.rule = rule
        self.message = message
        location = f"plan={plan_id!r}"
        if op_id is not None:
            location += f" op={op_id!r}"
        super().__init__(f"[{rule}] {message} ({location})")


class TemplateError(IRError):
    """Raised when a ``PlanTemplate`` fails validation, or ``instantiate`` cannot apply its values
    (ir/template.py) — sibling of ``PlanError``, one level up: a template is built on top of an
    already-valid plan, so a ``TemplateError`` is always about the template's own extra structure
    (its params, or the values used to instantiate it), never about the plan's own IR shape.
    """
