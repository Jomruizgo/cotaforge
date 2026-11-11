"""Entry builders shared by the memory and constraints tests (plain dicts validated by the real model)."""

from typing import Any

from cotaforge.memory import MemoryEntry

PROVENANCE: dict[str, Any] = {
    "source": "shop_measurement",
    "reference": "Tool table 2026-03",
    "by": "Ana",
    "date": "2026-10-11",
}


def make_entry(entry_id: str = "keep-it-simple", **overrides: Any) -> MemoryEntry:
    """A valid ``practice`` entry; ``overrides`` replace or add top-level fields."""
    data: dict[str, Any] = {
        "id": entry_id,
        "type": "practice",
        "title": "Keep it simple",
        "applies_when": "Any first sketch",
        "not_when": "The customer asks for an organic shape",
        "tags": ["sketching"],
        "body": "Prefer few features.",
    }
    data.update(overrides)
    return MemoryEntry.model_validate(data)


def capability(name: str, value: Any = None, **extra: Any) -> dict[str, Any]:
    """A capability dict; a list ``value`` becomes ``value_set`` (numbers) or ``value_names`` (strings)."""
    item: dict[str, Any] = {"name": name, **extra}
    if isinstance(value, list):
        item["value_names" if value and isinstance(value[0], str) else "value_set"] = value
    elif value is not None:
        item["value"] = value
    return item


def make_process(
    entry_id: str = "lathe-a",
    tag: str = "turning",
    capabilities: list[dict[str, Any]] | None = None,
    **overrides: Any,
) -> MemoryEntry:
    """A valid ``process`` entry (default: a lathe with max_diameter_mm 200) with provenance."""
    caps = [capability("max_diameter_mm", 200)] if capabilities is None else capabilities
    data: dict[str, Any] = {
        "id": entry_id,
        "type": "process",
        "title": f"Process {entry_id}",
        "applies_when": "Parts of revolution",
        "not_when": "Prismatic parts",
        "tags": ["revolution"],
        "provenance": PROVENANCE,
        "process": {"tag": tag, "capabilities": caps},
    }
    data.update(overrides)
    return MemoryEntry.model_validate(data)


def make_material(
    entry_id: str = "steel-1045",
    name: str = "Steel 1045",
    designations: list[str] | None = None,
    **overrides: Any,
) -> MemoryEntry:
    """A valid ``material`` entry with no numbers (so no provenance needed)."""
    data: dict[str, Any] = {
        "id": entry_id,
        "type": "material",
        "title": name,
        "applies_when": "Shafts and pins",
        "not_when": "Corrosive environments",
        "material": {"name": name, "designations": designations if designations is not None else ["AISI 1045", "C45"]},
    }
    data.update(overrides)
    return MemoryEntry.model_validate(data)
