"""A crash of the CAD software (its process ends) on the fake stand-in: the next call relaunches it, reopens the last
saved file and reports what was lost instead of running; a relaunch that fails is reported and tried again by a later
call, no sooner than a wait that doubles."""

from pathlib import Path
from typing import Any

from cotaforge.contract import AdapterError, ErrorCode, ErrorInfo, SessionInfo

from tests.conftest import Caller, MakeSession, NativeFake, plate


class CrashingFake(NativeFake):
    """The fake whose "process" can die: ``crash`` drops every open document (they lived in that process) and
    ``session_alive`` reports it; ``connect`` is the relaunch, and fails with ``relaunch_error`` when one is set.
    ``run_native(code="crash")`` dies in the middle of the call, like a fragment that takes the CAD down."""

    def __init__(self) -> None:
        super().__init__()
        self.alive = True
        self.connects = 0
        self.attempts = 0
        self.relaunch_error: str | None = None

    def crash(self) -> None:
        self.alive = False
        self._documents.clear()
        self._active = None

    def session_alive(self) -> bool:
        return self.alive

    def connect(self, request: Any) -> SessionInfo:
        self.attempts += 1
        if self.connects and self.relaunch_error is not None:
            raise AdapterError(ErrorInfo(code=ErrorCode.session_lost, message=self.relaunch_error, recoverable=False))
        self.connects += 1
        self.alive = True
        return super().connect(request)

    def run_native(self, request: Any) -> Any:
        if request.code.strip() == "crash":
            self.crash()
            raise AdapterError(
                ErrorInfo(code=ErrorCode.session_lost, message="worker exited (code -11)", recoverable=False)
            )
        return super().run_native(request)


def crashing(make: MakeSession) -> tuple[Caller, CrashingFake]:
    adapter = CrashingFake()
    return Caller(make(adapter)), adapter


def test_the_next_call_relaunches_reopens_the_last_save_and_names_what_was_lost(
    make: MakeSession, tmp_path: Path
) -> None:
    call, cad = crashing(make)
    plate(call)
    call.ok("doc", action="save", path="plate.fake")
    call.ok("run_native", code="box 10", id="block", intent="a block after the save")
    cad.crash()

    report = call.err("inspect", action="tree")
    assert report.startswith("error: cad_relaunched")
    assert "the CAD software crashed (its process ended) and was relaunched" in report
    assert "your inspect call was NOT run" in report
    assert f"reopened: {tmp_path / 'plate.fake'} (save 1; last saved step s2 extrude)" in report
    assert "lost: everything after that save: block native" in report
    assert "undo history does not survive a relaunch" in report
    assert "volume: 48000 mm3" in report  # the state of the reopened file, measured
    assert cad.connects == 2

    status = call.ok("doc", action="status")  # the session goes on from the save
    assert "steps: 2, last s2 extrude" in status and "UNSAVED" not in status
    assert "made before this document was reopened" in call.err("undo")


def test_a_tool_that_does_not_need_the_cad_still_surfaces_the_crash(make: MakeSession) -> None:
    """Once the start is over, every tool call checks the CAD process, as before the background start."""
    call, cad = crashing(make)
    plate(call)
    cad.crash()
    assert call.err("memory", action="index").startswith("error: cad_relaunched")
    assert cad.connects == 2


def test_a_saved_document_without_unsaved_changes_loses_nothing(make: MakeSession) -> None:
    call, cad = crashing(make)
    plate(call)
    call.ok("doc", action="save", path="plate.fake")
    cad.crash()
    report = call.err("doc", action="status")
    assert "lost: nothing, the document had no unsaved changes" in report
    assert "document: plate" in call.ok("doc", action="status")


def test_a_document_that_was_never_saved_is_lost_and_said_so(make: MakeSession) -> None:
    call, cad = crashing(make)
    plate(call)
    cad.crash()
    report = call.err("op", name="box", params={"size": [1, 1, 1]})
    assert "no saved document: plate was never saved, its 2 step(s) are lost; no document is open" in report
    assert 'doc(action="new"' in report
    assert "document: none" in call.ok("doc", action="status")
    assert call.ok("doc", action="new", name="again").startswith("document: again")


def test_with_no_document_open_nothing_is_lost(make: MakeSession) -> None:
    call, cad = crashing(make)
    cad.crash()
    assert "no document was open: nothing was lost" in call.err("doc", action="status")
    assert "document: none" in call.ok("doc", action="status")


def test_a_crash_inside_a_call_says_the_next_call_relaunches(make: MakeSession) -> None:
    call, _ = crashing(make)
    plate(call)
    died = call.err("run_native", code="crash", intent="takes the CAD down")
    assert "session_lost" in died and "the next call relaunches it and reopens the last saved file" in died
    assert "restart the server" not in died
    assert "lost: plate was never saved; its 2 step(s) went with the process" in died  # what went with it
    assert "resume: repeat any call; it relaunches FreeCAD" in died  # and how to go on
    assert "was relaunched" in call.err("doc", action="status")


def test_a_crash_reopens_the_assembly_with_its_parts_and_names_the_other_documents_it_lost(
    make: MakeSession, tmp_path: Path
) -> None:
    call, cad = crashing(make)
    call.ok("doc", action="new", name="cube")
    call.ok("op", name="box", id="cube_box", params={"size": [10, 10, 10]})
    call.ok("doc", action="save", path="cube.fake")
    call.ok("doc", action="new", name="scratch")
    call.ok("op", name="box", params={"size": [1, 1, 1]})  # never saved
    call.ok("doc", action="new", name="asm", kind="assembly")
    call.ok("doc", action="save", path="asm.fake")
    call.ok("op", name="insert_component", id="a", params={"part": "cube", "placement": {}})
    call.ok("doc", action="save")
    cad.crash()

    report = call.err("doc", action="status")

    assert f"reopened: {tmp_path / 'asm.fake'}" in report
    assert "also open and gone with the process: scratch (UNSAVED changes lost)" in report
    assert "cube" not in report.split("also open and gone with the process:")[1].split("(what was saved")[0]
    status = call.ok("doc", action="status")
    assert "documents: 2 open" in status and "asm (assembly) [active]" in status and "cube (part)" in status


def test_a_failed_relaunch_is_reported_with_what_to_do_and_waits_before_the_next_try(
    make: MakeSession, tmp_path: Path
) -> None:
    call, cad = crashing(make)
    clock = [1000.0]
    call.session._clock = lambda: clock[0]
    plate(call)
    call.ok("doc", action="save", path="plate.fake")
    cad.relaunch_error = "FreeCAD root not found"
    cad.crash()

    first = call.err("doc", action="status")
    assert first.startswith("error: cad_relaunch_failed")
    assert "could not be relaunched: FreeCAD root not found" in first
    assert f"{tmp_path / 'plate.fake'} keeps the last save" in first
    assert "relaunches it again" in first and "restart the cad server" in first
    clock[0] += 10
    second = call.err("doc", action="status")  # too soon: it answers the same, without asking the CAD
    assert second.startswith(first) and "next attempt: the first call in 20 s" in second
    assert call.err("op", name="box", params={"size": [1, 1, 1]}).startswith("error: cad_relaunch_failed")
    assert cad.connects == 1 and cad.attempts == 2  # one attempt so far, none for the calls in between


def test_the_relaunch_is_tried_again_by_the_next_call_after_the_wait_and_the_wait_doubles(
    make: MakeSession, tmp_path: Path
) -> None:
    call, cad = crashing(make)
    clock = [1000.0]
    call.session._clock = lambda: clock[0]
    plate(call)
    call.ok("doc", action="save", path="plate.fake")
    call.ok("run_native", code="box 10", id="block", intent="a block after the save")
    cad.relaunch_error = "the crashed instance still shows its error dialog"
    cad.crash()

    assert "could not be relaunched" in call.err("doc", action="status")  # the first try: waits 30 s
    clock[0] += 30
    again = call.err("doc", action="status")  # the second: fails too, waits 60 s
    assert "could not be relaunched" in again and "the first call after 60 s" in again
    assert cad.attempts == 3  # the crashing connect, and two relaunches
    clock[0] += 59
    assert "next attempt: the first call in 1 s" in call.err("doc", action="status")
    assert cad.attempts == 3

    cad.relaunch_error = None  # the engineer closed the dialog
    clock[0] += 1
    report = call.err("doc", action="status")
    assert report.startswith("error: cad_relaunched") and "was relaunched" in report
    # it reports what the dead session held, not the empty state the failed tries left behind
    assert f"reopened: {tmp_path / 'plate.fake'}" in report
    assert "lost: everything after that save: block native" in report
    assert "document: plate" in call.ok("doc", action="status")
    assert cad.connects == 2 and call.session._relaunch_failed is None


def test_a_relaunch_that_fails_again_never_waits_more_than_the_ceiling(make: MakeSession) -> None:
    call, cad = crashing(make)
    clock = [0.0]
    call.session._clock = lambda: clock[0]
    plate(call)
    cad.relaunch_error = "still broken"
    cad.crash()
    waits = []
    for _ in range(7):
        call.err("doc", action="status")  # one try per call once the wait is over
        waits.append(call.session._relaunch_retry_at - clock[0])
        clock[0] = call.session._relaunch_retry_at
    assert waits == [30, 60, 120, 240, 300, 300, 300]
