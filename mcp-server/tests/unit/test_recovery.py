# ruff: noqa: E501
"""A crash of the CAD during a TechDraw script loses nothing: a recovery copy of the unsaved work is taken first (and only
then), the reply says what happened and where the copy is, the engineer's file is never touched, and the answers of the
drawing review, who the engineer chose and the reports come back when the file is opened again."""

import json
from pathlib import Path
from typing import Any

import pytest
from cotaforge.contract import AdapterError, ErrorCode, ErrorInfo, SessionInfo

from cotaforge_mcp import recovery
from cotaforge_mcp.recovery import exit_meaning, touches_techdraw
from tests.conftest import Caller, DrawingFake, MakeSession, NativeFake, plate

HEAP_CORRUPTION = 3221226356  # 0xC0000374


class CrashingSheetFake(DrawingFake, NativeFake):
    """The fake whose CAD process can die in the middle of a fragment (exit code 0xC0000374, like the heap corruption
    that ended FreeCAD twice), taking every open document with it. A sheet reopened from an older save has no review.
    Its documents have a suffix of another CAD's: the recovery copy must take the adapter's, not FreeCAD's."""

    document_suffix = ".part"

    def __init__(self) -> None:
        super().__init__()
        self.alive = True
        self.connects = 0
        self.fail_copy: str | None = None

    def crash(self) -> None:
        self.alive = False
        self._documents.clear()
        self._active = None
        for sheet in self.drawings.values():  # what the file saved earlier knew of the review: nothing
            sheet.update(answers={}, choice=None, request=None, reports={}, report_n=0)

    def session_alive(self) -> bool:
        return self.alive

    def connect(self, request: Any) -> SessionInfo:
        self.connects += 1
        self.alive = True
        return super().connect(request)

    def save_document(self, request: Any) -> Any:
        if request.recovery_copy and self.fail_copy is not None:
            raise AdapterError(ErrorInfo(code=ErrorCode.internal, message=self.fail_copy, recoverable=True))
        return super().save_document(request)

    def run_native(self, request: Any) -> Any:
        request = request.model_copy(update={"code": request.code.split("#")[0].strip()})  # the comment names TechDraw
        if request.code.startswith("crash"):
            self.crash()
            raise AdapterError(
                ErrorInfo(
                    code=ErrorCode.session_lost,
                    message="worker exited (code 3221226356) while waiting for run_native",
                    details={"method": "run_native", "exit_code": HEAP_CORRUPTION},
                    recoverable=False,
                )
            )
        return super().run_native(request)


TECHDRAW_NOOP = "noop  # TechDraw: view.formatGeometricEdge(1, 0, 0.5, (0, 0, 0, 0), True)"
TECHDRAW_CRASH = "crash  # TechDraw: view.makeCosmeticLine(a, b, 0) and view.formatGeometricEdge(1, 0, 0.5)"


def crashing(make: MakeSession) -> tuple[Caller, CrashingSheetFake]:
    adapter = CrashingSheetFake()
    return Caller(make(adapter)), adapter


def copies(workspace: Path) -> list[str]:
    folder = workspace / recovery.DIRNAME
    return sorted(p.name for p in folder.glob("*.part")) if folder.is_dir() else []


# --- what takes TechDraw, what a code means ----------------------------------------------------------------------------


def test_what_works_with_techdraw_is_told_from_what_does_not() -> None:
    assert touches_techdraw("box 10") == []
    assert touches_techdraw("sk.addGeometry(Part.LineSegment(a, b))") == []
    code = "import TechDraw\nv = doc.getObject('View')\nv.makeCosmeticLine(a, b, 0)\nv.formatGeometricEdge(1, 0, 1, c, True)"
    assert touches_techdraw(code) == ["TechDraw", "makeCosmeticLine", "formatGeometricEdge"]


def test_the_exit_code_of_a_dead_cad_is_told_in_words() -> None:
    assert exit_meaning(HEAP_CORRUPTION) == "exit code 3221226356 = 0xC0000374: a corrupted heap inside FreeCAD"
    assert exit_meaning(3221225477).endswith("an access violation inside FreeCAD")
    assert exit_meaning(-11).startswith("SIGSEGV")
    assert exit_meaning(1) == "" and exit_meaning(None) == ""
    assert exit_meaning(77) == "exit code 77 = 0x0000004D"


# --- the recovery copy ----------------------------------------------------------------------------------------------------


def test_a_techdraw_fragment_takes_a_copy_of_the_unsaved_work_and_an_ordinary_one_does_not(
    make: MakeSession, tmp_path: Path
) -> None:
    call, cad = crashing(make)
    plate(call)
    call.ok("run_native", code="box 5", id="plain", intent="an ordinary fragment")
    assert cad.copy_calls == [] and copies(tmp_path) == []  # nothing to pay for an ordinary script

    call.ok("run_native", code=TECHDRAW_NOOP, id="td", intent="format an edge")
    assert len(cad.copy_calls) == 1 and copies(tmp_path) == ["plate.part"]
    manifest = json.loads((tmp_path / recovery.DIRNAME / "plate.copy.json").read_text(encoding="utf-8"))
    assert (
        manifest["before"] == "run_native td (format an edge)" and manifest["steps"] == 3 and manifest["source"] is None
    )
    assert call.session.doc_path is None  # the document keeps no file of the copy's: nothing about it changed


def test_a_document_with_no_unsaved_changes_needs_no_copy(make: MakeSession, tmp_path: Path) -> None:
    call, cad = crashing(make)
    plate(call)
    call.ok("doc", action="save", path="plate.fake")
    call.ok("run_native", code=TECHDRAW_NOOP, id="td", intent="format an edge")
    assert cad.copy_calls == []  # the last save is the copy


def test_a_copy_that_cannot_be_made_never_stops_the_fragment(make: MakeSession, tmp_path: Path) -> None:
    call, cad = crashing(make)
    plate(call)
    cad.fail_copy = "disk full"
    reply = call.ok("run_native", code=TECHDRAW_NOOP, id="td", intent="format an edge")
    assert reply.startswith("ok: td native")
    assert "warning: no recovery copy was made (disk full); save the document before more TechDraw scripts" in reply


def test_a_clean_save_makes_the_live_copy_unnecessary(make: MakeSession, tmp_path: Path) -> None:
    call, _ = crashing(make)
    plate(call)
    call.ok("run_native", code=TECHDRAW_NOOP, id="td", intent="format an edge")
    assert copies(tmp_path) == ["plate.part"]
    call.ok("doc", action="save", path="plate.fake")
    assert copies(tmp_path) == []


# --- the crash -------------------------------------------------------------------------------------------------------------


def test_a_crash_in_a_techdraw_fragment_says_what_ran_what_ended_it_and_where_the_work_is(
    make: MakeSession, tmp_path: Path
) -> None:
    call, cad = crashing(make)
    plate(call)
    call.ok("doc", action="save", path="plate.fake")
    saved = (tmp_path / "plate.fake").read_bytes()
    call.ok("run_native", code="box 5", id="after_save", intent="work after the save")

    died = call.err("run_native", code=TECHDRAW_CRASH, id="td", intent="line style 0")
    assert "session_lost" in died
    assert "the fragment that was running: run_native td (line style 0); it works with TechDraw (TechDraw," in died
    assert "makeCosmeticLine, formatGeometricEdge)" in died
    assert "FreeCAD ended with exit code 3221226356 = 0xC0000374: a corrupted heap inside FreeCAD" in died
    assert "drawing(action=add, items=[{type: line, ...}]) draws ISO 128 lines without them" in died
    kept = next((tmp_path / recovery.DIRNAME).glob("plate.crash-*.part"))
    assert f"recovery copy: {kept}" in died and "just before run_native td (line style 0)" in died
    assert "it holds plate with the work that was not saved: 3 step(s), the last after_save native" in died
    assert 'doc(action="open", path="' in died and "overwrite=true" in died
    assert "lost: nothing that the recovery copy below does not hold" in died
    assert (tmp_path / "plate.fake").read_bytes() == saved  # the engineer's file is exactly as it was

    report = call.err("doc", action="status")  # the next call relaunches the CAD and points at the same copy
    assert "was relaunched" in report and f"recovery copy: {kept}" in report
    assert cad.connects == 2
    assert not list((tmp_path / recovery.DIRNAME).glob("plate.part"))  # the live copy is the kept one now


def test_the_kept_copy_opens_with_the_step_ids_of_the_work_that_was_not_saved(
    make: MakeSession, tmp_path: Path
) -> None:
    call, _ = crashing(make)
    plate(call)
    call.ok("doc", action="save", path="plate.fake")
    call.ok("run_native", code="box 5", id="after_save", intent="work after the save")
    call.err("run_native", code=TECHDRAW_CRASH, id="td", intent="line style 0")
    call.err("doc", action="status")  # relaunched, reopened from the last save (2 steps)
    kept = next((tmp_path / recovery.DIRNAME).glob("plate.crash-*.part"))

    call.ok("doc", action="close", discard=True)
    opened = call.ok("doc", action="open", path=str(kept))
    assert "session restored: step ids and handles kept" in opened
    assert "after_save: native" in opened and "steps: 3" in call.ok("doc", action="status")
    call.ok("doc", action="save", path="plate.fake", overwrite=True)  # back in the engineer's file
    assert "steps: 3" in call.ok("doc", action="status") and "UNSAVED" not in call.ok("doc", action="status")


def test_a_document_that_was_never_saved_is_recovered_from_the_copy(make: MakeSession, tmp_path: Path) -> None:
    call, _ = crashing(make)
    plate(call)
    died = call.err("run_native", code=TECHDRAW_CRASH, id="td", intent="line style 0")
    assert "lost: nothing that the recovery copy below does not hold" in died
    report = call.err("doc", action="status")
    assert "no saved document: plate was never saved; no document is open" in report
    kept = next((tmp_path / recovery.DIRNAME).glob("plate.crash-*.part"))
    assert f'doc(action="open", path="{kept}")' in report
    opened = call.ok("doc", action="open", path=str(kept))
    assert "session restored" in opened and "steps: 2" in call.ok("doc", action="status")


def test_a_later_copy_does_not_replace_the_one_a_crash_kept(make: MakeSession, tmp_path: Path) -> None:
    call, _ = crashing(make)
    plate(call)
    call.err("run_native", code=TECHDRAW_CRASH, id="td", intent="line style 0")
    call.err("doc", action="status")
    call.ok("doc", action="new", name="plate")
    call.ok("op", name="box", params={"size": [1, 1, 1]})
    call.ok("run_native", code=TECHDRAW_NOOP, id="td2", intent="another TechDraw script")
    names = copies(tmp_path)
    assert len(names) == 2 and any(".crash-" in n for n in names) and "plate.part" in names


def test_a_crash_without_a_techdraw_fragment_has_no_copy_to_offer(make: MakeSession, tmp_path: Path) -> None:
    call, cad = crashing(make)
    plate(call)
    cad.crash()
    report = call.err("doc", action="status")
    assert "no saved document: plate was never saved, its 2 step(s) are lost" in report
    assert "recovery copy" not in report and copies(tmp_path) == []


# --- the journal of the drawing review ---------------------------------------------------------------------------------------


def review_a_sheet(call: Caller) -> None:
    call.ok("drawing", action="create", template="A4", title="Plate")
    call.ok("memory", action="brief", drawing_review="engineer_or_fabricator", text="the engineer chooses who reviews")
    call.ok("drawing", action="request_review")
    call.ok("drawing", action="review_choice", by="fabricator")
    call.ok(
        "drawing",
        action="review_report",
        by="fabricator",
        items=[{"kind": "missing", "where": "front view", "text": "no hole position", "fix": "add the position"}],
    )
    call.ok("drawing", action="answer", id="fab1.1", text="corregido: the position is on the front view now")


def test_the_answers_the_choice_and_the_reports_are_kept_outside_the_document(
    make: MakeSession, tmp_path: Path
) -> None:
    call, _ = crashing(make)
    plate(call)
    review_a_sheet(call)
    journal = (tmp_path / recovery.DIRNAME / "plate.revision.jsonl").read_text(encoding="utf-8").splitlines()
    actions = [json.loads(line)["request"]["action"] for line in journal]
    assert actions == ["request_review", "review_choice", "review_report", "answer"]


def test_a_crash_takes_the_review_with_the_unsaved_sheet_and_the_next_open_puts_it_back(
    make: MakeSession, tmp_path: Path
) -> None:
    call, cad = crashing(make)
    plate(call)
    call.ok("doc", action="save", path="plate.fake")
    review_a_sheet(call)  # the sheet holds it all, unsaved
    call.err("run_native", code=TECHDRAW_CRASH, id="td", intent="line style 0")
    assert cad.drawings["Drawing"]["answers"] == {} and cad.drawings["Drawing"]["choice"] is None  # gone with the CAD

    report = call.err("doc", action="status")  # relaunch: the last save is reopened and the journal is read
    assert "was relaunched" in report
    assert "sheet Drawing: put back from the review journal (the unsaved document lost them): " in report
    assert "review request, reviewer choice, 1 report, 1 answer" in report
    sheet = cad.drawings["Drawing"]
    assert sheet["choice"] == {"who": "fabricator"} and sheet["reports"]["fabricator"]["remarks"][0].id == "fab1.1"
    assert sheet["answers"]["fab1.1"].text.startswith("corregido")
    assert "UNSAVED" in call.ok("doc", action="status")  # they are in the sheet again, not saved yet


def test_nothing_is_put_back_that_the_sheet_already_has(make: MakeSession, tmp_path: Path) -> None:
    call, _ = crashing(make)
    plate(call)
    call.ok("doc", action="save", path="plate.fake")
    review_a_sheet(call)
    call.ok("doc", action="save")
    call.ok("doc", action="close")
    opened = call.ok("doc", action="open", path="plate.fake")
    assert "review journal" not in opened


def test_the_journal_follows_a_document_saved_for_the_first_time(make: MakeSession, tmp_path: Path) -> None:
    call, _ = crashing(make)
    plate(call)
    review_a_sheet(call)  # kept under the name the document was made with
    call.ok("doc", action="save", path="bracket.fake")
    folder = tmp_path / recovery.DIRNAME
    assert not (folder / "plate.revision.jsonl").exists() and (folder / "bracket.revision.jsonl").is_file()


@pytest.mark.parametrize("by", ["engineer", "fabricator"])
def test_the_engineer_choice_is_kept_whoever_he_chose(make: MakeSession, tmp_path: Path, by: str) -> None:
    call, _ = crashing(make)
    plate(call)
    call.ok("drawing", action="create", template="A4", title="Plate")
    call.ok("memory", action="brief", drawing_review="engineer_or_fabricator", text="the engineer chooses")
    call.ok("drawing", action="review_choice", by=by)
    lines = (tmp_path / recovery.DIRNAME / "plate.revision.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["request"]["by"] == by
