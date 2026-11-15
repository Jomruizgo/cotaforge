# ruff: noqa: E501
"""Welds, balloons, the parts list and the moulded-part marks are FreeCAD's own objects, on the real FreeCAD (Windows, VISIBLE):
the welded frame (weld symbol, cut list, balloons) and the assembly (parts list, balloons), and a
moulded part with its marks. The sheets are what a shop reader gets: the weld's size and length are black in the exported PDF,
every row of a list has a balloon with a leader on its part, the title block holds its texts, no mark runs over itself.
The test also plays the engineer's hand: it edits a weld tile, a balloon number and a cell of the list, moves a view and checks
that what hangs on it follows -- on a COPY of the document: a proof edit never stays on the sheet that is exported.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the images of the sheets.
"""

import base64
import json
import os
import shutil
import sys
from pathlib import Path

import pytest

from cotaforge_mcp import pdf_check
from tests.integration.conftest import Cad, finish_drawing, running_server
from tests.integration.test_drawing_review_gaps_real import build_assembly
from tests.integration.test_drawing_weldment_real import WELD, build_frame

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

MOULD_PART = Path(r"C:\Users\Jorge Ruiz\CotaForge-ejercicios\molde\parts\caja_paso_base.FCStd")  # read only: a copy is opened
PAGE_CODES = (
    "page_test_marker",
    "page_cell_overflow",
    "page_balloons_not_matching",
    "page_balloon_no_leader",
    "page_balloon_off_part",
    "page_weld_off_line",
    "title_block_overflow",
    "view_label_ambiguous",
    "mark_overlaps_itself",
)  # what a shop reader sent back on the sheets of the first version: none may be listed
NOTES = {"type": "note", "id": "weld", "text": "Proceso MAG, nivel de calidad ISO 5817-C. Tolerancias generales ISO 13920-B. Símbolos de soldadura según ISO 2553: a = garganta"}
WELD_SIZED = {**WELD, "all_around": False, "length_mm": 40}  # a size before the symbol and a length after it

LAST_SYMBOLS: list[str] = []  # the symbols the last count found: their unit keys
KINDS = """
import FreeCAD as App, collections, json
doc = App.ActiveDocument
page = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawPage"][0]
kinds = collections.Counter(o.TypeId for o in doc.Objects if o.TypeId.startswith(("TechDraw::DrawLeader", "TechDraw::DrawWeld", "TechDraw::DrawTile", "TechDraw::DrawViewBalloon", "TechDraw::DrawViewSpreadsheet", "Spreadsheet", "TechDraw::DrawViewSymbol", "TechDraw::DrawViewAnnotation", "TechDraw::DrawViewPart", "TechDraw::DrawProjGroup")))
foreign = [str(getattr(o, "CotaforgeKey", "")) or o.Label for o in page.Views if o.TypeId == "TechDraw::DrawViewSymbol"]
print("KINDS " + json.dumps(dict(kinds)) + " SYMBOLS " + json.dumps(foreign))
"""

# what the page holds as the shop reads it: the weld's tiles and tail, the first column of the list, and where each text is painted
SCAN = """
import FreeCAD as App, json, gui
import TechDrawGui
doc = App.ActiveDocument
page = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawPage"][0]
page.ViewObject.doubleClicked()
for _ in range(10):
    gui.pump()
scene = TechDrawGui.getSceneForPage(page)
tiles = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawTileWeld"]
cells = [o for o in doc.Objects if o.TypeId == "Spreadsheet::Sheet"]
texts = {}
for item in scene.items():
    if type(item).__name__ == "QGraphicsTextItem" and item.toPlainText().strip():
        r = item.sceneBoundingRect()
        texts.setdefault(item.toPlainText().strip(), []).append([round(r.x() / 10, 2), round(-(r.y() + r.height()) / 10, 2), round((r.x() + r.width()) / 10, 2), round(-r.y() / 10, 2), item.defaultTextColor().name()])
svgs = []
for item in scene.items():
    if "Pixmap" in type(item).__name__:
        r = item.sceneBoundingRect()
        if 3.0 < r.width() / 10 < 9.0 and abs(r.width() - r.height()) < 5:
            svgs.append([round(r.x() / 10, 1), round(-(r.y() + r.height()) / 10, 1), round((r.x() + r.width()) / 10, 1), round(-r.y() / 10, 1)])
balloons = [[str(o.Text), float(o.X), float(o.Y), float(o.OriginX), float(o.OriginY), float(o.SourceView.getScale())] for o in doc.Objects if o.TypeId == "TechDraw::DrawViewBalloon"]
def page_xy(view):
    x, y = float(view.X), float(view.Y)
    for g in page.Views:
        if g.TypeId == "TechDraw::DrawProjGroup" and view.Name in [v.Name for v in g.Views]:
            x, y = x + float(g.X), y + float(g.Y)
    return x, y

line_y = []
for o in doc.Objects:
    if o.TypeId == "TechDraw::DrawWeldSymbol":
        lead = o.Leader
        base_x, base_y = page_xy(lead.LeaderParent)
        line_y.append(round(base_y + float(lead.Y) * lead.LeaderParent.getScale() - float(lead.WayPoints[-1].y), 2))
want = __WANT__
facts = {
    "tiles": [[int(t.TileRow), str(t.SymbolFile).replace(chr(92), "/").rsplit("/", 1)[-1], str(t.LeftText), str(t.RightText)] for t in tiles],
    "tails": [str(o.TailText) for o in doc.Objects if o.TypeId == "TechDraw::DrawWeldSymbol"],
    "column_a": [c.getContents("A" + str(n)).lstrip(chr(39)) for c in cells for n in range(1, 12) if c.getContents("A" + str(n))],
    "texts": {k: v for k, v in texts.items() if k in want},
    "svgs": svgs[:6],
    "balloons": balloons,
    "line_y": line_y,
}
for key in __KEYS__:
    print("SCAN_" + key + " " + json.dumps(facts[key]))
"""


# the engineer's hand, on a COPY of the document: the sheet that is exported never holds a proof edit
HAND = """
import FreeCAD as App, FreeCADGui, json, gui, os, tempfile
import TechDrawGui
orig = App.ActiveDocument
path = os.path.join(tempfile.mkdtemp(prefix="cotaforge_proof_"), "proof.FCStd")
orig.saveCopy(path)
doc = App.openDocument(path)
App.setActiveDocument(doc.Name)
page = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawPage"][0]
page.ViewObject.doubleClicked()
for _ in range(10):
    gui.pump()
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
scene = TechDrawGui.getSceneForPage(page)

def where(text):
    for item in scene.items():
        if type(item).__name__ == "QGraphicsTextItem" and item.toPlainText().strip() == text:
            r = item.sceneBoundingRect()
            return (round((r.x() + r.width() / 2) / 10, 2), round(-(r.y() + r.height() / 2) / 10, 2))
    return None

before = {"balloon": where("9")}
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
after = {"balloon": where("9")}
facts = {"old": old, "now": now, "before": before, "after": after, "moved": moved.Name}
App.closeDocument(doc.Name)
App.setActiveDocument(orig.Name)
print("HAND " + json.dumps(facts))
"""


def shot(shots: Path, name: str, data: str) -> None:
    shots.mkdir(parents=True, exist_ok=True)
    (shots / f"{name}.png").write_bytes(base64.b64decode(data))


async def patient(cad: Cad, tool: str, tries: int = 30, **arguments: object) -> object:
    """``cad(tool, ...)``, asked again while the window is busy: a big part keeps FreeCAD painting for minutes after a sheet is
    built, and a call that finds it so answers ``timeout`` after 90 s without having failed."""
    reply = await cad(tool, **arguments)
    for _ in range(tries):
        if not (reply.is_error and "timeout" in reply.text):
            break
        reply = await cad(tool, **arguments)
    return reply


async def kinds(cad: Cad) -> dict[str, int]:
    reply = await patient(cad, "run_native", code=KINDS, intent="count what the sheet holds")
    assert not reply.is_error, reply.text
    text = reply.text
    line = next(part for part in text.splitlines() if "KINDS " in part)
    LAST_SYMBOLS[:] = json.loads(line.split(" SYMBOLS ", 1)[1])
    print("SYMBOLS", LAST_SYMBOLS)
    return json.loads(line.split("KINDS ", 1)[1].split(" SYMBOLS ", 1)[0])


async def scan(cad: Cad, want: tuple[str, ...] = (), keys: tuple[str, ...] = ("tiles", "tails", "column_a", "balloons", "line_y")) -> dict:
    code = SCAN.replace("__WANT__", json.dumps(list(want))).replace("__KEYS__", json.dumps(list(keys)))
    reply = await patient(cad, "run_native", code=code, intent="read what the page holds, as the shop reads it")
    assert not reply.is_error, reply.text
    text = reply.text
    out: dict = {}
    for line in text.splitlines():
        for key in ("tiles", "tails", "column_a", "texts", "svgs", "balloons", "line_y"):
            if f"SCAN_{key} " in line:
                out[key] = json.loads(line.split(f"SCAN_{key} ", 1)[1])
    return out


async def clean_review(cad: Cad, what: str) -> str:
    """The review of the sheet, listing none of what a shop reader sent back (PAGE_CODES)."""
    reply = await patient(cad, "drawing", action="review")
    assert not reply.is_error, reply.text
    review = reply.text
    print(f"REVIEW {what}", [line for line in review.splitlines() if any(code in line for code in PAGE_CODES)])
    print(f"FULL REVIEW {what}", review[:4000])
    listed = [code for code in PAGE_CODES if code in review]
    assert not listed, f"{what}: the review lists {listed}\n{review}"
    return review


def inside(box: list[float], rect_mm: tuple[float, float, float, float]) -> bool:
    cx, cy = (rect_mm[0] + rect_mm[2]) / 2, (rect_mm[1] + rect_mm[3]) / 2
    return box[0] - 0.5 <= cx <= box[2] + 0.5 and box[1] - 0.5 <= cy <= box[3] + 0.5


async def test_the_welded_frame_has_native_welds_a_cut_list_and_balloons(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await build_frame(cad)
        await cad.ok("drawing", action="create", template="A3", angle="first", title="Bastidor")
        rows = [{"component": "body:1", "name": "Tubo 40×40×3"}, {"component": "body:5", "name": "Chapa 6"}]
        added = await cad.ok("drawing", action="add", items=[{"type": "parts_list", "id": "cut", "rows": rows}, {"type": "balloons", "id": "bal", "view": "isometric"}, NOTES, WELD_SIZED])
        print("ADDED", added)
        print("LIST", await cad.ok("drawing", action="list"))
        found = await kinds(cad)
        print("FRAME", found)
        assert found.get("Spreadsheet::Sheet") == 1 and found.get("TechDraw::DrawViewSpreadsheet") == 1, found  # the cut list
        assert found.get("TechDraw::DrawViewBalloon") == 2, found  # one per position: the tubes and the plate
        assert found.get("TechDraw::DrawWeldSymbol") == 1 and found.get("TechDraw::DrawTileWeld", 0) >= 1, found
        assert found.get("TechDraw::DrawLeaderLine", 0) >= 3, found  # the reference line's, and an arrow for each of the two joints it marks
        assert not found.get("TechDraw::DrawViewSymbol"), found  # nothing the harness drew
        await clean_review(cad, "frame")

        # the weld as AWS writes it: the size left of the symbol, the length right of it, the process and notes in the tail
        facts = await scan(cad)
        print("SCAN", json.dumps(facts)[:1500])
        print("LINE_Y", facts["line_y"])
        by_row = {row: (name, left, right) for row, name, left, right in facts["tiles"] if name.endswith(("Down.svg", "Up.svg"))}
        assert by_row[-1][0] == "filletDown.svg" and by_row[-1][1:] == ("a3", "40"), by_row  # the arrow side: row -1, painted below the line
        assert len(facts["tails"]) == 1 and facts["tails"][0].startswith("4") and len(facts["tails"][0]) <= 3, facts["tails"]  # the tail: only how many identical joints
        assert sorted(x for x in facts["column_a"] if x.isdigit()) == ["1", "2"], facts["column_a"]  # the list rows: a balloon each
        assert all(((b[1] - b[3]) ** 2 + (b[2] - b[4]) ** 2) ** 0.5 * b[5] > 6.5 for b in facts["balloons"]), facts["balloons"]  # a leader that shows past the circle
        shot_reply = await cad("drawing", action="capture")
        assert shot_reply.images, shot_reply.text
        shot(shots, "nt6_frame", shot_reply.images[0].data)

        # what is read is the exported PDF: the size and the length are painted black there, in any window theme
        await finish_drawing(cad)
        pdf = workspace / "frame.pdf"
        exported = await cad.ok("export", action="pdf", path=str(pdf))
        assert "checked: one page" in exported, exported
        painted = pdf_check.filled_paths(pdf)
        facts = await scan(cad, ("a3", "40"), ("texts", "line_y"))
        line_y = facts["line_y"][0]
        for text in ("a3", "40"):
            boxes = facts["texts"].get(text)
            assert boxes, f"{text!r} is not painted on the sheet: {sorted(facts['texts'])[:20]}"
            for box in boxes:
                assert (box[1] + box[3]) / 2 < line_y - 0.3, f"{text!r} is not under the reference line (y {line_y}): {box}"  # the arrow side goes below the line
                ink = [colour for colour, rect in painted if inside(box, rect) and (rect[2] - rect[0]) < 30 and (rect[3] - rect[1]) < 10]
                print("INK", text, box[4], ink[:3])
                assert ink, f"nothing of {text!r} is in the PDF at {box}"
                assert not any(pdf_check.is_light(colour) for colour in ink), f"{text!r} is printed in a light colour: {ink[:3]}"
        shots.mkdir(parents=True, exist_ok=True)
        shutil.copy(pdf, shots / "nt6_frame.pdf")
        left, right = facts["texts"]["a3"][0], facts["texts"]["40"][0]
        zoom = await cad("drawing", action="capture", region=[round(left[0] - 30), round(line_y - 18), round(right[2] + 25), round(line_y + 18)])
        assert zoom.images, zoom.text
        shot(shots, "nt6_frame_weld", zoom.images[0].data)  # the weld as the welder reads it: size, symbol below the line, length

        # the engineer's hand: a tile, a balloon and a cell edited and a view moved -- on a COPY, the sheet stays as it was
        hand = await cad.ok("run_native", code=HAND, intent="the engineer edits a tile, a balloon and a cell, then moves a view (on a copy)")
        print("HAND EDIT", hand)
        proof = json.loads(next(part for part in hand.splitlines() if "HAND " in part).split("HAND ", 1)[1])
        assert proof["now"] == ["a4", "9", "'Pos. editada"], proof
        before, after = proof["before"]["balloon"], proof["after"]["balloon"]
        assert before and after and abs(after[0] - before[0] - 30.0) < 0.6 and abs(after[1] - before[1]) < 0.6, proof  # the balloon follows its view
        kept = await scan(cad)
        assert "Pos. editada" not in kept["column_a"] and sorted(x for x in kept["column_a"] if x.isdigit()) == ["1", "2"], kept["column_a"]  # the sheet kept its cells
        assert [t for t in kept["tiles"] if t[1].endswith(("Down.svg", "Up.svg"))][0][2] != "a4", kept["tiles"]
        await clean_review(cad, "frame after the proof on the copy")


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
        facts = await scan(cad)
        print("SCAN", json.dumps(facts)[:1200])
        assert sorted(x for x in facts["column_a"] if x.isdigit()) == sorted(str(b[0]) for b in facts["balloons"]), facts  # one balloon per row
        assert all(((b[1] - b[3]) ** 2 + (b[2] - b[4]) ** 2) ** 0.5 * b[5] > 6.5 for b in facts["balloons"]), facts["balloons"]  # a leader that shows past the circle
        await clean_review(cad, "assembly")
        shot_reply = await cad("drawing", action="capture")
        assert shot_reply.images, shot_reply.text
        shot(shots, "nt6_assembly", shot_reply.images[0].data)


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
        shot_reply = await patient(cad, "drawing", action="capture")
        assert shot_reply.images, shot_reply.text
        shot(shots, "nt6_mould", shot_reply.images[0].data)  # the sheet first: the checks below wait for a window that is busy for minutes
        found = await kinds(cad)
        print("MOULD", found)
        review = await clean_review(cad, "mould")
        print("FALLBACKS", [line for line in review.splitlines() if "native_fallback" in line or "TechDraw" in line][:12])
        mine = {"pl", "pu", "gate", "ej", "nd"}
        assert not mine & set(LAST_SYMBOLS), LAST_SYMBOLS  # none of the marks is an SVG the harness drew
        assert found.get("TechDraw::DrawLeaderLine", 0) >= 1, found
        assert found.get("TechDraw::DrawViewAnnotation", 0) >= 1, found



TAMPER = """
import FreeCAD as App, gui, json
doc = App.ActiveDocument
what = "__WHAT__"
balloons = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawViewBalloon"]
cells = [o for o in doc.Objects if o.TypeId == "Spreadsheet::Sheet"][0]
b = balloons[0]
if what == "no_leader":
    b.OriginX, b.OriginY = b.X, b.Y
elif what == "off_part":
    b.OriginX, b.OriginY = float(b.OriginX) + 4000.0, float(b.OriginY) + 4000.0
elif what == "number":
    b.Text = "9"
elif what == "marker":
    cells.set("B1", "'Pos. editada")
elif what == "restore":
    b.X, b.Y, b.OriginX, b.OriginY, b.Text = __BALLOON__
    cells.set("B1", __CELL__)
doc.recompute()
for _ in range(10):
    gui.pump()
print("STATE " + json.dumps([float(b.X), float(b.Y), float(b.OriginX), float(b.OriginY), str(b.Text), cells.getContents("B1")]))
"""


async def tamper(cad: Cad, what: str, balloon: str = "[]", cell: str = "''") -> list:
    code = TAMPER.replace("__WHAT__", what).replace("__BALLOON__", balloon).replace("__CELL__", cell)
    text = await cad.ok("run_native", code=code, intent=f"damage the sheet the way a shop reader found it damaged: {what}")
    return json.loads(next(line for line in text.splitlines() if "STATE " in line).split("STATE ", 1)[1])


async def test_the_review_finds_what_the_shop_reader_found_on_the_real_page(workspace: Path) -> None:
    """Each defect of the first version, made by hand on a real sheet, is listed by the review; put back, the review is clean."""
    async with running_server(workspace, visible=True) as (cad, _):
        await build_assembly(cad)
        await cad.ok("drawing", action="create", template="A3", angle="first", title="Conjunto", views=["front", "top", "iso"])
        await clean_review(cad, "assembly before the damage")
        state = await tamper(cad, "read")
        original = (state[0], state[1], state[2], state[3], state[4])
        for what, code in (("no_leader", "page_balloon_no_leader"), ("off_part", "page_balloon_off_part"), ("number", "page_balloons_not_matching"), ("marker", "page_test_marker")):
            await tamper(cad, what)
            review = await cad.ok("drawing", action="review")
            assert code in review, f"{what}: {code} is not listed\n{review[:1500]}"
            await tamper(cad, "restore", balloon=repr(list(original)), cell=repr(state[5]))
            await clean_review(cad, f"after putting {what} back")
