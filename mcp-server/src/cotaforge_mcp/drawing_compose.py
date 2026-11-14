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

from cotaforge.contract import AdapterError, AnnotateDrawingRequest, DrawingRequest
from cotaforge.language import say
from pydantic import ValidationError

from cotaforge_mcp import design_memory, drawing_gate, drawing_weld_questions
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
LEGIBLE_SCALE = 0.2
"""The legibility floor of a sheet (ISO 5455, 1:5; the same one the CAD uses to choose a paper): a bigger paper keeps the scale of
the smaller one, and a scale below the floor rises to it."""
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
    "positions": ("dim",),
    "notes": ("note",),
}
"""The decisions that are lists of items, and the item types each takes (the first is the default type)."""
SHEET_KEYS = frozenset(
    {
        "template", "angle", "scale", "title", "author", "date", "material", "language", "drawing_number", "revision",
        "general_tolerances", "company", "title_block", "symmetry", "views", "isometric", "body",
    }
)  # fmt: skip
WELD_KEYS = ("bodies", "welding")
"""The decisions of a welded part that are not items of their own: ``bodies`` (the designation of each position of the cut
list: it becomes the rows of its ``parts_list``) and ``welding`` (process, ISO 5817 quality level and ISO 13920 class: it
becomes the weld note)."""
KNOWN = frozenset({*KEYS, *WELD_KEYS, "sheet", "answers"})
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
    joints: tuple[str, ...] = ()
    """The joints of a welded part this question is about (``body:1+body:5``): it is covered when weld items name them all."""


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


@dataclass(frozen=True)
class Layout:
    """What the global layout planner did with a sheet: the items it moved, the ones it could not place (with the reason: they
    stay where they were and are reported, never dropped), whether the views and the room their annotations need fit the
    paper (``needed``: width and height in mm when they do not) and, when the worker could not plan, why (``failed``)."""

    placed: tuple[str, ...] = ()
    unplaced: tuple[str, ...] = ()
    fits: bool = True
    needed: tuple[float, float] = (0.0, 0.0)
    failed: str = ""

    @property
    def open(self) -> int:
        """How many things the paper has to solve: the items without a place, and one when the views do not fit."""
        return len(self.unplaced) + (0 if self.fits else 1)


def lay_out(
    session: Session,
    request: DrawingRequest | None,
    items: list[dict[str, Any]],
    remove: list[str],
    *,
    relayout: bool = False,
) -> Layout | None:
    """THE SEAM. Everything the order puts on the sheet goes through here: ``request`` (a create or a rebuild of the sheet,
    or ``None``), the ids to take off and the items to put on, in this order, and then ONE call to the worker that places the
    annotations of the whole sheet at once (the global layout planner: it sees every view and every item). ``relayout`` asks
    for that call when nothing is put on (a sheet rebuilt on another paper). Returns what the planner did, ``None`` when
    nothing was put on and no layout was asked for."""
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
    return _plan(session, sheet) if items or relayout else None


def _plan(session: Session, sheet: str | None) -> Layout:
    """The one call that places everything (a sheet the CAD cannot plan keeps the placement of every ``add``: it says so)."""
    try:
        done = session.adapter.annotate_drawing(AnnotateDrawingRequest(operation_id="drawing_compose", action="layout", sheet=sheet))
    except AdapterError as exc:
        return Layout(failed=str(exc))
    return Layout(done.layout_placed, done.layout_unplaced, done.layout_fits, tuple(done.layout_needed), "")


# --- the decisions ------------------------------------------------------------------------------------------------------


def _bad(problems: list[str]) -> ToolError:
    return ToolError(
        "the decisions are not valid, nothing was changed: " + "; ".join(problems),
        code="invalid_input",
        field_name="decisions",
        usage='drawing(action="compose", decisions={"datums":[{"letter":"A","face":"face:3"}], "gdt":[...], "notes":["..."], '
        '"sheet":{"template":"A3"}, "answers":{"req.coating":"no aplica: la pieza no lleva recubrimiento"}}); cad_guide(topic="drawing:compose")',
    )


def normalise(
    decisions: dict[str, Any] | None, language: str = "es"
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, str]]:
    """The decisions as ``(items, sheet settings, answers by row)``; a decision that is not valid refuses the whole order.
    ``language`` is the one the weld note is written in."""
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
    sheet = dict(sheet)
    items += _body_rows(data.get("bodies"), problems)
    note, tolerance = _weld_note(data.get("welding"), language, problems)
    if note:
        items.append(note)
        sheet.setdefault("general_tolerances", tolerance)  # the title block says the same class as the note
    answers = data.get("answers") or {}
    if not isinstance(answers, dict) or not all(isinstance(v, str) for v in answers.values()):
        problems.append("answers is {row id: 'applies: how, here' | 'no aplica: why'}")
        answers = {}
    if problems:
        raise _bad(problems)
    return items, sheet, dict(answers)


def _body_rows(bodies: Any, problems: list[str]) -> list[dict[str, Any]]:
    """The designations of the cut list of a welded part as the rows of ONE ``parts_list`` item."""
    if not bodies:
        return []
    fields = ("name", "reference", "material", "remarks")
    if not isinstance(bodies, list) or not all(
        isinstance(b, dict) and isinstance(b.get("component"), str) and isinstance(b.get("name"), str) and b["name"].strip() and set(b) <= {"component", *fields}
        for b in bodies
    ):
        problems.append('bodies is a list of {"component": "body:1", "name": "<designation: profile and size>"} (a name is never empty)')
        return []
    return [{"type": "parts_list", "id": f"{COMPOSED_PREFIX}bodies1", "rows": [dict(b) for b in bodies]}]


def _level(value: Any, standard: str, allowed: str, what: str, problems: list[str]) -> str:
    """The letter of a level or class (``C``, ``ISO 5817-C``, ``5817 C``); empty (and a problem said) when it is not valid."""
    text = str(value or "").strip().upper()
    letter = text[-1:] if text else ""
    if letter not in allowed or (len(text) > 1 and standard not in text):
        problems.append(f"welding.{what} is one of {', '.join(allowed)} (of ISO {standard}), not {value!r}")
        return ""
    return letter


def _weld_note(welding: Any, language: str, problems: list[str]) -> tuple[dict[str, Any] | None, str]:
    """The weld note of a welded part from the designer's decision (process, ISO 5817 quality level, ISO 13920 class of the
    general tolerances), and the general tolerance for the title block. It never states a size: the symbols do."""
    if not welding:
        return None, ""
    keys = {"process", "quality", "tolerance"}
    if not isinstance(welding, dict) or set(welding) != keys or not str(welding.get("process") or "").strip():
        problems.append('welding is {"process": "MAG", "quality": "C" (ISO 5817: B, C or D), "tolerance": "B" (ISO 13920: A, B, C or D)}')
        return None, ""
    before = len(problems)
    quality = _level(welding["quality"], "5817", "BCD", "quality", problems)
    cls = _level(welding["tolerance"], "13920", "ABCD", "tolerance", problems)
    if len(problems) > before:
        return None, ""
    process = str(welding["process"]).strip()
    text = say(
        language,
        f"Proceso de soldadura {process}; nivel de calidad ISO 5817-{quality}; tolerancias generales de la construcción soldada ISO 13920-{cls}",
        f"Welding process {process}; quality level ISO 5817-{quality}; general tolerances of the welded construction ISO 13920-{cls}",
    )
    return {"type": "note", "id": f"{COMPOSED_PREFIX}welding1", "text": text}, f"ISO 13920-{cls}"


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


def _survey(session: Session, sheet: str | None, extra: list[dict[str, Any]] | None = None, again: bool = True) -> Survey:
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
        reqs.plan_additions(
            rows,
            placed,
            assembly=bool(info.facts.get("assembly")),
            views=tuple(info.views),
            external_threads=bool(info.facts.get("thread_features")),
            weldment=bool(info.facts.get("weldment")),
        )
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
        if r.id == "req.welds" and info.weldment is not None and info.weldment.joints:
            continue  # asked joint group by joint group below
        keys = ROW_KEYS.get(r.id, ("notes", "answers"))
        questions.append(Question(keys, reqs.question_of(r, language), r.id))
    recompose = again and any(i.id.startswith(COMPOSED_PREFIX) for i in info.items)  # a new compose takes them off: everything is asked again
    for ask in drawing_weld_questions.ask(info.weldment, language, recompose, bool(info.facts.get("weld_class_on_sheet")), tuple(info.views), any(i.id.startswith(f"{COMPOSED_PREFIX}positions") for i in info.items)):
        questions.append(Question(ask.keys, ask.text, "", ask.joints))
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
    if question.joints:  # a group of joints: every one of them has a weld item
        return set(question.joints) <= drawing_weld_questions.weld_keys(items)
    for key in question.keys:
        if key == "answers":
            continue
        if key == "positions":
            if decided.get("positions") is not None:  # an empty list is the decision that nothing needs locating
                return True
        elif key == "sheet":
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
    items, sheet_over, answers = normalise(args.decisions, design_memory.load_brief(session.settings).lang)
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
    layout = lay_out(session, request, remove=remove, items=[*items, *derived])
    reviewed = drawing_gate.sheet_info(session, survey.info.sheet, review=True) if answers else None  # once, not per answer
    for row_id, text in answers.items():
        drawing_gate.answer(session, survey.info.sheet, row_id, text, args.by, args.reason, reviewed)
    notes: list[str] = []
    if not paper_fixed:
        paper, layout = _raise_paper(session, survey.info.sheet, make_request, fixed_scale, layout)
        notes += paper
    return ok("\n".join(_report(session, survey.info.sheet, items, derived, remove, notes, layout)), limit=REPLY_CHARS)


def _problems(info: Any) -> set[str]:
    """The layout findings of the review; on a welded part also the collisions: its cut list, notes, balloons and symbols crowd
    a small paper, and a bigger one (at the same scale) is what gives them room."""
    codes = LAYOUT_CODES | ({"collision"} if info.facts.get("weldment") else set())
    return {f.code for f in info.findings if f.code in codes}


def _open(info: Any, layout: Layout | None) -> int:
    """What a bigger paper has to solve: the layout findings of the review, the items the planner could not place and the views
    that do not fit."""
    return len(_problems(info)) + (layout.open if layout is not None else 0)


def _raise_paper(
    session: Session, sheet: str, make_request: Any, fixed_scale: str | None, layout: Layout | None = None
) -> tuple[list[str], Layout | None]:
    """Nobody fixed the paper and the annotations do not fit: the next paper (A2 at most), and the reply says so. The paper
    the engineer fixed is never touched (the caller does not come here); a scale he fixed is kept."""
    notes: list[str] = []
    for _ in range(len(PAPER_STEPS) - 1):
        info = drawing_gate.sheet_info(session, sheet, review=True)
        if info is None or not _open(info, layout):
            break
        key = info.template.split("_")[0][:2]
        if key not in PAPER_STEPS[:-1]:
            break
        bigger = PAPER_STEPS[PAPER_STEPS.index(key) + 1]
        before = _open(info, layout)
        # the bigger paper is for the room the annotations need, not for a bigger scale: the scale stays (the legibility floor
        # at least), a scale the engineer fixed is kept
        keep = fixed_scale or _ratio(max(info.scale, LEGIBLE_SCALE))
        stated: dict[str, Any] = {"template": bigger, "scale": keep}
        request = make_request(session, DrawingArgs(action="create", sheet=sheet, **stated))
        planned = lay_out(session, request, [], [], relayout=True)
        after = drawing_gate.sheet_info(session, sheet, review=True)
        if after is None or _open(after, planned) >= before:  # a bigger paper that does not help is put back
            back = make_request(session, DrawingArgs(action="create", sheet=sheet, template=key, **({"scale": fixed_scale} if fixed_scale else {})))
            if not fixed_scale:
                back = back.model_copy(update={"scale": None, "unset": tuple(n for n in back.unset if n != "scale")})
            layout = lay_out(session, back, [], [], relayout=True)
            notes.append(f"a paper bigger than {key} was tried and put back: it did not remove the {before} layout finding(s)")
            return notes, layout

        notes.append(
            f"paper raised from {key} to {bigger} (nobody fixed it and the annotations did not fit: {before} layout "
            f"finding(s) before, {_open(after, planned)} after; scale now {_ratio(after.scale)})"
        )
        layout = planned
    return notes, layout


def _layout_lines(layout: Layout | None) -> list[str]:
    """What the global layout did, and -- always -- what it could not place."""
    if layout is None:
        return []
    if layout.failed:
        return [f"layout: the CAD could not plan the sheet ({layout.failed}); every item keeps the place its own add gave it"]
    lines = [f"layout: {len(layout.placed)} dimension(s) placed by the global planner" + ("" if layout.fits else f"; the views and their annotations need {layout.needed[0]:g} x {layout.needed[1]:g} mm and do not fit the paper")]
    lines += [f"  NOT PLACED {line} (it stays where it was drawn; place it with side or offset_mm or change the sheet)" for line in layout.unplaced]
    return lines


def _report(session: Session, sheet: str, items: list[dict[str, Any]], derived: list[dict[str, Any]], removed: list[str], notes: list[str], layout: Layout | None = None) -> list[str]:
    survey = _survey(session, sheet, again=False)
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
    lines += _layout_lines(layout)
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
