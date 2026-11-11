"""The fake adapter and the new hole forms: a tapped hole carries its thread as feature metadata, and a hole on a
cylindrical face is refused (the fake's analytic bookkeeping cannot model the curved-wall exit)."""

import math

import pytest

from cotaforge.contract import (
    AdapterError,
    Empty,
    EndCondition,
    ErrorCode,
    HoleRequest,
    HoleStandard,
    HoleThread,
    MassRequest,
    PlaneName,
)
from tests.unit.fake.conftest import Session, cylindrical_faces, face_with_normal


def _volume(session: Session) -> float:
    return session.adapter.mass_properties(MassRequest(operation_id="mass")).volume_mm3


def test_a_hole_on_a_cylindrical_face_is_not_supported_and_changes_nothing(session: Session) -> None:
    session.new_part()
    sketch = session.circle_sketch("rod_sketch", PlaneName.XY, (0.0, 0.0), 40.0)
    rod = session.extrude("rod", sketch, 100.0)
    wall = cylindrical_faces(session.faces(rod.feature))[0]
    volume = _volume(session)
    with pytest.raises(AdapterError) as refused:
        session.adapter.hole(
            HoleRequest(
                operation_id="cross",
                face=wall.handle,
                position=(50.0, 0.0),
                standard=HoleStandard.simple,
                diameter_mm=6.0,
                end_condition=EndCondition.through_all,
            )
        )
    assert refused.value.info.code is ErrorCode.not_supported
    assert "cylindrical" in refused.value.info.message and "FreeCAD" in refused.value.info.message
    assert _volume(session) == pytest.approx(volume)
    assert len(session.adapter.feature_tree(Empty(operation_id="tree")).nodes) == 1  # only the rod


def test_a_tapped_hole_is_the_tap_drill_cylinder_and_keeps_its_thread_as_metadata(session: Session) -> None:
    session.new_part()
    plate = session.plate()
    top = face_with_normal(session.faces(plate.feature), (0.0, 0.0, 1.0))
    session.adapter.hole(
        HoleRequest(
            operation_id="tap",
            face=top.handle,
            position=(-40.0, -20.0),
            standard=HoleStandard.iso_tapped,
            size="M6",
            diameter_mm=5.0,
            end_condition=EndCondition.blind,
            depth_mm=6.0,
            thread=HoleThread(size="M6", depth_mm=4.0),
        )
    )
    assert _volume(session) == pytest.approx(100 * 60 * 8 - math.pi * 2.5**2 * 6.0)
    node = session.adapter.feature_tree(Empty(operation_id="tree")).nodes[-1]
    assert node.kind == "hole"
    assert node.parameters["thread_size"] == "M6" and node.parameters["thread_depth_mm"] == 4.0
    assert node.parameters["diameter_mm"] == 5.0
    assert "thread" not in node.parameters  # flattened, not a stringified dict


def test_a_hole_without_a_thread_has_no_thread_parameters(session: Session) -> None:
    session.new_part()
    plate = session.plate()
    top = face_with_normal(session.faces(plate.feature), (0.0, 0.0, 1.0))
    session.adapter.hole(
        HoleRequest(
            operation_id="plain",
            face=top.handle,
            position=(-40.0, -20.0),
            standard=HoleStandard.simple,
            diameter_mm=5.0,
            end_condition=EndCondition.through_all,
        )
    )
    node = session.adapter.feature_tree(Empty(operation_id="tree")).nodes[-1]
    assert not {key for key in node.parameters if key.startswith("thread")}
