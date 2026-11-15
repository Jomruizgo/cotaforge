# ruff: noqa: E501
"""Welds, balloons, the parts list and the moulded-part marks are FreeCAD's own objects, on the real FreeCAD (Windows, VISIBLE):
the welded frame (weld symbol, cut list, balloons) and the assembly (parts list, balloons), and a
moulded part with its marks. The test also plays the engineer's hand: it edits a weld tile, a balloon number and a cell of the
list, moves a view and checks that what hangs on it follows.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the images of the sheets.
"""

import base64
import os
import shutil
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import Cad, running_server
from tests.integration.test_drawing_review_gaps_real import build_assembly
from tests.integration.test_drawing_weldment_real import NOTE, WELD, build_frame

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

MOULD_PART = Path(r"C:\Users\Jorge Ruiz\CotaForge-ejercicios\molde\parts\caja_paso_base.FCStd")  # read only: a copy is opened

LAST_SYMBOLS: list[str] = []  # the symbols the last count found: their unit keys
KINDS = """
import FreeCAD as App, collections, json
doc = App.ActiveDocument
page = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawPage"][0]
kinds = collections.Counter(o.TypeId for o in doc.Objects if o.TypeId.startswith(("TechDraw::DrawLeader", "TechDraw::DrawWeld", "TechDraw::DrawTile", "TechDraw::DrawViewBalloon", "TechDraw::DrawViewSpreadsheet", "Spreadsheet", "TechDraw::DrawViewSymbol", "TechDraw::DrawViewAnnotation", "TechDraw::DrawViewPart", "TechDraw::DrawProjGroup")))
foreign = [str(getattr(o, "CotaforgeKey", "")) or o.Label for o in page.Views if o.TypeId == "TechDraw::DrawViewSymbol"]
print("KINDS " + json.dumps(dict(kinds)) + " SYMBOLS " + json.dumps(foreign))
"""

HAND = """
import FreeCAD as App, json, gui
doc = App.ActiveDocument
page = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawPage"][0]
tiles = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawTileWeld"]
tile = [t for t in tiles if str(t.SymbolFile).endswith(("Down.svg", "Up.svg"))][0]
balloon = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawViewBalloon"][0]
cells = [o for o in doc.Objects if o.TypeId == "Spreadsheet::Sheet"][0]
old = (str(tile.LeftText), str(balloon.Text), cells.getContents("A1"))
tile.LeftText = "a4"
balloon.Text = "9"
cells.set("A1", "'Pos. editada")
doc.recompute()
for _ in range(10):
    gui.pump()
now = (str(tile.LeftText), str(balloon.Text), cells.getContents("A1"))
import TechDrawGui
scene = TechDrawGui.getSceneForPage(page)

def where(text):
    for item in scene.items():
        if type(item).__name__ == "QGraphicsTextItem" and item.toPlainText().strip() == text:
            r = item.sceneBoundingRect()
            return (round((r.x() + r.width() / 2) / 10, 2), round(-(r.y() + r.height() / 2) / 10, 2))
    return None

before = {"balloon": where("9"), "list": None}
views = [o for o in page.Views if o.TypeId == "TechDraw::DrawViewPart"]
group = [o for o in page.Views if o.TypeId == "TechDraw::DrawProjGroup"]
target = balloon.SourceView
moved = target
if str(target.TypeId) == "TechDraw::DrawProjGroupItem":
    moved = [g for g in group if target.Name in [v.Name for v in g.Views]][0]
x0 = float(moved.X)
moved.X = x0 + 30.0
doc.recompute()
for _ in range(10):
    gui.pump()
after = {"balloon": where("9"), "list": None}
print("HAND " + json.dumps({"old": old, "now": now, "before": before, "after": after, "balloon_xy": [float(balloon.X), float(balloon.Y)], "moved": moved.Name, "tiles": [[t.Name, int(t.TileRow), str(t.LeftText), str(t.TileParent.Name)] for t in tiles]}))
"""


def shot(shots: Path, name: str, data: str) -> None:
    shots.mkdir(parents=True, exist_ok=True)
    (shots / f"{name}.png").write_bytes(base64.b64decode(data))


async def kinds(cad: Cad) -> dict[str, int]:
    import json

    text = await cad.ok("run_native", code=KINDS, intent="count what the sheet holds")
    line = next(part for part in text.splitlines() if "KINDS " in part)
    LAST_SYMBOLS[:] = json.loads(line.split(" SYMBOLS ", 1)[1])
    print("SYMBOLS", LAST_SYMBOLS)
    return json.loads(line.split("KINDS ", 1)[1].split(" SYMBOLS ", 1)[0])


async def test_the_welded_frame_has_native_welds_a_cut_list_and_balloons(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await build_frame(cad)
        await cad.ok("drawing", action="create", template="A3", angle="first", title="Bastidor")
        rows = [{"component": "body:1", "name": "Tubo 40×40×3"}, {"component": "body:5", "name": "Chapa 6"}]
        await cad.ok("drawing", action="add", items=[{"type": "parts_list", "id": "cut", "rows": rows}, {"type": "balloons", "id": "bal", "view": "isometric"}, NOTE, WELD])
        found = await kinds(cad)
        print("FRAME", found)
        assert found.get("Spreadsheet::Sheet") == 1 and found.get("TechDraw::DrawViewSpreadsheet") == 1, found  # the cut list
        assert found.get("TechDraw::DrawViewBalloon") == 2, found  # one per position
        assert found.get("TechDraw::DrawWeldSymbol") == 1 and found.get("TechDraw::DrawTileWeld", 0) >= 1, found
        assert found.get("TechDraw::DrawLeaderLine", 0) >= 2, found  # the weld's own and one for the second joint it marks
        assert not found.get("TechDraw::DrawViewSymbol"), found  # nothing the harness drew
        hand = await cad.ok("run_native", code=HAND, intent="the engineer edits a tile, a balloon and a cell, then moves a view")
        print("HAND EDIT", hand)
        line = next(part for part in hand.splitlines() if "HAND " in part)
        import json

        facts = json.loads(line.split("HAND ", 1)[1])
        assert facts["now"] == ["a4", "9", "'Pos. editada"], facts
        before, after = facts["before"]["balloon"], facts["after"]["balloon"]
        assert before and after and abs(after[0] - before[0] - 30.0) < 0.6 and abs(after[1] - before[1]) < 0.6, facts  # the balloon follows its view
        shot_reply = await cad("drawing", action="capture")
        assert shot_reply.images, shot_reply.text
        shot(shots, "nt4_frame", shot_reply.images[0].data)


async def test_the_assembly_has_a_native_parts_list_and_balloons(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await build_assembly(cad)
        made = await cad.ok("drawing", action="create", template="A3", angle="first", title="Conjunto", views=["front", "top", "iso"])
        assert "rq_parts_list" in made and "rq_balloons" in made, made
        found = await kinds(cad)
        print("ASSEMBLY", found)
        assert found.get("Spreadsheet::Sheet") == 1 and found.get("TechDraw::DrawViewSpreadsheet") == 1, found
        assert found.get("TechDraw::DrawViewBalloon", 0) >= 3, found  # base plate, spacer, top plate
        assert not found.get("TechDraw::DrawViewSymbol"), found
        shot_reply = await cad("drawing", action="capture")
        assert shot_reply.images, shot_reply.text
        shot(shots, "nt4_assembly", shot_reply.images[0].data)


async def test_a_moulded_part_has_its_marks_as_native_objects(workspace: Path, tmp_path: Path) -> None:
    if not MOULD_PART.is_file():
        pytest.skip("the moulded part of the owner's exercises is not on this machine")
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    shutil.copy(MOULD_PART, workspace / "caja.FCStd")  # a copy: the exercises folder is read only
    side = MOULD_PART.with_suffix(".cotaforge.json")
    if side.is_file():
        shutil.copy(side, workspace / "caja.cotaforge.json")
    async with running_server(workspace, visible=True) as (cad, _):
        print("OPEN", await cad.ok("doc", action="open", path="caja.FCStd"))
        box = await cad.ok(
            "run_native",
            code="import FreeCAD as App\nb = App.ActiveDocument.Objects[-1].Shape.BoundBox if False else None\nfor o in App.ActiveDocument.Objects:\n    if hasattr(o, 'Shape') and o.Shape.Solids:\n        bb = o.Shape.BoundBox\n        print('BOX', bb.XMin, bb.YMin, bb.ZMin, bb.XMax, bb.YMax, bb.ZMax)\n        break\n",
            intent="read the box of the part",
        )
        numbers = [float(v) for v in next(line for line in box.splitlines() if "BOX " in line).split("BOX ", 1)[1].split()]
        x0, y0, z0, x1, y1, z1 = numbers
        print("CREATE", await cad.ok("drawing", action="create", template="A3", angle="first", title="Caja"))
        items = [
            {"type": "parting_line", "id": "pl"},
            {"type": "pull_direction", "id": "pu", "draft_deg": 1.5},
            {"type": "mould_zone", "id": "gate", "kind": "gate", "at": [(x0 + x1) / 2, (y0 + y1) / 2, z1], "radius_mm": 4.0},
            {"type": "mould_zone", "id": "ej", "kind": "ejector", "at": [[x0 + (x1 - x0) * 0.25, (y0 + y1) / 2, z0], [x0 + (x1 - x0) * 0.75, (y0 + y1) / 2, z0]], "radius_mm": 3.0},
            {"type": "no_draft", "id": "nd", "kind": "no_draft", "at": [x1, (y0 + y1) / 2, (z0 + z1) / 2]},
        ]
        added = await cad.ok("drawing", action="add", items=items)
        print("MARKS", added)
        found = await kinds(cad)
        print("MOULD", found)
        review = await cad.ok("drawing", action="review")
        print("FALLBACKS", [line for line in review.splitlines() if "native_fallback" in line or "TechDraw" in line][:12])
        mine = {"pl", "pu", "gate", "ej", "nd"}
        assert not mine & set(LAST_SYMBOLS), LAST_SYMBOLS  # none of the marks is an SVG the harness drew
        assert found.get("TechDraw::DrawLeaderLine", 0) >= 1, found
        assert found.get("TechDraw::DrawViewAnnotation", 0) >= 1, found
        shot_reply = await cad("drawing", action="capture")
        assert shot_reply.images, shot_reply.text
        shot(shots, "nt4_mould", shot_reply.images[0].data)
