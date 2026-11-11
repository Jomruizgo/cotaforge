"""A large STEP assembly through the real server on the real FreeCAD: one body per solid, the coordinates of the
file kept, progress reported to the client while it runs, and the time and memory against FreeCAD's own
``Import.insert`` on the same file; and the same solids as an IGES of loose faces.

The files are made in the test by exporting solids from FreeCAD itself (a plate with a grid of holes, copied and placed
many times, in sub-assemblies). ``test_a_small_assembly...`` runs always; ``test_a_900_solid...`` (about
32,000 faces, minutes of FreeCAD time) is the performance test: it only runs with ``COTAFORGE_PERF=1`` and prints the
figures it measured (run it with ``-s`` to see them).
"""

import itertools
import json
import os
import re
import subprocess
import textwrap
import time
from pathlib import Path

import pytest
from cotaforge.adapters.freecad import find_freecad_root
from cotaforge.adapters.freecad.rpc import _binaries

from tests.integration.conftest import running_server, volume


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COTAFORGE_HOME", str(tmp_path / "home"))


GENERATOR = """
import json, sys
import FreeCAD as App, Part, Import
V = App.Vector
SOLIDS, GROUPS, HOLES_X, HOLES_Y, STEP, EXPECTED = {solids}, {groups}, {holes_x}, {holes_y}, r"{step}", r"{expected}"
PITCH = 6.5
WIDTH, DEPTH, HEIGHT = HOLES_X * PITCH + 3, HOLES_Y * PITCH + 3, 8.0

def plate(variant):
    shape = Part.makeBox(WIDTH, DEPTH, HEIGHT)
    radius = 1.0 + 0.1 * variant        # a few different parts, like a real assembly
    for i in range(HOLES_X):
        for j in range(HOLES_Y):
            shape = shape.cut(Part.makeCylinder(radius, HEIGHT + 4, V(3 + i * PITCH, 3 + j * PITCH, -2)))
    return shape

variants = [plate(v) for v in range(4)]
doc = App.newDocument("maker")
groups, per_group, made = [], -(-SOLIDS // GROUPS), 0
volume = 0.0
faces = 0
low = [1e18] * 3
high = [-1e18] * 3
for g in range(GROUPS):
    group = doc.addObject("App::Part", "Sub%d" % g)
    group.Placement = App.Placement(V(g * 1000.0, 2000.0, 300.0), App.Rotation())   # far from the origin
    groups.append(group)
    for k in range(per_group):
        if made == SOLIDS:
            break
        shape = variants[made % 4].copy()          # its own geometry, not an instance of one
        obj = doc.addObject("Part::Feature", "part_%d" % made)
        obj.Shape = shape
        obj.Placement = App.Placement(V((k % 10) * (WIDTH + 5), (k // 10) * (DEPTH + 5), 0), App.Rotation())
        group.addObject(obj)
        placed = shape.copy()
        placed.Placement = group.Placement.multiply(obj.Placement)
        box = placed.BoundBox
        low = [min(a, b) for a, b in zip(low, (box.XMin, box.YMin, box.ZMin))]
        high = [max(a, b) for a, b in zip(high, (box.XMax, box.YMax, box.ZMax))]
        volume += shape.Volume
        faces += len(shape.Faces)
        made += 1
doc.recompute()
Import.export(groups, STEP)
json.dump({{"solids": made, "faces": faces, "volume": volume, "low": low, "high": high}}, open(EXPECTED, "w"))
"""

# The same solids as an IGES whose faces arrive loose: the faces of each solid, placed where it stands, written without
# any topology above them.
TO_IGES = """
import FreeCAD as App, Import
doc = App.newDocument("again")
Import.insert(r"{step}", doc.Name)
flat = []
for obj in list(doc.Objects):
    if obj.isDerivedFrom("Part::Feature") and not obj.Shape.isNull():
        shape = obj.Shape.copy()
        shape.Placement = obj.getGlobalPlacement()
        made = doc.addObject("Part::Feature", "flat")
        made.Shape = shape
        flat.append(made)
Import.export(flat, r"{iges}")
"""

# Loose trimmed B-spline faces (a plate with holes, its top face converted to a B-spline surface), written as an IGES:
# the shape of a surface model from a supplier, where a face is expensive to check and to measure.
TRIMMED_FACES = """
import FreeCAD as App, Part, Import
V = App.Vector
doc = App.newDocument("surfaces")
made = []
for i in range({count}):
    box = Part.makeBox(30, 30, 5)
    for k in range(3):
        box = box.cut(Part.makeCylinder(2 + 0.1 * (i % 5), 20, V(7 + k * 8, 15, -5)))
    top = max(box.Faces, key=lambda f: f.CenterOfMass.z + f.Area * 1e-9)
    nurbs = top.toNurbs()
    nurbs.translate(V((i % 50) * 40, (i // 50) * 40, 0))
    obj = doc.addObject("Part::Feature", "f")
    obj.Shape = nurbs
    made.append(obj)
Import.export(made, r"{iges}")
"""

BASELINE_IGES = """
import sys, time
import FreeCAD as App, Import, Part
doc = App.newDocument("plain")
started = time.perf_counter()
Import.insert(r"{iges}", doc.Name)
insert_s = time.perf_counter() - started
started = time.perf_counter()
shape = Part.read(r"{iges}")
read_s = time.perf_counter() - started
peak = 0.0
if sys.platform != "win32":
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
print("PLAIN", insert_s, read_s, peak, len(doc.Objects), len(shape.Faces))
"""

BASELINE = """
import sys, time
import FreeCAD as App, Import
doc = App.newDocument("plain")
started = time.perf_counter()
Import.insert(r"{step}", doc.Name)
elapsed = time.perf_counter() - started
peak = 0.0
if sys.platform != "win32":
    import resource
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
print("PLAIN", elapsed, peak, len(doc.Objects))
"""


def freecad_run(script: Path, timeout_s: float) -> str:
    root = find_freecad_root()
    assert root is not None
    done = subprocess.run(  # noqa: S603
        [str(_binaries(root)[0]), str(script)], check=True, timeout=timeout_s, capture_output=True, text=True
    )
    out = done.stdout + done.stderr
    assert "Exception while processing file" not in out, out
    return out


def make_assembly(directory: Path, solids: int, groups: int, holes_x: int, holes_y: int) -> dict:
    script = directory / "make_assembly.py"
    script.write_text(
        textwrap.dedent(GENERATOR).format(
            solids=solids,
            groups=groups,
            holes_x=holes_x,
            holes_y=holes_y,
            step=directory / "assembly.step",
            expected=directory / "expected.json",
        ),
        encoding="utf-8",
    )
    freecad_run(script, 600.0)
    return json.loads((directory / "expected.json").read_text(encoding="utf-8"))


def make_iges(directory: Path) -> Path:
    script = directory / "make_iges.py"
    script.write_text(
        textwrap.dedent(TO_IGES).format(step=directory / "assembly.step", iges=directory / "assembly.igs"),
        encoding="utf-8",
    )
    freecad_run(script, 1800.0)
    return directory / "assembly.igs"


def make_trimmed_faces(directory: Path, count: int) -> Path:
    script = directory / "make_trimmed.py"
    script.write_text(
        textwrap.dedent(TRIMMED_FACES).format(count=count, iges=directory / "trimmed.igs"), encoding="utf-8"
    )
    freecad_run(script, 1800.0)
    return directory / "trimmed.igs"


async def reopen_and_ask(workspace: Path, file_name: str) -> tuple[float, float, str]:
    """Open the saved document in a NEW server (a new conversation) and ask for the status: the seconds each took and
    the status text."""
    async with running_server(workspace) as (cad, _):
        started = time.perf_counter()
        await cad.ok("doc", action="open", path=file_name)
        opened_s = time.perf_counter() - started
        started = time.perf_counter()
        status = await cad.ok("doc", action="status")
        return opened_s, time.perf_counter() - started, status


def numbers(text: str) -> list[float]:
    return [float(v) for v in text.split(",")]


def bbox_of(text: str) -> tuple[list[float], list[float]]:
    match = re.search(r"bbox: size \(.*?\) mm, min \((.*?)\), max \((.*?)\)", text)
    assert match, text
    return numbers(match.group(1)), numbers(match.group(2))


async def import_with_progress(
    workspace: Path, step: Path, name: str, **options: object
) -> tuple[str, float, list[tuple[float, str, float]]]:
    """The text of the import, the seconds it took and every progress notice the client got (value, message,
    seconds)."""
    heard: list[tuple[float, str, float]] = []
    started = time.perf_counter()

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        assert total == 100.0
        heard.append((progress, message or "", time.perf_counter() - started))

    async with running_server(workspace) as (cad, _):
        result = await cad.session.call_tool(
            "doc", {"action": "import", "path": str(step), "name": name, **options}, progress_callback=on_progress
        )
        elapsed = time.perf_counter() - started
        text = "\n".join(getattr(c, "text", "") for c in result.content)
        assert not result.isError, text
        return text, elapsed, heard


def check_the_import(text: str, expected: dict) -> None:
    assert f"imported: assembly.step (STEP): {expected['solids']} bodies" in text, text[:400]
    assert f"content: {expected['solids']} solids, {expected['solids']} shells, {expected['faces']} faces" in text
    assert volume(text) == pytest.approx(expected["volume"], rel=1e-6)
    low, high = bbox_of(text)  # the coordinates of the file, nothing recentred
    assert low == pytest.approx(expected["low"], abs=1e-3) and high == pytest.approx(expected["high"], abs=1e-3)
    assert "valid: yes" in text and "NOT valid" not in text


def check_the_progress(heard: list[tuple[float, str, float]], solids: int) -> None:
    values = [v for v, _, _ in heard]
    messages = [m for _, m, _ in heard]
    assert values == sorted(set(values)), "progress must only rise"
    assert max(values) <= 100.0
    # the CAD starts in the background while the call waits for it: those notices come first, then the import's own
    own = list(itertools.dropwhile(lambda m: m.startswith("starting the CAD software"), messages))
    assert not any(m.startswith("starting the CAD software") for m in own), "the start notices only come first"
    assert own[0].startswith("reading the file (FreeCAD gives no percentage here)")
    assert f"adopting body {solids}/{solids}" in messages and f"checking body {solids}/{solids}" in messages
    assert any(m.startswith("linking") for m in messages) and messages[-1].startswith("saving assembly.")
    prefixes = ("reading", "adopting", "checking", "saving")
    order = [next(i for i, m in enumerate(messages) if m.startswith(p)) for p in prefixes]
    assert order == sorted(order)


async def test_a_small_assembly_imports_one_body_per_solid_in_its_own_coordinates_and_reports_progress(
    workspace: Path,
) -> None:
    expected = make_assembly(workspace, solids=60, groups=3, holes_x=3, holes_y=2)
    assert expected["solids"] == 60

    text, elapsed, heard = await import_with_progress(workspace, workspace / "assembly.step", "assembly")

    check_the_import(text, expected)
    check_the_progress(heard, 60)
    assert (workspace / "assembly.FCStd").is_file() and (workspace / "assembly.cotaforge.json").is_file()
    print(f"\nsmall assembly: 60 solids, {expected['faces']} faces imported in {elapsed:.1f} s, {len(heard)} notices")


async def test_an_iges_of_loose_faces_is_one_surface_body_until_it_is_sewn_into_solids(workspace: Path) -> None:
    expected = make_assembly(workspace, solids=60, groups=3, holes_x=3, holes_y=2)
    iges = make_iges(workspace)

    surfaces, _, _ = await import_with_progress(workspace, iges, "skin")

    assert "imported: assembly.igs (IGES): 1 body" in surfaces, surfaces[:300]
    assert f"content: 0 solids, 0 shells, {expected['faces']} faces" in surfaces
    assert "NO solid (surfaces)" in surfaces and "note: read with the plain IGES reader" in surfaces
    assert "sew=true" in surfaces and "sewing:" not in surfaces

    sewn, _, heard = await import_with_progress(workspace, iges, "solids", sew=True)

    assert "imported: assembly.igs (IGES): 60 bodies" in sewn, sewn[:300]
    assert f"sewing: {expected['faces']} loose faces joined at 0.01 mm" in sewn and "60 closed -> 60 solids" in sewn
    assert "0 free edges" in sewn and "sew=true" not in sewn
    assert volume(sewn) == pytest.approx(expected["volume"], rel=1e-6)
    assert any(m.startswith("sewing the loose faces") for _, m, _ in heard)


@pytest.mark.skipif(os.environ.get("COTAFORGE_PERF") != "1", reason="performance test: set COTAFORGE_PERF=1")
async def test_a_900_solid_iges_of_loose_faces_is_one_body_and_sews_into_solids(workspace: Path) -> None:
    expected = make_assembly(workspace, solids=900, groups=12, holes_x=6, holes_y=5)
    iges = make_iges(workspace)
    baseline = workspace / "baseline_iges.py"
    baseline.write_text(textwrap.dedent(BASELINE_IGES).format(iges=iges), encoding="utf-8")
    found = re.search(r"PLAIN (\S+) (\S+) (\S+) (\d+) (\d+)", freecad_run(baseline, 1800.0))
    assert found is not None
    insert_s, read_s, plain_mb, objects, faces = found.groups()

    surfaces, plain_import_s, heard = await import_with_progress(workspace, iges, "skin")
    sewn, sew_import_s, sew_heard = await import_with_progress(workspace, iges, "solids", sew=True)

    assert f"content: 0 solids, 0 shells, {expected['faces']} faces" in surfaces
    assert "900 closed -> 900 solids" in sewn and volume(sewn) == pytest.approx(expected["volume"], rel=1e-6)
    peak = lambda notices: max(  # noqa: E731
        (float(f.group(1)) for _, m, _ in notices if (f := re.search(r"memory (\d+) MB", m))), default=0.0
    )
    print(
        f"\n900 solids as loose faces ({expected['faces']} faces)"
        f"\n  FreeCAD alone: Import.insert {float(insert_s):.1f} s ({objects} objects), "
        f"Part.read {float(read_s):.1f} s ({faces} faces), peak {float(plain_mb):.0f} MB"
        f"\n  cotaforge, surfaces only: {plain_import_s:.1f} s, memory seen {peak(heard):.0f} MB"
        f"\n  cotaforge, sew=true: {sew_import_s:.1f} s, memory seen {peak(sew_heard):.0f} MB"
    )
    for seen in (peak(heard), peak(sew_heard)):  # the plain read holds the result once; so does the import
        assert seen < 1.5 * float(plain_mb), f"memory {seen:.0f} MB against {float(plain_mb):.0f} MB for FreeCAD alone"
    assert plain_import_s < 2.0 * (float(read_s) + float(insert_s)) + 10.0


@pytest.mark.skipif(os.environ.get("COTAFORGE_PERF") != "1", reason="performance test: set COTAFORGE_PERF=1")
async def test_a_900_solid_assembly_imports_within_twice_the_time_of_freecad_itself(workspace: Path) -> None:
    expected = make_assembly(workspace, solids=900, groups=12, holes_x=6, holes_y=5)
    assert expected["solids"] == 900 and expected["faces"] == 900 * 36

    baseline = workspace / "baseline.py"
    baseline.write_text(textwrap.dedent(BASELINE).format(step=workspace / "assembly.step"), encoding="utf-8")
    plain_s, plain_mb, objects = re.search(r"PLAIN (\S+) (\S+) (\d+)", freecad_run(baseline, 1800.0)).groups()  # type: ignore[union-attr]

    text, elapsed, heard = await import_with_progress(workspace, workspace / "assembly.step", "assembly")

    check_the_import(text, expected)
    check_the_progress(heard, 900)
    beats = [m for _, m, _ in heard if " s elapsed" in m]
    peaks = [float(found.group(1)) for found in (re.search(r"memory (\d+) MB", m) for m in beats) if found]
    ratio = elapsed / float(plain_s)
    print(
        f"\n900 solids, {expected['faces']} faces, file {(workspace / 'assembly.step').stat().st_size / 1e6:.0f} MB"
        f"\n  FreeCAD Import.insert alone: {float(plain_s):.1f} s, peak {float(plain_mb):.0f} MB ({objects} objects)"
        f"\n  cotaforge doc import (read + adopt + check + save + record): {elapsed:.1f} s = {ratio:.2f} x, "
        f"worker memory seen at the heartbeats: {max(peaks, default=0):.0f} MB, {len(heard)} notices"
        f"\n  first notices: {[m for _, m, _ in heard[:3]]}"
    )
    assert ratio < 2.0, f"the import took {ratio:.2f} x what FreeCAD needs to read the file"
    if peaks and float(plain_mb) > 0:
        assert max(peaks) < 1.5 * float(plain_mb)
    # an untouched import is saved as id ranges: the session record is a small fraction of a record per sub-shape
    record = workspace / "assembly.cotaforge.json"
    assert record.stat().st_size < 4_000_000, f"the session record is {record.stat().st_size / 1e6:.1f} MB"
    opened_s, status_s, status = await reopen_and_ask(workspace, "assembly.FCStd")
    print(f"\n  session record {record.stat().st_size / 1e6:.2f} MB; reopen {opened_s:.1f} s, status {status_s:.2f} s")
    assert status_s < 3.0, f"the status of the reopened assembly took {status_s:.1f} s"
    assert "chars cut" not in status and "valid: yes" in status


@pytest.mark.skipif(os.environ.get("COTAFORGE_PERF") != "1", reason="performance test: set COTAFORGE_PERF=1")
async def test_one_body_of_two_thousand_trimmed_bspline_faces_imports_within_twice_the_plain_read(
    workspace: Path,
) -> None:
    iges = make_trimmed_faces(workspace, 2000)
    baseline = workspace / "baseline_trimmed.py"
    baseline.write_text(textwrap.dedent(BASELINE_IGES).format(iges=iges), encoding="utf-8")
    found = re.search(r"PLAIN (\S+) (\S+) (\S+) (\d+) (\d+)", freecad_run(baseline, 1800.0))
    assert found is not None
    _insert_s, read_s, plain_mb, _objects, faces = found.groups()

    text, elapsed, _heard = await import_with_progress(workspace, iges, "trimmed")

    assert f"content: 0 solids, 0 shells, {faces} faces" in text and "imported: trimmed.igs (IGES): 1 body" in text
    record = workspace / "trimmed.cotaforge.json"
    opened_s, status_s, status = await reopen_and_ask(workspace, "trimmed.FCStd")
    print(
        f"\n{faces} trimmed B-spline faces: Part.read {float(read_s):.1f} s, peak {float(plain_mb):.0f} MB"
        f"\n  cotaforge doc import: {elapsed:.1f} s = {elapsed / float(read_s):.2f} x, record {record.stat().st_size} B"
        f"\n  reopen {opened_s:.1f} s, status {status_s:.2f} s"
    )
    assert elapsed < 2.0 * float(read_s) + 5.0
    assert record.stat().st_size < 100_000 and status_s < 3.0 and opened_s < 20.0
