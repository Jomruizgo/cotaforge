"""Worked recipes for ``cad_guide(topic=recipe)``, written as the exact arguments of the ``op`` calls.

The text the agent reads is rendered from these steps and the tests run the same steps against the fake adapter, so a
recipe that stops being valid fails a test instead of misleading the agent.
"""

import json
from typing import Any

_XY = {"kind": "plane", "name": "XY"}
_TOP = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}


def _side(x: int, y: int) -> dict[str, Any]:
    return {"kind": "face", "from_op": "base", "select": {"normal": [x, y, 0]}}


def _corner(sx: int, sy: int) -> dict[str, Any]:
    return {"kind": "edge", "between": [_side(sx, 0), _side(0, sy)]}


def _hole(step_id: str, at: list[int]) -> dict[str, Any]:
    params = {"face": _TOP, "at": at, "standard": "simple", "diameter_mm": 6, "end_condition": "through_all"}
    return {"name": "hole", "id": step_id, "params": params}


PLATE: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "Outline on a base plane, centred on the origin; the named dimension lets you change the width later.",
        {
            "name": "sketch",
            "id": "outline",
            "params": {
                "on": _XY,
                "entities": [{"kind": "rect", "name": "plate", "center": [0, 0], "width": 100, "height": 60}],
                "named_dimensions": [{"name": "width", "entity": "plate", "attribute": "width"}],
            },
        },
    ),
    (
        "Thickness (the top face ends up at z=8).",
        {"name": "extrude", "id": "base", "params": {"sketch": "outline", "depth_mm": 8}},
    ),
    (
        'Slot on the top face: frame="world" takes [x, y, z] mm points (default: [u, v] from the face centre).',
        {
            "name": "sketch",
            "id": "slot_sk",
            "params": {
                "on": _TOP,
                "frame": "world",
                "entities": [{"kind": "slot", "name": "slot", "start": [-20, 0, 8], "end": [20, 0, 8], "width": 10}],
            },
        },
    ),
    ("Cut it through.", {"name": "cut", "id": "slot", "params": {"sketch": "slot_sk", "end_condition": "through_all"}}),
    (
        "Holes: at is a world point, projected onto the face; refused, with the distance, when outside it.",
        _hole("hole_a", [-35, 20, 8]),
    ),
    ("", _hole("hole_b", [35, -20, 8])),
    (
        "Round the four vertical corners (each edge is the meeting of two side faces).",
        {
            "name": "fillet",
            "id": "corners",
            "params": {"edges": [_corner(1, 1), _corner(1, -1), _corner(-1, 1), _corner(-1, -1)], "radius_mm": 5},
        },
    ),
    (
        "Change a dimension later: one call sets it in place; the steps after it keep their ids.",
        {"edit": "base", "params": {"depth_mm": 12}},
    ),
    ("", {"edit": "outline", "params": {"dimensions": {"width": 120}}}),
    ("The hole diameter is just as editable.", {"edit": "hole_a", "params": {"diameter_mm": 8}}),
)


def _line(name: str, a: list[int], b: list[int]) -> dict[str, Any]:
    return {"kind": "line", "name": name, "start": a, "end": b}


_PROFILE_EDGES = {"kind": "edges", "from_op": "plate", "where": {"parallel_to": "z", "convex": True}}
_FLAT_STRAIGHT = {"kind": "edges", "from_op": "plate", "where": {"curve": "line", "planar": True}}

CHAMFERS: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        'An L plate 19 mm thick (a second document, or undo(to="start")). Closed profile of lines, end to end.',
        {
            "name": "sketch",
            "id": "outline",
            "params": {
                "on": _XY,
                "entities": [
                    _line("a", [0, 0], [100, 0]),
                    _line("b", [100, 0], [100, 40]),
                    _line("c", [100, 40], [40, 40]),
                    _line("d", [40, 40], [40, 80]),
                    _line("e", [40, 80], [0, 80]),
                    _line("f", [0, 80], [0, 0]),
                ],
            },
        },
    ),
    ("", {"name": "extrude", "id": "plate", "params": {"sketch": "outline", "depth_mm": 19}}),
    (
        "Machine chamfer on the corners of the profile: the convex edges along the thickness, in ONE op. Look first "
        'with inspect(action="edges", op="plate", where={"parallel_to":"z","convex":true}): it lists them, changes '
        "nothing.",
        {"name": "chamfer", "id": "corners", "params": {"edges": [_PROFILE_EDGES], "distance1_mm": 3}},
    ),
    (
        "Instead (undo the step above): 1x45 on every straight edge between flat faces, holes untouched (a hole edge "
        "is a circle or touches a cylinder). If the CAD cannot do some edges the step fails NAMING them (id and "
        "position); skip_failing=true applies the rest and reports the skipped ones.",
        {
            "name": "chamfer",
            "id": "edge_break",
            "params": {"edges": [_FLAT_STRAIGHT], "distance1_mm": 1, "skip_failing": True},
        },
    ),
)
"""The second recipe: edge sets. The last step is the alternative to the one before it (the test undoes in between)."""


def call_text(arguments: dict[str, Any]) -> str:
    """One step as the call the agent writes."""
    if "edit" in arguments:
        head = f'edit="{arguments["edit"]}"'
    else:
        head = f'name="{arguments["name"]}", id="{arguments["id"]}"'
    return f"op({head}, params={json.dumps(arguments['params'], separators=(',', ':'))})"


def _render(title: str, steps: tuple[tuple[str, dict[str, Any]], ...]) -> list[str]:
    lines = [title]
    for comment, arguments in steps:
        if comment:
            lines.append(f"# {comment}")
        lines.append(call_text(arguments))
    return lines


def plate_text() -> str:
    lines = _render("Plate 100 x 60 x 8 with a slot, two holes and rounded corners (doc new first):", PLATE)
    lines.append("")
    lines.extend(_render("Chamfers on sets of edges (an L plate):", CHAMFERS))
    return "\n".join(lines)
