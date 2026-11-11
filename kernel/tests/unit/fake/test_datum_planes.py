"""FakeAdapter datum_plane / datum_axis / sketch-on-datum, straight through the contract."""

import pytest

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import (
    AdapterError,
    Axis,
    DatumAxisRequest,
    DatumPlaneHandle,
    DatumPlaneRequest,
    DatumPlaneTarget,
    Empty,
    ErrorCode,
    MassRequest,
    PlaneName,
    RectEntity,
    SketchRequest,
    frame_for_plane,
)
from cotaforge.verification import datum
from tests.unit.fake.conftest import Session

FRAME = datum.plane_offset(frame_for_plane(PlaneName.XZ), 7.0)
RECT = RectEntity(name="r", center=(0.0, 0.0), width=4.0, height=2.0)


def test_datum_plane_echoes_the_pose_and_assigns_sequential_ids(session: Session) -> None:
    session.new_part()
    first = session.adapter.datum_plane(DatumPlaneRequest(operation_id="d1", how="offset", frame=FRAME))
    second = session.adapter.datum_plane(DatumPlaneRequest(operation_id="d2", how="offset", frame=FRAME))
    assert first.frame == FRAME and first.rebuild_ok is True
    assert (first.datum.id, second.datum.id) == ("datum_plane:1", "datum_plane:2")


def test_datum_axis_echoes_the_axis(session: Session) -> None:
    session.new_part()
    axis = Axis(origin=(1.0, 2.0, 3.0), direction=(0.0, 0.0, 1.0))
    result = session.adapter.datum_axis(DatumAxisRequest(operation_id="a", how="two_points", axis=axis))
    assert result.axis == axis
    assert result.datum.id == "datum_axis:1"


def test_datums_never_touch_mass_bbox_feature_tree_or_undo(session: Session) -> None:
    session.new_part()
    session.adapter.datum_plane(DatumPlaneRequest(operation_id="d1", how="offset", frame=FRAME))
    assert session.adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == 0.0
    assert session.adapter.feature_tree(Empty(operation_id="t")).nodes == ()
    assert session.adapter.undo_last(Empty(operation_id="u")).reverted is None  # a datum is not a feature


def test_sketch_on_a_datum_uses_its_frame(session: Session) -> None:
    session.new_part()
    handle = session.adapter.datum_plane(DatumPlaneRequest(operation_id="d1", how="offset", frame=FRAME)).datum
    target = DatumPlaneTarget(handle=handle, frame=FRAME)
    result = session.adapter.sketch(SketchRequest(operation_id="s", on=target, entities=(RECT,)))
    assert result.frame == FRAME


def test_sketch_on_an_unknown_datum_is_an_invalid_reference(session: Session) -> None:
    session.new_part()
    target = DatumPlaneTarget(handle=DatumPlaneHandle(id="datum_plane:9"), frame=FRAME)
    with pytest.raises(AdapterError) as info:
        session.adapter.sketch(SketchRequest(operation_id="s", on=target, entities=(RECT,)))
    assert info.value.info.code is ErrorCode.invalid_reference


def test_sketch_with_a_frame_that_differs_from_the_datum_is_rejected(session: Session) -> None:
    session.new_part()
    handle = session.adapter.datum_plane(DatumPlaneRequest(operation_id="d1", how="offset", frame=FRAME)).datum
    other = datum.plane_offset(FRAME, 1.0)
    with pytest.raises(AdapterError) as info:
        session.adapter.sketch(
            SketchRequest(operation_id="s", on=DatumPlaneTarget(handle=handle, frame=other), entities=(RECT,))
        )
    assert info.value.info.code is ErrorCode.invalid_request


def test_a_datum_plane_needs_an_active_document() -> None:
    with pytest.raises(AdapterError) as info:
        FakeAdapter().datum_plane(DatumPlaneRequest(operation_id="d", how="offset", frame=FRAME))
    assert info.value.info.code is ErrorCode.no_active_document


def test_undo_rolls_back_the_datums_created_after_the_reverted_feature(session: Session) -> None:
    """Like the FreeCAD worker (whose snapshot restore drops them): undoing a feature leaves the datums that existed
    when it started and forgets the ones made afterwards."""
    session.new_part()
    before = session.adapter.datum_plane(DatumPlaneRequest(operation_id="d1", how="offset", frame=FRAME)).datum
    session.adapter.datum_axis(
        DatumAxisRequest(operation_id="a1", how="two_points", axis=Axis(origin=(0, 0, 0), direction=(0, 0, 1)))
    )
    session.plate()
    after = session.adapter.datum_plane(DatumPlaneRequest(operation_id="d2", how="offset", frame=FRAME)).datum
    session.adapter.datum_axis(
        DatumAxisRequest(operation_id="a2", how="two_points", axis=Axis(origin=(0, 0, 0), direction=(0, 1, 0)))
    )
    assert session.adapter.undo_last(Empty(operation_id="u")).reverted is not None
    doc = session.adapter._active
    assert doc is not None
    assert set(doc.datum_planes) == {before.id} and set(doc.datum_axes) == {"datum_axis:1"}
    target = DatumPlaneTarget(handle=after, frame=FRAME)
    with pytest.raises(AdapterError) as info:
        session.adapter.sketch(SketchRequest(operation_id="s", on=target, entities=(RECT,)))
    assert info.value.info.code is ErrorCode.invalid_reference
