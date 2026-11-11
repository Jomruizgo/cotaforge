"""The conduct file of a design space (``CLAUDE.md``) and how it keeps up with the template.

``init`` copies the template into a new design space and never touches that copy again, so a project made before a
release never got the rules added since. The cure is not ``--force`` (it wipes what the engineer wrote) but knowing
whether he wrote anything: the package ships the SHA-256 of every template version it ever released
(``data/conduct-history.json``). A ``CLAUDE.md`` that equals one of them is a template nobody edited and is replaced
by the current one (the old one is kept beside it); anything else is the engineer's and is never replaced: the current
template is written beside it as ``CLAUDE.md.new`` (``CLAUDE.md.nuevo`` in a project that has one) and the agent is told, at every start while that file exists, to
offer the merge. Removing ``CLAUDE.md.new`` closes it: ``.cotaforge/conduct-offered`` remembers which template
was offered, so it is not offered again until the template changes.

The rule is the same for every CAD: each harness gives its own :class:`Source` (its template and its history).
Nothing but ``CLAUDE.md`` and the two files named after it is ever written.
"""

import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from importlib import resources
from importlib.resources.abc import Traversable
from pathlib import Path
from typing import Any

from cotaforge import paths

import cotaforge_mcp

CLAUDE_MD = "CLAUDE.md"
BACKUP_SUFFIX = ".bak"
PROPOSAL_SUFFIX = paths.CONDUCT_PROPOSAL_SUFFIX
HISTORY_FILE = "conduct-history.json"
MCP_JSON = ".mcp.json"
SERVER_NAME = "cad"
OFFERED_MARKER = Path(".cotaforge") / "conduct-offered"
"""The digest of the template last offered as ``CLAUDE.md.new``."""
UTF8_BOM = b"\xef\xbb\xbf"
SHORT = 8
"""Characters of a digest in the name of a backup."""


def normalise(data: bytes) -> bytes:
    """The text without what an editor changes behind the engineer's back: the line endings and a byte-order mark."""
    return data.removeprefix(UTF8_BOM).replace(b"\r\n", b"\n").replace(b"\r", b"\n")


def digest(data: bytes) -> str:
    return hashlib.sha256(normalise(data)).hexdigest()


def parse_history(text: str) -> dict[str, str]:
    """``{digest: since}`` from the history file (``since`` is the release that first carried that template)."""
    data = json.loads(text)
    return {str(item["sha256"]): str(item.get("since", "")) for item in data["versions"]}


def add_version(history_path: Path, template: bytes, since: str) -> bool:
    """Append the digest of ``template`` to the history file unless it is there. Returns whether it was added."""
    text = history_path.read_text(encoding="utf-8")
    if digest(template) in parse_history(text):
        return False
    data: dict[str, Any] = json.loads(text)
    data["versions"].append({"sha256": digest(template), "since": since})
    history_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return True


@dataclass(frozen=True)
class Source:
    """What one harness knows of its conduct file: the current template, every one it released, its own version."""

    template: bytes
    history: Mapping[str, str]
    version: str

    @classmethod
    def load(cls, template: Traversable, history: Traversable, version: str) -> "Source":
        return cls(template.read_bytes(), parse_history(history.read_text(encoding="utf-8")), version)


def product_source() -> Source:
    """The FreeCAD harness (this package): its template and its history."""
    root = resources.files(cotaforge_mcp.__name__) / "data"
    return Source.load(root / "design-space" / CLAUDE_MD, root / HISTORY_FILE, cotaforge_mcp.__version__)


def source_of(adapter: object) -> Source | None:
    """The conduct source of the harness that runs ``adapter``: the adapter's own (``conduct_source()``) or, for the
    product's FreeCAD one, the package's. ``None`` for any other adapter: a foreign template is never used."""
    hook = getattr(adapter, "conduct_source", None)
    if callable(hook):
        found = hook()
        return found if isinstance(found, Source) else None
    return product_source() if getattr(adapter, "name", None) == "freecad" else None


class State(Enum):
    ABSENT = "absent"
    CURRENT = "current"
    REFRESHED = "refreshed"
    EDITED = "edited"


@dataclass(frozen=True)
class Outcome:
    state: State
    since: str = ""
    """REFRESHED: the release the replaced file belonged to."""
    backup: Path | None = None
    """REFRESHED: where the replaced file went."""
    proposal: Path | None = None
    """EDITED: the current template, beside the engineer's file."""
    announce: bool = False
    """EDITED: the agent is told (at every start while ``CLAUDE.md.new`` exists; not after it was removed)."""


def is_design_space(folder: Path) -> bool:
    """A folder ``init`` prepared: its ``.mcp.json`` starts the ``cad`` server. Any other ``CLAUDE.md`` is not ours."""
    try:
        servers = json.loads((folder / MCP_JSON).read_text(encoding="utf-8")).get("mcpServers", {})
        return SERVER_NAME in servers
    except (OSError, ValueError, AttributeError):
        return False


def write_atomic(path: Path, data: bytes) -> None:
    temp = path.with_name(path.name + ".tmp")
    temp.write_bytes(data)
    os.replace(temp, path)


def refresh(folder: Path, source: Source) -> Outcome:
    """Bring the ``CLAUDE.md`` of ``folder`` up to ``source`` when the engineer never edited it (see the module text).
    Raises :class:`OSError` when a file cannot be written; what was written before is complete (atomic replace)."""
    target = folder / CLAUDE_MD
    proposal = paths.resolve(folder, f"{CLAUDE_MD}{PROPOSAL_SUFFIX}")
    try:
        found = target.read_bytes()
    except FileNotFoundError:
        return Outcome(State.ABSENT)
    mine = digest(found)
    if mine == digest(source.template):
        _drop_proposal(proposal, source.template)
        return Outcome(State.CURRENT)
    since = source.history.get(mine)
    if since is not None:  # a template nobody edited: the old one is kept, then replaced
        backup = folder / f"{CLAUDE_MD}.{since or 'old'}-{mine[:SHORT]}{BACKUP_SUFFIX}"
        write_atomic(backup, found)
        write_atomic(target, source.template)
        _drop_proposal(proposal, source.template)
        return Outcome(State.REFRESHED, since=since, backup=backup)
    wanted = digest(source.template)
    marker = folder / OFFERED_MARKER
    try:
        waiting = digest(proposal.read_bytes()) == wanted  # offered and not yet dealt with: said at every start
    except OSError:
        waiting = False
    if waiting:
        return Outcome(State.EDITED, proposal=proposal, announce=True)
    try:
        dealt_with = marker.read_text(encoding="utf-8").strip() == wanted and not proposal.exists()
    except OSError:
        dealt_with = False
    if dealt_with:  # offered, merged and ``CLAUDE.md.new`` removed: the engineer's file stays as it is, silently
        return Outcome(State.EDITED, proposal=proposal, announce=False)
    write_atomic(proposal, source.template)
    marker.parent.mkdir(exist_ok=True)
    marker.write_text(wanted + "\n", encoding="utf-8")
    return Outcome(State.EDITED, proposal=proposal, announce=True)


def _drop_proposal(proposal: Path, template: bytes) -> None:
    """The proposal we wrote is useless once the file has the template; one the engineer changed is left alone."""
    try:
        if digest(proposal.read_bytes()) == digest(template):
            proposal.unlink()
    except OSError:
        pass


def notice(outcome: Outcome, version: str) -> str:
    """What the server tells the agent (in its instructions) about the conduct file; empty when there is nothing."""
    if outcome.state is State.REFRESHED and outcome.backup is not None:
        return (
            f"Conduct file: CLAUDE.md was an unedited old template and now is the one of CotaForge {version} "
            f"(the old one is {outcome.backup.name}). The CLAUDE.md in your context is the old one: read CLAUDE.md "
            "again now and tell the engineer in one line, in his language (the project language), that the conduct was updated."
        )
    if outcome.state is State.EDITED and outcome.announce and outcome.proposal is not None:
        return (
            f"Conduct file: the engineer edited CLAUDE.md and it lacks rules of the current template (CotaForge "
            f"{version}), written to {outcome.proposal.name}. At the start tell him in one line, in his language (the project language), and offer to merge: "
            "read both, list the rules that are missing, apply only what he accepts, never replace his file, and "
            f"delete {outcome.proposal.name} when done."
        )
    return ""


def server_notice(adapter: object, folder: Path) -> str:
    """Run at server start: refresh the conduct file of the design space ``folder`` and return the notice for the agent.
    Never raises: a conduct file that cannot be checked must not stop the server."""
    try:
        source = source_of(adapter)
        if source is None or not is_design_space(folder):
            return ""
        return notice(refresh(folder, source), source.version)
    except (OSError, ValueError, KeyError):
        return ""
