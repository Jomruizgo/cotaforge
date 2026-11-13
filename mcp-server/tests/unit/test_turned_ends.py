"""The alert for a turned part whose free end has a bare outer edge: the geometry on synthetic faces, and the line it
adds to the result of an op (fake CAD). It is an alert: no test here expects a refusal."""

from pathlib import Path
from typing import Any

import pytest
from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import (
    Axis,
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

from cotaforge_mcp import turned_ends
from cotaforge_mcp.config import Settings
from cotaforge_mcp.server import make_session
from cotaforge_mcp.turned_ends import ENTRY_ID, free_ends, main_axis
from tests.conftest import Caller

LINE = "memory: " + ENTRY_ID + " — recognize: "
PROVENANCE = Provenance(created_by={"id": "shaft"}, modified_by=())  # type: ignore[arg-type]


# ---- the geometry, on synthetic faces -----------------------------------------------------------------------------


def _box(centre: Any, half: Any) -> BoundingBox:
    return BoundingBox(
        min=tuple(c - h for c, h in zip(centre, half, strict=True)),
        max=tuple(c + h for c, h in zip(centre, half, strict=True)),
    )


def _cylinder(n: int, radius: float, z0: float, z1: float) -> FaceInfo:
    length = z1 - z0
    return FaceInfo(
        handle=FaceHandle(id=f"face:{n}"),
        body=BodyHandle(id="body:1"),
        surface=SurfaceType.cylinder,
        axis=Axis(origin=(0, 0, 0), direction=(0, 0, 1)),
        center=(0, 0, (z0 + z1) / 2),
        radius=radius,
        area_mm2=6.283185307179586 * radius * length,
        bbox=_box((0, 0, (z0 + z1) / 2), (radius, radius, length / 2)),
        provenance=PROVENANCE,
    )


def _disc(n: int, radius: float, z: float, up: bool, inner: float = 0.0) -> FaceInfo:
    return FaceInfo(
        handle=FaceHandle(id=f"face:{n}"),
        body=BodyHandle(id="body:1"),
        surface=SurfaceType.plane,
        normal=(0, 0, 1 if up else -1),
        center=(0, 0, z),
        area_mm2=3.141592653589793 * (radius**2 - inner**2),
        bbox=_box((0, 0, z), (radius, radius, 0)),
        provenance=PROVENANCE,
    )


def _cone(n: int, r0: float, r1: float, z0: float, z1: float) -> FaceInfo:
    return FaceInfo(
        handle=FaceHandle(id=f"face:{n}"),
        body=BodyHandle(id="body:1"),
        surface=SurfaceType.cone,
        axis=Axis(origin=(0, 0, 0), direction=(0, 0, 1)),
        center=(0, 0, (z0 + z1) / 2),
        area_mm2=3.141592653589793 * (r0 + r1) * ((z1 - z0) ** 2 + (r0 - r1) ** 2) ** 0.5,
        bbox=_box((0, 0, (z0 + z1) / 2), (max(r0, r1), max(r0, r1), (z1 - z0) / 2)),
        provenance=PROVENANCE,
    )


def _circle(n: int, radius: float, z: float, first: str, second: str, dihedral: float = 90.0) -> EdgeInfo:
    return EdgeInfo(
        handle=EdgeHandle(id=f"edge:{n}"),
        curve=CurveType.circle,
        faces=(FaceHandle(id=first), FaceHandle(id=second)),
        center=(0, 0, z),
        radius=radius,
        axis=Axis(origin=(0, 0, z), direction=(0, 0, 1)),
        length_mm=6.283185307179586 * radius,
        bbox=_box((0, 0, z), (radius, radius, 0)),
        convex=True,
        dihedral_deg=dihedral,
    )


def _shaft(chamfer_top: bool) -> tuple[list[FaceInfo], dict[str, list[EdgeInfo]]]:
    """A Ø20 shaft of 80 mm along Z; its top end has a 1 mm chamfer when ``chamfer_top``, else it is bare."""
    faces = [_disc(2, 10, 0, up=False)]
    edges = {"face:2": [_circle(1, 10, 0, "face:1", "face:2")]}
    if chamfer_top:
        faces += [_cylinder(1, 10, 0, 79), _cone(4, 10, 9, 79, 80), _disc(3, 9, 80, up=True)]
        edges["face:3"] = [_circle(3, 9, 80, "face:4", "face:3", 135.0)]
    else:
        faces += [_cylinder(1, 10, 0, 80), _disc(3, 10, 80, up=True)]
        edges["face:3"] = [_circle(2, 10, 80, "face:1", "face:3")]
    return faces, edges


def _ends(faces: list[FaceInfo], edges: dict[str, list[EdgeInfo]]) -> list[turned_ends.SharpEnd]:
    return free_ends("shaft", faces, lambda face: edges.get(face.handle.id, []))


def test_both_bare_ends_fire_one_each_with_the_diameter() -> None:
    faces, edges = _shaft(chamfer_top=False)
    found = _ends(faces, edges)
    assert [(e.position, e.diameter, e.axis) for e in found] == [(0.0, 20.0, 2), (80.0, 20.0, 2)]


def test_a_chamfered_end_does_not_fire_and_the_bare_one_does() -> None:
    faces, edges = _shaft(chamfer_top=True)
    assert [e.position for e in _ends(faces, edges)] == [0.0]


def test_a_rounded_end_does_not_fire() -> None:
    faces, edges = _shaft(chamfer_top=True)
    edges["face:3"] = [_circle(3, 9, 80, "face:4", "face:3", 170.0)]  # smooth junction
    faces = [f.model_copy(update={"surface": SurfaceType.torus}) if f.handle.id == "face:4" else f for f in faces]
    assert [e.position for e in _ends(faces, edges)] == [0.0]


def test_a_shoulder_is_not_a_free_end() -> None:
    faces = [
        _disc(2, 10, 0, up=False),
        _cylinder(1, 10, 0, 40),
        _disc(5, 10, 40, up=True),  # a plane at a shoulder: not at the extreme
        _cylinder(6, 15, 40, 80),
        _disc(3, 15, 80, up=True),
    ]
    edges = {
        "face:2": [_circle(1, 10, 0, "face:1", "face:2")],
        "face:3": [_circle(2, 15, 80, "face:6", "face:3")],
        "face:5": [_circle(3, 10, 40, "face:1", "face:5")],
    }
    assert [e.position for e in _ends(faces, edges)] == [0.0, 80.0]


def test_the_end_of_a_smaller_step_is_a_free_end_with_its_own_diameter() -> None:
    faces = [_disc(2, 6, 0, up=False), _cylinder(1, 6, 0, 30), _disc(7, 15, 30, up=False), _cylinder(6, 15, 30, 80)]
    faces.append(_disc(3, 15, 80, up=True))
    edges = {
        "face:2": [_circle(1, 6, 0, "face:1", "face:2")],
        "face:3": [_circle(2, 15, 80, "face:6", "face:3")],
    }
    assert [(e.position, e.diameter) for e in _ends(faces, edges)] == [(0.0, 12.0), (80.0, 30.0)]


def test_the_edge_of_a_bore_is_not_looked_at() -> None:
    faces, edges = _shaft(chamfer_top=True)
    faces[0] = _disc(2, 10, 0, up=False, inner=4)
    chamfered_outer = _circle(1, 10, 0, "face:1", "face:9")  # the outer circle meets something else (a cone)
    bore = _circle(8, 4, 0, "face:7", "face:2")
    edges["face:2"] = [chamfered_outer, bore]
    assert _ends(faces, edges) == []


def test_a_disc_is_not_a_shaft() -> None:
    faces = [_disc(2, 50, 0, up=False), _cylinder(1, 50, 0, 20), _disc(3, 50, 20, up=True)]
    edges = {"face:2": [_circle(1, 50, 0, "face:1", "face:2")], "face:3": [_circle(2, 50, 20, "face:1", "face:3")]}
    assert _ends(faces, edges) == []


def test_a_tiny_end_is_left_alone() -> None:
    faces = [_disc(2, 1.5, 0, up=False), _cylinder(1, 1.5, 0, 30), _disc(3, 1.5, 30, up=True)]
    edges = {"face:2": [_circle(1, 1.5, 0, "face:1", "face:2")], "face:3": [_circle(2, 1.5, 30, "face:1", "face:3")]}
    assert _ends(faces, edges) == []


def test_a_concave_edge_does_not_fire() -> None:
    faces, edges = _shaft(chamfer_top=False)
    edges["face:3"] = [edges["face:3"][0].model_copy(update={"convex": False})]
    assert [e.position for e in _ends(faces, edges)] == [0.0]


def test_a_part_that_is_mostly_not_turned_has_no_main_axis() -> None:
    faces = [_cylinder(1, 5, 0, 10), _disc(2, 5, 10, up=True)]
    faces.append(_disc(3, 50, 0, up=False).model_copy(update={"normal": (1, 0, 0), "area_mm2": 100000.0}))
    assert main_axis(faces) is None


def test_the_line_says_what_was_measured() -> None:
    line = turned_ends.line_for(turned_ends.SharpEnd("shaft", 25.0, 2, 120.0))
    assert line.startswith(LINE) and "Z=120 mm" in line and "Ø25 mm" in line and "\n" not in line and len(line) < 400


# ---- through the tools (fake CAD) -----------------------------------------------------------------------------------


@pytest.fixture
def base_call(tmp_path: Path) -> Caller:
    settings = Settings(adapter="fake", workspace=tmp_path, home=tmp_path / "home", base_memory=True)
    return Caller(make_session(settings, FakeAdapter()))


def _shaft_op(call: Caller, step: str = "shaft", **extra: Any) -> str:
    call.ok("doc", action="new", name="drive")
    return call.ok(
        "op",
        name="cylinder",
        id=step,
        params={"diameter_mm": 25, "height_mm": 120, "origin": [0, 0, 0], **extra},
    )


def test_the_base_set_has_the_entry_and_it_is_not_pushed_by_a_role_or_an_op() -> None:
    items = {i.entry.id: i.entry for i in MemoryStore(base_dir=base_memory_dir()).scope_entries(Scope.base)}
    entry = items[ENTRY_ID]
    assert entry.type.value == "practice" and entry.sector == "precision-machining"
    assert entry.when is None and entry.sources and entry.not_when and entry.examples


def test_a_bare_shaft_fires_once_per_end(base_call: Caller) -> None:
    text = _shaft_op(base_call)
    lines = [row for row in text.splitlines() if row.startswith(LINE)]
    assert len(lines) == 2 and "Z=0 mm" in lines[0] and "Z=120 mm" in lines[1] and "Ø25 mm" in lines[0]


def test_an_edit_that_removes_the_violation_is_measured_again(base_call: Caller) -> None:
    from cotaforge_mcp import measured_alerts

    _shaft_op(base_call)
    assert measured_alerts.count_open(base_call.session) == 2
    base_call.ok("op", edit="shaft", params={"height_mm": 20})  # shorter than its diameter: a disc
    assert measured_alerts.live(base_call.session) == {}


def test_a_declared_process_that_is_not_turning_does_not_fire(base_call: Caller) -> None:
    base_call.ok("memory", action="brief", sector="plastics", processes=["3D printing"])
    assert LINE not in _shaft_op(base_call)


def test_a_declared_turning_process_fires(base_call: Caller) -> None:
    base_call.ok("memory", action="brief", sector="precision machining", processes=["CNC turning"])
    assert LINE in _shaft_op(base_call)


def test_a_bought_part_is_left_alone(base_call: Caller) -> None:
    assert LINE not in _shaft_op(base_call, role="comercial")


def test_a_short_disc_is_left_alone(base_call: Caller) -> None:
    base_call.ok("doc", action="new", name="disc")
    text = base_call.ok("op", name="cylinder", id="disc", params={"diameter_mm": 80, "height_mm": 10})
    assert LINE not in text


def test_a_concept_model_gets_no_alert(base_call: Caller) -> None:
    base_call.ok("doc", action="new", name="drive")
    base_call.ok("doc", action="set_purpose", purpose="sketch")
    text = base_call.ok("op", name="cylinder", id="shaft", params={"diameter_mm": 25, "height_mm": 120})
    assert LINE not in text


def test_an_entry_of_the_engineer_that_replaces_it_silences_the_alert(base_call: Caller) -> None:
    store = MemoryStore(project_dir=base_call.session.settings.workspace / "memory", base_dir=base_memory_dir())
    base = store.get(ENTRY_ID)
    assert base is not None
    store.save(base.entry.model_copy(update={"status": EntryStatus.inactive}), Scope.project)
    assert LINE not in _shaft_op(base_call)


def test_a_failing_read_never_fails_the_op(base_call: Caller, monkeypatch: pytest.MonkeyPatch) -> None:
    base_call.ok("doc", action="new", name="drive")

    def broken(request: Any) -> Any:
        raise RuntimeError("the CAD went away")

    monkeypatch.setattr(base_call.session.adapter, "faces_of", broken)
    text = base_call.ok("op", name="cylinder", id="shaft", params={"diameter_mm": 25, "height_mm": 120})
    assert "ok: shaft" in text and LINE not in text
