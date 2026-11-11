"""``doc open`` and ``doc save`` on real files through the real server and FreeCAD.

Any .FCStd opens; a file this server saved and nobody touched comes back exactly; one edited elsewhere is adopted
with its drift reported; a damaged file is a clean error; a file that is not ours is never overwritten."""

import base64
import math
import re
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import Cad, run_freecad_script, running_server, volume

SKETCH = {
    "on": {"kind": "plane", "name": "XY"},
    "entities": [{"kind": "rect", "name": "outline", "center": [30, 20], "width": 60, "height": 40}],
}
TOP = {"kind": "face", "from_op": "s2", "select": {"normal": [0, 0, 1]}}
HOLE = {"face": TOP, "position": [0, 0], "standard": "simple", "diameter_mm": 8, "end_condition": "through_all"}
PLATE = 60 * 40 * 10
HOLED = PLATE - math.pi * 16 * 10

FOREIGN = """
import FreeCAD as App, Part, Sketcher
doc = App.newDocument("Foreign")
body = doc.addObject("PartDesign::Body", "Body")
sk = body.newObject("Sketcher::SketchObject", "Sketch")
sk.MapMode = "Deactivated"
V = App.Vector
pts = [V(0, 0, 0), V(50, 0, 0), V(50, 20, 0), V(0, 20, 0)]
for i in range(4):
    sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
for i in range(4):
    sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
pad = body.newObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 8
doc.recompute()
doc.saveAs(r"{path}")
"""

EDIT = """
import FreeCAD as App
doc = App.openDocument(r"{path}")
pad = next(o for o in doc.Objects if o.TypeId == "PartDesign::Pad")
pad.Length = {length}
doc.recompute()
doc.save()
"""

EDIT_BOX = """
import FreeCAD as App
doc = App.openDocument(r"{path}")
box = next(o for o in doc.Objects if o.TypeId == "Part::Box")
box.Length = 7
doc.recompute()
doc.save()
"""


def write_script(tmp_path: Path, name: str, text: str) -> Path:
    script = tmp_path / name
    script.write_text(text, encoding="utf-8")
    return script


async def saved_plate(cad: Cad, workspace: Path) -> Path:
    await cad.ok("doc", action="new", name="plate")
    await cad.ok("op", name="sketch", params=SKETCH)
    await cad.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 10})
    await cad.ok("op", name="hole", id="drill", params=HOLE)
    await cad.ok("doc", action="save", path="plate.FCStd")
    return workspace / "plate.FCStd"


async def test_a_stamped_file_comes_back_exactly(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        file = await saved_plate(cad, workspace)
        assert (workspace / "plate.cotaforge.json").is_file()
        assert [p.name for p in workspace.iterdir() if p.name.startswith(".")] == []  # no temporary litter
        assert "closed: plate" in await cad.ok("doc", action="close")

        opened = await cad.ok("doc", action="open", path="plate.FCStd")
        assert "saved by this server" in opened and "drift" not in opened and "ADOPTED" not in opened
        assert "s2: extrude" in opened and "drill: hole" in opened
        assert volume(opened) == pytest.approx(HOLED, rel=1e-6)
        assert "[s2]" in await cad.ok("inspect", action="tree")
        # the old ids still name the old geometry
        faces = await cad.ok("inspect", action="faces", op="s2", normal=[0, 0, 1])
        assert "faces: 1" in faces and "by=s2" in faces
        boss = {
            "on": TOP,
            "entities": [{"kind": "rect", "name": "boss", "center": [15, 0], "width": 6, "height": 6}],
        }
        await cad.ok("op", name="sketch", id="bs", params=boss)
        grown = await cad.ok("op", name="extrude", id="boss", params={"sketch": "bs", "depth_mm": 4})
        assert volume(grown) == pytest.approx(HOLED + 6 * 6 * 4, rel=1e-6)
        assert file.stat().st_size > 0


async def test_a_plain_fcstd_without_the_stamp_is_adopted_and_built_on(workspace: Path, tmp_path: Path) -> None:
    target = workspace / "foreign.FCStd"
    run_freecad_script(write_script(tmp_path, "make_foreign.py", FOREIGN.format(path=str(target))))
    assert target.is_file()

    async with running_server(workspace) as (cad, _):
        opened = await cad.ok("doc", action="open", path="foreign.FCStd")
        assert "no harness stamp" in opened and "ADOPTED" in opened
        assert "  body: feature:" in opened  # the body of the file is the alias to build on
        assert volume(opened) == pytest.approx(50 * 20 * 8, rel=1e-6)
        tree = await cad.ok("inspect", action="tree")
        assert "[body]" in tree and "made outside the steps" in tree
        top = await cad.ok("inspect", action="faces", normal=[0, 0, 1])
        assert "faces: 1" in top and "by=body" in top

        hole = await cad.ok(
            "op",
            name="hole",
            params={
                "face": {"kind": "face", "from_op": "body", "select": {"normal": [0, 0, 1]}},
                "position": [0, 0],
                "standard": "simple",
                "diameter_mm": 6,
                "end_condition": "through_all",
            },
        )
        assert volume(hole) == pytest.approx(50 * 20 * 8 - math.pi * 9 * 8, rel=1e-6)
        assert "UNSAVED" in await cad.ok("doc", action="status")
        assert "nothing to undo" not in await cad.ok("undo")  # the hole is ours: undoable
        assert "nothing to undo" in await cad.err("undo")  # the body that came in the file is not

        # saving the adopted design stamps it; reopening it restores the session, alias included
        await cad.ok("op", name="hole", id="again", params={
            "face": {"kind": "face", "from_op": "body", "select": {"normal": [0, 0, 1]}},
            "position": [0, 0], "standard": "simple", "diameter_mm": 6, "end_condition": "through_all",
        })  # fmt: skip
        await cad.ok("doc", action="save")
        await cad.ok("doc", action="close")
        again = await cad.ok("doc", action="open", path="foreign.FCStd")
        assert "step ids and handles kept" in again and "again: hole" in again
        assert volume(again) == pytest.approx(50 * 20 * 8 - math.pi * 9 * 8, rel=1e-6)


async def test_a_file_edited_elsewhere_is_adopted_and_the_drift_is_reported(workspace: Path, tmp_path: Path) -> None:
    async with running_server(workspace) as (cad, _):
        file = await saved_plate(cad, workspace)
        await cad.ok("doc", action="close")
        run_freecad_script(write_script(tmp_path, "edit.py", EDIT.format(path=str(file), length=14)))

        opened = await cad.ok("doc", action="open", path="plate.FCStd")
        assert "changed since the last save made by this server" in opened and "ADOPTED" in opened
        assert "drift: the file was edited after this server saved it" in opened
        thick = 60 * 40 * 14 - math.pi * 16 * 10  # a "through" hole was cut to the 10 mm the plate had then
        was_now = re.search(r"volume was ([\d.]+) mm3, now ([\d.]+) mm3", opened)
        assert was_now is not None, opened
        assert float(was_now.group(1)) == pytest.approx(HOLED, rel=1e-6)
        assert float(was_now.group(2)) == pytest.approx(thick, rel=1e-6)
        assert volume(opened) == pytest.approx(thick, rel=1e-6)
        assert "size was (60, 40, 10) mm, now (60, 40, 14) mm" in opened
        assert "s2: extrude" not in opened  # the previous step ids are gone: the adopted body is the way in


async def test_a_truncated_file_is_a_clean_error_and_the_server_stays_usable(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        file = await saved_plate(cad, workspace)
        await cad.ok("doc", action="close")
        data = file.read_bytes()
        file.write_bytes(data[: len(data) // 2])
        text = await cad.err("doc", action="open", path="plate.FCStd")
        assert "could not be opened" in text and "Traceback" not in text and "damaged or truncated" in text
        assert "document: none" in await cad.ok("doc", action="status")
        empty = workspace / "empty.FCStd"
        empty.write_bytes(b"")
        assert "could not be opened" in await cad.err("doc", action="open", path="empty.FCStd")
        (workspace / "text.FCStd").write_text("not a zip", encoding="utf-8")
        assert "could not be opened" in await cad.err("doc", action="open", path="text.FCStd")
        assert "document: fresh (part)" in await cad.ok("doc", action="new", name="fresh")


async def test_a_file_that_is_not_ours_is_never_overwritten(workspace: Path, tmp_path: Path) -> None:
    target = workspace / "foreign.FCStd"
    run_freecad_script(write_script(tmp_path, "make_foreign.py", FOREIGN.format(path=str(target))))
    before = target.read_bytes()
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="mine")
        await cad.ok("op", name="box", params={"size": [1, 1, 1]})
        text = await cad.err("doc", action="save", path="foreign.FCStd")
        assert "already exists" in text
        assert target.read_bytes() == before
        # a file saved by us and then changed elsewhere: saving over it is refused too
        await cad.ok("doc", action="save", path="mine.FCStd")
        mine = workspace / "mine.FCStd"
        run_freecad_script(write_script(tmp_path, "edit_mine.py", EDIT_BOX.format(path=str(mine))))
        await cad.ok("op", name="box", params={"size": [2, 2, 2]})
        refused = await cad.err("doc", action="save")
        assert "changed outside this server" in refused


async def test_export_and_capture_without_a_window(workspace: Path) -> None:
    async with running_server(workspace) as (cad, init):
        assert init.instructions is not None and "not available here: capture" in init.instructions
        await cad.ok("doc", action="new", name="exp")
        await cad.ok("op", name="box", params={"size": [10, 20, 30]})
        step = await cad.ok("export", action="step", path="exp.step")
        assert (workspace / "exp.step").stat().st_size > 200 and "exp.step" in step
        await cad.ok("export", action="mesh", path="exp.stl")
        assert (workspace / "exp.stl").stat().st_size > 100
        await cad.ok("export", action="macro", path="exp.py")
        assert "App.newDocument" in (workspace / "exp.py").read_text(encoding="utf-8")
        # headless: no window to render, so capture is refused with a clear reason instead of failing obscurely
        capture = await cad.err("view", action="capture")
        assert "not_supported" in capture and "not available" in capture


@pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")
async def test_visible_cad_captures_an_image(workspace: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, init):
        assert init.instructions is not None and "visible window" in init.instructions
        await cad.ok("doc", action="new", name="shot")
        await cad.ok("op", name="box", params={"size": [10, 20, 30]})
        await cad.ok("view", action="set_view", name="iso")
        reply = await cad("view", action="capture", width=320, height=240)
        assert not reply.is_error, reply.text
        assert len(reply.images) == 1 and reply.images[0].mimeType == "image/png"
        assert base64.b64decode(reply.images[0].data)[:8] == b"\x89PNG\r\n\x1a\n"
