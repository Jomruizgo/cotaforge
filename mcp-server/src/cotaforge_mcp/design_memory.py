"""Memory at the decision moment: roles of bodies and components, the project brief, the push of entries when an op
matches their triggers, and the answers the agent owes at a milestone.

The design state lives in two places that survive a save: the roles in the native document (the adapter keeps them
with the material and the route) and the rest -- stage, answers, the ops that are not plan operations -- in the
session record next to the file. An answer is accepted only for an entry the agent read in THIS server session.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from cotaforge.contract import AdapterError, SetDesignAttributesRequest, StyleRequest
from cotaforge.language import DEFAULT_LANGUAGE, normalize
from cotaforge.memory import (
    GENERAL_ID,
    MemoryEntry,
    Scope,
    SectorTable,
    Situation,
    Stage,
    normalize_text,
    push_line,
    pushed_by,
    relevant_to_sector,
    triggered_at_milestone,
)

from cotaforge_mcp import rules
from cotaforge_mcp.config import Settings
from cotaforge_mcp.reply import ToolError

if TYPE_CHECKING:
    from cotaforge_mcp.session import Session

BRIEF_FILE = "brief.yaml"
DRAWING_REVIEWS = ("engineer_or_fabricator", "fabricator", "engineer", "own", "none")
"""Who reviews a drawing before it is written (``drawing_review`` of the brief): the engineer or, if he does not choose
in time, the manufacturer agent | the manufacturer agent | the engineer | the drawing agent itself (the points of the
review) | nobody."""
DEFAULT_DRAWING_REVIEW = "engineer_or_fabricator"
OLD_DRAWING_REVIEWS = {
    "ingeniero_o_fabricante": "engineer_or_fabricator",
    "fabricante": "fabricator",
    "ingeniero": "engineer",
    "propio": "own",
    "ninguno": "none",
}
"""The Spanish values a brief had before 0.10: still read (and accepted in a ``memory brief`` call)."""


def drawing_review_mode(value: object) -> str:
    """The mode a brief value names (the old Spanish spelling included), lowercase; ``""`` when it names none."""
    text = str(value or "").strip().lower()
    return OLD_DRAWING_REVIEWS.get(text, text)


DEFAULT_REVIEW_WAIT_MIN = 15
ROLES_FILE = "roles.yaml"
MAX_PUSH_LINES = 3
MIN_ANSWER_WORDS = 3
APPLIES_PREFIXES = ("applies:", "aplica:", "corregido:", "fixed:", "met:", "cumple:")
# corregido / fixed: what a reviewer asked for; met / cumple: a requirement the sheet carries (where)
NOT_APPLIES_PREFIXES = ("does not apply:", "no aplica:")

# Roles of bodies and components: a closed default vocabulary, extensible by the engineer in ``roles.yaml``.
DEFAULT_ROLE_COLORS: dict[str, str] = {
    "estructura": "#8C8C8C",
    "soporte": "#0072B2",
    "contacto": "#D55E00",
    "piloto": "#CC79A7",
    "apoyo": "#56B4E9",
    "calza": "#F0E442",
    "actuador": "#E69F00",
    "sensor": "#009E73",
    "soldado": "#A0522D",
    "comercial": "#404040",
    "ajustable": "#7B3FA0",
    "fijacion": "#00CED1",
}


# ---- project files: brief and roles ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Brief:
    """What the engineer declared. ``sector`` is exactly what he wrote; ``sector_ids`` are the canonical sectors it
    resolves to (several for "chapa y soldadura"; none when the text is unknown or ambiguous)."""

    sector: str | None = None
    processes: tuple[str, ...] = ()
    sector_ids: tuple[str, ...] = ()
    ambiguous: tuple[tuple[str, tuple[str, ...]], ...] = ()
    """``(word, candidate ids)`` for a word of the sector that fits more than one sector: never assigned."""
    drawing_review: str = DEFAULT_DRAWING_REVIEW
    """Who reviews a drawing before it is exported (``DRAWING_REVIEWS``); an unknown value reads as the default."""
    drawing_review_wait_min: int = DEFAULT_REVIEW_WAIT_MIN
    """``engineer_or_fabricator``: minutes the engineer has to choose who reviews; then the manufacturer is assumed."""
    drawing_review_said: str = ""
    """The engineer's words that set a mode other than the default (kept so the file says who decided)."""
    language: str | None = None
    """The engineer's language (``es`` / ``en``) as the project states it; ``None``: it never did (see ``lang``)."""

    @property
    def lang(self) -> str:
        """The language the project speaks to the engineer: the stated one, else the default."""
        return self.language or DEFAULT_LANGUAGE

    @property
    def sector_words(self) -> list[str]:
        return [word for word in (self.sector, *self.processes) if word]

    @property
    def declared(self) -> bool:
        """True when the sector resolves to a real sector (not 'general', not unknown)."""
        return bool(self.sector_ids) and self.sector_ids != (GENERAL_ID,)


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def sector_table(settings: Settings) -> SectorTable:
    """The canonical sectors: the product's, extended by the user's and the project's ``sectors.yaml``."""
    return rules.memory_store(settings).sector_table()


def _brief_of(
    sector: str | None,
    processes: tuple[str, ...],
    table: SectorTable,
    review: str = DEFAULT_DRAWING_REVIEW,
    wait_min: int = DEFAULT_REVIEW_WAIT_MIN,
    said: str = "",
    language: str | None = None,
) -> Brief:
    resolution = table.resolve(sector)
    return Brief(sector, processes, resolution.ids, resolution.ambiguous, review, wait_min, said, language)


def _wait_minutes(value: Any) -> int:
    try:
        minutes = int(value)
    except (TypeError, ValueError):
        return DEFAULT_REVIEW_WAIT_MIN
    return minutes if minutes >= 0 else DEFAULT_REVIEW_WAIT_MIN


def workspace_language(workspace: Path) -> str:
    """The language the design space ``workspace`` speaks to the engineer (``language`` of its ``brief.yaml``; Spanish
    when it states none). Read from the file every time, so a change takes effect on the next message without
    restarting the server."""
    return normalize(_read_yaml(workspace / BRIEF_FILE).get("language")) or DEFAULT_LANGUAGE


def project_language(settings: Settings) -> str:
    return workspace_language(settings.workspace)


def stated_language(workspace: Path) -> str | None:
    """The language ``workspace`` states in its ``brief.yaml``; ``None`` when it states none (an older project)."""
    return normalize(_read_yaml(workspace / BRIEF_FILE).get("language"))


def write_language(workspace: Path, language: str) -> str:
    """Put ``language`` in the ``brief.yaml`` of ``workspace``, leaving every other key as it is (the file is created
    when it does not exist). Returns ``created`` / ``updated`` / ``unchanged``."""
    chosen = normalize(language)
    if chosen is None:
        msg = f"unknown language {language!r}: use one of es, en"
        raise ValueError(msg)
    path = workspace / BRIEF_FILE
    existed = path.is_file()
    data = _read_yaml(path)
    if data.get("language") == chosen:
        return "unchanged"
    data = {"language": chosen, **{key: value for key, value in data.items() if key != "language"}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return "updated" if existed else "created"


def load_brief(settings: Settings) -> Brief:
    data = _read_yaml(settings.workspace / BRIEF_FILE)
    sector = str(data.get("sector") or "").strip() or None
    processes = tuple(str(p).strip() for p in data.get("processes") or () if str(p).strip())
    review = drawing_review_mode(data.get("drawing_review"))
    return _brief_of(  # resolved with today's table, not the stored ids
        sector,
        processes,
        sector_table(settings),
        review if review in DRAWING_REVIEWS else DEFAULT_DRAWING_REVIEW,
        _wait_minutes(data.get("drawing_review_wait_min", DEFAULT_REVIEW_WAIT_MIN)),
        str(data.get("drawing_review_said") or "").strip(),
        normalize(data.get("language")),
    )


def save_brief(
    settings: Settings,
    sector: str | None,
    processes: list[str] | None,
    drawing_review: str | None = None,
    wait_min: int | None = None,
    said: str | None = None,
    language: str | None = None,
) -> Brief:
    """Keep what the engineer wrote in ``sector`` and the ids it resolves to in ``sector_ids`` (informative: the ids
    are resolved again on every load, so a sector added to ``sectors.yaml`` later takes effect), and how his drawings
    are reviewed (``drawing_review``, ``drawing_review_wait_min``; ``said`` his words when it is not the default) and
    the language he is spoken to in (``language``: ``es`` / ``en``; omitted, the one the project has)."""
    current = load_brief(settings)
    review = current.drawing_review if drawing_review is None else drawing_review
    if review == DEFAULT_DRAWING_REVIEW:
        said = ""  # the default needs no authority
    brief = _brief_of(
        current.sector if sector is None else (sector.strip() or None),
        current.processes if processes is None else tuple(p.strip() for p in processes if p.strip()),
        sector_table(settings),
        review,
        current.drawing_review_wait_min if wait_min is None else wait_min,
        current.drawing_review_said if said is None else " ".join(said.split()),
        normalize(language) or current.language,
    )
    data: dict[str, Any] = {}
    if brief.language:
        data["language"] = brief.language
    if brief.sector:
        data["sector"] = brief.sector
        if brief.sector_ids:
            data["sector_ids"] = list(brief.sector_ids)
    if brief.processes:
        data["processes"] = list(brief.processes)
    if brief.drawing_review != DEFAULT_DRAWING_REVIEW:
        data["drawing_review"] = brief.drawing_review
        if brief.drawing_review_said:
            data["drawing_review_said"] = brief.drawing_review_said
    if brief.drawing_review_wait_min != DEFAULT_REVIEW_WAIT_MIN:
        data["drawing_review_wait_min"] = brief.drawing_review_wait_min
    path = settings.workspace / BRIEF_FILE
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return brief


def role_palette(settings: Settings) -> dict[str, str]:
    """Role -> ``#rrggbb``: the defaults, changed or extended by the engineer's ``roles.yaml`` (``role: "#rrggbb"``)."""
    palette = dict(DEFAULT_ROLE_COLORS)
    mine = _read_yaml(settings.workspace / ROLES_FILE).get("roles")
    for name, color in mine.items() if isinstance(mine, dict) else ():
        key = normalize_text(str(name)).replace(" ", "-")
        text = str(color).strip()
        if key and len(text) == 7 and text.startswith("#"):
            palette[key] = text
    return palette


def normal_role(text: str) -> str:
    return normalize_text(text.strip()).replace(" ", "-")


def checked_role(settings: Settings, text: str) -> str:
    """The role id for ``text`` or a ToolError listing the vocabulary ('' stays '': it clears the role)."""
    if not text.strip():
        return ""
    role = normal_role(text)
    palette = role_palette(settings)
    if role not in palette:
        raise ToolError(
            f"unknown role {text!r}; roles: {', '.join(palette)}. A new role (and its colour) is added by the "
            f"engineer in {ROLES_FILE} of the design space: roles: {{name: '#rrggbb'}}",
            code="unknown_role",
            field_name="params.role",
        )
    return role


# ---- the state of one document ----------------------------------------------------------------------------------


@dataclass
class MemState:
    roles: dict[str, str] = field(default_factory=dict)
    """step id -> role; mirrored in the native document."""
    stage: str | None = None
    answers: dict[str, dict[str, str]] = field(default_factory=dict)
    ops_seen: set[str] = field(default_factory=set)
    """Actions that are not plan steps (drawing, export, edit/delete of a feature, native): they trigger entries too."""
    read: set[str] = field(default_factory=set)
    """Entries the agent read with ``memory get`` in this server session (never saved)."""
    pushed: set[str] = field(default_factory=set)
    """``entry|op`` already pushed in this server session: the same line is not repeated for the same op (never saved)."""

    def to_json(self) -> dict[str, Any]:
        return {"stage": self.stage, "answers": self.answers, "ops_seen": sorted(self.ops_seen)}

    @classmethod
    def from_json(cls, data: dict[str, Any] | None) -> "MemState":
        data = data or {}
        answers = {str(k): {str(a): str(b) for a, b in v.items()} for k, v in dict(data.get("answers") or {}).items()}
        stage = data.get("stage")
        return cls(stage=str(stage) if stage else None, answers=answers, ops_seen=set(data.get("ops_seen") or ()))


def effective_roles(session: "Session") -> dict[str, str]:
    """The roles of steps still in effect."""
    known = session.known_ids()
    return {step: role for step, role in session.mem.roles.items() if step in known}


def current_stage(session: "Session") -> Stage:
    if session.mem.stage:
        return Stage(session.mem.stage)
    return Stage.concept if session.purpose == "sketch" else Stage.detail


def load_roles(session: "Session", record_roles: dict[str, str]) -> None:
    """After an open: the native document's roles win; a file that came back without them gets the record's."""
    native: dict[str, str] = {}
    if "design_attributes" in session.capabilities.tools:
        native = dict(session.design_attributes().roles)
    session.mem.roles = native or dict(record_roles)
    if not native and session.mem.roles:
        _write_native(session)


def _write_native(session: "Session") -> None:
    if "set_design_attributes" not in session.capabilities.tools:
        return
    session.adapter.set_design_attributes(
        SetDesignAttributesRequest(operation_id=session._op_id("roles"), roles=dict(session.mem.roles))  # noqa: SLF001
    )


# ---- setting roles -----------------------------------------------------------------------------------------------


def _made_something(session: "Session", step: str) -> bool:
    context = session.executor.context if session.executor is not None else None
    if context is None:
        return False
    return step in context.body_by_op or step in context.body_sides or step in context.component_by_op


def set_role(session: "Session", step: str, role: str) -> list[str]:
    """Give the body or component made by ``step`` a role ('' clears it); stored in the document."""
    if step not in session.known_ids():
        raise ToolError(
            f"{step!r} is not a step in effect; a role goes on the step that made the body or component",
            code="invalid_input",
            field_name="edit",
        )
    if role and not _made_something(session, step):
        return [f"note: role {role!r} not set: {step} made no body or component"]
    if role:
        session.mem.roles[step] = role
    else:
        session.mem.roles.pop(step, None)
    _write_native(session)
    session.dirty = True
    lines = [f"role: {step} = {role}" if role else f"role: {step} cleared"]
    lines.extend(paint(session, [step] if role else []))
    return lines


def paint(session: "Session", steps: list[str] | None = None) -> list[str]:
    """Colour the bodies by role in the window (display only); ``steps=None`` paints every body with a role."""
    if "style_bodies" not in session.capabilities.tools:
        return []
    palette = role_palette(session.settings)
    context = session.executor.context if session.executor is not None else None
    if context is None:
        return []
    painted = 0
    for step, role in effective_roles(session).items():
        if steps is not None and step not in steps:
            continue
        sides = context.body_sides.get(step)
        bodies = list(sides.values()) if sides else ([context.body_by_op[step]] if step in context.body_by_op else [])
        for body in bodies:
            try:
                session.adapter.style_bodies(
                    StyleRequest(operation_id=session._op_id("paint"), bodies=(body,), color=palette[role])  # noqa: SLF001
                )
                painted += 1
            except AdapterError:
                return []  # headless CAD: there is no window to colour
    return [f"colours by role: {painted} body(ies) painted"] if painted and steps is None else []


# ---- the push -------------------------------------------------------------------------------------------------------

_cache: dict[str, Any] = {}


def _signature(settings: Settings) -> tuple[Any, ...]:
    store = rules.memory_store(settings)
    rows: list[tuple[str, int]] = []
    for scope in (Scope.project, Scope.user, Scope.base):
        directory = store.directory(scope)
        if directory is None or not directory.is_dir():
            continue
        with os.scandir(directory) as scan:
            rows.extend((entry.path, entry.stat().st_mtime_ns) for entry in scan if entry.name.endswith(".md"))
    return tuple(sorted(rows))


def active_entries(settings: Settings) -> list[MemoryEntry]:
    """Active entries of the merged memory, re-read only when a memory file changed."""
    signature = _signature(settings)
    key = f"{settings.workspace}|{settings.home}|{settings.cad}"
    cached = _cache.get(key)
    if cached is not None and cached[0] == signature:
        return list(cached[1])
    entries = [item.entry for item in rules.memory_store(settings).entries(include_inactive=False)]
    _cache[key] = (signature, entries)
    return list(entries)


def _walk_strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _walk_strings(item)]
    if isinstance(value, list | tuple):
        return [text for item in value for text in _walk_strings(item)]
    return []


def involved_roles(session: "Session", params: dict[str, Any], own: str = "") -> frozenset[str]:
    roles = effective_roles(session)
    found = {roles[text] for text in _walk_strings(params) if text in roles}
    if own:
        found.add(own)
    return frozenset(found)


def _of_the_project_sector(settings: Settings, entries: list[MemoryEntry]) -> list[MemoryEntry]:
    """Drop the practices of a sector other than the one the project declared: neither pushed nor required."""
    if not any(entry.sector for entry in entries):
        return entries
    brief = load_brief(settings)
    if not brief.declared:
        return entries
    table = sector_table(settings)
    return [entry for entry in entries if relevant_to_sector(entry, brief.sector, table)]


def push(session: "Session", op: str, roles: frozenset[str]) -> list[str]:
    """One line per active entry the op calls for: ``memory: <id> — recognize: ...`` (cheap: no full entries)."""
    try:
        entries = active_entries(session.settings)
    except OSError:
        return []
    situation = Situation(op=op, roles=roles, stage=current_stage(session))
    hits = [
        entry
        for entry in _of_the_project_sector(session.settings, entries)
        if pushed_by(entry, situation) and f"{entry.id}|{op}" not in session.mem.pushed
    ]
    session.mem.pushed.update(f"{entry.id}|{op}" for entry in hits[:MAX_PUSH_LINES])
    if not hits:
        return []
    lines = [push_line(entry) for entry in hits[:MAX_PUSH_LINES]]
    if len(hits) > MAX_PUSH_LINES:
        lines.append(f"memory: +{len(hits) - MAX_PUSH_LINES} more: {', '.join(e.id for e in hits[MAX_PUSH_LINES:])}")
    lines.append("memory: read the entry (memory get id=...) and answer at the milestone with memory(action=answer)")
    return lines


def after_op(session: "Session", step: str, op: str, params: dict[str, Any], role: str) -> list[str]:
    """Lines to add to the result of a successful op: the role set, the colours and the pushed memory."""
    lines: list[str] = []
    if role:
        lines.extend(set_role(session, step, role))
    own = session.mem.roles.get(step, "")
    lines.extend(push(session, op, involved_roles(session, params, own)))
    return lines


def after_action(session: "Session", op: str, params: dict[str, Any] | None = None) -> list[str]:
    """Edit/delete of a feature, drawing and export, native steps: remember the action and push."""
    session.mem.ops_seen.add(op)
    roles = involved_roles(session, params or {})
    if op.startswith("drawing_") or op == "native":
        roles = frozenset(effective_roles(session).values())
    return push(session, op, roles)


# ---- milestone: review and answers ---------------------------------------------------------------------------------


def triggered_for(session: "Session", stage: Stage) -> list[MemoryEntry]:
    roles = set(effective_roles(session).values())
    ops = {step.op for step in session.steps()} | session.mem.ops_seen
    return [
        entry
        for entry in _of_the_project_sector(session.settings, active_entries(session.settings))
        if triggered_at_milestone(entry, stage=stage, roles=roles, ops_run=ops)
    ]


def answer_problem(text: str) -> str | None:
    """Why ``text`` is not an answer (``None``: it is). It must say how the entry applies here, or why it does not."""
    lowered = text.strip().lower()
    prefixes = (*APPLIES_PREFIXES, *NOT_APPLIES_PREFIXES)
    prefix = next((p for p in prefixes if lowered.startswith(p)), None)
    if prefix is None:
        return (
            "start with 'applies:' (how; 'corregido:' for a change you made; 'met:' for a requirement, and where on "
            "the sheet) or 'does not apply:' (why not)"
        )
    if len(lowered.removeprefix(prefix).split()) < MIN_ANSWER_WORDS:
        return "say how it applies to which part, or why it does not (a few words, not 'yes')"
    return None


def checked_answer(session: "Session", entry_id: str | None, text: str) -> tuple[str, str]:
    """``(verdict, text)`` of an answer: an entry (``None``: a finding or question that is not memory) must have been read
    with ``memory get`` in THIS server session, and the text must say how it applies here or why it does not. Shared by
    the answers at a milestone and the answers on a drawing sheet."""
    if entry_id is not None and entry_id not in session.mem.read:
        raise ToolError(
            f"read {entry_id!r} first: memory(action=get, id={entry_id!r}); an answer is accepted only for an entry "
            "you read in this session",
            code="not_read",
            field_name="id",
        )
    problem = answer_problem(text)
    if problem:
        raise ToolError(f"the answer is not enough: {problem}", code="bad_answer", field_name="text")
    lowered = text.strip().lower()
    verdict = "applies" if lowered.startswith(APPLIES_PREFIXES) else "not_applies"
    return verdict, " ".join(text.split())


DRAWING_OPS = frozenset({"drawing_create", "drawing_dimension", "drawing_export"})
"""The ops of making a drawing that memory entries name: exporting one means all of them happened to it."""


def drawing_entries(session: "Session") -> list[MemoryEntry]:
    """The active entries the export of a drawing calls for: those that name an op of the drawing (``drawing_create``,
    ``drawing_dimension``, ``drawing_export``) and whose stage (detail) and roles match -- the base drawing principles
    and the engineer's own. The milestone rule of memory (``triggered_at_milestone``), asked for before the export."""
    roles = set(effective_roles(session).values())
    ops = {step.op for step in session.steps()} | session.mem.ops_seen | DRAWING_OPS
    return [
        entry
        for entry in _of_the_project_sector(session.settings, active_entries(session.settings))
        if entry.when is not None
        and any(op.startswith("drawing_") for op in entry.when.ops)
        and triggered_at_milestone(entry, stage=Stage.detail, roles=roles, ops_run=ops)
    ]


def record_answer(session: "Session", entry_id: str, text: str) -> str:
    entries = {entry.id: entry for entry in active_entries(session.settings)}
    if entry_id not in entries:
        raise ToolError(
            f"no active entry {entry_id!r} to answer (a proposed entry is not a rule until the engineer approves it)",
            code="unknown_entry",
            field_name="id",
        )
    verdict, said = checked_answer(session, entry_id, text)
    session.mem.answers[entry_id] = {"verdict": verdict, "text": said, "stage": current_stage(session).value}
    return f"answered: {entry_id} ({'applies' if verdict == 'applies' else 'does not apply'}) at stage {current_stage(session).value}"


def review(session: "Session", stage: Stage) -> tuple[list[str], list[str]]:
    """``(answered lines, pending lines)`` for the entries triggered in ``stage``."""
    session.mem.stage = stage.value
    answered: list[str] = []
    pending: list[str] = []
    for entry in triggered_for(session, stage):
        given = session.mem.answers.get(entry.id)
        if given is not None and given.get("stage") == stage.value:
            label = "APPLIES" if given["verdict"] == "applies" else "DOES NOT APPLY"
            answered.append(f"- {entry.id}: {label}: {given['text']}")
        else:
            read = "read" if entry.id in session.mem.read else "NOT read: memory(action=get) first"
            pending.append(
                f"- {entry.id} — recognize: {' '.join((entry.recognize or entry.applies_when).split())} [{read}]"
            )
    return answered, pending


def status_lines(session: "Session") -> list[str]:
    lines = []
    roles = effective_roles(session)
    if roles:
        lines.append("roles: " + ", ".join(f"{step}={role}" for step, role in roles.items()))
    if session.mem.answers:
        lines.append(f"memory answers: {len(session.mem.answers)} given (memory(action=review, stage=...) lists them)")
    return lines
