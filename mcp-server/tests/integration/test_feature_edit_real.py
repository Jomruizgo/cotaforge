"""Features of a file the harness did not build, through the real server and FreeCAD: list, change, delete, look, save.

A plate (pad, pocket, a fillet on the pocket's corners, a chamfer on its top edges) is made inside FreeCAD, saved
and opened: its history is adopted, no IR. Everything the engineer asked for in the field goes through the CAD's
own property or ``Body.removeObject`` as one undoable step with the volume checked; a fragment that saved the file
itself does not make the next ``doc save`` a conflict."""

import math
import re
from pathlib import Path

from tests.integration.conftest import Cad, run_freecad_script, running_server, volume
from tests.integration.test_files import write_script

PLATE_SCRIPT = """
import FreeCAD as App, Part, Sketcher
V = App.Vector
doc = App.newDocument("Plate")
body = doc.addObject("PartDesign::Body", "Body")


def rect(name, w, h, z=0.0):
    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = (body.Origin.OriginFeatures[3], [""])
    sk.MapMode = "FlatFace"
    sk.AttachmentOffset = App.Placement(V(0, 0, z), App.Rotation())
    pts = [V(-w / 2, -h / 2, 0), V(w / 2, -h / 2, 0), V(w / 2, h / 2, 0), V(-w / 2, h / 2, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
    for i in range(4):
        sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
    return sk


sk = rect("Sketch", 100, 60)
pad = body.newObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 10
doc.recompute()
sk2 = rect("Sketch001", 30, 20, 10)
pocket = body.newObject("PartDesign::Pocket", "Pocket")
pocket.Profile = sk2
pocket.Type = 1
doc.recompute()
inner = ["Edge%d" % (i + 1) for i, e in enumerate(pocket.Shape.Edges)
         if e.BoundBox.ZLength > 9 and abs(e.BoundBox.XMin) < 16 and abs(e.BoundBox.YMin) < 11]
fillet = body.newObject("PartDesign::Fillet", "Fillet")
fillet.Base = (pocket, inner)
fillet.Radius = 5
doc.recompute()
outer = ["Edge%d" % (i + 1) for i, e in enumerate(fillet.Shape.Edges)
         if e.BoundBox.ZMin > 9.9 and (e.BoundBox.XLength > 99 or e.BoundBox.YLength > 59
                                       or abs(e.BoundBox.XMax) > 49.9 or abs(e.BoundBox.YMax) > 29.9)]
chamfer = body.newObject("PartDesign::Chamfer", "Chamfer")
chamfer.Base = (fillet, outer)
chamfer.Size = 1
doc.recompute()
doc.saveAs(r"{path}")
"""
FILLET_GAIN = 4 * 10 * (5**2 - math.pi * 5**2 / 4)  # the four pocket corners, R5, 10 mm tall


def gain(radius: float) -> float:
    return 4 * 10 * (radius**2 - math.pi * radius**2 / 4)


def change(text: str) -> float:
    match = re.search(r"volume: -?[\d.]+ mm3 \(([+-][\d.]+)\)", text)
    assert match, text
    return float(match.group(1))


TOP = {"kind": "face", "from_op": "pad", "select": {"normal": [0, 0, 1]}}
FACE_X = {"kind": "face", "from_op": "pad", "select": {"normal": [1, 0, 0]}}


async def opened_plate(cad: Cad, workspace: Path, tmp_path: Path) -> float:
    target = workspace / "plate.FCStd"
    run_freecad_script(write_script(tmp_path, "make_plate.py", PLATE_SCRIPT.format(path=str(target))))
    opened = await cad.ok("doc", action="open", path="plate.FCStd")
    assert "ADOPTED" in opened
    return volume(opened)


async def test_the_acceptance_flow_list_edit_delete_save(workspace: Path, tmp_path: Path) -> None:
    async with running_server(workspace) as (cad, _):
        start = await opened_plate(cad, workspace, tmp_path)

        tree = await cad.ok("inspect", action="tree")
        for name in ("Pad", "Pocket", "Fillet", "Chamfer"):
            assert name in tree
        assert "body Body" in tree and "tip Chamfer" in tree
        assert "Radius=5" in tree and "Size=1" in tree and "made outside the steps" in tree
        detail = await cad.ok("inspect", action="tree", id="Chamfer")
        assert "Size:length" in detail and "built on: Fillet" in detail and "edges it works on" in detail

        filleted = await cad.ok("op", edit="Fillet", params={"Radius": 4}, intent="smaller corners")
        assert "Radius 5 -> 4" in filleted and "valid: yes" in filleted
        assert abs(change(filleted) - (gain(4) - FILLET_GAIN)) < 0.01
        assert volume(filleted) < start

        before = volume(filleted)
        chamfered = await cad.ok("op", edit="Chamfer", params={"Size": 3})
        assert "Size 1 -> 3" in chamfered and volume(chamfered) < before
        assert abs(change(chamfered) - (-0.5 * (3**2 - 1**2) * 320)) < 0.08 * 1280

        deleted = await cad.ok("op", delete="Chamfer")
        assert "tip: Body -> Fillet" in deleted and "removed: Chamfer" in deleted
        assert abs(volume(deleted) - (100 * 60 * 10 - 30 * 20 * 10 + gain(4))) < 0.01
        tree = await cad.ok("inspect", action="tree")
        listed = [line.strip().split()[0] for line in tree.split("features:")[1].splitlines()[1:]]
        assert "Chamfer" not in listed and "tip Fillet" in tree
        fillet_line = next(line for line in tree.splitlines() if line.strip().startswith("Fillet"))
        assert "hidden" not in fillet_line and "tip" in fillet_line
        for sketch in ("Sketch ", "Sketch001"):
            assert "hidden" in next(line for line in tree.splitlines() if line.strip().startswith(sketch))

        undone = await cad.ok("undo")  # the delete is one step
        assert "undone" in undone
        assert "tip Chamfer" in await cad.ok("inspect", action="tree")


async def test_a_native_save_is_not_a_conflict_and_force_and_reload_work(workspace: Path, tmp_path: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await opened_plate(cad, workspace, tmp_path)
        path = (workspace / "plate.FCStd").as_posix()
        native = await cad.ok("run_native", code=f'doc.saveAs(r"{path}")', intent="save from the CAD itself")
        assert "saved plate.FCStd itself" in native

        saved = await cad.ok("doc", action="save")
        assert "saved:" in saved

        # somebody else rewrites the file: a conflict, unless forced
        edit = tmp_path / "other.py"
        edit.write_text(
            f'import FreeCAD as App\nd = App.openDocument(r"{path}")\nd.getObject("Pad").Length = 12\n'
            "d.recompute()\nd.save()\n",
            encoding="utf-8",
        )
        run_freecad_script(edit)
        conflict = await cad.err("doc", action="save")
        assert "file_conflict" in conflict and "force=true" in conflict and "reload" in conflict
        forced = await cad.ok("doc", action="save", force=True)
        assert "saved:" in forced

        # reload: refuses while there are unsaved changes, takes the file when forced
        await cad.ok("op", edit="Fillet", params={"Radius": 3})
        refused = await cad.err("doc", action="reload")
        assert "unsaved_changes" in refused
        reloaded = await cad.ok("doc", action="reload", force=True)
        assert "reloaded: plate.FCStd" in reloaded
        assert "Radius=5" in await cad.ok("inspect", action="tree")


async def test_what_cannot_be_changed_is_refused_with_the_reason(workspace: Path, tmp_path: Path) -> None:
    async with running_server(workspace) as (cad, _):
        start = await opened_plate(cad, workspace, tmp_path)

        unknown = await cad.err("op", edit="Nothing", params={"Radius": 1})
        assert "no feature called 'Nothing'" in unknown and "steps:" in unknown
        bad = await cad.err("op", edit="Fillet", params={"Radius": -2})
        assert "Radius must be greater than 0" in bad
        wrong = await cad.err("op", edit="Fillet", params={"Colour": 1})
        assert "Radius" in wrong
        too_big = await cad.err("op", edit="Fillet", params={"Radius": 200})
        assert "was not applied" in too_big and "state: unchanged" in too_big
        needed = await cad.err("op", delete="Sketch001")
        assert "needed by Pocket" in needed and "cascade" in needed
        middle = await cad.err("op", delete="Pocket")
        assert "was not applied" in middle and "Fillet" in middle
        both = await cad.err("op", delete="Chamfer", edit="Fillet", params={})
        assert "delete is alone" in both
        assert abs(volume(await cad.ok("inspect", action="measure")) - start) < 1e-6

        cascade = await cad.ok("op", delete="Pocket", params={"cascade": True})
        assert "removed:" in cascade and abs(volume(cascade) - 100 * 60 * 10) < 0.01
        assert "tip Pad" in await cad.ok("inspect", action="tree")


async def test_the_steps_the_harness_made_keep_being_edited_through_their_step(workspace: Path) -> None:
    sketch = {
        "on": {"kind": "plane", "name": "XY"},
        "entities": [{"kind": "rect", "name": "outline", "center": [0, 0], "width": 40, "height": 30}],
    }
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="own")
        await cad.ok("op", name="sketch", id="sk", params=sketch)
        plate = volume(await cad.ok("op", name="extrude", id="pad", params={"sketch": "sk", "depth_mm": 8}))
        rim = {"edges": [{"kind": "edge", "between": [TOP, FACE_X]}], "distance1_mm": 2.0}
        chamfered = await cad.ok("op", name="chamfer", id="rim", params=rim)
        tree = await cad.ok("inspect", action="tree")
        owned = next(line for line in tree.splitlines() if line.strip().startswith("Chamfer"))
        assert "step rim" in owned
        redirected = await cad.err("op", edit="Chamfer", params={"Size": 3})
        assert 'op(edit="rim"' in redirected

        deleted = await cad.ok("op", delete="rim")  # by step id: the step's feature goes
        assert "deleted rim" in deleted and "removed: Chamfer" in deleted and "tip: Body -> Pad" in deleted
        assert volume(deleted) == plate > volume(chamfered)
        assert "rim" not in (await cad.ok("inspect", action="tree")).split("features:")[0]
        undone = await cad.ok("undo")
        assert "undone: s1 delete_feature" in undone and volume(undone) == volume(chamfered)


async def test_view_focus_shows_the_tip_and_hides_the_sketches(workspace: Path, tmp_path: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await opened_plate(cad, workspace, tmp_path)
        shown = await cad.ok(
            "run_native",
            code='for n in ("Sketch", "Pad"):\n    doc.getObject(n).Visibility = True\n',
            intent="a messy view",
        )
        assert shown
        focused = await cad.ok("view", action="focus")
        assert "hidden Sketch" in focused or "hidden" in focused
        tree = await cad.ok("inspect", action="tree")
        assert "hidden" in next(line for line in tree.splitlines() if line.strip().startswith("Pad"))
