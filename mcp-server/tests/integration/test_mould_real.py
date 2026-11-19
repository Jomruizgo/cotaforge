"""The mould tools through the real stdio server and FreeCAD: ``inspect(action=draft)`` finds the walls without
draft and the undercut of a hole across the pull, ``op draft`` tapers the walls, and ``op shrink_body`` derives the cavity."""

import re
from pathlib import Path

from tests.integration.conftest import running_server

FOOTPRINT = {
    "on": {"kind": "plane", "name": "XY"},
    "entities": [{"kind": "rect", "name": "r", "center": [20, 15], "width": 40, "height": 30}],
}


def _counts(text: str) -> tuple[int, int]:
    """(undercut, zero) from the first line of an ``inspect draft`` answer."""
    found = re.search(r"(\d+) undercut, (\d+) zero", text.splitlines()[0])
    assert found, text
    return int(found.group(1)), int(found.group(2))


def _wall(normal: list[int]) -> dict:
    return {"kind": "face", "from_op": "block", "select": {"normal": normal}}


async def test_draft_analysis_draft_and_cavity(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="moulded")
        await cad.ok("op", name="sketch", id="s1", intent="footprint", params=FOOTPRINT)
        await cad.ok("op", name="extrude", id="block", intent="block", params={"sketch": "s1", "depth_mm": 20})
        await cad.ok(
            "op",
            name="hole",
            id="cable",
            intent="cable entry across the pull",
            params={
                "face": _wall([1, 0, 0]),
                "at": [40, 15, 10],
                "standard": "simple",
                "diameter_mm": 6,
                "end_condition": "through_all",
            },
        )

        before = await cad.ok("inspect", action="draft", pull=[0, 0, 1], min_draft_deg=1.5)
        assert before.splitlines()[0].startswith("draft against the pull (0, 0, 1), minimum 1.5 deg: ")
        undercut, zero = _counts(before)
        assert undercut >= 1 and zero == 4  # the hole across the pull; the four walls (the +X one has the hole)

        taper = await cad.ok(
            "op",
            name="draft",
            id="taper",
            intent="walls 2 degrees, the mould half moves up",
            params={
                "faces": [_wall([0, 1, 0]), _wall([0, -1, 0]), _wall([-1, 0, 0])],
                "neutral": {"kind": "plane", "name": "XY"},
                "pull": "Z",
                "angle_deg": 2,
            },
        )
        assert "ok: taper draft" in taper
        after = await cad.ok("inspect", action="draft", pull=[0, 0, 1], min_draft_deg=1.5)
        assert _counts(after)[1] == 1  # only the +X wall, with the hole, is left without draft
        assert "draft 2 deg" not in "\n".join(line for line in after.splitlines() if line.startswith("face:"))

        cavity = await cad.ok(
            "op",
            name="shrink_body",
            id="cavity",
            intent="cavity of the block",
            params={
                "source": {"kind": "body", "from_op": "block"},
                "shrinkage_percent": [0.3, 0.9, 0.6],
                "factor_source": "test value (made up)",
            },
        )
        assert "ok: cavity shrink_body" in cavity
        refused = await cad.err(
            "op",
            name="shrink_body",
            id="bad",
            intent="no source",
            params={"source": {"kind": "body", "from_op": "block"}, "shrinkage_percent": 0.5},
        )
        assert "factor_source" in refused
