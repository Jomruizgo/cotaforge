# ruff: noqa: E501
"""The dimension scheme follows the measuring tool of the shop: the real server over stdio against the VISIBLE FreeCAD (Windows).

The 19 mm plate (100 x 60, four dowels, two counterbores, one M6) is drawn in both modes (steps and one order) the way an
agent draws it, with a right view, and the sheet is compared with the one the owner corrected by hand: per group of equal holes
the spacing between its holes (76 and 44 for the dowels, 28 for the counterbores) and ONE locator from the nearest edge of the
plate (12, 36 and 50 from the left edge, 8 from the edge), nothing counted from one datum baseline (that needs a height
gauge), nothing that leans on the symmetry of the plate and no extension line across the line of another dimension. The side
view a plate does not need is flagged, and neither word of the agent closes that finding: the view is taken off.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the images of the sheets.
"""

import base64
import os
import re
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import Cad, Reply, running_server
from tests.integration.test_drawing_compose_real import compose_the_rest, in_one_order
from tests.integration.test_drawing_finished_real import CLEARANCE, DOWELS, TAPPED, THICKNESS, make_plate

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

VIEWS = ["front", "top", "right", "iso"]  # what the agent of the owner's session drew: the right view of a plate adds nothing
LOC = re.compile(r"loc datum_dims \w+: \d+ hole\(s\) in the top view; x ([\d, ]+); y ([\d, ]+) \(mm")
OWNER_X = {12, 36, 50, 28, 76}  # the locators of the dowels, the counterbores and the M6, and the spacings of the dowels and the counterbores
OWNER_Y = {8, 44}  # the locator of the dowels from the edge and their spacing (the counterbores add 30, the M6 its 8 from the other edge)


def shot(shots: Path, name: str, reply: Reply) -> None:
    assert reply.images, reply.text
    shots.mkdir(parents=True, exist_ok=True)
    (shots / f"{name}.png").write_bytes(base64.b64decode(reply.images[0].data))


def scheme_of(listed: str) -> tuple[list[int], list[int]]:
    found = LOC.search(listed)
    assert found, listed
    return [int(v) for v in found.group(1).split(",")], [int(v) for v in found.group(2).split(",")]


def assert_the_owners_scheme(listed: str, review: str) -> None:
    x, y = scheme_of(listed)
    assert OWNER_X <= set(x) and len(x) == 5, x  # 12, 36, 50 from the left edge, 76 and 28 between the holes: five, each once
    assert OWNER_Y <= set(y) and sorted(y) == [8, 8, 30, 44], y  # 8 from the edge (dowels and M6), 44 between the dowels, 30 of the counterbores
    for corner in (15, 85, 52, 64, 88):  # a baseline from one datum would say these
        assert corner not in x + y, (corner, x, y)
    for finding in ("hole_not_located", "hole_partly_located", "hole_no_reference", "hole_group_no_locator", "dimension_crossing", "dimension_stack_order"):
        assert finding not in review, (finding, review)


async def hole_items(cad: Cad) -> None:
    await cad.ok(
        "drawing",
        action="add",
        items=[
            {"type": "hole_dim", "id": "clear", "at": [[x, y, THICKNESS] for x, y in CLEARANCE]},
            {"type": "hole_dim", "id": "tap", "at": [[*TAPPED, THICKNESS]]},
            {"type": "hole_dim", "id": "dowels", "at": [[x, y, THICKNESS] for x, y in DOWELS], "fit": "H7", "dowel": True},
        ],
    )


async def test_placa19_in_steps_mode_has_the_scheme_of_the_caliper_and_loses_its_side_view(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await make_plate(cad, dowel_fit="H7")
        await cad.ok("drawing", action="create", views=VIEWS, title="Placa 19", author="JR")
        await cad.ok("drawing", action="add", items=[{"type": "overall"}, {"type": "view_lines"}])
        await hole_items(cad)
        listed = await cad.ok("drawing", action="list")
        review = await cad.ok("drawing", action="review")
        shot(shots, "ds1_steps_placa19_agent", await cad("drawing", action="capture"))
        assert_the_owners_scheme(listed, review)
        # the right view of a plate adds nothing: the finding holds the export and the agent's word does not close it
        assert "BLOCKS EXPORT view_adds_nothing:right" in review, review
        assert "views=[right]" in review and "view=right" not in review, review  # the argument the tool takes
        word = "applies: revisado en la hoja, el requisito está cubierto por las cotas puestas"  # what the agent of the owner's session answered
        await cad.ok("drawing", action="answer", id="view_adds_nothing:right", text=word)
        refused = await cad.err("export", action="pdf", path="placa19.pdf")
        assert "- view_adds_nothing:right" in refused, refused  # the claim does not close it: the view is still there
        own = await cad.err("drawing", action="answer", id="view_adds_nothing:right", text="no aplica: la placa es plana")
        assert "measured_not_dismissable" in own, own
        # taking the view off closes it, and the sheet is the owner's: front, top and the isometric view
        await cad.ok("drawing", action="remove_view", views=["right"])
        after = await cad.ok("drawing", action="review")
        assert "view_adds_nothing" not in after, after
        listed = await cad.ok("drawing", action="list")
        assert_the_owners_scheme(listed, after)
        shot(shots, "ds1_steps_placa19_owner", await cad("drawing", action="capture"))


async def test_placa19_in_sheet_mode_has_the_same_scheme_whatever_the_symmetry(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await in_one_order(cad)
        await make_plate(cad, dowel_fit="H7")
        decisions = {
            "sheet": {"symmetry": True, "title": "Placa 19", "author": "JR", "material": "AISI 1045", "views": VIEWS},
            "finish": [{"general": True, "ra": 3.2}],
            "notes": ["Romper aristas vivas 0,3 mm; sin rebabas"],
        }
        composed = await compose_the_rest(cad, decisions, keep=("req.surface_texture",), views=VIEWS)
        assert "composed sheet" in composed, composed
        listed = await cad.ok("drawing", action="list")
        review = await cad.ok("drawing", action="review")
        shot(shots, "ds1_sheet_placa19_agent", await cad("drawing", action="capture"))
        assert_the_owners_scheme(listed, review)
        assert "view_adds_nothing:right" in review, review  # the declared symmetry does not change what is measured
        await cad.ok("drawing", action="remove_view", views=["right"])
        assert "view_adds_nothing" not in await cad.ok("drawing", action="review")
        shot(shots, "ds1_sheet_placa19_owner", await cad("drawing", action="capture"))


async def test_a_hole_in_the_middle_of_a_plate_is_dimensioned_from_an_edge_and_a_bare_position_tolerance_asks_for_a_note(
    workspace: Path, tmp_path: Path
) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await make_plate(cad, dowel_fit="H7")
        await cad.ok("drawing", action="create", views=["front", "top", "iso"], title="Placa 19", author="JR")
        await cad.ok("drawing", action="add", items=[{"type": "view_lines"}])
        await hole_items(cad)
        faces = await cad.ok("inspect", action="faces", op="base")
        bottom = next(line.split()[0] for line in faces.splitlines() if "n(0, 0, -1)" in line)
        print("DATUM", await cad.ok("drawing", action="add", items=[{"type": "datum", "id": "dA", "letter": "A", "face": bottom}]))
        print("GDT", await cad.ok("drawing", action="add", items=[{"type": "gdt", "id": "pos", "char": "position", "value": 0.02, "diameter": True, "datums": ["A"], "on": "dowels"}]))
        review = await cad.ok("drawing", action="review")
        print("REVIEW", review)
        assert "measurement_unstated:pos" in review, review  # no CMM note: nobody says how the position is measured
        await cad.ok("drawing", action="add", items=[{"type": "note", "text": "Posición de los 4× Ø6 H7: verificar en MMC"}])
        assert "measurement_unstated" not in await cad.ok("drawing", action="review")
        shot(shots, "ds1_position_note", await cad("drawing", action="capture"))
