"""Progress of a long tool call, as the MCP client sees it.

The adapter says what phase a long request is in (``ProgressNotice``); this module turns that into the numbers and the
text of an MCP ``notifications/progress`` message: one scale from 0 to 100 for the whole call, so a client can draw a
bar, and a message that says what is going on. The scale only ever goes up (the protocol requires it), and a phase the
CAD gives no count for (``reading``: FreeCAD reports no percentage while it reads the file) climbs towards the end of
its share but never reaches it, so no number pretends to be a measurement there.
"""

import math
import threading
from collections.abc import Callable

from cotaforge.contract import ProgressNotice

TOTAL = 100.0

# phase -> (first, last) point of the scale it covers; the work of an import in the order it happens
PHASE_SPAN: dict[str, tuple[float, float]] = {
    "reading": (0.0, 10.0),
    "preparing": (10.0, 13.0),
    "sewing": (13.0, 30.0),
    "building": (30.0, 33.0),
    "adopting": (33.0, 60.0),
    "checking": (60.0, 80.0),
    "linking": (80.0, 85.0),
    "saving": (85.0, 100.0),
}
CONNECT_SPAN = (0.0, 3.0)
"""Where a call that waited for the CAD to start stands on the scale (apart from the phases of an import; it has no
count either, so it climbs the same way), before the call itself goes on from there."""
READING_HALF_LIFE_S = 120.0
"""Seconds of reading after which the unmeasurable phase is shown half way through its share."""

Sink = Callable[[float, float, str], None]
"""``sink(progress, total, message)``: what is sent to the MCP client."""


def position(notice: ProgressNotice) -> float:
    """Where on the 0 to ``TOTAL`` scale a notice puts the call (before the monotone clamp of ``Reporter``)."""
    first, last = CONNECT_SPAN if notice.phase == "connecting" else PHASE_SPAN.get(notice.phase, PHASE_SPAN["saving"])
    if notice.current is not None and notice.total:
        return first + (last - first) * min(1.0, notice.current / notice.total)
    if notice.elapsed_s is not None:  # no count: closer to the end of the share the longer it takes, never there
        return first + (last - first) * (1.0 - math.exp(-math.log(2.0) * notice.elapsed_s / READING_HALF_LIFE_S)) * 0.99
    return first


def starting_message(elapsed_s: float) -> str:
    return f"starting the CAD software ({elapsed_s:.0f} s)"


class Reporter:
    """Sends ``ProgressNotice``s to a ``Sink``: never backwards, and every message even when the number stands still.
    ``start`` is the last number already sent for the request (by an earlier reporter): the scale goes on from it."""

    def __init__(self, sink: Sink, start: float = 0.0) -> None:
        self._sink = sink
        self._last = start
        self._lock = threading.Lock()  # the CAD's own notices and the adapter's heartbeats come from different threads

    def __call__(self, notice: ProgressNotice) -> None:
        with self._lock:
            self._last = max(self._last + 0.001, position(notice))  # the protocol wants a strictly increasing number
            self._sink(round(self._last, 3), TOTAL, notice.message or notice.phase)

    def say(self, phase: str, message: str) -> None:
        self(ProgressNotice(phase=phase, message=message))
