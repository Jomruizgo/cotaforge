"""Inserting an imported part into an assembly on the real FreeCAD through the real server: whatever edges the vendor
solids have (seams, degenerate apexes and poles, free edges of an open shell) the part goes in; the step reports counts,
never a list of faces; an operation that needs an edge between exactly two faces refuses the odd edge only when it is
asked for it; and a large vendor assembly goes in about as fast as FreeCAD links it.

The files are made in the test by exporting synthetic solids from FreeCAD itself. ``test_a_...900_solid...`` and
``test_..._two_thousand_...`` are the performance tests: they only run with ``COTAFORGE_PERF=1`` and print the figures
they measured (run them with ``-s`` to see them).
"""

import os
import re
import textwrap
import time
from pathlib import Path

import pytest

from tests.integration.conftest import Cad, run_freecad_script, running_server, volume
from tests.integration.test_import_scale import freecad_run, make_assembly, make_trimmed_faces


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COTAFORGE_HOME", str(tmp_path / "home"))


PREAMBLE = """
import FreeCAD as App, Part, Import
V = App.Vector

def export(shapes, path):
    doc = App.newDocument("maker")
    objs = []
    for i, shape in enumerate(shapes):
        obj = doc.addObject("Part::Feature", "part%d" % i)
        obj.Shape = shape
        objs.append(obj)
    doc.recompute()
    Import.export(objs, path)
    App.closeDocument(doc.Name)
"""


def make_files(directory: Path, body: str) -> None:
    script = directory / "make_files.py"
    script.write_text(PREAMBLE + textwrap.dedent(body), encoding="utf-8")
    run_freecad_script(script)


# a cone (apex), a sphere (poles) and a cylinder (seam) as separate solids of one part
ROUND = """
export([Part.makeCone(10, 0, 20), Part.makeSphere(8, V(40, 0, 0)), Part.makeCylinder(5, 20, V(80, 0, 0))], r"{path}")
"""

# a box with its top face missing (four free edges on one shell) beside a plain box
OPEN_SHELL = """
box = Part.makeBox(30, 20, 10)
top = max(box.Faces, key=lambda f: f.CenterOfMass.z)
export([Part.Shell([f for f in box.Faces if not f.isSame(top)]), Part.makeBox(10, 10, 10, V(50, 0, 0))], r"{path}")
"""

PLATE = """
plate = Part.makeBox(40, 30, 10).cut(Part.makeCylinder(4, 20, V(20, 15, -5)))
export([plate], r"{path}")
"""


async def import_and_insert(cad: Cad, name: str, step: Path) -> str:
    await cad.ok("doc", action="import", path=str(step), name=name)
    await cad.ok("doc", action="new", name=f"asm_{name}", kind="assembly")
    return await cad.ok(
        "op", name="insert_component", id=f"{name}_i", params={"part": name, "placement": {"origin": [0, 0, 0]}}
    )


@pytest.mark.parametrize(
    ("name", "body", "faces", "volume_mm3"),
    [("round", ROUND, 6, 2094.395 + 2144.661 + 1570.796), ("open_shell", OPEN_SHELL, 11, 1000.0)],
)
async def test_an_imported_part_with_odd_edges_inserts_and_the_result_counts_its_faces(
    workspace: Path, name: str, body: str, faces: int, volume_mm3: float
) -> None:
    supplier = workspace / "supplier"
    supplier.mkdir()
    make_files(supplier, body.format(path=supplier / f"{name}.step"))
    async with running_server(workspace) as (cad, _):
        text = await import_and_insert(cad, name, supplier / f"{name}.step")

        lines = text.splitlines()
        assert lines[0].startswith("saved the assembly first") and lines[1] == f"ok: {name}_i insert_component", text
        assert re.search(rf"created: {faces} faces, \d+ edges, \d+ bod", text), text
        assert "counted here and not listed" in text and "comp:1/face:" in text
        assert volume(text) == pytest.approx(volume_mm3, rel=1e-3), text
        assert "valid: yes" in text and len(text) < 1500, text

        # nothing was described at the insert; an edge is described when it is asked for: the ordinary ones are
        # (their faces are named), the odd ones are refused there with the reason
        answers = [await cad("inspect", action="edge", id=f"comp:1/edge:{n}") for n in range(1, 60)]
        refused = [a.text for a in answers if a.is_error and "is not shared by exactly two faces" in a.text]
        described = [a.text for a in answers if not a.is_error]
        assert described, "an ordinary edge is described"
        if name == "open_shell":
            assert refused and "only one face uses it" in refused[0], [a.text for a in answers if a.is_error][:3]
        else:  # a cone's apex, a sphere's poles and a cylinder's seam are not edges at all
            assert not refused, refused


async def test_two_imported_parts_are_mated_through_the_faces_of_their_components(workspace: Path) -> None:
    supplier = workspace / "supplier"
    supplier.mkdir()
    make_files(supplier, PLATE.format(path=supplier / "plate.step"))
    top = {"kind": "face", "from_op": "lower", "select": {"normal": [0, 0, 1], "largest": True}}
    underside = {"kind": "face", "from_op": "upper", "select": {"normal": [0, 0, -1], "largest": True}}
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="import", path=str(supplier / "plate.step"), name="plate")
        await cad.ok("doc", action="new", name="stack", kind="assembly")
        await cad.ok("op", name="insert_component", id="lower", params={"part": "plate", "placement": {}})
        await cad.ok(
            "op", name="insert_component", id="upper", params={"part": "plate", "placement": {"origin": [100, 0, 50]}}
        )

        mated = await cad.ok("op", name="mate_coincident", id="stack_them", params={"a": top, "b": underside})

        assert mated.startswith("ok: stack_them mate_coincident"), mated
        measured = await cad.ok("inspect", action="measure")
        assert "size (140, 30, 20) mm" in measured, measured  # only the heights are mated


def baseline_links(directory: Path, part_file: Path) -> tuple[float, int]:
    """FreeCAD alone: open the saved part, make an ``App::Part`` of ``App::Link`` to its objects in a new document,
    recompute. The seconds it took and the number of links."""
    script = directory / "baseline_links.py"
    owner = directory / "baseline_asm.FCStd"
    script.write_text(
        textwrap.dedent(
            f"""
            import time
            import FreeCAD as App
            part = App.openDocument(r"{part_file}")
            asm = App.newDocument("baseline_asm")
            asm.saveAs(r"{owner}")  # a link to another document needs its owner saved
            started = time.perf_counter()
            group = asm.addObject("App::Part", "Component")
            links = 0
            for obj in part.Objects:
                if obj.isDerivedFrom("Part::Feature") and not obj.Shape.isNull() and obj.Shape.Faces:
                    link = asm.addObject("App::Link", "L")
                    link.setLink(obj)
                    link.LinkTransform = True
                    group.addObject(link)
                    links += 1
            asm.recompute()
            print("LINKS", time.perf_counter() - started, links)
            """
        ),
        encoding="utf-8",
    )
    found = re.search(r"LINKS (\S+) (\d+)", freecad_run(script, 1800.0))
    assert found is not None
    return float(found.group(1)), int(found.group(2))


async def insert_timed(cad: Cad, part: str) -> tuple[str, float]:
    await cad.ok("doc", action="new", name="asm", kind="assembly")
    started = time.perf_counter()
    text = await cad.ok("op", name="insert_component", id="v1", params={"part": part, "placement": {}})
    return text, time.perf_counter() - started


async def check_insert_stays_small(cad: Cad, workspace: Path, text: str, elapsed: float, linking_s: float) -> None:
    assert len(text) < 2500, f"the result of the insert is {len(text)} characters"
    started = time.perf_counter()
    saved = await cad.ok("doc", action="save")
    save_s = time.perf_counter() - started
    size = (workspace / "asm.cotaforge.json").stat().st_size
    print(f"\n  insert {elapsed:.1f} s (FreeCAD links in {linking_s:.1f} s), save {save_s:.1f} s, record {size} B")
    assert "warning" not in saved, saved
    assert size < 300_000, f"the session record of the assembly is {size} bytes (a few hundred per body, none per face)"
    limit = 3.0 * linking_s + 10.0
    assert elapsed < limit, f"the insert took {elapsed:.1f} s; FreeCAD links the same in {linking_s:.1f} s"


@pytest.mark.skipif(os.environ.get("COTAFORGE_PERF") != "1", reason="performance test: set COTAFORGE_PERF=1")
async def test_a_900_solid_imported_assembly_is_inserted_as_fast_as_freecad_links_it(workspace: Path) -> None:
    expected = make_assembly(workspace, solids=900, groups=12, holes_x=6, holes_y=5)
    assert expected["solids"] == 900 and expected["faces"] == 900 * 36
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="import", path=str(workspace / "assembly.step"), name="vendor")
        linking_s, links = baseline_links(workspace, workspace / "vendor.FCStd")
        assert links == 900

        text, elapsed = await insert_timed(cad, "vendor")

        assert f"created: {expected['faces']} faces" in text and ", 900 bodies" in text, text
        assert volume(text) == pytest.approx(expected["volume"], rel=1e-6)
        await check_insert_stays_small(cad, workspace, text, elapsed, linking_s)
        # the insert described nothing, and a face is still one question away
        info = await cad.ok("inspect", action="face", id="comp:1/face:5")
        assert "comp:1" in info


@pytest.mark.skipif(os.environ.get("COTAFORGE_PERF") != "1", reason="performance test: set COTAFORGE_PERF=1")
async def test_one_imported_body_of_two_thousand_loose_bspline_faces_is_inserted_in_seconds(workspace: Path) -> None:
    iges = make_trimmed_faces(workspace, 2000)
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="import", path=str(iges), name="skin")
        linking_s, links = baseline_links(workspace, workspace / "skin.FCStd")
        assert links == 1

        text, elapsed = await insert_timed(cad, "skin")

        assert "created: 2000 faces" in text and "1 bodies" in text, text
        await check_insert_stays_small(cad, workspace, text, elapsed, linking_s)


# B-spline plates: every surface of a box with a hole converted to a B-spline, what a vendor file holds and what makes
# the exact box a pass over curved surfaces
NURBS_PLATES = """
plates = []
for i in range({count}):
    plate = Part.makeBox(30, 20, 8, V(i * 40, 0, 0)).cut(Part.makeCylinder(3, 20, V(i * 40 + 15, 10, -5)))
    plates.append(plate.toNurbs())
export(plates, r"{path}")
"""

# the same, 900 of them, and one body of 2000 loose trimmed B-spline faces beside them
NURBS_ASSEMBLY_AND_SKIN = """
V = App.Vector
variants = []
for v in range(4):
    plate = Part.makeBox(33, 33, 8)
    for i in range(6):
        for j in range(5):
            plate = plate.cut(Part.makeCylinder(1.0 + 0.1 * v, 12, V(3 + i * 6.5, 3 + j * 6.5, -2)))
    variants.append(plate.toNurbs())
shapes = []
for n in range(900):
    shape = variants[n % 4].copy()
    shape.translate(V((n % 30) * 40, (n // 30) * 40 + 2000, 300))
    shapes.append(shape)
faces = []
for i in range(2000):
    box = Part.makeBox(30, 30, 5)
    for k in range(3):
        box = box.cut(Part.makeCylinder(2 + 0.1 * (i % 5), 20, V(7 + k * 8, 15, -5)))
    top = max(box.Faces, key=lambda f: f.CenterOfMass.z + f.Area * 1e-9)
    nurbs = top.toNurbs()
    nurbs.translate(V((i % 50) * 40, (i // 50) * 40 + 6000, 0))
    faces.append(nurbs)
shapes.append(Part.makeCompound(faces))
export(shapes, r"{path}")
"""


async def test_an_insert_of_b_spline_bodies_reads_the_quick_box_and_measure_gives_the_exact_one(
    workspace: Path,
) -> None:
    supplier = workspace / "supplier"
    supplier.mkdir()
    make_files(supplier, NURBS_PLATES.format(count=6, path=supplier / "plates.step"))
    async with running_server(workspace) as (cad, _):
        text = await import_and_insert(cad, "plates", supplier / "plates.step")

        assert "(quick box: it may enclose a little more than the shape; inspect measure gives the exact one)" in text
        measured = await cad.ok("inspect", action="measure")
        assert "quick box" not in measured and "size (" in measured, measured
        # the exact box fits inside the quick one the insert reported
        quick = re.search(r"bbox: size \((.*?)\) mm", text)
        exact = re.search(r"bbox: size \((.*?)\) mm", measured)
        assert quick and exact
        for q, e in zip(quick.group(1).split(","), exact.group(1).split(","), strict=True):
            assert float(q) >= float(e) - 1e-6


@pytest.mark.skipif(os.environ.get("COTAFORGE_PERF") != "1", reason="performance test: set COTAFORGE_PERF=1")
async def test_the_insert_of_hundreds_of_b_spline_bodies_and_a_body_of_loose_faces_stays_far_below_the_timeout(
    workspace: Path,
) -> None:
    supplier = workspace / "supplier"
    supplier.mkdir()
    make_files(supplier, NURBS_ASSEMBLY_AND_SKIN.format(path=supplier / "hard.step"))
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="import", path=str(supplier / "hard.step"), name="hard")
        text, elapsed = await insert_timed(cad, "hard")
        started = time.perf_counter()
        measured = await cad.ok("inspect", action="measure")
        measure_s = time.perf_counter() - started

        print(f"\n  insert {elapsed:.1f} s, inspect measure (the exact box of every body) {measure_s:.1f} s")
        assert "quick box" in text and "created: " in text, text
        assert "quick box" not in measured
        assert elapsed < 20.0, f"the insert took {elapsed:.1f} s (the request timeout is 60 s)"
