"""Acceptance fixtures: a design space made by ``cotaforge init`` and the real server started from its ``.mcp.json``.

Nothing here talks to a model. The server is the same stdio subprocess Claude Code would start (command, args and
env taken from the generated ``.mcp.json``, working directory = the design space), over the real FreeCAD. The design
space and the ``COTAFORGE_HOME`` are temporary: the real profile is never read or written.

Every call is recorded in ``Space.trace`` and printed as a section of the report when a test fails.
"""

import json
import os
import re
import subprocess
import sys
from collections.abc import AsyncIterator, Generator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import mcp.types as types
import pytest
from cotaforge.adapters.freecad import find_freecad_root
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ENV_FREECAD_ROOT = "COTAFORGE_FREECAD_ROOT"
ARGS_WIDTH = 140
REPLY_WIDTH = 220


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    here = Path(__file__).parent
    root = find_freecad_root()
    for item in items:
        if Path(str(item.fspath)).is_relative_to(here):
            item.add_marker(pytest.mark.freecad)
            if root is None:
                item.add_marker(pytest.mark.skip(reason="FreeCAD root not found (COTAFORGE_FREECAD_ROOT)"))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(
    item: pytest.Item, call: pytest.CallInfo[None]
) -> Generator[None, pytest.TestReport, pytest.TestReport]:
    report = yield
    space = getattr(item, "funcargs", {}).get("space")
    if report.when == "call" and report.failed and isinstance(space, Space) and space.trace:
        report.sections.append(("acceptance trace", "\n".join(space.trace)))
    return report


def _short(text: str, width: int) -> str:
    flat = re.sub(r"\s+", " ", text).strip()
    return flat if len(flat) <= width else flat[: width - 3] + "..."


class Reply:
    def __init__(self, result: types.CallToolResult) -> None:
        self.is_error = bool(result.isError)
        self.text = "\n".join(c.text for c in result.content if isinstance(c, types.TextContent))


class Cad:
    """The tools of the server as awaitable calls; ``ok`` / ``err`` assert the outcome and return the text."""

    def __init__(self, session: ClientSession, trace: list[str]) -> None:
        self.session = session
        self.trace = trace

    async def __call__(self, tool: str, **arguments: Any) -> Reply:
        shown = ", ".join(f"{key}={value!r}" for key, value in arguments.items())
        self.trace.append(f"> {tool}({_short(shown, ARGS_WIDTH)})")
        reply = Reply(await self.session.call_tool(tool, arguments))
        self.trace.append(f"  {'ERROR' if reply.is_error else 'ok'}: {_short(reply.text, REPLY_WIDTH)}")
        return reply

    async def ok(self, tool: str, **arguments: Any) -> str:
        reply = await self(tool, **arguments)
        assert not reply.is_error, f"{tool} failed: {reply.text}"
        return reply.text

    async def err(self, tool: str, **arguments: Any) -> str:
        reply = await self(tool, **arguments)
        assert reply.is_error, f"{tool} should have been refused but replied: {reply.text}"
        return reply.text


@dataclass
class Space:
    """A design space made by ``cotaforge init`` (a temporary one) and what is needed to start its server."""

    workspace: Path
    home: Path
    trace: list[str] = field(default_factory=list)

    @property
    def mcp_json(self) -> dict[str, Any]:
        return json.loads((self.workspace / ".mcp.json").read_text(encoding="utf-8"))

    @property
    def settings(self) -> dict[str, Any]:
        return json.loads((self.workspace / ".claude" / "settings.local.json").read_text(encoding="utf-8"))

    def environment(self) -> dict[str, str]:
        """What the child processes inherit: this process' environment with every profile folder redirected."""
        return {**os.environ, "COTAFORGE_HOME": str(self.home), "COTAFORGE_BASE_MEMORY": "0", "HOME": str(self.home), "USERPROFILE": str(self.home)}

    def server_parameters(self) -> StdioServerParameters:
        """The server exactly as the generated ``.mcp.json`` declares it."""
        entry = self.mcp_json["mcpServers"]["cad"]
        env = {**self.environment(), **entry.get("env", {})}
        return StdioServerParameters(command=entry["command"], args=entry["args"], env=env, cwd=self.workspace)

    @asynccontextmanager
    async def server(self) -> AsyncIterator[tuple[Cad, types.InitializeResult]]:
        """A new server process (a new session) over this design space."""
        self.trace.append("-- server started from .mcp.json")
        async with stdio_client(self.server_parameters()) as (read, write), ClientSession(read, write) as session:
            init = await session.initialize()
            yield Cad(session, self.trace), init
        self.trace.append("-- server stopped")


def init_design_space(workspace: Path, home: Path) -> None:
    """Run ``cotaforge init <workspace>`` as a user would (``python -m cotaforge_mcp.cli``)."""
    home.mkdir(parents=True, exist_ok=True)
    command = [sys.executable, "-m", "cotaforge_mcp.cli", "init", str(workspace)]
    explicit = os.environ.get(ENV_FREECAD_ROOT)
    if explicit:
        command += ["--freecad-root", explicit]
    env = {**os.environ, "COTAFORGE_HOME": str(home), "HOME": str(home), "USERPROFILE": str(home)}
    done = subprocess.run(command, env=env, capture_output=True, text=True, timeout=120, check=False)  # noqa: S603
    assert done.returncode == 0, f"cotaforge init failed ({done.returncode}):\n{done.stdout}\n{done.stderr}"


@pytest.fixture
def space(tmp_path: Path) -> Space:
    workspace = tmp_path / "my design"  # a space in the path on purpose: that is what a Windows user folder looks like
    made = Space(workspace=workspace, home=tmp_path / "home")
    init_design_space(workspace, made.home)
    made.trace.append(f"-- cotaforge init {workspace}")
    return made


def volume(text: str) -> float:
    match = re.search(r"volume: (-?[\d.]+) mm3", text)
    assert match, f"no volume in: {text}"
    return float(match.group(1))


@pytest.fixture(autouse=True)
def isolated_freecad_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every FreeCAD these tests start (the server child inherits the environment) gets a profile of its own: never the
    engineer's recent files, recovery data or preferences."""
    monkeypatch.setenv("COTAFORGE_FREECAD_ISOLATED", "1")
