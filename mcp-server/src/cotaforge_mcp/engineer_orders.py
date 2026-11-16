"""The engineer's orders: one is recorded only when he confirmed that very call in Claude Code's own permission dialog.

What the harness measured the agent cannot dismiss on its own word; only the engineer's order can. An order the agent
types into a tool argument proves nothing, so the person is asked, by Claude Code and not by the agent:

* ``order_of`` says which tool calls carry an engineer's order (an approval, the dismissal of something measured, his
  review report, who reviews, the project's sector...). The hook and the server both use it, so they cannot disagree.
* Claude Code runs a ``PreToolUse`` hook (``python -m cotaforge_mcp.engineer_orders``, written into the design space by
  ``cotaforge init``) before every ``drawing``, ``memory`` and ``channel`` call. A call without an order passes
  untouched (the hook prints nothing). For one with an order the hook answers ``ask``: Claude Code shows the engineer
  its permission dialog with the exact phrase and what it closes, in the project language, and only he can answer it.
  Measured on Claude Code 2.1.325: the dialog appears in the ``default``, ``acceptEdits`` and ``auto`` modes even
  though the design space allows every ``mcp__cad`` tool; rejected, the call never reaches the server; with nobody to
  answer (``claude -p``) Claude Code denies the call.
* The signal the server checks is a ticket for that one call: when it asks, the hook writes
  ``<home>/orders/tickets/<tool_use_id>.json`` with a digest of the tool and its input. Claude Code gives the same id to
  the server with the call (``_meta["claudecode/toolUseId"]``), and the server accepts the order only if that ticket
  exists and its digest is the one of the arguments it received; the ticket is then deleted (``admit``). The agent
  chooses neither the id of its call nor the ``_meta`` of the request, so no argument it types makes a ticket.
* Never a turn waiting on a dialog nobody answers: the hook asks only while the engineer is at the screen, which is
  when the last message he typed in the session (the transcript's last ``turnOrigin: human``) is at most
  ``order_presence_min`` minutes old (``brief.yaml``; 2 by default). Otherwise it denies at once, the order is not
  recorded, and the point is kept in ``<home>/orders/pending/`` until his next message, when the ``UserPromptSubmit``
  side of this same hook hands the list to the agent to ask him in one short message.
  Claude Code gives a permission dialog no timeout: if he types and leaves at that very moment, the dialog waits for
  him, like any of its own. That is why the window is short.
* Everything he confirms is shown to him, whole: what the call carries and the digest covers is what the dialog says.
  An order too long for the dialog (``MAX_DIALOG`` characters) is refused with the reason, so the agent splits it; a
  review report is split with ``append`` (``too_long``): the first piece replaces the report, each next one adds to it.
* A permission mode in which no person answers a dialog (``bypassPermissions``, ``dontAsk``, anything unknown) and
  ``plan`` are denied at once as well, with the reason.

Where the files live: the harness's data folder (``COTAFORGE_HOME``, else ``~/.cotaforge``), outside the design space on
purpose: there the agent edits files without asking, here every write of its own needs the engineer's permission.

What this cannot do: a file is not a signature. An agent the engineer lets run shell commands unattended, in a design
space whose hook it first removed, could watch for its own call id and write the ticket itself; that is a forgery it
has to commit, no longer a sentence it types into a tool argument.

The hook runs before every call of three tools, so the command that starts it first looks at the call's JSON in the
shell (``ORDER_MARKERS``) and starts Python only for a call that may carry an order; and this module loads next to
nothing: what only an order needs (paths, digests, the brief, the transcript) is imported where it is used.
"""

from __future__ import annotations

import contextvars
import json
import os
import sys
import time

TYPE_CHECKING = False
if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path
    from typing import Any, BinaryIO, TextIO

    from cotaforge_mcp.config import Settings

ENV_HOME = "COTAFORGE_HOME"
ENV_PROJECT = "CLAUDE_PROJECT_DIR"
HOME_DIRNAME = ".cotaforge"
ORDERS_DIRNAME = "orders"
SERVER_NAME = "cad"
MCP_JSON = ".mcp.json"
BRIEF_FILE = "brief.yaml"
TOOL_PREFIX = f"mcp__{SERVER_NAME}__"
ORDER_TOOLS = ("drawing", "memory", "channel")
MATCHER = f"{TOOL_PREFIX}({'|'.join(ORDER_TOOLS)})"
"""The ``PreToolUse`` matcher of the hook: the three tools that can carry an order."""
ORDER_MARKERS = ('"by"', '"review_choice"', '"decide"', '"sector"', '"processes"', '"drawing_review"')
"""What a call's JSON must contain, as a token, to carry an order: the keys and values ``order_of`` reads. A call with
none of them carries no order, and the command ``cotaforge init`` writes (``prefilter`` in the CLI) does not even start
Python for it. A test checks that every kind of order contains one, so this list cannot fall behind ``order_of``."""
PRE_TOOL_EVENT = "PreToolUse"
PROMPT_EVENT = "UserPromptSubmit"
META_CALL_ID = "claudecode/toolUseId"
"""The key of a tool call's ``_meta`` in which Claude Code gives the server the id of the call (measured, 2.1.325)."""

ASKING_MODES = frozenset({"default", "acceptEdits", "auto"})
"""The permission modes in which Claude Code shows a hook's ``ask`` to the person: ``default``, ``acceptEdits`` and
``auto``, each measured on Claude Code 2.1.325."""
PRESENCE_KEY = "order_presence_min"
PRESENCE_MIN_DEFAULT = 2.0
"""Minutes since the engineer's last typed message within which he counts as being at the screen."""
PRESENCE_MIN_MAX = 10.0
"""The most the brief may ask for: the agent can edit ``brief.yaml``, and a window of hours would open a dialog with
nobody there (the session would wait for it, which is what the window exists to prevent)."""
KEEP_DAYS = 7
"""How long a ticket, or an order that waits for the engineer, is kept."""
CALL_ID = r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}"
HUMAN_TURN = "human"
TASK_NOTIFICATION = "<task-notification>"
DEFAULT_REVIEW_TOKENS = ("", "engineer_or_fabricator", "ingeniero_o_fabricante")
ENGINEER_TOKENS = ("engineer", "ingeniero")
MAX_DIALOG = 1500
"""The most characters the dialog shows; a longer order is refused, never cut (he must see all he confirms)."""

CODE = "order_not_confirmed"


# --- which calls carry an order -------------------------------------------------------------------------------------------


class Order:
    """An engineer's order carried by one tool call: its ``kind``, his phrase as the call carries it (``words``; may
    be empty: a choice says it all) and what it closes or decides, for the dialog he reads."""

    __slots__ = ("closes_en", "closes_es", "kind", "words")

    def __init__(self, kind: str, words: str, closes_es: str, closes_en: str) -> None:
        self.kind, self.words, self.closes_es, self.closes_en = kind, words, closes_es, closes_en

    def closes(self, language: str) -> str:
        return self.closes_en if language == "en" else self.closes_es


def _text(value: object) -> str:
    return " ".join(value.split()) if isinstance(value, str) else ""


def _short(value: object) -> str:
    """A value as the dialog shows it: all of it, on one line."""
    return _text(value) if isinstance(value, str) else _text(json.dumps(value, ensure_ascii=False, default=str))


def _remark(item: object) -> str:
    """One remark of his report, whole: its kind, its text and anything else it carries."""
    if not isinstance(item, dict):
        return _short(item)
    rest = {k: v for k, v in item.items() if k not in ("kind", "text")}
    head = ": ".join(part for part in (_short(item.get("kind") or ""), _short(item.get("text") or "")) if part)
    return head + (f" ({_short(rest)})" if rest else "")


def _is_engineer(value: object) -> bool:
    return _text(value).lower() in ENGINEER_TOKENS


def _sheet(args: Mapping[str, Any]) -> tuple[str, str]:
    sheet = _text(args.get("sheet"))
    return (f" en la hoja {sheet}", f" on sheet {sheet}") if sheet else ("", "")


def _drawing(args: Mapping[str, Any]) -> Order | None:
    action, by, reason = _text(args.get("action")), _text(args.get("by")), _text(args.get("reason"))
    on_es, on_en = _sheet(args)
    if action == "approve":
        if not (_text(args.get("sheet")) and by and reason):
            return None
        return Order(
            "approve_sheet", reason, f"la aprobación{on_es} (Aprobó: {by})", f"the approval{on_en} (approved by: {by})"
        )
    if action == "review_choice":
        who_es = "tú mismo" if _is_engineer(by) else "el agente fabricante"
        who_en = "yourself" if _is_engineer(by) else "the manufacturer agent"
        return Order("review_choice", reason, f"quién revisa el plano{on_es}: {who_es}", f"who reviews the drawing{on_en}: {who_en}")
    if action == "review_report":
        if not _is_engineer(by):
            return None  # the manufacturer agent's own report
        items = args.get("items") if isinstance(args.get("items"), list) else []
        points = "; ".join(_remark(item) for item in items)
        if args.get("append") is True:  # one more piece of a report recorded in pieces: it adds, it does not replace
            listed_es = f"{len(items)} observaciones más: {points}" if items else "sin observaciones más"
            listed_en = f"{len(items)} more remarks: {points}" if items else "no more remarks"
            return Order(
                "review_report",
                reason,
                f"tu informe de revisión{on_es}: se AÑADE a lo ya registrado, {listed_es}",
                f"your review report{on_en}: ADDED to what is already recorded, {listed_en}",
            )
        listed_es = f"{len(items)} observaciones: {points}" if items else "sin observaciones"
        listed_en = f"{len(items)} remarks: {points}" if items else "no remarks"
        return Order("review_report", reason, f"tu informe de revisión{on_es}, {listed_es}", f"your review report{on_en}, {listed_en}")
    if action == "compose" and (args.get("recompose") or by):
        if not (by and reason):
            return None
        decisions = args.get("decisions") if isinstance(args.get("decisions"), dict) else {}
        answers = decisions.get("answers") if isinstance(decisions.get("answers"), dict) else {}
        parts_es = ["volver a componer la hoja entera"] if args.get("recompose") else []
        parts_en = ["composing the whole sheet again"] if args.get("recompose") else []
        if answers:
            parts_es.append(f"las respuestas {_short(answers)}")
            parts_en.append(f"the answers {_short(answers)}")
        return Order(
            "compose",
            reason,
            (" y ".join(parts_es) or "la composición de la hoja") + on_es,
            (" and ".join(parts_en) or "the composition of the sheet") + on_en,
        )
    if not (by and reason):
        return None
    if action == "answer":
        point, said = _text(args.get("id")), _short(args.get("text") or "")
        return Order("answer", reason, f"{point}{on_es} («{said}»)", f"{point}{on_en} («{said}»)")
    if action == "remove":
        items = _short(args.get("items") or "")
        return Order("remove", reason, f"quitar {items}{on_es}", f"removing {items}{on_en}")
    return None


def _memory(args: Mapping[str, Any]) -> Order | None:
    action, by, text = _text(args.get("action")), _text(args.get("by")), _text(args.get("text"))
    entry = _text(args.get("id"))
    if action == "approve":
        if not (entry and by and text):
            return None
        scope = _text(args.get("scope"))
        where_es = {"user": " para todos tus diseños", "project": " solo para este proyecto"}.get(scope, "")
        where_en = {"user": " for all your designs", "project": " for this project only"}.get(scope, "")
        return Order(
            "approve_entry",
            text,
            f"la aprobación de la entrada de memoria {entry}{where_es}",
            f"the approval of memory entry {entry}{where_en}",
        )
    if action == "brief":
        said_es, said_en = [], []
        if args.get("sector") is not None:
            said_es.append(f"sector: {_short(args.get('sector'))}")
            said_en.append(f"sector: {_short(args.get('sector'))}")
        if args.get("processes") is not None:
            said_es.append(f"procesos: {_short(args.get('processes'))}")
            said_en.append(f"processes: {_short(args.get('processes'))}")
        review = _text(args.get("drawing_review")).lower().partition(":")[0]
        if review not in DEFAULT_REVIEW_TOKENS and not review.isdigit():
            said_es.append(f"quién revisa los planos: {review}")
            said_en.append(f"who reviews the drawings: {review}")
        if not said_es:
            return None
        return Order("brief", text, "el brief del proyecto, " + "; ".join(said_es), "the project brief, " + "; ".join(said_en))
    if action == "answer" and by and text:
        return Order(
            "keep_alert", text, f"la alerta {entry} (queda respondida por orden tuya)", f"alert {entry} (answered on your order)"
        )
    return None


def _channel(args: Mapping[str, Any]) -> Order | None:
    action, text = _text(args.get("action")), _text(args.get("text"))
    question, name = args.get("q"), _text(args.get("name"))
    of_es = f" de la pregunta {question} del plano {name}"
    of_en = f" to question {question} of drawing {name}"
    if question is None or not text:
        return None
    if action == "decide" and (args.get("by") is None or _is_engineer(args.get("by"))):
        return Order("decide", text, f"tu decisión{of_es}", f"your decision{of_en}")
    if action == "reply" and _is_engineer(args.get("by")):
        return Order("reply", text, f"tu respuesta{of_es}", f"your reply{of_en}")
    return None


def order_of(tool: str, arguments: object) -> Order | None:
    """The engineer's order that the call ``tool(**arguments)`` carries; ``None`` when it carries none. ``tool`` is the
    bare tool name (``drawing``). The one place that says which calls are his: the hook asks him about exactly these,
    and the server admits exactly these only with his confirmation. A call that lacks what its order needs (no
    name, no words) is not put to him: the server refuses it for its shape, and every place that records his word
    refuses a call that was not confirmed (``confirmed``)."""
    if not isinstance(arguments, dict):
        return None
    if tool == "drawing":
        return _drawing(arguments)
    if tool == "memory":
        return _memory(arguments)
    if tool == "channel":
        return _channel(arguments)
    return None


def digest(tool: str, arguments: object) -> str:
    """What binds a ticket to one call: the tool and its input, exactly."""
    import hashlib  # noqa: PLC0415

    body = json.dumps({"tool": tool, "input": arguments}, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


# --- where the files live --------------------------------------------------------------------------------------------


def orders_dir(home: Path | None) -> Path:
    from pathlib import Path  # noqa: PLC0415

    return (home if home is not None else Path.home() / HOME_DIRNAME) / ORDERS_DIRNAME


def ticket_path(home: Path | None, call_id: str | None) -> Path | None:
    """The ticket of one call; ``None`` when the id is not one a file can be named after."""
    import re  # noqa: PLC0415

    if not isinstance(call_id, str) or not re.fullmatch(CALL_ID, call_id):
        return None
    return orders_dir(home) / "tickets" / f"{call_id}.json"


def pending_path(home: Path | None, workspace: str) -> Path:
    """The orders of the design space ``workspace`` that wait for the engineer."""
    import hashlib  # noqa: PLC0415

    try:
        resolved = os.path.realpath(workspace)
    except OSError:
        resolved = workspace
    name = hashlib.sha256(os.path.normcase(resolved).encode("utf-8")).hexdigest()[:24]
    return orders_dir(home) / "pending" / f"{name}.jsonl"


def hook_home(environ: Mapping[str, str], project: str | None) -> Path | None:
    """The data folder the SERVER of this design space uses, seen from the hook: what ``.mcp.json`` gives the server
    wins over the environment, as it does when Claude Code starts it."""
    from pathlib import Path  # noqa: PLC0415

    stated = ""
    if project:
        try:
            data = json.loads((Path(project) / MCP_JSON).read_text(encoding="utf-8"))
            stated = str(data["mcpServers"][SERVER_NAME]["env"].get(ENV_HOME, "") or "")
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            stated = ""
    stated = stated.strip() or environ.get(ENV_HOME, "").strip()
    return Path(stated).expanduser() if stated else None


def _brief(workspace: str | None) -> dict[str, Any]:
    if not workspace:
        return {}
    try:
        from pathlib import Path  # noqa: PLC0415

        import yaml  # noqa: PLC0415 - only a call that carries an order reads the brief

        data = yaml.safe_load((Path(workspace) / BRIEF_FILE).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 - no brief, or one that cannot be read: the defaults
        return {}
    return data if isinstance(data, dict) else {}


def presence_minutes(brief: Mapping[str, Any]) -> float:
    """``order_presence_min`` of the brief: how long after his last message the engineer still counts as present, at
    most ``PRESENCE_MIN_MAX`` (a larger value counts as that); an invalid one (not a number, zero, negative, not finite)
    is the default."""
    value = brief.get(PRESENCE_KEY)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value < float("inf"):
        return PRESENCE_MIN_DEFAULT
    return min(float(value), PRESENCE_MIN_MAX)


def language_of(brief: Mapping[str, Any]) -> str:
    return "en" if _text(brief.get("language")).lower() in ("en", "english", "inglés", "ingles") else "es"


# --- the transcript: when he last typed -----------------------------------------------------------------------------------


def _harness_prompt(text: str) -> bool:
    try:
        from cotaforge_mcp.drawing_window import is_harness_prompt  # noqa: PLC0415 - only an order needs it

        return is_harness_prompt(text)
    except Exception:  # noqa: BLE001
        return False


def last_typed(transcript: object) -> float | None:
    """When the engineer last typed a message in the session of ``transcript`` (Claude Code's own file): the time of
    its last turn a person started (``turnOrigin: human``). The result of a background task, a prompt a program sent
    and the first message of a drawing window, which the harness writes, are not his. ``None`` when he typed nothing
    or the file cannot be read."""
    if not isinstance(transcript, str) or not transcript:
        return None
    from datetime import datetime  # noqa: PLC0415

    latest: float | None = None
    try:
        with open(transcript, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if '"turnOrigin"' not in line:
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict) or entry.get("type") != "user" or entry.get("turnOrigin") != HUMAN_TURN:
                    continue
                if entry.get("isSidechain"):
                    continue
                message = entry.get("message")
                content = message.get("content") if isinstance(message, dict) else None
                if isinstance(content, str) and _harness_prompt(content):
                    continue
                try:
                    at = datetime.fromisoformat(str(entry.get("timestamp")).replace("Z", "+00:00")).timestamp()
                except ValueError:
                    continue
                latest = at if latest is None else max(latest, at)
    except OSError:
        return None
    return latest


# --- the hook: before a tool call ---------------------------------------------------------------------------------------


def _answer(decision: str, reason: str) -> dict[str, Any]:
    return {
        "hookSpecificOutput": {
            "hookEventName": PRE_TOOL_EVENT,
            "permissionDecision": decision,
            "permissionDecisionReason": reason,
        }
    }


def question(order: Order, language: str) -> str:
    """What the engineer reads in the dialog."""
    if language == "en":
        phrase = f'"{order.words}"' if order.words else "(none)"
        return f"Is this your order? Phrase: {phrase}. Closes: {order.closes(language)}."
    phrase = f'"{order.words}"' if order.words else "(ninguna)"
    return f"¿Es orden tuya? Frase: {phrase}. Cierra: {order.closes(language)}."


def too_long(kind: str, length: int) -> str:
    """The refusal of an order whose dialog would not fit: what to send instead, so that nothing is left unrecorded."""
    start = (
        f"NOT RECORDED: this {kind} order is too long to show him whole ({length} characters; the dialog takes "
        f"{MAX_DIALOG}) and he must see all of what he confirms. "
    )
    if kind == "review_report":
        return (
            start + "A report is recorded in pieces: send the first remarks as this same call (it replaces his "
            "report), then each next group with append=true (it adds to the report, the ids go on); if an earlier "
            "piece of this report is already recorded, this one and the rest go with append=true. Each piece is put to "
            "him on its own: keep it short enough to fit, and leave none of his remarks out."
        )
    return (
        start + "Split it into smaller calls (fewer points or shorter text in each) and repeat them; every call is put "
        "to him on its own."
    )


def _forget_old(folder: Path, now: float, pattern: str = "*.json") -> None:
    try:
        files = list(folder.glob(pattern))
    except OSError:
        return
    for file in files:
        try:
            if file.stat().st_mtime < now - KEEP_DAYS * 86400:
                file.unlink()
        except OSError:
            continue


def _read_pending(path: Path, now: float | None = None) -> list[dict[str, Any]]:
    """The orders waiting in ``path``, without the ones older than ``KEEP_DAYS`` (they expire like a ticket does)."""
    oldest = (time.time() if now is None else now) - KEEP_DAYS * 86400
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    found = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if not isinstance(entry, dict):
            continue
        stamp = entry.get("at")
        if isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and stamp < oldest:
            continue
        found.append(entry)
    return found


def _write_pending(path: Path, entries: list[dict[str, Any]]) -> None:
    if not entries:
        try:
            path.unlink()
        except OSError:
            pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        handle.writelines(json.dumps(entry, ensure_ascii=False) + "\n" for entry in entries)


def pending(home: Path | None, workspace: str) -> list[dict[str, Any]]:
    """The orders of ``workspace`` that were not asked because the engineer was away, oldest first."""
    return _read_pending(pending_path(home, workspace))


def decide(payload: object, environ: Mapping[str, str], now: float | None = None) -> dict[str, Any] | None:
    """The hook's answer for one tool call Claude Code is about to make: ``None`` (the call carries no order: it passes
    untouched), ``ask`` (the engineer is asked, and the ticket of the call is written) or ``deny`` (nobody is there to
    ask: the order waits)."""
    if not isinstance(payload, dict) or payload.get("hook_event_name") != PRE_TOOL_EVENT:
        return None
    name = payload.get("tool_name")
    if not isinstance(name, str) or not name.startswith(TOOL_PREFIX):
        return None
    tool, arguments = name[len(TOOL_PREFIX) :], payload.get("tool_input")
    order = order_of(tool, arguments)
    if order is None:
        return None
    now = time.time() if now is None else now
    project = environ.get(ENV_PROJECT) or payload.get("cwd")
    workspace = project if isinstance(project, str) else ""
    home = hook_home(environ, workspace or None)
    brief = _brief(workspace)
    language = language_of(brief)
    asked = question(order, language)
    if len(asked) > MAX_DIALOG:
        return _answer("deny", too_long(order.kind, len(asked)))
    mode = payload.get("permission_mode")
    if mode not in ASKING_MODES:
        return _answer(
            "deny",
            f"NOT RECORDED: {order.kind} is the engineer's order and he confirms each one himself in Claude Code's "
            f"permission dialog; in permission mode {mode!r} nobody is asked. Tell him, in his language, to change the "
            f"mode (Shift+Tab) and what waits for him: {asked} Go on with every step that does not need it.",
        )
    key = digest(tool, arguments)
    waiting = pending_path(home, workspace)
    others = [entry for entry in _read_pending(waiting, now) if entry.get("key") != key]
    typed = last_typed(payload.get("transcript_path"))
    minutes = presence_minutes(brief)
    if typed is None or now - typed > minutes * 60:
        try:
            _write_pending(waiting, [*others, {"key": key, "at": now, "tool": tool, "kind": order.kind, "question": asked}])
            _forget_old(waiting.parent, now, "*.jsonl")  # a design space nobody came back to
        except OSError:
            pass  # the point stays open on the sheet all the same
        away = "the engineer is away: it waits for him" if language == "en" else "el ingeniero no está: queda pendiente para cuando vuelva"
        return _answer(
            "deny",
            f"{away}. NOT RECORDED: he typed nothing in this session in the last {minutes:g} min, so he was not asked "
            f"({asked}). The point stays open; it is kept and you will be handed the list when he writes again. Do not "
            "repeat this call until then and never close the point another way: go on with every step of the work that "
            "does not need it (only the export or approval that depends on this point waits).",
        )
    ticket = ticket_path(home, payload.get("tool_use_id"))
    if ticket is None:
        return _answer(
            "deny",
            f"NOT RECORDED: Claude Code gave this call no id, so the engineer's confirmation could not be tied to it "
            f"({asked}). Tell him his Claude Code must be up to date for an order of his to be recorded.",
        )
    try:
        ticket.parent.mkdir(parents=True, exist_ok=True)
        _forget_old(ticket.parent, now)
        with ticket.open("w", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps({"at": now, "digest": key, "kind": order.kind}) + "\n")
        _write_pending(waiting, others)  # he is being asked now: it no longer waits
    except OSError as error:
        return _answer("deny", f"NOT RECORDED: the ticket of the order could not be written ({error}); tell the engineer.")
    return _answer("ask", asked)


# --- the hook: when he writes again ------------------------------------------------------------------------------------


def back(payload: object, environ: Mapping[str, str]) -> dict[str, Any] | None:
    """The hook's answer when a message starts a turn: the orders that waited for the engineer are handed to the agent
    (once: the list is emptied), to ask him in one short message. ``None`` when nothing waits or no person wrote."""
    if not isinstance(payload, dict) or payload.get("hook_event_name") != PROMPT_EVENT:
        return None
    prompt = payload.get("prompt")
    if not isinstance(prompt, str) or prompt.lstrip().startswith(TASK_NOTIFICATION):
        return None  # the result of a background task: he did not come back
    project = environ.get(ENV_PROJECT) or payload.get("cwd")
    workspace = project if isinstance(project, str) else ""
    home = hook_home(environ, workspace or None)
    waiting = pending_path(home, workspace)
    entries = _read_pending(waiting)
    if not entries or _harness_prompt(prompt):
        return None
    _write_pending(waiting, [])
    lines = "\n".join(f"{index}. {entry.get('question')}" for index, entry in enumerate(entries, 1))
    context = (
        f"CotaForge: while the engineer was away, {len(entries)} order(s) of his could not be confirmed and were NOT "
        f"recorded:\n{lines}\nHe is back. After attending to his message, list these points to him as questions in ONE "
        "short message, in his language, so he can answer them together. For each one he confirms, repeat the call: "
        "Claude Code will ask him in its permission dialog."
    )
    return {"hookSpecificOutput": {"hookEventName": PROMPT_EVENT, "additionalContext": context}}


def main(
    stdin: BinaryIO | None = None, stdout: TextIO | None = None, environ: Mapping[str, str] | None = None
) -> int:
    """The hook: always exits 0. A payload it cannot read prints nothing, and the call goes on to the server, which
    admits no order without a ticket."""
    try:
        source = stdin if stdin is not None else sys.stdin.buffer  # bytes: Claude Code writes UTF-8 whatever the console is
        payload = json.loads(source.read().decode("utf-8"))
        env = os.environ if environ is None else environ
        answer = back(payload, env) if isinstance(payload, dict) and payload.get("hook_event_name") == PROMPT_EVENT else decide(payload, env)
        if answer is not None:
            text = json.dumps(answer, ensure_ascii=True)  # ASCII on the wire: no console code page can garble it
            (stdout if stdout is not None else sys.stdout).write(text)
    except Exception:  # noqa: BLE001 - a hook must never break the call it sees
        return 0
    return 0


# --- the server -------------------------------------------------------------------------------------------------------

_current: contextvars.ContextVar[Order | None] = contextvars.ContextVar("cotaforge_engineer_order", default=None)
"""The order the engineer confirmed for the tool call that is running (each call runs in a thread and a context of its own)."""


def admit(settings: "Settings", tool: str, arguments: object, call_id: str | None) -> Order | None:
    """The order the call ``tool(**arguments)`` carries, admitted: the engineer confirmed this very call (its ticket
    exists and is of these arguments; it is used up here). ``None`` when the call carries no order. Refuses otherwise:
    nothing was changed."""
    from cotaforge_mcp.reply import ToolError  # noqa: PLC0415 - the hook imports this module and needs no pydantic

    order = order_of(tool, arguments)
    if order is None:
        return None
    start = (
        f"{order.kind} is the engineer's order, and an order is recorded only when he confirms that very call in Claude "
        "Code's permission dialog: nothing was changed. "
    )
    path = ticket_path(settings.home, call_id)
    if path is None:
        raise ToolError(
            start + "This call reached the server with no call id from Claude Code, so no confirmation can belong to "
            "it (another client, or a Claude Code that is not up to date). Tell the engineer",
            code=CODE,
            field_name="by",
        )
    try:
        ticket = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        ticket = None
    if not isinstance(ticket, dict) or ticket.get("digest") != digest(tool, arguments):
        raise ToolError(
            start + "This call did not go through the dialog: this design space lacks the hook that asks him (it was "
            "prepared by an older version). Tell the engineer to prepare the folder again (`cotaforge init <folder>`, "
            "or the CotaForge shortcut that opens a folder) and to start Claude Code again. The decision is not "
            "yours: tell him what is open and what you recommend",
            code=CODE,
            field_name="by",
        )
    try:
        path.unlink()
    except OSError:
        pass
    return order


class confirming:  # noqa: N801 - used as ``with confirming(order):``
    """While a tool call runs: the order the engineer confirmed for it (``None``: it carries none)."""

    def __init__(self, order: Order | None) -> None:
        self.order = order

    def __enter__(self) -> None:
        self.token = _current.set(self.order)

    def __exit__(self, *exc: object) -> None:
        _current.reset(self.token)


def confirmed(what: str) -> Order:
    """Called where the server is about to record something in the engineer's name: the order he confirmed for the
    running call. Refuses when there is none, so a place ``order_of`` does not know can never record one unasked."""
    from cotaforge_mcp.reply import ToolError  # noqa: PLC0415

    order = _current.get()
    if order is None:
        raise ToolError(
            f"{what} is the engineer's order, and an order is recorded only when he confirms that very call in Claude "
            "Code's permission dialog; this call was not put to him: nothing was changed. The decision is not yours: "
            "tell him what is open and what you recommend",
            code=CODE,
            field_name="by",
        )
    return order


if __name__ == "__main__":
    sys.exit(main())
