"""``worker/debounce.py``'s coalescing policy, in isolation: no FreeCAD/Qt needed, backed by a fake
scheduler that runs synchronously under the test's own control (a real ``QTimer`` needs a Qt event loop,
which this suite does not have)."""

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"


def _load_debounce() -> Any:
    spec = importlib.util.spec_from_file_location("worker_debounce", WORKER_DIR / "debounce.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


debounce = _load_debounce()


class FakeScheduler:
    """A fake, manually-advanced clock: ``schedule`` records a pending (deadline, callback) and returns a
    handle; ``advance`` fires every pending callback whose deadline has passed, in deadline order (ties in
    call order) -- exactly the "restart, latest wins" semantics a real ``QTimer`` gives for free."""

    def __init__(self) -> None:
        self.now = 0.0
        self._pending: dict[int, tuple[float, Any]] = {}
        self._next_handle = 0

    def schedule(self, delay_s: float, callback: Any) -> int:
        self._next_handle += 1
        handle = self._next_handle
        self._pending[handle] = (self.now + delay_s, callback)
        return handle

    def cancel(self, handle: int) -> None:
        self._pending.pop(handle, None)

    def advance(self, seconds: float) -> None:
        self.now += seconds
        due = [(deadline, h, cb) for h, (deadline, cb) in self._pending.items() if deadline <= self.now]
        for _, h, cb in sorted(due):
            self._pending.pop(h, None)
            cb()

    @property
    def pending_count(self) -> int:
        return len(self._pending)


def test_a_single_ping_fires_after_the_delay() -> None:
    scheduler = FakeScheduler()
    settled = []
    d = debounce.Debouncer(
        0.15, lambda: settled.append(scheduler.now), schedule=scheduler.schedule, cancel=scheduler.cancel
    )
    d.ping()
    scheduler.advance(0.10)
    assert settled == []  # not yet
    scheduler.advance(0.05)
    assert settled == pytest.approx([0.15])


def test_a_burst_of_pings_within_the_window_fires_only_once() -> None:
    """Drag-selecting fires the observer once per sub-element; only the last ping in the burst survives."""
    scheduler = FakeScheduler()
    settled = []
    d = debounce.Debouncer(
        0.15, lambda: settled.append(scheduler.now), schedule=scheduler.schedule, cancel=scheduler.cancel
    )
    for _ in range(5):
        d.ping()
        scheduler.advance(0.05)  # well within the 0.15s window: each ping restarts the countdown
    assert settled == []  # still coalescing
    scheduler.advance(0.15)  # now let the last ping's countdown actually elapse
    assert settled == pytest.approx([0.40])  # last ping was at t=0.20; it fires at 0.20 + 0.15 = 0.40


def test_pings_spaced_beyond_the_window_each_settle_separately() -> None:
    scheduler = FakeScheduler()
    settled = []
    d = debounce.Debouncer(
        0.15, lambda: settled.append(scheduler.now), schedule=scheduler.schedule, cancel=scheduler.cancel
    )
    d.ping()
    scheduler.advance(0.15)
    d.ping()
    scheduler.advance(0.15)
    assert settled == pytest.approx([0.15, 0.30])


def test_only_one_pending_schedule_exists_at_a_time() -> None:
    """Every ``ping`` before the countdown elapses cancels the previous pending schedule -- the scheduler
    never accumulates one entry per ping."""
    scheduler = FakeScheduler()
    d = debounce.Debouncer(0.15, lambda: None, schedule=scheduler.schedule, cancel=scheduler.cancel)
    for _ in range(10):
        d.ping()
    assert scheduler.pending_count == 1
