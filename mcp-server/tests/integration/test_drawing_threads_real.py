# ruff: noqa: E501
"""Threads on a sheet are TechDraw's own objects, on the REAL FreeCAD (Windows, VISIBLE).

Every line a thread has on the sheet -- the thin lines of an external thread at its minor diameter, the wide line that ends it, the
three-quarter arc of an end view, the thin lines of a threaded bore in a section -- is a cosmetic edge of its view, the kind
TechDraw's own *Cosmetic thread* commands make. None is a picture. The line that ends a thread spans exactly the major diameter and
the lines of the minor diameter run the thread length. The widths on the paper are the harness's table.

A thread the engineer draws with TechDraw's own command is accepted; a line of the sheet that he deletes stays deleted and one he
restyles keeps his style, through annotate, review and export.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the PDFs and the images of the sheets.
"""

import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from tests import pdf_widths
from tests.integration.conftest import Cad, Reply, finish_drawing, running_server
from tests.integration.test_drawing_line_weights_real import SCENARIOS, unexplained_widths
from tests.integration.test_drawing_review_gaps_real import plate

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

THIN_MM = 0.18
THICK_MM = 0.35
TOLERANCE_MM = 0.011
CONTINUOUS = 1  # TechDraw's line number of the continuous line in every line standard
SHAFT_MAJOR_MM = 20.0  # the bench shaft: M20x1.5 over 26 mm of useful length
SHAFT_LENGTH_MM = 26.0
SHAFT_MINOR_MM = 18.376  # d1 of M20x1.5
PLATE_MAJOR_MM = 8.0  # the plate: two tapped M8 holes, thread 10 mm deep
PLATE_THREAD_DEPTH_MM = 10.0

# the cosmetic edges of every view of the page, written to a file (the reply of run_native is cut at a thousand characters)
READ = """
import FreeCAD as App, io, json, re, zipfile
doc = App.ActiveDocument
def numbers(view):
    z = zipfile.ZipFile(io.BytesIO(bytes(view.dumpPropertyContent("CosmeticEdges"))))
    xml = z.read(z.namelist()[0]).decode()
    return [int(m.group(1)) if (m := re.search(r'<LineNumber value="(\\d+)"', b)) else 0 for b in re.findall(r"<CosmeticEdge\\s.*?</CosmeticEdge>", xml, flags=re.S)]
out = {"symbols": [o.Label for o in doc.Objects if o.TypeId == "TechDraw::DrawViewSymbol"], "views": {}}
for v in doc.Objects:
    if not hasattr(v, "CosmeticEdges"):
        continue
    edges = list(v.CosmeticEdges)
    out["views"][v.Name] = {"label": v.Label, "scale": float(v.getScale()), "edges": []}
    for e, n in zip(edges, numbers(v) if edges else []):
        fmt = dict(e.Format)
        try:
            geo = ["circle", round(e.Center.x, 3), round(e.Center.y, 3), round(e.Radius, 3)]
        except Exception:
            geo = ["line", round(e.Start.x, 3), round(e.Start.y, 3), round(e.End.x, 3), round(e.End.y, 3)]
        out["views"][v.Name]["edges"].append({"geo": geo, "weight": round(fmt.get("weight"), 3), "style": fmt.get("style"), "number": n, "tag": str(e.Tag)})
open(__OUT__, "w").write(json.dumps(out))
"""


async def snapshot(cad: Cad, workspace: Path) -> dict[str, Any]:
    """The symbols of the page and, per view, its cosmetic edges: ``{"symbols": [...], "views": {name: {"label", "scale", "edges":
    [{"geo", "weight", "style", "number", "tag"}]}}}``, read from the live document."""
    target = workspace / "th1_snapshot.json"
    target.unlink(missing_ok=True)
    reply = await cad("run_native", code=READ.replace("__OUT__", repr(str(target))), intent="read the cosmetic edges of the views of the page")
    assert not reply.is_error, reply.text
    return json.loads(target.read_text(encoding="utf-8"))


def edges_of(snap: dict[str, Any], label: str | None = None) -> list[dict[str, Any]]:
    return [e for v in snap["views"].values() if label is None or v["label"] == label for e in v["edges"]]


def lines(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in edges if e["geo"][0] == "line"]


def length(edge: dict[str, Any]) -> float:
    _, x1, y1, x2, y2 = edge["geo"]
    return float(((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5)


def circles(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [e for e in edges if e["geo"][0] == "circle"]


def thread_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What a thread is on a view: the continuous thin lines, arcs and circles and the continuous wide lines (style 1; a centre
    line is style 4)."""
    return [e for e in edges if e["style"] == 1 and any(abs(e["weight"] - w) <= TOLERANCE_MM for w in (THIN_MM, THICK_MM))]


def keep(out: Path, name: str, reply: Reply) -> None:
    if reply.images:
        (out / name).write_bytes(base64.b64decode(reply.images[0].data))


def no_thread_picture(snap: dict[str, Any]) -> None:
    assert not [s for s in snap["symbols"] if "thread" in s.lower() or "rosca" in s.lower()], snap["symbols"]


def stand_on_the_table(pdf: Path) -> None:
    """The widths on the paper are the table's, and no thin dashed stroke is anything but a chain line."""
    print(pdf_widths.table(pdf))
    assert not unexplained_widths(pdf_widths.by_width(pdf)), pdf_widths.by_width(pdf)
    thin_dashed = [p for w, p, _ in pdf_widths.dashed_strokes(pdf) if abs(w - THIN_MM) <= TOLERANCE_MM]
    assert all(pdf_widths.is_chain(p) for p in thin_dashed), sorted(set(thin_dashed))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COTAFORGE_HOME", str(tmp_path / "home"))


def shaft_thread(snap: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """The two thin lines at the minor diameter and the one wide line that ends the thread, on the front view of the shaft."""
    front = lines(edges_of(snap, "View_front"))
    thin = [e for e in front if abs(e["weight"] - THIN_MM) <= TOLERANCE_MM and e["style"] == 1 and abs(length(e) - SHAFT_LENGTH_MM) <= TOLERANCE_MM]
    wide = [
        e
        for e in front
        if abs(e["weight"] - THICK_MM) <= TOLERANCE_MM
        and e["style"] == 1
        and abs(length(e) - SHAFT_MAJOR_MM) <= TOLERANCE_MM
        and abs(e["geo"][1] - e["geo"][3]) <= TOLERANCE_MM
    ]
    assert len(thin) == 2 and len(wide) == 1, (thin, wide)
    return thin, wide[0]


@pytest.mark.expected_findings("dimension_gap", "view_adds_nothing", "native_unread")
async def test_the_bench_shaft_thread_is_native_and_exactly_as_long_as_it_is_wide(workspace: Path, tmp_path: Path) -> None:
    out = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    out.mkdir(parents=True, exist_ok=True)
    async with running_server(workspace, visible=True) as (cad, _):
        await SCENARIOS["shaft"](cad, workspace)
        snap = await snapshot(cad, workspace)
        no_thread_picture(snap)
        thin, wide = shaft_thread(snap)
        # the thin lines run the thread length at the minor diameter, either side of the axis; the wide line ends them
        assert sorted(round(e["geo"][2], 2) for e in thin) == [-round(SHAFT_MINOR_MM / 2, 2), round(SHAFT_MINOR_MM / 2, 2)], thin
        assert abs(length(wide) - SHAFT_MAJOR_MM) <= TOLERANCE_MM, wide  # square to the axis, across the major diameter and no further
        assert {round(wide["geo"][1], 2)} == {round(thin[0]["geo"][3], 2)} == {round(thin[1]["geo"][3], 2)}  # on the end of the thin lines
        assert all(e["number"] == CONTINUOUS for e in (*thin, wide)), (thin, wide)  # TechDraw's line number: continuous
        keep(out, "th1_shaft.png", await cad("drawing", action="capture"))
        await finish_drawing(cad)
        pdf = workspace / "shaft_thread.pdf"
        await cad.ok("export", action="pdf", path=str(pdf))
        kept = out / "th1_shaft.pdf"
        kept.write_bytes(pdf.read_bytes())
    stand_on_the_table(kept)


def plate_views(snap: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """The threaded plate's end view (the top view) and its section."""
    top = edges_of(snap, "View_top")
    standard = ("View_top", "View_front", "View_right", "View_iso")
    return top, [e for v in snap["views"].values() if v["label"] not in standard for e in v["edges"]]


async def plate_sheet(cad: Cad) -> None:
    await plate(cad, "placa", 20, True, "Placa roscada")
    await cad.ok("drawing", action="create", template="A3", angle="first", title="Placa roscada", views=["top", "front"])
    await cad.ok("drawing", action="add", items=[{"type": "hole_dim", "all": True}, {"type": "view_lines"}, {"type": "overall"}])
    await cad.ok("drawing", action="add", items=[{"type": "section", "id": "secT", "holes": "all", "view": "top"}])


@pytest.mark.expected_findings("dimension_gap", "view_adds_nothing", "native_unread", "hole_not_located", "sheet_underused")
async def test_a_tapped_plate_has_the_arc_in_its_end_view_and_the_lines_in_its_section(workspace: Path, tmp_path: Path) -> None:
    out = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    out.mkdir(parents=True, exist_ok=True)
    async with running_server(workspace, visible=True) as (cad, _):
        await plate_sheet(cad)
        snap = await snapshot(cad, workspace)
        print("PLATE", json.dumps({k: [(e["geo"], e["weight"], e["style"], e["number"]) for e in v["edges"]] for k, v in snap["views"].items()}))
        no_thread_picture(snap)
        top, section = plate_views(snap)
        arcs = [e for e in circles(thread_edges(top)) if abs(e["geo"][3] - PLATE_MAJOR_MM / 2) <= TOLERANCE_MM]
        assert len(arcs) == 2, top  # one at the major diameter round each tapped hole of the end view
        assert all(e["number"] == CONTINUOUS and abs(e["weight"] - THIN_MM) <= TOLERANCE_MM for e in arcs), arcs
        cut = [e for e in lines(thread_edges(section)) if abs(length(e) - PLATE_THREAD_DEPTH_MM) <= TOLERANCE_MM and abs(e["weight"] - THIN_MM) <= TOLERANCE_MM]
        assert len(cut) == 4, section  # two thin lines at the major diameter, the thread's depth long, in each bored wall
        assert all(e["number"] == CONTINUOUS for e in cut), cut
        keep(out, "th1_plate.png", await cad("drawing", action="capture"))
        await finish_drawing(cad)
        pdf = workspace / "plate_thread.pdf"
        await cad.ok("export", action="pdf", path=str(pdf))
        kept = out / "th1_plate.pdf"
        kept.write_bytes(pdf.read_bytes())
    stand_on_the_table(kept)


# the engineer at the shaft: he deletes the line that ends the thread and gives one of its thin lines another weight and another
# line number (the path TechDraw's own line-style combo writes: the saved content of ``CosmeticEdges``)
HAND_SHAFT = """
import FreeCAD as App, io, json, re, zipfile
doc = App.ActiveDocument
front = next(v for v in doc.Objects if hasattr(v, "CosmeticEdges") and v.Label == "View_front")
def ends(e):
    return e.Start.x, e.Start.y, e.End.x, e.End.y
wide = [e for e in front.CosmeticEdges if dict(e.Format).get("weight") == 0.35 and dict(e.Format).get("style") == 1 and abs(e.Start.x - e.End.x) < 1e-6 and abs(abs(e.Start.y - e.End.y) - 20.0) < 0.01]
thin = [e for e in front.CosmeticEdges if dict(e.Format).get("weight") == 0.18 and dict(e.Format).get("style") == 1 and abs(abs(e.Start.x - e.End.x) - 26.0) < 0.01 and e.Start.y > 0]
assert len(wide) == 1 and len(thin) == 1, (len(wide), len(thin))
front.removeCosmeticEdge(str(wide[0].Tag))
fmt = dict(thin[0].Format)
thin[0].Format = {**fmt, "weight": 0.5}
at = [i for i, e in enumerate(front.CosmeticEdges) if abs(e.Start.y - thin[0].Start.y) < 1e-6 and abs(e.End.x - thin[0].End.x) < 1e-6 and e.Start.y > 0 and dict(e.Format).get("weight") == 0.5][0]
z = zipfile.ZipFile(io.BytesIO(bytes(front.dumpPropertyContent("CosmeticEdges"))))
name = z.namelist()[0]
xml = z.read(name).decode()
blocks = list(re.finditer(r"<CosmeticEdge\s.*?</CosmeticEdge>", xml, flags=re.S))
text = re.sub(r'<LineNumber value="\d+"', '<LineNumber value="2"', blocks[at].group(0), count=1)
xml = xml[: blocks[at].start()] + text + xml[blocks[at].end():]
buf = io.BytesIO()
with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as archive:
    archive.writestr(name, xml)
front.restorePropertyContent("CosmeticEdges", bytearray(buf.getvalue()))
doc.recompute()
print("HAND done")
"""


def hand_state(snap: dict[str, Any]) -> tuple[int, list[tuple[float, int]]]:
    """(how many wide thread-end lines the front view has, ``(weight, line number)`` of its thin lines as long as the thread)."""
    front = lines(edges_of(snap, "View_front"))
    wide = [e for e in front if abs(e["weight"] - THICK_MM) <= TOLERANCE_MM and e["style"] == 1 and abs(length(e) - SHAFT_MAJOR_MM) <= TOLERANCE_MM and abs(e["geo"][1] - e["geo"][3]) <= TOLERANCE_MM]
    thin = [e for e in front if e["style"] == 1 and abs(abs(e["geo"][1] - e["geo"][3]) - SHAFT_LENGTH_MM) <= TOLERANCE_MM]
    return len(wide), sorted((e["weight"], e["number"]) for e in thin)


@pytest.mark.expected_findings("dimension_gap", "view_adds_nothing", "native_unread")
async def test_a_thread_line_the_engineer_deletes_stays_deleted_and_one_he_restyles_keeps_his_style(workspace: Path, tmp_path: Path) -> None:
    out = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    out.mkdir(parents=True, exist_ok=True)
    async with running_server(workspace, visible=True) as (cad, _):
        await SCENARIOS["shaft"](cad, workspace)
        made = hand_state(await snapshot(cad, workspace))
        assert made == (1, [(THIN_MM, CONTINUOUS), (THIN_MM, CONTINUOUS)]), made
        reply = await cad("run_native", code=HAND_SHAFT, intent="the engineer deletes the line that ends the thread and restyles one of its thin lines")
        assert not reply.is_error, reply.text
        by_hand = hand_state(await snapshot(cad, workspace))
        print("BY HAND", by_hand)
        assert by_hand == (0, [(THIN_MM, CONTINUOUS), (0.5, 2)]), by_hand
        keep(out, "th1_hand_0.png", await cad("drawing", action="capture"))

        await cad.ok("drawing", action="add", items=[{"type": "note", "id": "th1_note", "text": "Sin rebabas"}])
        assert hand_state(await snapshot(cad, workspace)) == by_hand  # an item added later makes nothing of the sheet again
        review = await cad.ok("drawing", action="review")
        print("REVIEW", review)
        assert hand_state(await snapshot(cad, workspace)) == by_hand  # nor does the review
        assert "BLOCKS EXPORT line_deleted_by_hand" not in review and "line_deleted_by_hand" in review, review  # he is told once, nothing is held
        await finish_drawing(cad)
        pdf = workspace / "shaft_hand.pdf"
        await cad.ok("export", action="pdf", path=str(pdf))
        assert hand_state(await snapshot(cad, workspace)) == by_hand  # nor does the export
        keep(out, "th1_hand_1.png", await cad("drawing", action="capture"))
        (out / "th1_shaft_hand.pdf").write_bytes(pdf.read_bytes())


# the engineer at the plate: he takes the sheet's arc off one tapped hole and draws the thread of that hole with TechDraw's own
# command (*Cosmetic thread, hole seen from the end*): the circle of the hole picked, the command makes the arc
HAND_PLATE = """
import FreeCAD as App, FreeCADGui as Gui, gui, json
doc = App.ActiveDocument
page = [o for o in doc.Objects if o.TypeId == "TechDraw::DrawPage"][0]
top = next(v for v in doc.Objects if hasattr(v, "CosmeticEdges") and v.Label == "View_top")
scale = float(top.getScale())
def radius(e):
    try:
        return e.Radius
    except Exception:
        return None
arcs = [e for e in top.CosmeticEdges if dict(e.Format).get("weight") == 0.18 and dict(e.Format).get("style") == 1 and radius(e) is not None and abs(radius(e) - 4.0) < 0.01]
assert len(arcs) == 2, len(arcs)
gone = min(arcs, key=lambda e: e.Center.x)
top.removeCosmeticEdge(str(gone.Tag))
doc.recompute()
page.ViewObject.doubleClicked()
for _ in range(10):
    gui.pump()
wanted, i, picked = None, 0, None
while True:
    try:
        edge = top.getEdgeByIndex(i)
    except Exception:
        break
    if type(edge.Curve).__name__ == "Circle" and abs(edge.Curve.Radius - 3.4 * scale) < 0.02 and (picked is None or edge.Curve.Center.x < picked[1]):
        picked = (i, edge.Curve.Center.x)
    i += 1
assert picked is not None
Gui.Selection.clearSelection()
Gui.Selection.addSelection(doc.Name, top.Name, "Edge%d" % picked[0])
Gui.runCommand("TechDraw_ExtensionThreadHoleBottom")
for _ in range(20):
    gui.pump()
doc.recompute()
print("HAND done " + json.dumps([picked[0], len(list(top.CosmeticEdges))]))
"""


def plate_arcs(snap: dict[str, Any]) -> list[tuple[float, float, int, float]]:
    """``(x of the centre, radius, line number, weight)`` of the circles of the top view that are round the tapped holes, left to right."""
    arcs = [e for e in circles(edges_of(snap, "View_top")) if abs(e["geo"][3] - PLATE_MAJOR_MM / 2) <= 0.05]
    return sorted((e["geo"][1], e["geo"][3], e["number"], e["weight"]) for e in arcs)


@pytest.mark.expected_findings("dimension_gap", "view_adds_nothing", "native_unread", "hole_not_located", "sheet_underused")
async def test_a_thread_the_engineer_draws_with_the_command_is_accepted_and_left_as_he_made_it(workspace: Path, tmp_path: Path) -> None:
    out = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    out.mkdir(parents=True, exist_ok=True)
    async with running_server(workspace, visible=True) as (cad, _):
        await plate_sheet(cad)
        made = plate_arcs(await snapshot(cad, workspace))
        assert len(made) == 2 and all(n == CONTINUOUS and abs(w - THIN_MM) <= TOLERANCE_MM for _, _, n, w in made), made
        reply = await cad("run_native", code=HAND_PLATE, intent="the engineer replaces the thread arc of one hole with TechDraw's own command")
        assert not reply.is_error, reply.text
        print("HAND", reply.text[-300:])
        by_hand = plate_arcs(await snapshot(cad, workspace))
        print("BY HAND", by_hand)
        assert len(by_hand) == 2, by_hand  # his arc stands where the sheet's was, and the other hole keeps the sheet's
        mine = [a for a in by_hand if a[3] != made[1][3] or a[2] != made[1][2]]
        assert by_hand[1] == made[1] and len(mine) == 1, (by_hand, made)  # the right hole is the sheet's, untouched
        keep(out, "th1_plate_hand_0.png", await cad("drawing", action="capture"))

        await cad.ok("drawing", action="add", items=[{"type": "note", "id": "th1_note", "text": "Sin rebabas"}])
        assert plate_arcs(await snapshot(cad, workspace)) == by_hand  # the sheet makes no arc again, and does not restyle his
        review = await cad.ok("drawing", action="review")
        print("REVIEW", review)
        assert "thread_no_representation" not in review, review  # a thread he drew counts as drawn
        assert plate_arcs(await snapshot(cad, workspace)) == by_hand
        await finish_drawing(cad)
        pdf = workspace / "plate_hand.pdf"
        await cad.ok("export", action="pdf", path=str(pdf))
        assert plate_arcs(await snapshot(cad, workspace)) == by_hand
        keep(out, "th1_plate_hand_1.png", await cad("drawing", action="capture"))
        (out / "th1_plate_hand.pdf").write_bytes(pdf.read_bytes())
