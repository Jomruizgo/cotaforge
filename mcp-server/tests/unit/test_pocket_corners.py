"""The alert for a milled pocket whose inside corners no end mill can make: the geometry on synthetic faces, and the
line it adds to the result of an op (fake CAD). It is an alert: no test here expects a refusal."""

from pathlib import Path
from typing import Any

import pytest
from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import (
    BodyHandle,
    BoundingBox,
    CurveType,
    EdgeHandle,
    EdgeInfo,
    FaceHandle,
    FaceInfo,
    Provenance,
    SurfaceType,
)
from cotaforge.memory import EntryStatus, MemoryStore, Scope, base_memory_dir

from cotaforge_mcp import pocket_corners
from cotaforge_mcp.config import Settings
from cotaforge_mcp.pocket_corners import ENTRY_ID, cut_axis, read_pocket, tool_radius
from cotaforge_mcp.server import make_session
from tests.conftest import Caller, plate

MEMORY_LINE = "memory: " + ENTRY_ID + " — recognize: "
TOP = {"kind": "face", "from_op": "s2", "select": {"normal": [0, 0, 1]}}


# ---- the geometry, on synthetic faces -----------------------------------------------------------------------------


def _wall(n: int, normal: Any, centre: Any, size: Any) -> FaceInfo:
    half = [s / 2 for s in size]
    return FaceInfo(
        handle=FaceHandle(id=f"face:{n}"),
        body=BodyHandle(id="body:1"),
        surface=SurfaceType.plane,
        normal=normal,
        center=centre,
        area_mm2=1.0,
        bbox=BoundingBox(
            min=tuple(c - h for c, h in zip(centre, half, strict=True)),
            max=tuple(c + h for c, h in zip(centre, half, strict=True)),
        ),
        provenance=Provenance(created_by={"id": "pocket"}, modified_by=()),  # type: ignore[arg-type]
    )


def _pocket_faces(depth: float) -> list[FaceInfo]:
    """A 10 x 10 pocket: four walls and a floor."""
    return [
        _wall(1, (1, 0, 0), (5, 0, -depth / 2), (0, 10, depth)),
        _wall(2, (-1, 0, 0), (-5, 0, -depth / 2), (0, 10, depth)),
        _wall(3, (0, 1, 0), (0, 5, -depth / 2), (10, 0, depth)),
        _wall(4, (0, -1, 0), (0, -5, -depth / 2), (10, 0, depth)),
        _wall(5, (0, 0, 1), (0, 0, -depth), (10, 10, 0)),
    ]


def _corner(n: int, first: str, second: str, concave: bool | None = True, dihedral: float = 90.0) -> EdgeInfo:
    return EdgeInfo(
        handle=EdgeHandle(id=f"edge:{n}"),
        curve=CurveType.line,
        faces=(FaceHandle(id=first), FaceHandle(id=second)),
        start=(0, 0, 0),
        end=(0, 0, -12),
        length_mm=12.0,
        bbox=BoundingBox(min=(0, 0, -12), max=(0, 0, 0)),
        convex=concave if concave is None else not concave,
        dihedral_deg=dihedral,
    )


def _cylinder(n: int, radius: float, depth: float) -> FaceInfo:
    """A quarter of a cylinder on the Z axis: a rounded corner."""
    from cotaforge.contract import Axis

    return FaceInfo(
        handle=FaceHandle(id=f"face:{n}"),
        body=BodyHandle(id="body:1"),
        surface=SurfaceType.cylinder,
        axis=Axis(origin=(0, 0, 0), direction=(0, 0, 1)),
        center=(5, 5, -depth / 2),
        radius=radius,
        area_mm2=0.5 * 3.141592653589793 * radius * depth,
        bbox=BoundingBox(min=(5 - radius, 5 - radius, -depth), max=(5, 5, 0)),
        provenance=Provenance(created_by={"id": "round"}, modified_by=()),  # type: ignore[arg-type]
    )


def _square_edges(face: FaceInfo) -> list[EdgeInfo]:
    """The vertical corners of the square pocket, each seen from the two walls that meet in it."""
    pairs = {"face:1": ("face:3", "face:4"), "face:2": ("face:3", "face:4"), "face:3": ("face:1", "face:2")}
    pairs["face:4"] = ("face:1", "face:2")
    if face.handle.id not in pairs:
        return []
    return [
        _corner(int("".join(sorted((face.handle.id[-1], other[-1])))), face.handle.id, other)
        for other in pairs[face.handle.id]
    ]  # one edge has one id, seen from either wall


def test_the_cut_axis_of_a_pocket_is_the_direction_its_walls_are_perpendicular_to() -> None:
    assert cut_axis(_pocket_faces(12)) == (0.0, 0.0, 1.0)
    assert cut_axis(_pocket_faces(12)[:4]) == (0.0, 0.0, 1.0)  # no floor: the same axis


def test_the_smallest_tool_reaches_four_diameters() -> None:
    assert tool_radius(12) == pytest.approx(1.5)


def test_a_square_pocket_has_four_sharp_corners() -> None:
    faces = _pocket_faces(12)
    found = read_pocket("pocket", faces, {f.handle.id: f for f in faces}, _square_edges)
    assert found is not None
    assert (found.corners, found.smallest, found.depth, found.through) == (4, 0.0, 12.0, False)
    assert found.tool == pytest.approx(1.5)


def test_a_pocket_without_a_floor_goes_through() -> None:
    faces = _pocket_faces(12)[:4]
    found = read_pocket("window", faces, {f.handle.id: f for f in faces}, _square_edges)
    assert found is not None and found.through


def test_a_rounded_corner_as_large_as_the_tool_needs_does_not_fire() -> None:
    faces = _pocket_faces(12)
    round_corner = _cylinder(9, 2.0, 12)  # radius 2 against a tool of 1.5

    def edges(face: FaceInfo) -> list[EdgeInfo]:
        if face.handle.id == "face:1":
            return [_corner(1, "face:1", "face:9", None, 0.0)]
        return []

    universe = {f.handle.id: f for f in faces} | {"face:9": round_corner}
    assert read_pocket("pocket", faces, universe, edges) is None


def test_a_rounded_corner_smaller_than_the_tool_fires_with_its_radius() -> None:
    faces = _pocket_faces(40)  # depth 40: the tool is 5
    tight = _cylinder(9, 2.0, 40)

    def edges(face: FaceInfo) -> list[EdgeInfo]:
        return [_corner(1, "face:1", "face:9", None, 0.0)] if face.handle.id == "face:1" else []

    universe = {f.handle.id: f for f in faces} | {"face:9": tight}
    found = read_pocket("pocket", faces, universe, edges)
    assert found is not None and (found.corners, found.smallest, found.tool) == (1, 2.0, 5.0)


def test_a_smooth_junction_is_not_a_corner() -> None:
    faces = _pocket_faces(12)

    def edges(face: FaceInfo) -> list[EdgeInfo]:
        return [_corner(1, "face:1", "face:3", dihedral=0.5)] if face.handle.id == "face:1" else []

    assert read_pocket("pocket", faces, {f.handle.id: f for f in faces}, edges) is None


def test_a_convex_corner_is_not_an_inside_corner() -> None:
    faces = _pocket_faces(12)

    def edges(face: FaceInfo) -> list[EdgeInfo]:
        return [_corner(1, "face:1", "face:3", concave=False)] if face.handle.id == "face:1" else []

    assert read_pocket("pocket", faces, {f.handle.id: f for f in faces}, edges) is None


def test_an_engraving_is_not_a_pocket() -> None:
    faces = _pocket_faces(0.5)
    assert read_pocket("mark", faces, {f.handle.id: f for f in faces}, _square_edges) is None


def test_the_line_says_what_was_measured() -> None:
    pocket = pocket_corners.Pocket("pocket", 12.0, 4, 0.0, 1.5, False)
    line = pocket_corners.line_for([pocket])
    assert line.startswith(MEMORY_LINE)
    assert "pocket (12 mm deep)" in line and "4 vertical inside corners with radius 0" in line
    assert "radius 1.5 mm" in line and "\n" not in line and len(line) < 400


# ---- through the tools (fake CAD) -----------------------------------------------------------------------------------


@pytest.fixture
def base_call(tmp_path: Path) -> Caller:
    settings = Settings(adapter="fake", workspace=tmp_path, home=tmp_path / "home", base_memory=True)
    return Caller(make_session(settings, FakeAdapter()))


def _block(call: Caller) -> None:
    """A 100 x 60 x 30 block."""
    call.ok("doc", action="new", name="block")
    call.ok("op", name="sketch", params={"on": {"kind": "plane", "name": "XY"}, "entities": [_rect(50, 30, 100, 60)]})
    call.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 30})


def _rect(x: float, y: float, width: float, height: float) -> dict[str, Any]:
    return {"kind": "rect", "name": "r", "center": [x, y], "width": width, "height": height}


def _pocket(call: Caller, step: str, entity: dict[str, Any], depth: float, **extra: Any) -> str:
    top = {"kind": "face", "from_op": "s2", "select": {"normal": [0, 0, 1]}}
    call.ok("op", name="sketch", id=f"{step}_sk", params={"on": top, "entities": [entity]})
    return call.ok("op", name="cut", id=step, params={"sketch": f"{step}_sk", "depth_mm": depth, **extra})


SLOT = {"kind": "slot", "name": "s", "start": [30, 30], "end": [70, 30], "width": 10}


def test_the_base_set_has_the_entry_and_it_is_not_pushed_by_a_role_or_an_op() -> None:
    items = {i.entry.id: i.entry for i in MemoryStore(base_dir=base_memory_dir()).scope_entries(Scope.base)}
    entry = items[ENTRY_ID]
    assert entry.type.value == "practice" and entry.sector == "precision-machining"
    assert entry.when is None and entry.sources and entry.not_when and entry.examples
    for word in ("electrical discharge", "laser", "waterjet", "dog-bone"):
        assert word in entry.not_when


def test_a_sharp_pocket_adds_the_memory_line(base_call: Caller) -> None:
    call = base_call
    _block(call)
    text = _pocket(call, "pocket", _rect(50, 30, 20, 20), 12)
    line = next(row for row in text.splitlines() if row.startswith(MEMORY_LINE))
    assert "pocket (12 mm deep)" in line and "4 vertical inside corners with radius 0" in line
    assert "radius 1.5 mm" in line


def test_the_line_is_not_repeated_while_nothing_changes(base_call: Caller) -> None:
    call = base_call
    _block(call)
    assert MEMORY_LINE in _pocket(call, "pocket", _rect(50, 30, 20, 20), 12)
    call.ok("op", name="sketch", id="boss_sk", params={"on": TOP, "entities": [_rect(10, 10, 4, 4)]})
    assert MEMORY_LINE not in call.ok("op", name="extrude", id="boss", params={"sketch": "boss_sk", "depth_mm": 3})


def test_a_slot_with_round_ends_as_large_as_the_tool_needs_does_not_fire(base_call: Caller) -> None:
    call = base_call
    _block(call)
    assert MEMORY_LINE not in _pocket(call, "slot", SLOT, 12)  # ends of radius 5 against a tool of 1.5


def test_a_declared_laser_process_does_not_fire_on_a_blind_pocket_either(base_call: Caller) -> None:
    call = base_call
    call.ok("memory", action="brief", sector="chapa metálica", processes=["laser_cut"])
    _block(call)
    assert MEMORY_LINE not in _pocket(call, "pocket", _rect(50, 30, 20, 20), 12)


def test_a_laser_cut_outline_through_a_plate_does_not_fire(base_call: Caller) -> None:
    call = base_call
    call.ok("memory", action="brief", sector="chapa metálica", processes=["laser_cut"])
    plate(call)
    call.ok("op", name="sketch", params={"on": TOP, "entities": [_rect(50, 30, 20, 20)]})
    text = call.ok("op", name="cut", params={"sketch": "s3", "end_condition": "through_all"})
    assert MEMORY_LINE not in text


def test_a_through_cut_is_left_alone_when_no_process_is_declared(base_call: Caller) -> None:
    call = base_call
    plate(call)
    call.ok("op", name="sketch", params={"on": TOP, "entities": [_rect(50, 30, 20, 20)]})
    text = call.ok("op", name="cut", params={"sketch": "s3", "end_condition": "through_all"})
    assert MEMORY_LINE not in text


def test_a_through_cut_fires_when_milling_is_declared(base_call: Caller) -> None:
    call = base_call
    call.ok("memory", action="brief", sector="precision machining", processes=["CNC milling"])
    plate(call)
    call.ok("op", name="sketch", params={"on": TOP, "entities": [_rect(50, 30, 20, 20)]})
    text = call.ok("op", name="cut", params={"sketch": "s3", "end_condition": "through_all"})
    assert MEMORY_LINE in text  # 8 mm thick: the smallest tool has radius 1 mm and the walls are sharp


def test_a_declared_edm_process_does_not_fire(base_call: Caller) -> None:
    call = base_call
    call.ok("memory", action="brief", sector="precision machining", processes=["EDM"])
    _block(call)
    assert MEMORY_LINE not in _pocket(call, "pocket", _rect(50, 30, 20, 20), 12)


def test_a_declared_milling_process_fires(base_call: Caller) -> None:
    call = base_call
    call.ok("memory", action="brief", sector="precision machining", processes=["CNC milling", "EDM"])
    _block(call)
    assert MEMORY_LINE in _pocket(call, "pocket", _rect(50, 30, 20, 20), 12)


def test_a_concept_model_gets_no_alert(base_call: Caller) -> None:
    call = base_call
    call.ok("doc", action="new", name="block")
    call.ok("doc", action="set_purpose", purpose="sketch")
    call.ok("op", name="sketch", params={"on": {"kind": "plane", "name": "XY"}, "entities": [_rect(50, 30, 100, 60)]})
    call.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 30})
    assert MEMORY_LINE not in _pocket(call, "pocket", _rect(50, 30, 20, 20), 12)


def test_an_entry_of_the_engineer_that_replaces_it_silences_the_alert(base_call: Caller) -> None:
    call = base_call
    settings = call.session.settings
    store = MemoryStore(project_dir=settings.workspace / "memory", base_dir=base_memory_dir())
    base = store.get(ENTRY_ID)
    assert base is not None
    store.save(base.entry.model_copy(update={"status": EntryStatus.inactive}), Scope.project)
    _block(call)
    assert MEMORY_LINE not in _pocket(call, "pocket", _rect(50, 30, 20, 20), 12)


def test_a_failing_read_never_fails_the_op(base_call: Caller, monkeypatch: pytest.MonkeyPatch) -> None:
    call = base_call
    _block(call)

    def broken(request: Any) -> Any:
        raise RuntimeError("the CAD went away")

    call.ok("op", name="sketch", id="pocket_sk", params={"on": TOP, "entities": [_rect(50, 30, 20, 20)]})
    monkeypatch.setattr(call.session.adapter, "faces_of", broken)
    text = call.ok("op", name="cut", id="pocket", params={"sketch": "pocket_sk", "depth_mm": 12})
    assert "ok: pocket cut" in text and MEMORY_LINE not in text


def test_editing_the_depth_gives_a_new_reading(base_call: Caller) -> None:
    call = base_call
    _block(call)
    assert "12 mm deep" in _pocket(call, "pocket", _rect(50, 30, 20, 20), 12)
    text = call.ok("op", edit="pocket", params={"depth_mm": 20})
    assert MEMORY_LINE in text and "20 mm deep" in text
