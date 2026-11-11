"""What an adapter whose CAD is not FreeCAD may say by itself: the sentences it kept (``notices``) reach the agent once,
and its crash wording replaces the server's FreeCAD one, whole, through ``guide_topic("note:crash.<key>")``."""

from collections.abc import Sequence
from typing import Any

import pytest
from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import AdapterError, ErrorCode, ErrorInfo

from cotaforge_mcp.guide import guide, server_instructions
from cotaforge_mcp.reply import ToolError
from cotaforge_mcp.session import _native_crash_lines
from tests.conftest import Caller, MakeSession, NativeFake, plate
from tests.unit.test_crash_recovery import CrashingFake


class Chatty(FakeAdapter):
    """Keeps sentences about what it did on its own, and hands them over once."""

    def __init__(self) -> None:
        super().__init__()
        self.kept: list[str] = []

    def notices(self) -> Sequence[str]:
        out, self.kept = self.kept, []
        return out


class Mute(Chatty):
    def notices(self) -> Sequence[str]:
        msg = "a broken adapter"
        raise RuntimeError(msg)


def test_an_adapter_without_notices_changes_nothing_in_a_reply(make: MakeSession) -> None:
    call = Caller(make(FakeAdapter()))
    assert FakeAdapter().notices() == ()
    assert "notice:" not in call.ok("doc", action="new", name="plate")


def test_a_notice_heads_the_next_reply_once_whatever_the_tool(make: MakeSession) -> None:
    adapter = Chatty()
    adapter.kept.append("a known dialog was closed: 'Visual Basic for Applications'")
    call = Caller(make(adapter))
    first = call.ok("doc", action="new", name="plate")  # the first call after connecting says what happened then
    assert first.startswith("notice: a known dialog was closed: 'Visual Basic for Applications'\n")
    assert "notice:" not in call.ok("doc", action="status")  # once each

    adapter.kept.extend(["closed a dialog (1)", "closed a dialog (2)"])
    failing = call.err("op", name="no_such_op", params={})  # an error carries them too
    assert failing.startswith("notice: closed a dialog (1)\nnotice: closed a dialog (2)\n")
    assert "notice:" not in call.ok("doc", action="status")


def test_a_notice_comes_before_the_hand_edits_line_never_instead_of_it(make: MakeSession) -> None:
    adapter = Chatty()
    call = Caller(make(adapter))
    call.ok("doc", action="new", name="plate")
    adapter.kept.append("closed a dialog")
    adapter.kept.append("closed another")
    reply = call.ok("doc", action="status")
    assert reply.splitlines()[:2] == ["notice: closed a dialog", "notice: closed another"]
    assert "document: plate" in reply


def test_notices_are_not_in_the_instructions_the_server_sends_at_start(make: MakeSession) -> None:
    adapter = Chatty()
    adapter.kept.append("a known dialog was closed")
    session = make(adapter)
    assert "dialog" not in server_instructions(session)
    assert adapter.kept == ["a known dialog was closed"]  # asking for the instructions did not drain them


def test_an_adapter_that_cannot_say_its_notices_never_fails_the_call(make: MakeSession) -> None:
    call = Caller(make(Mute()))
    assert call.ok("doc", action="new", name="plate").startswith("document: plate")


# --- crash wording ----------------------------------------------------------------------------------------------------


class OwnWords(CrashingFake):
    """A CAD that is not FreeCAD: its crash wording says so."""

    def guide_topic(self, topic: str) -> str | None:
        return {
            "note:crash.resume": "resume: repeat any call; it relaunches the CAD and the copies are in recovery/",
            "note:crash.native": "the CAD ended with exit code -11 (see its error dialog)",
            "note:crash.busy": "it ends by itself, or when the CAD is closed; the next call relaunches it",
        }.get(topic)

    def run_native(self, request: Any) -> Any:
        if request.code.strip() == "crash":
            self.crash()
            raise AdapterError(ErrorInfo(code=ErrorCode.session_lost, message="exited (code -11)", recoverable=False))
        return NativeFake.run_native(self, request)


def test_the_adapters_crash_wording_replaces_the_servers_freecad_wording(make: MakeSession) -> None:
    call = Caller(make(OwnWords()))
    plate(call)
    died = call.err("run_native", code="crash", intent="takes the CAD down")
    assert "the next call relaunches it and reopens the last saved file" in died
    assert "resume: repeat any call; it relaunches the CAD and the copies are in recovery/" in died
    assert "the CAD ended with exit code -11 (see its error dialog)" in died
    assert "the fragment that was running: run_native" in died  # the server's own first line stays
    assert "FreeCAD" not in died and "TechDraw" not in died


def test_an_adapter_that_says_nothing_after_a_crash_may_say_so(make: MakeSession) -> None:
    class Quiet(OwnWords):
        def guide_topic(self, topic: str) -> str | None:
            return "" if topic == "note:crash.native" else None

    call = Caller(make(Quiet()))
    plate(call)
    died = call.err("run_native", code="crash", intent="takes the CAD down")
    assert died.rstrip().endswith("relaunches FreeCAD, reopens the last saved file and says what it found")
    assert "FreeCAD ended" not in died


def test_the_busy_line_is_the_adapters_when_it_has_one(make: MakeSession) -> None:
    call = Caller(make(OwnWords()))
    error = call.session.busy_error("op", 50.0)
    assert "it ends by itself, or when the CAD is closed; the next call relaunches it" in error.message
    assert "FreeCAD" not in error.message
    plain = Caller(make(FakeAdapter())).session.busy_error("op", 50.0)
    assert plain.message.endswith(
        "it ends by itself, or when FreeCAD is closed: the next call then relaunches it and reopens the last "
        "saved file (what was not saved is lost)\nnext: repeat the call in a moment"
    )


def test_freecads_crash_wording_is_the_servers_default_byte_for_byte() -> None:
    exc = AdapterError(
        ErrorInfo(code=ErrorCode.session_lost, message="x", recoverable=False, details={"exit_code": -11})
    )
    assert _native_crash_lines("s3", "a fragment", ["makeCosmeticLine"], exc) == [
        "the fragment that was running: run_native s3 (a fragment); it works with TechDraw (makeCosmeticLine)",
        "FreeCAD ended with SIGSEGV (a segmentation fault inside FreeCAD)",
        "the TechDraw calls of the fragment are the likely cause (a fault of FreeCAD, not of the script's logic): "
        "do not repeat them as they are; cad_guide(topic=native) lists the known ones and "
        "drawing(action=add, items=[{type: line, ...}]) draws ISO 128 lines without them",
    ]
    assert _native_crash_lines("s3", "", [], exc) == [
        "the fragment that was running: run_native s3",
        "FreeCAD ended with SIGSEGV (a segmentation fault inside FreeCAD)",
    ]
    assert FakeAdapter().guide_topic("note:crash.resume") is None


class OtherCad(NativeFake):
    """A CAD that words the FreeCAD sentences its own way, drops one, and reads Parasolid."""

    def guide_topic(self, topic: str) -> str | None:
        return {
            "sentence:trap.save_conflict": "A fragment that saves with SaveAs3 is not a conflict.",
            "sentence:native.recovery": "",
        }.get(topic)

    def import_suffixes(self) -> tuple[str, ...]:
        return (".x_t",)


def test_an_adapter_words_a_sentence_of_traps_and_native_its_own_way_or_drops_it(make: MakeSession) -> None:
    own, plain = make(OtherCad()), make(NativeFake())
    traps = guide(own, "traps")
    assert "A fragment that saves with SaveAs3 is not a conflict." in traps and "doc.saveAs" not in traps
    assert "doc.saveAs" in guide(plain, "traps")  # the server's text is FreeCAD's until the adapter says otherwise
    assert "TechDraw" not in guide(own, "native") and "TechDraw" in guide(plain, "native")
    assert "rolled back" in guide(own, "native")  # a sentence the adapter did not touch stays
    with pytest.raises(ToolError, match="unknown topic"):  # a sentence key is not a topic the agent can ask for
        guide(own, "sentence:native.recovery")


def test_a_cad_that_reads_parasolid_does_not_tell_the_agent_it_cannot(make: MakeSession) -> None:
    assert "Parasolid (.x_t, .x_b) cannot be read" in guide(make(NativeFake()), "assembly")
    reads = guide(make(OtherCad()), "assembly")
    assert "cannot be read" not in reads and "Parasolid (.x_t) is read like STEP." in reads
