# ruff: noqa: E501
"""The owner's plate on the REAL FreeCAD (Windows, VISIBLE): a 100 x 60 plate with 4 dowel holes Ø6 H7 on two rows 8 mm from the edges.
Vertically the sheet carries 8 (edge to the upper row), 44 (row to row), 8 (lower row to the edge) and the overall 60: the chain closes
on the overall, so one of the two 8 is one dimension too many. It is ONE blocking finding (``over_dimensioned``), and removing the
dimension of the other end, as the message says, clears it.

The plate is designed through the harness's own flow (operations, alerts, design review), with made-up dimensions."""

import sys
from pathlib import Path

import pytest

from tests.integration.bench_parts import DESIGN_ANSWERS, XY, design_review
from tests.integration.conftest import Cad, running_server

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

DESIGN_ANSWERS["dowel_plate"] = {
    "design-precision-the-assembly-guarantees": "applies: the four dowel holes are finished on the single plate after grinding, located in coordinates from the ground edges; the plate alone guarantees the pattern, it is not joined or bent before.",
    "drawing-thread-designation": "does not apply: the plate has no threaded hole; the dowel holes are D6 H7.",
}
LOWER_8 = {"type": "dim", "id": "low8", "kind": "distance", "from": {"at": [-40, 22, 5]}, "to": {"at": [-40, 30, 5]}, "along": "y", "view": "top", "side": "left"}


async def build(cad: Cad) -> None:
    top = {"kind": "face", "from_op": "plate", "select": {"normal": [0, 0, 1]}}
    await cad.ok("memory", action="brief", sector="precision-machining")
    await cad.ok("doc", action="new", name="dowel_plate")
    await cad.ok("doc", action="set_material", material="Acero 1.2379 (D2) templado 60 HRC")
    await cad.ok("doc", action="set_route", route=["fresado", "taladrado", "temple", "rectificado", "rectificado de coordenadas"])
    await cad.ok("op", name="sketch", id="outline", params={"on": XY, "entities": [{"kind": "rect", "name": "plate", "center": [0, 0], "width": 100, "height": 60}]})
    await cad.ok("op", name="extrude", id="plate", params={"sketch": "outline", "depth_mm": 10})
    dowels = {"face": top, "positions": [[-40, -22], [40, -22], [-40, 22], [40, 22]], "standard": "simple", "diameter_mm": 6, "fit": "H7", "end_condition": "through_all"}
    await cad.ok("op", name="hole", id="dowels", params=dowels)
    edges = {"kind": "edges", "from_op": "dowels", "where": {"convex": True, "exclude_holes": True}}
    await cad.ok("op", name="chamfer", id="edge_break", params={"edges": [edges], "distance1_mm": 0.5})
    await design_review(cad, "dowel_plate")


async def test_8_44_8_on_the_overall_60_is_one_blocking_finding_and_removing_the_other_8_clears_it(workspace: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        await build(cad)
        await cad.ok("drawing", action="create", title="Placa", template="A3", language="es")
        print("ADDED", await cad.ok("drawing", action="add", items=[LOWER_8]))
        review = await cad.ok("drawing", action="review")
        print("REVIEW", review)
        assert "low8" in review, review
        closing = [line for line in review.splitlines() if "over_dimensioned" in line or "dimension_redundant" in line]
        print("CLOSING", closing)
        assert len(closing) == 1 and "BLOCKS EXPORT over_dimensioned" in closing[0] and "low8" in closing[0]
        await cad.ok("drawing", action="remove", items=["low8"])
        after = await cad.ok("drawing", action="review")
        print("AFTER", after)
        assert "over_dimensioned" not in after and "dimension_redundant" not in after
        cad.built = False  # a partial sheet (two items), not a finished drawing: the finished-sheet check does not apply
