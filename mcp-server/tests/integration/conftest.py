"""Integration: the real server as a stdio subprocess against the real FreeCAD (skipped when there is none).

The child inherits this process' environment (the caller redirects every profile directory to a temporary place);
only the server's own COTAFORGE_* variables are set here.
"""

import os
import re
import subprocess
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import mcp.types as types
import pytest
from cotaforge.adapters.freecad import find_freecad_root
from cotaforge.adapters.freecad.rpc import _binaries
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    here = Path(__file__).parent
    root = find_freecad_root()
    for item in items:
        if Path(str(item.fspath)).is_relative_to(here):
            item.add_marker(pytest.mark.freecad)
            if root is None:
                item.add_marker(pytest.mark.skip(reason="FreeCAD root not found (COTAFORGE_FREECAD_ROOT)"))


class Reply:
    def __init__(self, result: types.CallToolResult) -> None:
        self.is_error = bool(result.isError)
        self.text = "\n".join(c.text for c in result.content if isinstance(c, types.TextContent))
        self.images = [c for c in result.content if isinstance(c, types.ImageContent)]


class Cad:
    def __init__(self, session: ClientSession) -> None:
        self.session = session

    async def __call__(self, tool: str, **arguments: Any) -> Reply:
        return Reply(await self.session.call_tool(tool, arguments))

    async def ok(self, tool: str, **arguments: Any) -> str:
        reply = await self(tool, **arguments)
        assert not reply.is_error, reply.text
        return reply.text

    async def err(self, tool: str, **arguments: Any) -> str:
        reply = await self(tool, **arguments)
        assert reply.is_error, reply.text
        return reply.text


@asynccontextmanager
async def running_server(
    workspace: Path, *, visible: bool = False
) -> AsyncIterator[tuple[Cad, types.InitializeResult]]:
    env = {
        **os.environ,
        "COTAFORGE_ADAPTER": "freecad",
        "COTAFORGE_WORKSPACE": str(workspace),
        "COTAFORGE_VISIBLE": "1" if visible else "0",
        "COTAFORGE_FREECAD_ISOLATED": "1",  # a FreeCAD profile of its own: never the engineer's recent files or recovery data
    }
    params = StdioServerParameters(command=sys.executable, args=["-m", "cotaforge_mcp"], env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        yield Cad(session), init


FINDING_LINE = re.compile(r"^  (?:warning|info|BLOCKS EXPORT)(?: \(does not block export\))? (\S+?)(?: \[[^\]]*\])?(?: \(row [^)]*\))?: ", re.M)
ROW_LINE = re.compile(r"^  \[(?:MISSING|NEEDS AN ANSWER)\] (req\.\S+) ", re.M)
ORDER = {"by": "la prueba", "reason": "la prueba trata de otra cosa que este punto; el ingeniero lo ordena así"}
"""What an engineer who orders a point closed says: a test about something else than the finished drawing plays him, so
the measured points (which an agent may not dismiss on its own word) close like the rest."""
QUESTION_LINE = re.compile(r"^  (q\.\S+): ", re.M)
MEMORY_LINE = re.compile(r"^  (\S+) — recognize: ", re.M)


async def pending_points(cad: Cad, sheet: str | None = None) -> tuple[list[str], list[str], list[str]]:
    """(findings, questions, memory entries) the review of ``sheet`` still lists without an answer."""
    arguments = {"sheet": sheet} if sheet else {}
    text = await cad.ok("drawing", action="review", **arguments)
    return FINDING_LINE.findall(text), QUESTION_LINE.findall(text), MEMORY_LINE.findall(text)


async def pending_rows(cad: Cad, sheet: str | None = None) -> list[str]:
    """The rows of the table of requirements of ``sheet`` that are missing or need an answer."""
    arguments = {"sheet": sheet} if sheet else {}
    return ROW_LINE.findall(await cad.ok("drawing", action="review", **arguments))


async def finish_drawing(
    cad: Cad, sheet: str | None = None, reason: str = "no aplica: la prueba trata de otra cosa que este requisito"
) -> int:
    """Close every point of the review of a drawing the way an agent that has decided none applies would, for a test
    that is about something else than the finished drawing (the export of a sheet is refused until this is done): the
    project reviews its drawings itself (``propio``, the engineer's words quoted), so the points are the agent's own.
    Memory entries are read first, as the server requires. Returns how many points it answered."""
    await cad.ok("memory", action="brief", drawing_review="propio", text="la prueba trata de otra cosa que la revisión")
    arguments = {"sheet": sheet} if sheet else {}
    total = 0
    for _ in range(4):  # answering opens the next points (the memory entries of the drawing ops, the rows that follow)
        findings, questions, memory = await pending_points(cad, sheet)
        rows = await pending_rows(cad, sheet)
        if not (findings or questions or memory or rows):
            break
        for entry in memory:
            await cad.ok("memory", action="get", id=entry)
        for point in [*findings, *questions, *memory, *rows]:
            await cad.ok("drawing", action="answer", id=point, text=reason, **ORDER, **arguments)
        total += len(findings) + len(questions) + len(memory) + len(rows)
    return total


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    path = tmp_path / "design"
    path.mkdir()
    return path


def run_freecad_script(script: Path) -> None:
    """Run a script in FreeCAD's own interpreter (how a file the harness never wrote, or a file edited elsewhere,
    gets made)."""
    root = find_freecad_root()
    assert root is not None
    done = subprocess.run(  # noqa: S603
        [str(_binaries(root)[0]), str(script)], check=True, timeout=180, capture_output=True, text=True
    )
    assert "Exception while processing file" not in done.stderr + done.stdout, done.stderr + done.stdout


def volume(text: str) -> float:
    match = re.search(r"volume: (-?[\d.]+) mm3", text)
    assert match, text
    return float(match.group(1))


async def close_findings(
    cad: Cad, sheet: str | None = None, reason: str = "no aplica: la prueba trata de otra cosa"
) -> int:
    """Answer only the findings of the CAD (they hold the export in every mode but ``ninguno``). Returns how many."""
    findings, _, _ = await pending_points(cad, sheet)
    rows = await pending_rows(cad, sheet)
    arguments = {"sheet": sheet} if sheet else {}
    for point in [*findings, *rows]:
        await cad.ok("drawing", action="answer", id=point, text=reason, **ORDER, **arguments)
    return len(findings) + len(rows)


@pytest.fixture(autouse=True)
def isolated_freecad_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every FreeCAD these tests start (the server child inherits the environment) gets a profile of its own: never the
    engineer's recent files, recovery data or preferences."""
    monkeypatch.setenv("COTAFORGE_FREECAD_ISOLATED", "1")
