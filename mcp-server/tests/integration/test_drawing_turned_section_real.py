# ruff: noqa: E501
"""A part of revolution with only coaxial features is drawn in ONE axial section (and the isometric), on the real FreeCAD
(Windows, VISIBLE): a stepped hollow ring gets the section and no end view; the same ring with four holes on a pitch circle
keeps its end view; a solid stepped pin keeps a plain outside view and no section. Both flows are played: the steps one
(``drawing create``, then the dimensions) and the one-order one (``plan`` and ``compose``). The values are made up.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the images of the sheets.
"""

import base64
import math
import os
import re
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import Cad, Reply, pending_points, running_server
from tests.integration.test_drawing_compose_real import compose_the_rest, in_one_order

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

XY = {"kind": "plane", "name": "XY"}
FLANGE_TOP = {"kind": "face", "from_op": "flange", "select": {"normal": [0, 0, 1], "largest": True}}
BOSS_TOP = {"kind": "face", "from_op": "boss", "select": {"normal": [0, 0, 1], "center": [0, 0, 30]}}
FACE = re.compile(r"(face:\d+)\b[^\n]*")


def shot(shots: Path, name: str, reply: Reply) -> None:
    assert reply.images, reply.text
    shots.mkdir(parents=True, exist_ok=True)
    (shots / f"{name}.png").write_bytes(base64.b64decode(reply.images[0].data))


def shots_dir(tmp_path: Path) -> Path:
    return Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")


async def make_ring(cad: Cad, holes: int = 0) -> None:
    """A stepped hollow ring, all coaxial: a flange Ø80 x 12, a boss Ø56 x 18 on it and a Ø30 bore with a Ø44 x 10
    counterbore; with ``holes`` a pitch circle of Ø9 holes through the flange at radius 34."""
    await cad.ok("doc", action="new", name="anillo")
    await cad.ok("op", name="sketch", id="s1", params={"on": XY, "entities": [{"kind": "circle", "name": "od", "center": [0, 0], "diameter": 80}]})
    await cad.ok("op", name="extrude", id="flange", params={"sketch": "s1", "depth_mm": 12})
    top = {"kind": "face", "from_op": "flange", "select": {"normal": [0, 0, 1], "largest": True}}
    await cad.ok("op", name="sketch", id="s2", params={"on": top, "entities": [{"kind": "circle", "name": "boss", "center": [0, 0], "diameter": 56}]})
    await cad.ok("op", name="extrude", id="boss", params={"sketch": "s2", "depth_mm": 18})
    bore = {"face": BOSS_TOP, "at": [0, 0, 30], "standard": "simple", "diameter_mm": 30, "end_condition": "through_all", "counterbore": {"diameter_mm": 44, "depth_mm": 10}}
    await cad.ok("op", name="hole", id="bore", params=bore)
    for k in range(holes):
        x, y = 34 * math.cos(math.pi / 2 * k), 34 * math.sin(math.pi / 2 * k)
        params = {"face": FLANGE_TOP, "at": [round(x, 3), round(y, 3), 12], "standard": "simple", "diameter_mm": 9, "end_condition": "through_all"}
        await cad.ok("op", name="hole", id=f"h{k + 1}", params=params)


async def make_pin(cad: Cad) -> None:
    """A solid stepped pin: Ø20 x 40 with a Ø12 x 20 spigot on it, nothing inside."""
    await cad.ok("doc", action="new", name="pasador")
    await cad.ok("op", name="sketch", id="s1", params={"on": XY, "entities": [{"kind": "circle", "name": "od", "center": [0, 0], "diameter": 20}]})
    await cad.ok("op", name="extrude", id="flange", params={"sketch": "s1", "depth_mm": 40})
    top = {"kind": "face", "from_op": "flange", "select": {"normal": [0, 0, 1], "largest": True}}
    await cad.ok("op", name="sketch", id="s2", params={"on": top, "entities": [{"kind": "circle", "name": "spigot", "center": [0, 0], "diameter": 12}]})
    await cad.ok("op", name="extrude", id="boss", params={"sketch": "s2", "depth_mm": 20})


async def face_of(cad: Cad, surface: str, wanted: str) -> str:
    """The id of the face of ``surface`` whose line in ``inspect faces`` holds ``wanted`` (``r=28``, ``c(0, 0, 12)``)."""
    text = await cad.ok("inspect", action="faces", surface=surface)
    line = next(line for line in text.splitlines() if wanted in line)
    found = FACE.match(line.strip())
    assert found, text
    return found.group(1)


async def test_a_stepped_hollow_ring_is_one_section_in_steps_mode(workspace: Path, tmp_path: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        await make_ring(cad)
        created = await cad.ok("drawing", action="create", title="Anillo escalonado")  # nobody chose views, paper or scale
        print("CREATED", created)
        assert "views: front, isometric" in created, created  # the section is the main view: no end view
        assert "in place of the front view" in created and "no cutting line or letters" in created, created
        assert "an end view would only repeat circles" in created, created
        # the engineer's dimensions, all on the section: the boss Ø (the flange Ø is the overall one, the bore ones are the
        # callout) and the height of the flange, direct from the seating face (the underside), never chained from another step
        seat = await face_of(cad, "plane", "c(0, 0, 0)")
        flange_top = await face_of(cad, "plane", "c(0, 0, 12)")
        boss = await face_of(cad, "cylinder", "r=28")
        items = [
            {"type": "dim", "id": "d_boss", "kind": "diameter", "face": boss, "view": "A-A"},
            {"type": "dim", "id": "h_flange", "kind": "distance", "from": {"face": seat}, "to": {"face": flange_top}, "view": "A-A"},
        ]
        added = await cad.ok("drawing", action="add", items=items)
        print("ADDED", added)
        assert "error" not in added.lower().replace("no error", ""), added
        review = await cad.ok("drawing", action="review")
        findings, _, _ = await pending_points(cad)
        print("FINDINGS", findings)
        assert not any(f.startswith(("view_adds_nothing", "no_view_lines", "hole_not_located")) for f in findings), review
        shot(shots_dir(tmp_path), "ring_steps", await cad("drawing", action="capture"))


async def test_the_ring_with_a_pitch_circle_of_holes_keeps_its_end_view(workspace: Path, tmp_path: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        await make_ring(cad, holes=4)
        created = await cad.ok("drawing", action="create", title="Anillo con agujeros")
        print("CREATED", created)
        assert "views: top, isometric" in created and "a section A-A through the axis" in created, created
        assert "in place of the front view" not in created, created
        shot(shots_dir(tmp_path), "ring_holes_steps", await cad("drawing", action="capture"))


async def test_a_solid_stepped_pin_is_a_plain_outside_view_no_section(workspace: Path, tmp_path: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        await make_pin(cad)
        created = await cad.ok("drawing", action="create", title="Pasador")
        print("CREATED", created)
        assert "views: front, isometric" in created and "ONE main view" in created, created
        assert "A-A" not in created and "in place of" not in created, created
        shot(shots_dir(tmp_path), "pin_steps", await cad("drawing", action="capture"))


async def test_the_ring_is_composed_in_one_order_as_one_section(workspace: Path, tmp_path: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        await in_one_order(cad)
        await make_ring(cad)
        composed = await compose_the_rest(cad, {"sheet": {"title": "Anillo escalonado"}, "finish": [{"general": True, "ra": 3.2}]}, keep=("req.surface_texture",))
        print("COMPOSED", composed)
        assert "composed sheet" in composed, composed
        shot(shots_dir(tmp_path), "ring_compose", await cad("drawing", action="capture"))
        print("REVIEW", await cad.ok("drawing", action="review"))


async def test_the_ring_with_holes_and_the_pin_are_composed_too(workspace: Path, tmp_path: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        await in_one_order(cad)
        await make_ring(cad, holes=4)
        composed = await compose_the_rest(cad, {"sheet": {"title": "Anillo con agujeros", "symmetry": True}, "finish": [{"general": True, "ra": 3.2}]}, keep=("req.surface_texture",))
        print("COMPOSED", composed)
        shot(shots_dir(tmp_path), "ring_holes_compose", await cad("drawing", action="capture"))
        await make_pin(cad)
        composed = await compose_the_rest(cad, {"sheet": {"title": "Pasador"}, "finish": [{"general": True, "ra": 1.6}]}, keep=("req.surface_texture",))
        print("COMPOSED PIN", composed)
        shot(shots_dir(tmp_path), "pin_compose", await cad("drawing", action="capture"))
