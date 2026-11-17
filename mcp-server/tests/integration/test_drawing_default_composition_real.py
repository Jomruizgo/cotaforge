# ruff: noqa: E501
"""The default composition on the REAL FreeCAD (Windows, VISIBLE): what the harness puts on a sheet by itself never breaks what its own
catalog forbids. The bench shaft (a Ø22 end with a closed keyway 6 x 25) and a 100 x 60 plate with two groups of holes are composed with
the defaults only (nothing declared, no symmetry), and the review says nothing of a dimension on a hidden line, a loop, a chain that
closes on the overall size, or a dimension given by the others.

The plate is designed through the harness's own flow (operations, alerts, design review), with made-up dimensions."""

import re
import sys
from pathlib import Path

import pytest

from tests.integration.bench_parts import DESIGN_ANSWERS, XY, design_review
from tests.integration.conftest import Cad, running_server
from tests.integration.test_drawing_compose_real import compose_the_rest, in_one_order
from tests.integration.test_drawing_shaft_sheet_real import shaft

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

NEVER = ("dimension_on_hidden", "dimension_redundant", "over_dimensioned", "dimension_twice", "duplicate_dimension", "view_duplicate")


def never_raised(review: str) -> list[str]:
    return [line for line in review.splitlines() if any(code in line for code in NEVER) and ("BLOCKS EXPORT" in line or "warning" in line)]


async def test_the_bench_shaft_has_one_axial_view_the_keyway_face_on_its_depth_from_the_surface_and_nothing_on_a_hidden_line(workspace: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        await in_one_order(cad)
        await shaft(cad, workspace)
        composed = await compose_the_rest(cad, {"sheet": {"title": "Eje"}, "finish": [{"general": True, "ra": 3.2}]}, keep=("req.surface_texture", "req.keyway"))
        print("COMPOSED", composed)
        review = await cad.ok("drawing", action="review")
        print("REVIEW", review)
        assert never_raised(review) == [], never_raised(review)
        assert re.search(r"rq_keyway_t dim ok: 3,5 mm along z", review), "the closed keyway's depth is t1 from the surface, not 18,5 across the shaft"
        assert re.search(r"rq_keyway_l dim ok: 19 mm along x in the front view", review) and re.search(r"rq_keyway_p dim ok: 7 mm along x in the front view", review)
        cad.built = False  # the notes the harness asks for are the engineer's: this test is about the dimensions


async def build_dowel_plate(cad: Cad) -> None:
    top = {"kind": "face", "from_op": "plate", "select": {"normal": [0, 0, 1]}}
    await cad.ok("memory", action="brief", sector="precision-machining")
    await cad.ok("doc", action="new", name="dowel_plate")
    await cad.ok("doc", action="set_material", material="Acero 1.2379 (D2) templado 60 HRC")
    await cad.ok("doc", action="set_route", route=["fresado", "taladrado", "temple", "rectificado", "rectificado de coordenadas"])
    await cad.ok("op", name="sketch", id="outline", params={"on": XY, "entities": [{"kind": "rect", "name": "plate", "center": [0, 0], "width": 100, "height": 60}]})
    await cad.ok("op", name="extrude", id="plate", params={"sketch": "outline", "depth_mm": 10})
    dowels = {"face": top, "positions": [[-40, -22], [40, -22], [-40, 22], [40, 22]], "standard": "simple", "diameter_mm": 6, "fit": "H7", "end_condition": "through_all"}
    await cad.ok("op", name="hole", id="dowels", params=dowels)
    screws = {"face": top, "positions": [[-12, 22], [12, 22]], "standard": "iso_tapped", "size": "M6", "diameter_mm": 5, "end_condition": "through_all", "thread_depth_mm": 10}
    await cad.ok("op", name="hole", id="screws", params=screws)
    edges = {"kind": "edges", "from_op": "screws", "where": {"convex": True, "exclude_holes": True}}
    await cad.ok("op", name="chamfer", id="edge_break", params={"edges": [edges], "distance1_mm": 0.5})
    DESIGN_ANSWERS["dowel_plate_default"] = {
        "design-precision-the-assembly-guarantees": "applies: the four dowel holes are finished on the single plate after grinding, located in coordinates from the ground edges; the plate alone guarantees the pattern.",
        "drawing-thread-designation": "applies: the two M6 holes are tapped through; each is called with its thread.",
    }
    await design_review(cad, "dowel_plate_default")


async def test_the_plate_100_by_60_with_two_groups_says_8_and_44_once_and_the_review_has_nothing_to_say(workspace: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        await in_one_order(cad)
        await build_dowel_plate(cad)
        await compose_the_rest(cad, {"sheet": {"title": "Placa"}, "finish": [{"general": True, "ra": 3.2}]}, keep=("req.surface_texture",))
        review = await cad.ok("drawing", action="review")
        print("REVIEW", review)
        assert never_raised(review) == [], never_raised(review)
        located = next(line for line in review.splitlines() if " datum_dims ok" in line)
        assert re.search(r"\by 8, 44\b", located), located  # 8 + 44 + 8 = 60 is the chain it used to build from this one item
        cad.built = False


async def test_a_plate_tapped_from_its_top_face_starts_its_axis_there(workspace: Path) -> None:
    """A through hole tapped for 8 mm of a 12 mm plate is drilled from the top: its axis starts at the top face, so the thread lines of a
    section are counted from there and not from the bottom."""
    async with running_server(workspace, visible=True) as (cad, _):
        top = {"kind": "face", "from_op": "plate", "select": {"normal": [0, 0, 1]}}
        await cad.ok("doc", action="new", name="tapped")
        await cad.ok("op", name="sketch", id="outline", params={"on": XY, "entities": [{"kind": "rect", "name": "plate", "center": [0, 0], "width": 40, "height": 30}]})
        await cad.ok("op", name="extrude", id="plate", params={"sketch": "outline", "depth_mm": 12})
        await cad.ok("op", name="hole", id="m8", params={"face": top, "at": [0, 0, 12], "standard": "iso_tapped", "size": "M8", "diameter_mm": 6.8, "end_condition": "through_all", "thread_depth_mm": 8})
        holes = await cad.ok("inspect", action="holes")
        print("HOLES", holes)
        assert re.search(r"axis from \(0, 0, 12\) along \(0, 0, -1\)", holes), holes  # the entry: the top face, going down (it used to start at the bottom)
        cad.built = False
