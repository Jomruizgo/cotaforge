"""The MCP server ``cad``: stdio transport, tools listed by what the active CAD supports, instructions with the
generated profile."""

import base64
import functools
import sys
import threading
import time
from typing import Any

import anyio
import anyio.from_thread
import mcp.types as types
from cotaforge.contract import Adapter, ProgressNotice
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from cotaforge_mcp import __version__, conduct
from cotaforge_mcp.config import ConfigError, Settings, make_adapter
from cotaforge_mcp.guide import server_instructions
from cotaforge_mcp.progress import TOTAL, Reporter, position, starting_message
from cotaforge_mcp.reply import Reply, fail
from cotaforge_mcp.schemas import TOOLS, TOOLS_BY_NAME, input_schema, tool_description
from cotaforge_mcp.session import CONNECT_TICK_S, CONNECT_WAIT_MAX_S, Session
from cotaforge_mcp.tools import call_tool, refuse_early

SERVER_NAME = "cad"
CONNECT_POLL_S = 0.1


def to_result(reply: Reply) -> types.CallToolResult:
    content: list[Any] = [types.TextContent(type="text", text=reply.text)]
    for image in reply.images:
        content.append(
            types.ImageContent(type="image", data=base64.b64encode(image.data).decode("ascii"), mimeType=image.mime)
        )
    return types.CallToolResult(content=content, isError=reply.is_error)


class ProgressSender:
    """``progress.Sink`` that sends MCP ``notifications/progress`` for one request, from any thread. Messages after the
    request has ended (or that the stream refuses) are dropped: progress must never fail the call it reports on."""

    def __init__(self, context: Any, token: str | int, portal: anyio.from_thread.BlockingPortal) -> None:
        self._context = context
        self._token = token
        self._portal = portal
        self._open = True

    def close(self) -> None:
        self._open = False

    def __call__(self, progress: float, total: float, message: str) -> None:
        if not self._open:
            return
        try:
            self._portal.start_task_soon(
                self._context.session.send_progress_notification,
                self._token,
                progress,
                total,
                message,
                self._context.request_id,
            )
        except Exception:  # noqa: BLE001 - the portal is gone: the call is over
            pass


async def wait_for_cad(session: Session, context: Any, token: str | int | None) -> tuple[float, Reply | None]:
    """A call that needs the CAD while it is still starting: wait for it on the event loop (so the wait ends with the
    request when the client goes away), telling the client every few seconds when it asked for progress. Returns the
    last progress number sent (the tool goes on from it) and, when the wait ran out, the reply that says so."""
    started = sent_at = time.monotonic()
    last = 0.0
    while session.connecting:
        await anyio.sleep(CONNECT_POLL_S)
        now = time.monotonic()
        if now - started >= CONNECT_WAIT_MAX_S:
            return last, fail(session.still_starting(now - started))
        if token is not None and now - sent_at >= CONNECT_TICK_S:
            sent_at = now
            notice = ProgressNotice(phase="connecting", elapsed_s=now - started)
            last = round(max(last + 0.001, position(notice)), 3)
            try:
                await context.session.send_progress_notification(
                    token, last, TOTAL, starting_message(now - started), context.request_id
                )
            except Exception:  # noqa: BLE001 - progress must never fail the call it reports on (a closed stream)
                token = None
    return last, None


def build_server(session: Session, notice: str = "") -> Server:
    """The low-level server over ``session``, which need not be connected: the instructions and the tool list come
    from what the adapter declares (no CAD process) and are fixed here, once. Tool schemas are written by hand from the
    argument models so their size is under control; arguments are validated by the handler, not by the SDK. ``notice``
    is what the agent must know about the design space's conduct file from the first word (``server_notice``)."""
    tools = [
        types.Tool(name=t.name, description=tool_description(t, session), inputSchema=input_schema(t, session))
        for t in TOOLS
        if t.visible(session)
    ]
    server: Server = Server(SERVER_NAME, version=__version__, instructions=server_instructions(session, notice))

    @server.list_tools()
    async def list_tools() -> list[types.Tool]:
        return tools

    @server.call_tool(validate_input=False)
    async def handle(name: str, arguments: dict[str, Any] | None) -> types.CallToolResult:
        context = server.request_context
        token = context.meta.progressToken if context.meta is not None else None
        sent = 0.0
        tool = TOOLS_BY_NAME.get(name)
        if tool is not None and tool.needs_cad and session.connecting:
            early = refuse_early(session, name, arguments)  # a call that cannot run anyway does not wait for the CAD
            if early is not None:
                return to_result(early)
            sent, refused = await wait_for_cad(session, context, token)
            if refused is not None:
                return to_result(refused)
        # A client that gives the call up (it cancels the request) is answered no more: the handler ends at once instead
        # of staying behind the thread, which runs to its end (a running CAD operation cannot be interrupted) and is
        # marked, so that whoever needs the CAD next is told what it is waiting for (``Session.holding``).
        gave_up = threading.Event()
        try:
            if token is None:  # the client did not ask for progress: nothing is sent
                run = functools.partial(call_tool, session, name, arguments, None, gave_up)
                return to_result(await anyio.to_thread.run_sync(run, abandon_on_cancel=True))
            # The tool runs in a thread and its progress arrives from threads (the adapter's heartbeat and its reader);
            # the portal hands each message to this event loop, which owns the stream to the client.
            async with anyio.from_thread.BlockingPortal() as portal:
                sender = ProgressSender(context, token, portal)
                try:
                    run = functools.partial(call_tool, session, name, arguments, Reporter(sender, sent), gave_up)
                    reply = await anyio.to_thread.run_sync(run, abandon_on_cancel=True)
                finally:
                    sender.close()
            return to_result(reply)
        except anyio.get_cancelled_exc_class():
            gave_up.set()
            raise

    return server


async def serve(session: Session) -> None:
    """Answer MCP over stdio at once; the CAD connects in the background from the moment the server is up."""
    server = build_server(session, conduct.server_notice(session.adapter, session.settings.workspace))
    async with stdio_server() as (read, write):
        session.begin_connect(on_failure=_report_cad_failure)
        await server.run(read, write, server.create_initialization_options())


def _report_cad_failure(reason: str) -> None:
    sys.stderr.write(f"cotaforge-mcp: CAD not available: {reason}\n")


def make_session(settings: Settings, adapter: Adapter | None = None) -> Session:
    """A session already connected (blocking), for tests and scripts; the server itself connects in the background."""
    session = Session(adapter or make_adapter(settings), settings)
    session.connect()
    return session


def main() -> None:
    try:
        settings = Settings.from_env()
    except ConfigError as exc:
        sys.stderr.write(f"cotaforge-mcp: {exc}\n")
        raise SystemExit(2) from exc
    session = Session(make_adapter(settings), settings)
    try:
        anyio.run(serve, session)
    finally:
        session.disconnect()  # also cancels a connect still running: no CAD process is left behind


if __name__ == "__main__":
    main()
