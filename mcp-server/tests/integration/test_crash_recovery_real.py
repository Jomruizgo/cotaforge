"""A crash of the real FreeCAD worker (simulated by killing the worker THIS test's session spawned, by its own PID):
the next call relaunches it, reopens the last saved file and reports what was lost."""

import os
import signal
import threading
import time
from pathlib import Path

from cotaforge.adapters.freecad import FreeCADAdapter

from cotaforge_mcp.config import Settings
from cotaforge_mcp.server import make_session
from tests.conftest import Caller

SKETCH = {
    "on": {"kind": "plane", "name": "XY"},
    "entities": [{"kind": "rect", "name": "outline", "center": [30, 20], "width": 60, "height": 40}],
}
BOX = 'b = doc.addObject("Part::Box", "Late")\nb.Placement = Placement(Vector(500, 0, 0), Rotation())\n'


def kill_own_worker(adapter: FreeCADAdapter) -> int:
    pid = adapter.worker_pid
    assert pid is not None
    os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    deadline = time.monotonic() + 30
    while adapter.session_alive() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not adapter.session_alive()
    return pid


def test_a_killed_worker_is_relaunched_and_the_last_save_reopened(tmp_path: Path) -> None:
    workspace = tmp_path / "design"
    workspace.mkdir()
    settings = Settings(adapter="freecad", workspace=workspace, home=tmp_path / "home")
    adapter = FreeCADAdapter(log_dir=tmp_path / "logs", timeout_s=120.0)
    session = make_session(settings, adapter)
    assert session.connect_error is None, session.connect_error
    call = Caller(session)
    try:
        call.ok("doc", action="new", name="plate")
        call.ok("op", name="sketch", params=SKETCH)
        call.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 10})
        call.ok("doc", action="save", path="plate.FCStd")
        call.ok("run_native", code=BOX, id="late", intent="a block after the save")
        dead = kill_own_worker(adapter)

        report = call.err("inspect", action="tree")
        assert "the CAD software crashed (its process ended) and was relaunched (headless)" in report
        assert f"reopened: {workspace / 'plate.FCStd'} (save 1; last saved step s2 extrude)" in report
        assert "lost: everything after that save: late native" in report
        assert "undo history does not survive a relaunch" in report
        assert "volume: 24000 mm3" in report
        assert adapter.worker_pid not in (None, dead)

        assert "Late" not in call.ok("inspect", action="tree")  # the unsaved step is really gone
        assert "steps: 2, last s2 extrude" in call.ok("doc", action="status")
        assert call.ok("run_native", code=BOX, id="again", intent="the session works again").startswith("ok:")
    finally:
        session.disconnect()


def test_a_worker_killed_in_the_middle_of_a_call_leaves_no_call_waiting(tmp_path: Path) -> None:
    """Friction: FreeCAD was closed while a long call ran and the next ``view`` waited for ever. The call that was
    running ends with what was lost, and the first calls after it (view, inspect, doc status) are each answered in
    seconds."""
    workspace = tmp_path / "design"
    workspace.mkdir()
    settings = Settings(adapter="freecad", workspace=workspace, home=tmp_path / "home")
    adapter = FreeCADAdapter(log_dir=tmp_path / "logs", timeout_s=120.0)
    session = make_session(settings, adapter)
    assert session.connect_error is None, session.connect_error
    call = Caller(session)
    try:
        call.ok("doc", action="new", name="plate")
        call.ok("op", name="sketch", params=SKETCH)
        call.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 10})
        long_call = threading.Thread(
            target=lambda: running.append(
                Caller(session)(
                    "run_native", code="import time\ntime.sleep(60)", intent="a long fragment", timeout_s=90
                )
            ),
            daemon=True,
        )
        running: list[object] = []
        long_call.start()
        time.sleep(2.0)
        assert long_call.is_alive()  # the fragment is running in the worker
        kill_own_worker(adapter)
        long_call.join(10.0)
        assert not long_call.is_alive(), "the call running in the worker that was killed never came back"
        died = running[0].text  # type: ignore[attr-defined]
        assert "session_lost" in died and "lost: plate was never saved" in died and "resume:" in died

        for tool, arguments in (
            ("view", {"action": "selection"}),
            ("inspect", {"action": "tree"}),
            ("doc", {"action": "status"}),
        ):
            started = time.monotonic()
            reply = Caller(session)(tool, **arguments)
            assert time.monotonic() - started < 20.0, f"{tool} waited {time.monotonic() - started:.0f} s"
            assert reply.text
        assert adapter.session_alive()
    finally:
        session.disconnect()
