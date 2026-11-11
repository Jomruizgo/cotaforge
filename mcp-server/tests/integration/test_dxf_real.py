"""DXF export of a face outline through the real server and FreeCAD's own exporter, read back with a tiny parser."""

import math
import re
from pathlib import Path

import pytest

from tests.integration.conftest import Cad, running_server, volume

PLATE = {
    "on": {"kind": "plane", "name": "XY"},
    "entities": [{"kind": "rect", "name": "outline", "center": [50, 30], "width": 100, "height": 60}],
}
TOP = {"kind": "face", "from_op": "s2", "select": {"normal": [0, 0, 1]}}
HOLE = {"face": TOP, "standard": "simple", "end_condition": "through_all"}
# Sketches cannot hold arcs, so a slot is a rectangle cut plus a drilled hole at each end: two straight sides and two
# semicircular ends.
SLOT_BODY = {
    "on": TOP,
    "entities": [{"kind": "rect", "name": "body", "center": [0, -12], "width": 32, "height": 8}],
}


def read_dxf(path: Path) -> list[tuple[str, list[tuple[int, str]]]]:
    """(entity type, its (group code, value) pairs) of the ENTITIES section; no dependency on a DXF library."""
    lines = path.read_text(encoding="latin-1").splitlines()
    pairs = [(int(lines[i].strip()), lines[i + 1].strip()) for i in range(0, len(lines) - 1, 2)]
    entities: list[tuple[str, list[tuple[int, str]]]] = []
    inside = False
    for code, value in pairs:
        if code == 2 and value == "ENTITIES":
            inside = True
        elif code == 0 and value == "ENDSEC":
            inside = False
        elif inside and code == 0:
            entities.append((value, []))
        elif inside and entities:
            entities[-1][1].append((code, value))
    return entities


def kinds(entities: list[tuple[str, list[tuple[int, str]]]]) -> dict[str, int]:
    found: dict[str, int] = {}
    for kind, _ in entities:
        found[kind] = found.get(kind, 0) + 1
    return found


def curved_segments(entities: list[tuple[str, list[tuple[int, str]]]]) -> int:
    """Arcs kept as arcs: ARC entities plus the bulged segments of polylines."""
    arcs = sum(1 for kind, _ in entities if kind == "ARC")
    bulged = sum(1 for kind, data in entities if kind == "LWPOLYLINE" for code, v in data if code == 42 and float(v))
    return arcs + bulged


def extent(entities: list[tuple[str, list[tuple[int, str]]]]) -> tuple[float, float, float, float]:
    """Bounding box of the straight geometry and the circles (enough: the outline here is straight)."""
    xs: list[float] = []
    ys: list[float] = []
    for kind, data in entities:
        if kind not in ("LINE", "LWPOLYLINE", "CIRCLE"):
            continue
        radius = next((float(v) for code, v in data if code == 40 and kind == "CIRCLE"), 0.0)
        points = [
            (float(v), float(w))
            for (c1, v), (c2, w) in zip(data, data[1:], strict=False)
            if c1 in (10, 11) and c2 in (20, 21)
        ]
        for x, y in points:
            xs += [x - radius, x + radius]
            ys += [y - radius, y + radius]
    return min(xs), min(ys), max(xs), max(ys)


async def at(cad: Cad, x: float, y: float) -> list[float]:
    """The (u, v) offset from the top face's CURRENT centre to the world point (x, y): every cut moves that centre."""
    faces = await cad.ok("inspect", action="faces", normal=[0, 0, 1])
    centre = re.search(r"c\((-?[\d.]+), (-?[\d.]+), ", faces)
    assert centre is not None, faces
    return [x - float(centre.group(1)), y - float(centre.group(2))]


async def plate_with_hole_and_slot(cad: Cad) -> None:
    """100 x 60 x 8 plate, a 8 mm hole at (50, 45) and a slot 8 mm wide between (34, 18) and (66, 18)."""
    await cad.ok("doc", action="new", name="placa")
    await cad.ok("op", name="sketch", params=PLATE)
    await cad.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 8})
    await cad.ok("op", name="sketch", id="sl", params=SLOT_BODY)
    await cad.ok("op", name="cut", id="slot", params={"sketch": "sl", "depth_mm": 8})
    for step, (x, y) in (("drill", (50, 45)), ("end1", (34, 18)), ("end2", (66, 18))):
        params = {**HOLE, "diameter_mm": 8, "position": await at(cad, x, y)}
        cut = await cad.ok("op", name="hole", id=step, params=params)
    expected = 100 * 60 * 8 - math.pi * 16 * 8 - (32 * 8 + math.pi * 16) * 8
    assert volume(cut) == pytest.approx(expected, rel=1e-3)


async def test_a_plate_with_a_hole_and_a_slot_exports_all_its_loops_at_real_scale(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await plate_with_hole_and_slot(cad)
        before = await cad.ok("inspect", action="tree")
        face = re.search(r"(face:\d+) plane n\(0, 0, 1\)", await cad.ok("inspect", action="faces", normal=[0, 0, 1]))
        assert face is not None
        reply = await cad.ok("export", action="dxf", face=face.group(1), path="placa.dxf")
        assert "exported dxf" in reply

        entities = read_dxf(workspace / "placa.dxf")
        found = kinds(entities)
        assert found.get("CIRCLE") == 1  # the drilled hole stays a circle
        # the two ends of the slot stay true arcs (a hole placed from a 3-decimal reading may split one in two)
        radii = [float(v) for kind, data in entities if kind == "ARC" for code, v in data if code == 40]
        assert curved_segments(entities) >= 2 and radii == pytest.approx([4.0] * len(radii), abs=1e-6)
        assert not {"SPLINE", "ELLIPSE"} & set(found)
        straight = found.get("LINE", 0) + sum(
            1 for kind, data in entities if kind == "LWPOLYLINE" for code, _ in data if code == 10
        )
        assert straight >= 3  # outer contour (a polyline or four lines) and the slot sides
        x0, y0, x1, y1 = extent(entities)
        assert (x0, y0) == pytest.approx((0, 0), abs=1e-3) and (x1, y1) == pytest.approx((100, 60), abs=1e-3)
        text = (workspace / "placa.dxf").read_text(encoding="latin-1")
        assert "$INSUNITS" in text  # units declared in the file
        assert await cad.ok("inspect", action="tree") == before  # the live document is untouched
        assert [p.name for p in workspace.iterdir() if p.name.startswith(".")] == []


async def test_the_same_face_by_reference_and_a_side_face_in_its_own_plane(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await plate_with_hole_and_slot(cad)
        await cad.ok("export", action="dxf", face={**TOP, "from_op": "s2"}, path="by_ref.dxf")
        by_ref = read_dxf(workspace / "by_ref.dxf")
        assert kinds(by_ref).get("CIRCLE") == 1
        # the 60 x 8 end face is flattened into its own plane: 60 by 8, nothing of the 100 mm length
        await cad.ok("export", action="dxf", face={"kind": "face", "from_op": "s2", "select": {"normal": [1, 0, 0]}},
                     path="end.dxf")  # fmt: skip
        x0, y0, x1, y1 = extent(read_dxf(workspace / "end.dxf"))
        assert sorted((x1 - x0, y1 - y0)) == pytest.approx([8, 60], abs=1e-3)
        assert (x0, y0) == pytest.approx((0, 0), abs=1e-3)


async def test_dxf_refuses_to_overwrite_and_a_curved_face(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await plate_with_hole_and_slot(cad)
        await cad.ok("export", action="dxf", face=TOP, path="out.dxf")
        first = (workspace / "out.dxf").read_bytes()
        refused = await cad.err("export", action="dxf", face=TOP, path="out.dxf")
        assert "already exists" in refused and "overwrite=true" in refused
        assert (workspace / "out.dxf").read_bytes() == first
        await cad.ok("export", action="dxf", face=TOP, path="out.dxf", overwrite=True)
        cylinder = re.search(r"(face:\d+) cylinder", await cad.ok("inspect", action="faces", surface="cylinder"))
        assert cylinder is not None
        curved = await cad.err("export", action="dxf", face=cylinder.group(1), path="curved.dxf")
        assert "planar" in curved and not (workspace / "curved.dxf").exists()
        # the session keeps working after the exports
        assert "volume" in await cad.ok("inspect", action="properties")


async def test_mass_and_bom_of_the_exported_plate(workspace: Path) -> None:
    async with running_server(workspace) as (cad, _):
        await plate_with_hole_and_slot(cad)
        assert "mass: not available" in await cad.ok("inspect", action="properties")
        await cad.ok("doc", action="set_material", material="Acero estructural")
        properties = await cad.ok("inspect", action="properties")
        match = re.search(r"mass: ([\d.]+) kg \(density 7850 kg/m3", properties)
        assert match is not None, properties
        assert float(match.group(1)) == pytest.approx(volume(properties) * 7850e-9, rel=1e-3)
        bom = await cad.ok("inspect", action="bom")
        assert "bom: 1 line(s), 1 part(s); material: Acero estructural" in bom
        assert "total mass:" in bom and "EN 1993-1-1" in bom
