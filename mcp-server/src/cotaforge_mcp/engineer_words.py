"""The engineer's words: an order of his is accepted only if he typed it, in this session.

What the harness measured the agent cannot dismiss on its own word; only the engineer's order can, with his words
quoted. Quoted words that the agent types itself prove nothing, so the words are checked against what he really typed:

* Claude Code runs a ``UserPromptSubmit`` hook (``python -m cotaforge_mcp.engineer_words``, written into the design
  space by ``cotaforge init``) for every message that starts a turn. The hook appends the message to the log of the
  session, with the id Claude Code gave the message and the transcript it is written to. A subagent's prompt, a tool
  result and the agent's own replies never pass through that hook, and the first message of a drawing window, which the
  harness writes, is left out (``drawing_window.is_harness_prompt``).
* That hook also runs for messages NO person wrote: Claude Code hands the result of a background task (a subagent, a
  shell command) to the agent as a new message, and the text in it is the agent's doing. Nothing in what the hook
  receives tells the two apart; the transcript does. So a logged message counts only if the session's transcript says
  a person started that turn (``typed_by_a_person``): ``turnOrigin`` ``human``, or ``sdk`` when the whole session is a
  programmatic one (in a terminal session a ``sdk`` turn is somebody running ``claude -p --resume`` on it, which is
  not the person at the keyboard). Anything else, or a message the transcript does not have, does not count.
* The server accepts an order only if its quoted words are in one of those messages (``require``), compared without
  regard to case, to runs of spaces and to the quote marks around them, and only at word boundaries. Fewer than
  ``MIN_WORDS`` words ("sí", "ok", "aprobado") say nothing by themselves: they are accepted only as the whole of his
  LAST message, the answer to what he was just asked.

Where the log lives: ``<home>/sessions/<session id>.jsonl``, with ``<home>`` the harness's data folder (``COTAFORGE_HOME``,
else ``~/.cotaforge``) and the id the one Claude Code gives both the hook (``session_id``) and the server
(``CLAUDE_CODE_SESSION_ID``). It is outside the design space on purpose: there the agent edits files without asking,
here every write of its own needs the engineer's permission. One file per session, so a resumed session keeps its
words and another session's never count; a file nobody wrote to for ``KEEP_DAYS`` is deleted by the next hook run.
Claude Code can give a running conversation a new id without starting the server again (``/clear``): each line also
carries the pid of the Claude Code that ran the hook (``CLAUDE_PID``), and a line written after this server started by
the Claude Code that is its parent process counts as well.

What this cannot do: files are not a signature. An agent the engineer lets run shell commands unattended could write
the log and the transcript itself; that is a forgery it has to commit, no longer a sentence it types into a tool
argument. And the words are checked, not what he meant by them: that he typed them for THIS point is the agent's claim.

The hook always exits 0 and prints nothing (what a ``UserPromptSubmit`` hook prints is added to the conversation).
"""

import json
import os
import re
import sys
import time
import unicodedata
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, BinaryIO

if TYPE_CHECKING:
    from cotaforge_mcp.config import Settings

ENV_HOME = "COTAFORGE_HOME"
ENV_SESSION = "CLAUDE_CODE_SESSION_ID"
ENV_ENTRYPOINT = "CLAUDE_CODE_ENTRYPOINT"
ENV_PROJECT = "CLAUDE_PROJECT_DIR"
ENV_CLAUDE_PID = "CLAUDE_PID"
SESSIONS_DIRNAME = "sessions"
HOME_DIRNAME = ".cotaforge"
SERVER_NAME = "cad"
MCP_JSON = ".mcp.json"
HOOK_EVENT = "UserPromptSubmit"

TERMINAL_ENTRYPOINT = "cli"
"""``CLAUDE_CODE_ENTRYPOINT`` of a session a person types in at the terminal."""
HUMAN_TURN = "human"
PROGRAM_TURN = "sdk"
"""``turnOrigin`` in Claude Code's transcript: the person at the keyboard, and a prompt given by a program (``claude -p``,
an application that embeds Claude Code). Every other value is a turn Claude Code started itself."""

MIN_WORDS = 3
"""Quoted words shorter than this are accepted only as the whole of the engineer's last message."""
KEEP_DAYS = 30
MAX_MESSAGE_CHARS = 100_000
SESSION_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}")
QUOTE_MARKS = "\"'«»“”‘’`"
WORD = re.compile(r"\w+")

CODE = "not_the_engineers_words"
STARTED = time.time()
"""When this process loaded the module: for the server, when it started."""

Entry = dict[str, Any]


# --- where the log lives ------------------------------------------------------------------------------------------------


def sessions_dir(home: Path | None) -> Path:
    return (home if home is not None else Path.home() / HOME_DIRNAME) / SESSIONS_DIRNAME


def log_path(home: Path | None, session_id: str) -> Path | None:
    """The log of one session; ``None`` when the id is not one a file can be named after."""
    if not SESSION_ID.fullmatch(session_id or ""):
        return None
    return sessions_dir(home) / f"{session_id}.jsonl"


def hook_home(environ: Mapping[str, str], project: str | None) -> Path | None:
    """The data folder the SERVER of this design space uses, seen from the hook: what ``.mcp.json`` gives the server
    wins over the environment, as it does when Claude Code starts it."""
    stated = ""
    if project:
        try:
            data = json.loads((Path(project) / MCP_JSON).read_text(encoding="utf-8"))
            stated = str(data["mcpServers"][SERVER_NAME]["env"].get(ENV_HOME, "") or "")
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            stated = ""
    stated = stated.strip() or environ.get(ENV_HOME, "").strip()
    return Path(stated).expanduser() if stated else None


# --- writing (the hook) -------------------------------------------------------------------------------------------------


def record(
    home: Path | None,
    session_id: str,
    text: str,
    at: float | None = None,
    claude_pid: int | None = None,
    prompt_id: str | None = None,
    transcript: str | None = None,
) -> Path | None:
    """Append one message to the log of ``session_id``. Returns the file, ``None`` when nothing was written (no usable
    id, an empty message). ``prompt_id`` and ``transcript``: the id Claude Code gave the message and the transcript it
    writes it to (without them the message can never be shown to be a person's). ``claude_pid``: the Claude Code
    process it was submitted in."""
    path = log_path(home, session_id)
    if path is None or not text.strip():
        return None
    path.parent.mkdir(parents=True, exist_ok=True)
    entry: Entry = {"at": time.time() if at is None else at, "text": text[:MAX_MESSAGE_CHARS]}
    for key, value in (("prompt_id", prompt_id), ("transcript", transcript), ("claude_pid", claude_pid)):
        if value is not None:
            entry[key] = value
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return path


def forget_old(home: Path | None, now: float | None = None, keep_days: float = KEEP_DAYS) -> list[Path]:
    """Delete the logs nobody wrote to for ``keep_days`` (a session that old gives its orders again)."""
    limit = (time.time() if now is None else now) - keep_days * 86400
    gone: list[Path] = []
    try:
        files = list(sessions_dir(home).glob("*.jsonl"))
    except OSError:
        return gone
    for file in files:
        try:
            if file.stat().st_mtime < limit:
                file.unlink()
                gone.append(file)
        except OSError:
            continue
    return gone


def hook(payload: object, environ: Mapping[str, str]) -> Path | None:
    """What the hook does with one payload of Claude Code: the message that starts a turn goes to the log of its session."""
    if not isinstance(payload, dict) or payload.get("hook_event_name") != HOOK_EVENT:
        return None
    prompt, session_id = payload.get("prompt"), payload.get("session_id")
    if not isinstance(prompt, str) or not isinstance(session_id, str):
        return None
    from cotaforge_mcp.drawing_window import is_harness_prompt  # noqa: PLC0415 - only the hook needs it

    if is_harness_prompt(prompt):
        return None  # the first message of a drawing window: the harness wrote it, not the engineer
    project = environ.get(ENV_PROJECT) or payload.get("cwd")
    home = hook_home(environ, project if isinstance(project, str) else None)
    pid = environ.get(ENV_CLAUDE_PID, "")
    prompt_id, transcript = payload.get("prompt_id"), payload.get("transcript_path")
    written = record(
        home,
        session_id,
        prompt,
        claude_pid=int(pid) if pid.isdigit() else None,
        prompt_id=prompt_id if isinstance(prompt_id, str) else None,
        transcript=transcript if isinstance(transcript, str) else None,
    )
    forget_old(home)
    return written


def main(stdin: BinaryIO | None = None, environ: Mapping[str, str] | None = None) -> int:
    try:
        source = (
            stdin if stdin is not None else sys.stdin.buffer
        )  # bytes: Claude Code writes UTF-8 whatever the console is
        hook(json.loads(source.read().decode("utf-8")), os.environ if environ is None else environ)
    except Exception:  # noqa: BLE001 - a hook must never break the message it sees
        return 0
    return 0


# --- reading (the server) -----------------------------------------------------------------------------------------------


def _lines(path: Path) -> list[Entry]:
    try:
        with path.open(encoding="utf-8", errors="replace") as handle:
            lines = handle.read().splitlines()
    except OSError:
        return []
    found: list[Entry] = []
    for line in lines:
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict):
            found.append(entry)
    return found


def _at(entry: Entry) -> float:
    at = entry.get("at")
    return float(at) if isinstance(at, (int, float)) else 0.0


def logged(home: Path | None, session_id: str, parent_pid: int | None = None, since: float = 0.0) -> list[Entry]:
    """Every message the hook logged for ``session_id``, oldest first, whoever wrote it (nothing when there is no log).
    ``parent_pid``: also what the Claude Code with that pid logged after ``since`` under another id (see the module text)."""
    own = log_path(home, session_id)
    if own is None:
        return []
    entries = [e for e in _lines(own) if isinstance(e.get("text"), str)]
    if parent_pid is not None:
        try:
            others = [file for file in sessions_dir(home).glob("*.jsonl") if file != own]
        except OSError:
            others = []
        for file in others:
            entries += [
                e
                for e in _lines(file)
                if isinstance(e.get("text"), str) and e.get("claude_pid") == parent_pid and _at(e) >= since
            ]
        entries.sort(key=_at)
    return entries


def turn_origins(transcript: Path, prompt_ids: set[str]) -> dict[str, set[str]]:
    """What Claude Code's transcript says started each of ``prompt_ids``: its ``turnOrigin`` values (a message the
    transcript does not have gets none)."""
    found: dict[str, set[str]] = {}
    try:
        with transcript.open(encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if '"turnOrigin"' not in line or not any(prompt_id in line for prompt_id in prompt_ids):
                    continue
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if (
                    not isinstance(entry, dict)
                    or entry.get("type") != "user"
                    or entry.get("promptId") not in prompt_ids
                ):
                    continue
                found.setdefault(entry["promptId"], set()).add(str(entry.get("turnOrigin")))
    except OSError:
        return {}
    return found


def person_turns(entrypoint: str | None) -> frozenset[str]:
    """The ``turnOrigin`` values that are a person's in a session started the way ``entrypoint`` says."""
    return frozenset({HUMAN_TURN}) if entrypoint == TERMINAL_ENTRYPOINT else frozenset({HUMAN_TURN, PROGRAM_TURN})


def typed_by_a_person(entries: list[Entry], entrypoint: str | None) -> list[str]:
    """The texts of ``entries`` that the transcript of their session shows a person wrote, in the same order."""
    wanted: dict[str, set[str]] = {}
    for entry in entries:
        prompt_id, transcript = entry.get("prompt_id"), entry.get("transcript")
        if isinstance(prompt_id, str) and prompt_id and isinstance(transcript, str) and transcript:
            wanted.setdefault(transcript, set()).add(prompt_id)
    origins = {transcript: turn_origins(Path(transcript), ids) for transcript, ids in wanted.items()}
    allowed = person_turns(entrypoint)
    texts: list[str] = []
    for entry in entries:
        said = origins.get(str(entry.get("transcript")), {}).get(str(entry.get("prompt_id")), set())
        if said and said <= allowed:
            texts.append(str(entry["text"]))
    return texts


def messages(
    home: Path | None,
    session_id: str,
    parent_pid: int | None = None,
    since: float = 0.0,
    entrypoint: str | None = TERMINAL_ENTRYPOINT,
) -> list[str]:
    """What the engineer typed in ``session_id``, oldest first: the logged messages a person wrote."""
    return typed_by_a_person(logged(home, session_id, parent_pid, since), entrypoint)


def normalise(text: str) -> str:
    """The form two texts are compared in: one case, single spaces, composed characters."""
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def quoted(words: str) -> str:
    """``words`` without the quote marks and spaces around them."""
    return " ".join(words.split()).strip(QUOTE_MARKS + " ")


def _bare(text: str) -> str:
    return " ".join(WORD.findall(normalise(text)))


def spoken(said: list[str], words: str) -> bool:
    """Whether ``words`` are in what the engineer typed (``said``, oldest first); see the module text."""
    needle = normalise(quoted(words))
    if not WORD.search(needle):
        return False
    if len(WORD.findall(needle)) < MIN_WORDS:
        return bool(said) and _bare(said[-1]) == _bare(needle)
    pattern = re.compile(rf"(?<!\w){re.escape(needle)}(?!\w)")
    return any(pattern.search(normalise(message)) for message in said)


def require(settings: "Settings", words: str | None, what: str, field_name: str = "reason") -> str:
    """The engineer's words for ``what`` (an order, an approval...), checked: returns them without the quote marks
    around them, or refuses. ``field_name``: the argument of the tool that carries them."""
    from cotaforge_mcp.reply import ToolError  # noqa: PLC0415 - the hook imports this module and needs no pydantic

    said = quoted(words or "")
    session_id = settings.session_id or ""
    if log_path(settings.home, session_id) is None:
        raise ToolError(
            f"{what} is the engineer's, and this server cannot tell which session it serves (Claude Code gave it no "
            f"{ENV_SESSION}), so it cannot check that he said it: nothing was changed. Tell the engineer: his Claude "
            "Code must be up to date for an order of his to be accepted",
            code=CODE,
            field_name=field_name,
        )
    entries = logged(settings.home, session_id, os.getppid(), STARTED)
    if not entries:
        raise ToolError(
            f"{what} is the engineer's, and nothing he typed in this session was recorded, so it cannot be checked "
            "that he said it: nothing was changed. Either he has typed nothing yet, or this design space lacks the hook "
            "that keeps his messages (it was prepared by an older version): tell him to prepare the folder again "
            "(`cotaforge init <folder>`, or the CotaForge shortcut that opens a folder) and to start Claude Code again",
            code=CODE,
            field_name=field_name,
        )
    typed = typed_by_a_person(entries, settings.entrypoint)
    if not spoken(typed, said):
        short = len(WORD.findall(normalise(said))) < MIN_WORDS
        elsewhere = spoken([str(entry["text"]) for entry in entries], said) if not short else False
        raise ToolError(
            f"{what} is the engineer's, and he did not type «{said}» in this session: nothing was changed. "
            + (
                "Those words reached this session in a message no person typed (the result of a background task, a "
                "message another program sent, or one Claude Code's transcript does not show as his): they are not his. "
                if elsewhere
                else ""
            )
            + f"`{field_name}` carries HIS words, copied exactly from a message he typed here (no name in front, nothing "
            "reworded, nothing of yours or of another agent)"
            + (f"; fewer than {MIN_WORDS} words count only as the whole of his last message" if short else "")
            + ". If he has not said it, the decision is not yours: tell him what is open and what you recommend, and "
            "wait until he says it himself",
            code=CODE,
            field_name=field_name,
        )
    return said


if __name__ == "__main__":
    sys.exit(main())
