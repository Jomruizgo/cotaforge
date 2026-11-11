"""Arcs and slots through the real stdio server and FreeCAD: a slot cut, a profile of lines and arcs, a slot width
edited by name, and the sketch status of a fully constrained arc profile."""

import math
from pathlib import Path

import pytest

from tests.integration.conftest import running_server, volume

XY = {"kind": "plane", "name": "XY"}
PLATE = {"on": XY, "entities": [{"kind": "rect", "name": "r", "center": [50, 30], "width": 100, "height": 60}]}
SLOT = {"kind": "slot", "name": "s", "start": [30, 30], "end": [70, 30], "width": 10}
BAR = [
    {"kind": "line", "name": "top", "start": [10, 5], "end": [50, 5]},
    {"kind": "arc3", "name": "right", "start": [50, 5], "through": [55, 0], "end": [50, -5]},
    {"kind": "line", "name": "bottom", "start": [50, -5], "end": [10, -5]},
    {"kind": "arc_ends", "name": "left", "center": [10, 0], "start": [10, -5], "end": [10, 5], "ccw": False},
]


def slot_area(width: float) -> float:
    return 40 * width + math.pi * (width / 2) ** 2


async def test_a_slot_is_cut_and_its_width_edited_by_name(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="slotted")
        await cad.ok("op", name="sketch", params=PLATE)
        await cad.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 8})
        dims = [{"name": "slot_w", "entity": "s", "attribute": "width"}]
        await cad.ok("op", name="sketch", params={"on": XY, "entities": [SLOT], "named_dimensions": dims})
        cut = await cad.ok(
            "op", name="cut", params={"sketch": "s3", "end_condition": "through_all", "direction": "positive"}
        )
        assert volume(cut) == pytest.approx(48000 - slot_area(10) * 8, rel=1e-6)
        wider = await cad.ok("op", edit="s3", params={"dimensions": {"slot_w": 14}})
        assert "slot_w 10 -> 14" in wider and "changed in place" in wider and "recomputed: s2, s4" in wider
        assert volume(wider) == pytest.approx(48000 - slot_area(14) * 8, rel=1e-6)
        assert "fully constrained" in await cad.ok("inspect", action="sketch_status", sketch="s3")


async def test_a_profile_of_lines_and_arcs_extrudes_with_its_exact_area(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="bar")
        sketched = await cad.ok("op", name="sketch", params={"on": XY, "entities": BAR})
        assert f"area {slot_area(10):.3f}".rstrip("0") in sketched
        extruded = await cad.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 6})
        assert volume(extruded) == pytest.approx(slot_area(10) * 6, rel=1e-6)
        assert "valid: yes" in extruded
        assert "fully constrained" in await cad.ok("inspect", action="sketch_status", sketch="s1")
