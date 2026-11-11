"""Edge sets through the real stdio server and FreeCAD, the way an engineer drives them: a 19 mm plate with an L profile
and an inner R4 corner, a hole and a seat; the chamfer of the corners of the profile in ONE op, the 1x45 on the straight
edges between flat faces without touching the holes, a corner that cannot take 3 mm (named; ``skip_failing`` applies
the rest). The volumes are read with ``inspect measure``-style tools after every step and checked against closed
forms."""

from pathlib import Path

import pytest

from tests.integration.conftest import running_server, volume

XY = {"kind": "plane", "name": "XY"}
TOP = {"kind": "face", "from_op": "plate", "select": {"normal": [0, 0, 1]}}
THICKNESS = 19.0
ARC = {"kind": "arc3", "name": "inner", "start": [44, 40], "through": [41.171573, 41.171573], "end": [40, 44]}


def _line(name: str, a: list[float], b: list[float]) -> dict:
    return {"kind": "line", "name": name, "start": a, "end": b}


L_PROFILE = [
    _line("bottom", [0, 0], [100, 0]),
    _line("right", [100, 0], [100, 40]),
    _line("step", [100, 40], [44, 40]),
    ARC,
    _line("wall", [40, 44], [40, 80]),
    _line("top", [40, 80], [0, 80]),
    _line("left", [0, 80], [0, 0]),
]
JOGGED_PROFILE = [
    _line("bottom", [0, 0], [100, 0]),
    _line("right", [100, 0], [100, 40]),
    _line("step", [100, 40], [44, 40]),
    ARC,
    _line("short_wall", [40, 44], [40, 47]),
    _line("jog", [40, 47], [42, 47]),
    _line("wall", [42, 47], [42, 80]),
    _line("top", [42, 80], [0, 80]),
    _line("left", [0, 80], [0, 0]),
]
CORNERS = {"kind": "edges", "from_op": "plate", "where": {"parallel_to": "z", "convex": True}}
FLAT_STRAIGHT = {"kind": "edges", "from_op": "plate", "where": {"curve": "line", "planar": True}}


async def _plate(cad, profile: list[dict]) -> float:
    await cad.ok("doc", action="new", name="l_plate")
    await cad.ok("op", name="sketch", id="outline", params={"on": XY, "entities": profile})
    await cad.ok("op", name="extrude", id="plate", params={"sketch": "outline", "depth_mm": THICKNESS})
    hole = {"face": TOP, "at": [20, 20, THICKNESS], "standard": "simple", "diameter_mm": 6.6}
    await cad.ok("op", name="hole", id="bore", params={**hole, "end_condition": "through_all"})
    seat = {"on": XY, "entities": [{"kind": "circle", "name": "seat", "center": [70, 20], "diameter": 12}]}
    await cad.ok("op", name="sketch", id="seat_sketch", params=seat)
    cut = {"sketch": "seat_sketch", "end_condition": "blind", "depth_mm": 6, "direction": "positive"}
    return volume(await cad.ok("op", name="cut", id="seat_pocket", params=cut))


async def test_the_corners_of_the_profile_in_one_op_after_a_preview(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        before = await _plate(cad, L_PROFILE)
        shown = await cad.ok("inspect", action="edges", op="plate", where=CORNERS["where"])
        assert "5 edge(s) (parallel_to Z, convex)" in shown and shown.count("direction (0, 0, 1)") == 5
        assert volume(await cad.ok("inspect", action="properties")) == pytest.approx(before)  # looking changed nothing
        done = await cad.ok("op", name="chamfer", id="corners", params={"edges": [CORNERS], "distance1_mm": 3})
        assert "edges[0]: 5 edge(s)" in done and "warning" not in done
        assert volume(await cad.ok("inspect", action="properties")) == pytest.approx(
            before - 5 * 3 * 3 / 2 * THICKNESS, abs=1e-3
        )


async def test_1x45_on_the_straight_edges_between_flat_faces_and_not_on_the_holes(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        before = await _plate(cad, L_PROFILE)
        done = await cad.ok("op", name="chamfer", id="edge_break", params={"edges": [FLAT_STRAIGHT], "distance1_mm": 1})
        assert "17 edge(s) (planar faces, curve line)" in done
        removed = before - volume(await cad.ok("inspect", action="properties"))
        assert 0 < removed < 17 * 0.5 * 100
        circles = await cad.ok("inspect", action="edges", op="edge_break", where={"curve": "circle"})
        assert "4 edge(s)" in circles  # the mouth and the exit of the bore, the seat's mouth and its floor: untouched


async def test_a_corner_that_cannot_take_3_mm_is_named_and_skip_failing_chamfers_the_others(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        before = await _plate(cad, JOGGED_PROFILE)
        refused = await cad.err("op", name="chamfer", id="corners", params={"edges": [CORNERS], "distance1_mm": 3})
        assert "cannot take 1 of the 6 edges" in refused and "at (42, 47, 9.5)" in refused
        assert "skip_failing" in refused and "state: unchanged" in refused
        assert volume(await cad.ok("inspect", action="properties")) == pytest.approx(before)
        done = await cad.ok(
            "op", name="chamfer", id="corners", params={"edges": [CORNERS], "distance1_mm": 3, "skip_failing": True}
        )
        assert "skipped 1 edge(s)" in done and "midpoint (42, 47, 9.5)" in done
        assert volume(await cad.ok("inspect", action="properties")) == pytest.approx(
            before - 5 * 3 * 3 / 2 * THICKNESS, abs=1e-3
        )


async def test_editing_the_chamfer_resolves_the_set_again_on_the_part_as_it_is(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        before = await _plate(cad, L_PROFILE)
        await cad.ok("op", name="chamfer", id="corners", params={"edges": [CORNERS], "distance1_mm": 3})
        edited = await cad.ok("op", edit="corners", params={"distance1_mm": 1.5})
        assert "rebuilt from corners" in edited
        assert volume(await cad.ok("inspect", action="properties")) == pytest.approx(
            before - 5 * 1.5 * 1.5 / 2 * THICKNESS, abs=1e-3
        )
