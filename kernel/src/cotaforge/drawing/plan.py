# ruff: noqa: E501
"""The plan of one sheet: the work sequence, and at each step only the catalog rules that apply to THIS drawing.

``build`` crosses a sheet context (``context.py``) with the rule catalog and with what the real page says now: the
review findings still open and the state of the requirement rows. Each rule comes out with how it is enforced (blocks,
warns, or debt: no check yet) and its status on the page. Nothing is remembered between calls: the status is the page's.
The method of each sheet type (``sheet_types.yaml``: what is particular to a part sheet or an assembly sheet at each
step) rides along; the rules themselves live only in the catalog.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from functools import cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict

from cotaforge.drawing.catalog import Catalog, Rule
from cotaforge.drawing.context import Question, SheetContext

SHEET_TYPES_PATH = Path(__file__).with_name("sheet_types.yaml")
SEQUENCE: tuple[tuple[str, str], ...] = (
    ("purpose", "what it is for and who reads it"),
    ("views", "views"),
    ("references", "references"),
    ("dimensions", "dimensions"),
    ("tolerances", "tolerances and fits"),
    ("notes", "notes and title block"),
    ("deliverables", "deliverables"),
    ("review", "read it as the shop"),
)
"""The eight steps of every drawing, in order. The catalog's ``layout`` rules hold at every one of them."""
LINE_DETAIL = 60
"""How much of what a rule lacks the one-line status of a reply quotes: the plan gives it whole."""
ENFORCEMENT = {"blocks": "blocks the export", "warns": "warns", "debt": "no check yet: you read it on the sheet"}
SUMMARY_OPEN = 3
"""How many open rules the one-line summary of a step that is not the one in view names."""


class SheetType(BaseModel):
    """The method of one type of sheet: who reads it, what is particular to it at each step, what it never carries."""

    model_config = ConfigDict(extra="forbid")

    title: str
    reader: str
    steps: dict[str, str]
    never: tuple[str, ...] = ()


@cache
def sheet_types(path: Path = SHEET_TYPES_PATH) -> dict[str, SheetType]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    return {name: SheetType.model_validate(body) for name, body in data.items()}


@dataclass(frozen=True)
class RowState:
    """A requirement row as the page has it: ``closed`` (met, or answered in a way that counts) and what it lacks."""

    closed: bool
    lacking: str = ""


@dataclass(frozen=True)
class RuleStatus:
    rule: Rule
    state: str
    """``open`` (the page breaks it, or its row is not closed), ``clear`` (a check holds it and finds nothing) or
    ``unchecked`` (debt: no check reads it)."""
    detail: str = ""

    @property
    def holds(self) -> bool:
        """It keeps the step open: a rule that only warns never does."""
        return self.state == "open" and self.rule.weight != "warns"


@dataclass(frozen=True)
class Step:
    number: int
    key: str
    title: str
    method: str = ""
    rules: tuple[RuleStatus, ...] = ()
    questions: tuple[Question, ...] = ()
    pending: tuple[str, ...] = ()

    @property
    def open(self) -> tuple[str, ...]:
        """What is still missing in this step, shortest first: questions, rules the page breaks, what the review awaits."""
        return (
            *(f"ask {q.id}" for q in self.questions),
            *(f"{s.rule.id} ({s.detail if len(s.detail) <= LINE_DETAIL else s.detail[: LINE_DETAIL - 1] + '…'})" for s in self.rules if s.holds),
            *self.pending,
        )


@dataclass(frozen=True)
class Plan:
    context: SheetContext
    steps: tuple[Step, ...]
    layout: tuple[RuleStatus, ...] = ()
    method: SheetType | None = field(default=None, compare=False)

    @property
    def current(self) -> Step:
        return next((s for s in self.steps if s.open), self.steps[-1])

    def line(self, quick: bool = False) -> str:
        """The one line every drawing reply ends with: the step the sheet is at and what it still lacks. ``quick``: read
        from the checks of the step just taken, not from the whole review (an add, an update, a remove): it says so."""
        step = self.current
        missing = step.open
        layout = [s for s in self.layout if s.holds]
        text = f"step {step.number}/{len(self.steps)} {step.title}: "
        if missing:
            shown = "; ".join(missing[:3]) + (f"; +{len(missing) - 3} more" if len(missing) > 3 else "")
            text += f"{len(missing)} open ({shown})"
        else:
            text += "nothing open in the plan" + (" so far" if quick else "")
        if layout:
            text += f" | layout: {', '.join(s.rule.id.removeprefix('layout.') for s in layout)} open"
        if quick:
            text += ' | step checks only: drawing(action="review") runs the whole review'
        return text + ' | drawing(action="plan") gives this step\'s rules'


def applies(rule: Rule, context: SheetContext) -> bool:
    """A rule applies when every scope it names matches: its sheet types, its processes and its features (any one of each)."""
    scope = rule.applies
    return (
        (not scope.sheet_types or context.sheet_type in scope.sheet_types)
        and (not scope.processes or bool(set(scope.processes) & set(context.processes)))
        and (not scope.features or bool(set(scope.features) & set(context.features)))
    )


def _status(rule: Rule, open_codes: Mapping[str, int], rows: Mapping[str, RowState]) -> RuleStatus:
    row = rows.get(rule.requirement) if rule.requirement else None
    found = [f"{code} x{open_codes[code]}" if open_codes[code] > 1 else code for code in rule.checks if open_codes.get(code)]
    if row is not None and not row.closed:
        detail = f"{rule.requirement}: {row.lacking}" if row.lacking else f"{rule.requirement} needs an answer"
        return RuleStatus(rule, "open", detail + (f"; {', '.join(found)}" if found else ""))
    if found:
        return RuleStatus(rule, "open", ", ".join(found))
    if row is not None:
        return RuleStatus(rule, "clear", f"{rule.requirement} closed")
    if not rule.checks and rule.weight != "gate":
        return RuleStatus(rule, "unchecked")
    return RuleStatus(rule, "clear")


def build(
    context: SheetContext,
    catalog: Catalog,
    open_codes: Mapping[str, int] | None = None,
    rows: Mapping[str, RowState] | None = None,
    review_pending: tuple[str, ...] = (),
) -> Plan:
    """The plan of the sheet ``context`` describes. ``open_codes``: the review findings not closed on the page, by code;
    ``rows``: the requirement rows that apply, by id; ``review_pending``: what the independent review still waits for."""
    codes, states = open_codes or {}, rows or {}
    method = sheet_types().get(context.sheet_type)
    by_step: dict[str, list[RuleStatus]] = {}
    for rule in catalog.rules:
        if applies(rule, context):
            by_step.setdefault(rule.step, []).append(_status(rule, codes, states))
    steps = tuple(
        Step(
            number=number,
            key=key,
            title=title,
            method=(method.steps.get(key, "") if method else "").strip(),
            rules=tuple(by_step.get(key, ())),
            questions=context.open_at(key),
            pending=review_pending if key == "review" else (),
        )
        for number, (key, title) in enumerate(SEQUENCE, start=1)
    )
    return Plan(context, steps, tuple(by_step.get("layout", ())), method)


def _rule_line(status: RuleStatus, text: bool) -> str:
    rule = status.rule
    mark = {"open": "OPEN", "clear": "ok", "unchecked": "read"}[status.state]
    how = (
        "its requirement row holds the export"
        if rule.weight == "debt" and rule.requirement
        else f"gate: {rule.gate}"
        if rule.weight == "gate"
        else ENFORCEMENT[rule.weight]
    )
    head = f"[{mark}] {rule.id} ({how}{', owner' if rule.origin == 'owner' else ''})"
    if status.detail and status.state == "open":
        head += f" <- {status.detail}"
    return f"{head}: {' '.join(rule.text.split())}" if text else head


def _summary(s: Step) -> str:
    """What a step that is not in view comes to in one line: how many of its rules are open (the first few named) and how
    many it has."""
    held = [r.rule.id for r in s.rules if r.holds]
    asked = [f"ask {q.id}" for q in s.questions]
    shown = [*asked, *held][:SUMMARY_OPEN]
    more = len(asked) + len(held) - len(shown)
    state = f"{len(s.open)} open: {', '.join(shown)}{f' +{more}' if more > 0 else ''}" if s.open else "nothing open"
    return f"STEP {s.number} {s.title.upper()} ({state}; {len(s.rules)} rules)"


def render(plan: Plan, step: int | None = None, full: bool = False) -> list[str]:
    """The plan as the agent reads it: the step in view (the current one, or the one asked for with ``step``) in full, with
    each of its rules, their status and their text; every other step in ONE line (how many of its rules are open, named
    the first few). ``full``: every step in full, which is long and only given on request."""
    context = plan.context
    current = plan.current
    lines = [
        f"sheet type: {context.sheet_type or 'UNDECIDED'}"
        + (f" ({plan.method.title}; read by {plan.method.reader})" if plan.method else "")
        + f" | processes: {', '.join(context.processes) or 'none declared'}"
        + f" | material: {context.material or 'not given'} | quantity: {context.quantity or 'not given'}",
        f"features on this part: {', '.join(context.features) or 'none found'}",
    ]
    if context.questions:
        lines.append(
            "open questions (asked of the designer or the engineer, never assumed; record what THEY said with "
            'drawing(action="answer", id="<id>", text="applies: <their words>")):'
        )
        lines += [f"  ? {q.id} [{q.step}] {q.about}: {q.text}" for q in context.questions]
    for s in plan.steps:
        shown = full or (step == s.number if step else s.number == current.number)
        here = " <- YOU ARE HERE" if s.number == current.number else ""
        if not shown:
            lines.append(_summary(s) + here)
            continue
        state = f"{len(s.open)} open" if s.open else "nothing open"
        lines.append(f"STEP {s.number} {s.title.upper()} ({state}){here}")
        if s.method:
            lines += [f"  {line}" for line in s.method.splitlines()]
        lines += [f"  wait: {p}" for p in s.pending]
        lines += ["  " + _rule_line(status, True) for status in s.rules]
    if plan.layout:
        lines.append("AT EVERY STEP (layout)")
        lines += ["  " + _rule_line(status, full or status.state == "open") for status in plan.layout]
    if plan.method and plan.method.never:
        lines.append(f"this {context.sheet_type} sheet never carries: " + "; ".join(plan.method.never))
    return lines
