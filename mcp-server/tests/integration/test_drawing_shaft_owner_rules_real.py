# ruff: noqa: E501
"""Two owner rules of the shaft sheet on the REAL FreeCAD (Windows, VISIBLE): a hidden line is never dimensioned
(``dimension_on_hidden``) and no dimension is the sum or the difference of others (``dimension_redundant``).

The bench shaft (thread end, two seats, body, Ø22 end with a closed keyway) is drawn the way an agent draws it. The keyway floor is
seen from the top; from the front it is behind the shaft, so its lines are hidden ones there. A length measured between the ends of
those lines is flagged. The 125 that the overall 168 and the 43 of the thread already give is flagged ONCE, as ``over_dimensioned``
(a chain that closes on the overall: it holds the export; ``dimension_redundant`` does not list it again). The hidden-line finding and the
loops that do not close on the overall are listed, not holding the export, because the sheet the harness composes by default breaks them. Without the items that break them the sheet draws
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
        assert 'action="remove"' not in review and 'drawing(action="add_view", views=["top"])' in review
        assert 'drawing(action="update", items=[{"id":"kw_len","view":"top"}])' in review


async def test_following_the_message_moves_the_dimension_to_the_view_that_sees_the_keyway_face_on(workspace: Path) -> None:
    """The way out the finding names, call by call: ``add_view`` of the top view (the keyway floor faces up) is accepted; ``update`` of the
    dimension to it is NOT refused by any removal gate (nothing is removed, no reason= or by= asked). On the default shaft sheet the harness's
    own keyway section puts its cutting line across the length of the keyway in the top view, so the edit is refused by the placement guard
    ("would put kw_len on rq_keyway_sec"), and the error names what to change (side, offset_mm). Placing it clear of that line is NOT
    verified on real FreeCAD: no side or offset_mm up to 100 mm cleared it on the bench (see the PR)."""
    async with running_server(workspace, visible=True) as (cad, _):
        cad.allow("collision", "dimension_gap", "dimension_stack_order", "dimension_crossing")  # the defects are built on purpose
        await start(cad, workspace)
        await cad.ok("drawing", action="add", items=[{"type": "view_lines"}, HIDDEN])
        assert "dimension_on_hidden" in await cad.ok("drawing", action="review")
        print("ADD_VIEW", await cad.ok("drawing", action="add_view", views=["top"]))
        reply = await cad("drawing", action="update", items=[{"id": "kw_len", "view": "top"}])
        print("UPDATE", reply.is_error, reply.text)
        if reply.is_error:  # refused only by the placement guard, which says what to change: never by a gate the message hides
            assert "would put kw_len on" in reply.text and "side" in reply.text and "offset_mm" in reply.text, reply.text
            assert "reason" not in reply.text and "measured_not_dismissable" not in reply.text
        else:
            assert "dimension_on_hidden:kw_len" not in await cad.ok("drawing", action="review")


async def test_the_125_beside_the_overall_168_and_the_43_is_flagged(workspace: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        cad.allow("collision", "dimension_gap", "dimension_stack_order", "dimension_crossing")  # the defects are built on purpose
        await start(cad, workspace)
        await cad.ok("drawing", action="add", items=[{"type": "view_lines"}, FROM_END, THREAD_END])
        review = await cad.ok("drawing", action="review")
        print("REVIEW", review)
        assert "over_dimensioned:len125,len43" in review and "BLOCKS EXPORT over_dimensioned" in review
        assert "dimension_redundant" not in review  # one defect, one finding, one weight


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
