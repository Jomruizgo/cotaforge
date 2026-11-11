"""A tiny, CAD-agnostic debounce policy (CAD-selection seam): coalesces a burst of ``ping()`` calls
(e.g. FreeCAD's ``Gui.Selection`` observer firing once per sub-element while the engineer drag-selects)
into a single ``on_settle()`` call, fired ``delay_s`` after the *last* ping.

No FreeCAD/Qt import on purpose, so this is unit-testable with a fake scheduler (``tests/unit/freecad_client``):
the timer mechanism is injected (``schedule``/``cancel``) because the real worker must fire ``on_settle``
on the Qt thread (a ``QTimer`` guarantees that), which a plain ``threading.Timer`` would not.
"""

from collections.abc import Callable
from typing import Any

Scheduler = Callable[[float, Callable[[], None]], Any]
Canceller = Callable[[Any], None]

__all__ = ["Debouncer"]


class Debouncer:
    """``ping()`` (re)starts a ``delay_s`` countdown; only the last ping in a burst survives to fire
    ``on_settle``. ``schedule(delay_s, callback) -> handle`` arms the countdown (the real worker backs
    this with a restarted ``QTimer``, so ``callback`` lands on the Qt thread); ``cancel(handle)`` disarms
    a still-pending one. Not thread-safe by itself -- the worker only ever calls ``ping`` from the Qt
    thread (the selection observer and the settle callback both run there)."""

    def __init__(
        self, delay_s: float, on_settle: Callable[[], None], *, schedule: Scheduler, cancel: Canceller
    ) -> None:
        self._delay_s = delay_s
        self._on_settle = on_settle
        self._schedule = schedule
        self._cancel = cancel
        self._handle: Any = None

    def ping(self) -> None:
        """Restart the countdown: any pending settle from an earlier ping never fires."""
        if self._handle is not None:
            self._cancel(self._handle)
        self._handle = self._schedule(self._delay_s, self._fire)

    def _fire(self) -> None:
        self._handle = None
        self._on_settle()
