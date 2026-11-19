"""The alert for a turned part whose free end has a bare outer edge, through the real stdio server and FreeCAD: a
stepped shaft with one chamfered end and one bare end fires only on the bare one, and closes once that end is
chamfered."""

from pathlib import Path

from cotaforge_mcp.turned_ends import ENTRY_ID
from tests.integration.conftest import running_server

LINE = "memory: " + ENTRY_ID + " — recognize: "


def _circles(diameter: float) -> dict:
    """The convex circular edges of one diameter (a free end, or a shoulder: the junction of a step is concave)."""
    length = 3.141592653589793 * diameter
    return {
        "kind": "edges",
        "from_op": "shaft",
        "where": {"curve": "circle", "convex": True, "length_mm": {"min": length - 0.5, "max": length + 0.5}},
    }


async def test_only_the_bare_end_fires_and_chamfering_it_closes_the_alert(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="shaft")
        seen = ""
        for step, diameter, height, z in (("a", 20, 40, 0), ("b", 30, 60, 40), ("c", 24, 40, 100)):
            seen += await cad.ok(
                "op",
                name="cylinder",
                id=step,
                params={"diameter_mm": diameter, "height_mm": height, "origin": [0, 0, z]},
            )
        for step, target, tool in (("ab", "a", "b"), ("shaft", "ab", "c")):
            seen += await cad.ok("op", name="boolean_union", id=step, params={"target": target, "tools": [tool]})
        # each line is said once: the two free ends of the finished shaft were both said on the way
        assert "the free end at Z=0 mm" in seen and "the free end at Z=140 mm" in seen and "Ø24 mm" in seen
        chamfered = await cad.ok("op", name="chamfer", id="end_a", params={"edges": [_circles(20)], "distance1_mm": 1})
        assert "Z=0 mm" not in chamfered
        review = await cad.err("memory", action="review", stage="detail")
        assert "Z=140 mm" in review and "Z=0 mm" not in review and "Ø24 mm" in review
        await cad.ok("op", name="chamfer", id="end_c", params={"edges": [_circles(24)], "distance1_mm": 1})
        assert ENTRY_ID not in await cad.ok("memory", action="review", stage="detail")
