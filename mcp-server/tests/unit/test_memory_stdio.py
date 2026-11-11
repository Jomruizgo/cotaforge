"""``memory`` through the real server as a stdio subprocess (fake CAD): the index in the initialize instructions, save
in one session and seen by a new server process, and the provenance refusal. Every folder is temporary."""

import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import mcp.types as types
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from tests.conftest import process_entry

ENTRY = {
    "id": "ajuste-ejes",
    "type": "practice",
    "title": "Ajuste de ejes",
    "applies_when": "Ejes con rodamiento",
    "not_when": "Ejes sin carga",
}


@asynccontextmanager
async def running_server(workspace: Path, home: Path) -> AsyncIterator[tuple[ClientSession, types.InitializeResult]]:
    env = {
        **os.environ,
        "COTAFORGE_ADAPTER": "fake",
        "COTAFORGE_WORKSPACE": str(workspace),
        "COTAFORGE_HOME": str(home),  # never the real ~/.cotaforge
        "COTAFORGE_BASE_MEMORY": "0",  # the base set would make the memory section non-empty
        "HOME": str(home),
        "USERPROFILE": str(home),
    }
    params = StdioServerParameters(command=sys.executable, args=["-m", "cotaforge_mcp"], env=env)
    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
        yield session, await session.initialize()


async def call(session: ClientSession, **arguments: object) -> tuple[bool, str]:
    result = await session.call_tool("memory", dict(arguments))
    text = "\n".join(c.text for c in result.content if isinstance(c, types.TextContent))
    return bool(result.isError), text


async def test_memory_over_stdio_and_the_index_reaches_the_next_server(tmp_path: Path) -> None:
    workspace, home = tmp_path / "design", tmp_path / "home"
    workspace.mkdir()

    async with running_server(workspace, home) as (session, init):
        assert init.instructions is not None and "Memory (" not in init.instructions  # empty memory: silent
        listed = {tool.name: tool for tool in (await session.list_tools()).tools}
        assert "memory" in listed
        assert listed["memory"].inputSchema["properties"]["action"]["enum"] == [
            "index",
            "get",
            "search",
            "save",
            "deactivate",
            "approve",
            "answer",
            "review",
            "brief",
        ]

        is_error, text = await call(session, action="save", entry=ENTRY)
        assert not is_error and text.startswith("saved: ajuste-ejes in project memory")
        statement = {"source": "user_statement", "reference": "his words", "by": "Ana", "date": "2026-10-27"}
        is_error, text = await call(session, action="save", entry={**ENTRY, "provenance": statement}, scope="user")
        assert not is_error and "note: the project entry with the same id wins" in text
        is_error, text = await call(session, action="approve", id="ajuste-ejes", by="Ana", text="Ana: apruebala")
        assert not is_error and text.startswith("approved: ajuste-ejes")

        unproven = process_entry()
        del unproven["provenance"]
        is_error, text = await call(session, action="save", entry=unproven)
        assert is_error and text.startswith("error: invalid_entry") and "provenance" in text

        is_error, text = await call(session, action="index")  # fresh, without restarting the server
        assert not is_error and "project — practices: ajuste-ejes" in text

    async with running_server(workspace, home) as (_, init):  # a new session: the index is in the instructions
        assert init.instructions is not None
        assert "Memory (the engineer's knowledge" in init.instructions
        assert "project — practices: ajuste-ejes" in init.instructions
