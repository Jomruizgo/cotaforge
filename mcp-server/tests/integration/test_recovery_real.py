"""The real FreeCAD worker ends in the middle of a TechDraw script (killed by its own PID, as a crash would): the
copy of the unsaved work taken before the script is kept and opens with the step ids it had; the engineer's file is
untouched; and the answers, the choice of reviewer and the report of the drawing review, which a crash takes with the
unsaved sheet, come back when the file is opened again."""

import hashlib
import os
import signal
import threading
import time
from pathlib import Path

from cotaforge.adapters.freecad import FreeCADAdapter

from cotaforge_mcp import recovery
from cotaforge_mcp.config import Settings
from cotaforge_mcp.server import make_session
from tests.conftest import Caller

SKETCH = {
    "on": {"kind": "plane", "name": "XY"},
    "entities": [{"kind": "rect", "name": "outline", "center": [30, 20], "width": 60, "height": 40}],
}
TECHDRAW_THAT_HANGS = (
    "import time\n# TechDraw: view.formatGeometricEdge(1, 0, 0.5, (0, 0, 0, 0), True)\ntime.sleep(90)\n"
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def kill_own_worker(adapter: FreeCADAdapter) -> None:
    pid = adapter.worker_pid
    assert pid is not None
    os.kill(pid, getattr(signal, "SIGKILL", signal.SIGTERM))
    deadline = time.monotonic() + 30
    while adapter.session_alive() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not adapter.session_alive()


def start(tmp_path: Path):  # noqa: ANN201 - a (session, adapter, call) triple for the tests below
    workspace = tmp_path / "design"
    workspace.mkdir()
    settings = Settings(adapter="freecad", workspace=workspace, home=tmp_path / "home")
    adapter = FreeCADAdapter(log_dir=tmp_path / "logs", timeout_s=120.0)
    session = make_session(settings, adapter)
    assert session.connect_error is None, session.connect_error
    return workspace, session, adapter, Caller(session)


def test_a_crash_in_a_techdraw_script_keeps_the_unsaved_work_in_a_copy_that_opens_as_it_was(tmp_path: Path) -> None:
    workspace, session, adapter, call = start(tmp_path)
    try:
        call.ok("doc", action="new", name="plate")
        call.ok("op", name="sketch", params=SKETCH)
        call.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 10})
        call.ok("doc", action="save", path="plate.FCStd")
        engineer = workspace / "plate.FCStd"
        before = sha(engineer)
        call.ok("op", name="box", id="late", params={"size": [5, 5, 5], "placement": {"origin": [100, 0, 0]}})

        ran: list[object] = []
        script = threading.Thread(
            target=lambda: ran.append(
                Caller(session)("run_native", code=TECHDRAW_THAT_HANGS, id="td", intent="format an edge", timeout_s=120)
            ),
            daemon=True,
        )
        script.start()
        time.sleep(4.0)  # the copy is taken before the script starts
        assert (workspace / recovery.DIRNAME / "plate.FCStd").is_file()
        kill_own_worker(adapter)
        script.join(20.0)
        assert not script.is_alive()
        died = ran[0].text  # type: ignore[attr-defined]
        assert "the fragment that was running: run_native td (format an edge); it works with TechDraw" in died
        kept = next((workspace / recovery.DIRNAME).glob("plate.crash-*.FCStd"))
        assert f"recovery copy: {kept}" in died and "just before run_native td (format an edge)" in died
        assert sha(engineer) == before  # the engineer's file is exactly as he left it

        report = call.err("doc", action="status")  # relaunch: the last save is reopened and the copy is named again
        assert "was relaunched" in report and f"recovery copy: {kept}" in report
        assert "late" not in call.ok("inspect", action="tree")  # the late box was never saved

        call.ok("doc", action="close", discard=True)
        opened = call.ok("doc", action="open", path=str(kept))
        assert "session restored: step ids and handles kept" in opened and "late: box" in opened
        assert "volume: 24125 mm3" in call.ok("doc", action="status")  # the plate (24000) and the late box (125)
        call.ok("doc", action="save", path="plate.FCStd", overwrite=True)
        assert sha(engineer) != before and "late" in call.ok("inspect", action="tree")  # back in the engineer's file
    finally:
        session.disconnect()


def test_the_review_of_a_drawing_that_a_crash_took_comes_back_when_the_file_is_opened(tmp_path: Path) -> None:
    workspace, session, adapter, call = start(tmp_path)
    try:
        call.ok("doc", action="new", name="plate")
        call.ok("op", name="sketch", params=SKETCH)
        call.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 10})
        call.ok("drawing", action="create", template="A4", title="Plate")
        call.ok("doc", action="save", path="plate.FCStd")  # the sheet is in the file, the review is not yet
        call.ok("memory", action="brief", drawing_review="ingeniero_o_fabricante", text="the engineer chooses")
        call.ok("drawing", action="request_review")
        call.ok("drawing", action="review_choice", by="fabricator")
        item = {"kind": "missing", "where": "front view", "text": "no hole position", "fix": "add the position"}
        call.ok("drawing", action="review_report", by="fabricator", items=[item])
        call.ok("drawing", action="answer", id="fab1.1", text="corregido: the position is on the front view")
        assert (workspace / recovery.DIRNAME / "plate.revision.jsonl").is_file()

        kill_own_worker(adapter)  # FreeCAD ends; the sheet with the review was never saved
        report = call.err("doc", action="status")
        assert "was relaunched" in report
        assert "put back from the review journal" in report, report
        assert "reviewer choice" in report and "1 report" in report and "1 answer" in report
        review = call.ok("drawing", action="review")
        assert "fab1.1" in review and "fabricator" in review
    finally:
        session.disconnect()
