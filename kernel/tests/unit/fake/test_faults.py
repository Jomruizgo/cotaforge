"""Fault injection: every FaultKind's observable behaviour and fired_faults()."""

import math

import pytest

from cotaforge.adapters.fake import Fault, FaultKind, FaultPlan
from cotaforge.contract import (
    AdapterError,
    ComponentRequest,
    Empty,
    EndCondition,
    ErrorCode,
    FaceRequest,
    FeatureHandle,
    HoleRequest,
    HoleStandard,
    MassRequest,
    MateCoincidentRequest,
    MateDistanceRequest,
    OperationResult,
    PlaneName,
    RectEntity,
    SketchRequest,
    SketchStatusRequest,
)
from tests.unit.fake.conftest import Session, face_with_normal, session_with_faults
from tests.unit.fake.test_assembly import _assembly

Z = (0.0, 0.0, 1.0)
NZ = (0.0, 0.0, -1.0)
HOLE_VOLUME = math.pi * 3.3**2 * 8.0


def _hole(session: Session, op: str = "hole_1", *, blind: bool = False) -> OperationResult:
    plate = session.plate()
    assert plate.feature is not None
    top = face_with_normal(session.faces(plate.feature), Z)
    return session.adapter.hole(
        HoleRequest(
            operation_id=op,
            face=top.handle,
            position=(0.0, 0.0),
            standard=HoleStandard.iso_clearance,
            size="M6",
            diameter_mm=6.6,
            end_condition=EndCondition.blind if blind else EndCondition.through_all,
            depth_mm=8.0 if blind else None,
        )
    )


def test_fault_plan_parses_from_bench_dict() -> None:
    plan = FaultPlan.model_validate(
        {
            "faults": [
                {"operation_id": "hole_1", "attempt": 1, "kind": "rebuild_failed"},
                {"operation_id": "holes", "kind": "raise_error"},
            ]
        }
    )
    assert plan.faults[0].kind is FaultKind.rebuild_failed
    assert plan.faults[1].attempt is None
    assert plan.faults[1].error_code is ErrorCode.geometry_failed
    assert plan.faults[1].recoverable is True


def test_raise_error_first_attempt_only_leaves_document_untouched_and_retry_succeeds() -> None:
    session = session_with_faults(
        FaultPlan(
            faults=(Fault(operation_id="hole_1", attempt=1, kind=FaultKind.raise_error, error_code=ErrorCode.timeout),)
        )
    )
    session.new_part()
    plate = session.plate()
    assert plate.feature is not None
    before = session.adapter.mass_properties(MassRequest(operation_id="m"))
    top = face_with_normal(session.faces(plate.feature), Z)
    request = HoleRequest(
        operation_id="hole_1",
        face=top.handle,
        position=(0.0, 0.0),
        standard=HoleStandard.simple,
        diameter_mm=6.6,
        end_condition=EndCondition.through_all,
    )
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.hole(request)
    assert excinfo.value.info.code is ErrorCode.timeout
    assert excinfo.value.info.operation_id == "hole_1"
    assert excinfo.value.info.recoverable is True
    assert session.adapter.mass_properties(MassRequest(operation_id="m")) == before
    assert len(session.adapter.feature_tree(Empty(operation_id="t")).nodes) == 1
    result = session.adapter.hole(request)
    assert result.rebuild_ok
    assert session.adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(
        48000 - HOLE_VOLUME
    )
    assert session.adapter.fired_faults() == (("hole_1", 1, FaultKind.raise_error),)


def test_raise_error_non_recoverable_code() -> None:
    session = session_with_faults(
        FaultPlan(
            faults=(
                Fault(
                    operation_id="p",
                    kind=FaultKind.raise_error,
                    error_code=ErrorCode.invalid_request,
                    recoverable=False,
                ),
            )
        )
    )
    session.new_part()
    with pytest.raises(AdapterError) as excinfo:
        session.plate("p")
    assert excinfo.value.info.code is ErrorCode.invalid_request
    assert excinfo.value.info.recoverable is False


def test_rebuild_failed_creates_the_feature_and_undo_removes_it() -> None:
    session = session_with_faults(
        FaultPlan(faults=(Fault(operation_id="hole_1", attempt=1, kind=FaultKind.rebuild_failed),))
    )
    session.new_part()
    result = _hole(session)
    assert result.feature is not None
    assert result.rebuild_ok is False
    assert session.adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(
        48000 - HOLE_VOLUME
    )
    assert session.adapter.undo_last(Empty(operation_id="u")).reverted == result.feature
    assert session.adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(48000.0)
    assert session.adapter.fired_faults() == (("hole_1", 1, FaultKind.rebuild_failed),)


def test_wrong_volume_scales_the_primitive_and_undo_removes_it() -> None:
    session = session_with_faults(
        FaultPlan(faults=(Fault(operation_id="hole_1", kind=FaultKind.wrong_volume, volume_factor=0.5),))
    )
    session.new_part()
    result = _hole(session, blind=True)
    assert result.rebuild_ok is True
    assert session.adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(
        48000 - HOLE_VOLUME / 2
    )
    session.adapter.undo_last(Empty(operation_id="u"))
    assert session.adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(48000.0)


def test_wrong_face_count_adds_a_spurious_zero_area_face() -> None:
    session = session_with_faults(FaultPlan(faults=(Fault(operation_id="hole_1", kind=FaultKind.wrong_face_count),)))
    session.new_part()
    result = _hole(session)
    assert result.feature is not None
    assert len(result.created_faces) == 2
    faces = session.faces(result.feature).faces
    assert sum(1 for f in faces if f.area_mm2 == 0.0) == 1
    assert session.adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(
        48000 - HOLE_VOLUME
    )
    session.adapter.undo_last(Empty(operation_id="u"))
    assert len(session.faces(session.adapter.feature_tree(Empty(operation_id="t")).nodes[0].handle).faces) == 6


def test_canary_fail_flips_the_canary_from_the_moment_it_fires() -> None:
    session = session_with_faults(
        FaultPlan(
            faults=(
                Fault(operation_id="hole_1", kind=FaultKind.canary_fail),
                Fault(operation_id="hole_1", kind=FaultKind.raise_error),
            )
        )
    )
    session.new_part()
    assert session.adapter.canary(Empty(operation_id="c")).healthy is True
    with pytest.raises(AdapterError):
        _hole(session)
    canary = session.adapter.canary(Empty(operation_id="c"))
    assert canary.healthy is False
    assert canary.checks["render"] is False
    assert session.adapter.fired_faults() == (
        ("hole_1", 1, FaultKind.canary_fail),
        ("hole_1", 1, FaultKind.raise_error),
    )


def test_wrong_side_on_coincident_mate_until_flip() -> None:
    session = session_with_faults(FaultPlan(faults=(Fault(operation_id="coin", kind=FaultKind.wrong_side),)))
    block_c, pin_c = _assembly(session)
    assert pin_c.component is not None
    block_top = face_with_normal(session.faces(block_c.feature), Z)  # type: ignore[arg-type]
    pin_bottom = face_with_normal(session.faces(pin_c.feature), NZ)  # type: ignore[arg-type]
    session.adapter.mate_coincident(MateCoincidentRequest(operation_id="coin", a=block_top.handle, b=pin_bottom.handle))
    box = session.adapter.component_bounding_box(ComponentRequest(operation_id="q", component=pin_c.component))
    assert box.min[2] == pytest.approx(-20.0) and box.max[2] == pytest.approx(20.0)  # other side of z = 20
    session.adapter.undo_last(Empty(operation_id="u"))
    session.adapter.mate_coincident(
        MateCoincidentRequest(operation_id="coin", a=block_top.handle, b=pin_bottom.handle, flip=True)
    )
    box = session.adapter.component_bounding_box(ComponentRequest(operation_id="q", component=pin_c.component))
    assert box.min[2] == pytest.approx(20.0) and box.max[2] == pytest.approx(60.0)
    assert session.adapter.fired_faults() == (("coin", 1, FaultKind.wrong_side), ("coin", 2, FaultKind.wrong_side))


def test_wrong_side_on_distance_mate_uses_minus_side() -> None:
    session = session_with_faults(FaultPlan(faults=(Fault(operation_id="dist", kind=FaultKind.wrong_side),)))
    block_c, pin_c = _assembly(session)
    assert pin_c.component is not None
    block_top = face_with_normal(session.faces(block_c.feature), Z)  # type: ignore[arg-type]
    pin_bottom = face_with_normal(session.faces(pin_c.feature), NZ)  # type: ignore[arg-type]
    session.adapter.mate_distance(
        MateDistanceRequest(operation_id="dist", a=block_top.handle, b=pin_bottom.handle, distance_mm=5.0)
    )
    box = session.adapter.component_bounding_box(ComponentRequest(operation_id="q", component=pin_c.component))
    assert box.min[2] == pytest.approx(15.0)
    session.adapter.undo_last(Empty(operation_id="u"))
    session.adapter.mate_distance(
        MateDistanceRequest(operation_id="dist", a=block_top.handle, b=pin_bottom.handle, distance_mm=5.0, flip=True)
    )
    box = session.adapter.component_bounding_box(ComponentRequest(operation_id="q", component=pin_c.component))
    assert box.min[2] == pytest.approx(25.0)


def test_underdefined_sketch_forces_rebuild_ok_false_then_clears_on_the_next_attempt() -> None:
    """The fault only supplies the failing signal (``rebuild_ok=False``); the sketch is stored exactly as
    the request declares it, so ``sketch_status`` reporting ``dof > 0`` is a property of the request (no
    ``named_dimensions``), not of the fault. On the second attempt (the fault fires once), ``rebuild_ok``
    is ``True`` again -- the level-1 signal clears, independent of whether the sketch was ever repaired."""
    session = session_with_faults(
        FaultPlan(faults=(Fault(operation_id="base_sketch", attempt=1, kind=FaultKind.underdefined_sketch),))
    )
    session.new_part()
    request = SketchRequest(
        operation_id="base_sketch",
        on=PlaneName.XY,
        entities=(RectEntity(name="outline", center=(0.0, 0.0), width=40.0, height=40.0),),
    )
    first = session.adapter.sketch(request)
    assert first.rebuild_ok is False
    status = session.adapter.sketch_status(
        SketchStatusRequest(operation_id="q", sketch=FeatureHandle(id=first.sketch.id))
    )
    assert status.dof > 0 and status.fully_constrained is False
    second = session.adapter.sketch(request)
    assert second.rebuild_ok is True
    assert session.adapter.fired_faults() == (("base_sketch", 1, FaultKind.underdefined_sketch),)


def test_dangling_reference_renames_the_referenced_face_and_raises_invalid_reference() -> None:
    session = session_with_faults(
        FaultPlan(
            faults=(Fault(operation_id="hole_1", attempt=1, kind=FaultKind.dangling_reference, recoverable=False),)
        )
    )
    session.new_part()
    plate = session.plate()
    assert plate.feature is not None
    top = face_with_normal(session.faces(plate.feature), Z)
    request = HoleRequest(
        operation_id="hole_1",
        face=top.handle,
        position=(0.0, 0.0),
        standard=HoleStandard.simple,
        diameter_mm=6.6,
        end_condition=EndCondition.through_all,
    )
    with pytest.raises(AdapterError) as excinfo:
        session.adapter.hole(request)
    assert excinfo.value.info.code is ErrorCode.invalid_reference
    assert excinfo.value.info.recoverable is False
    with pytest.raises(AdapterError) as stale:
        session.adapter.face_info(FaceRequest(operation_id="q", face=top.handle))
    assert stale.value.info.code is ErrorCode.invalid_reference
    renamed = face_with_normal(session.faces(plate.feature), Z)
    assert renamed.handle.id != top.handle.id
    result = session.adapter.hole(request.model_copy(update={"face": renamed.handle}))
    assert result.rebuild_ok is True
    assert session.adapter.fired_faults() == (("hole_1", 1, FaultKind.dangling_reference),)


def test_times_limits_how_often_a_fault_fires_and_attempts_are_per_operation() -> None:
    session = session_with_faults(FaultPlan(faults=(Fault(operation_id="p", kind=FaultKind.raise_error, times=2),)))
    session.new_part()
    with pytest.raises(AdapterError):
        session.plate("p")
    with pytest.raises(AdapterError):
        session.plate("p")
    session.plate("p")
    session.plate("other")
    assert session.adapter.fired_faults() == (("p", 1, FaultKind.raise_error), ("p", 2, FaultKind.raise_error))
