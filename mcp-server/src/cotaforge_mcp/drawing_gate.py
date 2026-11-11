# ruff: noqa: E501
"""A finished drawing: what ``drawing(action=review)`` asks, what ``export(pdf|dxf)`` waits for, the answers and the
engineer's approval.

The harness does not decide whether a drawing is good. It makes sure somebody other than the agent that drew it looked
at the sheet, and that every point they raised was closed. A point is closed by FIXING it (the finding is gone on the
next review) or by ANSWERING it: ``applies: <how, here>`` or ``no aplica: <why>``. Who looks is the project's
``drawing_review`` setting (the brief, ``design_memory.DRAWING_REVIEWS``):

* ``engineer_or_fabricator`` (default) -- the engineer chooses between reviewing the sheet himself and the manufacturer
  agent reviewing it (``request_review`` asks, ``review_choice`` records his answer). If he does not choose within
  ``drawing_review_wait_min`` the manufacturer is assumed. There is no timer in the server: the wait is a comparison of
  the clock with the time the request was recorded, made when the agent tries to export or reviews;
* ``fabricator`` -- the manufacturer agent (a person who must make or assemble the part with ONLY the drawing in hand);
* ``engineer`` -- the engineer's own report, without a time limit;
* ``own`` -- the drawing agent asks itself: the questions of judgement that no memory principle covers and the memory
  entries the export calls for (the older behaviour);
* ``none`` -- nothing is asked.

The blocking automatic findings (a hole with no position, a missing note...) hold the export in every mode but ``none``.
The choice of reviewer belongs to the SHEET, not to a version of it: once the engineer chose (or the manufacturer was
assumed) it stays, and a change of the sheet neither asks again nor restarts the wait. A report stays valid through the
fixes it asked for: the gate needs every remark of the latest report answered (``corregido: ...`` / ``no aplica: ...``).
A new review happens only if the engineer asks for it or the agent decides the sheet changed beyond the report's remarks
(it launches the reviewer again; the new report supersedes). The sheet's change counter (``edits``) is shown next to the
report (``reviewed at edit 3, sheet now at edit 5``) so both see what changed after the review; it gates nothing.
Automatic blocking findings are checked again on every export. The server cannot prove WHO recorded a report (any agent can call the tool); the rule keeps honest agents
honest and leaves the answers to every point in the sheet, where the engineer reads them.

The answers live in the sheet, inside the document, so they survive save, close and reopen. ``Aprobó`` in the title
block is the engineer's: ``approve`` is a gate action like ``memory(approve)``.
"""

import math
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from cotaforge.contract import (
    AdapterError,
    AnnotateDrawingRequest,
    DescribeDrawingRequest,
    DrawingRemark,
    DrawingReport,
    DrawingSheetInfo,
)
from cotaforge.language import say
from cotaforge.memory import MemoryEntry
from pydantic import ValidationError

from cotaforge_mcp import design_memory, recovery
from cotaforge_mcp.reply import ToolError
from cotaforge_mcp.session import Session

MAX_LINE = 190
ANSWER_USAGE = 'drawing(action="answer", sheet?, id="<id from review>", text="applies: how, here" | "no aplica: why")'
ANSWER_EXAMPLE = 'drawing(action="answer", id="q.callouts", text="no aplica: una placa con un solo tipo de agujero no necesita distinguirlos")'
REPORT_USAGE = (
    'drawing(action="review_report", sheet, by="fabricator" | "engineer", items=[{"kind": "missing" | "ambiguous" | '
    '"costly" | "other", "where": "<place on the sheet>", "text": "<the point>", "fix": "<smallest change>"}])'
)
CHOICE_USAGE = 'drawing(action="review_choice", sheet, by="engineer" | "fabricator")'
MAX_SHOWN_REMARKS = 12

clock: Callable[[], float] = time.time
"""The server's clock (epoch seconds): the time a review was requested and the wait are measured with it. A test swaps it."""


def _short(text: str, limit: int = MAX_LINE) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def finding_id(finding: Any) -> str:
    return finding.id or finding.code


@dataclass
class Missing:
    """What a sheet still owes: findings, (``own``) questions and memory entries without an answer, (the other modes) the
    state of the review, the next step and the remarks of the reviewers without an answer."""

    findings: list[Any] = field(default_factory=list)
    questions: list[Any] = field(default_factory=list)
    memory: list[MemoryEntry] = field(default_factory=list)
    state: str = ""
    """One line: which case the review of the sheet is in (empty when the mode asks no outside review)."""
    next_steps: list[str] = field(default_factory=list)
    """What to do now to get the review that is missing (empty when the review is there)."""
    remarks: list[tuple[str, DrawingRemark]] = field(default_factory=list)
    """``(who, remark)`` of the report that counts, without an answer."""

    @property
    def count(self) -> int:
        return len(self.findings) + len(self.questions) + len(self.memory) + len(self.remarks) + bool(self.next_steps)


# --- the setting --------------------------------------------------------------------------------------------------------


def _brief(session: Session) -> design_memory.Brief:
    return design_memory.load_brief(session.settings)


def mode_of(session: Session) -> str:
    return _brief(session).drawing_review


# --- who must review, and where it stands -------------------------------------------------------------------------------


def _launch(sheet: str) -> str:
    return (
        f'launch the `fabricator` agent now (Agent tool, subagent_type="fabricator") and tell it the sheet: "{sheet}"; it '
        f'sees only the drawing and records its report with drawing(action=review_report, sheet="{sheet}", by="fabricator", '
        "items=[...])"
    )


def review_question(sheet: str, language: str = "es") -> str:
    """The ONE question the engineer is asked about who reviews a sheet: he reads it word for word, so it is in his
    language."""
    return say(
        language,
        f"¿Revisas tú la hoja {sheet} o la revisa el agente fabricante?",
        f"Do you review sheet {sheet} yourself, or should the manufacturer agent?",
    )


def _words(language: str) -> tuple[str, str, str]:
    """What the engineer says to answer it: he reviews it, the manufacturer does, he has no remarks."""
    return (
        say(language, "yo", "me"),
        "fabricante" if language == "es" else "manufacturer",
        say(language, "sin observaciones", "no remarks"),
    )


def _engineer_report(sheet: str, language: str = "es") -> str:
    return (
        f'record what the engineer says (every remark, or none: "{_words(language)[2]}") with drawing(action=review_report, '
        f'sheet="{sheet}", by="engineer", items=[...]); what he points at in the sheet window you read with '
        "view(action=selection) and put in `where`"
    )


def _unanswered(info: DrawingSheetInfo, report: DrawingReport, who: str) -> list[tuple[str, DrawingRemark]]:
    return [(who, r) for r in report.remarks if r.id not in info.answers]


def _counts(info: DrawingSheetInfo, who: str, report: DrawingReport, state: str, miss: Missing) -> None:
    """``report`` is the one that counts: its remarks without an answer block. The changes made since it are shown, they
    gate nothing."""
    since = info.edits - report.edit
    shown = f"{who} report {report.n}, reviewed at edit {report.edit}, sheet now at edit {info.edits}"
    note = ""
    if since > 0:
        note = (
            f"; the sheet changed {since} time(s) after the review: if the changes go beyond the remarks you answered, "
            f"ask for a new review ({_launch(info.sheet) if who == 'fabricator' else _engineer_report(info.sheet)}); its "
            "report supersedes this one"
        )
    miss.state = f"{state}: {shown}, {len(report.remarks)} remark(s){note}"
    miss.remarks = _unanswered(info, report, who)


def _from_one(info: DrawingSheetInfo, who: str, state: str, miss: Missing) -> None:
    """The review is the latest report of ``who``: it must exist and every remark be answered."""
    report = info.reports.get(who)
    if report is None:
        miss.state = f"{state}: no {who} report yet"
        miss.next_steps.append(_launch(info.sheet) if who == "fabricator" else _engineer_report(info.sheet))
        return
    _counts(info, who, report, state, miss)


def _manufacturer_or_later_engineer(info: DrawingSheetInfo, state: str, miss: Missing) -> None:
    """The manufacturer reviews; a report of the engineer recorded after his (a new review) supersedes it."""
    fab, eng = info.reports.get("fabricator"), info.reports.get("engineer")
    if eng is not None and (fab is None or eng.n > fab.n):
        _counts(info, "engineer", eng, f"{state} (the engineer reviewed later)", miss)
        return
    _from_one(info, "fabricator", state, miss)


def review_status(mode: str, wait_min: int, info: DrawingSheetInfo, now: float, language: str = "es") -> Missing:
    """The review part of what a sheet owes in ``mode``: which case it is in (``state``), what to do next and the remarks
    without an answer. ``now`` is the clock; the only time-dependent case is the engineer's wait."""
    miss = Missing()
    if mode == "fabricator":
        _from_one(info, "fabricator", "manufacturer review", miss)
        return miss
    if mode == "engineer":
        _from_one(info, "engineer", "engineer review", miss)
        return miss
    if mode != "engineer_or_fabricator":
        return miss
    sheet = info.sheet
    if info.review_choice == "engineer":
        _from_one(info, "engineer", "the engineer chose to review the sheet himself (no time limit)", miss)
        return miss
    if info.review_choice == "fabricator":
        _manufacturer_or_later_engineer(info, "the engineer chose the manufacturer", miss)
        return miss
    engineer = info.reports.get("engineer")
    if engineer is not None:  # he reviewed without being asked, or before the question: his report counts
        _counts(info, "engineer", engineer, "the engineer's report counts", miss)
        return miss
    request = info.review_request
    if request is None:
        miss.state = f"nobody has been asked who reviews sheet {sheet}"
        miss.next_steps.append(
            f'drawing(action="request_review", sheet="{sheet}"): it tells you the one question to put to the engineer and '
            "the timer to start"
        )
        return miss
    left = request.at + wait_min * 60 - now
    if left > 0:
        minutes = math.ceil(left / 60)
        miss.state = (
            f"waiting for the engineer's choice between reviewing sheet {sheet} himself and the manufacturer: "
            f"{minutes} min left of {wait_min}"
        )
        miss.next_steps.append(
            f"if you have not asked him, ask exactly: «{review_question(sheet, language)}»; when he answers record it with "
            f"{CHOICE_USAGE}; keep working on "
            f"other things (never end the turn to wait). When the {wait_min} min pass without an answer the manufacturer "
            "is assumed and this export will say so"
        )
        return miss
    _manufacturer_or_later_engineer(
        info, f"the engineer did not choose within {wait_min} min: the manufacturer review counts", miss
    )
    return miss


def missing(session: Session, sheet: DrawingSheetInfo, now: float | None = None) -> Missing:
    """Everything the export of ``sheet`` still waits for in the project's mode (``none``: nothing)."""
    brief = _brief(session)
    if brief.drawing_review == "none":
        return Missing()
    found = review_status(
        brief.drawing_review, brief.drawing_review_wait_min, sheet, clock() if now is None else now, brief.lang
    )
    found.findings = [f for f in sheet.findings if f.blocking and f.answer is None]
    if brief.drawing_review == "own":
        found.questions = [q for q in sheet.questions if q.answer is None]
        found.memory = [e for e in design_memory.drawing_entries(session) if e.id not in sheet.answers]
    return found


def _recognise(entry: MemoryEntry) -> str:
    return " ".join((entry.recognize or entry.applies_when).split())


def _verdict(answer: Any) -> str:
    """``APPLIES: how`` / ``DOES NOT APPLY: why`` -- the text without the prefix the agent typed (the label says it)."""
    text = answer.text
    for prefix in (*design_memory.APPLIES_PREFIXES, *design_memory.NOT_APPLIES_PREFIXES):
        if text.lower().startswith(prefix):
            text = text[len(prefix) :].strip()
            break
    return f"{'APPLIES' if answer.verdict == 'applies' else 'DOES NOT APPLY'}: {text}"


def _remark_line(who: str, remark: DrawingRemark) -> str:
    where = f" [{_short(remark.where, 60)}]" if remark.where else ""
    fix = f" -> smallest fix: {_short(remark.fix, 100)}" if remark.fix else ""
    return f"{remark.id} ({who}, {remark.kind}){where}: {_short(remark.text, 140)}{fix}"


# --- the review ---------------------------------------------------------------------------------------------------------


def review_lines(session: Session, sheet: DrawingSheetInfo) -> list[str]:
    """The review of one sheet: the findings (warnings first, they arrive sorted), the answers given, then who reviews it
    and where that stands (or, in ``own``, the questions of judgement and the memory entries) and what the export
    still waits for."""
    brief = _brief(session)
    pending = [f for f in sheet.findings if f.answer is None]
    done = [f for f in sheet.findings if f.answer is not None]
    lines: list[str] = []
    if not sheet.findings:
        lines.append(
            "review: nothing found (every hole and feature is dimensioned, no dimension is repeated, the layout is clean)"
        )
    else:
        warnings = sum(f.severity == "warning" for f in pending)
        tail = f", {len(done)} answered" if done else ""
        lines.append(f"review: {warnings} warning(s), {len(pending) - warnings} to confirm{tail}")
    for f in pending:
        refs = ", ".join([*f.items, *f.features])
        lines.append(f"  {f.severity} {finding_id(f)}{' [' + refs + ']' if refs else ''}: {f.message}")
    lines += _answered_lines(session, sheet, done)
    if brief.drawing_review == "own":
        lines += _question_lines(sheet)
        lines += _memory_lines(session, sheet)
    miss = missing(session, sheet)
    lines.append(f"drawing review mode: {brief.drawing_review}")
    if miss.state:
        lines.append(f"  state: {miss.state}")
    lines += [f"  next: {step}" for step in miss.next_steps]
    lines += _report_lines(sheet, miss)
    if brief.drawing_review == "none":
        lines.append("export of this sheet is open: the project asks no review (drawing_review=none)")
    elif miss.count:
        lines.append(
            f"export of this sheet is REFUSED: {miss.count} pending ({len(miss.findings)} findings, {len(miss.questions)} "
            f"questions, {len(miss.memory)} memory entries, {len(miss.remarks)} reviewer remarks"
            f"{', the review itself' if miss.next_steps else ''}). Fix a point (it is gone on the next review) or answer it: {ANSWER_USAGE}"
        )
    else:
        lines.append("export of this sheet is open: nothing is pending")
    lines.append(
        f"approved by: {sheet.approved_by}"
        if sheet.approved_by
        else 'not approved ("Aprobó" is empty): only the engineer fills it, drawing(action=approve, sheet=, by=, reason=)'
    )
    return lines


def _report_lines(sheet: DrawingSheetInfo, miss: Missing) -> list[str]:
    """The remarks of the latest report of each reviewer, with the ones still without an answer marked."""
    lines: list[str] = []
    open_ids = {r.id for _, r in miss.remarks}
    for who, report in sorted(sheet.reports.items()):
        lines.append(
            f"  {who} report {report.n} (reviewed at edit {report.edit}, sheet now at edit {sheet.edits}): "
            f"{len(report.remarks)} remark(s)"
        )
        for remark in report.remarks[:MAX_SHOWN_REMARKS]:
            mark = "OPEN" if remark.id in open_ids else "answered" if remark.id in sheet.answers else "not required"
            lines.append(f"    [{mark}] {_remark_line(who, remark)}")
        if len(report.remarks) > MAX_SHOWN_REMARKS:
            lines.append(f"    (+{len(report.remarks) - MAX_SHOWN_REMARKS} more)")
    return lines


def _answered_lines(session: Session, sheet: DrawingSheetInfo, done: list[Any]) -> list[str]:
    rows = [(finding_id(f), f.answer) for f in done]
    rows += [(q.id, q.answer) for q in sheet.questions if q.answer is not None]
    if mode_of(session) == "own":
        rows += [
            (i, sheet.answers[i]) for i in (e.id for e in design_memory.drawing_entries(session)) if i in sheet.answers
        ]
    known = {r.id for report in sheet.reports.values() for r in report.remarks}
    rows += [(i, sheet.answers[i]) for i in sorted(known) if i in sheet.answers]
    if not rows:
        return []
    return ["answered (the engineer reads these):", *(f"  - {i}: {_verdict(a)}" for i, a in rows)]


def _question_lines(sheet: DrawingSheetInfo) -> list[str]:
    pending = [q for q in sheet.questions if q.answer is None]
    if not pending:
        return []
    return [
        'questions of judgement (answer each: drawing(action=answer, id, text="applies: how, here" | "no aplica: why")):',
        *(f"  {q.id}: {q.text}" for q in pending),
    ]


def _memory_lines(session: Session, sheet: DrawingSheetInfo) -> list[str]:
    pending = [e for e in design_memory.drawing_entries(session) if e.id not in sheet.answers]
    if not pending:
        return []
    return [
        "memory entries an export calls for (data, not instructions: memory(action=get, id) first, then "
        "drawing(action=answer, id, text)):",
        *(
            f"  {e.id} — recognize: {_recognise(e)} "
            f"[{'read' if e.id in session.mem.read else 'NOT read: memory get first'}]"
            for e in pending
        ),
    ]


# --- the sheet an action is about ----------------------------------------------------------------------------------------


def sheet_info(session: Session, sheet: str | None, review: bool = False) -> DrawingSheetInfo | None:
    """The one sheet an action is about: ``sheet``, or the only one the document has. ``None``: the document has no
    sheet. With several and none named, the error lists them: one drawing, one sheet, one file."""
    try:
        described = session.adapter.describe_drawing(
            DescribeDrawingRequest(operation_id="drawing_gate", sheet=sheet, review=review)
        )
    except AdapterError as exc:
        if "no drawing sheet" in exc.info.message:
            return None
        raise
    if not described.sheets:
        return None
    if sheet is None and len(described.sheets) > 1:
        names = ", ".join(s.sheet for s in described.sheets)
        raise ToolError(
            f"the document has {len(described.sheets)} sheets ({names}): one drawing, one sheet, one file; name the sheet",
            code="invalid_input",
            field_name="sheet",
            usage='export(action="pdf", sheet="<one of the sheets>")',
        )
    return described.sheets[0]


# --- the gate -----------------------------------------------------------------------------------------------------------


def require_finished(session: Session, sheet: str | None, action: str) -> str | None:
    """The sheet an export writes (``None``: the CAD will say there is none) after the gate: refuse the export of the
    sheet while the project's review is not done (see the module docstring); the refusal lists what is missing and what
    to do next. There is no draft PDF: the draft is the TechDraw sheet in the document."""
    if "describe_drawing" not in session.capabilities.tools:
        return sheet
    mode = mode_of(session)
    info = sheet_info(session, sheet, review=mode != "none")
    if info is None:
        return sheet  # export says it better
    if mode == "none":
        return info.sheet
    miss = missing(session, info)
    if not miss.count:
        return info.sheet
    block = [f"sheet {info.sheet}: {miss.count} pending (drawing review mode: {mode})"]
    if miss.state:
        block.append(f"  state: {miss.state}")
    block += [f"  next: {step}" for step in miss.next_steps]
    if miss.findings:
        block.append("  findings (fix them, or answer them):")
        block += [f"    - {finding_id(f)}: {_short(f.message)}" for f in miss.findings]
    if miss.remarks:
        block.append(
            "  remarks of the reviewer (fix the sheet and answer corregido: ..., or answer no aplica: ...; a report stays valid through the fixes it asked for):"
        )
        block += [f"    - {_remark_line(who, r)}" for who, r in miss.remarks]
    if miss.questions:
        block.append("  questions of judgement:")
        block += [f"    - {q.id}: {_short(q.text)}" for q in miss.questions]
    if miss.memory:
        block.append("  memory entries (memory(action=get, id=...) first):")
        block += [
            f"    - {e.id}: {_short(_recognise(e))} [{'read' if e.id in session.mem.read else 'NOT read'}]"
            for e in miss.memory
        ]
    raise ToolError(
        f"export {action} refused: the drawing is not finished. A point is closed by fixing it or by answering it; "
        "the TechDraw sheet in the document is the draft (there is no draft PDF).\n"
        + "\n".join(block)
        + f"\nanswer each point with {ANSWER_USAGE}",
        code="drawing_unfinished",
        field_name="sheet",
        usage=ANSWER_USAGE,
        example=ANSWER_EXAMPLE,
    )


# --- asking for a review, the engineer's choice, the reports -------------------------------------------------------------------


def _timer(minutes: int) -> str:
    seconds = minutes * 60
    return (
        f"START THE TIMER NOW so the wait does not depend on you coming back by chance: Bash `sleep {seconds}` with "
        f"run_in_background=true (PowerShell: `Start-Sleep -Seconds {seconds}` in the background); Claude Code wakes you "
        "when it ends. When woken: if the engineer has already answered the choice, ignore the timer; if not, launch the "
        "`fabricator` agent for this sheet. (The server's clock is the source of truth: this timer is only the alarm.)"
    )


def request_review(session: Session, sheet: str | None) -> str:
    """Ask for the review the project's mode needs; in ``engineer_or_fabricator`` it records when the engineer was asked."""
    brief = _brief(session)
    info = sheet_info(session, sheet)
    if info is None:
        raise ToolError(
            'the document has no drawing sheet: drawing(action="create") makes one',
            code="invalid_input",
            field_name="sheet",
        )
    name = info.sheet
    mode = brief.drawing_review
    if mode in ("own", "none"):
        return f"drawing review mode is {mode}: nobody outside is asked. " + (
            "Answer the points of drawing(action=review)." if mode == "own" else "Nothing to review."
        )
    if mode == "fabricator":
        return f"drawing review mode is fabricator: {_launch(name)}. Then answer every remark of its report."
    if mode == "engineer":
        return (
            f"drawing review mode is engineer: ask the engineer to review sheet {name} in the CAD window and to tell "
            f"you his remarks (or «{_words(brief.lang)[2]}»); keep working on other things, never end the turn to wait. When he "
            f"does: {_engineer_report(name, brief.lang)}. Answer every remark (fix the sheet, or say why not). No time limit."
        )
    wait = brief.drawing_review_wait_min
    if info.review_choice:  # the choice belongs to the sheet: it is not asked again, nor does the wait restart
        who = "the engineer himself" if info.review_choice == "engineer" else "the manufacturer agent"
        todo = _engineer_report(name) if info.review_choice == "engineer" else _launch(name)
        return (
            f"sheet {name} is already reviewed by {who} (the engineer chose; it stays through the changes of the sheet, "
            f"nothing is asked again). To get a NEW review after changes beyond the remarks you answered: {todo}. "
            "The new report supersedes the old one."
        )
    request = info.review_request
    if request is None:
        asked = AnnotateDrawingRequest(
            operation_id="drawing_request_review", action="request_review", sheet=name, at=clock()
        )
        session.adapter.annotate_drawing(asked)
        recovery.note(session, asked)
        session.dirty = True
        left = ""
    else:  # asked already: asking again neither extends the wait nor asks him twice
        minutes = math.ceil((request.at + wait * 60 - clock()) / 60)
        if minutes <= 0:
            return (
                f"sheet {name}: the engineer did not choose within {wait} min, so the manufacturer review counts. "
                f"Now: {_launch(name)}. Then answer every remark of its report."
            )
        left = f" (already requested: {minutes} min left of {wait}; do not ask him again unless he has not been asked)"
    return (
        f"review requested for sheet {name}{left}. NOW ask the engineer exactly one question: «{review_question(name, brief.lang)}» and say that if he does not answer within {wait} min the manufacturer review "
        f"counts. Then keep working on other things; NEVER end the turn just to wait.\n"
        f"- {_timer(wait)}\n"
        f'- He answers «{_words(brief.lang)[0]}»: drawing(action="review_choice", sheet="{name}", by="engineer"), '
        f"then he tells you his remarks (or «{_words(brief.lang)[2]}»): {_engineer_report(name, brief.lang)}. There is no time limit once he chose.\n"
        f'- He answers «{_words(brief.lang)[1]}», or the {wait} min pass: drawing(action="review_choice", sheet="{name}", by="fabricator") '
        f"if he said it, and {_launch(name)}.\n"
        "- Either way every remark of the report that counts is answered before the export: fix the sheet and answer "
        "«fixed: ...», or answer «does not apply: ...». The choice stays for this sheet through its changes."
    )


def review_choice(session: Session, sheet: str | None, by: str | None) -> str:
    """Record who the engineer chose to review the sheet (only ``engineer_or_fabricator`` has a choice)."""
    brief = _brief(session)
    if brief.drawing_review != "engineer_or_fabricator":
        raise ToolError(
            f"the choice of reviewer exists only in drawing_review=engineer_or_fabricator (this project: {brief.drawing_review})",
            code="invalid_input",
            field_name="by",
        )
    if by not in ("engineer", "fabricator"):
        raise ToolError(
            "review_choice records what the ENGINEER answered: by=engineer (he reviews) or by=fabricator",
            code="invalid_input",
            field_name="by",
            usage=CHOICE_USAGE,
        )
    info = sheet_info(session, sheet)
    if info is None:
        raise ToolError("the document has no drawing sheet", code="invalid_input", field_name="sheet")
    chosen = AnnotateDrawingRequest(
        operation_id="drawing_review_choice", action="review_choice", sheet=info.sheet, by=by
    )
    session.adapter.annotate_drawing(chosen)
    recovery.note(session, chosen)
    session.dirty = True
    if by == "engineer":
        return (
            f"recorded: the engineer reviews sheet {info.sheet} himself (no time limit; the timer, if it wakes you, means "
            f"nothing now). When he tells you his remarks (or «{_words(brief.lang)[2]}»): {_engineer_report(info.sheet, brief.lang)}"
        )
    return f"recorded: the engineer chose the manufacturer for sheet {info.sheet}. Now: {_launch(info.sheet)}"


def review_report(session: Session, sheet: str | None, by: str | None, items: list[Any] | None) -> str:
    """Store a reviewer's report (``items`` may be empty: it looked and found nothing). Does not change the sheet."""
    info = sheet_info(session, sheet)
    if info is None:
        raise ToolError(
            'the document has no drawing sheet: drawing(action="create") makes one',
            code="invalid_input",
            field_name="sheet",
        )
    try:
        request = AnnotateDrawingRequest(
            operation_id="drawing_review_report",
            action="review_report",
            sheet=info.sheet,
            by=by,
            remarks=tuple(items or ()),
            at=clock(),
        )
    except ValidationError as exc:
        raise ToolError(
            "; ".join(str(e["msg"]).removeprefix("Value error, ") for e in exc.errors()[:3]),
            code="invalid_input",
            field_name="items",
            usage=REPORT_USAGE,
        ) from exc
    session.adapter.annotate_drawing(request)
    recovery.note(session, request)
    session.dirty = True
    fresh = sheet_info(session, info.sheet)
    report = fresh.reports.get(str(by)) if fresh is not None else None
    ids = [r.id for r in report.remarks] if report is not None else []
    mode = mode_of(session)
    note = ""
    if mode in ("own", "none"):
        note = f" (drawing_review={mode}: this project does not wait for it)"
    elif mode == "fabricator" and by != "fabricator" or mode == "engineer" and by != "engineer":
        note = f" (drawing_review={mode}: only the {mode} report opens the export)"
    if not ids:
        return f"recorded: {by} report on sheet {info.sheet} with no remarks: nothing to answer{note}"
    return (
        f"recorded: {by} report {report.n if report else ''} on sheet {info.sheet}, {len(ids)} remark(s): "
        f"{', '.join(ids)}. Close each: fix the sheet and answer "
        f'drawing(action="answer", id=<id>, text="corregido: what you changed") (the report stays valid through the '
        f'fixes it asked for), or text="no aplica: why it is not a problem" -- never a reason invented to get through; '
        f"the engineer reads them{note}"
    )


# --- answer and approve -------------------------------------------------------------------------------------------------


def answer(session: Session, sheet: str | None, item_id: str | None, text: str | None) -> str:
    if not item_id or not text:
        raise ToolError(
            "answer needs id (from drawing(action=review)) and text",
            code="invalid_input",
            field_name="id",
            usage=ANSWER_USAGE,
            example=ANSWER_EXAMPLE,
        )
    called = {e.id for e in design_memory.drawing_entries(session)}
    is_memory = item_id in called
    if not is_memory and item_id in {e.id for e in design_memory.active_entries(session.settings)}:
        raise ToolError(
            f"{item_id!r} is a memory entry that an export of the drawing does not call for here (its triggers do not "
            "match); answer the points drawing(action=review) lists",
            code="unknown_entry",
            field_name="id",
            usage=ANSWER_USAGE,
        )
    verdict, said = design_memory.checked_answer(session, item_id if is_memory else None, text)
    given = AnnotateDrawingRequest(
        operation_id="drawing_answer",
        action="answer",
        sheet=sheet,
        id=item_id,
        verdict=verdict,  # type: ignore[arg-type]
        text=said,
        memory=is_memory,
    )
    result = session.adapter.annotate_drawing(given)
    recovery.note(session, given.model_copy(update={"sheet": result.sheet}))  # outside the document too: a crash
    session.dirty = True  # the answer is in the sheet: it is saved with the document
    if is_memory:  # the same answer closes the entry at the milestone: memory(review) does not ask it a second time
        stage = design_memory.current_stage(session).value
        session.mem.answers[item_id] = {"verdict": verdict, "text": said, "stage": stage}
    kind = "memory entry" if is_memory else "point of the review"
    return (
        f"answered: {item_id} ({'applies' if verdict == 'applies' else 'does not apply'}) on sheet {result.sheet} "
        f"({kind}); it is kept in the sheet and the engineer reads it in the review. drawing(action=review) lists what "
        "is left"
    )


def approve(session: Session, sheet: str | None, by: str | None, reason: str | None) -> str:
    if not sheet:
        raise ToolError(
            "approve names the sheet it approves",
            code="invalid_input",
            field_name="sheet",
            usage='drawing(action="approve", sheet="Drawing", by="Ana", reason="Ana: aprueba el plano")',
        )
    if not (by and by.strip()) or not (reason and reason.strip()):
        raise ToolError(
            "approve is the engineer's gate: call it only on his explicit instruction, naming him in `by` and quoting "
            "his words in `reason`; 'Aprobó' in the title block is his and nobody else writes it",
            code="invalid_input",
            field_name="by",
            usage='drawing(action="approve", sheet="Drawing", by="Ana", reason="Ana: aprueba el plano")',
            example='drawing(action="approve", sheet="Drawing", by="Ana", reason="Ana: aprueba el plano")',
        )
    result = session.adapter.annotate_drawing(
        AnnotateDrawingRequest(
            operation_id="drawing_approve", action="approve", sheet=sheet, by=by.strip(), text=" ".join(reason.split())
        )
    )
    session.dirty = True
    return (
        f"approved: sheet {result.sheet} by {by.strip()} ({' '.join(reason.split())}); 'Aprobó' is filled in the title "
        "block. Any later change of the sheet (an item, a view, a layout) empties it again: the approval is of this drawing"
    )
