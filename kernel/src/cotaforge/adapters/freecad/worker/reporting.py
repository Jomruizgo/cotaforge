"""Progress of a long request, pushed to the client as ``progress`` notifications (no ``id``; the client's reader routes
them around the request/response queue, like ``selection_changed``).

A step of a loop calls the ``Reporter`` for every item; it forwards the first call of a phase, the last call of a phase
and otherwise at most one call per ``min_interval_s``, so a loop over thousands of bodies sends a few messages a second.
"""

import time
from collections.abc import Callable
from typing import Any

MIN_INTERVAL_S = 0.5

Emit = Callable[[dict[str, Any]], None]


class Reporter:
    """``reporter(phase, current, total, message)``: ``current``/``total`` count the items of the phase (``None`` when
    it has no count). Does nothing without an ``emit``."""

    def __init__(
        self,
        emit: Emit | None,
        operation_id: str | None = None,
        min_interval_s: float = MIN_INTERVAL_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._emit = emit
        self._operation_id = operation_id
        self._min_interval_s = min_interval_s
        self._clock = clock
        self._phase: str | None = None
        self._last = float("-inf")

    def __call__(self, phase: str, current: int | None = None, total: int | None = None, message: str = "") -> None:
        if self._emit is None:
            return
        now = self._clock()
        first = phase != self._phase
        last = current is not None and total is not None and current >= total
        if not (first or last or now - self._last >= self._min_interval_s):
            return
        self._phase = phase
        self._last = now
        self._emit(
            {"operation_id": self._operation_id, "phase": phase, "current": current, "total": total, "message": message}
        )
