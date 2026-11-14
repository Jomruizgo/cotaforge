# ruff: noqa: E501
"""Circular hole patterns on the REAL FreeCAD (Windows, VISIBLE), in both drawing modes: the sheet draws the pitch circle as a
chain circle, a radial centre line through each hole and each centre mark turned along its radius, dimensions the pitch circle
diameter (and the angle of the first hole when it is not on a centre line), and gives the holes no X / Y. Four made-up parts:
a flange (8 tapped holes and one lone small hole on a radius), a plate with six holes on a pitch circle off its centre around a
bore, and the mounting plate and the flanged cover of the drawing bench.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the images of the sheets.
"""

import base64
import math
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.integration.conftest import ORDER, Cad, Reply, running_server
from tests.integration.test_drawing_compose_real import compose_the_rest, in_one_order

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

XY = {"kind": "plane", "name": "XY"}
TOP = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}
TAPPED = {"standard": "iso_tapped", "size": "M8", "diameter_mm": 6.8, "end_condition": "through_all", "thread_depth_mm": 12}


def shot(shots: Path, name: str, reply: Reply) -> None:
    assert reply.images, reply.text
    shots.mkdir(parents=True, exist_ok=True)
    (shots / f"{name}.png").write_bytes(base64.b64decode(reply.images[0].data))


async def flange(cad: Cad) -> list[float]:
    """Ø140 x 12, bore Ø30, 8 M8 on Ø80 (first at 22.5 degrees) and one Ø4 hole at R25, 30 degrees. Returns the axis point of that lone hole."""
    await cad.ok("doc", action="new", name="flange")
    await cad.ok("doc", action="set_material", material="C45")
    await cad.ok("op", name="sketch", id="outline", params={"on": XY, "entities": [{"kind": "circle", "name": "od", "center": [0, 0], "diameter": 140}]})
    await cad.ok("op", name="extrude", id="base", params={"sketch": "outline", "depth_mm": 12})
    await cad.ok("op", name="hole", id="bore", params={"face": TOP, "at": [0, 0, 12], "standard": "simple", "diameter_mm": 30, "end_condition": "through_all"})
    for i in range(8):
        a = math.radians(22.5 + 45 * i)
        await cad.ok("op", name="hole", id=f"m8_{i + 1}", params={"face": TOP, "at": [round(40 * math.cos(a), 4), round(40 * math.sin(a), 4), 12], **TAPPED})
    a = math.radians(30)
    lone = [round(25 * math.cos(a), 4), round(25 * math.sin(a), 4), 12]
    await cad.ok("op", name="hole", id="pin", params={"face": TOP, "at": lone, "standard": "simple", "diameter_mm": 4, "end_condition": "through_all"})
    return lone


async def offset_plate(cad: Cad) -> None:
    """150 x 100 x 12, bore Ø20 at (25, -10) and six Ø6.6 on a Ø45 pitch circle about it (first at 15 degrees)."""
    await cad.ok("doc", action="new", name="offset-plate")
    await cad.ok("doc", action="set_material", material="S355J2")
    await cad.ok("op", name="sketch", id="outline", params={"on": XY, "entities": [{"kind": "rect", "name": "plate", "center": [0, 0], "width": 150, "height": 100}]})
    await cad.ok("op", name="extrude", id="base", params={"sketch": "outline", "depth_mm": 12})
    await cad.ok("op", name="hole", id="bore", params={"face": TOP, "at": [25, -10, 12], "standard": "simple", "diameter_mm": 20, "end_condition": "through_all"})
    for i in range(6):
        a = math.radians(15 + 60 * i)
        at = [round(25 + 22.5 * math.cos(a), 4), round(-10 + 22.5 * math.sin(a), 4), 12]
        await cad.ok("op", name="hole", id=f"h{i + 1}", params={"face": TOP, "at": at, "standard": "simple", "diameter_mm": 6.6, "end_condition": "through_all"})


async def bench_plate(cad: Cad) -> None:
    """The mounting plate of the drawing bench: 150 x 100 x 12, bore Ø42 H7, 6 M8 on Ø70 (first on +X), 4 Ø11 in a 120 x 70 rectangle."""
    await cad.ok("doc", action="new", name="mounting-plate")
    await cad.ok("doc", action="set_material", material="S355J2")
    await cad.ok("op", name="sketch", id="outline", params={"on": XY, "entities": [{"kind": "rect", "name": "plate", "center": [0, 0], "width": 150, "height": 100, "chamfer_mm": 5}]})
    await cad.ok("op", name="extrude", id="base", params={"sketch": "outline", "depth_mm": 12})
    await cad.ok("op", name="hole", id="pilot", params={"face": TOP, "at": [0, 0, 12], "standard": "simple", "diameter_mm": 42, "end_condition": "through_all", "fit": "H7"})
    for i in range(6):
        a = math.radians(60 * i)
        await cad.ok("op", name="hole", id=f"m8_{i + 1}", params={"face": TOP, "at": [round(35 * math.cos(a), 4), round(35 * math.sin(a), 4), 12], **TAPPED})
    for i, (x, y) in enumerate([(-60, -35), (60, -35), (60, 35), (-60, 35)], start=1):
        await cad.ok("op", name="hole", id=f"fix_{i}", params={"face": TOP, "at": [x, y, 12], "standard": "iso_clearance", "size": "M10", "diameter_mm": 11, "end_condition": "through_all"})


async def bench_cover(cad: Cad) -> None:
    """The flanged cover of the drawing bench (turned, axis along X): 4 Ø9 on a Ø80 pitch circle at 45 degrees from the axes."""
    profile = [(0, 21), (0, 50), (10, 50), (10, 31), (17, 31), (18, 30), (18, 17), (8, 17), (8, 21)]
    await cad.ok("doc", action="new", name="flanged-cover")
    await cad.ok("doc", action="set_material", material="C45")
    entities = [{"kind": "line", "name": f"l{i}", "start": list(a), "end": list(b)} for i, (a, b) in enumerate(zip(profile, profile[1:] + profile[:1], strict=True))]
    await cad.ok("op", name="sketch", id="profile", params={"on": XY, "entities": entities})
    await cad.ok("op", name="revolve", id="cover", params={"sketch": "profile", "axis": "u"})
    face = {"kind": "face", "from_op": "cover", "select": {"normal": [-1, 0, 0], "largest": True}}
    for i in range(4):
        a = math.radians(45 + 90 * i)
        await cad.ok("op", name="hole", id=f"bolt_{i + 1}", params={"face": face, "at": [0, round(40 * math.cos(a), 4), round(40 * math.sin(a), 4)], "standard": "iso_clearance", "size": "M8", "diameter_mm": 9, "end_condition": "through_all"})


PARTS = {"flange": flange, "offset_plate": offset_plate, "bench_plate": bench_plate, "bench_cover": bench_cover}


async def steps_sheet(cad: Cad, shots: Path, name: str, views: list[str] | None, extra: list[dict[str, Any]]) -> str:
    created = await cad.ok("drawing", action="create", template="A3", **({"views": views} if views else {}))
    print("CREATE", name, created)
    await cad.ok("drawing", action="add", items=[{"type": "hole_dim", "all": True}, {"type": "view_lines"}, {"type": "overall"}, *extra])
    listed = await cad.ok("drawing", action="list")
    print("LIST", name, listed)
    shot(shots, f"steps_{name}", await cad("drawing", action="capture"))
    review = await cad.ok("drawing", action="review")
    print("REVIEW", name, review)
    return review + listed


@pytest.mark.parametrize("which", list(PARTS))
async def test_steps_mode_dimensions_the_pattern_from_its_axis(workspace: Path, tmp_path: Path, which: str) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        lone = await PARTS[which](cad)
        extra: list[dict[str, Any]] = []
        if which == "flange":  # the lone pin on a radius: its function is the engineer's, so he asks for it
            extra = [{"type": "pattern_dim", "at": lone}, {"type": "pattern_dim", "at": lone, "kind": "angle"}]
        views = None if which == "bench_cover" else ["top", "iso"]
        text = await steps_sheet(cad, shots, which, views, extra)
        assert "pd1" in text and "pattern_not_polar" not in text, text


@pytest.mark.parametrize("which", list(PARTS))
async def test_sheet_mode_dimensions_the_pattern_from_its_axis(workspace: Path, tmp_path: Path, which: str) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await in_one_order(cad)
        await PARTS[which](cad)
        composed = await compose_the_rest(cad, {"sheet": {"symmetry": True, "title": which}}, **({"template": "A3"}))
        print("COMPOSED", which, composed)
        listed = await cad.ok("drawing", action="list")
        print("LIST", which, listed)
        shot(shots, f"sheet_{which}", await cad("drawing", action="capture"))
        review = await cad.ok("drawing", action="review")
        print("REVIEW", which, review)
        assert "pd1" in listed and "pattern_not_polar" not in review, review + listed
        assert ORDER  # the engineer the test plays orders the answers; nothing is asked of him about the pattern
