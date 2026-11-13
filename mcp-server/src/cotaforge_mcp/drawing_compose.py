# ruff: noqa: E501
"""``drawing(action=plan)`` and ``drawing(action=compose)``: the sheet built in ONE order (``drawing_flow: sheet`` of the
brief). The harness derives everything the model and the intent file already say (views, paper and scale, overall sizes,
callouts, axes, chamfers, keyway, parts list, notes of the intent file, title block, the positions of the holes); the
engineer's decisions (datums, relations, fits, tolerances, GD&T, finish, sections, welds, notes) are what it cannot know:
``plan`` lists them as questions and ``compose`` refuses, before it changes anything, while one is missing.

A decision is an item of the types that already exist (no placement: where it goes is the layout's job), so the
corrections afterwards are the actions of today. What ``compose`` puts on the sheet has ids that start with ``dc_``: a
second ``compose`` takes those off and puts the new ones; an item added by hand or placed by hand stays and is never
touched. The only place that decides WHERE things go is ``lay_out`` (the seam of the global layout planner)."""

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

from cotaforge.contract import AnnotateDrawingRequest, DrawingRequest
from cotaforge.language import say
from pydantic import ValidationError

from cotaforge_mcp import design_memory, drawing_gate
from cotaforge_mcp import drawing_requirements as reqs
from cotaforge_mcp.reply import Reply, ToolError, ok
from cotaforge_mcp.schemas import DrawingArgs
from cotaforge_mcp.session import Session

REPLY_CHARS = 14000
COMPOSED_PREFIX = "dc_"
"""The id of every item a ``compose`` puts on the sheet starts with it (``dc_gdt1``): the next compose replaces them."""
MAX_ITEMS_PER_CALL = 50
PAPER_STEPS = ("A4", "A3", "A2")
"""The papers the composer may raise a sheet to when nobody fixed the paper (A2 is the limit: beyond it is the engineer's)."""
LAYOUT_CODES = frozenset({"views_overlap", "view_outside_frame", "item_outside_frame", "text_outside_frame"})
"""The findings that say the views and annotations do not fit the paper (a local ``collision`` between two annotations is
the placement's to solve, not a reason for a bigger paper)."""
LOCATION_CODES = frozenset({"hole_not_located", "hole_partly_located"})

KEYS: dict[str, tuple[str, ...]] = {
    "datums": ("datum",),
    "relations": ("dim", "datum_dims"),
    "fits": ("hole_dim", "dim"),
    "tol": ("dim", "hole_dim"),
    "gdt": ("gdt",),
    "finish": ("finish",),
    "sections": ("section", "detail", "aux_view"),
    "welds": ("weld",),
    "notes": ("note",),
}
"""The decisions that are lists of items, and the item types each takes (the first is the default type)."""
SHEET_KEYS = frozenset(
    {
        "template", "angle", "scale", "title", "author", "date", "material", "language", "drawing_number", "revision",
        "general_tolerances", "company", "title_block", "symmetry", "views", "isometric", "body",
    }
)  # fmt: skip
KNOWN = frozenset({*KEYS, "sheet", "answers"})
ROW_KEYS: dict[str, tuple[str, ...]] = {
    "req.thread": ("fits", "notes"),
    "req.fit_class": ("fits", "tol"),
    "req.dimensions": ("relations", "fits", "tol"),
    "req.functional_dimensions": ("relations", "tol"),
    "req.keyway": ("fits", "tol"),
    "req.surface_texture": ("finish",),
    "req.geometric_tolerance": ("gdt",),
    "req.welds": ("welds",),
    "req.general_tolerance": ("sheet",),
    "req.title_block": ("sheet",),
}
"""The decision that answers each row of the table of requirements; any other row is closed by a note or by ``answers``."""
LOCATION_KEYS = ("datums", "relations", "sheet")


@dataclass(frozen=True)
class Question:
    """A decision the harness cannot take: ``keys`` are the decisions that answer it, ``row`` the requirement it stands for
    (empty: the location of the holes), ``text`` what is asked of the engineer (his language, with a proposal)."""

    keys: tuple[str, ...]
    text: str
    row: str = ""


@dataclass
class Survey:
    info: Any
    rows: list[reqs.Requirement]
    adds: list[reqs.Addition]
    questions: list[Question]
    held: list[str]
    """Rows that no decision closes: the model has to be fixed (a measured design alert)."""


def flow_of(session: Session) -> str:
    return design_memory.load_brief(session.settings).drawing_flow


def require_sheet_flow(session: Session, action: str) -> None:
    if flow_of(session) != "sheet":
        raise ToolError(
            f"drawing {action} exists only in a project that builds sheets in one order (drawing_flow: sheet in its "
            'brief.yaml, the engineer\'s choice: memory(action="brief", drawing_flow="sheet")); this one builds them in '
            'steps: drawing(action="create") and a few items per add',
            code="invalid_input",
            field_name="action",
        )
    session.need("create_drawing", f"drawing {action}")
    session.need("annotate_drawing", f"drawing {action}")
    session.need("describe_drawing", f"drawing {action}")


# --- the seam of the layout ---------------------------------------------------------------------------------------------


def lay_out(session: Session, request: DrawingRequest | None, items: list[dict[str, Any]], remove: list[str]) -> None:
    """THE SEAM. Everything the order puts on the sheet goes through here: ``request`` (a create or a rebuild of the sheet,
    or ``None``), the ids to take off and the items to put on, in this order. Today the placement is the one of every
    ``add`` (items placed one after the other, the later one moves out of the way of the earlier). The global layout
    planner replaces the body: the same inputs, one call to the worker that places everything at once."""
    sheet = request.sheet if request is not None else None
    if request is not None:
        session.adapter.create_drawing(request)
    if remove:
        session.adapter.annotate_drawing(
            AnnotateDrawingRequest(operation_id="drawing_compose", action="remove", sheet=sheet, ids=tuple(remove))
        )
    for at in range(0, len(items), MAX_ITEMS_PER_CALL):
        session.adapter.annotate_drawing(
            AnnotateDrawingRequest(
                operation_id="drawing_compose", action="add", sheet=sheet, items=tuple(items[at : at + MAX_ITEMS_PER_CALL])
            )
        )
    session.dirty = True


# --- the decisions ------------------------------------------------------------------------------------------------------


def _bad(problems: list[str]) -> ToolError:
    return ToolError(
        "the decisions are not valid, nothing was changed: " + "; ".join(problems),
        code="invalid_input",
        field_name="decisions",
        usage='drawing(action="compose", decisions={"datums":[{"letter":"A","face":"face:3"}], "gdt":[...], "notes":["..."], '
        '"sheet":{"template":"A3"}, "answers":{"req.coating":"no aplica: la pieza no lleva recubrimiento"}}); cad_guide(topic="drawing:compose")',
    )


def normalise(decisions: dict[str, Any] | None) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, str]]:
    """The decisions as ``(items, sheet settings, answers by row)``; a decision that is not valid refuses the whole order."""
    data = decisions or {}
    problems: list[str] = []
    unknown = sorted(set(data) - KNOWN)
    if unknown:
        problems.append(f"unknown decision(s) {', '.join(unknown)}; they are {', '.join(sorted(KNOWN))}")
    items: list[dict[str, Any]] = []
    for key, types in KEYS.items():
        listed = data.get(key) or []
        if not isinstance(listed, list):
            problems.append(f"{key} is a list")
            continue
        for number, raw in enumerate(listed, start=1):
            spec = {"text": raw} if key == "notes" and isinstance(raw, str) else raw
            if not isinstance(spec, dict):
                problems.append(f"{key} #{number} is not an item like {{...}}")
                continue
            kind = spec.get("type", types[0])
            if kind not in types:
                problems.append(f"{key} #{number}: type {kind!r} is not one of {', '.join(types)}")
                continue
            if "id" in spec and not str(spec["id"]).startswith(COMPOSED_PREFIX):
                problems.append(f"{key} #{number}: leave the id out (the composer names it {COMPOSED_PREFIX}{key}{number})")
                continue
            items.append({**spec, "type": kind, "id": f"{COMPOSED_PREFIX}{key}{number}"})
    sheet = data.get("sheet") or {}
    if not isinstance(sheet, dict) or set(sheet) - SHEET_KEYS:
        problems.append(f"sheet is an object with {', '.join(sorted(SHEET_KEYS))}")
        sheet = {}
    answers = data.get("answers") or {}
    if not isinstance(answers, dict) or not all(isinstance(v, str) for v in answers.values()):
        problems.append("answers is {row id: 'applies: how, here' | 'no aplica: why'}")
        answers = {}
    if problems:
        raise _bad(problems)
    return items, sheet, dict(answers)


def _check_items(items: list[dict[str, Any]]) -> None:
    """The contract's own check of the items (what is wrong with one that needs no CAD), before anything is drawn."""
    if not items:
        return
    try:
        AnnotateDrawingRequest(operation_id="drawing_compose", action="add", items=tuple(items[:MAX_ITEMS_PER_CALL]))
        for at in range(MAX_ITEMS_PER_CALL, len(items), MAX_ITEMS_PER_CALL):
            AnnotateDrawingRequest(operation_id="drawing_compose", action="add", items=tuple(items[at : at + MAX_ITEMS_PER_CALL]))
    except ValidationError as exc:
        raise _bad([str(e["msg"]).removeprefix("Value error, ") for e in exc.errors()[:3]]) from exc


def _create_args(args: DrawingArgs, sheet: dict[str, Any]) -> DrawingArgs:
    """The arguments of a ``create`` for this order: what the call stated, then the decisions' ``sheet``."""
    fields = {
        name: getattr(args, name)
        for name in args.model_fields_set
        if name
        in (
            "template", "angle", "scale", "title", "author", "date", "material", "language", "drawing_number", "revision",
            "general_tolerances", "company", "title_block", "symmetry", "views", "isometric", "body", "projection", "fresh",
        )  # fmt: skip
    }
    return DrawingArgs(action="create", sheet=args.sheet, **{**fields, **sheet})


# --- what the harness knows ---------------------------------------------------------------------------------------------


def _survey(session: Session, sheet: str | None, extra: list[dict[str, Any]] | None = None) -> Survey:
    """What the sheet is, what the harness adds by itself and what it still has to ask."""
    info = drawing_gate.sheet_info(session, sheet, review=True)
    if info is None:
        raise ToolError("the document has no drawing sheet", code="invalid_input", field_name="sheet")
    language = design_memory.load_brief(session.settings).lang
    rows = drawing_gate.sheet_requirements(session, info)
    mode = drawing_gate.mode_of(session)
    placed: list[Any] = list(info.items)
    placed += [SimpleNamespace(id=i["id"], type=i["type"]) for i in extra or []]
    adds = (
        reqs.plan_additions(rows, placed, assembly=bool(info.facts.get("assembly")), views=tuple(info.views))
        if mode != "none" and info.facts
        else []
    )
    if (
        mode != "none"
        and any(f.code == "no_view_lines" for f in info.findings)
        and not any(i.type == "view_lines" for i in placed)
        and reqs.VIEW_LINES_ID not in {a.item["id"] for a in adds}
    ):  # the review asks for the axes of every hole and cylinder whatever the row says: they are derived
        item: dict[str, Any] = {"type": "view_lines", "id": reqs.VIEW_LINES_ID}
        if info.facts.get("assembly"):
            item["hidden"] = []  # the hidden edges of a whole assembly are not what the shop reads
        adds.append(reqs.Addition(("req.centre_lines",), item, "the centre lines and marks of every hole and cylinder"))
    meant = {row for a in adds for row in a.rows}
    questions: list[Question] = []
    held: list[str] = []
    for r in rows:
        if r.closed or r.id in meant:
            continue
        if r.row.model_only:
            held.append(r.id)
            continue
        if r.row.auto in ("callouts", "view_lines", "chamfers", "parts_list", "keyway") and r.id not in ROW_KEYS:
            continue  # the harness meets it when the model shows it (nothing to decide)
        keys = ROW_KEYS.get(r.id, ("notes", "answers"))
        questions.append(Question(keys, reqs.question_of(r, language), r.id))
    located = any(f.code in LOCATION_CODES and not reqs.finding_closed(f) for f in info.findings)
    if located and mode != "none":
        questions.insert(
            0,
            Question(
                LOCATION_KEYS,
                say(
                    language,
                    "¿Desde qué cara o arista se cuentan las posiciones de los agujeros? (una cara de datos con su letra; "
                    "o la simetría de la pieza si la intención la declara)",
                    "Which face or edge are the positions of the holes counted from? (a datum face with its letter; or the "
                    "part's symmetry if the intent declares it)",
                ),
            ),
        )
    return Survey(info, rows, adds, questions, held)


def _bare_sheet(session: Session, args: DrawingArgs, make_request: Any) -> tuple[Any, bool]:
    """The sheet of the order, and whether this call made it: a document with none gets the views, the paper and scale of
    the part and the title block, nothing annotated (the facts the plan reads are measured on it)."""
    info = drawing_gate.sheet_info(session, args.sheet)
    if info is not None:
        return info, False
    lay_out(session, make_request(session, _create_args(args, {})), [], [])
    info = drawing_gate.sheet_info(session, args.sheet, review=True)
    if info is None:
        raise ToolError("the CAD made no sheet", code="cad_error")
    if "views" not in args.model_fields_set and any(f.code == "single_view_enough" for f in info.findings):
        # a plate whose holes go through its thickness is drawn in ONE view (and the isometric): nothing is on the sheet
        # yet, so it is made again from scratch and the paper is chosen for the views that stay
        lay_out(session, make_request(session, _create_args(args, {"views": ["top", "iso"], "fresh": True})), [], [])
        info = drawing_gate.sheet_info(session, args.sheet)
    return info, True


def _derived_lines(survey: Survey) -> list[str]:
    info = survey.info
    have = {i.type for i in info.items}
    lines = [f"views: {', '.join(info.views)}; title block and overall dimensions"]
    if "datum_dims" in have:
        lines.append("hole positions (loc): from the datums or the declared symmetry; none is drawn without a reference")
    lines += [f"{a.item['id']}: {a.what} (meets {', '.join(a.rows)})" for a in survey.adds]
    return lines


# --- plan ---------------------------------------------------------------------------------------------------------------


def plan(session: Session, args: DrawingArgs, make_request: Any) -> Reply:
    require_sheet_flow(session, "plan")
    with_sheet, made = _bare_sheet(session, args, make_request)
    survey = _survey(session, with_sheet.sheet)
    info = survey.info
    lines = [
        f"plan for sheet {info.sheet}: {info.template} at {_ratio(info.scale)}, {info.language}"
        + (f"; made now with the views and the title block only (the facts below are measured on it): {len(info.items)} items" if made else ""),
        "the harness puts on the sheet by itself (you do not ask for these):",
        *(f"  - {line}" for line in _derived_lines(survey)),
    ]
    if survey.questions:
        lines.append("decisions it needs, asked of the engineer one at a time (the key is where compose takes the answer):")
        lines += [f"  - [{'|'.join(q.keys)}] {q.text}" + (f" ({q.row})" if q.row else "") for q in survey.questions]
    else:
        lines.append("decisions it needs: none open")
    if survey.held:
        lines.append(f"held by the model, no decision closes them: {', '.join(survey.held)} (fix the model; the review lists them)")
    lines.append(
        "next: show this plan to the engineer and wait for his go-ahead (he is the one who knows what the part is for); "
        'then drawing(action="compose", decisions={...}); cad_guide(topic="drawing:compose") has the shape'
    )
    return ok("\n".join(lines), limit=REPLY_CHARS)


def _ratio(scale: float) -> str:
    return f"1:{round(1 / scale, 2):g}" if scale < 1 else f"{scale:g}:1"


# --- compose ------------------------------------------------------------------------------------------------------------


def _covered(question: Question, decided: dict[str, Any], items: list[dict[str, Any]], answers: dict[str, str]) -> bool:
    if question.row and question.row in answers:
        return True
    for key in question.keys:
        if key == "answers":
            continue
        if key == "sheet":
            if decided.get("sheet"):
                return True
        elif any(i["id"].startswith(f"{COMPOSED_PREFIX}{key}") for i in items):
            return True
    return False


def _check_answers(session: Session, survey: Survey, answers: dict[str, str], by: str | None, reason: str | None) -> None:
    problems = []
    rows = {r.id: r for r in survey.rows}
    for row_id, text in answers.items():
        row = rows.get(row_id)
        if row is None:
            problems.append(f"answers: {row_id!r} is not a row that applies to this sheet ({', '.join(sorted(rows)) or 'none'})")
            continue
        try:
            verdict, _ = design_memory.checked_answer(session, None, text)
        except ToolError as exc:
            problems.append(f"answers[{row_id}]: {exc}")
            continue
        if verdict != reqs.APPLIES and (row.measured or row.row.model_only) and not (by and by.strip() and reason and reason.strip()):
            problems.append(
                f"answers[{row_id}]: the harness measured it (or the intent file says it): 'no aplica' only on the "
                "engineer's order (by=<him>, reason=<his words>)"
            )
    if problems:
        raise _bad(problems)


def compose(session: Session, args: DrawingArgs, make_request: Any) -> Reply:
    require_sheet_flow(session, "compose")
    items, sheet_over, answers = normalise(args.decisions)
    _check_items(items)
    paper_fixed = "template" in sheet_over or "template" in args.model_fields_set
    fixed_scale = sheet_over.get("scale") or (args.scale if "scale" in args.model_fields_set else None)
    info, made = _bare_sheet(session, args, make_request)
    survey = _survey(session, info.sheet, items)
    _check_answers(session, survey, answers, args.by, args.reason)
    open_questions = [q for q in survey.questions if not _covered(q, args.decisions or {}, items, answers)]
    if open_questions:
        raise ToolError(
            f"decisions missing, nothing was changed{' (the sheet has only its views and title block)' if made else ''}: "
            + " | ".join(f"[{'|'.join(q.keys)}] {q.text}" + (f" ({q.row})" if q.row else "") for q in open_questions),
            code="decisions_missing",
            field_name="decisions",
            usage='drawing(action="plan") lists them again; each is answered by the decision in brackets, or '
            'decisions.answers={"<row id>": "no aplica: <why>"} when the engineer says it does not apply',
        )
    request = None
    if sheet_over or (args.model_fields_set & {"title", "template", "scale", "angle", "views", "body", "fresh"}):
        request = make_request(session, _create_args(args, sheet_over))
    old = [i.id for i in survey.info.items if i.id.startswith(COMPOSED_PREFIX)]
    remove = list(old)
    if any(i["type"] == "datum_dims" for i in items) and "loc" in {i.id for i in survey.info.items}:
        remove.append("loc")  # the engineer chose the relation: the default location would dimension the holes twice
    derived = [a.item for a in survey.adds]
    lay_out(session, request, remove=remove, items=[*items, *derived])
    for row_id, text in answers.items():
        drawing_gate.answer(session, survey.info.sheet, row_id, text, args.by, args.reason)
    notes: list[str] = []
    if not paper_fixed:
        notes += _raise_paper(session, survey.info.sheet, make_request, fixed_scale)
    return ok("\n".join(_report(session, survey.info.sheet, items, derived, remove, notes)), limit=REPLY_CHARS)


def _problems(info: Any) -> set[str]:
    return {f.code for f in info.findings if f.code in LAYOUT_CODES}


def _raise_paper(session: Session, sheet: str, make_request: Any, fixed_scale: str | None) -> list[str]:
    """Nobody fixed the paper and the annotations do not fit: the next paper (A2 at most), and the reply says so. The paper
    the engineer fixed is never touched (the caller does not come here); a scale he fixed is kept."""
    notes: list[str] = []
    for _ in range(len(PAPER_STEPS) - 1):
        info = drawing_gate.sheet_info(session, sheet, review=True)
        if info is None or not _problems(info):
            break
        key = info.template.split("_")[0][:2]
        if key not in PAPER_STEPS[:-1]:
            break
        bigger = PAPER_STEPS[PAPER_STEPS.index(key) + 1]
        before = len(_problems(info))
        stated: dict[str, Any] = {"template": bigger, **({"scale": fixed_scale} if fixed_scale else {})}
        request = make_request(session, DrawingArgs(action="create", sheet=sheet, **stated))
        if not fixed_scale:  # the largest standard scale that fits the new paper, not the old one
            request = request.model_copy(update={"scale": None, "unset": tuple(n for n in request.unset if n != "scale")})
        lay_out(session, request, [], [])
        after = drawing_gate.sheet_info(session, sheet, review=True)
        if after is None or len(_problems(after)) >= before:  # a bigger paper that does not help is put back
            back = make_request(session, DrawingArgs(action="create", sheet=sheet, **{**stated, "template": key}))
            if not fixed_scale:
                back = back.model_copy(update={"scale": None, "unset": tuple(n for n in back.unset if n != "scale")})
            lay_out(session, back, [], [])
            notes.append(f"a paper bigger than {key} was tried and put back: it did not remove the {before} layout finding(s)")
            break
        notes.append(
            f"paper raised from {key} to {bigger} (nobody fixed it and the annotations did not fit: {before} layout "
            f"finding(s) before, {len(_problems(after))} after; scale now {_ratio(after.scale)})"
        )
    return notes


def _report(session: Session, sheet: str, items: list[dict[str, Any]], derived: list[dict[str, Any]], removed: list[str], notes: list[str]) -> list[str]:
    survey = _survey(session, sheet)
    info = survey.info
    mine = {i["id"] for i in items} | {d["id"] for d in derived}
    broken = [i for i in info.items if i.id in mine and i.status in ("error", "refused")]
    blocking = [f for f in info.findings if f.blocking and not reqs.finding_closed(f)]
    lines = [
        f"composed sheet {info.sheet}: {info.template} at {_ratio(info.scale)}, {len(info.items)} items "
        f"({len(items)} from your decisions, {len(derived)} derived by the harness)"
        + (f"; took off {', '.join(removed)}" if removed else "")
    ]
    lines += notes
    lines += [f"  {i.id} {i.type} {i.status}: {i.detail}" for i in broken]
    if derived:
        lines.append("derived: " + ", ".join(f"{d['id']} ({d['type']})" for d in derived))
    still = [q for q in survey.questions]
    lines.append(f"review: {len(blocking)} finding(s) hold the export; {len(still)} decision(s) still open")
    lines += [f"  open [{'|'.join(q.keys)}] {q.text}" for q in still]
    lines.append(
        'next: drawing(action="capture") and look at it (region= to read a dimension); drawing(action="review"); fix what '
        "a shop reader would reject with add / remove / update (a placed item stays where you put it); never rebuild with add what compose derives"
    )
    return lines
