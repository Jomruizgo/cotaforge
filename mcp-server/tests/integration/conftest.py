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

from cotaforge_mcp import engineer_words
from tests.conftest import claude_code_prompt


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


SHEET_RULES = frozenset(
    {
        "view_adds_nothing",
        "view_duplicate",
        "dimension_crossing",
        "dimension_stack_order",
        "dimension_gap",
        "sheet_underused",
        "collision",
        "views_overlap",
        "text_outside_frame",
        "paper_too_small",
        "native_unread",
        "native_orphaned",
    }
)
"""The codes of the geometry of the drawing: a view with nothing on it or drawn twice, lines across lines, a sheet left empty or
crowded, what the review could not read. A real test that builds a sheet FAILS when its review holds one of them (see
``assert_sheet_clean``) unless it lists it on purpose (``@pytest.mark.expected_findings``)."""
EXPECTED: tuple[str, ...] = ()
"""The codes the running test lists as expected (the autouse fixture below fills it from its ``expected_findings`` marks)."""
PENDING_BLOCKING = re.compile(r"^  BLOCKS EXPORT (\S+?)(?: \[[^\]]*\])?(?: \(row [^)]*\))?: ", re.M)
ANSWERED = re.compile(r"^  - ([a-z_]+(?:[:#]\S*)?): (?:APPLIES|DOES NOT APPLY)", re.M)


SESSION_ID = "integration-session"
"""The Claude Code session the server of a test believes it serves (Claude Code gives a real one its id)."""


class Cad:
    def __init__(self, session: ClientSession, looks: bool = True, home: Path | None = None) -> None:
        self.session = session
        self.home = home
        """The data folder of the server (``COTAFORGE_HOME``): where what the engineer types is kept."""
        self.built = False  # a sheet was built (created or composed) in this test: its review runs when the test ends
        self.allowed: set[str] = set()
        self.looks = looks
        """Look at the sheet before an export of it, like the agent does (export waits for a capture taken after the last
        change of the sheet): the tests of the export are about something else; the ones about the look pass ``looks=False``."""

    def says(self, text: str) -> str:
        """The engineer types ``text`` in the session: the hook Claude Code runs for each of his messages records
        it, as in a real session. Returns the text, to quote it in an order (an order he did not type is refused)."""
        assert self.home is not None
        payload = claude_code_prompt(self.home, SESSION_ID, text)
        assert engineer_words.hook(payload, {engineer_words.ENV_HOME: str(self.home)}) is not None
        return text

    def allow(self, *codes: str) -> None:
        """This test builds a defect on purpose: these codes may stand on its sheets."""
        self.allowed.update(codes)

    async def assert_sheet_clean(
        self, sheet: str | None = None, expected: tuple[str, ...] = (), strict: bool = False
    ) -> None:
        """Fail when the review of the sheet holds a finding of the drawing rules (``SHEET_RULES``) that the test did not list in
        ``expected`` (or ``allow``): the ones still pending AND the ones a test answered to get past them. ``strict``: every pending
        finding that holds the export."""
        arguments = {"sheet": sheet} if sheet else {}
        text = await self.ok("drawing", action="review", **arguments)
        listed = {*expected, *self.allowed, *EXPECTED}
        codes = [(m.split(":")[0].split("#")[0], m) for m in PENDING_BLOCKING.findall(text)]
        answered = [(m.split(":")[0].split("#")[0], m) for m in ANSWERED.findall(text)]
        bad = [i for code, i in codes if code not in listed and (strict or code in SHEET_RULES)]
        bad += [f"{i} (answered)" for code, i in answered if code in SHEET_RULES and code not in listed]
        detail = "\n  ".join(bad)
        assert not bad, f"the review of the sheet holds findings of the drawing rules:\n  {detail}\n{text[:3000]}"

    async def __call__(self, tool: str, **arguments: Any) -> Reply:
        if tool == "drawing" and arguments.get("action") in ("create", "compose"):
            self.built = True
        if (
            self.looks
            and tool == "export"
            and arguments.get("action") in ("pdf", "dxf")
            and arguments.get("face") is None
            and arguments.get("flat") is None
        ):
            sheet = {"sheet": arguments["sheet"]} if arguments.get("sheet") else {}
            await self.session.call_tool("drawing", {"action": "capture", **sheet})
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
        engineer_words.ENV_SESSION: SESSION_ID,
        engineer_words.ENV_ENTRYPOINT: engineer_words.TERMINAL_ENTRYPOINT,
    }
    env.setdefault(engineer_words.ENV_HOME, str(workspace.parent / "home"))  # never the real data folder
    params = StdioServerParameters(command=sys.executable, args=["-m", "cotaforge_mcp"], env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        init = await session.initialize()
        cad = Cad(session, home=Path(env[engineer_words.ENV_HOME]))
        yield cad, init
        if cad.built:  # no test sheet without a review: reached only when the test body ended without an error
            await cad.assert_sheet_clean()


FINDING_LINE = re.compile(r"^  (?:warning|info|BLOCKS EXPORT)(?: \(does not block export\))? (\S+?)(?: \[[^\]]*\])?(?: \(row [^)]*\))?: ", re.M)
ROW_LINE = re.compile(r"^  \[(?:MISSING|NEEDS AN ANSWER)\] (req\.\S+) ", re.M)
ORDER_WORDS = "esta prueba trata de otra cosa: ordeno cerrar este punto"


def order(cad: Cad) -> dict[str, str]:
    """The order of an engineer who closes a point: a test about something else than the finished drawing plays him.
    He types it first (the server accepts an order only with words he typed in the session), then it is quoted, so
    the measured points (which an agent may not dismiss on its own word) close like the rest."""
    return {"by": "Ana", "reason": cad.says(ORDER_WORDS)}


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
    await cad.ok("memory", action="brief", drawing_review="propio", text=cad.says("esta prueba trata de otra cosa que la revisión"))
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
            await cad.ok("drawing", action="answer", id=point, text=reason, **order(cad), **arguments)
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
        await cad.ok("drawing", action="answer", id=point, text=reason, **order(cad), **arguments)
    return len(findings) + len(rows)


@pytest.fixture(autouse=True)
def isolated_freecad_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every FreeCAD these tests start (the server child inherits the environment) gets a profile of its own: never the
    engineer's recent files, recovery data or preferences."""
    monkeypatch.setenv("COTAFORGE_FREECAD_ISOLATED", "1")


@pytest.fixture(autouse=True)
def expected_findings(request: pytest.FixtureRequest) -> None:
    """The defects a real test builds on purpose (``@pytest.mark.expected_findings("view_adds_nothing", ...)``): their codes may stand
    on its sheets when its review runs at the end."""
    global EXPECTED  # noqa: PLW0603 - the server context manager of the test reads it
    EXPECTED = tuple(code for mark in request.node.iter_markers("expected_findings") for code in mark.args)
