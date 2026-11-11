"""Feature-first modelling through the real stdio server and FreeCAD: the recipe of cad_guide end to end, a world-frame
sketch and a world-point hole on a face, the hint on a boolean hole, and the document purpose surviving a reopen."""

import json
import math
from pathlib import Path

import pytest

from cotaforge_mcp import recipes
from tests.integration.conftest import running_server, volume

XY = {"kind": "plane", "name": "XY"}
TOP = {"kind": "face", "from_op": "s2", "select": {"normal": [0, 0, 1]}}
PLATE = {"on": XY, "entities": [{"kind": "rect", "name": "r", "center": [0, 0], "width": 100, "height": 60}]}


async def test_the_recipe_of_cad_guide_builds_the_plate_it_describes(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="recipe")
        text = ""
        for _comment, arguments in recipes.PLATE:
            text = await cad.ok("op", **arguments)
            assert "valid: yes" in text
        # 120 x 60 x 12 after the edits: slot, holes D8 and D6, corners rounded r5
        plate_area = 120 * 60 - (4 - math.pi) * 25
        expected = (plate_area - (40 * 10 + math.pi * 25) - math.pi * 16 - math.pi * 9) * 12
        assert volume(text) == pytest.approx(expected, rel=1e-4)


async def test_a_world_slot_and_a_world_hole_land_where_the_points_say(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="plate")
        await cad.ok("op", name="sketch", params=PLATE)
        await cad.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 8})
        slot = {"kind": "slot", "name": "s", "start": [-20, 10, 8], "end": [20, 10, 8], "width": 10}
        sketched = await cad.ok("op", name="sketch", params={"on": TOP, "frame": "world", "entities": [slot]})
        assert "world frame" in sketched
        cut = await cad.ok("op", name="cut", params={"sketch": "s3", "end_condition": "through_all"})
        assert volume(cut) == pytest.approx(48000 - (40 * 10 + math.pi * 25) * 8, rel=1e-6)
        hole = {"face": TOP, "at": [30, -15, 8], "standard": "simple", "diameter_mm": 6, "end_condition": "through_all"}
        drilled = await cad.ok("op", name="hole", params=hole)
        assert volume(drilled) == pytest.approx(48000 - (40 * 10 + math.pi * 25) * 8 - math.pi * 9 * 8, rel=1e-6)
        off = await cad.err("op", name="hole", params={**hole, "at": [80, 0, 8]})
        assert "30.000 mm outside" in off  # the face spans x -50..50
        far = {**slot, "end": [20, 10, 5]}
        assert "3 mm off the sketch plane" in await cad.err(
            "op", name="sketch", params={"on": TOP, "frame": "world", "entities": [far]}
        )


async def test_a_boolean_hole_gets_the_hint_and_a_sketch_document_does_not(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="block")
        await cad.ok("op", name="box", id="b", params={"size": [100, 60, 20]})
        await cad.ok("op", name="cylinder", id="t", params={"diameter_mm": 10, "height_mm": 30, "origin": [30, 30, -5]})
        tool = [{"kind": "body", "from_op": "t"}]
        subtract = {"target": {"kind": "body", "from_op": "b"}, "tools": tool}
        text = await cad.ok("op", name="boolean_subtract", id="c", params=subtract)
        assert "hint: this is a hole; use hole" in text
        example = next(line for line in text.splitlines() if line.startswith("hint:"))
        params = json.loads(example.split("params=", 1)[1][:-1])
        await cad.ok("undo")
        drilled = await cad.ok("op", name="hole", params=params)  # the hint's own example is a valid hole
        assert "valid: yes" in drilled
        await cad.ok("undo", to="t")
        await cad.ok("doc", action="set_purpose", purpose="sketch")
        assert "hint" not in await cad.ok("op", name="boolean_subtract", id="c2", params=subtract)


async def test_the_purpose_survives_a_save_and_a_reopen(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="kept")
        await cad.ok("op", name="box", id="b", params={"size": [10, 10, 10]})
        await cad.ok("doc", action="set_purpose", purpose="sketch")
        await cad.ok("doc", action="save", path="kept.FCStd")
        await cad.ok("doc", action="close")
        assert "purpose: sketch" in await cad.ok("doc", action="open", path="kept.FCStd")
