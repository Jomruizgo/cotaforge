# ruff: noqa: E501
"""The ``channel`` tool: the drafting session asks, the design session answers, over files of the design folder.

Everything runs in a temporary folder; two sessions are two callers (and, for the race, two processes) on one folder.
"""

import json
import os
import shlex
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import pytest

from cotaforge_mcp import channel_tools, drawing_window
from cotaforge_mcp.config import Settings
from cotaforge_mcp.reply import Reply, ToolError
from cotaforge_mcp.schemas import TOOLS_BY_NAME, ChannelArgs, input_schema, visible_tools
from tests.conftest import BareFake, Caller, DrawingFake, MakeSession

NAME = "soporte"


def call(workspace: Path, **arguments: object) -> str:
    reply: Reply = channel_tools.run(workspace, ChannelArgs.model_validate(arguments))
    assert not reply.is_error
    return reply.text


def refused(workspace: Path, **arguments: object) -> ToolError:
    with pytest.raises(ToolError) as caught:
        channel_tools.run(workspace, ChannelArgs.model_validate(arguments))
    return caught.value


def ask(workspace: Path, text: str = "¿Lleva ajuste?", **more: object) -> str:
    return call(workspace, action="ask", role="drafter", name=NAME, text=text, proposal="H7", **more)


def log(workspace: Path) -> list[dict[str, object]]:
    path = workspace / "drawings" / f"{NAME}-questions.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


# --- the log: order, numbers and the readable copy -------------------------------------------------------------


def test_events_are_numbered_in_order_and_questions_have_their_own_number(tmp_path: Path) -> None:
    ask(tmp_path, "primera")
    ask(tmp_path, "segunda")
    call(tmp_path, action="reply", role="designer", name=NAME, q=1, by="designer", text="sí, H7")
    call(tmp_path, action="done", role="drafter", name=NAME)
    events = log(tmp_path)
    assert [e["n"] for e in events] == [1, 2, 3, 4]
    assert [e["kind"] for e in events] == ["ask", "ask", "reply", "done"]
    assert [e.get("q") for e in events] == [1, 2, 1, None]
    assert events[0]["proposal"] == "H7" and events[2]["via"] == "designer" and events[2]["by"] == "designer"


def test_the_readable_copy_follows_every_event_and_marks_what_is_open(tmp_path: Path) -> None:
    ask(tmp_path, "primera")
    ask(tmp_path, "segunda")
    call(tmp_path, action="reply", role="designer", name=NAME, q=1, by="designer", text="sí, H7")
    text = (tmp_path / "drawings" / f"{NAME}-questions.md").read_text(encoding="utf-8")
    assert "## 1. primera" in text and "Respuesta del diseñador" in text and "sí, H7" in text
    assert "## 2. segunda" in text and "[abierta]" in text
    assert "terminado" not in text
    call(tmp_path, action="done", role="drafter", name=NAME)
    assert "Dibujo terminado" in (tmp_path / "drawings" / f"{NAME}-questions.md").read_text(encoding="utf-8")


def test_a_half_written_last_line_is_not_there_yet(tmp_path: Path) -> None:
    ask(tmp_path)
    path = tmp_path / "drawings" / f"{NAME}-questions.jsonl"
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"n": 2, "kind": "rep')  # a writer is in the middle of its line
    assert [e["n"] for e in channel_tools.read_events(path)] == [1]


def test_list_says_what_is_open_and_which_drawings_have_a_channel(tmp_path: Path) -> None:
    assert "none" in call(tmp_path, action="list")
    ask(tmp_path)
    assert NAME in call(tmp_path, action="list")
    assert "1 open, 0 answered" in call(tmp_path, action="list", name=NAME)


# --- identity --------------------------------------------------------------------------------------------------


def test_the_dibujante_never_answers_its_own_question_and_only_relays_the_engineer(tmp_path: Path) -> None:
    ask(tmp_path)
    error = refused(tmp_path, action="reply", role="drafter", name=NAME, q=1, by="designer", text="H7")
    assert error.code == "not_allowed" and "never answers its own question" in error.message
    call(tmp_path, action="reply", role="drafter", name=NAME, q=1, by="engineer", text="H7, sí")
    last = log(tmp_path)[-1]
    assert (last["by"], last["via"]) == ("engineer", "drafter")


def test_a_reply_from_the_design_session_is_the_designers_not_the_engineers(tmp_path: Path) -> None:
    ask(tmp_path)
    error = refused(tmp_path, action="reply", role="designer", name=NAME, q=1, by="engineer", text="H7")
    assert error.code == "not_allowed" and "drawing session" in error.message


def test_only_the_dibujante_asks_and_finishes(tmp_path: Path) -> None:
    assert refused(tmp_path, action="ask", role="designer", name=NAME, text="x").code == "not_allowed"
    assert refused(tmp_path, action="done", role="designer", name=NAME).code == "not_allowed"
    assert refused(tmp_path, action="ask", name=NAME, text="x").field_name == "role"


def test_the_plain_spelling_of_the_designer_is_accepted(tmp_path: Path) -> None:
    ask(tmp_path)
    call(tmp_path, action="reply", role="disenador", name=NAME, q=1, by="disenador", text="sí")
    assert log(tmp_path)[-1]["by"] == "designer"


def test_a_reply_needs_a_question_that_exists_and_a_channel_that_is_open(tmp_path: Path) -> None:
    ask(tmp_path)
    assert (
        refused(tmp_path, action="reply", role="designer", name=NAME, q=9, by="designer", text="x").code == "not_found"
    )
    call(tmp_path, action="done", role="drafter", name=NAME)
    assert refused(tmp_path, action="reply", role="designer", name=NAME, q=1, by="designer", text="x").code == "closed"
    ask(tmp_path, "otra")  # a new ask opens the channel again
    call(tmp_path, action="reply", role="designer", name=NAME, q=2, by="designer", text="x")


def test_the_name_cannot_leave_the_planos_folder(tmp_path: Path) -> None:
    for bad in ("../fuera", "a/b", "", ".oculto", "a b"):
        assert refused(tmp_path, action="ask", role="drafter", name=bad, text="x").field_name == "name"
    assert not (tmp_path.parent / "fuera-questions.jsonl").exists()


# --- wait ------------------------------------------------------------------------------------------------------


class Clock:
    """A clock that only moves when somebody sleeps, so a wait of minutes takes no time."""

    def __init__(self) -> None:
        self.now = 0.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def wait(workspace: Path, role: str, since: int = 0, **more: object) -> tuple[str, Clock]:
    clock = Clock()
    args = ChannelArgs.model_validate({"action": "wait", "role": role, "name": NAME, "since": since, **more})
    return channel_tools.run(workspace, args, clock=clock, sleep=clock.sleep).text, clock


def test_a_wait_returns_at_once_with_what_the_other_session_wrote(tmp_path: Path) -> None:
    ask(tmp_path, "primera")
    text, clock = wait(tmp_path, "designer")
    assert "PREGUNTA 1" in text and "primera" in text and "propuesta del dibujante: H7" in text
    assert "since=1" in text and clock.slept == []
    assert "answer each question" in text


def test_a_wait_does_not_return_what_its_own_session_wrote(tmp_path: Path) -> None:
    ask(tmp_path)
    text, clock = wait(tmp_path, "drafter", timeout_s=5)
    assert text.startswith("nothing yet") and "since=0" in text and sum(clock.slept) >= 5


def test_the_engineers_answer_given_in_the_drawing_session_reaches_the_designer_with_a_reminder(tmp_path: Path) -> None:
    ask(tmp_path)
    call(tmp_path, action="reply", role="drafter", name=NAME, q=1, by="engineer", text="No, es libre")
    text, _ = wait(tmp_path, "designer", since=1)
    assert "RESPUESTA a 1 del ingeniero" in text and "No, es libre" in text
    assert "not a decision yet" in text and "assumptions-and-decisions.md" in text
    again, _ = wait(tmp_path, "drafter", since=1)  # the drafter wrote it: it hears nothing back
    assert again.startswith("nothing yet")


def test_the_dibujante_hears_the_designers_answer_and_the_designer_hears_done(tmp_path: Path) -> None:
    ask(tmp_path)
    call(tmp_path, action="reply", role="designer", name=NAME, q=1, by="designer", text="H7")
    text, _ = wait(tmp_path, "drafter", since=1)
    assert "RESPUESTA a 1 del diseñador" in text and "since=2" in text
    call(tmp_path, action="done", role="drafter", name=NAME, text="hoja lista")
    text, _ = wait(tmp_path, "designer", since=2)
    assert "TERMINADO" in text and "stop waiting" in text and "next:" not in text


def test_a_wait_with_nothing_new_gives_up_at_its_limit_and_says_how_to_call_again(tmp_path: Path) -> None:
    text, clock = wait(tmp_path, "designer", since=4, timeout_s=30)
    assert text.startswith("nothing yet") and 'channel(action="wait", role="designer"' in text and "since=4" in text
    assert 30 <= sum(clock.slept) < 31


def test_the_limit_of_a_wait_is_bounded(tmp_path: Path) -> None:
    _, clock = wait(tmp_path, "designer", timeout_s=99999)
    assert channel_tools.WAIT_MAX_S <= sum(clock.slept) < channel_tools.WAIT_MAX_S + 1
    _, clock = wait(tmp_path, "designer")
    assert sum(clock.slept) < channel_tools.WAIT_DEFAULT_S + 1


def test_a_wait_ends_when_the_client_gives_the_call_up(tmp_path: Path) -> None:
    gave_up = threading.Event()
    gave_up.set()
    args = ChannelArgs.model_validate({"action": "wait", "role": "designer", "name": NAME})
    assert channel_tools.run(tmp_path, args, gave_up).text.startswith("nothing yet")


def test_a_wait_in_one_thread_wakes_when_another_session_writes(tmp_path: Path) -> None:
    out: list[str] = []
    args = ChannelArgs.model_validate({"action": "wait", "role": "designer", "name": NAME, "timeout_s": 20})
    waiter = threading.Thread(target=lambda: out.append(channel_tools.run(tmp_path, args).text))
    started = time.monotonic()
    waiter.start()
    time.sleep(0.3)
    ask(tmp_path, "¿y esta?")
    waiter.join(10)
    assert out and "¿y esta?" in out[0] and time.monotonic() - started < 10


# --- two processes on one folder, a stale lock -----------------------------------------------------------------

WRITER = """
import sys
from pathlib import Path
from cotaforge_mcp import channel_tools
from cotaforge_mcp.schemas import ChannelArgs
workspace, role, count = Path(sys.argv[1]), sys.argv[2], int(sys.argv[3])
for i in range(count):
    args = ChannelArgs.model_validate({"action": "ask", "role": "drafter", "name": "soporte", "text": f"{role}-{i}"})
    channel_tools.run(workspace, args)
"""


def test_two_processes_on_the_same_folder_never_take_the_same_number(tmp_path: Path) -> None:
    env = {**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}
    procs = [
        subprocess.Popen([sys.executable, "-c", WRITER, str(tmp_path), name, "15"], env=env) for name in ("a", "b", "c")
    ]
    for proc in procs:
        assert proc.wait(timeout=120) == 0
    events = log(tmp_path)
    assert [e["n"] for e in events] == list(range(1, 46))
    assert sorted(e["q"] for e in events) == list(range(1, 46))
    assert len({e["text"] for e in events}) == 45, "no event was lost or written twice"
    assert not (tmp_path / "drawings" / f"{NAME}-questions.lock").exists()


def test_a_lock_left_by_a_dead_process_is_taken_over(tmp_path: Path) -> None:
    lock = tmp_path / "drawings" / f"{NAME}-questions.lock"
    lock.parent.mkdir()
    lock.write_text("99999", encoding="utf-8")
    old = time.time() - channel_tools.LOCK_STALE_S - 5
    os.utime(lock, (old, old))
    ask(tmp_path)
    assert len(log(tmp_path)) == 1 and not lock.exists()


def test_a_lock_held_by_a_live_writer_is_waited_for_and_then_reported(tmp_path: Path) -> None:
    lock = tmp_path / "drawings" / f"{NAME}-questions.lock"
    lock.parent.mkdir()
    lock.write_text("1", encoding="utf-8")  # fresh: somebody is writing
    with pytest.raises(ToolError) as caught, channel_tools._locked(lock, wait_s=0.2):
        pass
    assert caught.value.code == "busy" and lock.exists(), "a lock that is not ours stays"


# --- an answer is not a decision --------------------------------------------------------------------------------


def status_of(workspace: Path, q: int) -> str:
    return str(
        channel_tools.questions(channel_tools.read_events(workspace / "drawings" / f"{NAME}-questions.jsonl"))[q][
            "status"
        ]
    )


def test_a_question_goes_from_open_to_answered_to_decided(tmp_path: Path) -> None:
    ask(tmp_path)
    assert status_of(tmp_path, 1) == "open"
    call(tmp_path, action="reply", role="drafter", name=NAME, q=1, by="engineer", text="H7")
    assert status_of(tmp_path, 1) == "answered"
    call(tmp_path, action="decide", role="designer", name=NAME, q=1, by="engineer", text="H7, confirmado")
    assert status_of(tmp_path, 1) == "decided"
    last = log(tmp_path)[-1]
    assert (last["kind"], last["by"], last["via"]) == ("decide", "engineer", "designer")


def test_the_designer_disputes_the_engineers_answer_and_the_drafter_holds_the_point(tmp_path: Path) -> None:
    ask(tmp_path)
    call(tmp_path, action="reply", role="drafter", name=NAME, q=1, by="engineer", text="Sin ajuste")
    call(tmp_path, action="dispute", role="designer", name=NAME, q=1, text="recibe un pasador: H7 (ISO 286)")
    assert status_of(tmp_path, 1) == "disputed"
    heard, _ = wait(tmp_path, "drafter", since=2)
    assert "OBJECIÓN a 1" in heard and "hold that point" in heard
    assert "disputed" in call(tmp_path, action="list", name=NAME)


def test_a_disputed_question_is_decided_only_with_the_objection_on_record(tmp_path: Path) -> None:
    ask(tmp_path)
    call(tmp_path, action="reply", role="drafter", name=NAME, q=1, by="engineer", text="Sin ajuste")
    call(tmp_path, action="dispute", role="designer", name=NAME, q=1, text="recibe un pasador: H7")
    error = refused(
        tmp_path, action="decide", role="designer", name=NAME, q=1, by="engineer", text="Sin ajuste, insiste"
    )
    assert error.field_name == "objection" and status_of(tmp_path, 1) == "disputed"
    call(
        tmp_path,
        action="decide",
        role="designer",
        name=NAME,
        q=1,
        text="Sin ajuste, insiste",
        objection="el diseñador recomendó H7: recibe un pasador",
    )
    assert status_of(tmp_path, 1) == "decided"
    text = (tmp_path / "drawings" / f"{NAME}-questions.md").read_text(encoding="utf-8")
    assert "[decidida]" in text and "Objeción registrada: el diseñador recomendó H7" in text
    news, _ = wait(tmp_path, "drafter", since=3)
    assert "DECISIÓN sobre 1" in news and "draw it as decided" in news


def test_the_drafter_can_object_too_and_a_decided_question_takes_no_more_answers(tmp_path: Path) -> None:
    ask(tmp_path)
    call(tmp_path, action="reply", role="drafter", name=NAME, q=1, by="engineer", text="Cotas en cadena")
    call(tmp_path, action="dispute", role="drafter", name=NAME, q=1, text="una cadena acumula tolerancia")
    call(
        tmp_path,
        action="decide",
        role="drafter",
        name=NAME,
        q=1,
        text="Cotas en cadena",
        objection="acumula tolerancia",
    )
    assert refused(tmp_path, action="reply", role="designer", name=NAME, q=1, by="designer", text="x").code == "decided"
    assert refused(tmp_path, action="dispute", role="designer", name=NAME, q=1, text="x").code == "decided"
    assert refused(tmp_path, action="decide", role="drafter", name=NAME, q=1, text="otra vez").code == "decided"


def test_a_decision_is_the_engineers_and_the_designer_is_told_when_he_answered(tmp_path: Path) -> None:
    ask(tmp_path)
    assert (
        refused(tmp_path, action="decide", role="designer", name=NAME, q=1, by="designer", text="x").field_name == "by"
    )
    call(tmp_path, action="reply", role="drafter", name=NAME, q=1, by="engineer", text="Libre")
    heard, _ = wait(tmp_path, "designer", since=1)
    assert "not a decision yet" in heard and "check it against" in heard and 'action="dispute"' in heard


def test_done_names_the_questions_still_not_decided(tmp_path: Path) -> None:
    ask(tmp_path)
    ask(tmp_path, "otra")
    call(tmp_path, action="decide", role="drafter", name=NAME, q=1, text="libre")
    assert "question(s) 2" in call(tmp_path, action="done", role="drafter", name=NAME)


# --- the designer opens the drawing session -------------------------------------------------------------------------------

SESSION = "11111111-2222-3333-4444-555555555555"


class FakePopen:
    """Stands for ``subprocess.Popen``: it records the command line and opens nothing."""

    def __init__(self, fail: OSError | None = None) -> None:
        self.calls: list[tuple[list[str], dict[str, object]]] = []
        self.fail = fail

    def __call__(self, line: list[str], **kwargs: object) -> object:
        if self.fail is not None:
            raise self.fail
        self.calls.append((line, kwargs))
        return object()


class Machine:
    """A fake machine: a clock that moves when somebody sleeps, processes that are alive until killed, a config folder."""

    def __init__(self, tmp: Path, terminal: bool = True, claude: str = "C:\\bin\\claude.exe") -> None:
        self.popen = FakePopen()
        self.clock = 1_000_000.0
        self.killed: list[tuple[int, bool]] = []
        self.dead: set[int] = set()
        self.home = tmp / "claude-home"
        self.paths = {"wt.exe": "C:\\wt.exe"} if terminal else {}
        self.paths["claude"] = claude
        self.on_kill: Callable[[int], None] = lambda pid: None
        self.on_sleep: Callable[[], None] = lambda: None

    def host(self) -> channel_tools.Host:
        return channel_tools.Host(
            platform="win32",
            which=self.paths.get,
            popen=self.popen,
            kill=self.kill,
            alive=lambda pid: pid not in self.dead,
            now=lambda: self.clock,
            sleep=self.sleep,
            home=self.home,
            pid=4321,
            executable="C:\\py\\python.exe",
        )

    def sleep(self, seconds: float) -> None:
        self.clock += seconds
        self.on_sleep()

    def kill(self, pid: int, tree: bool) -> None:
        self.killed.append((pid, tree))
        self.dead.add(pid)
        self.on_kill(pid)


def open_window(workspace: Path, host: channel_tools.Host, **more: object) -> Reply:
    fields = {"action": "open_drawing", "role": "designer", "name": NAME, "agent": "cotaforge:drafter", **more}
    return channel_tools.run(workspace, ChannelArgs.model_validate(fields), host=host)


def test_on_windows_the_window_runs_a_wrapper_that_runs_the_agent_in_the_design_folder(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    reply = open_window(tmp_path, machine.host())
    ((line, kwargs),) = machine.popen.calls
    record = tmp_path / "drawings" / f"{NAME}-window.json"
    assert line[:3] == ["wt.exe", "-d", str(tmp_path)]
    assert line[3:9] == [
        "C:\\py\\python.exe",
        "-m",
        "cotaforge_mcp.drawing_window",
        "--record",
        str(record),
        "--opener",
    ]
    assert line[9] == "4321" and line[10] == "--"
    claude = line[11:]
    assert claude[:3] == ["C:\\bin\\claude.exe", "--agent", "cotaforge:drafter"] and claude[3] == "--session-id"
    prompt = claude[-1]
    assert prompt == drawing_window.opening_prompt(NAME) and f"drawings/{NAME}-intent.md" in prompt
    assert not set(';"&|^%') & set(prompt), "a terminal command line carries it"
    assert kwargs["cwd"] == str(tmp_path) and kwargs["close_fds"] is True
    assert "opened in a new window" in reply.text and "claude --resume " + claude[4] in reply.text
    assert "cd /d" in reply.text and "closes too" in reply.text
    assert drawing_window.read_record(record)["state"] == "starting"  # type: ignore[index]


def test_without_windows_terminal_a_plain_console_is_started_through_cmd(tmp_path: Path) -> None:
    machine = Machine(tmp_path, terminal=False)
    open_window(tmp_path, machine.host(), agent="cotaforge-othercad:drafter")
    ((line, _),) = machine.popen.calls
    assert line[:6] == ["cmd.exe", "/c", "start", "cotaforge drafter", "/D", str(tmp_path)]
    assert "cotaforge-othercad:drafter" in line


def test_claude_behind_a_cmd_shim_is_started_through_cmd_and_its_tree_is_the_one_ended(tmp_path: Path) -> None:
    machine = Machine(tmp_path, claude="C:\\npm\\claude.cmd")
    open_window(tmp_path, machine.host())
    ((line, _),) = machine.popen.calls
    assert "--tree" in line and line[line.index("--") + 1 : line.index("--") + 4] == [
        "cmd.exe",
        "/c",
        "C:\\npm\\claude.cmd",
    ]


def test_where_the_harness_opens_no_window_it_gives_the_command(tmp_path: Path) -> None:
    popen = FakePopen()
    for platform in ("linux", "darwin"):
        host = channel_tools.Host(platform=platform, which=lambda _: None, popen=popen)
        text = open_window(tmp_path, host).text
        folder = shlex.quote(str(tmp_path))  # a Windows path gets quoted: the text is the shell's whatever the machine
        assert f"cd {folder} && claude --agent cotaforge:drafter" in text and "second terminal" in text
    assert popen.calls == []
    assert not (tmp_path / "drawings" / f"{NAME}-window.json").exists()


def test_a_window_that_cannot_be_opened_says_so_and_gives_the_command(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    machine.popen.fail = OSError("no console")
    with pytest.raises(ToolError) as caught:
        open_window(tmp_path, machine.host())
    assert caught.value.code == "not_opened" and "claude --agent cotaforge:drafter" in caught.value.message


def test_a_drawing_is_not_opened_twice(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    open_window(tmp_path, machine.host())
    with pytest.raises(ToolError) as caught:
        open_window(tmp_path, machine.host())
    assert caught.value.code == "already_open" and len(machine.popen.calls) == 1
    machine.clock += drawing_window.STARTING_S + 1  # it never started: the record is stale
    open_window(tmp_path, machine.host())
    assert len(machine.popen.calls) == 2


def test_only_the_designer_opens_it_and_the_agent_name_is_checked(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    host = machine.host()
    for role in ("drafter", None):
        with pytest.raises(ToolError) as caught:
            open_window(tmp_path, host, role=role)
        assert caught.value.field_name == "role" or caught.value.code == "not_allowed"
    for bad in (None, "", "dibujante; calc", "a b", "--dangerous", "cotaforge:drafter & del"):
        with pytest.raises(ToolError) as caught:
            open_window(tmp_path, host, agent=bad)
        assert caught.value.field_name == "agent"
    with pytest.raises(ToolError) as caught:
        open_window(tmp_path, host, name="../x")
    assert caught.value.field_name == "name" and machine.popen.calls == []


# --- the wrapper that runs inside the window ---------------------------------------------------------------------------


class FakeProc:
    """A ``claude`` that runs for ``ticks`` beats and then ends (or never, until killed)."""

    def __init__(self, machine: Machine, ticks: int | None, pid: int = 777) -> None:
        self.pid = pid
        self.ticks = ticks
        self.machine = machine

    def wait(self, timeout: float) -> int:
        if self.pid in self.machine.dead:
            return 1
        if self.ticks is not None:
            if self.ticks == 0:
                return 0
            self.ticks -= 1
        self.machine.clock += timeout
        raise subprocess.TimeoutExpired("claude", timeout)


def wrapped(
    tmp: Path, machine: Machine, proc: FakeProc | OSError, opener_alive: Callable[[int], bool]
) -> tuple[int, Path]:
    record = tmp / "drawings" / f"{NAME}-window.json"
    drawing_window.write_record(record, {"state": "starting", "session_id": SESSION, "started": machine.clock})
    host = channel_tools.Host(
        platform="win32",
        popen=(lambda command: proc) if not isinstance(proc, OSError) else _raise(proc),
        kill=machine.kill,
        alive=opener_alive,
        now=lambda: machine.clock,
        pid=555,
    )
    return drawing_window.run_wrapper(record, 4321, False, ["claude"], host, beat_s=5.0), record


def _raise(exc: OSError) -> Callable[..., object]:
    def popen(command: list[str]) -> object:
        raise exc

    return popen


def test_the_wrapper_records_its_child_keeps_a_beat_and_marks_the_record_closed_when_it_ends(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    seen: list[dict[str, object]] = []

    def opener(pid: int) -> bool:
        seen.append(dict(drawing_window.read_record(tmp_path / "drawings" / f"{NAME}-window.json") or {}))
        return True

    code, record = wrapped(tmp_path, machine, FakeProc(machine, ticks=3), opener)
    assert code == 0 and machine.killed == []
    assert seen and seen[0]["state"] == "running" and seen[0]["claude_pid"] == 777 and seen[0]["wrapper_pid"] == 555
    assert seen[0]["session_id"] == SESSION and seen[-1]["beat"] > seen[0]["beat"]  # type: ignore[operator]
    final = drawing_window.read_record(record)
    assert final is not None and final["state"] == "closed" and final["opener_pid"] == 4321


def test_the_wrapper_ends_claude_by_pid_when_the_design_session_is_gone(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    code, record = wrapped(tmp_path, machine, FakeProc(machine, ticks=None), lambda pid: False)
    assert machine.killed == [(777, False)] and code == 1
    assert (drawing_window.read_record(record) or {})["state"] == "closed"


def test_a_claude_that_cannot_start_leaves_a_closed_record(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    code, record = wrapped(tmp_path, machine, OSError("not found"), lambda pid: True)
    assert code == 1 and (drawing_window.read_record(record) or {})["state"] == "closed"


# --- closing: only what the harness opened, only when the drawing is done and nobody is mid-turn -------------------------


def running_window(tmp: Path, machine: Machine, idle_s: float | None = 120.0, tree: bool = False) -> Path:
    """A window the wrapper is running: a fresh record, and the drawing session's transcript last written ``idle_s`` ago."""
    record = tmp / "drawings" / f"{NAME}-window.json"
    drawing_window.write_record(
        record,
        {
            "state": "running",
            "claude_pid": 777,
            "wrapper_pid": 555,
            "opener_pid": 4321,
            "session_id": SESSION,
            "tree": tree,
            "started": machine.clock - 60,
            "beat": machine.clock,
        },
    )
    if idle_s is not None:
        transcript = drawing_window.transcript(machine.host(), tmp, SESSION)
        transcript.parent.mkdir(parents=True)
        transcript.write_text("{}\n", encoding="utf-8")
        os.utime(transcript, (machine.clock - idle_s, machine.clock - idle_s))
    machine.on_kill = lambda pid: drawing_window.write_record(
        record, {**(drawing_window.read_record(record) or {}), "state": "closed"}
    )
    return record


def finished(workspace: Path, settled: bool = True) -> None:
    ask(workspace)
    if settled:
        call(workspace, action="decide", role="drafter", name=NAME, q=1, text="libre")
    call(workspace, action="done", role="drafter", name=NAME)


def close(workspace: Path, machine: Machine, **more: object) -> str:
    fields = {"action": "close", "role": "designer", "name": NAME, **more}
    return channel_tools.run(workspace, ChannelArgs.model_validate(fields), host=machine.host()).text


def test_the_designer_hears_done_and_the_window_is_closed_by_pid_after_the_work_is_saved(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    running_window(tmp_path, machine, idle_s=120)
    finished(tmp_path)
    args = ChannelArgs.model_validate({"action": "wait", "role": "designer", "name": NAME, "since": 0})
    text = channel_tools.run(tmp_path, args, host=machine.host()).text
    assert "stop waiting" in text and "-> window: the drawing window of soporte was closed" in text
    assert machine.killed == [(777, False)]
    assert f"claude --resume {SESSION}" in text


def test_nothing_is_closed_while_a_question_is_open_or_disputed(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    running_window(tmp_path, machine)
    finished(tmp_path, settled=False)
    args = ChannelArgs.model_validate({"action": "wait", "role": "designer", "name": NAME, "since": 0})
    text = channel_tools.run(tmp_path, args, host=machine.host()).text
    assert "NOT closed, question(s) 1" in text and machine.killed == []


def test_a_drawing_session_in_the_middle_of_a_turn_is_not_closed(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    running_window(tmp_path, machine, idle_s=3)
    path = drawing_window.transcript(machine.host(), tmp_path, SESSION)
    machine.on_sleep = lambda: os.utime(path, (machine.clock, machine.clock))  # a turn keeps writing its transcript
    finished(tmp_path)
    text = close(tmp_path, machine)
    assert text.startswith("NOT closed") and "active" in text and machine.killed == []
    assert machine.clock >= 1_000_000.0 + channel_tools.WAIT_IDLE_S, "it waited for the turn to end, within a bound"


def test_a_turn_that_ends_while_waiting_lets_the_close_go_on(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    running_window(tmp_path, machine, idle_s=25)  # 5 s more and the 30 s of quiet are there
    finished(tmp_path)
    assert "was closed" in close(tmp_path, machine) and machine.killed == [(777, False)]


def test_when_it_cannot_be_known_whether_the_engineer_is_typing_the_designer_is_told_to_ask(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    running_window(tmp_path, machine, idle_s=None)  # no transcript found
    finished(tmp_path)
    text = close(tmp_path, machine)
    assert text.startswith("NOT closed") and "ask him" in text and machine.killed == []
    assert "was closed" in close(tmp_path, machine, confirmed=True) and machine.killed == [(777, False)]


def test_an_explicit_close_before_done_needs_the_engineers_word(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    running_window(tmp_path, machine, idle_s=1)
    ask(tmp_path)
    text = close(tmp_path, machine)
    assert text.startswith("NOT closed") and "unsaved" in text and machine.killed == []
    assert "was closed" in close(tmp_path, machine, confirmed=True), "he said so: neither done nor quiet is asked"


def test_a_window_behind_a_cmd_shim_is_ended_with_its_tree(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    running_window(tmp_path, machine, tree=True)
    finished(tmp_path)
    close(tmp_path, machine)
    assert machine.killed == [(777, True)]


def test_a_stale_record_never_ends_a_pid_and_a_missing_one_is_nothing_to_close(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    record = running_window(tmp_path, machine)
    finished(tmp_path)
    machine.clock += drawing_window.STALE_S + 1  # the wrapper stopped beating: the pid may be another process by now
    assert "stale" in close(tmp_path, machine, confirmed=True) and machine.killed == []
    record.unlink()
    assert "no drawing window is open" in close(tmp_path, machine, confirmed=True)


def test_only_the_designer_closes_and_only_the_record_of_that_drawing(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    running_window(tmp_path, machine)
    finished(tmp_path)
    args = ChannelArgs.model_validate({"action": "close", "role": "drafter", "name": NAME})
    with pytest.raises(ToolError) as caught:
        channel_tools.run(tmp_path, args, host=machine.host())
    assert caught.value.code == "not_allowed" and machine.killed == []
    other = ChannelArgs.model_validate({"action": "close", "role": "designer", "name": "otra"})
    assert "no drawing window is open for otra" in channel_tools.run(tmp_path, other, host=machine.host()).text
    assert machine.killed == []


# --- through the server's dispatcher ---------------------------------------------------------------------------


def test_the_tool_exists_with_a_cad_that_makes_sheets_and_needs_no_cad_to_answer(
    make: MakeSession, settings: Settings
) -> None:
    session = make(DrawingFake())
    assert "channel" in visible_tools(session) and TOOLS_BY_NAME["channel"].needs_cad is False
    assert input_schema(TOOLS_BY_NAME["channel"], session)["properties"]["action"]["enum"] == [
        "ask",
        "reply",
        "dispute",
        "decide",
        "wait",
        "done",
        "list",
        "open_drawing",
        "reopen",
        "close",
    ]
    caller = Caller(session)
    assert "question 1 recorded" in caller.ok("channel", action="ask", role="drafter", name=NAME, text="¿ajuste?")
    assert (settings.workspace / "drawings" / f"{NAME}-questions.jsonl").is_file()
    assert "PREGUNTA 1" in caller.ok("channel", action="wait", role="designer", name=NAME, timeout_s=1)
    assert "role" in caller.err("channel", action="ask", name=NAME, text="x")


def test_a_cad_without_sheets_has_no_channel(make: MakeSession) -> None:
    assert "channel" not in visible_tools(make(BareFake()))


def test_the_wrapper_entry_point_needs_a_command_and_hands_the_options_to_the_wrapper(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import signal  # noqa: PLC0415

    seen: list[tuple[object, ...]] = []
    monkeypatch.setattr(drawing_window, "run_wrapper", lambda *a: seen.append(a) or 0)
    monkeypatch.setattr(signal, "signal", lambda *a: None)  # the test process keeps its own Ctrl+C
    assert drawing_window.main(["--record", "r.json", "--opener", "7"]) == 2
    assert drawing_window.main(["--record", "r.json", "--", "claude"]) == 2
    argv = ["--record", str(tmp_path / "r.json"), "--opener", "7", "--tree", "--", "cmd.exe", "/c", "claude"]
    assert drawing_window.main(argv) == 0
    assert seen == [(tmp_path / "r.json", 7, True, ["cmd.exe", "/c", "claude"])]


# --- the window closed by mistake: the channel hears it, the designer reopens the same conversation ----------------------


def events_of(workspace: Path) -> list[dict[str, object]]:
    return log(workspace)


def test_a_window_closed_by_the_engineer_before_done_is_written_into_the_channel(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    ask(tmp_path)
    code, record = wrapped(tmp_path, machine, FakeProc(machine, ticks=2), lambda pid: True)
    assert code == 0 and (drawing_window.read_record(record) or {})["state"] == "closed"
    assert [e["kind"] for e in events_of(tmp_path)] == ["ask", "closed"]
    heard, _ = wait(tmp_path, "designer", since=1)
    assert "VENTANA CERRADA antes de terminar" in heard and 'action="reopen"' in heard and "Nothing is lost" in heard


def test_the_channel_is_not_told_when_the_drawing_was_done_or_the_harness_closed_it_or_the_design_session_ended(
    tmp_path: Path,
) -> None:
    machine = Machine(tmp_path)
    ask(tmp_path)
    call(tmp_path, action="done", role="drafter", name=NAME)
    wrapped(tmp_path, machine, FakeProc(machine, ticks=1), lambda pid: True)  # done first: nothing to add
    assert [e["kind"] for e in events_of(tmp_path)] == ["ask", "done"]

    other = tmp_path / "otra"
    other.mkdir()
    ask(other)
    record = other / "drawings" / f"{NAME}-window.json"
    machine2 = Machine(other)
    # the harness marks the record before it ends claude: the wrapper sees `closing` at its next beat and at its end
    proc = FakeProc(machine2, ticks=2)
    marking = lambda pid: (  # noqa: E731
        drawing_window.write_record(record, {**(drawing_window.read_record(record) or {}), "closing": True}) or True
    )
    wrapped(other, machine2, proc, marking)
    assert [e["kind"] for e in events_of(other)] == ["ask"]

    third = tmp_path / "tercera"
    third.mkdir()
    ask(third)
    machine3 = Machine(third)
    wrapped(third, machine3, FakeProc(machine3, ticks=None), lambda pid: False)  # the opener is gone: nobody to tell
    assert [e["kind"] for e in events_of(third)] == ["ask"]


def test_a_resume_that_fails_at_once_is_replaced_by_the_fresh_session_the_record_names(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    record = tmp_path / "drawings" / f"{NAME}-window.json"
    drawing_window.write_record(
        record,
        {
            "state": "starting",
            "session_id": SESSION,
            "started": machine.clock,
            "fallback": {"command": ["claude", "--session-id", "new-id", "prompt"], "session_id": "new-id"},
        },
    )
    started: list[list[str]] = []

    class Failing(FakeProc):
        def wait(self, timeout: float) -> int:
            return 1

    def popen(command: list[str]) -> object:
        started.append(command)
        return Failing(machine, ticks=0) if len(started) == 1 else FakeProc(machine, ticks=1, pid=888)

    host = channel_tools.Host(
        platform="win32", popen=popen, kill=machine.kill, alive=lambda pid: True, now=lambda: machine.clock, pid=555
    )
    drawing_window.run_wrapper(record, 4321, False, ["claude", "--resume", SESSION], host, beat_s=5.0)
    assert started == [["claude", "--resume", SESSION], ["claude", "--session-id", "new-id", "prompt"]]
    assert (drawing_window.read_record(record) or {})["session_id"] == "new-id"


def test_a_resume_that_runs_for_a_while_and_then_fails_is_not_started_again(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    record = tmp_path / "drawings" / f"{NAME}-window.json"
    drawing_window.write_record(
        record,
        {"state": "starting", "session_id": SESSION, "fallback": {"command": ["claude"], "session_id": "new"}},
    )
    started: list[list[str]] = []

    class Late(FakeProc):
        def wait(self, timeout: float) -> int:
            machine.clock += 60  # it ran a minute: it did resume
            return 1

    host = channel_tools.Host(
        platform="win32",
        popen=lambda command: started.append(command) or Late(machine, ticks=0),
        kill=machine.kill,
        alive=lambda pid: True,
        now=lambda: machine.clock,
        pid=555,
    )
    drawing_window.run_wrapper(record, 4321, False, ["claude", "--resume", SESSION], host, beat_s=5.0)
    assert len(started) == 1


def test_reopen_continues_the_same_conversation_when_its_transcript_exists(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    record = running_window(tmp_path, machine)
    drawing_window.write_record(record, {**(drawing_window.read_record(record) or {}), "state": "closed"})
    reply = channel_tools.run(
        tmp_path,
        ChannelArgs.model_validate(
            {"action": "reopen", "role": "designer", "name": NAME, "agent": "cotaforge:drafter"}
        ),
        host=machine.host(),
    )
    ((line, _),) = machine.popen.calls
    claude = line[line.index("--") + 1 :]
    assert claude[:5] == ["C:\\bin\\claude.exe", "--resume", SESSION, "--agent", "cotaforge:drafter"]
    assert claude[-1] == drawing_window.reopening_prompt(NAME) and not set(';"&|^%') & set(claude[-1])
    assert "the same conversation (--resume)" in reply.text
    starting = drawing_window.read_record(record) or {}
    assert starting["session_id"] == SESSION and starting["fallback"]["command"][-1] == drawing_window.reopening_prompt(
        NAME
    )
    assert "--resume" not in starting["fallback"]["command"] and "--session-id" in starting["fallback"]["command"]


def test_reopen_starts_a_new_session_that_reads_the_channel_when_there_is_nothing_to_resume(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    ask(tmp_path)
    reply = channel_tools.run(
        tmp_path,
        ChannelArgs.model_validate(
            {"action": "reopen", "role": "designer", "name": NAME, "agent": "cotaforge:drafter"}
        ),
        host=machine.host(),
    )
    ((line, _),) = machine.popen.calls
    assert "--resume" not in line and "--session-id" in line
    assert "a new session that reads the channel" in reply.text


def test_reopen_is_the_designers_and_not_while_the_window_is_open(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    running_window(tmp_path, machine)
    args = {"action": "reopen", "role": "designer", "name": NAME, "agent": "cotaforge:drafter"}
    with pytest.raises(ToolError) as caught:
        channel_tools.run(tmp_path, ChannelArgs.model_validate(args), host=machine.host())
    assert caught.value.code == "already_open" and machine.popen.calls == []
    with pytest.raises(ToolError) as caught:
        channel_tools.run(tmp_path, ChannelArgs.model_validate({**args, "role": "drafter"}), host=machine.host())
    assert caught.value.code == "not_allowed"
    linux = channel_tools.Host(platform="linux", popen=machine.popen)
    text = channel_tools.run(tmp_path, ChannelArgs.model_validate({**args, "name": "nueva"}), host=linux).text
    assert "second terminal" in text and "claude --agent cotaforge:drafter" in text


def test_a_close_marks_the_record_before_it_ends_claude(tmp_path: Path) -> None:
    machine = Machine(tmp_path)
    record = running_window(tmp_path, machine)
    marks: list[object] = []
    machine.on_kill = lambda pid: marks.append((drawing_window.read_record(record) or {}).get("closing"))
    finished(tmp_path)
    close(tmp_path, machine)
    assert marks == [True]
