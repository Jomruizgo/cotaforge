"""A customer's STEP/IGES part on the real FreeCAD through the real server: the files are made in the test by exporting
known solids from FreeCAD itself, then imported (coordinates and units kept, one body per solid), measured, searched for
holes, inserted in an assembly and kept as a design in progress (hole, boss, fillet on top of the imported solid)."""

import math
import re
import textwrap
import time
from pathlib import Path

import pytest

from tests.integration.conftest import Cad, run_freecad_script, running_server, volume


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COTAFORGE_HOME", str(tmp_path / "home"))


PREAMBLE = """
import FreeCAD as App, Part, Import
V = App.Vector

def export(shape, path, label="part"):
    doc = App.newDocument("maker")
    obj = doc.addObject("Part::Feature", label)
    obj.Shape = shape
    doc.recompute()
    Import.export([obj], path)
    App.closeDocument(doc.Name)

def as_bspline(shape):
    # what some exporters write and transformGeometry makes: every surface a B-spline (moved to a far corner)
    move = App.Matrix()
    move.move(V(1000, 2000, 300))
    moved = shape.transformGeometry(move)
    kinds = {type(f.Surface).__name__ for f in moved.Faces}
    assert not kinds & {"Cylinder", "Plane"}, kinds
    return moved
"""

PLATE_VOLUME = 100 * 60 * 8
TOP = {"kind": "face", "from_op": "block", "select": {"normal": [0, 0, 1]}}


def make_files(directory: Path, body: str) -> None:
    script = directory / "make_files.py"
    script.write_text(PREAMBLE + textwrap.dedent(body), encoding="utf-8")
    run_freecad_script(script)


def bbox_of(text: str) -> tuple[list[float], list[float]]:
    match = re.search(r"bbox: size \(.*?\) mm, min \((.*?)\), max \((.*?)\)", text)
    assert match, text
    return [float(v) for v in match.group(1).split(",")], [float(v) for v in match.group(2).split(",")]


async def test_a_step_part_is_imported_where_the_file_puts_it_and_its_holes_are_listed(workspace: Path) -> None:
    supplier = workspace / "supplier"
    supplier.mkdir()
    make_files(
        supplier,
        f"""
        plate = Part.makeBox(100, 60, 8, V(1000, 2000, 300))
        plate = plate.cut(Part.makeCylinder(3, 20, V(1020, 2030, 290)))   # D6 through
        plate = plate.cut(Part.makeCylinder(5, 3, V(1070, 2030, 305)))    # D10 blind, 3 deep from the top
        plate = plate.fuse(Part.makeCylinder(4, 10, V(1050, 2010, 308)))  # a D8 pin standing on top: not a hole
        export(plate.removeSplitter(), r"{supplier / "bracket.step"}", "bracket")
        """,
    )
    expected = PLATE_VOLUME - math.pi * 9 * 8 - math.pi * 25 * 3 + math.pi * 16 * 10
    async with running_server(workspace) as (cad, _):
        text = await cad.ok("doc", action="import", path=str(supplier / "bracket.step"), name="bracket")

        assert text.startswith("document: bracket (part) from bracket.step"), text
        assert "units: the file declares mm" in text and "no recentring" in text
        assert "content: 1 solids" in text and "valid, closed" in text
        assert volume(text) == pytest.approx(expected, rel=1e-6)
        low, high = bbox_of(text)
        assert low == pytest.approx([1000, 2000, 300]) and high == pytest.approx([1100, 2060, 318])
        assert "no feature history" in text and "op(edit)" in text
        assert (workspace / "bracket.FCStd").is_file() and (workspace / "bracket.cotaforge.json").is_file()

        holes = await cad.ok("inspect", action="holes")
        lines = holes.splitlines()
        assert lines[0].startswith("holes: 2 in 1 body; by diameter (mm): 1 x D6 (1 through), 1 x D10"), holes
        assert lines[1].startswith("D6 through depth 8") and "axis from (1020, 2030, 300)" in lines[1], holes
        assert lines[2].startswith("D10 blind depth 3") and "axis from (1070, 2030, 308) along (0, 0, -1)" in lines[2]
        face_ids = re.search(r"faces (face:\d+(?:,face:\d+)*)", lines[1])
        assert face_ids is not None
        shown = await cad.ok("inspect", action="face", id=face_ids.group(1).split(",")[0])
        assert "cylinder" in shown and "r=3" in shown

        # the file is a part document an assembly can link to, at the coordinates of the file
        await cad.ok("doc", action="new", name="cell", kind="assembly")
        await cad.ok("op", name="insert_component", id="bracket_i", params={"part": "bracket", "placement": {}})
        measured = await cad.ok("inspect", action="measure")
        low, high = bbox_of(measured)
        assert low == pytest.approx([1000, 2000, 300]) and high == pytest.approx([1100, 2060, 318])


async def test_a_step_assembly_gives_one_body_per_solid_with_its_placements_folded_in(workspace: Path) -> None:
    supplier = workspace / "supplier"
    supplier.mkdir()
    make_files(
        supplier,
        f"""
        doc = App.newDocument("asm")
        group = doc.addObject("App::Part", "Fixture")
        a = doc.addObject("Part::Box", "base"); a.Length = 40; a.Width = 40; a.Height = 10
        b = doc.addObject("Part::Box", "arm"); b.Length = 10; b.Width = 10; b.Height = 30
        a.Placement = App.Placement(V(500, 600, 700), App.Rotation())  # the exporter keeps the placement of each
        b.Placement = App.Placement(V(505, 605, 710), App.Rotation())  # product, but not that of its container
        group.addObject(a); group.addObject(b)
        doc.recompute()
        Import.export([group], r"{supplier / "fixture.stp"}")
        """,
    )
    async with running_server(workspace) as (cad, _):
        text = await cad.ok("doc", action="import", path=str(supplier / "fixture.stp"), name="fixture")

        assert "2 bodies" in text and "content: 2 solids" in text, text
        assert volume(text) == pytest.approx(40 * 40 * 10 + 10 * 10 * 30, rel=1e-6)
        low, high = bbox_of(text)
        assert low == pytest.approx([500, 600, 700]) and high == pytest.approx([540, 640, 740])


async def test_a_part_declared_in_inches_is_converted_and_the_file_unit_is_reported(workspace: Path) -> None:
    supplier = workspace / "supplier"
    supplier.mkdir()
    make_files(
        supplier,
        f"""
        Part.setStaticValue("write.step.unit", "INCH")
        export(Part.makeBox(10, 20, 30, V(100, 0, 0)), r"{supplier / "inch.step"}", "inch_block")
        Part.setStaticValue("write.step.unit", "MM")
        """,
    )
    async with running_server(workspace) as (cad, _):
        text = await cad.ok("doc", action="import", path=str(supplier / "inch.step"), name="inch_block")

        assert "units: the file declares inch; converted to mm" in text, text
        low, high = bbox_of(text)
        size = [h - lo for h, lo in zip(high, low, strict=True)]
        assert size == pytest.approx([254, 508, 762], rel=1e-6)  # the file says 10 x 20 x 30 inches


async def test_iges_solids_and_surfaces(workspace: Path) -> None:
    supplier = workspace / "supplier"
    supplier.mkdir()
    make_files(
        supplier,
        f"""
        box = Part.makeBox(10, 20, 30, V(100, 200, 300))
        Part.setStaticValue("write.iges.brep.mode", 1)
        box.exportIges(r"{supplier / "solid.igs"}")
        Part.setStaticValue("write.iges.brep.mode", 0)
        box.exportIges(r"{supplier / "faces.iges"}")
        """,
    )
    async with running_server(workspace) as (cad, _):
        solid = await cad.ok("doc", action="import", path=str(supplier / "solid.igs"), name="solid")
        assert "units: the file declares mm" in solid and "content: 1 solids" in solid, solid
        assert volume(solid) == pytest.approx(6000, rel=1e-6)
        assert "warning" not in solid

        faces = await cad.ok("doc", action="import", path=str(supplier / "faces.iges"), name="faces")
        assert "warning" in faces and "volume is not meaningful" in faces, faces
        low, high = bbox_of(faces)
        assert low == pytest.approx([100, 200, 300]) and high == pytest.approx([110, 220, 330])


async def test_parasolid_is_refused_and_so_is_a_name_that_would_replace_a_file(workspace: Path) -> None:
    (workspace / "supplier.x_t").write_text("**PART1", encoding="utf-8")
    (workspace / "other.step").write_text("not a step file", encoding="utf-8")
    async with running_server(workspace) as (cad, _):
        text = await cad.err("doc", action="import", path="supplier.x_t", name="supplier")
        assert "no Parasolid reader" in text and "STEP" in text

        broken = await cad.err("doc", action="import", path="other.step", name="other")
        assert "could not be read" in broken or "holds no solid" in broken, broken
        assert not (workspace / "other.FCStd").exists()
        assert "document: none" in await cad.ok("doc", action="status")


async def design_on(cad: Cad, workspace: Path) -> float:
    """Import a plain 100 x 60 x 8 block at (1000, 2000, 300) and design on it: a hole, a boss and a rounded edge.
    Returns the volume it ends with."""
    text = await cad.ok("doc", action="import", path=str(workspace / "supplier" / "block.step"), name="block")
    assert volume(text) == pytest.approx(PLATE_VOLUME, rel=1e-6), text
    assert "adopted: block" in text, text

    drilled = await cad.ok(
        "op",
        name="hole",
        id="pin_hole",
        params={
            "face": TOP,
            "at": [1010, 2010, 308],
            "standard": "simple",
            "diameter_mm": 5,
            "end_condition": "through_all",
        },
    )
    assert volume(drilled) == pytest.approx(PLATE_VOLUME - math.pi * 6.25 * 8, rel=1e-4), drilled

    await cad.ok(
        "op",
        name="sketch",
        id="boss_sketch",
        params={"on": TOP, "entities": [{"kind": "circle", "name": "boss", "center": [0, 0], "diameter": 10}]},
    )
    padded = await cad.ok("op", name="extrude", id="boss", params={"sketch": "boss_sketch", "depth_mm": 6})
    assert volume(padded) == pytest.approx(PLATE_VOLUME - math.pi * 6.25 * 8 + math.pi * 25 * 6, rel=1e-4), padded

    side = {"kind": "face", "from_op": "block", "select": {"normal": [1, 0, 0]}}
    top_plate = {"kind": "face", "from_op": "block", "select": {"normal": [0, 0, 1], "largest": True}}
    rounded = await cad.ok(
        "op",
        name="fillet",
        id="round",
        params={"edges": [{"kind": "edge", "between": [top_plate, side]}], "radius_mm": 2},
    )
    final = PLATE_VOLUME - math.pi * 6.25 * 8 + math.pi * 25 * 6 - (1 - math.pi / 4) * 4 * 60
    assert volume(rounded) == pytest.approx(final, rel=1e-3), rounded
    return final


async def test_an_imported_part_is_a_design_in_progress_that_saves_and_reopens(workspace: Path) -> None:
    supplier = workspace / "supplier"
    supplier.mkdir()
    make_files(
        supplier,
        f'export(Part.makeBox(100, 60, 8, V(1000, 2000, 300)), r"{supplier / "block.step"}", "block")',
    )
    async with running_server(workspace) as (cad, _):
        final = await design_on(cad, workspace)

        saved = await cad.ok("doc", action="save")
        assert "warning" not in saved, saved
        holes = await cad.ok("inspect", action="holes")
        assert "D5 through depth 8" in holes, holes

    async with running_server(workspace) as (cad, _):
        opened = await cad.ok("doc", action="open", path="block.FCStd")
        assert volume(opened) == pytest.approx(final, rel=1e-3), opened
        low, high = bbox_of(opened)
        assert low == pytest.approx([1000, 2000, 300]) and high == pytest.approx([1100, 2060, 314])
        # and it goes on: another hole in the plate
        more = await cad.ok(
            "op",
            name="hole",
            params={
                "face": {"kind": "face", "from_op": "block", "select": {"normal": [0, 0, 1], "largest": True}},
                "at": [1090, 2050, 308],
                "standard": "simple",
                "diameter_mm": 3,
                "end_condition": "through_all",
            },
        )
        assert volume(more) == pytest.approx(volume(opened) - math.pi * 2.25 * 8, rel=1e-4), more


async def test_holes_tell_counterbores_drill_points_and_pocket_fillets_apart(workspace: Path) -> None:
    supplier = workspace / "supplier"
    supplier.mkdir()
    make_files(
        supplier,
        f"""
        block = Part.makeBox(200, 100, 8, V(1000, 2000, 300))
        block = block.cut(Part.makeCylinder(3, 20, V(1020, 2020, 290)))     # D6 through ...
        block = block.cut(Part.makeCylinder(5, 3, V(1020, 2020, 305)))      # ... counterbored D10 x 3
        drill = Part.makeCylinder(2.5, 6, V(1100, 2050, 302))               # D5 blind, 6 deep, with a drill point
        drill = drill.fuse(Part.makeCone(2.5, 0, 1.5, V(1100, 2050, 302), V(0, 0, -1)))
        block = block.cut(drill)
        pocket = Part.makeBox(30, 30, 4, V(1150, 2035, 304))                 # a pocket whose inside corners are rounded
        block = block.cut(pocket)
        corners = [e for e in block.Edges if abs(e.Length - 4) < 1e-6
                   and min(abs(e.Vertexes[0].X - 1150), abs(e.Vertexes[0].X - 1180)) < 1e-6
                   and min(abs(e.Vertexes[0].Y - 2035), abs(e.Vertexes[0].Y - 2065)) < 1e-6]
        assert len(corners) == 4, len(corners)
        block = block.makeFillet(3, corners)
        export(block, r"{supplier / "holes.step"}", "holes")
        """,
    )
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="import", path=str(supplier / "holes.step"), name="holes")

        text = await cad.ok("inspect", action="holes")

        lines = text.splitlines()
        assert lines[0].startswith("holes: 3 in 1 body; by diameter (mm): 1 x D5, 1 x D6 (1 through), 1 x D10"), text
        assert lines[1].startswith("D5 blind depth 6") and "axis from (1100, 2050, 308) along (0, 0, -1)" in lines[1], (
            text
        )
        assert lines[2].startswith("D6 through depth 5"), text
        assert lines[3].startswith("D10 blind depth 3") and "axis from (1020, 2020, 308) along (0, 0, -1)" in lines[3]


HOLE_ROW = re.compile(r"^D(\d+) (through|blind) depth ([\d.]+).* axis from \((.*?)\) along \((.*?)\) .*fitted", re.M)


def hole_rows(text: str) -> list[tuple[int, str, float, list[float], list[float]]]:
    return [
        (int(d), kind, float(depth), [float(v) for v in origin.split(",")], [float(v) for v in along.split(",")])
        for d, kind, depth, origin, along in HOLE_ROW.findall(text)
    ]


async def test_holes_are_recognised_on_bspline_faces_by_fitting_a_cylinder(workspace: Path) -> None:
    supplier = workspace / "supplier"
    supplier.mkdir()
    make_files(
        supplier,
        f"""
        plate = Part.makeBox(160, 100, 10)
        for i in range(6):
            plate = plate.cut(Part.makeCylinder(2, 20, V(15 + 20 * i, 15, -5)))         # six D4 through
        for i in range(4):
            plate = plate.cut(Part.makeCylinder(3, 20, V(20 + 30 * i, 35, -5)))         # four D6 through
        for i in range(2):
            plate = plate.cut(Part.makeCylinder(4, 5, V(20 + 30 * i, 55, 5)))           # two D8 blind, 5 deep
            plate = plate.cut(Part.makeCylinder(5, 20, V(20 + 30 * i, 75, -5)))         # two D10 through
        # one D12 through made of two half cylinders: two faces and a seam, one hole
        half = Part.makeCylinder(6, 20, V(100, 60, -5), V(0, 0, 1), 180)
        plate = plate.cut(half).cut(half.rotated(V(100, 60, 0), V(0, 0, 1), 180))
        plate = plate.fuse(Part.makeCylinder(3.5, 6, V(140, 80, 10)))                   # a D7 pin: not a hole
        export(as_bspline(plate), r"{supplier / "customer.step"}", "customer")
        """,
    )
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="import", path=str(supplier / "customer.step"), name="customer")

        text = await cad.ok("inspect", action="holes", limit=50)

        header = text.splitlines()[0]
        assert header.startswith(
            "holes: 15 in 1 body; by diameter (mm): 6 x D4 (6 through), 4 x D6 (4 through), 2 x D8, "
            "2 x D10 (2 through), 1 x D12 (1 through)"
        ), text
        assert "D7" not in header and "15 of 15 holes are on non-analytic" in text, text
        rows = hole_rows(text)
        assert len(rows) == 15, text
        by_diameter = {d: [r for r in rows if r[0] == d] for d in (4, 6, 8, 10, 12)}
        assert {d: len(r) for d, r in by_diameter.items()} == {4: 6, 6: 4, 8: 2, 10: 2, 12: 1}
        for _, kind, depth, origin, along in by_diameter[4] + by_diameter[6] + by_diameter[10] + by_diameter[12]:
            assert kind == "through" and depth == pytest.approx(10, abs=1e-3), text
            assert along == pytest.approx([0, 0, 1], abs=1e-6) and origin[2] == pytest.approx(300, abs=1e-3)
        for _, kind, depth, origin, along in by_diameter[8]:  # blind: the axis runs from the open top into the material
            assert kind == "blind" and depth == pytest.approx(5, abs=1e-3), text
            assert along == pytest.approx([0, 0, -1], abs=1e-6) and origin[2] == pytest.approx(310, abs=1e-3)
        assert sorted(r[3][0] for r in by_diameter[4]) == pytest.approx([1015 + 20 * i for i in range(6)], abs=1e-3)
        assert by_diameter[12][0][3][:2] == pytest.approx([1100, 2060], abs=1e-3)


async def test_a_part_of_three_hundred_bspline_faces_lists_its_holes_in_seconds(workspace: Path) -> None:
    supplier = workspace / "supplier"
    supplier.mkdir()
    make_files(
        supplier,
        f"""
        plate = Part.makeBox(400, 400, 8)
        tools = [Part.makeCylinder(2.5, 20, V(12 + 22 * i, 12 + 22 * j, -5)) for i in range(17) for j in range(17)]
        plate = plate.cut(Part.makeCompound(tools))
        export(as_bspline(plate), r"{supplier / "perforated.step"}", "perforated")
        """,
    )
    async with running_server(workspace) as (cad, _):
        text = await cad.ok("doc", action="import", path=str(supplier / "perforated.step"), name="perforated")
        faces = int(re.search(r"(\d+) faces", text).group(1))  # type: ignore[union-attr]
        assert faces >= 289, text

        started = time.perf_counter()
        holes = await cad.ok("inspect", action="holes", limit=5)
        elapsed = time.perf_counter() - started

        assert holes.splitlines()[0].startswith("holes: 289 in 1 body; by diameter (mm): 289 x D5 (289 through)"), holes
        print(f"holes on {faces} B-spline faces: {elapsed:.2f} s")
        assert elapsed < 20.0
