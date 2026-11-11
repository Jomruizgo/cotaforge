"""No call waits for ever behind another: a call the client gave up keeps the CAD busy until it ends, and whoever
needs the CAD next is answered within a bounded time with what is still running; a call that needs no CAD, and the
check that the CAD process is alive, never wait for a running call."""

import contextlib
import threading
import time
from typing import Any

import anyio
import mcp.types as types
import pytest
from mcp.shared.exceptions import McpError
from mcp.shared.memory import create_connected_server_and_client_session

from cotaforge_mcp import session as session_module
from cotaforge_mcp.server import build_server
from tests.conftest import Caller, MakeSession, NativeFake, plate


class BusyFake(NativeFake):
    """A CAD whose ``run_native`` is still working until the test lets it go (a long operation nobody can interrupt)."""

    def __init__(self) -> None:
        super().__init__()
        self.entered = threading.Event()
        self.release = threading.Event()

    def run_native(self, request: Any) -> Any:
        self.entered.set()
        assert self.release.wait(30.0), "the test never released the CAD"
        return super().run_native(request)


@pytest.fixture(autouse=True)
def short_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(session_module, "CALL_WAIT_S", 0.6)
    monkeypatch.setattr(session_module, "CANCELLED_WAIT_S", 0.3)
    monkeypatch.setattr(session_module, "BUSY_SLICE_S", 0.05)


def start_long_call(call: Caller, adapter: BusyFake) -> threading.Thread:
    thread = threading.Thread(target=lambda: call("run_native", code="noop", intent="a long operation"), daemon=True)
    thread.start()
    assert adapter.entered.wait(5.0)
    return thread


def test_a_call_behind_a_running_one_is_answered_in_bounded_time_with_what_runs(make: MakeSession) -> None:
    adapter = BusyFake()
    session = make(adapter)
    call = Caller(session)
    plate(call)
    thread = start_long_call(Caller(session), adapter)
    try:
        started = time.monotonic()
        text = call.err("doc", action="status")
        assert time.monotonic() - started < 3.0
        assert text.startswith("error: cad_busy")
        assert "still busy with an earlier call: run_native, running for" in text
        assert "gave that call up" not in text  # nobody cancelled it: it is only slow
        assert "FreeCAD is closed" in text and "repeat the call in a moment" in text
    finally:
        adapter.release.set()
        thread.join(5.0)
    assert "document: plate" in call.ok("doc", action="status")  # the CAD is free again: nothing stays stuck


def test_a_call_that_needs_no_cad_does_not_wait_for_a_running_one(make: MakeSession) -> None:
    adapter = BusyFake()
    session = make(adapter)
    call = Caller(session)
    plate(call)
    thread = start_long_call(Caller(session), adapter)
    try:
        started = time.monotonic()
        call.ok("memory", action="index")
        call.ok("cad_guide", topic="traps")
        session.recover_if_lost("memory")  # the check of the CAD process takes no lock either
        assert time.monotonic() - started < 0.5
    finally:
        adapter.release.set()
        thread.join(5.0)


async def test_a_call_the_client_cancelled_is_given_up_at_once_and_the_next_one_is_told_why(
    settings: Any,
) -> None:
    adapter = BusyFake()
    session = session_module.Session(adapter, settings)
    session.connect()
    server = build_server(session)
    try:
        async with create_connected_server_and_client_session(server) as client:
            await client.initialize()
            created = await client.call_tool("doc", {"action": "new", "name": "plate"})
            assert not created.isError, created
            request_id = client._request_id  # type: ignore[attr-defined]  # the id the next request gets
            outcome: list[Any] = []

            async def long_call() -> None:
                with anyio.move_on_after(2.0), contextlib.suppress(McpError):  # the server answers a cancelled call
                    outcome.append(await client.call_tool("run_native", {"code": "noop", "intent": "long"}))

            async with anyio.create_task_group() as tasks:
                tasks.start_soon(long_call)
                while not adapter.entered.is_set():
                    await anyio.sleep(0.02)
                await client.send_notification(
                    types.ClientNotification(
                        types.CancelledNotification(
                            method="notifications/cancelled",
                            params=types.CancelledNotificationParams(
                                requestId=request_id, reason="the engineer pressed Esc"
                            ),
                        )
                    )
                )
                await anyio.sleep(0.2)
                started = time.monotonic()
                reply = await client.call_tool("doc", {"action": "status"})
                assert time.monotonic() - started < 3.0
                text = reply.content[0].text  # type: ignore[union-attr]
                assert reply.isError and text.startswith("error: cad_busy"), text
                assert "gave that call up" in text and "run_native" in text
                tasks.cancel_scope.cancel()
            adapter.release.set()
            await anyio.sleep(0.3)
            reply = await client.call_tool("doc", {"action": "status"})
            assert not reply.isError and "document: plate" in reply.content[0].text  # type: ignore[union-attr]
    finally:
        adapter.release.set()
        session.disconnect()
