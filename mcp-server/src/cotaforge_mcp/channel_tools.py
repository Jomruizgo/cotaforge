# ruff: noqa: E501
"""The ``channel`` tool: the questions of the drafting session (``drafter``) to the design session (``designer``).

The two sessions are two Claude Code sessions in the same design folder, each with its own server process, so the
channel lives in files of that folder, per drawing:

* ``drawings/<name>-questions.jsonl`` -- the log: one JSON event per line, appended under a lock, never rewritten;
* ``drawings/<name>-questions.md`` -- the same, readable (the engineer can open it); rewritten after every event.

An event is ``ask`` (a question of the drafter, with its proposed answer), ``reply``, ``dispute`` (an expert objects to
an answer and asks to hold it), ``decide`` (the engineer's decision, with the objection written next to it when there
was one) or ``done``. Every event carries ``n`` (its place in the log: a reader asks for what came after the last ``n``
it saw, so a reader needs no memory of its own and survives a restart), ``via`` (the session that wrote it) and ``by``
(whose words they are): a reply typed by the human in the drawing session is ``by=engineer`` (the human turn is the
engineer by definition) and is written via the drafter, so the designer, who waits on the log, learns what the engineer
answered; a reply from the design session is ``by=designer``. The drafter never answers its own question.

An engineer's answer is not yet a decision: both agents keep the expert's role and may object (``dispute``). Each
question has a status from its last event: open (no answer), answered, disputed (held), decided. A disputed question is
decided only with the objection on record. The server cannot prove who is at the keyboard (any agent can call the tool,
like ``review_report``): the rules refuse the combinations that cannot be true.

``wait`` blocks the call for a bounded time (default 2 min, at most 10) and returns as soon as another session wrote
something; with nothing new it says so and the caller calls again. Claude Code lets a stdio tool call run for hours and
only drops one that is silent for 30 minutes, so the bound is ours, to keep the caller's turn short and interruptible.
"""

import contextlib
import json
import os
import re
import threading
import time
import uuid
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cotaforge import paths
from cotaforge.language import say

from cotaforge_mcp.design_memory import workspace_language
from cotaforge_mcp.drawing_window import (
    AGENT_RE,
    Host,
    close_window,
    open_window,
    read_record,
    record_path,
    transcript,
    typed,
)
from cotaforge_mcp.reply import Reply, ToolError, ok
from cotaforge_mcp.schemas import ChannelArgs

WAIT_DEFAULT_S = 120.0
WAIT_MAX_S = 600.0
POLL_S = 0.5
LOCK_WAIT_S = 10.0
LOCK_STALE_S = 30.0
WAIT_IDLE_S = 45.0
"""How long the close after a ``done`` waits for the drawing session to be idle before it leaves the window to the engineer."""
"""A lock older than this is a dead process's: the section it guards takes milliseconds."""
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$")
USAGE = (
    'channel(action="ask|reply|dispute|decide|wait|done|list|open_drawing", role="drafter|designer", name, '
    "text?, proposal?, q?, by?, objection?, agent?, since?, timeout_s?)"
)
ASK_EXAMPLE = 'channel(action="ask", role="drafter", name="bracket", text="Does this hole take a fit?", proposal="H7")'
STATUS_WORDS = {"open": "abierta", "answered": "respondida", "disputed": "en disputa", "decided": "decidida"}
KIND_STATUS = {"reply": "answered", "dispute": "disputed", "decide": "decided"}


def drawings_dir(workspace: Path) -> Path:
    return paths.resolve(workspace, paths.DRAWINGS)


def _paths(workspace: Path, name: str) -> tuple[Path, Path, Path]:
    """The log, its readable copy and its lock; all three keep the Spanish names when the log has them."""
    folder = drawings_dir(workspace)
    log = paths.resolve(folder, f"{name}{paths.QUESTIONS_STEM}.jsonl")
    stem = log.name.removesuffix(".jsonl")
    return log, folder / f"{stem}.md", folder / f"{stem}.lock"


def _checked_name(name: str | None) -> str:
    if not name or not NAME_RE.match(name):
        raise ToolError(
            "name is the drawing's name as in drawings/<name>-intent.md (letters, digits, . _ -)",
            code="invalid_input",
            field_name="name",
            usage=USAGE,
            example=ASK_EXAMPLE,
        )
    return name


@contextlib.contextmanager
def _locked(lock: Path, wait_s: float = LOCK_WAIT_S, stale_s: float = LOCK_STALE_S) -> Iterator[None]:
    """One writer at a time across processes: the lock is a file made with ``O_EXCL``. A lock whose file is older than
    ``stale_s`` belongs to a process that died inside the section: it is taken over."""
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + wait_s
    while True:
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            try:
                if time.time() - lock.stat().st_mtime > stale_s:
                    lock.unlink()
                    continue
            except FileNotFoundError:
                continue  # its holder just let go
            if time.monotonic() > deadline:
                raise ToolError(
                    f"{lock.name} is held by another session for too long; try again", code="busy"
                ) from None
            time.sleep(0.02)
            continue
        with os.fdopen(descriptor, "w") as handle:
            handle.write(str(os.getpid()))
        break
    try:
        yield
    finally:
        with contextlib.suppress(FileNotFoundError):
            lock.unlink()


def read_events(log: Path) -> list[dict[str, Any]]:
    """The events of the log. A line that is not complete JSON (a writer is in the middle of it) is not there yet."""
    try:
        text = log.read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    events = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and isinstance(event.get("n"), int):
            for key in ("via", "by"):  # a log written before 0.10 has the Spanish tokens
                if isinstance(event.get(key), str):
                    event[key] = paths.alias_token(event[key])
            events.append(event)
    return events


def questions(events: list[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Each question with the events about it and its status (open, answered, disputed or decided: the last word)."""
    out: dict[int, dict[str, Any]] = {}
    for event in events:
        if event["kind"] == "ask":
            out[event["q"]] = {"ask": event, "events": [], "status": "open"}
        elif event["kind"] in KIND_STATUS and event.get("q") in out:
            out[event["q"]]["events"].append(event)
            out[event["q"]]["status"] = KIND_STATUS[event["kind"]]
    return out


def is_done(events: list[dict[str, Any]]) -> bool:
    """Whether the last word is the drafter's ``done`` (a new ``ask`` after it opens the channel again)."""
    return bool(events) and events[-1]["kind"] == "done"


def _unsettled(events: list[dict[str, Any]]) -> list[int]:
    return [q for q, item in questions(events).items() if item["status"] in ("open", "disputed")]


def record_closed(workspace: Path, name: str) -> dict[str, Any]:
    """The drawing session's window ended without ``done``: say so in the channel (the designer hears it)."""
    fields = {"kind": "closed", "via": "drafter", "by": "drafter", "text": "drawing session closed before done"}
    return _append(workspace, name, fields, lambda events: None)


WHO_ES = {"drafter": "dibujante", "designer": "diseñador", "engineer": "ingeniero"}


def _who(token: str, language: str) -> str:
    """The name of a role as the engineer reads it, in the project's language."""
    return WHO_ES.get(token, token) if language == "es" else token


STATUS_WORDS_EN = {"open": "open", "answered": "answered", "disputed": "disputed", "decided": "decided"}


def _line(event: dict[str, Any], language: str = "es") -> str:
    """One event as the sessions (and the engineer, who reads what they quote) see it, in the project's language."""

    def s(es: str, en: str) -> str:
        return say(language, es, en)

    stamp = str(event.get("at", ""))[11:16]
    who = _who(event["by"], language)
    via = (
        ""
        if event["via"] == event["by"]
        else s(
            f", escrita desde la sesión del {_who(event['via'], language)}",
            f", written from the {event['via']} session",
        )
    )
    kind, n = event["kind"], event["n"]
    if kind == "ask":
        head = s(
            f"[n={n}] PREGUNTA {event['q']} del {who} ({stamp}): {event['text']}",
            f"[n={n}] QUESTION {event['q']} from the {who} ({stamp}): {event['text']}",
        )
        proposal = s("propuesta del dibujante", "drafter's proposal")
        return head + (f"\n    {proposal}: {event['proposal']}" if event.get("proposal") else "")
    if kind == "reply":
        return s(
            f"[n={n}] RESPUESTA a {event['q']} del {who} ({stamp}{via}): {event['text']}",
            f"[n={n}] REPLY to {event['q']} from the {who} ({stamp}{via}): {event['text']}",
        )
    if kind == "dispute":
        return s(
            f"[n={n}] OBJECIÓN a {event['q']} del {who} ({stamp}{via}), la pregunta queda en disputa: {event['text']}",
            f"[n={n}] OBJECTION to {event['q']} from the {who} ({stamp}{via}), the question is held in dispute: {event['text']}",
        )
    if kind == "decide":
        objection = (
            s(" Objeción registrada: ", " Objection on record: ") + str(event["objection"])
            if event.get("objection")
            else ""
        )
        return s(
            f"[n={n}] DECISIÓN sobre {event['q']} del {who} ({stamp}{via}): {event['text']}.{objection}",
            f"[n={n}] DECISION on {event['q']} from the {who} ({stamp}{via}): {event['text']}.{objection}",
        )
    if kind == "closed":
        return s(
            f"[n={n}] VENTANA CERRADA antes de terminar ({stamp}): la sesión de dibujo se cerró sin decir done",
            f"[n={n}] WINDOW CLOSED before finishing ({stamp}): the drawing session closed without saying done",
        )
    return s(f"[n={n}] TERMINADO por el {who} ({stamp})", f"[n={n}] FINISHED by the {who} ({stamp})") + (
        f": {event['text']}" if event.get("text") else ""
    )


def render_markdown(name: str, events: list[dict[str, Any]], language: str = "es") -> str:
    """The readable copy of the log (the engineer opens it), in the project's language."""

    def s(es: str, en: str) -> str:
        return say(language, es, en)

    words = STATUS_WORDS if language == "es" else STATUS_WORDS_EN
    lines = [s(f"# Preguntas del dibujante: {name}", f"# Drafter's questions: {name}"), ""]
    if not events:
        lines.append(s("Sin preguntas todavía.", "No questions yet."))
    for q, item in questions(events).items():
        ask = item["ask"]
        lines.append(f"## {q}. {ask['text']}  [{words[item['status']]}]")
        if ask.get("proposal"):
            lines.append(f"- {s('Propuesta del dibujante', 'Drafter' + chr(39) + 's proposal')}: {ask['proposal']}")
        for event in item["events"]:
            label = {
                "reply": s("Respuesta", "Reply"),
                "dispute": s("Objeción", "Objection"),
                "decide": s("Decisión", "Decision"),
            }[event["kind"]]
            via = (
                ""
                if event["via"] == event["by"]
                else s(
                    f", dada en la sesión del {_who(event['via'], language)}",
                    f", given in the {event['via']} session",
                )
            )
            when = str(event["at"])[:16].replace("T", " ")
            lines.append(
                s(
                    f"- {label} del {_who(event['by'], language)} ({when}{via}): {event['text']}",
                    f"- {label} from the {event['by']} ({when}{via}): {event['text']}",
                )
            )
            if event.get("objection"):
                lines.append(f"  - {s('Objeción registrada', 'Objection on record')}: {event['objection']}")
        lines.append("")
    if is_done(events):
        stamp = str(events[-1]["at"])[:16].replace("T", " ")
        lines.append(s(f"**Dibujo terminado** ({stamp}).", f"**Drawing finished** ({stamp})."))
    return "\n".join(lines).rstrip() + "\n"


def _append(
    workspace: Path, name: str, fields: dict[str, Any], check: Callable[[list[dict[str, Any]]], None]
) -> dict[str, Any]:
    """Add one event under the lock: ``check`` sees the log as it is now and may refuse; ``n`` and (for an ask) ``q`` are
    given here, so two sessions never take the same number."""
    log, readable, lock = _paths(workspace, name)
    with _locked(lock):
        events = read_events(log)
        check(events)
        event = {"n": len(events) + 1, "at": datetime.now(UTC).isoformat(timespec="seconds"), **fields}
        if event["kind"] == "ask":
            event["q"] = len(questions(events)) + 1
        events.append(event)
        with log.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
        readable.write_text(render_markdown(name, events, workspace_language(workspace)), encoding="utf-8")
    return event


def _agent_name(agent: str) -> str:
    """``cotaforge:dibujante`` (the name before 0.10) is ``cotaforge:drafter``."""
    head, sep, tail = agent.rpartition(":")
    return head + sep + paths.alias_token(tail)


def _role(args: ChannelArgs, allowed: tuple[str, ...]) -> str:
    if args.role is None:
        raise ToolError(
            "role says which session you are: drafter (the drawing session) or designer (the design session)",
            code="invalid_input",
            field_name="role",
            usage=USAGE,
            example=ASK_EXAMPLE,
        )
    if args.role not in allowed:
        raise ToolError(
            f"{args.action} is for the {' / '.join(allowed)} session",
            code="not_allowed",
            field_name="role",
            usage=USAGE,
        )
    return args.role


def _text(args: ChannelArgs) -> str:
    text = (args.text or "").strip()
    if not text:
        raise ToolError(
            f"{args.action} needs text", code="invalid_input", field_name="text", usage=USAGE, example=ASK_EXAMPLE
        )
    return text


def _about(args: ChannelArgs, name: str) -> Callable[[list[dict[str, Any]]], None]:
    """The check of an event about question ``args.q``: it exists, the channel is open and the question not decided."""

    def check(events: list[dict[str, Any]]) -> None:
        asked = questions(events)
        if args.q not in asked:
            raise ToolError(f"there is no question {args.q} in {name}", code="not_found", field_name="q")
        if is_done(events):
            raise ToolError(f"{name} is closed (the drafter said done); only a new ask opens it again", code="closed")
        if args.action != "decide" and asked[args.q]["status"] == "decided":
            raise ToolError(f"question {args.q} is already decided", code="decided", field_name="q")
        if args.action == "decide":
            if asked[args.q]["status"] == "decided":
                raise ToolError(f"question {args.q} is already decided", code="decided", field_name="q")
            if asked[args.q]["status"] == "disputed" and not (args.objection or "").strip():
                raise ToolError(
                    f"question {args.q} was disputed: record the decision with the objection (objection=...) next to it",
                    code="invalid_input",
                    field_name="objection",
                    usage=USAGE,
                )

    return check


def _ask(workspace: Path, args: ChannelArgs, name: str) -> Reply:
    role = _role(args, ("drafter",))
    fields = {"kind": "ask", "via": role, "by": role, "text": _text(args), "proposal": (args.proposal or "").strip()}
    event = _append(workspace, name, fields, lambda events: None)
    return ok(
        f"question {event['q']} recorded (n={event['n']}). Keep drawing what does not depend on it, then "
        f'channel(action="wait", role="drafter", name="{name}", since={event["n"]}) for the reply; if the engineer '
        f'answers in this session first, record his words with channel(action="reply", by="engineer", q={event["q"]}).'
    )


def _reply(workspace: Path, args: ChannelArgs, name: str) -> Reply:
    role = _role(args, ("drafter", "designer"))
    text = _text(args)
    if args.q is None or args.by is None:
        raise ToolError(
            "reply needs q (the question's number) and by", code="invalid_input", field_name="q", usage=USAGE
        )
    if role == "drafter" and args.by != "engineer":
        raise ToolError(
            "the drafter never answers its own question: a reply here is the engineer's words (by=engineer); the "
            "designer answers from its own session",
            code="not_allowed",
            field_name="by",
        )
    if role == "designer" and args.by != "designer":
        raise ToolError(
            "from the design session the reply is the designer's (by=designer); what the engineer answers is "
            "recorded from the drawing session, where he is the human",
            code="not_allowed",
            field_name="by",
        )
    fields = {"kind": "reply", "via": role, "by": args.by, "q": args.q, "text": text}
    event = _append(workspace, name, fields, _about(args, name))
    return ok(f"reply to {event['q']} recorded (n={event['n']}); the question is answered, not decided.")


def _dispute(workspace: Path, args: ChannelArgs, name: str) -> Reply:
    """An expert objects to an answer: the designer to what the engineer told the drafter, the drafter to an answer that
    is bad drawing practice. The question is held until the engineer confirms or insists (``decide``)."""
    role = _role(args, ("drafter", "designer"))
    if args.q is None:
        raise ToolError("dispute needs q (the question's number)", code="invalid_input", field_name="q", usage=USAGE)
    fields = {"kind": "dispute", "via": role, "by": role, "q": args.q, "text": _text(args)}
    event = _append(workspace, name, fields, _about(args, name))
    return ok(f"objection to {event['q']} recorded (n={event['n']}); the question is held until the engineer decides.")


def _decide(workspace: Path, args: ChannelArgs, name: str) -> Reply:
    """The engineer's decision, written by the session he said it in. If the question was disputed the objection stays
    next to it, so the record shows that he was told and chose."""
    role = _role(args, ("drafter", "designer"))
    if args.q is None or args.by not in (None, "engineer"):
        raise ToolError(
            "decide needs q and is always the engineer's decision (by=engineer)", code="invalid_input", field_name="by"
        )
    fields = {
        "kind": "decide",
        "via": role,
        "by": "engineer",
        "q": args.q,
        "text": _text(args),
        "objection": (args.objection or "").strip(),
    }
    event = _append(workspace, name, fields, _about(args, name))
    return ok(f"decision on {event['q']} recorded (n={event['n']}).")


def _done(workspace: Path, args: ChannelArgs, name: str) -> Reply:
    role = _role(args, ("drafter",))
    fields = {"kind": "done", "via": role, "by": role, "text": (args.text or "").strip()}
    event = _append(workspace, name, fields, lambda events: None)
    pending = _unsettled(read_events(_paths(workspace, name)[0]))
    note = (
        f" Still open or disputed: question(s) {', '.join(map(str, pending))}; say so in your final message."
        if pending
        else ""
    )
    return ok(f"done recorded (n={event['n']}); the designer stops waiting.{note}")


def _list(workspace: Path, name: str) -> Reply:
    events = read_events(_paths(workspace, name)[0])
    if not events:
        return ok(f"{name}: no events yet")
    asked = questions(events)
    counts = ", ".join(f"{sum(1 for i in asked.values() if i['status'] == s)} {s}" for s in STATUS_WORDS)
    state = "closed (done)" if is_done(events) else "open"
    return ok(
        "\n".join(
            [
                f"{name}: {state}; questions: {counts}",
                *(_line(event, workspace_language(workspace)) for event in events),
            ]
        ),
        limit=6000,
    )


def _wait(
    workspace: Path,
    args: ChannelArgs,
    name: str,
    cancelled: threading.Event | None,
    clock: Callable[[], float],
    sleep: Callable[[float], None],
    host: Host,
) -> Reply:
    role = _role(args, ("drafter", "designer"))
    limit = min(max(args.timeout_s if args.timeout_s is not None else WAIT_DEFAULT_S, 1.0), WAIT_MAX_S)
    log = _paths(workspace, name)[0]
    started = clock()
    while True:
        events = read_events(log)
        news = [event for event in events if event["n"] > args.since and event["via"] != role]
        if news:
            text = _news(role, name, events, news, workspace_language(workspace))
            if role == "designer" and is_done(events) and events[-1] in news:
                text += "\n" + _window_note(workspace, name, events, host)
            return ok(text, limit=6000)
        waited = clock() - started
        if waited >= limit or (cancelled is not None and cancelled.is_set()):
            return ok(
                f"nothing yet ({waited:.0f} s). Call again: "
                f'channel(action="wait", role="{role}", name="{name}", since={args.since})'
            )
        sleep(min(POLL_S, max(limit - waited, 0.01)))


def _window_note(workspace: Path, name: str, events: list[dict[str, Any]], host: Host) -> str:
    """The drawing is done: close the window this harness opened for it, unless a question is still open or disputed or
    somebody is mid-turn there. The designer tells the engineer what happened."""
    if _unsettled(events):
        return (
            f"-> window: NOT closed, question(s) {', '.join(map(str, _unsettled(events)))} are still open or disputed. "
            'Settle them with the engineer, then channel(action="close").'
        )
    closed, text = close_window(host, workspace, name, done=True, confirmed=False, wait_idle_s=WAIT_IDLE_S)
    return f"-> window: {text}" + ("" if closed else " Tell the engineer.")


def _news(role: str, name: str, events: list[dict[str, Any]], news: list[dict[str, Any]], language: str = "es") -> str:
    last = max(event["n"] for event in news)
    lines = [_line(event, language) for event in news]
    kinds = {(event["kind"], event["by"]) for event in news}
    if role == "designer":
        if ("ask", "drafter") in kinds:
            lines.append(
                '-> answer each question you can: channel(action="reply", role="designer", q=<number>, by="designer", '
                "text=...); ask the engineer in this session only what you do not know."
            )
        if ("reply", "engineer") in kinds:
            lines.append(
                "-> the engineer answered this himself in the drawing session. It is not a decision yet: check it against "
                "what you know (intent, calculations, assumptions-and-decisions.md, standards, the model). If you agree, "
                'record it as his decision (assumptions-and-decisions.md and channel(action="decide", q=<number>, '
                'by="engineer", text=...)). If you do not, tell him here why, with your recommendation and a source if '
                'there is one, and send channel(action="dispute", role="designer", q=<number>, text=...) so the '
                "drafter holds the point; when he confirms or insists, decide with objection=<yours> next to it."
            )
        if ("closed", "drafter") in kinds:
            lines.append(
                "-> the drawing session closed before done (the engineer may have closed the window by mistake). Tell "
                'him and offer to carry on: channel(action="reopen", role="designer", name=<name>, agent=<agent>) '
                "continues the same conversation. Nothing is lost: questions and answers are in the channel."
            )
        if ("done", "drafter") in kinds and events[-1] in news:
            lines.append("-> the drafter is done: stop waiting.")
            return "\n".join(lines)
    else:
        if ("reply", "designer") in kinds:
            lines.append(
                "-> the designer's answer. If the engineer says otherwise in this session, his word wins: record it with "
                'channel(action="reply", by="engineer", ...). If an answer is bad drawing practice or clashes with the '
                "sheet or a standard, say so before drawing it (dispute); if he insists, draw it and decide with "
                "objection=<yours>."
            )
        if ("dispute", "designer") in kinds:
            lines.append("-> the designer disputes the engineer's answer: hold that point until a decision arrives.")
        if ("decide", "engineer") in kinds:
            lines.append("-> decided: draw it as decided.")
    lines.append(f'next: channel(action="wait", role="{role}", name="{name}", since={last})')
    return "\n".join(lines)


def _open_drawing(workspace: Path, args: ChannelArgs, name: str, host: Host) -> Reply:
    """Open the drawing session for ``name`` in a window of its own. The window is recorded (``drawings/<name>-window.json``)
    so that the harness, and only the harness, can close it when the drawing is done."""
    _role(args, ("designer",))
    if not args.agent or not AGENT_RE.match(args.agent):
        raise ToolError(
            "agent is the exact name of the plugin's drawing agent, e.g. cotaforge:drafter "
            "(the design folder's CLAUDE.md gives it)",
            code="invalid_input",
            field_name="agent",
            usage=USAGE,
        )
    fallback = typed(host, workspace, args.agent, name)
    if not host.platform.startswith("win"):
        return ok(f"this machine opens no window from here. Tell the engineer to run, in a second terminal: {fallback}")
    session_id = str(uuid.uuid4())
    try:
        line = open_window(host, workspace, name, args.agent, session_id)
    except RuntimeError as exc:
        raise ToolError(f"{exc}; close it first or use it", code="already_open") from exc
    except OSError as exc:
        raise ToolError(
            f"could not open the window ({exc}). Tell the engineer to run: {fallback}", code="not_opened"
        ) from exc
    return ok(
        f"drawing session for {name} opened in a new window ({line[0]}). Tell the engineer, and give him this command as "
        f"a fallback: {fallback}. Do not open it again. Wait on the channel: "
        f'channel(action="wait", role="designer", name="{name}", since=0). When the drawing is done the window is closed '
        f"for you (or when you call close); if this session ends it closes too. To pick that session up later: "
        f"`claude --resume {session_id}` in this folder."
    )


def _reopen(workspace: Path, args: ChannelArgs, name: str, host: Host) -> Reply:
    """Open the drawing session again after its window was closed: the SAME conversation (``--resume``) when its
    transcript exists, else a new session that reads the channel and the sheet and says what it found."""
    _role(args, ("designer",))
    if not args.agent or not AGENT_RE.match(args.agent):
        raise ToolError(
            "agent is the exact name of the plugin's drawing agent, e.g. cotaforge:drafter",
            code="invalid_input",
            field_name="agent",
            usage=USAGE,
        )
    previous = (read_record(record_path(workspace, name)) or {}).get("session_id")
    resume = str(previous) if previous and transcript(host, workspace, str(previous)).is_file() else None
    fallback = typed(host, workspace, args.agent, name, resume)
    if not host.platform.startswith("win"):
        return ok(f"this machine opens no window from here. Tell the engineer to run, in a second terminal: {fallback}")
    session_id = str(uuid.uuid4())
    try:
        line = open_window(host, workspace, name, args.agent, session_id, resume)
    except RuntimeError as exc:
        raise ToolError(f"{exc}; it is open already", code="already_open") from exc
    except OSError as exc:
        raise ToolError(
            f"could not open the window ({exc}). Tell the engineer to run: {fallback}", code="not_opened"
        ) from exc
    how = "the same conversation (--resume)" if resume else "a new session that reads the channel and the sheet"
    return ok(
        f"drawing session for {name} opened again in a new window ({line[0]}): {how}. Tell the engineer; fallback "
        f"command: {fallback}. Wait on the channel: "
        f'channel(action="wait", role="designer", name="{name}", since=<the last n you saw>).'
    )


def _close(workspace: Path, args: ChannelArgs, name: str, host: Host) -> Reply:
    """The designer closes the drawing window at the engineer's word (``confirmed``), or after the drawing is done."""
    _role(args, ("designer",))
    events = read_events(_paths(workspace, name)[0])
    done = is_done(events) and not _unsettled(events)
    closed, text = close_window(host, workspace, name, done=done, confirmed=args.confirmed, wait_idle_s=WAIT_IDLE_S)
    return ok(text) if closed else ok(f"NOT closed. {text}")


def run(
    workspace: Path,
    args: ChannelArgs,
    cancelled: threading.Event | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    host: Host | None = None,
) -> Reply:
    """Run one ``channel`` call on the design folder ``workspace``."""
    args = args.model_copy(
        update={
            "role": paths.alias_token(args.role) if args.role else args.role,
            "by": paths.alias_token(args.by) if args.by else args.by,
            "agent": _agent_name(args.agent) if args.agent else args.agent,
        }
    )
    if args.action == "list" and args.name is None:
        folder = drawings_dir(workspace)
        found = sorted(
            {
                p.name.removesuffix(".jsonl").removesuffix("-questions").removesuffix("-preguntas")
                for p in [*folder.glob("*-questions.jsonl"), *folder.glob("*-preguntas.jsonl")]
            }
        )
        return ok("drawings with a channel: " + (", ".join(found) if folder.is_dir() and found else "none"))
    name = _checked_name(args.name)
    if args.action == "ask":
        return _ask(workspace, args, name)
    if args.action == "reply":
        return _reply(workspace, args, name)
    if args.action == "dispute":
        return _dispute(workspace, args, name)
    if args.action == "decide":
        return _decide(workspace, args, name)
    if args.action == "done":
        return _done(workspace, args, name)
    if args.action == "open_drawing":
        return _open_drawing(workspace, args, name, host or Host())
    if args.action == "reopen":
        return _reopen(workspace, args, name, host or Host())
    if args.action == "close":
        return _close(workspace, args, name, host or Host())
    if args.action == "wait":
        return _wait(workspace, args, name, cancelled, clock, sleep, host or Host())
    return _list(workspace, name)
