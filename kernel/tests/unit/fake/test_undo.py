"""Undo round trips: fingerprint(before) == fingerprint(after op + undo) for every builder."""

from collections.abc import Callable

import pytest

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.adapters.fake.model import FakeDocument
from cotaforge.contract import (
    Axis,
    BooleanKind,
    BooleanRequest,
    BoxRequest,
    ChamferRequest,
    CircularPatternRequest,
    CutRequest,
    CylinderRequest,
    Empty,
    EndCondition,
    FaceRequest,
    FilletRequest,
    HoleRequest,
    HoleStandard,
    LinearPatternRequest,
    MassRequest,
    MateAlignment,
    MateCoincidentRequest,
    MateConcentricRequest,
    MateDistanceRequest,
    MoveBodyRequest,
    OperationResult,
    PlaneName,
    RevolveRequest,
    SketchHandle,
    Transform,
)
from tests.unit.fake.conftest import Session, cylindrical_faces, face_with_normal
from tests.unit.fake.test_assembly import _assembly, _mate_pin_into_bore

X = (1.0, 0.0, 0.0)
Z = (0.0, 0.0, 1.0)
NZ = (0.0, 0.0, -1.0)


def _active(session: Session) -> FakeDocument:
    doc = session.adapter._active  # noqa: SLF001 - the fingerprint is an internal test hook by design (7.7)
    assert doc is not None
    return doc


def _round_trip(session: Session, operation: Callable[[], OperationResult]) -> None:
    doc = _active(session)
    before = doc.fingerprint()
    mass_before = session.adapter.mass_properties(MassRequest(operation_id="m"))
    features_before = len(doc.features)
    result = operation()
    assert result.feature is not None
    assert doc.fingerprint() != before
    reverted = session.adapter.undo_last(Empty(operation_id="u"))
    assert reverted.reverted == result.feature
    assert doc.fingerprint() == before
    assert len(doc.features) == features_before
    assert session.adapter.mass_properties(MassRequest(operation_id="m")) == mass_before


def _hole(session: Session, face_handle: object, op: str = "hole", **kwargs: object) -> OperationResult:
    fields: dict[str, object] = {
        "operation_id": op,
        "face": face_handle,
        "position": (0.0, 0.0),
        "standard": HoleStandard.simple,
        "diameter_mm": 6.6,
        "end_condition": EndCondition.through_all,
    }
    fields.update(kwargs)
    return session.adapter.hole(HoleRequest(**fields))  # type: ignore[arg-type]


def test_undo_extrude_on_plane_and_on_face(session: Session) -> None:
    session.new_part()
    sketch, _ = session.rect_sketch("s", PlaneName.XY, (50.0, 30.0), 100.0, 60.0)
    _round_trip(session, lambda: session.extrude("plate", sketch, 8.0))
    plate = session.extrude("plate", sketch, 8.0)
    top = face_with_normal(session.faces(plate.feature), Z)  # type: ignore[arg-type]
    boss, _ = session.rect_sketch("s2", top.handle, (0.0, 0.0), 10.0, 10.0)
    _round_trip(session, lambda: session.extrude("boss", boss, 5.0))
    circle = session.circle_sketch("s3", top.handle, (20.0, 0.0), 10.0)
    _round_trip(session, lambda: session.extrude("pin", circle, 5.0))


def test_undo_cuts_and_holes(session: Session) -> None:
    session.new_part()
    plate = session.plate()
    top = face_with_normal(session.faces(plate.feature), Z)  # type: ignore[arg-type]
    pocket, _ = session.rect_sketch("s", top.handle, (0.0, 0.0), 10.0, 10.0)
    _round_trip(
        session,
        lambda: session.adapter.cut(CutRequest(operation_id="pocket", sketch=SketchHandle(id=pocket), depth_mm=3.0)),
    )
    _round_trip(
        session,
        lambda: session.adapter.cut(
            CutRequest(operation_id="through", sketch=SketchHandle(id=pocket), end_condition=EndCondition.through_all)
        ),
    )
    _round_trip(session, lambda: _hole(session, top.handle))
    _round_trip(session, lambda: _hole(session, top.handle, end_condition=EndCondition.blind, depth_mm=4.0))


def test_undo_patterns(session: Session) -> None:
    session.new_part()
    plate = session.plate()
    top = face_with_normal(session.faces(plate.feature), Z)  # type: ignore[arg-type]
    hole = _hole(session, top.handle, position=(-40.0, -20.0))
    assert hole.feature is not None
    _round_trip(
        session,
        lambda: session.adapter.linear_pattern(
            LinearPatternRequest(
                operation_id="lp",
                features=(hole.feature,),  # type: ignore[arg-type]
                direction=X,
                spacing_mm=80.0,
                count=2,
                direction2=(0.0, 1.0, 0.0),
                spacing2_mm=40.0,
                count2=2,
            )
        ),
    )
    _round_trip(
        session,
        lambda: session.adapter.circular_pattern(
            CircularPatternRequest(
                operation_id="cp",
                features=(hole.feature,),  # type: ignore[arg-type]
                axis=Axis(origin=(50.0, 30.0, 0.0), direction=Z),
                count=4,
            )
        ),
    )


def test_undo_revolve(session: Session) -> None:
    session.new_part()
    profile = [(0.0, 0.0), (40.0, 0.0), (40.0, 6.0), (20.0, 6.0), (20.0, 10.0), (0.0, 10.0)]
    sketch = session.polygon_sketch("s", PlaneName.XY, profile)
    _round_trip(
        session,
        lambda: session.adapter.revolve(
            RevolveRequest(
                operation_id="shaft", sketch=SketchHandle(id=sketch), axis=Axis(origin=(0, 0, 0), direction=X)
            )
        ),
    )


def test_undo_chamfer_and_fillet_restore_removed_edges(session: Session) -> None:
    session.new_part()
    plate = session.plate()
    faces = session.faces(plate.feature)  # type: ignore[arg-type]
    top, side = face_with_normal(faces, Z), face_with_normal(faces, X)
    edge = session.edges_between(top, side)[0]
    _round_trip(
        session,
        lambda: session.adapter.chamfer(
            ChamferRequest(
                operation_id="ch", edges=(edge.handle,), distance1_mm=2.0, distance2_mm=3.0, distance1_face=top.handle
            )
        ),
    )
    assert session.edges_between(top, side)[0].handle == edge.handle
    _round_trip(
        session, lambda: session.adapter.fillet(FilletRequest(operation_id="f", edges=(edge.handle,), radius_mm=2.0))
    )
    assert len(session.adapter.edges_of(FaceRequest(operation_id="q", face=top.handle)).edges) == 4


def test_undo_box_cylinder_booleans_and_move(session: Session) -> None:
    session.new_part()
    _round_trip(session, lambda: session.adapter.box(BoxRequest(operation_id="box", size=(100.0, 60.0, 20.0))))
    box = session.adapter.box(BoxRequest(operation_id="box", size=(100.0, 60.0, 20.0)))
    cyl_request = CylinderRequest(
        operation_id="cyl", diameter_mm=30.0, height_mm=20.0, axis=Axis(origin=(50, 30, 0), direction=Z)
    )
    _round_trip(session, lambda: session.adapter.cylinder(cyl_request))
    cyl = session.adapter.cylinder(cyl_request)
    for kind in (BooleanKind.subtract, BooleanKind.union):
        _round_trip(
            session,
            lambda kind=kind: session.adapter.boolean(
                BooleanRequest(operation_id="bool", kind=kind, target=box.created_bodies[0], tools=cyl.created_bodies)
            ),
        )
    other = session.adapter.box(
        BoxRequest(operation_id="box2", size=(10.0, 10.0, 10.0), transform=Transform(origin=(5.0, 5.0, 5.0)))
    )
    _round_trip(
        session,
        lambda: session.adapter.boolean(
            BooleanRequest(
                operation_id="int", kind=BooleanKind.intersect, target=box.created_bodies[0], tools=other.created_bodies
            )
        ),
    )
    _round_trip(
        session,
        lambda: session.adapter.move_body(
            MoveBodyRequest(
                operation_id="mv",
                body=box.created_bodies[0],
                transform=Transform.from_axis_angle(Z, 90.0, origin=(10.0, 0.0, 0.0)),
            )
        ),
    )


def test_undo_mates_and_components() -> None:
    session = Session(FakeAdapter())
    block_c, pin_c = _assembly(session)
    block_faces = session.faces(block_c.feature)  # type: ignore[arg-type]
    pin_faces = session.faces(pin_c.feature)  # type: ignore[arg-type]
    bore = cylindrical_faces(block_faces)[0]
    pin_side = cylindrical_faces(pin_faces)[0]
    _round_trip(
        session,
        lambda: session.adapter.mate_concentric(
            MateConcentricRequest(
                operation_id="c", a=bore.handle, b=pin_side.handle, alignment=MateAlignment.anti_aligned
            )
        ),
    )
    block_top = face_with_normal(block_faces, Z)
    pin_bottom = face_with_normal(pin_faces, NZ)
    _round_trip(
        session,
        lambda: session.adapter.mate_coincident(
            MateCoincidentRequest(operation_id="co", a=block_top.handle, b=pin_bottom.handle)
        ),
    )
    _round_trip(
        session,
        lambda: session.adapter.mate_distance(
            MateDistanceRequest(operation_id="d", a=block_top.handle, b=pin_bottom.handle, distance_mm=3.0)
        ),
    )
    _mate_pin_into_bore(session, block_c, pin_c)
    doc = _active(session)
    fingerprints = [doc.fingerprint()]
    for _ in range(5):
        session.adapter.undo_last(Empty(operation_id="u"))
        fingerprints.append(doc.fingerprint())
    assert len(set(fingerprints)) == 6
    assert doc.features == [] and doc.components == {}
    assert session.adapter.undo_last(Empty(operation_id="u")).reverted is None


def test_failed_operation_leaves_fingerprint_unchanged(session: Session) -> None:
    session.new_part()
    session.plate()
    doc = _active(session)
    sketch = session.circle_sketch("s", PlaneName.XY, (20.0, 20.0), 10.0)
    before = doc.fingerprint()
    with pytest.raises(Exception):  # noqa: B017 - any AdapterError; the point is the state afterwards
        session.adapter.revolve(
            RevolveRequest(operation_id="rev", sketch=SketchHandle(id=sketch), axis=Axis(origin=(0, 0, 0), direction=X))
        )
    assert doc.fingerprint() == before
