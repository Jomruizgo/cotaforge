# ruff: noqa: E501
"""The centre lines of the holes in the edge views on the real FreeCAD (Windows, VISIBLE): short, the same reach at both ends,
never through a dimension or its text. The clean plate (100 x 60 x 19) and a block with a blind hole (angled drill point) and a
cross hole are drawn step by step and composed in one order, with their front view (the edge view) and its hidden lines; the
sheet is captured, the edge view zoomed, so a person can look at it. The sheet's own review must have no centre line finding.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the images of the sheets.
"""

import base64
import os
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import Cad, Reply, running_server
from tests.integration.test_drawing_compose_real import compose_the_rest, in_one_order
from tests.integration.test_drawing_finished_real import make_plate

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

VIEWS = ["top", "front", "iso"]
XY = {"kind": "plane", "name": "XY"}
TOP = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}
PLATE_EDGE = [90.0, 218.0, 215.0, 272.0]  # the front view of the clean plate on its A3 sheet, with its dimensions
FRONT = {"kind": "face", "from_op": "base", "select": {"normal": [0, -1, 0]}}


def shot(shots: Path, name: str, reply: Reply) -> None:
    assert reply.images, reply.text
    shots.mkdir(parents=True, exist_ok=True)
    (shots / f"{name}.png").write_bytes(base64.b64decode(reply.images[0].data))


async def look(cad: Cad, shots: Path, name: str, region: list[float] | None = None) -> None:
    """The whole sheet, then ``region`` of it (mm from the lower left corner: the edge view) zoomed in."""
    shot(shots, name, await cad("drawing", action="capture"))
    if region is not None:
        shot(shots, f"{name}_edge", await cad("drawing", action="capture", region=region))


async def no_centre_finding(cad: Cad) -> None:
    review = await cad.ok("drawing", action="review")
    assert "centre_line_crosses" not in review and "centre_line_long" not in review, review


async def make_block(cad: Cad) -> None:
    """A 60 x 40 x 30 block: a blind hole from above (angled drill point) and a cross hole through it from the front."""
    await cad.ok("doc", action="new", name="bloque")
    await cad.ok("doc", action="set_material", material="AISI 1045")
    rect = [{"kind": "rect", "name": "block", "center": [0, 0], "width": 60, "height": 40}]
    await cad.ok("op", name="sketch", id="outline", params={"on": XY, "entities": rect})
    await cad.ok("op", name="extrude", id="base", params={"sketch": "outline", "depth_mm": 30})
    blind = {"face": TOP, "at": [-15, 0, 30], "standard": "simple", "diameter_mm": 8, "end_condition": "blind", "depth_mm": 18, "drill_point": "angled"}
    await cad.ok("op", name="hole", id="blind", params=blind)
    cross = {"face": FRONT, "at": [15, -20, 15], "standard": "simple", "diameter_mm": 6, "end_condition": "through_all"}
    await cad.ok("op", name="hole", id="cross", params=cross)


async def test_the_clean_plate_edge_view_has_short_symmetric_centre_lines_step_by_step(
    workspace: Path, tmp_path: Path
) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await make_plate(cad, dowel_fit="H7")
        made = await cad.ok("drawing", action="create", template="A3", angle="first", title="Placa 19", views=VIEWS)
        print("CREATED", made)
        print("ADDED", await cad.ok("drawing", action="add", items=[{"type": "view_lines"}]))
        await look(cad, shots, "cl1_plate_steps", PLATE_EDGE)
        await no_centre_finding(cad)


async def test_the_clean_plate_edge_view_has_short_symmetric_centre_lines_composed(
    workspace: Path, tmp_path: Path
) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await in_one_order(cad)
        await make_plate(cad, dowel_fit="H7")
        faces = await cad.ok("inspect", action="faces", op="base")
        front = next(line.split()[0] for line in faces.splitlines() if "n(0, -1, 0)" in line)
        decisions = {
            "sheet": {"symmetry": True, "title": "Placa 19", "author": "Taller Sur", "material": "AISI 1045", "views": VIEWS},
            "datums": [{"letter": "C", "face": front}],
            "finish": [{"general": True, "ra": 3.2}],
        }
        composed = await compose_the_rest(cad, decisions, keep=("req.surface_texture",), views=VIEWS)
        print("COMPOSED", composed)
        await look(cad, shots, "cl1_plate_composed", PLATE_EDGE)
        await no_centre_finding(cad)


async def test_a_block_with_a_blind_and_a_cross_hole_has_short_symmetric_centre_lines_in_both_modes(
    workspace: Path, tmp_path: Path
) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await make_block(cad)
        await cad.ok("drawing", action="create", template="A3", angle="first", title="Bloque", views=VIEWS)
        await cad.ok("drawing", action="add", items=[{"type": "view_lines"}])
        await look(cad, shots, "cl1_block_steps")
        await no_centre_finding(cad)
    async with running_server(workspace, visible=True) as (cad, _):
        await in_one_order(cad)
        await make_block(cad)
        decisions = {"sheet": {"title": "Bloque", "views": VIEWS}, "finish": [{"general": True, "ra": 3.2}]}
        composed = await compose_the_rest(cad, decisions, keep=("req.surface_texture",), views=VIEWS)
        print("COMPOSED", composed)
        await look(cad, shots, "cl1_block_composed")
        await no_centre_finding(cad)
