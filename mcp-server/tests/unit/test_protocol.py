"""The server over the real MCP protocol (in-memory transport): initialize, list, call, images, errors."""

import base64

import mcp.types as types
import pytest
from mcp.shared.memory import create_connected_server_and_client_session

from cotaforge_mcp.server import build_server
from cotaforge_mcp.session import Session
from tests.conftest import PLATE_SKETCH, PNG, MakeSession, VisibleFake


async def test_initialize_carries_the_generated_profile(session: Session) -> None:
    async with create_connected_server_and_client_session(build_server(session)) as client:
        init = await client.initialize()
        assert init.serverInfo.name == "cad"
        assert init.instructions is not None
        assert init.instructions.startswith("CAD: fake") and "Conduct:" in init.instructions


async def test_list_tools_and_a_full_call_sequence(session: Session) -> None:
    async with create_connected_server_and_client_session(build_server(session)) as client:
        tools = (await client.list_tools()).tools
        assert [t.name for t in tools] == [
            "doc",
            "op",
            "undo",
            "inspect",
            "view",
            "check",
            "memory",
            "catalog",
            "cad_guide",
        ]
        assert all(t.inputSchema["type"] == "object" for t in tools)

        created = await client.call_tool("doc", {"action": "new", "name": "plate"})
        assert not created.isError
        await client.call_tool("op", {"name": "sketch", "params": PLATE_SKETCH})
        result = await client.call_tool("op", {"name": "extrude", "params": {"sketch": "s1", "depth_mm": 8}})
        assert not result.isError
        text = result.content[0]
        assert isinstance(text, types.TextContent) and "volume: 48000 mm3" in text.text


async def test_invalid_input_is_a_tool_error_not_a_protocol_error(session: Session) -> None:
    async with create_connected_server_and_client_session(build_server(session)) as client:
        result = await client.call_tool("op", {"name": "extrude", "params": {"depth_mm": "x"}})
        assert result.isError
        text = result.content[0]
        assert isinstance(text, types.TextContent)
        assert "usage:" in text.text and "example:" in text.text and "Traceback" not in text.text
        wrong = await client.call_tool("doc", {"action": 5})
        assert wrong.isError and "field: action" in wrong.content[0].text  # type: ignore[union-attr]


async def test_capture_returns_an_image(make: MakeSession) -> None:
    session = make(VisibleFake())
    async with create_connected_server_and_client_session(build_server(session)) as client:
        await client.call_tool("doc", {"action": "new", "name": "p"})
        shot = await client.call_tool("view", {"action": "capture", "width": 64, "height": 48})
        assert not shot.isError
        image = shot.content[1]
        assert isinstance(image, types.ImageContent) and image.mimeType == "image/png"
        assert base64.b64decode(image.data) == PNG


@pytest.mark.parametrize("tool", ["export", "nope"])
async def test_hidden_tools_cannot_be_called(session: Session, tool: str) -> None:
    async with create_connected_server_and_client_session(build_server(session)) as client:
        result = await client.call_tool(tool, {"action": "step"})
        assert result.isError and "unknown tool" in result.content[0].text  # type: ignore[union-attr]
