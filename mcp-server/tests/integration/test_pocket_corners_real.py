"""The alert for a milled pocket with corners no end mill can make, through the real stdio server and FreeCAD: a sharp
pocket and a slot too narrow for its depth fire, and a slot whose rounded ends are large enough does not."""

from pathlib import Path

from cotaforge_mcp.pocket_corners import ENTRY_ID
from tests.integration.conftest import running_server

XY = {"kind": "plane", "name": "XY"}
TOP = {"kind": "face", "from_op": "s2", "select": {"normal": [0, 0, 1]}}
BLOCK = {"on": XY, "entities": [{"kind": "rect", "name": "r", "center": [50, 30], "width": 100, "height": 60}]}
LINE = "memory: " + ENTRY_ID + " — recognize: "


def slot(width: float, y: float) -> dict:
    return {"kind": "slot", "name": "s", "start": [-20, y], "end": [20, y], "width": width}


def square(side: float) -> dict:
    return {"kind": "rect", "name": "r", "center": [0, 18], "width": side, "height": side}


async def pocket(cad, step: str, entity: dict, depth: float) -> str:  # noqa: ANN001
    await cad.ok("op", name="sketch", id=f"{step}_sk", params={"on": TOP, "entities": [entity]})
    return await cad.ok("op", name="cut", id=step, params={"sketch": f"{step}_sk", "depth_mm": depth})


async def test_sharp_and_too_narrow_pockets_fire_and_a_round_slot_does_not(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="block")
        await cad.ok("op", name="sketch", params=BLOCK)
        await cad.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 30})
        wide = await pocket(cad, "wide", slot(10, 0), 12)  # ends of radius 5, the smallest tool for 12 mm has 1.5
        assert LINE not in wide
        sharp = await pocket(cad, "sharp", square(10), 12)
        line = next(row for row in sharp.splitlines() if row.startswith(LINE))
        assert "4 vertical inside corners with radius 0" in line and "radius 1.5 mm" in line
        narrow = await pocket(cad, "narrow", slot(2, -20), 20)  # ends of radius 1, the smallest tool for 20 mm has 2.5
        line = next(row for row in narrow.splitlines() if row.startswith(LINE))
        assert "down to radius 1 mm" in line and "radius 2.5 mm" in line
