# ruff: noqa: E501
"""The size of each process stage and the revision of a released sheet, on the real FreeCAD (Windows, VISIBLE), in both ways of
building a sheet (item by item, and in one order). Parts and numbers are made up.

* A ring whose diameter and thickness are turned with an allowance and then ground: the intent file declares the route per feature; the export waits until every stage
  of it has its dimension, labelled with the process (native TechDraw dimensions), the intermediate ones secondary.
* A plate whose hole grows after its first export: the second export puts the revision row in the title block's own table
  (letter, previous value, requester, date), FreeCAD's revision triangle beside the changed dimension and the revision field.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the images of the sheets.
"""

import base64
import os
import re
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import Cad, finish_drawing, running_server
from tests.integration.test_drawing_compose_real import compose_the_rest, in_one_order
from tests.integration.test_drawing_requirements_real import TOP, make_plate

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

RING_INTENT = """# Intención: anillo

## Proceso
- Diámetro exterior: torneado Ø60,3 -> rectificado
- Espesor: torneado 12,3 -> rectificado
"""
OUTER, THICK = "face:outer", "face:thick"  # replaced by the ids FreeCAD gave


async def make_ring(cad: Cad) -> str:
    """A ring Ø60 x 12 with a Ø30 bore; returns the id of its outer cylinder."""
    await cad.ok("doc", action="new", name="anillo")
    await cad.ok("doc", action="set_material", material="AISI 1045")
    await cad.ok("op", name="cylinder", id="base", params={"diameter_mm": 60, "height_mm": 12, "axis": "Z"})
    bore = {"face": TOP, "at": [0, 0, 12], "standard": "simple", "diameter_mm": 30, "end_condition": "through_all"}
    await cad.ok("op", name="hole", id="bore", params=bore)
    faces = await cad.ok("inspect", action="faces", surface="cylinder")
    print("CYLINDERS", faces)
    return next(m.group(0) for line in faces.splitlines() if "r=30 " in line and (m := re.search(r"face:\d+", line)))


def stage_items(outer: str) -> list[dict[str, object]]:
    between = {"from": {"at": [20, 0, 0]}, "to": {"at": [20, 0, 12]}}
    return [
        {"type": "dim", "id": "ds1", "kind": "diameter", "face": [outer], "stage": "torneado", "size_mm": 60.3, "view": "A-A"},
        {"type": "dim", "id": "ds2", "kind": "diameter", "face": [outer], "stage": "rectificado", "fit": "g6", "show": "symbol", "view": "A-A"},
        {"type": "dim", "id": "es1", "kind": "distance", **between, "stage": "torneado", "size_mm": 12.3, "view": "A-A"},
        {"type": "dim", "id": "es2", "kind": "distance", **between, "stage": "rectificado", "tol": [0.0, -0.02], "view": "A-A"},
    ]


async def shot(cad: Cad, shots: Path, name: str) -> None:
    reply = await cad("drawing", action="capture")
    assert reply.images, reply.text
    shots.mkdir(parents=True, exist_ok=True)
    (shots / f"{name}.png").write_bytes(base64.b64decode(reply.images[0].data))


def shots_dir(tmp_path: Path) -> Path:
    return Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")


async def test_a_declared_route_holds_the_export_until_each_stage_has_its_dimension_item_by_item(workspace: Path, tmp_path: Path) -> None:
    shots = shots_dir(tmp_path)
    async with running_server(workspace, visible=True) as (cad, _):
        outer = await make_ring(cad)
        (workspace / "drawings").mkdir(exist_ok=True)
        (workspace / "drawings" / "anillo-intent.md").write_text(RING_INTENT, encoding="utf-8")
        print("CREATED", await cad.ok("drawing", action="create", title="Anillo"))
        review = await cad.ok("drawing", action="review")
        print("REVIEW", review)
        assert "req.process_stages" in review and "stage «torneado» has no dimension" in review, review
        items = stage_items(outer)
        added = await cad.ok("drawing", action="add", items=items[:2])
        print("ADDED", added)
        assert "process stage torneado" in added, added
        partial = await cad.ok("drawing", action="review")
        assert "route «Espesor»" in partial and "route «Diámetro exterior»" not in partial, partial  # the first route is complete, the second is not
        print("ADDED", await cad.ok("drawing", action="add", items=items[2:]))
        done = await cad.ok("drawing", action="review")
        print("REVIEW2", done)
        assert "stage «" not in done, done  # every stage of the route has its dimension: the row is met
        await shot(cad, shots, "ring_stages_step")
        state = await cad.ok("drawing", action="read")
        print("READ", state)


async def test_a_declared_route_in_the_sheet_built_in_one_order(workspace: Path, tmp_path: Path) -> None:
    shots = shots_dir(tmp_path)
    async with running_server(workspace, visible=True) as (cad, _):
        await in_one_order(cad)
        outer = await make_ring(cad)
        (workspace / "drawings").mkdir(exist_ok=True)
        (workspace / "drawings" / "anillo-intent.md").write_text(RING_INTENT, encoding="utf-8")
        plan = await cad.ok("drawing", action="plan", title="Anillo")
        print("PLAN", plan)
        assert "req.process_stages" in plan, plan  # the plan asks for the sizes of the stages of the declared route
        composed = await compose_the_rest(cad, {"relations": [{k: v for k, v in i.items() if k != "id"} for i in stage_items(outer)], "sheet": {"title": "Anillo"}}, keep=("req.process_stages",))
        print("COMPOSED", composed)
        assert "composed sheet" in composed, composed
        assert "stage «" not in await cad.ok("drawing", action="review")
        await shot(cad, shots, "ring_stages_one_order")
        print("READ", await cad.ok("drawing", action="read"))
        print("PROBE", await cad.ok("run_native", code=PROBE, intent="read the dimensions of the page, nothing is changed"))


async def release_then_change(cad: Cad, workspace: Path, shots: Path, tag: str) -> None:
    """The plate is exported, a hole grows (the engineer asks for it), and the export again puts the revision on the sheet."""
    cad.looks = False  # the new row is a change somebody must look at: the test looks itself, after it is put
    cad.allow("view_adds_nothing")  # the plate sheet in A3 shows a side view the test is not about
    entry = "machining-turned-end-chamfer"  # a measured alert of the plate the engineer, played by the test, orders kept
    await cad.ok("memory", action="get", id=entry)
    await cad.ok("memory", action="answer", id=entry, by="Ana", text="no aplica: " + cad.says("esta prueba trata de la revisión de la hoja, no del borde del agujero"))
    await finish_drawing(cad)
    await shot(cad, shots, f"{tag}_0_first_issue")
    await cad("drawing", action="capture")
    first = await cad.ok("export", action="pdf", path="placa.pdf")
    print("FIRST", first)
    assert "revision" not in first.lower().split("written")[-1], first  # a first issue carries no row
    read = await cad.ok("drawing", action="read")
    assert "revision" not in read.lower().replace("revision_index", ""), read
    await cad.ok("op", edit="h1", params={"diameter_mm": 9})  # the hole grows after the sheet went out
    await finish_drawing(cad)  # what the changed hole opens on the sheet (its position, its callout) is closed as before
    refused = await cad.err("export", action="pdf", path="placa.pdf", overwrite=True, revision_by="Cliente Norte", revision_reason="agujero 1 mayor")
    print("REFUSED", refused)
    assert "sheet_not_looked_at" in refused and "revision A recorded" in refused, refused
    await shot(cad, shots, f"{tag}_1_revision_A")
    second = await cad.ok("export", action="pdf", path="placa.pdf", overwrite=True, revision_by="Cliente Norte", revision_reason="agujero 1 mayor")
    print("SECOND", second)
    read = await cad.ok("drawing", action="read")
    print("READ", read)
    print("LIST", await cad.ok("drawing", action="list"))
    assert "Ø6,6 a Ø9" in read and "Cliente" in read, read  # the previous and the new value, and who asked
    marks = await cad.ok("run_native", code=SYMBOLS, intent="read the revision markers of the page, nothing is changed")
    print("SYMBOLS", marks)
    assert "SYM" in marks and "Revision_Triangle" in marks and "['A']" in marks, marks  # FreeCAD's own triangle with the letter
    await cad.ok("op", edit="h1", params={"diameter_mm": 11})
    await finish_drawing(cad)
    await cad.err("export", action="pdf", path="placa.pdf", overwrite=True)
    await shot(cad, shots, f"{tag}_2_revision_B")
    await cad.ok("export", action="pdf", path="placa.pdf", overwrite=True)
    again = await cad.ok("drawing", action="read")
    assert "Ø9 a Ø11" in again and "Ø6,6 a Ø9" in again, again  # A is still there with its previous value, B added after it


async def test_a_hole_changed_after_the_first_export_is_a_revision_item_by_item(workspace: Path, tmp_path: Path) -> None:
    shots = shots_dir(tmp_path)
    async with running_server(workspace, visible=True) as (cad, _):
        await make_plate(cad)
        await cad.ok("memory", action="brief", drawing_review="propio", text=cad.says("esta prueba trata de otra cosa"))
        print("CREATED", await cad.ok("drawing", action="create", title="Placa"))
        await cad.ok("drawing", action="add", items=[{"type": "hole_dim", "id": "hd", "all": True}])
        await release_then_change(cad, workspace, shots, "plate_step")


async def test_a_hole_changed_after_the_first_export_is_a_revision_in_one_order(workspace: Path, tmp_path: Path) -> None:
    shots = shots_dir(tmp_path)
    async with running_server(workspace, visible=True) as (cad, _):
        await in_one_order(cad)
        await make_plate(cad)
        composed = await compose_the_rest(cad, {"sheet": {"title": "Placa", "symmetry": True}})
        print("COMPOSED", composed)
        await release_then_change(cad, workspace, shots, "plate_one_order")


SYMBOLS = """
for o in doc.Objects:
    if o.TypeId == 'TechDraw::DrawViewSymbol':
        print('SYM', o.Label, '|', getattr(o, 'CotaforgeLibrary', ''), '|', list(o.EditableTexts), '|', round(float(o.X), 1), round(float(o.Y), 1), '|', round(float(o.Scale), 2), '|', o.Visibility)
"""

PROBE = """
for o in doc.Objects:
    if o.TypeId == 'TechDraw::DrawViewDimension':
        vo = o.ViewObject
        print('DIM', o.Label, '|', str(o.FormatSpec), '|', [round(c, 2) for c in vo.Color], '|', str(vo.LineWidth), '|', o.Arbitrary, '|', round(float(o.X), 1), round(float(o.Y), 1), vo.Visibility)
"""


async def test_the_intermediate_stages_are_native_dimensions_drawn_thin_and_grey(workspace: Path, tmp_path: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        outer = await make_ring(cad)
        (workspace / "drawings").mkdir(exist_ok=True)
        (workspace / "drawings" / "anillo-intent.md").write_text(RING_INTENT, encoding="utf-8")
        await cad.ok("drawing", action="create", title="Anillo")
        print("ADDED", await cad.ok("drawing", action="add", items=stage_items(outer)))
        print("READ", await cad.ok("drawing", action="read"))
        probe = await cad.ok("run_native", code=PROBE, intent="read the style of the stage dimensions, nothing is changed")
        print("PROBE", probe)
        rows = {m.group(1): m for m in re.finditer(r"DIM (\w+) d1 \| (.*?) \| \[(.*?)\] \| (.*?) \| (\w+)", probe)}
        assert {"ds1", "ds2", "es1", "es2"} <= set(rows), probe
        for item in ("ds1", "es1"):  # the sizes of the stages before the last one
            assert rows[item].group(3) == "0.45, 0.45, 0.45, 1.0", probe
        for item in ("ds2", "es2"):  # the final size stands out: the colour of the sheet
            assert rows[item].group(3) != "0.45, 0.45, 0.45, 1.0", probe
        assert all(rows[i].group(5) == "True" for i in rows), probe  # the label is the dimension's own format text, editable in FreeCAD
