"""Helpers for the integration tests that look inside a real FreeCAD worker, save and reopen its documents or end it
the hard way."""

import os
import time
from typing import Any

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import DocumentStamp
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutorState
from cotaforge.ir import parse_plan
from cotaforge.verification.verifier import Verifier
from tests.platform_support import pid_running


def call_hook(
    adapter: FreeCADAdapter, method: str, params: dict[str, Any] | None = None, timeout_s: float = 300.0
) -> Any:
    """One test-hook RPC (the adapter must have been created with ``enable_test_hooks=True``)."""
    rpc = adapter._rpc  # noqa: SLF001 - the hooks are not part of the public contract
    assert rpc is not None
    return rpc.call(method, {"operation_id": method, **(params or {})}, timeout_s=timeout_s)


def kill_worker(adapter: FreeCADAdapter) -> None:
    """The worker dies without closing or saving anything (this test's own child, through its own handle), as after a
    crash."""
    pid = adapter.worker_pid
    assert pid is not None
    adapter.crash_worker()
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if hasattr(os, "WNOHANG"):  # POSIX: reap the zombie, or the PID still looks alive
            try:
                if os.waitpid(pid, os.WNOHANG)[0] == pid:
                    return
            except ChildProcessError:
                return
        elif not pid_running(pid):
            return
        time.sleep(0.1)
    msg = f"worker {pid} did not die"
    raise AssertionError(msg)


def stamp_for(plan_id: str, steps: int, save_seq: int = 1) -> DocumentStamp:
    return DocumentStamp(
        design_id="demo",
        plan_id=plan_id,
        plan_revision=1,
        save_seq=save_seq,
        last_step_index=steps,
        harness_version="0.0.1",
        plan_sha256="a" * 64,
    )


def run_plan(adapter: FreeCADAdapter, plan: dict[str, Any]) -> Executor:
    """Run ``plan`` to the end through the executor, verifier included, on ``adapter``."""
    executor = Executor(parse_plan(plan), adapter, Verifier(), ExecutorConfig())
    executor.start()
    executor.run_to_end()
    assert executor.state is ExecutorState.completed, executor.trace().active_steps()[-1]
    return executor


def assert_close(a: Any, b: Any, where: str = "", rel: float = 1e-9) -> None:
    """Equal JSON-like values, floats within ``rel`` (a reloaded shape can differ in the last ulp)."""
    if isinstance(a, dict):
        assert isinstance(b, dict) and sorted(a) == sorted(b), f"{where}: keys {sorted(a)} vs {sorted(b)}"
        for key in a:
            assert_close(a[key], b[key], f"{where}.{key}", rel)
    elif isinstance(a, list | tuple):
        assert isinstance(b, list | tuple) and len(a) == len(b), f"{where}: {a} vs {b}"
        for index, (x, y) in enumerate(zip(a, b, strict=True)):
            assert_close(x, y, f"{where}[{index}]", rel)
    elif isinstance(a, float) or isinstance(b, float):
        assert abs(a - b) <= rel * max(1.0, abs(a), abs(b)), f"{where}: {a} vs {b}"
    else:
        assert a == b, f"{where}: {a} vs {b}"
