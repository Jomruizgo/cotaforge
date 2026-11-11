"""The alert for a bearing that sits on a toleranced length much longer than itself: the geometry on synthetic faces,
and the line it adds to the result of an op (fake CAD). It is an alert: no test here expects a refusal."""

from pathlib import Path
from typing import Any

import pytest
from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import (
    Axis,
    BodyHandle,
    BoundingBox,
    ComponentHandle,
    FaceHandle,
    FaceInfo,
    Provenance,
    SurfaceType,
)
from cotaforge.memory import EntryStatus, MemoryStore, Scope, base_memory_dir

from cotaforge_mcp import bearing_seats
from cotaforge_mcp.bearing_seats import ENTRY_ID, Cylinder, cylinder_of, long_seats
from cotaforge_mcp.config import Settings
from cotaforge_mcp.server import make_session
from tests.conftest import Caller

MEMORY_LINE = "memory: " + ENTRY_ID + " — recognize: "


# ---- the geometry, on synthetic faces -----------------------------------------------------------------------------


def _face(
    n: int, body: str, radius: float, z0: float, z1: float, *, component: str | None = None, axis: Any = (0, 0, 1)
) -> FaceInfo:
    """A cylindrical face on the Z axis between z0 and z1 (box and centre as the CAD reports them)."""
    return FaceInfo(
        handle=FaceHandle(id=f"face:{n}"),
        body=BodyHandle(id=body),
        component=ComponentHandle(id=component) if component else None,
        surface=SurfaceType.cylinder,
        axis=Axis(origin=(0, 0, (z0 + z1) / 2), direction=axis),
        center=(0, 0, (z0 + z1) / 2),
        radius=radius,
        area_mm2=2 * 3.141592653589793 * radius * (z1 - z0),
        bbox=BoundingBox(min=(-radius, -radius, z0), max=(radius, radius, z1)),
        provenance=Provenance(created_by={"id": "s1"}, modified_by=()),  # type: ignore[arg-type]
    )


def _cylinders(owner: str, *faces: FaceInfo) -> list[Cylinder]:
    return [c for c in (cylinder_of(f, owner) for f in faces) if c is not None]


def _ring(owner: str, bore: float, outside: float, z0: float, width: float) -> list[Cylinder]:
    """A bearing envelope: the bore and the outside, both as long as the ring is wide."""
    return _cylinders(owner, _face(1, owner, bore / 2, z0, z0 + width), _face(2, owner, outside / 2, z0, z0 + width))


def test_two_bearings_on_a_straight_shaft_of_one_diameter_fire() -> None:
    shaft = _cylinders("body:1", _face(3, "body:1", 12.5, 0, 120))
    cylinders = {"body:1": shaft, "body:2": _ring("body:2", 25, 52, 20, 15), "body:3": _ring("body:3", 25, 52, 85, 15)}
    seats = long_seats(cylinders, {"body:2": "env_a", "body:3": "env_b"})
    assert [(s.bearing, s.shaft, s.diameter, s.run, s.width) for s in seats] == [
        ("env_a", "body:1", 25.0, 120.0, 15.0),
        ("env_b", "body:1", 25.0, 120.0, 15.0),
    ]


def test_a_stepped_shaft_with_seats_and_shoulders_does_not_fire() -> None:
    """Seats as long as the ring plus a chamfer, a shoulder between them, smaller diameters outside: the usual one."""
    pieces = [
        ("body:1", 10.0, 0, 20),  # D20 end
        ("body:1", 12.5, 20, 37),  # seat of the first bearing: D25, 17 long for a ring 15 wide
        ("body:1", 15.0, 37, 85),  # D30 shoulder and middle
        ("body:1", 12.5, 85, 102),  # seat of the second bearing
        ("body:1", 10.0, 102, 120),
    ]
    shaft = _cylinders("body:1", *(_face(i, o, r, a, b) for i, (o, r, a, b) in enumerate(pieces, start=10)))
    cylinders = {"body:1": shaft, "body:2": _ring("body:2", 25, 52, 20, 15), "body:3": _ring("body:3", 25, 52, 87, 15)}
    assert long_seats(cylinders, {"body:2": "env_a", "body:3": "env_b"}) == []


def test_only_the_bearing_on_the_long_run_fires() -> None:
    pieces = [(10, 12.5, 0, 60), (11, 12.5, 60, 62), (12, 15.0, 62, 80), (13, 12.5, 80, 97)]
    shaft = _cylinders("body:1", *(_face(i, "body:1", r, a, b) for i, r, a, b in pieces))
    cylinders = {"body:1": shaft, "body:2": _ring("body:2", 25, 52, 20, 15), "body:3": _ring("body:3", 25, 52, 82, 15)}
    seats = long_seats(cylinders, {"body:2": "env_a", "body:3": "env_b"})
    assert [(s.bearing, s.run) for s in seats] == [("env_a", 62.0)]  # the faces 0-60 and 60-62 are one run


def test_a_cylinder_split_in_two_halves_reads_as_one_run() -> None:
    half_a = _face(10, "body:1", 12.5, 0, 120)
    half_b = _face(11, "body:1", 12.5, 0, 120)
    half_a = half_a.model_copy(update={"area_mm2": half_a.area_mm2 / 2})
    half_b = half_b.model_copy(update={"area_mm2": half_b.area_mm2 / 2})
    cylinders = {"body:1": _cylinders("body:1", half_a, half_b), "body:2": _ring("body:2", 25, 52, 20, 15)}
    seats = long_seats(cylinders, {"body:2": "env_a"})
    assert [(s.run, s.diameter) for s in seats] == [(120.0, 25.0)]


def test_the_run_is_measured_along_a_tilted_axis_too() -> None:
    axis = (0.6, 0.0, 0.8)
    face = _face(10, "body:1", 12.5, 0, 100).model_copy(
        update={"axis": Axis(origin=(0, 0, 0), direction=axis), "center": (0.0, 0.0, 0.0)}
    )
    face = face.model_copy(update={"bbox": BoundingBox(min=(-40, -12.5, -50), max=(40, 12.5, 50))})
    bearing = _face(1, "body:2", 12.5, 0, 15).model_copy(
        update={"axis": Axis(origin=(0, 0, 0), direction=axis), "center": (0.0, 0.0, 0.0)}
    )
    outside = bearing.model_copy(
        update={"handle": FaceHandle(id="face:2"), "radius": 26.0, "area_mm2": 2 * 3.141592653589793 * 26.0 * 15}
    )
    cylinders = {"body:1": _cylinders("body:1", face), "body:2": _cylinders("body:2", bearing, outside)}
    seats = long_seats(cylinders, {"body:2": "env_a"})
    assert len(seats) == 1 and seats[0].run == pytest.approx(100.0)


def test_a_shaft_of_another_diameter_is_not_the_seat() -> None:
    shaft = _cylinders("body:1", _face(3, "body:1", 10, 0, 120))
    cylinders = {"body:1": shaft, "body:2": _ring("body:2", 25, 52, 20, 15)}
    assert long_seats(cylinders, {"body:2": "env_a"}) == []


def test_a_bearing_without_a_bore_is_not_seen() -> None:
    """A bearing drawn as a solid disc has no bore to compare: the alert says nothing (a documented limit)."""
    disc = _cylinders("body:2", _face(1, "body:2", 26, 20, 35))
    shaft = _cylinders("body:1", _face(3, "body:1", 12.5, 0, 120))
    assert long_seats({"body:1": shaft, "body:2": disc}, {"body:2": "env_a"}) == []


def test_a_bearing_on_a_short_sleeve_over_a_plain_shaft_does_not_fire() -> None:
    """The ``not_when`` of the entry: the ring sits on a sleeve whose outside is the bore, not on the shaft (D20). The
    sleeve is as long as the ring plus its nut thread. A sleeve longer than twice the ring would fire: it cannot be
    told from a shaft."""
    shaft = _cylinders("body:1", _face(3, "body:1", 10, 0, 120))
    sleeve = _cylinders("body:4", _face(4, "body:4", 12.5, 20, 40), _face(5, "body:4", 10, 20, 40))
    cylinders = {"body:1": shaft, "body:4": sleeve, "body:2": _ring("body:2", 25, 52, 22, 15)}
    assert long_seats(cylinders, {"body:2": "env_a"}) == []


def test_the_line_says_what_was_measured() -> None:
    line = bearing_seats.line_for([bearing_seats.Seat("env_a", "shaft", 25.0, 120.0, 15.0)])
    assert line.startswith(MEMORY_LINE)
    assert "env_a (width 15 mm)" in line and "shaft" in line and "120 mm in a row of Ø25" in line
    assert "\n" not in line and len(line) < 260


# ---- through the tools (fake CAD) -----------------------------------------------------------------------------------


@pytest.fixture
def base_call(tmp_path: Path) -> Caller:
    settings = Settings(adapter="fake", workspace=tmp_path, home=tmp_path / "home", base_memory=True)
    return Caller(make_session(settings, FakeAdapter()))


def _cylinder(call: Caller, step: str, diameter: float, z: float, height: float, **extra: Any) -> str:
    return call.ok(
        "op",
        name="cylinder",
        id=step,
        params={"diameter_mm": diameter, "height_mm": height, "origin": [0, 0, z], **extra},
    )


def _ring_step(call: Caller, step: str, z: float, **extra: Any) -> str:
    """A bearing envelope: D52 outside, D25 bore, 15 wide."""
    _cylinder(call, step, 52, z, 15, **extra)
    _cylinder(call, f"{step}_bore", 25, z, 15)
    return call.ok("op", name="boolean_subtract", id=f"{step}_ring", params={"target": step, "tools": [f"{step}_bore"]})


def _all(call: Caller, *texts: str) -> str:
    return "\n".join(texts)


def test_the_base_set_has_the_entry_and_it_is_not_pushed_by_a_role_or_an_op() -> None:
    items = {i.entry.id: i.entry for i in MemoryStore(base_dir=base_memory_dir()).scope_entries(Scope.base)}
    entry = items[ENTRY_ID]
    assert entry.type.value == "practice" and entry.sector == "power-transmission"
    assert entry.when is None and entry.sources and entry.not_when
    assert "sleeve" in entry.not_when and "needle" in entry.not_when and "ground" in entry.not_when


def test_a_straight_shaft_with_a_bearing_envelope_adds_the_memory_line(base_call: Caller) -> None:
    call = base_call
    call.ok("doc", action="new", name="drive")
    _cylinder(call, "shaft", 25, 0, 120)
    text = _ring_step(call, "env_6205", 20, role="comercial")
    assert MEMORY_LINE in text
    line = next(row for row in text.splitlines() if row.startswith(MEMORY_LINE))
    assert "env_6205 (width 15 mm)" in line and "on shaft" in line and "120 mm in a row of Ø25" in line


def test_the_line_is_not_repeated_while_nothing_changes(base_call: Caller) -> None:
    call = base_call
    call.ok("doc", action="new", name="drive")
    _cylinder(call, "shaft", 25, 0, 120)
    assert MEMORY_LINE in _ring_step(call, "env_6205", 20, role="comercial")
    assert MEMORY_LINE not in _cylinder(call, "boss", 10, 200, 5)


def test_two_bearings_on_one_run_share_one_line(base_call: Caller) -> None:
    call = base_call
    call.ok("doc", action="new", name="drive")
    _cylinder(call, "shaft", 25, 0, 120)
    _ring_step(call, "env_a", 20, role="comercial")
    text = _ring_step(call, "env_b", 85, role="comercial")
    lines = [row for row in text.splitlines() if row.startswith(MEMORY_LINE)]
    assert len(lines) == 1 and "env_a (width 15 mm) and env_b (width 15 mm) sit" in lines[0]


def test_a_stepped_shaft_adds_no_line(base_call: Caller) -> None:
    call = base_call
    call.ok("doc", action="new", name="drive")
    _cylinder(call, "end_a", 20, 0, 20)
    _cylinder(call, "seat_a", 25, 20, 17)
    _cylinder(call, "shoulder", 30, 37, 48)
    _cylinder(call, "seat_b", 25, 85, 17)
    _cylinder(call, "end_b", 20, 102, 18)
    first = _ring_step(call, "env_a", 20, role="comercial")
    second = _ring_step(call, "env_b", 87, role="comercial")
    assert "memoria:" not in first and "memoria:" not in second


def test_a_bearing_is_known_by_its_name_without_a_role(base_call: Caller) -> None:
    call = base_call
    call.ok("doc", action="new", name="drive")
    _cylinder(call, "shaft", 25, 0, 120)
    assert MEMORY_LINE in _ring_step(call, "rodamiento_6205", 20)


def test_a_ring_that_is_neither_commercial_nor_named_as_a_bearing_is_left_alone(base_call: Caller) -> None:
    call = base_call
    call.ok("doc", action="new", name="drive")
    _cylinder(call, "shaft", 25, 0, 120)
    assert "memoria:" not in _ring_step(call, "spacer", 20)


def test_the_declared_sector_does_not_hide_a_measured_alert(base_call: Caller) -> None:
    call = base_call
    call.ok("memory", action="brief", sector="chapa metálica")
    call.ok("doc", action="new", name="drive")
    _cylinder(call, "shaft", 25, 0, 120)
    assert MEMORY_LINE in _ring_step(call, "env_6205", 20, role="comercial")


def test_a_concept_model_gets_no_alert(base_call: Caller) -> None:
    call = base_call
    call.ok("doc", action="new", name="drive")
    call.ok("doc", action="set_purpose", purpose="sketch")
    _cylinder(call, "shaft", 25, 0, 120)
    assert "memoria:" not in _ring_step(call, "env_6205", 20, role="comercial")


def test_an_entry_of_the_engineer_that_replaces_it_silences_the_alert(base_call: Caller) -> None:
    call = base_call
    settings = call.session.settings
    store = MemoryStore(project_dir=settings.workspace / "memory", base_dir=base_memory_dir())
    base = store.get(ENTRY_ID)
    assert base is not None
    store.save(base.entry.model_copy(update={"status": EntryStatus.inactive}), Scope.project)
    call.ok("doc", action="new", name="drive")
    _cylinder(call, "shaft", 25, 0, 120)
    assert "memoria:" not in _ring_step(call, "env_6205", 20, role="comercial")


def test_a_failing_read_never_fails_the_op(base_call: Caller, monkeypatch: pytest.MonkeyPatch) -> None:
    call = base_call
    call.ok("doc", action="new", name="drive")
    _cylinder(call, "shaft", 25, 0, 120)

    def broken(request: Any) -> Any:
        raise RuntimeError("the CAD went away")

    monkeypatch.setattr(call.session.adapter, "faces_of", broken)
    text = _cylinder(call, "env_6205", 52, 20, 15, role="comercial")
    assert "ok: env_6205 cylinder" in text and "memoria:" not in text


def test_editing_the_shaft_shorter_gives_a_new_reading(base_call: Caller) -> None:
    call = base_call
    call.ok("doc", action="new", name="drive")
    _cylinder(call, "shaft", 25, 0, 120)
    assert "120 mm in a row" in _ring_step(call, "env_6205", 2, role="comercial")
    text = call.ok("op", edit="shaft", params={"height_mm": 90})
    assert MEMORY_LINE in text and "90 mm in a row" in text
    short = call.ok("op", edit="shaft", params={"height_mm": 28})  # the seat and a little more: 28 < 2 x 15
    assert "memoria:" not in short
