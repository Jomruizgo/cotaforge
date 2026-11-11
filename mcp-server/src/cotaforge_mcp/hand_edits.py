"""What the engineer changed by hand in the CAD since the agent's last call, as one short block at the start of the
next tool reply.

The CAD tells the server about a change of the DESIGN the moment it happens (``ManualEditProducer``: a property,
an object added, removed or renamed, an undo, a sketch; never the camera, the view, visibility, colours or the
selection). There is no channel to wake the agent, so the notices wait here, coalesced, and ``take`` hands them over
once with whichever tool call comes next. The selection is deliberately NOT pushed: the agent reads it only when the
engineer says so (``view(action=selection)``).

``push`` runs on the adapter's reader thread, ``take`` on a tool-call thread: the queue is behind a lock.
"""

import re
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from cotaforge.contract import ManualEditNotice

if TYPE_CHECKING:
    from cotaforge_mcp.session import Session

MAX_PENDING = 60
"""Objects kept while the agent does not call; the rest is only counted."""
MAX_SHOWN = 6
"""Objects named in one block; the rest is counted."""
MAX_LINES = 3
"""Changes shown per object."""
MAX_LINE_CHARS = 220
HEADER = "hand edits in the CAD since your last call (the engineer changed the model; re-read what you rely on):"

_PROPERTY_LINE = re.compile(r"^(?P<prop>[^:]+): (?P<old>.+) -> (?P<new>.+)$")


@dataclass
class _Entry:
    document: str
    name: str
    ref: str
    kind: str
    action: str
    reason: str
    props: dict[str, list[str]] = field(default_factory=dict)  # property -> [old, new]
    other: list[str] = field(default_factory=list)

    def absorb(self, lines: tuple[str, ...]) -> None:
        for line in lines:
            parsed = _PROPERTY_LINE.match(line)
            if parsed is None:
                if line not in self.other:
                    self.other.append(line)
                continue
            prop, old, new = parsed["prop"], parsed["old"], parsed["new"]
            seen = self.props.get(prop)
            if seen is None:
                self.props[prop] = [old, new]
            elif seen[0] == new:
                del self.props[prop]  # edited and put back: nothing changed after all
            else:
                seen[1] = new

    def lines(self) -> list[str]:
        return [f"{prop}: {old} -> {new}" for prop, (old, new) in self.props.items()] + self.other


@dataclass
class _Touch:
    """One object the engineer changed by hand that the plan does not describe: ``action`` is ``edited`` or ``added``;
    ``props`` holds, per property, its value before the first edit and now, and is dropped when it is put back (an
    undo). ``opaque`` marks a change that has no before and after to compare, so only a removal ends it."""

    action: str
    props: dict[str, list[str]] = field(default_factory=dict)
    opaque: bool = False

    def absorb(self, lines: tuple[str, ...]) -> None:
        for line in lines:
            parsed = _PROPERTY_LINE.match(line)
            if parsed is None:
                self.opaque = True
                continue
            prop, old, new = parsed["prop"], parsed["old"], parsed["new"]
            seen = self.props.get(prop)
            if seen is None:
                self.props[prop] = [old, new]
            elif seen[0] == new:
                del self.props[prop]
            else:
                seen[1] = new

    def settled(self) -> bool:
        """An edit that was put back: nothing differs from the plan any more."""
        return self.action == "edited" and not self.props and not self.opaque


class HandEdits:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[tuple[str, str], _Entry] = {}
        self._uncounted = 0
        self._touched: dict[tuple[str, str], _Touch] = {}
        """Objects the engineer edited or added by hand, kept after the notice was handed over: a rebuild of the
        steps would drop them, so it is refused while any is there (``touched``)."""

    # --- the adapter's side (reader thread) --------------------------------------------------------------------

    def push(self, notice: ManualEditNotice) -> None:
        with self._lock:
            if notice.action == "more":
                first = notice.changes[0].split(" ", 1)[0] if notice.changes else ""
                self._uncounted += int(first) if first.isdigit() else 1
                return
            key = (notice.document, notice.name or notice.ref)
            self._touch(key, notice)
            entry = self._entries.get(key)
            if entry is None:
                if len(self._entries) >= MAX_PENDING:
                    self._uncounted += 1
                    return
                entry = _Entry(
                    notice.document,
                    notice.name or notice.ref,
                    notice.ref,
                    notice.artefact_kind,
                    notice.action,
                    notice.reason,
                )
                self._entries[key] = entry
                if notice.action == "edited":
                    entry.absorb(notice.changes)
                else:
                    entry.other.extend(notice.changes)
                return
            self._merge(key, entry, notice)

    def _touch(self, key: tuple[str, str], notice: ManualEditNotice) -> None:
        """Keep the object while it differs from the plan; forget it once removed, or edited and put back."""
        if notice.action == "removed":
            self._touched.pop(key, None)
        elif notice.action == "added":
            self._touched[key] = _Touch("added")
        elif notice.action == "edited":
            touch = self._touched.setdefault(key, _Touch("edited"))
            if touch.action == "edited":
                touch.absorb(notice.changes)
                if touch.settled():
                    del self._touched[key]

    def _merge(self, key: tuple[str, str], entry: _Entry, notice: ManualEditNotice) -> None:
        if notice.action == "removed":
            if entry.action == "added":
                del self._entries[key]  # added and removed since the last call: it was never there
                return
            entry.action, entry.reason = "removed", notice.reason
            entry.props.clear()
            entry.other = list(notice.changes)
        elif notice.action == "added":
            entry.action, entry.reason = "added", notice.reason  # removed and made again
            entry.props.clear()
            entry.other = list(notice.changes)
        elif entry.action == "edited":
            entry.reason = notice.reason
            entry.absorb(notice.changes)
            if not entry.props and not entry.other:
                del self._entries[key]  # edited and put back

    def clear(self) -> None:
        """The CAD was relaunched: what was pending described a model that no longer exists."""
        with self._lock:
            self._entries.clear()
            self._uncounted = 0
            self._touched.clear()

    def forget(self) -> None:
        """A document was closed, opened or created: what was touched by hand belonged to the old one."""
        with self._lock:
            self._touched.clear()

    def touched(self, document: str | None = None) -> list[str]:
        """The names of the objects the engineer changed or added by hand and that still differ from the plan, in
        ``document`` (every document when ``None``; a notice that names no document counts for any)."""
        with self._lock:
            return [name for doc, name in self._touched if document is None or doc in ("", document)]

    # --- the tool call's side ------------------------------------------------------------------------------------

    def pending(self) -> bool:
        with self._lock:
            return bool(self._entries) or self._uncounted > 0

    def take(self, session: "Session") -> str | None:
        """The block for the reply that is being assembled, and forget it; ``None`` when nothing changed."""
        with self._lock:
            entries = list(self._entries.values())
            uncounted = self._uncounted
            self._entries.clear()
            self._uncounted = 0
        if not entries and not uncounted:
            return None
        lines = [HEADER]
        for entry in entries[:MAX_SHOWN]:
            lines.append(self._line(session, entry))
        left = len(entries) - MAX_SHOWN + uncounted
        if left > 0:
            lines.append(f"... and {left} more changed objects (inspect(action=tree) shows the current state)")
        return "\n".join(lines)

    @staticmethod
    def _line(session: "Session", entry: _Entry) -> str:
        where = f"[{entry.document}] " if entry.document and entry.document != session.doc_name else ""
        step = None
        if entry.kind == "feature" and (not entry.document or entry.document == session.doc_name):
            step = session.step_of_feature(entry.ref)
        name = f"{entry.name} (step {step})" if step else entry.name
        changes = entry.lines()
        shown = "; ".join(changes[:MAX_LINES]) + (
            f"; +{len(changes) - MAX_LINES} more" if len(changes) > MAX_LINES else ""
        )
        tag = f" ({entry.reason.split(' ', 1)[0]})" if entry.reason.startswith(("undo", "redo")) else ""
        text = f"- {where}{name}: {shown}{tag}"
        return text if len(text) <= MAX_LINE_CHARS else text[: MAX_LINE_CHARS - 3] + "..."
