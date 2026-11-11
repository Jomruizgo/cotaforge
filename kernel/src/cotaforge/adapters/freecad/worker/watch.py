"""When did the ENGINEER change the model by hand? (live manual-edit seam; no FreeCAD import, so the policy is
unit-testable against stand-ins: ``tests/unit/freecad_client/test_watch.py``.)

``gui.start_document_watch`` tells ``on_event`` that something happened to a document and, when it knows, to which
object (an object created or deleted, a property changed) -- or only to the document (a recompute, an undo, a redo).
This module decides what that means:

- While a request of the harness runs (``busy()``), the event is the harness's own: the object is only marked as
  touched, and ``after_harness`` takes a new baseline of the touched objects, so nothing the harness did is ever
  reported later.
- Otherwise it is the engineer's: the object is queued and, once the burst of events settles (a hand edit is several
  events: the change, the recompute, the objects it touched), the queued objects are captured again and compared with
  their baseline (``changes.py``): property edits, objects added, objects removed. Camera moves, selections, visibility
  and colours never get this far (they raise no event, or only display properties that ``changes.capture`` leaves out),
  and an event that changes nothing of the design reports nothing.
- ``before_harness`` settles what is still queued BEFORE a harness request starts, so a hand edit made a moment
  before the agent's next call is neither lost to the debounce nor mistaken for the harness's work.

Only the objects the events named are captured again (an undo or a redo names none, so it captures the whole
document): the cost follows what changed, not the size of the document.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

import changes
from debounce import Debouncer

SETTLE_S = 0.3
"""Quiet time after the last event before the queued objects are compared with their baseline."""

MAX_EDITS = 25
"""Most objects one settle reports; the rest is counted in a final ``more`` edit."""

FULL_CAUSES = frozenset({"undo", "redo"})
"""Events that change objects without naming them."""

__all__ = ["Edit", "ManualEditWatch", "compare"]


@dataclass(frozen=True)
class Edit:
    """One object the engineer changed: ``action`` is ``edited``, ``added``, ``removed`` or (the closing one of a
    long list) ``more``."""

    object: str
    action: str
    label: str
    type_id: str
    lines: list[str]
    reason: str


@dataclass
class _Dirty:
    """What happened to one document since its baseline: the objects named, the causes, and whether something
    changed that no event named (``full``)."""

    names: set[str] = field(default_factory=set)
    causes: set[str] = field(default_factory=set)
    full: bool = False

    def add(self, cause: str, name: str | None) -> None:
        self.causes.add(cause)
        if name is not None:
            self.names.add(name)
        if cause in FULL_CAUSES:
            self.full = True


def _reason(causes: set[str]) -> str:
    if "undo" in causes:
        return "undo in the FreeCAD GUI"
    if "redo" in causes:
        return "redo in the FreeCAD GUI"
    return "hand edit in the FreeCAD GUI"


def compare(before: changes.Baseline, after: changes.Baseline, causes: set[str] | None = None) -> list[Edit]:
    """What differs between two captures of the same objects of one document, as ``Edit``s: edited objects first (in
    document order), then added, then removed; at most ``MAX_EDITS``, the rest counted."""
    reason = _reason(causes or set())
    edits: list[Edit] = []
    for name, lines in changes.diff(before.props, after.props).items():
        edits.append(Edit(name, "edited", str(after.props[name].get("Label", name)), after.kinds[name], lines, reason))
    for name in after.props:
        if name not in before.props:
            kind = after.kinds[name]
            label = str(after.props[name].get("Label", name))
            edits.append(Edit(name, "added", label, kind, [f"added {kind}"], reason))
    for name in before.props:
        if name not in after.props:
            kind = before.kinds[name]
            label = str(before.props[name].get("Label", name))
            edits.append(Edit(name, "removed", label, kind, [f"removed {kind}"], reason))
    if len(edits) > MAX_EDITS:
        left = len(edits) - MAX_EDITS
        edits = [*edits[:MAX_EDITS], Edit("", "more", "", "", [f"{left} more objects changed"], reason)]
    return edits


class ManualEditWatch:
    """The policy above. Everything FreeCAD-specific is injected: ``capture(doc_name, names)`` (a ``Baseline`` of the
    objects called ``names``, of the whole document when ``names`` is ``None``, ``None`` when the document is gone),
    ``known(doc_name)`` (is it a document of the harness), ``known_docs()``, ``busy()`` (is a harness request
    running), ``report(doc_name, edits)`` and the timer (``schedule``/``cancel``, a restarted ``QTimer`` in the real
    worker). All of it runs on the Qt thread."""

    def __init__(
        self,
        *,
        capture: Callable[[str, set[str] | None], changes.Baseline | None],
        known: Callable[[str], bool],
        known_docs: Callable[[], Iterable[str]],
        busy: Callable[[], bool],
        report: Callable[[str, list[Edit]], None],
        schedule: Callable[[float, Callable[[], None]], Any],
        cancel: Callable[[Any], None],
        settle_s: float = SETTLE_S,
    ) -> None:
        self._capture = capture
        self._known = known
        self._known_docs = known_docs
        self._busy = busy
        self._report = report
        self._base: dict[str, changes.Baseline] = {}
        self._pending: dict[str, _Dirty] = {}
        self._touched: dict[str, _Dirty] = {}
        self._debouncer = Debouncer(settle_s, self.settle, schedule=schedule, cancel=cancel)

    # --- events ------------------------------------------------------------------------------------------------

    def on_event(self, doc_name: str, cause: str, name: str | None = None) -> None:
        """Something happened to ``doc_name`` -- to its object ``name`` when the CAD says which (``recompute``,
        ``added``, ``removed``, ``changed``, ``undo``, ``redo``)."""
        if self._busy():
            self._touched.setdefault(doc_name, _Dirty()).add(cause, name)
            return
        if not self._known(doc_name):
            return
        self._pending.setdefault(doc_name, _Dirty()).add(cause, name)
        self._debouncer.ping()

    def settle(self) -> None:
        """The burst is over (the debounce timer, or ``before_harness``): compare what was queued with the baseline and
        report the difference."""
        if self._busy():
            self._debouncer.ping()
            return
        pending, self._pending = self._pending, {}
        for doc_name, dirty in pending.items():
            self._check(doc_name, dirty)

    def _check(self, doc_name: str, dirty: _Dirty) -> None:
        base = self._base.get(doc_name)
        if base is None or dirty.full:
            after = self._safe_capture(doc_name, None)
            if after is None:
                self._base.pop(doc_name, None)
                return
            self._base[doc_name] = after
            if base is not None:
                self._tell(doc_name, base, after, dirty)
            return  # without a baseline this capture only seeds it
        if not dirty.names:
            return  # a recompute that no object announced a change in: nothing to compare
        after = self._safe_capture(doc_name, dirty.names)
        if after is None:
            self._base.pop(doc_name, None)
            return
        before = changes.Baseline(
            {n: base.props[n] for n in dirty.names if n in base.props},
            {n: base.kinds[n] for n in dirty.names if n in base.kinds},
        )
        self._merge(doc_name, dirty.names, after)
        self._tell(doc_name, before, after, dirty)

    def _tell(self, doc_name: str, before: changes.Baseline, after: changes.Baseline, dirty: _Dirty) -> None:
        edits = compare(before, after, dirty.causes)
        if edits:
            self._report(doc_name, edits)

    def _merge(self, doc_name: str, names: set[str], captured: changes.Baseline) -> None:
        """The objects ``names`` as they are now replace those of the baseline (and are dropped from it when gone)."""
        base = self._base[doc_name]
        for name in names:
            if name in captured.props:
                base.props[name] = captured.props[name]
                base.kinds[name] = captured.kinds[name]
            else:
                base.props.pop(name, None)
                base.kinds.pop(name, None)

    def _safe_capture(self, doc_name: str, names: set[str] | None) -> changes.Baseline | None:
        try:
            return self._capture(doc_name, names)
        except Exception:  # noqa: BLE001 - a document being closed or rebuilt: no baseline, no report, no crash
            return None

    # --- the harness's own requests ------------------------------------------------------------------------------

    def before_harness(self) -> None:
        """A harness request is about to run: report what the engineer did before it, while the baseline is still
        the state they started from."""
        if self._pending:
            self.settle()

    def after_harness(self) -> None:
        """The request is over: the objects it changed take their new state as the baseline, and any document the
        watch has never seen (one the request just opened or created) gets its first one."""
        touched, self._touched = self._touched, {}
        for doc_name in self._known_docs():
            if doc_name not in self._base:
                touched.setdefault(doc_name, _Dirty()).full = True
        for doc_name, dirty in touched.items():
            self._pending.pop(doc_name, None)
            if not self._known(doc_name):
                self._base.pop(doc_name, None)
                continue
            if dirty.full or doc_name not in self._base:
                captured = self._safe_capture(doc_name, None)
                if captured is None:
                    self._base.pop(doc_name, None)
                else:
                    self._base[doc_name] = captured
            elif dirty.names:
                captured = self._safe_capture(doc_name, dirty.names)
                if captured is None:
                    self._base.pop(doc_name, None)
                else:
                    self._merge(doc_name, dirty.names, captured)

    def forget(self, doc_name: str) -> None:
        """The document was closed."""
        self._base.pop(doc_name, None)
        self._pending.pop(doc_name, None)
        self._touched.pop(doc_name, None)
