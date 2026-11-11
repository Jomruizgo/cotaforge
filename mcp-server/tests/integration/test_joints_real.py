"""The trapdoor of the real use, through the real server on the real FreeCAD: a plate on the ground, a lid on a hinge
along one of the plate's edges, a fixed obstacle above. The hinge axis comes from an edge of the plate, the sweep finds
the first overlap at the analytic angle (within the step), and the assembly saves and resumes with the joint."""

import math
import re
from pathlib import Path

import pytest

from tests.integration.conftest import Cad, running_server

LID_LENGTH, LID_THICKNESS, CEILING = 100.0, 5.0, 90.0


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COTAFORGE_HOME", str(tmp_path / "home"))


def analytic_clash_angle() -> float:
    reach = math.hypot(LID_LENGTH, LID_THICKNESS)
    return math.degrees(math.asin(CEILING / reach) - math.atan2(LID_THICKNESS, LID_LENGTH))


def slab(width: float, depth: float) -> dict:
    return {
        "on": {"kind": "plane", "name": "XY"},
        "entities": [
            {"kind": "rect", "name": "outline", "center": [width / 2, depth / 2], "width": width, "height": depth}
        ],
    }


async def part(cad: Cad, name: str, width: float, depth: float, thickness: float) -> None:
    await cad.ok("doc", action="new", name=name)
    await cad.ok("op", name="sketch", id=f"{name}_sketch", params=slab(width, depth))
    await cad.ok("op", name="extrude", id=f"{name}_pad", params={"sketch": f"{name}_sketch", "depth_mm": thickness})
    await cad.ok("doc", action="save", path=f"parts/{name}.FCStd")


def face(from_op: str, normal: list[float]) -> dict:
    return {"kind": "face", "from_op": from_op, "select": {"normal": normal}}


async def scene(cad: Cad) -> None:
    await part(cad, "plate", 100, 100, 10)
    await part(cad, "lid", LID_LENGTH, 100, LID_THICKNESS)
    await part(cad, "block", 120, 100, 20)
    await cad.ok("doc", action="new", name="trapdoor", kind="assembly")
    await cad.ok("op", name="insert_component", id="plate_i", params={"part": "plate", "placement": {}})
    await cad.ok("op", name="insert_component", id="lid_i", params={"part": "lid", "placement": {"origin": [0, 0, 10]}})
    await cad.ok(
        "op",
        name="insert_component",
        id="block_i",
        params={"part": "block", "placement": {"origin": [0, 0, 10 + CEILING]}},
    )


HINGE = {
    "type": "revolute",
    "a": "plate_i",
    "b": "lid_i",
    "axis": {"kind": "edge", "between": [face("plate_i", [0, 0, 1]), face("plate_i", [-1, 0, 0])]},
    "min": -120,
    "max": 120,
}


def lifting_sign(text: str) -> float:
    """+1 when a positive value lifts the lid. The axis comes from an edge (start to end, as the CAD orders it): along
    -Y a positive angle lifts the lid, along +Y a negative one does."""
    match = re.search(r"direction \((-?[\d.]+), (-?[\d.]+), (-?[\d.]+)\)", text)
    assert match, text
    return -1.0 if float(match.group(2)) > 0 else 1.0


async def test_the_trapdoor_sweep_finds_the_first_overlap_at_the_analytic_angle_and_resumes(workspace: Path) -> None:
    expected = analytic_clash_angle()
    async with running_server(workspace) as (cad, _):
        await scene(cad)
        made = await cad.ok("op", name="add_joint", id="hinge", params=HINGE)
        assert "joint hinge (joint:1): revolute" in made and "to comp:1 [plate_i]" in made, made
        assert "origin (0, " in made and ", 10)" in made  # the plate's top edge at x=0
        sign = lifting_sign(made)

        opened = await cad.ok("op", name="set_joint", id="open", params={"joint": "hinge", "value": 30 * sign})
        assert f"value {30 * sign:g} deg" in opened, opened

        swept = await cad.ok("inspect", action="sweep", id="hinge", range=[0, 90 * sign, 1])
        assert "91 positions checked" in swept and "every 1 deg" in swept, swept
        match = re.search(r"first overlap: at (-?[\d.]+) deg", swept)
        assert match, swept
        assert abs(abs(float(match.group(1))) - expected) <= 1.0, (swept, expected)
        assert "lid_i" in swept and "block_i" in swept
        closest = re.search(r"block_i\]: OVERLAPS from (-?[\d.]+) deg; least clearance before it ([\d.]+) mm at", swept)
        assert closest and 0 < float(closest.group(2)) < 2.0, swept  # it got within a couple of mm before the clash
        assert "the joint is back at " + f"{30 * sign:g} deg" in swept  # restored to where it was before the sweep
        assert "no collision" not in swept.lower()

        await cad.ok("doc", action="save")

    async with running_server(workspace) as (cad, _):
        reopened = await cad.ok("doc", action="open", path="trapdoor.FCStd")
        assert "session restored: step ids and handles kept" in reopened, reopened
        again = await cad.ok("op", name="set_joint", params={"joint": "hinge", "value": 10 * sign})
        assert "joint hinge (joint:1): revolute" in again and "value" in again
        swept = await cad.ok("inspect", action="sweep", id="hinge", range=[0, 90 * sign, 1])
        match = re.search(r"first overlap: at (-?[\d.]+) deg", swept)
        assert match and abs(abs(float(match.group(1))) - expected) <= 1.0, swept


async def test_a_jointed_lid_is_not_placed_by_hand_and_undo_returns_it(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await scene(cad)
        await cad.ok("op", name="add_joint", id="hinge", params=HINGE)

        refusal = await cad.err("op", name="place_component", params={"component": "lid_i", "placement": {}})
        assert "joined by joint joint:1" in refusal and "set_joint" in refusal

        await cad.ok("undo")
        await cad.ok("op", name="place_component", params={"component": "lid_i", "placement": {"origin": [0, 0, 12]}})
