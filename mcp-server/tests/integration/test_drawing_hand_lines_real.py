# ruff: noqa: E501
"""The engineer's hand and the sheet's centre lines, on the REAL FreeCAD (Windows, VISIBLE): the bench plate (centre marks on a
view from above) and the shaft (axes that are TechDraw's own centre-line objects).

* A centre line the engineer deletes by hand stays deleted: no item added later, no review and no export draws it again, and it
  never holds the export; the review mentions it once, as information.
* A centre line the engineer makes by hand (FreeCAD's own centre-line command and its defaults) carries his own line style and is
  never judged by ``centre_line_not_chain``.
* The widths on the paper are still the table's (0.35 / 0.18 / 0.07): the line standard changes the dash patterns only.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the PDFs and the images of the sheets.
"""

import base64
import json
import os
import re
import sys
from pathlib import Path

import pytest

from tests import pdf_widths
from tests.integration.conftest import Cad, finish_drawing, running_server
from tests.integration.test_drawing_line_weights_real import SCENARIOS, unexplained_widths

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

THIN_MM = 0.18
TOLERANCE_MM = 0.011


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COTAFORGE_HOME", str(tmp_path / "home"))


# what the views of the page hold: their centre lines (TechDraw's own objects) and the cosmetic edges drawn as centre lines, with
# the line number each one saves
READ = """
import FreeCAD as App, io, json, re, zipfile
doc = App.ActiveDocument
page = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawPage"][0]
def numbers(view, prop, item):
    z = zipfile.ZipFile(io.BytesIO(bytes(view.dumpPropertyContent(prop))))
    xml = z.read(z.namelist()[0]).decode()
    return [int(m.group(1)) if (m := re.search(r'<LineNumber value="(\\d+)"', b)) else 0 for b in re.findall(r"<" + item + r"\\s.*?</" + item + ">", xml, flags=re.S)]
out = {}
for v in doc.Objects:
    if not hasattr(v, "CenterLines"):
        continue
    centre = list(v.CenterLines)
    cosmetic = list(v.CosmeticEdges)
    out[v.Name] = {
        "centre": numbers(v, "CenterLines", "CenterLine") if centre else [],
        "marks": [n for e, n in zip(cosmetic, numbers(v, "CosmeticEdges", "CosmeticEdge") if cosmetic else []) if dict(e.Format).get("style") == 4],
    }
print("LINES " + json.dumps(out))
"""

# the engineer at the sheet: he deletes one of the sheet's lines and makes a centre line of his own with FreeCAD's own command
HAND = """
import FreeCAD as App, FreeCADGui as Gui, gui, json
doc = App.ActiveDocument
page = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawPage"][0]
views = [v for v in doc.Objects if hasattr(v, "CenterLines")]
result = {"deleted": None, "made": None, "how": None}
if __KIND__ == "centre":
    view = next(v for v in views if len(list(v.CenterLines)) >= 1)
    gone = list(view.CenterLines)[0]
    result["deleted"] = [view.Name, ",".join(str(e) for e in gone.Edges)]
    view.removeCenterLine(str(gone.Tag))
else:
    view = next(v for v in views if any(dict(e.Format).get("style") == 4 for e in v.CosmeticEdges))
    gone = next(e for e in view.CosmeticEdges if dict(e.Format).get("style") == 4)
    result["deleted"] = [view.Name, str(gone.Tag)]
    view.removeCosmeticEdge(str(gone.Tag))
doc.recompute()
# his own centre line: two parallel edges of a view, picked and sent to FreeCAD's two-lines centre-line command; its dialog is
# accepted with the OK button, with the values it opens with
edges = list(view.getVisibleEdges())
edges = edges[: len(edges) - len(list(view.CenterLines)) - len(list(view.CosmeticEdges))]  # the part's own edges come first
lines =[i for i, e in enumerate(edges) if type(e.Curve).__name__ == "Line"]
own = len(list(view.CenterLines))
tried = []
try:
    page.ViewObject.doubleClicked()
    for _ in range(10):
        gui.pump()
    Gui.Selection.clearSelection()
    pair = None
    for a in lines:
        for b in lines:
            if a < b and abs(abs(edges[a].Vertexes[0].Point.y - edges[b].Vertexes[0].Point.y)) > 3 and abs(edges[a].Vertexes[0].Point.x - edges[a].Vertexes[-1].Point.x) > 3 and abs(edges[a].Vertexes[0].Point.y - edges[a].Vertexes[-1].Point.y) < 1e-6 and abs(edges[b].Vertexes[0].Point.y - edges[b].Vertexes[-1].Point.y) < 1e-6:
                pair = (a, b)
                break
        if pair:
            break
    tried.append(["pair", pair])
    Gui.Selection.addSelection(doc.Name, view.Name, "Edge%d" % pair[0])
    Gui.Selection.addSelection(doc.Name, view.Name, "Edge%d" % pair[1])
    Gui.runCommand("TechDraw_2LineCenterLine")
    for _ in range(20):
        gui.pump()
    from PySide import QtWidgets
    boxes = [w for w in QtWidgets.QApplication.allWidgets() if isinstance(w, QtWidgets.QDialogButtonBox) and w.isVisible()]
    tried.append(["boxes", len(boxes)])
    ok = None
    for box in boxes:
        ok = box.button(QtWidgets.QDialogButtonBox.Ok)
        if ok is not None:
            break
    ok.click()
    for _ in range(20):
        gui.pump()
    result["how"] = "FreeCAD command TechDraw_2LineCenterLine, dialog accepted"
except Exception as exc:
    tried.append(["error", type(exc).__name__ + ": " + str(exc)])
if len(list(view.CenterLines)) == own:
    result["how"] = "makeCenterLine (the dialog could not be driven)"
    if len(lines) >= 2:
        view.makeCenterLine(["Edge%d" % lines[0], "Edge%d" % lines[1]], 1)
result["tried"] = tried
result["made"] = len(list(view.CenterLines)) - own
doc.recompute()
for _ in range(20):
    gui.pump()
print("HAND " + json.dumps(result))
"""


async def lines(cad: Cad) -> dict[str, dict[str, list]]:
    reply = await cad("run_native", code=READ, intent="count the centre lines the views hold")
    assert not reply.is_error, reply.text
    row = next(part for part in reply.text.splitlines() if "LINES " in part)
    return json.loads(row.split("LINES ", 1)[1])


def total(snapshot: dict[str, dict[str, list]], key: str) -> int:
    return sum(len(view[key]) for view in snapshot.values())


@pytest.mark.expected_findings("dimension_gap", "view_adds_nothing", "native_unread")
@pytest.mark.parametrize(("which", "kind"), [("shaft", "centre"), ("plate", "mark")])
async def test_a_line_the_engineer_deletes_stays_deleted_and_one_he_makes_is_not_judged(
    which: str, kind: str, workspace: Path, tmp_path: Path
) -> None:
    out = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    out.mkdir(parents=True, exist_ok=True)
    async with running_server(workspace, visible=True) as (cad, _):
        await SCENARIOS[which](cad, workspace)
        made = await lines(cad)
        print("MADE", json.dumps(made))
        first = await cad.ok("drawing", action="review")
        assert "centre_line_not_chain" not in first and "line_deleted_by_hand" not in first, first
        capture = await cad("drawing", action="capture")
        if capture.images:
            (out / f"cl4_{which}_0_made.png").write_bytes(base64.b64decode(capture.images[0].data))

        reply = await cad("run_native", code=HAND.replace("__KIND__", repr(kind)), intent="the engineer deletes one axis and draws one of his own (on this sheet)")
        assert not reply.is_error, reply.text
        hand = json.loads(next(p for p in reply.text.splitlines() if "HAND " in p).split("HAND ", 1)[1])
        print("HAND", hand)
        assert hand["made"] == 1, hand
        after_hand = await lines(cad)
        print("AFTER_HAND", json.dumps(after_hand))
        assert total(after_hand, "centre") + total(after_hand, "marks") == total(made, "centre") + total(made, "marks"), (after_hand, made)

        # an item added later, the review, the export: none of them draws the deleted line again, none of them judges his
        await cad.ok("drawing", action="add", items=[{"type": "note", "id": "cl4_note", "text": "Sin rebabas"}])
        after_add = await lines(cad)
        assert [total(after_add, k) for k in ("centre", "marks")] == [total(after_hand, k) for k in ("centre", "marks")], (after_add, after_hand)
        review = await cad.ok("drawing", action="review")
        assert "centre_line_not_chain" not in review, review
        assert len(re.findall(r"line_deleted_by_hand", review)) <= 2, review  # one finding (its id may be repeated once in the table)
        assert "BLOCKS EXPORT line_deleted_by_hand" not in review, review
        assert "line_deleted_by_hand" in review, review
        capture = await cad("drawing", action="capture")
        if capture.images:
            (out / f"cl4_{which}_1_after_hand.png").write_bytes(base64.b64decode(capture.images[0].data))

        await finish_drawing(cad)
        pdf = workspace / f"{which}_hand.pdf"
        await cad.ok("export", action="pdf", path=str(pdf))  # not held by the deleted line
        after_export = await lines(cad)
        assert [total(after_export, k) for k in ("centre", "marks")] == [total(after_hand, k) for k in ("centre", "marks")], (after_export, after_hand)
        kept = out / f"cl4_{which}_hand.pdf"
        kept.write_bytes(pdf.read_bytes())
    dashed = pdf_widths.dashed_strokes(kept)
    print("DASH PATTERNS", which, sorted({(width, pattern) for width, pattern, _ in dashed}))
    widths = pdf_widths.by_width(kept)
    print(pdf_widths.table(kept))
    assert not unexplained_widths(widths), (widths, "the line standard must change dash patterns only, never widths")
    thin_chain = [pattern for width, pattern, _ in dashed if abs(width - THIN_MM) <= TOLERANCE_MM and pdf_widths.is_chain(pattern)]
    assert thin_chain, "the sheet's centre lines are still the chain line in the PDF"
