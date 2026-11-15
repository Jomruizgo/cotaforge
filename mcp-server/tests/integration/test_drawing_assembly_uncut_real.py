# ruff: noqa: E501
"""In a section of an assembly the shafts, bolts, keys and pins stay whole, on the real FreeCAD (Windows, VISIBLE).

The bench assembly has made-up dimensions: a shaft on two ball bearings in a housing, a key, a hub (a pulley blank) and a cover
bolted with four screws. Its axial section keeps the shaft, the key and the screws whole (TechDraw's own objects: a view of the
whole parts laid over the section of the cut ones, bound to it by expression) and hatches the housing, the cover, the hub and
the bearings, the touching ones at different angles (one TechDraw section per hatch, all from the same plane).

The bearings are the envelope block d x D x B, cut and hatched as one body (the general simplified practice of ISO 8826-1 without
its cross symbol): no rings and no balls are modelled.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the images of the sheets.
"""

import base64
import json
import os
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.integration.conftest import Cad, Reply, pending_points, running_server

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

XY = {"kind": "plane", "name": "XY"}
XZ = {"kind": "plane", "name": "XZ"}


def shot(shots: Path, name: str, reply: Reply) -> None:
    assert reply.images, reply.text
    shots.mkdir(parents=True, exist_ok=True)
    (shots / f"{name}.png").write_bytes(base64.b64decode(reply.images[0].data))


def shots_dir(tmp_path: Path) -> Path:
    return Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")


async def revolved(cad: Cad, name: str, title: str, material: str, profile: list[tuple[float, float]]) -> None:
    """A part of revolution about the Z axis: ``profile`` is the closed outline (radius, height) of its half section."""
    await cad.ok("doc", action="new", name=name)
    await cad.ok("doc", action="set_material", material=material)
    await cad.ok("doc", action="set_title", name=title)
    flipped = [(r, -z) for r, z in profile]  # the XZ plane of a sketch looks along -Y: its v axis points down -Z
    pairs = zip(flipped, flipped[1:] + flipped[:1], strict=True)
    lines = [{"kind": "line", "name": f"l{i}", "start": list(a), "end": list(b)} for i, (a, b) in enumerate(pairs)]
    await cad.ok("op", name="sketch", id="profile", params={"on": XZ, "entities": lines})
    await cad.ok("op", name="revolve", id="body", params={"sketch": "profile", "axis": "v"})


async def build_bench(cad: Cad, titles: bool = True) -> None:
    """Shaft Ø20 on two bearings 20 x 47 x 14 in a housing Ø90, a 6 x 6 key and a hub Ø56 x 30 on its end, a cover bolted with four M6 x 25.
    Without ``titles`` the parts carry no designation: their file names are all there is to tell what they are."""

    def name(title: str, fallback: str) -> str:
        return title if titles else fallback

    await revolved(cad, "shaft", name("Eje", "part-a"), "C45", [(0, 6), (10, 6), (10, 24), (12.5, 24), (12.5, 86), (10, 86), (10, 156), (0, 156)])
    await cad.ok("op", name="ref_plane", id="kw_plane", params={"define": {"how": "offset", "base": {"kind": "plane", "name": "YZ"}, "distance_mm": 10}})
    slot = [{"kind": "slot", "name": "kw", "start": [0, 123], "end": [0, 146], "width": 6}]
    await cad.ok("op", name="sketch", id="kw_sketch", params={"on": {"kind": "plane", "from_op": "kw_plane"}, "entities": slot})
    await cad.ok("op", name="cut", id="keyway", params={"sketch": "kw_sketch", "end_condition": "blind", "depth_mm": 3.5})
    await cad.ok("doc", action="save", path="shaft.FCStd")

    await revolved(cad, "housing", name("Carcasa", "part-b"), "S355J2", [(13, 0), (45, 0), (45, 100), (23.5, 100), (23.5, 86), (20, 86), (20, 24), (23.5, 24), (23.5, 10), (13, 10)])
    top = {"kind": "face", "from_op": "body", "select": {"normal": [0, 0, 1], "center": [0, 0, 100]}}
    holes = {"face": top, "bolt_circle": {"diameter_mm": 68, "count": 4}, "standard": "simple", "diameter_mm": 6, "end_condition": "blind", "depth_mm": 15, "drill_point": "flat"}
    await cad.ok("op", name="hole", id="tapped", params=holes)
    await cad.ok("doc", action="save", path="housing.FCStd")

    await revolved(cad, "bearing", name("Rodamiento 6204", "part-c"), "GCr15", [(10, 0), (23.5, 0), (23.5, 14), (10, 14)])
    await cad.ok("doc", action="save", path="bearing.FCStd")

    await revolved(cad, "cover", name("Tapa", "part-d"), "S355J2", [(12, 0), (45, 0), (45, 10), (12, 10)])
    top = {"kind": "face", "from_op": "body", "select": {"normal": [0, 0, 1], "center": [0, 0, 10]}}
    holes = {"face": top, "bolt_circle": {"diameter_mm": 68, "count": 4}, "standard": "simple", "diameter_mm": 6.6, "end_condition": "through_all"}
    await cad.ok("op", name="hole", id="clearance", params=holes)
    await cad.ok("doc", action="save", path="cover.FCStd")

    await revolved(cad, "hub", name("Cubo", "part-e"), "GG25", [(10, 0), (28, 0), (28, 30), (10, 30)])
    top = {"kind": "face", "from_op": "body", "select": {"normal": [0, 0, 1], "center": [0, 0, 30]}}
    await cad.ok("op", name="sketch", id="kw", params={"on": top, "entities": [{"kind": "rect", "name": "kw", "center": [10.9, 0], "width": 3.8, "height": 6}]})
    await cad.ok("op", name="cut", id="hub_keyway", params={"sketch": "kw", "end_condition": "through_all"})
    await cad.ok("doc", action="save", path="hub.FCStd")

    await cad.ok("doc", action="new", name="key")
    await cad.ok("doc", action="set_material", material="C45")
    await cad.ok("doc", action="set_title", name=name("Chaveta 6x6x17", "part-f"))
    await cad.ok("op", name="sketch", id="k", params={"on": XY, "entities": [{"kind": "rect", "name": "k", "center": [0, 0], "width": 6, "height": 6}]})
    await cad.ok("op", name="extrude", id="block", params={"sketch": "k", "depth_mm": 17})
    await cad.ok("doc", action="save", path="key.FCStd")

    await revolved(cad, "screw", name("Tornillo M6x25", "part-g"), "8.8", [(0, -25), (3, -25), (3, 0), (5, 0), (5, 6), (0, 6)])
    await cad.ok("doc", action="save", path="screw.FCStd")

    await cad.ok("doc", action="new", name="bench-assembly", kind="assembly")
    put = {"housing": [0, 0, 0], "shaft": [0, 0, 0], "bearing_low": [0, 0, 10], "bearing_top": [0, 0, 86], "cover": [0, 0, 100], "hub": [0, 0, 120], "key": [9.5, 0, 126]}
    parts = {"bearing_low": "bearing", "bearing_top": "bearing"}
    for comp, origin in put.items():
        await cad.ok("op", name="insert_component", id=comp, params={"part": parts.get(comp, comp), "placement": {"origin": origin}})
    for n, (x, y) in enumerate(((34, 0), (0, 34), (-34, 0), (0, -34)), start=1):
        await cad.ok("op", name="insert_component", id=f"screw_{n}", params={"part": "screw", "placement": {"origin": [x, y, 110]}})
    await cad.ok("op", name="fix_component", id="housing_fix", params={"component": "housing"})


SECTION = {"type": "section", "id": "secA", "at": [0, 0, 0], "normal": [0, 1, 0], "view": "top", "letter": "A"}

# what the sheet made, read back from the live FreeCAD document by a script that runs in it (``run_native``): one row per object of the section
STATE_SCRIPT = '''
import json
import FreeCAD as App
import sheet_native

doc = App.ActiveDocument
page = next(o for o in doc.Objects if o.TypeId == "TechDraw::DrawPage")


def page_edges(view):
    """The edges the part is drawn with (the sheet's own centre lines left out), in page mm: TechDraw keeps them scaled and y down."""
    rows = []
    for e in sheet_native.model_edges(view):
        b = e.BoundBox
        rows.append([float(view.X) + b.XMin, float(view.Y) - b.YMax, float(view.X) + b.XMax, float(view.Y) - b.YMin])
    return rows


views = []
for o in page.Views:
    if "CotaforgeDerived" not in o.PropertiesList:
        continue
    rows = page_edges(o)
    row = {
        "name": o.Name,
        "label": o.Label,
        "type": o.TypeId,
        "role": str(getattr(o, "CotaforgeRole", "")),
        "visible": bool(o.Visibility),
        "x": float(o.X),
        "y": float(o.Y),
        "expr": {p: e for p, e in o.ExpressionEngine},
        "scale": float(o.getScale()),
        "source": [s.Name for s in o.Source],
        "base": o.BaseView.Name if getattr(o, "BaseView", None) is not None else None,
        "box": [min(r[0] for r in rows), min(r[1] for r in rows), max(r[2] for r in rows), max(r[3] for r in rows)] if rows else None,
        "caption": str(getattr(o, "Caption", "")),
        "rows": rows,
    }
    if o.TypeId == "TechDraw::DrawViewSection":
        row["hatch_rotation"] = float(o.HatchRotation)
        row["hatch_scale"] = float(o.HatchScale)
        row["surface"] = str(o.CutSurfaceDisplay)
    views.append(row)
json.dump(views, open(OUT, "w"))
'''


async def native_state(cad: Cad, workspace: Path) -> list[dict[str, Any]]:
    out = workspace / "native_state.json"
    out.unlink(missing_ok=True)
    reply = await cad("run_native", code=f"OUT = {str(out)!r}\n" + STATE_SCRIPT, intent="read the views of the section back")
    assert not reply.is_error, reply.text
    return json.loads(out.read_text(encoding="utf-8"))


def pieces(state: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {"section": [], "whole": [], "base": []}
    for row in state:
        out[row["role"].partition(":")[2] or "other"].append(row)
    return out


def show_through(state: list[dict[str, Any]]) -> list[tuple[float, float, float, float]]:
    """Lines of the cut pieces that stand INSIDE the outline of the whole shaft (where the cut bodies leave the bore empty, the
    far half of their bore is seen through it): ``(x0, y0, x1, y1)`` in mm from the shaft's axis."""
    found = pieces(state)
    whole = found["whole"][0]
    axis = (whole["box"][0] + whole["box"][2]) / 2
    scale = whole["scale"]
    band = 10.0 * scale - 0.2
    out = []
    for row in found["section"]:
        for x0, y0, x1, y1 in row["rows"]:
            if abs(x0 - axis) < band and abs(x1 - axis) < band:
                out.append((round((x0 - axis) / scale, 2), round(y0, 2), round((x1 - axis) / scale, 2), round(y1, 2)))
    return out


async def look(cad: Cad, shots: Path, name: str, region: list[float] | None = None) -> None:
    arguments = {"region": region} if region is not None else {}
    shot(shots, name, await cad("drawing", action="capture", **arguments))


async def test_the_bench_section_keeps_the_shaft_the_key_and_the_screws_whole(workspace: Path, tmp_path: Path) -> None:
    shots = shots_dir(tmp_path)
    async with running_server(workspace, visible=True) as (cad, _):
        await build_bench(cad)
        await cad.ok("drawing", action="create", template="A3", angle="first", views=["top", "iso"], title="Reductor de banco")
        added = await cad.ok("drawing", action="add", items=[SECTION])
        print("ADD", added)
        assert "whole, not cut" in added and "Eje" in added and "Chaveta" in added and "Tornillo" in added, added
        assert "Carcasa" not in added.split("whole, not cut")[1].split(";")[0], added  # the housing is cut
        review = await cad.ok("drawing", action="review")
        findings, _, _ = await pending_points(cad)
        print("FINDINGS", findings)
        assert not any(f.startswith(("native_fallback:secA", "assembly_solid_cut", "assembly_whole_guess")) for f in findings), review
        state = await native_state(cad, workspace)
        found = pieces(state)
        print("OBJECTS", [(r["label"], r["role"], r["source"], r.get("hatch_rotation"), r.get("hatch_scale"), r["expr"], r["visible"]) for r in state])
        shafts = {"Component001", "Component006", "Component007", "Component009"}  # the shaft, the key and the two screws the plane passes through
        assert len(found["whole"]) == 1 and set(found["whole"][0]["source"]) == shafts, found["whole"]
        assert len(found["section"]) == len(found["base"]) >= 2, state  # one section per hatch, each over a hidden base of its own bodies
        assert all(not r["visible"] for r in found["base"]) and all(r["visible"] for r in found["section"]), state
        cut = [set(r["source"]) for r in found["section"]]
        assert all(not (c & shafts) for c in cut), cut  # none of the whole parts is in a cut piece
        assert len({(r["hatch_rotation"], r["hatch_scale"]) for r in found["section"]}) == len(found["section"]), state  # each its own hatch
        primary = found["section"][0]
        assert primary["label"] == "A-A" and primary["caption"].strip() == "A - A", primary
        for other in [*found["section"][1:], *found["whole"]]:
            assert other["expr"].get("X", "").startswith(primary["name"]) and other["expr"].get("Y", "").startswith(primary["name"]), other  # bound to the first piece
        print("SHOW-THROUGH", len(show_through(state)), show_through(state))
        await look(cad, shots, "bench_section")
        await look(cad, shots, "bench_section_zoom", [140, 85, 215, 185])
        await look(cad, shots, "bench_section_bearing", [150, 85, 205, 115])
        for line in review.splitlines():
            if "centre_line_gap" in line or "assembly_" in line:
                print("REVIEW LINE", line[:400])


async def bench_sheet(cad: Cad, workspace: Path, titles: bool = True) -> str:
    await build_bench(cad, titles)
    await cad.ok("drawing", action="create", template="A3", angle="first", views=["top", "iso"], title="Reductor de banco")
    return await cad.ok("drawing", action="add", items=[SECTION])


MOVE_SCRIPT = '''
import FreeCAD as App

doc = App.ActiveDocument
section = doc.getObject(NAME)
section.setExpression("X", None)  # the engineer takes the section off its parent and moves it by hand
section.setExpression("Y", None)
section.X = float(section.X) + 15.0
section.Y = float(section.Y) - 8.0
doc.recompute()
'''


async def test_moving_the_section_by_hand_moves_the_whole_parts_with_it(workspace: Path, tmp_path: Path) -> None:
    shots = shots_dir(tmp_path)
    async with running_server(workspace, visible=True) as (cad, _):
        await bench_sheet(cad, workspace)
        before = pieces(await native_state(cad, workspace))
        primary = before["section"][0]
        reply = await cad("run_native", code=f"NAME = {primary['name']!r}\n" + MOVE_SCRIPT, intent="move the section by hand")
        assert not reply.is_error, reply.text
        after = pieces(await native_state(cad, workspace))
        moved = {r["label"]: r for r in [*after["section"], *after["whole"]]}
        for row in [*before["section"], *before["whole"]]:
            now = moved[row["label"]]
            print("MOVED", row["label"], [round(v, 2) for v in row["box"]], [round(v, 2) for v in now["box"]])
            assert abs(now["box"][0] - row["box"][0] - 15.0) < 0.05 and abs(now["box"][1] - row["box"][1] + 8.0) < 0.05, (row["label"], row["box"], now["box"])
        assert before["whole"] and all(r["expr"].get("X", "").startswith(primary["name"]) for r in after["whole"]), after["whole"]
        shot(shots, "bench_section_moved", await cad("drawing", action="capture"))


async def test_a_cut_that_would_hatch_a_shaft_is_blocked_and_the_right_sheet_is_quiet(workspace: Path, tmp_path: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        await bench_sheet(cad, workspace)
        findings, _, _ = await pending_points(cad)
        assert not any(f.startswith("assembly_solid_cut") for f in findings), findings  # the correct sheet: quiet
        await cad.ok("drawing", action="update", items=[{"id": "secA", "whole": []}])  # a cut of every body, the shaft included
        findings, _, _ = await pending_points(cad)
        assert "assembly_solid_cut:A-A" in findings, findings
        print("FORCED FINDINGS", findings)
        review = await cad.ok("drawing", action="review")
        line = next(row for row in review.splitlines() if "assembly_solid_cut" in row)
        print("FORCED", line)
        assert "Eje (shaft)" in line and "Chaveta 6x6x17 (key)" in line and "Tornillo M6x25 (screw)" in line and "Carcasa" not in line, line
        refused = await cad.err("export", action="pdf", path="forced.pdf")
        assert "assembly_solid_cut" in refused, refused  # it holds the export
        await cad.ok("drawing", action="update", items=[{"id": "secA", "whole": ["Eje"]}])  # only the shaft whole: the key and the screws are cut
        review = await cad.ok("drawing", action="review")
        line = next(row for row in review.splitlines() if "assembly_solid_cut" in row)
        assert "Eje (shaft)" not in line and "Chaveta 6x6x17 (key)" in line, line
        await cad.ok("drawing", action="update", items=[{"id": "secA", "whole": ["Eje", "Chaveta 6x6x17", "screw_1", "screw_3"]}])
        findings, _, _ = await pending_points(cad)
        assert not any(f.startswith("assembly_solid_cut") for f in findings), findings  # named by designation or by component id
        refused = await cad.err("drawing", action="update", items=[{"id": "secA", "whole": ["no-such-part"]}])
        assert "no component called no-such-part" in refused, refused


async def test_a_part_held_whole_by_its_shape_alone_is_a_question_to_the_engineer(workspace: Path, tmp_path: Path) -> None:
    shots = shots_dir(tmp_path)
    async with running_server(workspace, visible=True) as (cad, _):
        await revolved(cad, "part-1", "Pieza 1", "C45", [(0, 0), (10, 0), (10, 100), (0, 100)])  # a long solid of revolution: nothing says it is a shaft
        await cad.ok("doc", action="save", path="part-1.FCStd")
        await revolved(cad, "part-2", "Pieza 2", "S355J2", [(10, 20), (20, 20), (20, 80), (10, 80)])  # a sleeve around it
        await cad.ok("doc", action="save", path="part-2.FCStd")
        await cad.ok("doc", action="new", name="pair", kind="assembly")
        for comp in ("part-1", "part-2"):
            await cad.ok("op", name="insert_component", id=comp.replace("-", "_"), params={"part": comp, "placement": {"origin": [0, 0, 0]}})
        await cad.ok("drawing", action="create", template="A4", angle="first", views=["top", "iso"], title="Pareja")
        added = await cad.ok("drawing", action="add", items=[SECTION])
        print("ADD", added)
        assert "whole, not cut" in added and "Pieza 1" in added and "Pieza 2" not in added.split("whole, not cut")[1].split(";")[0], added
        findings, _, _ = await pending_points(cad)
        assert "assembly_whole_guess:A-A" in findings, findings
        review = await cad.ok("drawing", action="review")
        line = next(row for row in review.splitlines() if "assembly_whole_guess" in row)
        assert "Pieza 1 (shaft)" in line and "whole=[...]" in line, line
        refused = await cad.err("export", action="pdf", path="guess.pdf")
        assert "assembly_whole_guess" in refused, refused  # asked before it is exported
        await cad.ok("drawing", action="update", items=[{"id": "secA", "whole": []}])  # the engineer says it is not a shaft: cut and hatched
        findings, _, _ = await pending_points(cad)
        assert not any(f.startswith("assembly_whole_guess") for f in findings), findings
        shot(shots, "pair_cut_by_order", await cad("drawing", action="capture"))


async def test_the_clamp_assembly_has_no_solid_part_to_leave_whole(workspace: Path, tmp_path: Path) -> None:
    """Two plates held apart by two spacer sleeves (its bolts are not modelled): everything is cut and the touching bodies get hatches of their own."""
    from tests.integration.test_drawing_review_gaps_real import build_assembly  # noqa: PLC0415

    shots = shots_dir(tmp_path)
    async with running_server(workspace, visible=True) as (cad, _):
        await build_assembly(cad)
        await cad.ok("drawing", action="create", template="A3", angle="first", views=["front", "top", "iso"], title="Conjunto")
        added = await cad.ok("drawing", action="add", items=[{"type": "section", "id": "secB", "at": [0, 0, 18], "normal": [0, 1, 0], "view": "top", "letter": "B"}])
        print("ADD", added)
        assert "whole, not cut" not in added, added
        state = await native_state(cad, workspace)
        found = pieces(state)
        print("CLAMP OBJECTS", [(r["label"], r["role"], r["source"], r.get("hatch_rotation"), r.get("hatch_scale"), r["visible"]) for r in state])
        findings, _, _ = await pending_points(cad)
        assert not any(f.startswith(("assembly_solid_cut", "assembly_whole_guess", "native_fallback:secB")) for f in findings), findings
        shot(shots, "clamp_section", await cad("drawing", action="capture"))
        assert not found["whole"] and found["section"], state
