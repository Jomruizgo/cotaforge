# ruff: noqa: E501
"""The per-step plan of a sheet, as the drawing tool serves it: ``drawing(action=plan)`` and the line every drawing
reply ends with.

The kernel holds the catalog, the sheet context and the plan (``cotaforge.drawing``); this module only gathers what
they read: the facts and the findings of the CAD's review of the REAL page, the state of the requirement rows, the
intent file and where the independent review stands. Nothing is kept between calls: a status is what the page says now.
"""

from dataclasses import replace
from typing import Any

from cotaforge.contract import AdapterError
from cotaforge.drawing import catalog, context, plan

from cotaforge_mcp import design_memory, drawing_gate
from cotaforge_mcp import drawing_requirements as reqs
from cotaforge_mcp.reply import ToolError
from cotaforge_mcp.session import Session

REVIEW_READ = "read the sheet as the shop, then ask for the review"
MAX_LACKING = 110


def _lacking(row: reqs.Requirement) -> str:
    said = "; ".join(row.lacking) or ("its answer does not count" if row.refused_answer else "")
    return said if len(said) <= MAX_LACKING else said[: MAX_LACKING - 1] + "…"


def _words(text: str) -> str:
    """An answer without the ``applies:`` it was typed with, nor the engineer's order appended to it."""
    for prefix in design_memory.APPLIES_PREFIXES:
        if text.lower().startswith(prefix):
            text = text[len(prefix) :]
            break
    return text.split(" [ordered by ")[0].strip()


def _review_pending(session: Session, info: Any) -> tuple[str, ...]:
    brief = design_memory.load_brief(session.settings)
    if brief.drawing_review in ("none", "own"):
        return ()
    miss = drawing_gate.review_status(
        brief.drawing_review, brief.drawing_review_wait_min, info, drawing_gate.clock(), brief.lang
    )
    pending = [REVIEW_READ] if miss.next_steps else []
    if miss.remarks:
        pending.append(f"{len(miss.remarks)} reviewer remark(s) to answer")
    return tuple(pending)


def sheet_plan(session: Session, info: Any) -> plan.Plan:
    """The plan of the sheet ``info`` describes (a ``DrawingSheetInfo`` read WITH its review)."""
    intent = reqs.find_intent(session.settings.workspace, session.doc_name)
    sections = dict(intent.sections)
    answered: set[str] = set()
    for key, given in ((i.removeprefix(context.QUESTION_PREFIX), a) for i, a in info.answers.items()):
        if f"{context.QUESTION_PREFIX}{key}" not in info.answers:
            continue  # not an answer to a question of the plan
        answered.add(key)
        if given.verdict == reqs.APPLIES and not sections.get(key):
            sections[key] = _words(given.text)  # what the designer or the engineer said, where the intent file is silent
    found = context.build(info.facts, sections, str(info.fields.get("material", "")))
    found = replace(found, questions=tuple(q for q in found.questions if q.key not in answered))
    rows = drawing_gate.sheet_requirements(session, info)
    open_codes: dict[str, int] = {}
    for finding in info.findings:
        if not reqs.finding_closed(finding):
            open_codes[finding.code] = open_codes.get(finding.code, 0) + 1
    states = {r.id: plan.RowState(r.closed, _lacking(r)) for r in rows}
    return plan.build(found, catalog.load(), open_codes, states, _review_pending(session, info))


def step_line(session: Session, sheet: str | None, info: Any = None) -> list[str]:
    """The last line of a drawing reply: the step the sheet is at and what is still missing in it. It reads the page (one
    review of the sheet) unless the caller already has it (``info``); it never fails the action it closes."""
    if "describe_drawing" not in session.capabilities.tools:
        return []
    try:
        if info is None:
            info = drawing_gate.sheet_info(session, sheet, review=True)
        if info is None or not info.facts:
            return []
        return [sheet_plan(session, info).line()]
    except (AdapterError, ToolError, OSError, ValueError):
        return []


def lines(session: Session, info: Any, step: int | None = None) -> list[str]:
    """``drawing(action=plan)``: the sequence of this sheet, each step with the rules that apply and their status."""
    if not info.facts:
        return ["this CAD reports no facts of the part and the sheet: the plan cannot say which rules apply"]
    made = sheet_plan(session, info)
    if step is not None and not 1 <= step <= len(made.steps):
        raise ToolError(
            f"step is 1 to {len(made.steps)}", code="invalid_input", field_name="step", usage='drawing(action="plan", step=4)'
        )
    return [
        f"work sequence of sheet {info.sheet} (the status is read from the page now; step= shows another step in full):",
        *plan.render(made, step),
        made.line(),
    ]
