"""Tool registry and MCP schema export (contract/toolschema.py)."""

import json
from enum import Enum
from typing import Any

from pydantic import BaseModel

from cotaforge.contract.requests import Request


class Group(str, Enum):  # noqa: UP042
    SESSION = "session"
    HEALTH = "health"
    CONSTRUCTION = "construction"
    ASSEMBLIES = "assemblies"
    PROVENANCE = "provenance"
    QUERY = "query"
    REFERENCES = "references"
    CHANGES = "changes"
    VIEW = "view"
    DRAWING = "drawing"
    UNDO = "undo"
    EXPORT = "export"
    SELECTION = "selection"
    NATIVE = "native"
    SHEET_METAL = "sheet_metal"


class ToolSpec(BaseModel, frozen=True):
    model_config = {"arbitrary_types_allowed": True}

    name: str
    group: Group
    description: str
    optional: bool
    request_model: type[Request]
    response_model: type[BaseModel]


TOOL_REGISTRY: dict[str, ToolSpec] = {}


def export_mcp_tools(include_optional: bool = False) -> list[dict[str, Any]]:
    """Export the tool registry as MCP-style tool descriptors.

    Deterministic: sorted by (group, name).
    """
    specs = [spec for spec in TOOL_REGISTRY.values() if include_optional or not spec.optional]
    specs.sort(key=lambda spec: (spec.group.value, spec.name))
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "inputSchema": spec.request_model.model_json_schema(),
            "outputSchema": spec.response_model.model_json_schema(),
            "annotations": {"group": spec.group.value, "optional": spec.optional},
        }
        for spec in specs
    ]


def export_mcp_tools_json(path: str) -> None:
    """Write the full tool registry (including optional tools) to a JSON file."""
    tools = export_mcp_tools(include_optional=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(tools, handle, indent=2)
