"""The rule of every feature: its sketch ends fully defined by dimensions. The fake models what the real adapters do:
a sketch the tool makes is always fully defined; the ``underdefined_sketch`` fault leaves one under defined, and a
feature built on it is refused with the reason code an external adapter uses."""

from collections.abc import Callable

import pytest

from cotaforge.adapters.fake import Fault, FaultKind, FaultPlan
from cotaforge.contract import (
    AdapterError,
    Axis,
    BaseFlangeRequest,
    CutRequest,
    ErrorCode,
    PlaneName,
    RectEntity,
    RevolveRequest,
    SketchHandle,
    SketchRequest,
    SweepRequest,
)
from tests.unit.fake.conftest import Session, session_with_faults


def _faulty_session() -> tuple[Session, str]:
    session = session_with_faults(
        FaultPlan(faults=(Fault(operation_id="loose", attempt=1, kind=FaultKind.underdefined_sketch),))
    )
    session.new_part()
    sketch, _ = session.rect_sketch("loose", PlaneName.XY, (0.0, 0.0), 40.0, 40.0)
    return session, sketch


def _uses(session: Session, sketch: str) -> dict[str, Callable[[], object]]:
    handle = SketchHandle(id=sketch)
    return {
        "extrude": lambda: session.extrude("pad", sketch, 5.0),
        "cut": lambda: session.adapter.cut(CutRequest(operation_id="pocket", sketch=handle, depth_mm=2.0)),
        "revolve": lambda: session.adapter.revolve(
            RevolveRequest(operation_id="turn", sketch=handle, axis=Axis(origin=(0, 0, 0), direction=(1, 0, 0)))
        ),
        "sweep": lambda: session.adapter.sweep(
            SweepRequest(operation_id="sweep", sketch=handle, path=((0.0, 0.0, 0.0), (0.0, 0.0, 30.0)))
        ),
        "base_flange": lambda: session.adapter.base_flange(
            BaseFlangeRequest(operation_id="sheet", sketch=handle, thickness_mm=2.0)
        ),
    }


@pytest.mark.parametrize("operation", ["extrude", "cut", "revolve", "sweep", "base_flange"])
def test_every_operation_that_builds_from_a_sketch_refuses_one_that_is_not_fully_defined(operation: str) -> None:
    session, sketch = _faulty_session()
    with pytest.raises(AdapterError) as caught:
        _uses(session, sketch)[operation]()
    info = caught.value.info
    assert info.code is ErrorCode.invalid_request and info.recoverable is False
    assert info.details["reason"] == "sketch_not_fully_defined"
    assert info.details["sketch"] == sketch
    assert int(info.details["dof"] or 0) >= 1
    assert "degree(s) of freedom" in info.message and "dimensions" in info.message


def test_a_refused_operation_leaves_the_document_as_it_was() -> None:
    session, sketch = _faulty_session()
    doc = session.adapter._active  # noqa: SLF001 - the fingerprint is an internal test hook by design
    assert doc is not None
    before = doc.fingerprint()
    with pytest.raises(AdapterError):
        session.extrude("pad", sketch, 5.0)
    assert doc.fingerprint() == before and not doc.features


def test_the_retry_of_the_sketch_after_the_fault_builds_normally() -> None:
    """The fault fires on attempt 1 only: the second sketch is fully defined and a feature is built on it."""
    session, first = _faulty_session()
    second = session.adapter.sketch(
        SketchRequest(
            operation_id="loose",
            on=PlaneName.XY,
            entities=(RectEntity(name="r", center=(0.0, 0.0), width=40.0, height=40.0),),
        )
    )
    assert second.rebuild_ok is True and second.sketch.id != first
    result = session.extrude("pad", second.sketch.id, 5.0)
    assert result.feature is not None and result.rebuild_ok


def test_sketches_the_tool_makes_are_fully_defined_without_any_dimension_named() -> None:
    """No fault: a bare rectangle is built on at once, as the real adapters complete the constraint set themselves."""
    session = session_with_faults(FaultPlan())
    session.new_part()
    sketch, _ = session.rect_sketch("s", PlaneName.XY, (0.0, 0.0), 30.0, 20.0)
    assert session.extrude("pad", sketch, 4.0).feature is not None
