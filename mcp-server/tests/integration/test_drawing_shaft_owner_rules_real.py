# ruff: noqa: E501
"""Two owner rules of the shaft sheet on the REAL FreeCAD (Windows, VISIBLE): a hidden line is never dimensioned
(``dimension_on_hidden``) and no dimension is the sum or the difference of others (``dimension_redundant``).

The bench shaft (thread end, two seats, body, Ø22 end with a closed keyway) is drawn the way an agent draws it. The keyway floor is
seen from the top; from the front it is behind the shaft, so its lines are hidden ones there. A length measured between the ends of
those lines is flagged. The 125 that the overall 168 and the 43 of the thread already give is flagged. Both are listed, not holding the
export, because the sheet the harness composes by default breaks them. Without the items that break them the sheet draws
the same shaft with neither.
"""

import sys
from pathlib import Path

import pytest

from tests.integration.conftest import Cad, order, running_server
from tests.integration.test_drawing_shaft_sheet_real import DIAMETERS, shaft

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

HIDDEN = {"type": "dim", "id": "kw_len", "kind": "distance", "from": {"at": [125, 3, 7.5]}, "to": {"at": [144, 3, 7.5]}, "along": "x", "view": "front", "side": "above"}
FROM_END = {"type": "dim", "id": "len125", "kind": "distance", "from": {"at": [0, 5, 0]}, "to": {"at": [125, 0, -11]}, "along": "x", "view": "front", "side": "below"}
THREAD_END = {"type": "dim", "id": "len43", "kind": "distance", "from": {"at": [125, 0, -11]}, "to": {"at": [168, 5, 0]}, "along": "x", "view": "front", "side": "below"}


async def start(cad: Cad, workspace: Path) -> None:
    await cad.ok("memory", action="brief", drawing_review="propio", text=cad.says("el agente revisa el plano por su cuenta"))
    await shaft(cad, workspace)
    await cad.ok("drawing", action="create", title="Eje de transmision", template="A3", language="es")


async def test_a_length_between_the_ends_of_hidden_lines_is_flagged(workspace: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        cad.allow("collision", "dimension_gap", "dimension_stack_order", "dimension_crossing")  # the defects are built on purpose
        await start(cad, workspace)
        await cad.ok("drawing", action="add", items=[{"type": "view_lines"}, HIDDEN])
        review = await cad.ok("drawing", action="review")
        print("REVIEW", review)
        assert "dimension_on_hidden" in review and "kw_len" in review


async def test_the_125_beside_the_overall_168_and_the_43_is_flagged(workspace: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        cad.allow("collision", "dimension_gap", "dimension_stack_order", "dimension_crossing")  # the defects are built on purpose
        await start(cad, workspace)
        await cad.ok("drawing", action="add", items=[{"type": "view_lines"}, FROM_END, THREAD_END])
        review = await cad.ok("drawing", action="review")
        print("REVIEW", review)
        assert "dimension_redundant" in review and "len125" in review


async def test_a_shaft_sheet_without_them_raises_neither(workspace: Path) -> None:
    """What the harness composes by default for this shaft breaks both rules (the automatic overall stands beside the datum baseline, the
    automatic keyway length and position stand on the hidden lines of the front view): both findings are listed for the default sheet, and
    gone once those items are removed."""
    async with running_server(workspace, visible=True) as (cad, _):
        await start(cad, workspace)
        await cad.ok("drawing", action="add", items=[{"type": "baseline_dims", "from": "face:6", "view": "front", "side": "below"}, *DIAMETERS, {"type": "view_lines"}])
        default = await cad.ok("drawing", action="review")
        print("DEFAULT", default)
        await cad.ok("drawing", action="remove", items=["o1", "rq_keyway_l", "rq_keyway_p"], **order(cad))
        review = await cad.ok("drawing", action="review")
        print("REVIEW", review)
        assert "dimension_on_hidden" not in review and "dimension_redundant" not in review
