"""The server answers the MCP handshake without a CAD process: instructions and tool list come from what the adapter
declares (and fit the 4096 characters Claude Code keeps), the CAD connects in the background, a call that needs it waits
for it and one that does not, does not; a client that leaves during the start leaves no CAD behind."""

import threading
import time
from typing import Any

import anyio
import pytest
from cotaforge.adapters.fake import FakeAdapter
from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import AdapterError, ConnectCancellable, ConnectRequest, ErrorCode, ErrorInfo, SessionInfo
from mcp.shared.memory import create_connected_server_and_client_session

from cotaforge_mcp import memory_tools
from cotaforge_mcp import server as server_module
from cotaforge_mcp import session as session_module
from cotaforge_mcp.config import Settings
from cotaforge_mcp.guide import ASSUMED_OPS, INSTRUCTIONS_MAX_CHARS, profile_text, server_instructions
from cotaforge_mcp.reply import ToolError
from cotaforge_mcp.server import build_server
from cotaforge_mcp.session import Session
from tests.conftest import Caller, VisibleFake, declare

LONG_MEMORY = 60  # about 3500 characters of ids: more than the rules leave


class SlowFake(FakeAdapter):
    """A CAD that takes as long to start as the test lets it, and can be told to give up."""

    def __init__(self, error: str | None = None) -> None:
        super().__init__()
        self.started = threading.Event()
        self.release = threading.Event()
        self.cancelled = False
        self.error = error
        self.disconnects = 0

    def connect(self, request: ConnectRequest) -> SessionInfo:
        self.started.set()
        self.release.wait(30.0)
        if self.cancelled or self.error:
            raise AdapterError(
                ErrorInfo(
                    code=ErrorCode.session_lost,
                    message=self.error or "connect cancelled",
                    operation_id=request.operation_id,
                    recoverable=False,
                )
            )
        return super().connect(request)

    def cancel_connect(self) -> None:
        self.cancelled = True
        self.release.set()

    def disconnect(self, request: Any) -> Any:
        self.disconnects += 1
        return super().disconnect(request)


def make_memory(settings: Settings, count: int = LONG_MEMORY) -> None:
    for number in range(count):
        declare(
            settings,
            {
                "id": f"regla-{number:03d}-con-un-nombre-largo-y-descriptivo-de-la-practica",
                "type": "practice",
                "title": f"Regla {number}",
                "applies_when": "Ejes con rodamiento",
                "not_when": "Ejes sin carga",
                "body": "Usar h6.",
            },
        )


def adapters() -> dict[str, Any]:
    return {
        "fake": FakeAdapter(),
        "fake-visible": VisibleFake(),
        "freecad": FreeCADAdapter(),
        "freecad-visible": FreeCADAdapter(visible=True),
    }


# --- the instructions: fixed, short, independent of a live CAD ------------------------------------------------------


@pytest.mark.parametrize("name", ["fake", "fake-visible", "freecad", "freecad-visible"])
def test_the_instructions_fit_what_claude_code_keeps_with_and_without_memory(name: str, settings: Settings) -> None:
    session = Session(adapters()[name], settings)
    assert len(server_instructions(session)) <= INSTRUCTIONS_MAX_CHARS
    make_memory(settings)
    text = server_instructions(session)
    assert len(text) <= INSTRUCTIONS_MAX_CHARS
    assert "Conduct:" in text and "index cut:" in text  # the rules are whole; the index says it was cut
    assert "memory(action=index|search) has the rest" in text


def test_the_memory_index_gets_what_the_rules_leave(settings: Settings) -> None:
    session = Session(FreeCADAdapter(visible=True), settings)
    bare = server_instructions(session)
    make_memory(settings, 12)
    text = server_instructions(session)
    assert text.startswith(bare) and "index cut" not in text and text.count("con-un-nombre-largo") == 12


@pytest.mark.parametrize("name", ["fake", "fake-visible"])
def test_the_instructions_do_not_change_once_the_cad_is_up(name: str, settings: Settings) -> None:
    session = Session(adapters()[name], settings)
    before = server_instructions(session)
    session.connect()
    assert server_instructions(session) == before


def test_freecad_instructions_and_tools_need_no_process(settings: Settings) -> None:
    adapter = FreeCADAdapter(visible=True)
    session = Session(adapter, settings)
    text = server_instructions(session)
    assert text.startswith("CAD: freecad (visible window).") and "NOT connected" not in text
    assert f"ops (sheet metal{ASSUMED_OPS}): base_flange, edge_flange, unfold" in text  # only a worker knows the add-on
    assert "ops (sheet metal):" not in text
    assert adapter.worker_pid is None and adapter.sheet_metal is None


def test_a_cad_with_nothing_assumed_states_its_ops_plainly(settings: Settings) -> None:
    text = server_instructions(Session(FakeAdapter(), settings))
    assert "ops (sheet metal): base_flange, edge_flange, unfold" in text and ASSUMED_OPS not in text


def test_the_profile_topic_still_says_what_only_a_live_cad_knows(settings: Settings) -> None:
    session = Session(FakeAdapter(), settings)
    assert "NOT connected: not started" in profile_text(session)
    session.connect()
    assert profile_text(session).startswith("CAD: fake 0.")
    assert profile_text(session, live=False).startswith("CAD: fake. Units mm")


# --- the handshake while the CAD starts -------------------------------------------------------------------------------


async def test_the_handshake_and_the_tool_list_do_not_wait_for_the_cad(settings: Settings) -> None:
    adapter = SlowFake()
    session = Session(adapter, settings)
    server = build_server(session)
    session.begin_connect()
    assert adapter.started.wait(5.0) and session.connecting
    try:
        async with create_connected_server_and_client_session(server) as client:
            started = time.monotonic()
            init = await client.initialize()
            tools = (await client.list_tools()).tools
            assert time.monotonic() - started < 2.0 and session.connecting
            assert init.instructions is not None and len(init.instructions) <= INSTRUCTIONS_MAX_CHARS
            assert [t.name for t in tools][:2] == ["doc", "op"]
    finally:
        session.disconnect()


async def test_a_tool_that_needs_the_cad_waits_for_it_and_says_so_and_the_others_do_not(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(server_module, "CONNECT_TICK_S", 0.05)
    adapter = SlowFake()
    session = Session(adapter, settings)
    server = build_server(session)
    session.begin_connect()
    notices: list[tuple[float, str | None]] = []

    async def heard(progress: float, total: float | None, message: str | None) -> None:
        notices.append((progress, message))

    async def release_later() -> None:
        await anyio.sleep(0.5)
        adapter.release.set()

    try:
        async with create_connected_server_and_client_session(server) as client:
            await client.initialize()
            for tool, arguments in (
                ("memory", {"action": "index"}),
                ("catalog", {"action": "search", "text": "tubo"}),
                ("cad_guide", {"topic": "traps"}),
            ):
                reply = await client.call_tool(tool, arguments)
                assert not reply.isError and session.connecting, tool  # none of them waited
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(release_later)
                created = await client.call_tool("doc", {"action": "new", "name": "plate"}, progress_callback=heard)
            assert not created.isError, created
            assert not session.connecting and session.info is not None
            assert notices and all("starting the CAD software" in (m or "") for _, m in notices)
            numbers = [n for n, _ in notices]
            assert numbers == sorted(set(numbers)) and numbers[-1] < 3.0  # strictly up, inside its share of the scale
    finally:
        session.disconnect()


async def test_the_guide_topics_that_say_what_this_cad_can_do_wait_for_it_and_the_rest_do_not(
    settings: Settings,
) -> None:
    adapter = SlowFake()
    session = Session(adapter, settings)
    server = build_server(session)
    session.begin_connect()

    async def release_later() -> None:
        await anyio.sleep(0.5)
        adapter.release.set()

    try:
        async with create_connected_server_and_client_session(server) as client:
            await client.initialize()
            assert not (await client.call_tool("cad_guide", {"topic": "traps"})).isError and session.connecting
            async with anyio.create_task_group() as tasks:
                tasks.start_soon(release_later)
                reply = await client.call_tool("cad_guide", {"topic": "ops"})
            assert not reply.isError and not session.connecting  # it answered once the CAD was up
    finally:
        session.disconnect()


async def test_a_guide_topic_that_waits_still_answers_when_the_cad_did_not_start(settings: Settings) -> None:
    adapter = SlowFake(error="FreeCAD root not found; set COTAFORGE_FREECAD_ROOT")
    session = Session(adapter, settings)
    server = build_server(session)
    session.begin_connect()
    adapter.release.set()
    try:
        async with create_connected_server_and_client_session(server) as client:
            await client.initialize()
            assert not (await client.call_tool("cad_guide", {"topic": "ops"})).isError
    finally:
        session.disconnect()


async def test_a_cad_that_did_not_start_answers_with_the_reason(settings: Settings) -> None:
    adapter = SlowFake(error="FreeCAD root not found; set COTAFORGE_FREECAD_ROOT")
    session = Session(adapter, settings)
    server = build_server(session)
    failures: list[str] = []
    session.begin_connect(failures.append)
    adapter.release.set()
    try:
        async with create_connected_server_and_client_session(server) as client:
            await client.initialize()
            reply = await client.call_tool("doc", {"action": "new", "name": "plate"})
            assert reply.isError
            text = reply.content[0].text  # type: ignore[union-attr]
            assert "did not start" in text and "FreeCAD root not found" in text and "restart the cad server" in text
            assert "cad_unavailable" in text
            assert not (await client.call_tool("memory", {"action": "index"})).isError
        assert failures == ["FreeCAD root not found; set COTAFORGE_FREECAD_ROOT"]
    finally:
        session.disconnect()


async def test_the_wait_for_the_cad_has_a_limit(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server_module, "CONNECT_WAIT_MAX_S", 0.3)
    adapter = SlowFake()
    session = Session(adapter, settings)
    server = build_server(session)
    session.begin_connect()
    try:
        async with create_connected_server_and_client_session(server) as client:
            await client.initialize()
            reply = await client.call_tool("op", {"name": "sketch", "params": {}})
            assert reply.isError and "still starting" in reply.content[0].text  # type: ignore[union-attr]
            assert session.connecting  # the start goes on: the next call waits for it again
    finally:
        session.disconnect()


def test_a_direct_call_waits_too(settings: Settings) -> None:
    """``call_tool`` without the server (a script, a test) has the same rule."""
    adapter = SlowFake()
    session = Session(adapter, settings)
    session.begin_connect()
    threading.Timer(0.3, adapter.release.set).start()
    call = Caller(session)
    assert "document: plate" in call.ok("doc", action="new", name="plate")
    assert not session.connecting
    session.disconnect()


# --- the client leaves while the CAD is starting ------------------------------------------------------------------


def test_disconnect_during_the_start_cancels_it_and_releases_the_adapter(settings: Settings) -> None:
    adapter = SlowFake()
    assert isinstance(adapter, ConnectCancellable)
    session = Session(adapter, settings)
    session.begin_connect()
    assert adapter.started.wait(5.0) and session.connecting
    started = time.monotonic()
    session.disconnect()
    assert time.monotonic() - started < 5.0
    assert adapter.cancelled and not session.connecting and session.info is None
    assert adapter.disconnects >= 1


def test_a_start_that_ends_after_the_client_left_is_released(settings: Settings) -> None:
    """An adapter that cannot be cancelled: the thread releases what it started once it is done."""

    class Uncancellable(SlowFake):
        cancel_connect = None  # type: ignore[assignment]

    adapter = Uncancellable()
    session = Session(adapter, settings)
    session.begin_connect()
    assert adapter.started.wait(5.0)
    threading.Timer(0.3, adapter.release.set).start()
    session.disconnect()  # waits (bounded) for the thread, then releases
    assert session.info is not None and adapter.disconnects >= 1  # connected after all, and disconnected
    assert not session.connecting


def test_begin_connect_is_once_and_not_after_disconnect(settings: Settings) -> None:
    adapter = SlowFake()
    session = Session(adapter, settings)
    session.disconnect()
    session.begin_connect()
    assert not session.connecting and not adapter.started.is_set()


def test_a_thread_that_cannot_be_started_is_an_error_not_a_wait(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_thread(self: threading.Thread) -> None:
        raise RuntimeError("can't start new thread")

    monkeypatch.setattr(threading.Thread, "start", no_thread)
    session = Session(SlowFake(), settings)
    failures: list[str] = []
    session.begin_connect(failures.append)
    assert not session.connecting and failures and "can't start new thread" in failures[0]
    with pytest.raises(ToolError, match="did not start"):
        session.wait_for_cad()


def test_the_join_of_a_disconnect_is_bounded(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    """A connect that ignores the cancel: disconnect gives up waiting for it, and the thread still releases what it
    started when it ends."""
    monkeypatch.setattr(session_module, "CONNECT_CANCEL_JOIN_S", 0.2)

    class Deaf(SlowFake):
        def cancel_connect(self) -> None:
            self.cancelled = False  # does nothing

    adapter = Deaf()
    session = Session(adapter, settings)
    session.begin_connect()
    assert adapter.started.wait(5.0)
    started = time.monotonic()
    session.disconnect()
    assert time.monotonic() - started < 3.0 and session.connecting  # gave up waiting, the thread is still in connect
    adapter.release.set()
    deadline = time.monotonic() + 5.0
    while session.connecting and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not session.connecting and adapter.disconnects >= 2  # released by the thread as well as by disconnect


# --- the wait: its limit, the end of the server, the failures ----------------------------------------------------


def test_wait_for_cad_stops_at_its_limit_and_when_the_server_is_closing(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(session_module, "CONNECT_TICK_S", 0.02)
    adapter = SlowFake()
    session = Session(adapter, settings)
    session.begin_connect()
    heard: list[Any] = []
    with pytest.raises(ToolError) as limit:
        session.wait_for_cad(heard.append, timeout_s=0.1)
    assert limit.value.code == "cad_starting" and heard and heard[0].phase == "connecting"
    assert session.connecting  # the start goes on
    session._closing = True
    with pytest.raises(ToolError) as closing:
        session.wait_for_cad(timeout_s=60.0)
    assert closing.value.code == "cad_starting"
    session.disconnect()


def test_an_exception_that_is_not_an_adapter_error_in_the_connect_thread_is_reported(settings: Settings) -> None:
    class Broken(FakeAdapter):
        def connect(self, request: ConnectRequest) -> SessionInfo:
            raise RuntimeError("boom")

    session = Session(Broken(), settings)
    failures: list[str] = []
    session.begin_connect(failures.append)
    deadline = time.monotonic() + 5.0
    while session.connecting and time.monotonic() < deadline:
        time.sleep(0.02)
    assert not session.connecting and session.connect_error == "RuntimeError: boom"
    deadline = time.monotonic() + 5.0
    while not failures and time.monotonic() < deadline:
        time.sleep(0.02)
    assert failures == ["RuntimeError: boom"]
    assert "cad_unavailable" in Caller(session).err("doc", action="new", name="x")


def test_every_call_after_a_failed_start_gets_the_same_answer(settings: Settings) -> None:
    adapter = SlowFake(error="FreeCAD root not found")
    session = Session(adapter, settings)
    session.begin_connect()
    adapter.release.set()
    assert session._connect_done.wait(5.0)
    call = Caller(session)
    first = call.err("doc", action="status")
    assert "cad_unavailable" in first and "FreeCAD root not found" in first
    assert call.err("doc", action="status") == first
    assert "cad_unavailable" in call.err("op", name="sketch", params={})
    assert call.ok("memory", action="index")  # the tools that need no CAD go on working


async def test_a_call_that_cannot_run_is_refused_without_waiting_for_the_cad(settings: Settings) -> None:
    adapter = SlowFake()
    session = Session(adapter, settings)
    server = build_server(session)
    session.begin_connect()
    assert adapter.started.wait(5.0)
    try:
        async with create_connected_server_and_client_session(server) as client:
            await client.initialize()
            started = time.monotonic()
            wrong = await client.call_tool("doc", {"action": 5})
            assert wrong.isError and "field: action" in wrong.content[0].text  # type: ignore[union-attr]
            unknown = await client.call_tool("nope", {})
            assert unknown.isError and "unknown tool" in unknown.content[0].text  # type: ignore[union-attr]
            assert time.monotonic() - started < 2.0 and session.connecting
    finally:
        session.disconnect()


async def test_a_progress_notice_the_stream_refuses_does_not_fail_the_wait(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    class ClosedStream:
        request_id = 1

        class session:  # noqa: N801 - a stand-in for the request's session
            @staticmethod
            async def send_progress_notification(*args: object) -> None:
                raise anyio.ClosedResourceError

    monkeypatch.setattr(server_module, "CONNECT_TICK_S", 0.01)
    adapter = SlowFake()
    session = Session(adapter, settings)
    session.begin_connect()
    threading.Timer(0.3, adapter.release.set).start()
    last, refused = await server_module.wait_for_cad(session, ClosedStream, 7)
    assert refused is None and not session.connecting and last > 0
    session.disconnect()


def test_the_last_resort_cut_of_the_memory_index_ends_on_a_line(settings: Settings) -> None:
    make_memory(settings, 4)
    full = memory_tools.instructions_section(settings)
    lines = full.splitlines()
    for limit in (len(lines[0]) + 20, len(lines[0]) - 5, 0):
        cut = memory_tools.instructions_section(settings, limit)
        assert len(cut) <= limit
        assert cut == "" or cut in full and full.startswith(cut) and (full[len(cut)] == "\n")
