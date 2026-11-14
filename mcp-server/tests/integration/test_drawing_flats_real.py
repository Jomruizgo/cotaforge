# ruff: noqa: E501
"""The flats of a turned part get their diagonals, on the real FreeCAD (Windows, VISIBLE): a washer with a square flange whose
corners a cylinder cuts, and a shaft with wrench flats, drawn step by step (``view_lines``) and composed in one order. A
flat seen face-on in a view that has a centre line reads as a cylinder unless it carries its two thin diagonals.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the images of the sheets.
"""

import base64
import os
import re
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import ORDER, Cad, Reply, pending_points, running_server
from tests.integration.test_drawing_compose_real import compose_the_rest, in_one_order

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

XY = {"kind": "plane", "name": "XY"}
FLANGE_TOP = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}
SHAFT_TOP = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}


def shot(shots: Path, name: str, reply: Reply) -> None:
    assert reply.images, reply.text
    shots.mkdir(parents=True, exist_ok=True)
    (shots / f"{name}.png").write_bytes(base64.b64decode(reply.images[0].data))


async def make_washer(cad: Cad) -> None:
    """A 40 x 40 square flange, 6 thick, whose corners a Ø50 cylinder cuts, a Ø30 x 5 boss and a Ø10 hole through."""
    await cad.ok("doc", action="new", name="arandela")
    await cad.ok("doc", action="set_material", material="C45")
    await cad.ok(
        "op",
        name="sketch",
        id="disc",
        params={"on": XY, "entities": [{"kind": "circle", "name": "od", "center": [0, 0], "diameter": 50}]},
    )
    await cad.ok("op", name="extrude", id="base", params={"sketch": "disc", "depth_mm": 6})
    for number, rects in enumerate(
        (((22.5, 0, 5, 70), (-22.5, 0, 5, 70)), ((0, 22.5, 70, 5), (0, -22.5, 70, 5))), start=1
    ):  # one pair of strips at a time: a sketch on a face is placed at the centre of that face, which stays on the axis
        sides = [
            {"kind": "rect", "name": f"side{n}", "center": [cx, cy], "width": w, "height": h}
            for n, (cx, cy, w, h) in enumerate(rects)
        ]
        await cad.ok("op", name="sketch", id=f"cs{number}", params={"on": FLANGE_TOP, "entities": sides})
        await cad.ok("op", name="cut", id=f"sides{number}", params={"sketch": f"cs{number}", "depth_mm": 25})
    await cad.ok(
        "op",
        name="sketch",
        id="bs",
        params={"on": FLANGE_TOP, "entities": [{"kind": "circle", "name": "boss", "center": [0, 0], "diameter": 30}]},
    )
    await cad.ok("op", name="extrude", id="boss", params={"sketch": "bs", "depth_mm": 5})
    underside = {
        "kind": "face",
        "from_op": "base",
        "select": {"normal": [0, 0, -1]},
    }  # the one face of the flange that looks down
    hole = {
        "face": underside,
        "at": [0, 0, 0],
        "standard": "iso_clearance",
        "size": "M10",
        "diameter_mm": 10,
        "end_condition": "through_all",
    }
    await cad.ok("op", name="hole", id="bore", params=hole)


async def make_flatted_shaft(cad: Cad) -> None:
    """A Ø24 x 60 shaft with two wrench flats 18 mm across, 22 mm long, at one end."""
    await cad.ok("doc", action="new", name="eje_llave")
    await cad.ok("doc", action="set_material", material="AISI 1045")
    await cad.ok(
        "op",
        name="sketch",
        id="outline",
        params={"on": XY, "entities": [{"kind": "circle", "name": "od", "center": [0, 0], "diameter": 24}]},
    )
    await cad.ok("op", name="extrude", id="base", params={"sketch": "outline", "depth_mm": 60})
    for number, x in enumerate((11.5, -11.5), start=1):
        rect = [{"kind": "rect", "name": "flat", "center": [x, 0], "width": 5, "height": 30}]
        await cad.ok("op", name="sketch", id=f"fs{number}", params={"on": SHAFT_TOP, "entities": rect})
        await cad.ok("op", name="cut", id=f"flat{number}", params={"sketch": f"fs{number}", "depth_mm": 22})


def diagonal_findings(findings: list[str]) -> list[str]:
    return [f for f in findings if f.startswith("flat_face_unmarked")]


async def steps_sheet(cad: Cad, shots: Path, name: str, title: str, views: list[str]) -> None:
    made = await cad.ok("drawing", action="create", template="A3", angle="first", title=title, views=views)
    print("CREATED", made)
    shot(
        shots, name, await cad("drawing", action="capture")
    )  # what the order of create leaves: view_lines is there already
    await cad.ok("drawing", action="remove", items=["rq_view_lines"], **ORDER)
    review = await cad.ok("drawing", action="review")
    findings, _, _ = await pending_points(cad)
    unmarked = diagonal_findings(findings)
    print("BEFORE", unmarked)
    assert unmarked, review  # nothing carries the diagonals now: the review names the face
    line = next(row for row in review.splitlines() if "flat_face_unmarked" in row)
    assert re.search(r"face:\d+", line) and any(v in line for v in views), line  # ... and the view
    added = await cad.ok("drawing", action="add", items=[{"type": "view_lines"}])
    print("ADDED", added)
    assert "diagonals on the flat faces seen face-on" in added, added
    findings, _, _ = await pending_points(cad)
    assert not diagonal_findings(findings), findings
    shot(shots, f"{name}_readded", await cad("drawing", action="capture"))


async def test_the_flange_of_a_washer_gets_its_diagonals_step_by_step(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await make_washer(cad)
        await steps_sheet(cad, shots, "fx1_washer_steps", "Arandela", ["front", "top", "right", "iso"])


async def test_the_flange_of_a_washer_gets_its_diagonals_composed_in_one_order(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await in_one_order(cad)
        await make_washer(cad)
        composed = await compose_the_rest(
            cad,
            {
                "sheet": {"title": "Arandela", "views": ["front", "top", "right", "iso"]},
                "finish": [{"general": True, "ra": 3.2}],
            },
            keep=("req.surface_texture",),
        )
        print("COMPOSED", composed)
        assert "composed sheet" in composed and "rq_view_lines" in composed, composed
        findings, _, _ = await pending_points(cad)
        assert not diagonal_findings(findings), findings
        shot(shots, "fx1_washer_sheet", await cad("drawing", action="capture"))


async def test_the_wrench_flats_of_a_shaft_get_their_diagonals_step_by_step(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await make_flatted_shaft(cad)
        await steps_sheet(cad, shots, "fx1_shaft_steps", "Eje con llaves", ["front", "top", "iso"])


async def test_the_wrench_flats_of_a_shaft_get_their_diagonals_composed_in_one_order(
    workspace: Path, tmp_path: Path
) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await in_one_order(cad)
        await make_flatted_shaft(cad)
        composed = await compose_the_rest(
            cad,
            {
                "sheet": {"title": "Eje con llaves", "views": ["front", "top", "iso"]},
                "finish": [{"general": True, "ra": 1.6}],
            },
            keep=("req.surface_texture",),
        )
        print("COMPOSED", composed)
        assert "composed sheet" in composed, composed
        findings, _, _ = await pending_points(cad)
        assert not diagonal_findings(findings), findings
        shot(shots, "fx1_shaft_sheet", await cad("drawing", action="capture"))
