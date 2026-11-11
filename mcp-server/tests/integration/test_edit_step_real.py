"""``op(edit=...)`` through the real stdio server and FreeCAD: parameters of earlier steps change and the model
recomputes with analytic volumes, in place (PartDesign steps and Part primitives)."""

import math
from pathlib import Path

import pytest

from tests.integration.conftest import running_server, volume

L, W, H = 60.0, 40.0, 10.0
PLATE_SKETCH = {
    "on": {"kind": "plane", "name": "XY"},
    "entities": [{"kind": "rect", "name": "outline", "center": [30, 20], "width": L, "height": W}],
    "named_dimensions": [{"name": "plate_len", "entity": "outline", "attribute": "width"}],
}
TOP = {"kind": "face", "from_op": "s2", "select": {"normal": [0, 0, 1]}}
HOLE = {"face": TOP, "position": [10, 0], "standard": "simple", "diameter_mm": 8, "end_condition": "through_all"}


def drilled(diameter: float, depth: float = H, length: float = L) -> float:
    return length * W * depth - math.pi * (diameter / 2) ** 2 * depth


async def test_partdesign_steps_are_edited_in_place_and_what_follows_recomputes(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="plate")
        await cad.ok("op", name="sketch", params=PLATE_SKETCH)
        await cad.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": H})
        await cad.ok("op", name="hole", params=HOLE)

        hole = await cad.ok("op", edit="s3", params={"diameter_mm": 12})
        assert "diameter_mm 8 -> 12" in hole and "changed in place" in hole and "s3" in hole
        assert volume(hole) == pytest.approx(drilled(12), rel=1e-6)

        deeper = await cad.ok("op", edit="s2", params={"depth_mm": 15})
        assert "changed in place" in deeper and "recomputed: s2, s3" in deeper
        assert volume(deeper) == pytest.approx(drilled(12, 15), rel=1e-6)

        longer = await cad.ok("op", edit="s1", params={"dimensions": {"plate_len": 80}})
        assert "plate_len 60 -> 80" in longer
        assert volume(longer) == pytest.approx(drilled(12, 15, 80), rel=1e-6)

        # the history is the same one: ids kept, undo works step by step
        tree = await cad.ok("inspect", action="tree")
        assert "[s2]" in tree and "[s3]" in tree
        assert volume(await cad.ok("undo", to="s2")) == pytest.approx(80 * W * 15, rel=1e-6)


async def test_a_rebuild_that_fails_puts_the_original_back(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="plate")
        await cad.ok("op", name="sketch", params=PLATE_SKETCH)
        await cad.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": H})
        await cad.ok("op", name="hole", params={**HOLE, "position": [25, 0]})  # near the +X end of the 60 mm plate

        text = await cad.err("op", edit="s1", params={"dimensions": {"plate_len": 40}})
        assert "edit s1 was not applied" in text and "the cut s3 no longer removes any material" in text
        assert "state: unchanged" in text
        assert volume(await cad.ok("inspect", action="properties")) == pytest.approx(drilled(8), rel=1e-6)  # as it was

        again = await cad.ok("op", edit="s3", params={"diameter_mm": 6})  # the original model is intact and editable
        assert volume(again) == pytest.approx(drilled(6), rel=1e-6)


async def test_part_primitives_are_edited_in_place(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="block")
        await cad.ok("op", name="box", id="b", params={"size": [100, 60, 20]})
        await cad.ok("op", name="cylinder", id="t", params={"diameter_mm": 30, "height_mm": 20, "origin": [50, 30, 0]})
        await cad.ok(
            "op",
            name="boolean_subtract",
            id="c",
            params={"target": {"kind": "body", "from_op": "b"}, "tools": [{"kind": "body", "from_op": "t"}]},
        )
        text = await cad.ok("op", edit="t", params={"diameter_mm": 40})
        assert "changed in place" in text and "recomputed: t, c" in text
        assert volume(text) == pytest.approx(100 * 60 * 20 - math.pi * 20**2 * 20, rel=1e-6)
