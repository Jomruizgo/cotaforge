"""The scenario through the real stdio server and the real FreeCAD 1.1.x, using only the ``op`` vocabulary.

Part one covers extrude, hole, fillet, chamfer, shell, linear_pattern, ref_plane and mirror with analytic volumes;
part two covers revolve, sweep and a sketch on a reference plane, an undo deeper than FreeCAD's own 20-step stack, a
save/close/open round trip, more editing and the STEP export.
"""

import math
from pathlib import Path

import pytest

from tests.integration.conftest import running_server, volume

LENGTH, WIDTH, HEIGHT = 60.0, 40.0, 10.0
HOLE_D = 8.0
FILLET_R = 5.0
WALL = 2.0
PLATE = LENGTH * WIDTH * HEIGHT
HOLED = PLATE - math.pi * (HOLE_D / 2) ** 2 * HEIGHT
ROUNDED = HOLED - (1 - math.pi / 4) * FILLET_R**2 * HEIGHT
BROKEN = ROUNDED - 0.5 * 2.0**2 * WIDTH  # a 2 mm chamfer along the 40 mm edge on +X
IN_WALL = math.pi * (HOLE_D / 2) ** 2 * WALL  # a through hole in the 2 mm top wall of the shell

PLATE_SKETCH = {
    "on": {"kind": "plane", "name": "XY"},
    "entities": [{"kind": "rect", "name": "outline", "center": [30, 20], "width": LENGTH, "height": WIDTH}],
}


def face(normal: list[float], from_op: str = "s2") -> dict:
    return {"kind": "face", "from_op": from_op, "select": {"normal": normal}}


TOP = face([0, 0, 1])
HOLE = {"face": TOP, "position": [0, 0], "standard": "simple", "diameter_mm": HOLE_D, "end_condition": "through_all"}
SHALLOW = {
    "standard": "simple",
    "diameter_mm": HOLE_D,
    "end_condition": "blind",
    "depth_mm": 1.0,
    "drill_point": "flat",
}
CORNER = {"edges": [{"kind": "edge", "between": [face([-1, 0, 0]), face([0, -1, 0])]}], "radius_mm": FILLET_R}
RIM = {"edges": [{"kind": "edge", "between": [TOP, face([1, 0, 0])]}], "distance1_mm": 2.0}


async def test_part_one_through_the_vocabulary_with_analytic_volumes(workspace: Path) -> None:
    async with running_server(workspace) as (cad, init):
        # the instructions are sent before the CAD is up and carry no version; a call that needs the CAD waits for it and
        # the live CAD says it: in doc status and, once it is up, in the profile
        assert init.instructions is not None and init.instructions.startswith("CAD: freecad.")
        assert (await cad.ok("doc", action="status")).startswith("cad: freecad 1.1")
        assert (await cad.ok("cad_guide", topic="profile")).startswith("CAD: freecad 1.1")
        assert "document: part1 (part)" in await cad.ok("doc", action="new", name="part1")

        sketch = await cad.ok("op", name="sketch", params=PLATE_SKETCH, intent="outline")
        assert "area 2400 mm2" in sketch
        extruded = await cad.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": HEIGHT})
        assert volume(extruded) == pytest.approx(PLATE, rel=1e-6)
        assert f"size ({LENGTH:g}, {WIDTH:g}, {HEIGHT:g}) mm" in extruded and "valid: yes" in extruded

        assert volume(await cad.ok("op", name="hole", id="drill", params=HOLE)) == pytest.approx(HOLED, rel=1e-6)
        assert volume(await cad.ok("op", name="fillet", id="round", params=CORNER)) == pytest.approx(ROUNDED, rel=1e-6)
        assert volume(await cad.ok("op", name="chamfer", id="rim", params=RIM)) == pytest.approx(BROKEN, rel=1e-6)

        shelled = await cad.ok(
            "op", name="shell", id="hollow", params={"faces": [face([0, 0, -1])], "thickness_mm": WALL}
        )
        assert 0 < volume(shelled) < BROKEN / 2 and "valid: yes" in shelled

        patterned = await cad.ok(
            "op",
            name="linear_pattern",
            id="row",
            params={"features": ["drill"], "direction": "X", "spacing_mm": 15, "count": 2},
        )
        assert "instances: 2" in patterned
        after_pattern = volume(patterned)
        assert after_pattern == pytest.approx(volume(shelled) - IN_WALL, rel=1e-6)

        seed = await cad.ok("op", name="hole", id="seed", params={**SHALLOW, "face": TOP, "position": [-15, 10]})
        assert volume(seed) == pytest.approx(after_pattern - IN_WALL / 2, rel=1e-6)
        await cad.ok(
            "op",
            name="ref_plane",
            id="mid",
            params={"define": {"how": "offset", "base": {"kind": "plane", "name": "YZ"}, "distance_mm": 30}},
        )
        mirrored = await cad.ok(
            "op", name="mirror", id="twin", params={"features": ["seed"], "plane": {"kind": "plane", "from_op": "mid"}}
        )
        assert volume(mirrored) == pytest.approx(after_pattern - 2 * IN_WALL / 2, rel=1e-6)
        assert "valid: yes" in mirrored

        props = await cad.ok("inspect", action="properties")
        assert volume(props) == pytest.approx(volume(mirrored), rel=1e-6)
        tree = await cad.ok("inspect", action="tree")
        for step in ("drill", "round", "rim", "hollow", "row", "seed", "twin"):
            assert f"[{step}]" in tree, step


async def test_a_shell_matches_the_analytic_volume(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="box")
        await cad.ok("op", name="sketch", params=PLATE_SKETCH)
        await cad.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": HEIGHT})
        shelled = await cad.ok("op", name="shell", params={"faces": [TOP], "thickness_mm": WALL})
        walls = PLATE - (LENGTH - 2 * WALL) * (WIDTH - 2 * WALL) * (HEIGHT - WALL)
        assert volume(shelled) == pytest.approx(walls, rel=1e-6)


async def test_part_two_revolve_sweep_datum_undo_past_the_cap_save_reopen_export(workspace: Path) -> None:
    ring = math.pi * (20.0**2 - 10.0**2) * 20.0  # rectangle u 10..20, v 0..20 revolved about the sketch v axis
    pipe = math.pi * 3.0**2 * (50.0 + 30.0)  # D6 circle along a 50 + 30 mm polyline
    pad = 10.0 * 10.0 * 5.0
    async with running_server(workspace) as (cad, _):
        await cad.ok("doc", action="new", name="part2")
        await cad.ok(
            "op",
            name="sketch",
            id="ringsk",
            params={
                "on": {"kind": "plane", "name": "XY"},
                "entities": [{"kind": "rect", "name": "r", "center": [15, 10], "width": 10, "height": 20}],
            },
        )
        revolved = await cad.ok("op", name="revolve", id="ring", params={"sketch": "ringsk", "axis": "v"})
        assert volume(revolved) == pytest.approx(ring, rel=1e-6)

        await cad.ok(
            "op",
            name="sketch",
            id="prof",
            params={
                "on": {"kind": "plane", "name": "XY"},
                "entities": [{"kind": "circle", "name": "c", "center": [200, 0], "diameter": 6}],
            },
        )
        swept = await cad.ok(
            "op",
            name="sweep",
            id="pipe",
            params={
                "sketch": "prof",
                "path": {"kind": "polyline", "points": [[200, 0, 0], [200, 0, 50], [230, 0, 50]]},
                "merge": False,
            },
        )
        assert volume(swept) == pytest.approx(ring + pipe, rel=1e-6)

        await cad.ok(
            "op",
            name="ref_plane",
            id="lift",
            params={"define": {"how": "offset", "base": {"kind": "plane", "name": "XY"}, "distance_mm": 15}},
        )
        await cad.ok(
            "op",
            name="sketch",
            id="padsk",
            params={
                "on": {"kind": "plane", "from_op": "lift"},
                "entities": [{"kind": "rect", "name": "p", "center": [300, 0], "width": 10, "height": 10}],
            },
        )
        padded = await cad.ok("op", name="extrude", id="pad", params={"sketch": "padsk", "depth_mm": 5, "merge": False})
        total = ring + pipe + pad
        assert volume(padded) == pytest.approx(total, rel=1e-6)
        assert volume(await cad.ok("inspect", action="properties")) == pytest.approx(total, rel=1e-6)
        measure = await cad.ok("inspect", action="measure")
        assert measure.count("body:") == 3 and "size (10, 10, 5)" in measure

        # 30 small bodies, then undo them and more: far deeper than the 20 transactions FreeCAD keeps
        for index in range(30):
            await cad.ok(
                "op",
                name="box",
                id=f"b{index}",
                params={"size": [1, 1, 1], "placement": {"origin": [index * 2, 500, 0]}},
            )
        assert volume(await cad.ok("inspect", action="properties")) == pytest.approx(total + 30, rel=1e-6)
        back = await cad.ok("undo", to="pad")
        assert volume(back) == pytest.approx(total, rel=1e-6) and "steps now:" in back
        deeper = await cad.ok("undo", to="pipe")
        assert volume(deeper) == pytest.approx(ring + pipe, rel=1e-6)
        assert "pad" in deeper and "lift" in deeper
        again = await cad.ok(
            "op", name="box", id="keep", params={"size": [1, 1, 1], "placement": {"origin": [0, 600, 0]}}
        )
        assert volume(again) == pytest.approx(ring + pipe + 1, rel=1e-6)

        saved = await cad.ok("doc", action="save", path="part2.FCStd")
        assert "part2.FCStd" in saved and (workspace / "part2.FCStd").is_file()
        assert (workspace / "part2.cotaforge.json").is_file()
        assert "closed: part2" in await cad.ok("doc", action="close")

        opened = await cad.ok("doc", action="open", path="part2.FCStd")
        assert "step ids and handles kept" in opened and "drift" not in opened
        assert volume(opened) == pytest.approx(ring + pipe + 1, rel=1e-6)
        assert "[keep]" in await cad.ok("inspect", action="tree")

        # keep editing: a reopened design is referenced by the step ids it had
        more = await cad.ok(
            "op", name="box", id="after", params={"size": [2, 2, 2], "placement": {"origin": [0, 700, 0]}}
        )
        assert volume(more) == pytest.approx(ring + pipe + 1 + 8, rel=1e-6)
        assert (await cad.ok("undo")).startswith("undone: after box")
        assert "made before this document was reopened" in await cad.err("undo")

        exported = await cad.ok("export", action="step", path="part2.step")
        target = workspace / "part2.step"
        assert "part2.step" in exported and target.stat().st_size > 500
        assert "ISO-10303" in target.read_text(encoding="utf-8", errors="replace")[:200]
