"""``run_native`` through the server: gating, history, undo, failures and the usage counter (fake stand-in)."""

import json
import re
from pathlib import Path

import pytest
from cotaforge.contract import MassRequest

from cotaforge_mcp.guide import guide, server_instructions
from cotaforge_mcp.schemas import TOOLS_BY_NAME, input_schema, visible_tools
from cotaforge_mcp.session import Session
from cotaforge_mcp.usage import usage_path
from tests.conftest import Caller, MakeSession, NativeFake, plate


def volume(call: Caller) -> float:
    return call.session.adapter.mass_properties(MassRequest(operation_id="v")).volume_mm3


@pytest.fixture
def native(make: MakeSession) -> Caller:
    return Caller(make(NativeFake()))


def test_the_tool_exists_only_when_the_cad_has_it(session: Session, native: Caller) -> None:
    assert "run_native" not in visible_tools(session)
    assert "unknown tool" in Caller(session).err("run_native", code="x")
    assert "run_native" in visible_tools(native.session)
    assert input_schema(TOOLS_BY_NAME["run_native"], native.session)["required"] == ["code"]
    assert 'read cad_guide(topic="native")' in server_instructions(native.session)
    assert "no scripting escape hatch" in server_instructions(session)


def test_a_native_step_is_a_step_of_the_history_and_undo_reverts_it(native: Caller) -> None:
    plate(native)
    step = native.ok("run_native", code="box 10", intent="a test block")
    assert step.startswith("ok: s3 native") and "created: Box (Part::Box)" in step and "output: hi" in step
    assert "volume change: +1000 mm3" in step and "UNSAVED" in native.ok("doc", action="status")
    assert [s.id for s in native.session.steps()] == ["s1", "s2", "s3"]
    assert "[s3]" in native.ok("inspect", action="tree")

    native.ok("op", name="box", params={"size": [1, 1, 1]})
    undone = native.ok("undo", to="s2")
    assert undone.startswith("undone: s4 box, s3 native") and volume(native) == pytest.approx(48000)
    redo = native.ok("run_native", code="box 2", id="again")
    assert redo.startswith("ok: again native")
    assert native.ok("undo").startswith("undone: again native")
    assert volume(native) == pytest.approx(48000)


def test_undo_to_the_start_takes_native_steps_with_it(native: Caller) -> None:
    native.ok("doc", action="new", name="n")
    native.ok("run_native", code="box 3")
    native.ok("op", name="box", params={"size": [1, 1, 1]})
    native.ok("run_native", code="box 4")
    back = native.ok("undo", to="start")
    assert "steps now: 0" in back and volume(native) == pytest.approx(0)


def test_a_failing_fragment_changes_nothing_and_says_why(native: Caller) -> None:
    plate(native)
    text = native.err("run_native", code="boom()", intent="try")
    assert text.startswith("error: native_failed")
    assert "s3 native was not applied" in text and "error: RuntimeError (line 1): boom" in text
    assert "hint: no such thing" in text and "state: unchanged" in text and "output: before" in text
    assert [s.id for s in native.session.steps()] == ["s1", "s2"]
    assert native.ok("run_native", code="box 1").startswith("ok: s4 native")  # a failed call still used its auto id


def test_a_fragment_that_changes_nothing_is_not_recorded(native: Caller) -> None:
    plate(native)
    text = native.ok("run_native", code="noop", intent="look")
    assert "changes: none recorded" in text
    assert [s.id for s in native.session.steps()] == ["s1", "s2"]
    assert native.ok("undo").startswith("undone: s2 extrude")  # nothing native in the way


def test_ids_and_arguments_are_checked(native: Caller) -> None:
    plate(native)
    assert "not a valid step id" in native.err("run_native", code="box 1", id="Bad-Id")
    assert "already used" in native.err("run_native", code="box 1", id="s1")
    assert "field: code" in native.err("run_native")
    assert "field: timeout_s" in native.err("run_native", code="box 1", timeout_s=0)


def test_without_a_document_there_is_nothing_to_script(native: Caller) -> None:
    assert "no document is open" in native.err("run_native", code="box 1")


def test_every_call_is_counted_without_the_code(native: Caller, tmp_path: Path) -> None:
    plate(native)
    native.ok("run_native", code="box 10", intent="block")
    native.err("run_native", code="oops", intent="bad")
    native.ok("run_native", code="noop")
    lines = [json.loads(line) for line in usage_path(tmp_path).read_text(encoding="utf-8").splitlines()]
    assert [(line["ok"], line["rolled_back"], line["intent"]) for line in lines] == [
        (True, False, "block"),
        (False, True, "bad"),
        (True, False, ""),
    ]
    assert lines[1]["error"] == "RuntimeError" and lines[0]["code_chars"] == len("box 10")
    assert all(re.fullmatch(r"[0-9a-f]{64}", line["code_sha256"]) and "ts" in line for line in lines)
    assert "box 10" not in usage_path(tmp_path).read_text(encoding="utf-8")


def test_the_guide_topic_explains_the_environment(native: Caller, session: Session) -> None:
    text = guide(native.session, "native")
    assert "ONE step" in text and "rolled back" in text and "Preloaded: doc, App" in text
    assert "not available with fake" in guide(session, "native")
