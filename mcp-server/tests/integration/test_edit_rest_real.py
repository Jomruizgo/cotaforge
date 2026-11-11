"""Editing a design changes the dimension asked and nothing else, also when the edit rebuilds the steps (a changed fit
or thread) and after an undo: through the real stdio server and FreeCAD."""

import re
from pathlib import Path

from tests.integration.conftest import running_server

L, W, H = 60.0, 40.0, 10.0
PLATE_SKETCH = {
    "on": {"kind": "plane", "name": "XY"},
    "entities": [{"kind": "rect", "name": "outline", "center": [30, 20], "width": L, "height": W}],
}
TOP = {"kind": "face", "from_op": "s2", "select": {"normal": [0, 0, 1]}}
FIRST = {"face": TOP, "position": [10, 0], "standard": "simple", "diameter_mm": 8, "end_condition": "through_all"}
SECOND = {"face": TOP, "position": [25, 0], "standard": "simple", "diameter_mm": 4, "end_condition": "through_all"}
AXIS = re.compile(r"D(\S+) .*?axis from \(?\[?([-\d.e]+), ([-\d.e]+), ([-\d.e]+)")


def axes(text: str, diameter: float) -> list[tuple[float, float, float]]:
    found = []
    for line in text.splitlines():
        match = AXIS.search(line)
        if match and abs(float(match.group(1)) - diameter) < 1e-6:
            found.append(tuple(float(match.group(i)) for i in (2, 3, 4)))
    return sorted(found)


def moved(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return sum((p - q) ** 2 for p, q in zip(a, b, strict=True)) ** 0.5


async def build(cad, *holes: dict) -> None:  # noqa: ANN001
    await cad.ok("doc", action="new", name="plate")
    await cad.ok("op", name="sketch", params=PLATE_SKETCH)
    await cad.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": H})
    for hole in holes:
        await cad.ok("op", name="hole", params=hole)


async def test_a_rebuild_keeps_the_place_of_a_later_hole(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await build(cad, FIRST, SECOND)
        before = axes(await cad.ok("inspect", action="holes"), 4)
        assert len(before) == 1, before
        reply = await cad.ok("op", edit="s3", params={"standard": "iso_tapped", "size": "M10"})
        assert "rebuilt from s3" in reply, reply
        after = axes(await cad.ok("inspect", action="holes"), 4)
        assert len(after) == 1, after
        assert moved(before[0], after[0]) < 1e-6, (before, after, reply)


async def test_a_hole_can_be_edited_after_an_edit_and_an_undo(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await build(cad, FIRST)
        await cad.ok("op", edit="s2", params={"depth_mm": 14})
        await cad.ok("op", name="hole", params=SECOND)
        await cad.ok("undo", to="s3")
        reply = await cad.ok("op", edit="s3", params={"diameter_mm": 12})
        assert "diameter_mm 8 -> 12" in reply, reply


async def test_a_rebuild_keeps_the_place_of_a_pocket_sketched_on_the_face(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await build(cad, FIRST)
        circle = {"kind": "circle", "name": "c", "center": [25, 0], "diameter": 4}
        await cad.ok("op", name="sketch", id="pk", params={"on": TOP, "entities": [circle]})
        await cad.ok("op", name="cut", params={"sketch": "pk", "depth_mm": 3})
        before = axes(await cad.ok("inspect", action="holes"), 4)
        assert len(before) == 1, before
        reply = await cad.ok("op", edit="s3", params={"standard": "iso_tapped", "size": "M10"})
        assert "rebuilt from s3" in reply and "could not stay" not in reply, reply
        after = axes(await cad.ok("inspect", action="holes"), 4)
        assert len(after) == 1 and moved(before[0], after[0]) < 1e-6, (before, after)


async def test_a_rebuild_still_follows_the_face_when_the_plate_is_thicker(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await build(cad, FIRST, SECOND)
        await cad.ok("op", edit="s2", params={"depth_mm": 14})
        before = axes(await cad.ok("inspect", action="holes"), 4)
        reply = await cad.ok("op", edit="s3", params={"standard": "iso_tapped", "size": "M10"})
        assert "rebuilt from s3" in reply, reply
        after = axes(await cad.ok("inspect", action="holes"), 4)
        assert len(after) == 1 and moved(before[0], after[0]) < 1e-6, (before, after)
        assert "volume: 32" in reply, reply
