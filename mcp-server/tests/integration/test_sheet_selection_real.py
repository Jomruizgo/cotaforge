# ruff: noqa: E501
"""Selection on a drawing sheet, both ways, end to end: the real server over stdio against the VISIBLE FreeCAD (Windows).

The engineer's clicks are made with ``Gui.Selection.addSelection`` (what a click ends in) through ``run_native``; everything
the agent does is a tool call: it reads ``view(action=selection)`` and points with ``view(action=show)``. Set
``COTAFORGE_TEST_SHOTS`` to a folder to keep what the tools answered (``sheet_selection_outputs.txt``).
"""

import os
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import Cad, running_server
from tests.integration.test_drawing_production import HOLES, THICKNESS, make_plate

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

SELECT_TOP_CIRCLE = """
import FreeCAD as App, FreeCADGui as Gui
doc = App.ActiveDocument
page = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawPage"][0]
top = [v for g in page.Views if g.TypeId == "TechDraw::DrawProjGroup" for v in g.Views if v.Type == "Top"][0]
found = [
    i for i in range(len(top.getVisibleEdges()))
    if type(top.getEdgeByIndex(i).Curve).__name__ == "Circle"
    and abs(top.getEdgeByIndex(i).Curve.Radius - 4.0) < 1e-6
    and abs(top.getEdgeByIndex(i).Curve.Center.x + 38.0) < 1e-3
]
Gui.Selection.clearSelection()
Gui.Selection.addSelection(doc.Name, top.Name, "Edge%d" % found[0])
print("selected", top.Name, "Edge%d" % found[0])
"""

SELECT_OBJECTS = """
import FreeCAD as App, FreeCADGui as Gui
doc = App.ActiveDocument
Gui.Selection.clearSelection()
for name, sub in {targets}:
    Gui.Selection.addSelection(doc.Name, name, sub)
print("selected", {targets})
"""

SELECT_RIGHT_VIEW = """
import FreeCAD as App, FreeCADGui as Gui
doc = App.ActiveDocument
page = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawPage"][0]
right = [v for g in page.Views if g.TypeId == "TechDraw::DrawProjGroup" for v in g.Views if v.Type == "Right"][0]
Gui.Selection.clearSelection()
Gui.Selection.addSelection(doc.Name, right.Name, "")
"""

SELECT_HOLE_WALL = """
import FreeCAD as App, FreeCADGui as Gui
doc = App.ActiveDocument
body = doc.getObject("Body")
walls = [
    i + 1 for i, f in enumerate(body.Tip.Shape.Faces)
    if type(f.Surface).__name__ == "Cylinder" and abs(f.Surface.Radius - 4.0) < 1e-6 and abs(f.Surface.Center.x + 38.0) < 1e-3
]
Gui.Selection.clearSelection()
Gui.Selection.addSelection(doc.Name, "Body", "Face%d" % walls[0])
print("selected Face%d" % walls[0])
"""


async def select(cad: Cad, code: str) -> None:
    reply = await cad("run_native", code=code, intent="select as the engineer would (test)")
    assert not reply.is_error, reply.text


async def test_the_engineer_selects_on_the_sheet_and_the_agent_points_back(workspace: Path) -> None:
    kept: list[str] = []

    def keep(title: str, text: str) -> None:
        kept.append(f"=== {title}\n{text}\n")

    async with running_server(workspace, visible=True) as (cad, init):
        assert init.instructions is not None and 'cad_guide(topic="pointing")' in init.instructions
        await make_plate(cad)
        await cad.ok("drawing", action="create", template="A3", title="Placa", scale="1:1")
        a, b = HOLES["a"], HOLES["b"]
        await cad.ok(
            "drawing",
            action="add",
            items=[
                {"type": "hole_dim", "id": "h1", "at": [[*a, THICKNESS], [*b, THICKNESS]]},
                {
                    "type": "dim",
                    "id": "d3",
                    "kind": "distance",
                    "from": {"at": [*a, THICKNESS]},
                    "to": {"at": [*b, THICKNESS]},
                    "along": "x",
                },
            ],
        )

        # a distance dimension: its item, its value, the two holes it measures
        await select(cad, SELECT_OBJECTS.format(targets=[("Drawing_d3", "")]))
        text = await cad.ok("view", action="selection")
        keep("selection: the dimension d3", text)
        assert "item d3 dim" in text and 'prints "76"' in text and text.count("hole D8 through") == 2
        assert "distance along x" in text

        # a hole circle of the top view: the hole, its diameter, its axis, its wall face
        await select(cad, SELECT_TOP_CIRCLE)
        text = await cad.ok("view", action="selection")
        keep("selection: the circle of hole a in the top view", text)
        assert "line of the top view" in text and "hole D8 through" in text and "circle diameter 8" in text

        # a whole view
        await select(cad, SELECT_RIGHT_VIEW)
        text = await cad.ok("view", action="selection")
        keep("selection: the right view", text)
        assert "right view" in text and "looks along" in text

        # the agent points: an item on the sheet, a view, both
        shown = await cad.ok("view", action="show", ids=["d3"])
        keep("show d3 on the sheet", shown)
        assert shown.startswith("shown: highlight") and "d3" in shown
        shown = await cad.ok("view", action="show", ids=["view:top", "h1"])
        keep("show view:top and h1", shown)
        assert "view:top" in shown and "h1" in shown
        assert "neither an item" in await cad.err("view", action="show", ids=["nope"])

        # a face selected in 3D: the dimensions that refer to it, and shown on the sheet
        await select(cad, SELECT_HOLE_WALL)
        text = await cad.ok("view", action="selection")
        keep("selection: the wall of hole a, in 3D", text)
        assert "on the sheet:" in text and "h1 hole_dim" in text and "d3 dim 76" in text
        face = text.split("id=")[1].split()[0]
        shown = await cad.ok("view", action="show", ids=[face], on="sheet")
        keep(f"show {face} on the sheet", shown)
        assert "h1" in shown and "d3" in shown and "top view" in shown
        shown = await cad.ok("view", action="show", ids=["d3"], on="3d")
        keep("show d3 in 3D", shown)
        assert "3D:" in shown and "d3" in shown
        await cad.ok("view", action="show", clear=True)

    out = os.environ.get("COTAFORGE_TEST_SHOTS")
    if out:
        Path(out).mkdir(parents=True, exist_ok=True)
        (Path(out) / "sheet_selection_outputs.txt").write_text("\n".join(kept), encoding="utf-8")
