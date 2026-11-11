"""The engineer edits the model by hand in the VISIBLE FreeCAD window: the next tool reply says so; looking at the
model (camera, visibility, colour, selection) and the harness' own work say nothing. The real server over stdio
against the real FreeCAD (Windows).

A hand edit is made OUTSIDE any request of the harness, as the engineer's mouse would: ``run_native`` only arms a
Qt timer, which fires after the request has ended, on the same thread the window's events run on.
"""

import asyncio
import sys
from pathlib import Path

import pytest

from cotaforge_mcp.hand_edits import HEADER
from tests.integration.conftest import Cad, running_server
from tests.integration.test_drawing_production import make_plate

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

LATER = """
from PySide import QtCore

def _later():
    import FreeCAD as App
    import FreeCADGui as Gui
    doc = App.ActiveDocument
{body}

QtCore.QTimer.singleShot({delay_ms}, _later)
"""

LOOK_AT_IT = """    view = Gui.ActiveDocument.ActiveView
    view.viewRear()
    view.fitAll()
    body = doc.getObject("Body")
    Gui.ActiveDocument.getObject("Body").Visibility = False
    Gui.ActiveDocument.getObject("Body").Visibility = True
    Gui.ActiveDocument.getObject("Body").ShapeColor = (1.0, 0.0, 0.0)
    Gui.Selection.clearSelection()
    Gui.Selection.addSelection(doc.Name, "Body", "Face1")
"""

EDIT_THE_PAD = """    pad = [o for o in doc.Objects if o.TypeId == "PartDesign::Pad"][0]
    pad.Length = pad.Length.Value + 2
    doc.recompute()
"""

RENAME_AND_ADD = """    doc.getObject("Body").Label = "Main body"
    doc.openTransaction("by hand")
    doc.addObject("Part::Box", "Spare")
    doc.commitTransaction()
    doc.recompute()
"""


HANDS_AFTER_MS = 2500
"""Longer than everything ``run_native`` still asks the CAD for after it ran its code (measuring the result): the
timer has to fire when the server is idle, like the engineer's mouse does, not inside the call that armed it."""


async def by_hand(cad: Cad, body: str) -> None:
    """Arm what the engineer does in the window, then wait for it to happen and for the watch to settle."""
    code = LATER.format(body=body, delay_ms=HANDS_AFTER_MS)
    await cad.ok("run_native", code=code, intent="act as the engineer's hands in the window (test)")
    await asyncio.sleep(HANDS_AFTER_MS / 1000 + 1.0)


async def test_hand_edits_reach_the_agent_with_the_next_reply_and_looking_does_not(workspace: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        await make_plate(cad)
        await asyncio.sleep(1.0)  # the recomputes of the plate's own ops, long settled
        quiet = await cad.ok("doc", action="status")
        assert HEADER not in quiet

        # looking is not editing: camera, visibility, colour and selection
        await by_hand(cad, LOOK_AT_IT)
        after_looking = await cad.ok("doc", action="status")
        assert HEADER not in after_looking
        # ... and the selection was not pushed: the agent reads it when the engineer says so
        assert "selection: 1 usable" in await cad.ok("view", action="selection")

        # a property edited by hand: named by its step, with the old and the new value
        await by_hand(cad, EDIT_THE_PAD)
        told = await cad.ok("doc", action="status")
        lines = told.splitlines()
        assert lines[0] == HEADER
        assert lines[1].startswith("- Pad (step base): Length: 16") and "-> 18" in lines[1], told
        assert len(HEADER) + len(lines[1]) < 250  # what it costs the agent's context
        assert HEADER not in await cad.ok("doc", action="status")  # once

        # a rename and an object added, several tools later: told with whichever tool comes next
        await by_hand(cad, RENAME_AND_ADD)
        told = await cad.ok("inspect", action="tree")
        lines = told.splitlines()
        assert lines[0] == HEADER
        assert any("Label: 'base_body' -> 'Main body'" in line for line in lines[1:3]), told
        assert any(line.startswith("- Spare: added Part::Box") for line in lines[1:3]), told

        # the harness' own work afterwards is not a hand edit, and its numbers are measured from the engineer's change
        entities = [{"kind": "rect", "name": "probe", "center": [0, 0], "width": 10, "height": 10}]
        await cad.ok(
            "op", name="sketch", id="probe", params={"on": {"kind": "plane", "name": "XY"}, "entities": entities}
        )
        await asyncio.sleep(1.0)
        assert HEADER not in await cad.ok("doc", action="status")
