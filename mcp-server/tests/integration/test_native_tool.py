"""``run_native`` through the real stdio server and FreeCAD: a step among the ops, undo through it, failures that roll
back by themselves, the usage counter, and a save / close / open round trip across a native step."""

import json
import math
from pathlib import Path

import pytest

from tests.integration.conftest import Cad, running_server, volume

SKETCH = {
    "on": {"kind": "plane", "name": "XY"},
    "entities": [{"kind": "rect", "name": "outline", "center": [30, 20], "width": 60, "height": 40}],
}
PLATE = 60 * 40 * 10
HOLE = {
    "face": {"kind": "face", "from_op": "s2", "select": {"normal": [0, 0, 1]}},
    "position": [0, 0],
    "standard": "simple",
    "diameter_mm": 8,
    "end_condition": "through_all",
}
HOLED = PLATE - math.pi * 16 * 10
FAR_BOX = (
    'b = doc.addObject("Part::Box", "NativeBox")\n'
    "b.Length, b.Width, b.Height = 10, 10, 10\n"
    "b.Placement = Placement(Vector(500, 0, 0), Rotation())\n"
)


async def plate(cad: Cad) -> None:
    await cad.ok("doc", action="new", name="plate")
    await cad.ok("op", name="sketch", params=SKETCH)
    await cad.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 10})


async def test_a_native_step_among_ops_is_undone_in_its_place(workspace: Path) -> None:
    async with running_server(workspace) as (cad, init):
        assert init.instructions is not None and 'cad_guide(topic="native")' in init.instructions
        await plate(cad)
        step = await cad.ok("run_native", code=FAR_BOX, intent="a far block")
        assert step.startswith("ok: s3 native") and "NativeBox (Part::Box)" in step
        assert "volume change: +1000 mm3" in step and "size (510, 40, 10)" in step
        assert "[s3]" in await cad.ok("inspect", action="tree")

        drilled = await cad.ok("op", name="hole", id="drill", params=HOLE)
        assert volume(drilled) == pytest.approx(HOLED + 1000, rel=1e-6)  # the loose block is part of the document
        undone = await cad.ok("undo", to="s2")
        assert undone.startswith("undone: drill hole, s3 native")
        assert volume(undone) == pytest.approx(PLATE, rel=1e-6)
        tree = await cad.ok("inspect", action="tree")
        assert "NativeBox" not in tree and "[s3]" not in tree


async def test_a_native_edit_of_a_harness_feature_is_undone_exactly(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await plate(cad)
        code = 'pad = [o for o in doc.Objects if o.TypeId == "PartDesign::Pad"][0]\npad.Length = 25\n'
        edited = await cad.ok("run_native", code=code, intent="thicker plate")
        assert "modified:" in edited and "volume change: +36000 mm3" in edited
        assert volume(await cad.ok("inspect", action="properties")) == pytest.approx(60 * 40 * 25, rel=1e-6)
        back = await cad.ok("undo")
        assert back.startswith("undone: s3 native") and volume(back) == pytest.approx(PLATE, rel=1e-6)


async def test_forty_native_steps_undo_past_the_cap_through_the_server(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="many")
        for index in range(40):
            code = (
                f'b = doc.addObject("Part::Box", "B{index}")\n'
                f"b.Placement = Placement(Vector({index * 20}, 0, 0), Rotation())"
            )
            await cad.ok("run_native", code=code, id=f"n{index}", intent=f"block {index}")
        assert "steps: 40" in await cad.ok("doc", action="status")
        back = await cad.ok("undo", to="n4")
        assert "steps now: 5" in back
        await cad.ok("undo", to="start")
        tree = await cad.ok("inspect", action="tree")
        assert "tree: empty" in tree


async def test_failures_roll_back_by_themselves(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await plate(cad)
        raised = await cad.err("run_native", code="x = 1\nz = Vectr(1, 2, 3)\n", intent="typo")
        assert "error: NameError (line 2)" in raised and "Vector" in raised and "state: unchanged" in raised

        silent = await cad.err(
            "run_native",
            code=(
                'a = doc.addObject("Part::Box", "A")\nt = doc.addObject("Part::Box", "T")\n'
                "t.Length, t.Width, t.Height = 20, 20, 20\n"
                'c = doc.addObject("Part::Cut", "C")\nc.Base = a\nc.Tool = t\n'
            ),
            intent="an empty boolean",
        )
        assert "boolean produced an empty result" in silent and "state: unchanged" in silent

        slow = await cad.err("run_native", code="while True:\n    pass\n", timeout_s=2, intent="loop")
        assert "Timeout" in slow and "state: unchanged" in slow
        tree = await cad.ok("inspect", action="tree")
        assert "NativeBox" not in tree and "[s3]" not in tree
        assert volume(await cad.ok("inspect", action="properties")) == pytest.approx(PLATE, rel=1e-6)
        assert (await cad.ok("run_native", code=FAR_BOX)).startswith("ok:")  # the server is still alive


async def test_the_usage_counter_has_one_line_per_call_and_no_code(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await plate(cad)
        await cad.ok("run_native", code=FAR_BOX, intent="a far block")
        await cad.err("run_native", code="raise RuntimeError('no')", intent="fails")
        await cad.ok("run_native", code="print('only looking')", intent="looking")
    lines = [json.loads(line) for line in (workspace / "native-usage.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [(line["ok"], line["rolled_back"], line["intent"]) for line in lines] == [
        (True, False, "a far block"),
        (False, True, "fails"),
        (True, False, "looking"),
    ]
    assert "NativeBox" not in (workspace / "native-usage.jsonl").read_text(encoding="utf-8")


async def test_a_native_step_survives_save_close_open(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await plate(cad)
        await cad.ok("run_native", code=FAR_BOX, id="far", intent="a far block")
        await cad.ok("doc", action="save", path="plate.FCStd")
        await cad.ok("doc", action="close")

        opened = await cad.ok("doc", action="open", path="plate.FCStd")
        assert "step ids and handles kept" in opened and "far: native" in opened and "drift" not in opened
        assert "[far]" in await cad.ok("inspect", action="tree")
        # the native step ran before the reopen: it cannot be undone, what comes after can
        hole = await cad.ok("op", name="hole", id="drill", params=HOLE)
        assert volume(hole) == pytest.approx(HOLED + 1000, rel=1e-6)  # the loose block came back with the file
        assert (await cad.ok("undo")).startswith("undone: drill hole")
        assert "made before this document was reopened" in await cad.err("undo")
        more = await cad.ok("run_native", code='doc.getObject("NativeBox").Length = 30', id="longer")
        assert more.startswith("ok: longer native")
        assert (await cad.ok("undo")).startswith("undone: longer native")
