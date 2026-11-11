"""Capability filtering and the guide generated from the kernel models."""

import json
from pathlib import Path

import pytest
from cotaforge.ir import OP_NAMES

from cotaforge_mcp import catalog
from cotaforge_mcp.config import ConfigError, Settings
from cotaforge_mcp.guide import guide, profile_text, server_instructions
from cotaforge_mcp.schemas import TOOLS, TOOLS_BY_NAME, input_schema, visible_tools
from cotaforge_mcp.session import Session
from cotaforge_mcp.terms import term_hits
from tests.conftest import BareFake, Caller, DrawingFake, MakeSession, VisibleFake, fake_without

# --- capability filtering ---------------------------------------------------------------------------------------


def test_the_full_fake_shows_its_tools_and_actions(session: Session) -> None:
    assert visible_tools(session) == ["doc", "op", "undo", "inspect", "view", "check", "memory", "catalog", "cad_guide"]
    doc = input_schema(TOOLS_BY_NAME["doc"], session)
    assert doc["properties"]["action"]["enum"] == [
        "new",
        "open",
        "import",
        "save",
        "status",
        "close",
        "switch",
        "set_material",
        "set_route",
        "set_purpose",
        "convention",
        "reload",
    ]
    inspect = input_schema(TOOLS_BY_NAME["inspect"], session)
    assert "sketch_status" in inspect["properties"]["action"]["enum"]
    assert "holes" in inspect["properties"]["action"]["enum"]


def test_a_bare_adapter_hides_every_optional_tool_and_action(make: MakeSession) -> None:
    session = make(BareFake())
    assert visible_tools(session) == ["doc", "op", "undo", "inspect", "check", "memory", "catalog", "cad_guide"]
    assert input_schema(TOOLS_BY_NAME["doc"], session)["properties"]["action"]["enum"] == [
        "new",
        "status",
        "switch",
        "set_purpose",
        "convention",
    ]
    assert "sketch_status" not in input_schema(TOOLS_BY_NAME["inspect"], session)["properties"]["action"]["enum"]
    call = Caller(session)
    assert "unknown tool" in call.err("view", action="show")
    assert "not available with this CAD" in call.err("doc", action="open", path="x")
    call.ok("doc", action="new", name="bare")
    assert "not available with this CAD" in call.err("doc", action="save")


def test_a_visible_cad_adds_capture_and_set_view(make: MakeSession) -> None:
    session = make(VisibleFake())
    assert input_schema(TOOLS_BY_NAME["view"], session)["properties"]["action"]["enum"] == [
        "selection",
        "show",
        "capture",
        "set_view",
        "style",
        "section",
    ]
    assert "not available here" not in profile_text(session)
    assert "capture" in profile_text(make(fake_without("capture", "set_view")))


def test_ops_the_adapter_cannot_run_disappear(make: MakeSession) -> None:
    session = make(fake_without("linear_pattern"))
    assert "linear_pattern" not in catalog.available_ops(session.capabilities)
    assert "linear_pattern" not in guide(session, "ops")
    call = Caller(session)
    call.ok("doc", action="new", name="p")
    text = call.err(
        "op", name="linear_pattern", params={"features": ["s1"], "direction": "X", "spacing_mm": 5, "count": 2}
    )
    assert "not supported" in text or "unknown op" in text
    assert "not supported by fake" in guide(session, "op:linear_pattern")


# --- the always-loaded part stays small ------------------------------------------------------------------------------


def test_profile_and_instructions_are_compact_and_extensible(session: Session) -> None:
    profile = profile_text(session)
    assert profile.startswith("CAD: fake") and len(profile) < 2200
    instructions = server_instructions(session)
    assert "Conduct:" in instructions and instructions.count("\n- ") == 5


def test_every_operation_of_the_ir_is_in_the_profile(session: Session) -> None:
    profile = profile_text(session)
    for name in OP_NAMES:
        if name in catalog.available_ops(session.capabilities):
            assert name in profile


def test_the_memory_section_is_empty_without_entries_and_the_native_topic_is_a_stub(session: Session) -> None:
    from cotaforge_mcp.guide import memory_index_section

    assert memory_index_section(session) == ""
    assert "not available with fake" in guide(session, "native")


def _pydantic_titles(node: object) -> list[str]:
    """The schema ANNOTATIONS called title (a string); a property that is called title (an object) is not one."""
    found: list[str] = []
    if isinstance(node, dict):
        found.extend(v for k, v in node.items() if k == "title" and isinstance(v, str))
        for value in node.values():
            found.extend(_pydantic_titles(value))
    elif isinstance(node, list):
        for value in node:
            found.extend(_pydantic_titles(value))
    return found


def test_tool_schemas_are_small_and_valid_json(session: Session) -> None:
    for tool in TOOLS:
        schema = input_schema(tool, session)
        text = json.dumps(schema)
        assert not _pydantic_titles(schema) and len(text) < 1800, (tool.name, len(text))  # no pydantic titles


def test_the_drawing_title_is_a_property_the_schema_still_names(make: MakeSession) -> None:
    session = make(DrawingFake())
    schema = input_schema(TOOLS_BY_NAME["drawing"], session)
    assert "title" in schema["properties"] and schema["properties"]["action"]["enum"] == [
        "create",
        "add",
        "remove",
        "remove_sheet",
        "update",
        "add_view",
        "remove_view",
        "answer",
        "approve",
        "request_review",
        "review_choice",
        "review_report",
        "list",
        "read",
        "review",
        "capture",
    ]


def test_settings_from_environment(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {"COTAFORGE_ADAPTER": "FAKE", "COTAFORGE_WORKSPACE": str(tmp_path), "COTAFORGE_VISIBLE": "1"}
    )
    assert (settings.adapter, settings.workspace, settings.visible) == ("fake", tmp_path.resolve(), True)
    assert Settings.from_env({}).adapter == "freecad"
    with pytest.raises(ConfigError):
        Settings.from_env({"COTAFORGE_ADAPTER": "othercad"})
    assert settings.resolve("a/b.step") == tmp_path.resolve() / "a" / "b.step"


def test_terms_hits_feed_the_unknown_op_hint() -> None:
    assert term_hits("Extruir Corte") == ["cut"]
    assert term_hits("saliente por revolución") == ["revolve"]
    assert term_hits("asistente para taladro") == ["hole"]
