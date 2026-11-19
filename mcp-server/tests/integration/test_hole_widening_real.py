"""A counterbore or a countersink made of two steps gets the hint to use ONE hole, through the real stdio server and
FreeCAD: a coaxial pocket after a hole, a hole after a coaxial pocket; and the hint's own edit (counterbore= on the
hole) builds the same part. (The cone of a countersink is covered on synthetic faces in test_hints_widened.)"""

from pathlib import Path
from typing import Any

import pytest

from tests.integration.conftest import running_server, volume

XY = {"kind": "plane", "name": "XY"}
PLATE = {"on": XY, "entities": [{"kind": "rect", "name": "r", "center": [0, 0], "width": 150, "height": 100}]}
TOP = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1], "largest": True}}
M8 = {"face": TOP, "standard": "simple", "diameter_mm": 9, "end_condition": "through_all"}


def pocket_sketch(x: float, y: float) -> dict:
    circle = {"kind": "circle", "name": "c", "center": [x, y, 20], "diameter": 15}
    return {"on": TOP, "frame": "world", "entities": [circle]}


async def plate(cad: Any) -> None:
    await cad.ok("doc", action="new", name="plate")
    await cad.ok("op", name="sketch", id="o", params=PLATE)
    await cad.ok("op", name="extrude", id="base", params={"sketch": "o", "depth_mm": 20})


async def test_a_pocket_after_a_hole_hints_the_counterbore_and_the_edit_it_names_builds_it(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await plate(cad)
        await cad.ok("op", name="hole", id="h1", params={**M8, "at": [60, 35, 20]})
        await cad.ok("op", name="sketch", id="pk", params=pocket_sketch(60, 35))
        cut = await cad.ok(
            "op", name="cut", id="pocket", params={"sketch": "pk", "end_condition": "blind", "depth_mm": 8.5}
        )
        hint = next(line for line in cut.splitlines() if line.startswith("hint: pocket is a counterbore of hole h1"))
        assert "D9" in hint and "D15" in hint and '"depth_mm":8.5' in hint
        by_hand = volume(cut)
        await cad.ok("undo", to="h1")
        edited = await cad.ok("op", edit="h1", params={"counterbore": {"diameter_mm": 15, "depth_mm": 8.5}})
        assert "hint" not in edited
        assert volume(edited) == pytest.approx(by_hand, rel=1e-6)


async def test_a_hole_after_a_pocket_hints_it_too(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await plate(cad)
        await cad.ok("op", name="sketch", id="pk", params=pocket_sketch(-60, 35))
        await cad.ok("op", name="cut", id="pocket", params={"sketch": "pk", "end_condition": "blind", "depth_mm": 8.5})
        floor = {"kind": "face", "from_op": "pocket", "select": {"normal": [0, 0, 1], "center": [-60, 35, 11.5]}}
        hole = await cad.ok("op", name="hole", id="h2", params={**M8, "face": floor, "at": [-60, 35, 11.5]})
        assert "hint: pocket is a counterbore of hole h2" in hole
