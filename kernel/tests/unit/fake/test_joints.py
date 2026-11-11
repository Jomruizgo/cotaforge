"""Joints on the fake: fixed, revolute and slider between components (or a component and the ground), their value and
limits, undo, the refusal to move a jointed component by hand, a sweep with interference, and a save / reopen."""

import math
from pathlib import Path

import pytest

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import (
    AdapterError,
    AddJointRequest,
    Axis,
    BoundingBox,
    BoxRequest,
    ComponentHandle,
    ComponentRequest,
    DocumentHandle,
    DocumentKind,
    DocumentStamp,
    Empty,
    ErrorCode,
    InsertComponentRequest,
    JointRequest,
    JointType,
    OpenDocumentRequest,
    PlaceComponentRequest,
    SaveDocumentRequest,
    SetJointRequest,
    SweepJointRequest,
    Transform,
    joint_delta,
    sweep_values,
)
from tests.unit.fake.conftest import Session

X = Axis(origin=(0, 0, 0), direction=(1, 0, 0))
Y_THROUGH_TOP = Axis(origin=(0, 0, 10), direction=(0, 1, 0))


def _part(session: Session, name: str, size: tuple[float, float, float]) -> str:
    doc = session.new_part(name)
    session.adapter.box(BoxRequest(operation_id=f"{name}_box", size=size))
    return doc


def _insert(session: Session, op: str, part: str, origin: tuple[float, float, float]) -> ComponentHandle:
    result = session.adapter.insert_component(
        InsertComponentRequest(operation_id=op, part=DocumentHandle(id=part), transform=Transform(origin=origin))
    )
    assert result.component is not None
    return result.component


def _bbox(session: Session, component: ComponentHandle) -> BoundingBox:
    return session.adapter.component_bounding_box(ComponentRequest(operation_id="q", component=component))


def _gate(session: Session) -> tuple[ComponentHandle, ComponentHandle]:
    """A 10 mm cube (the gate) 100 mm from a 10 mm cube post, both in an assembly: the gate slides along +X."""
    gate_part = _part(session, "gate", (10, 10, 10))
    post_part = _part(session, "post", (10, 10, 10))
    session.new_part("asm", DocumentKind.assembly)
    return _insert(session, "gate", gate_part, (0, 0, 0)), _insert(session, "post", post_part, (100, 0, 0))


def _add(session: Session, op: str, b: ComponentHandle, **fields: object) -> object:
    return session.adapter.add_joint(AddJointRequest(operation_id=op, b=b, **fields))  # type: ignore[arg-type]


def test_a_slider_to_the_ground_moves_the_component_along_its_axis_and_remembers_the_value(session: Session) -> None:
    gate, _ = _gate(session)

    made = _add(session, "rail", gate, joint_type=JointType.slider, axis=X, min_value=0, max_value=200)
    moved = session.adapter.set_joint(
        SetJointRequest(operation_id="open", joint=made.joint.handle, value=40)  # type: ignore[attr-defined]
    )

    assert made.joint.value == 0 and made.joint.unit == "mm"  # type: ignore[attr-defined]
    assert moved.joint.value == 40 and moved.component == gate  # type: ignore[union-attr]
    assert _bbox(session, gate).min == pytest.approx((40, 0, 0))


def test_the_value_is_absolute_from_the_pose_the_joint_was_made_at(session: Session) -> None:
    gate, _ = _gate(session)
    made = _add(session, "rail", gate, joint_type=JointType.slider, axis=X)
    handle = made.joint.handle  # type: ignore[attr-defined]

    session.adapter.set_joint(SetJointRequest(operation_id="a", joint=handle, value=40))
    session.adapter.set_joint(SetJointRequest(operation_id="b", joint=handle, value=15))

    assert _bbox(session, gate).min == pytest.approx((15, 0, 0))
    assert session.adapter.joint_info(JointRequest(operation_id="q", joint=handle)).value == 15


def test_a_revolute_turns_the_component_about_the_axis_right_handed(session: Session) -> None:
    lid_part = _part(session, "lid", (100, 50, 5))
    session.new_part("asm", DocumentKind.assembly)
    lid = _insert(session, "lid", lid_part, (0, 0, 10))
    made = _add(session, "hinge", lid, joint_type=JointType.revolute, axis=Y_THROUGH_TOP)

    session.adapter.set_joint(SetJointRequest(operation_id="a", joint=made.joint.handle, value=90))  # type: ignore[attr-defined]

    # +90 about +Y turns the lid's +X (its length) into -Z: it hangs 100 mm below the hinge
    box = _bbox(session, lid)
    assert box.min == pytest.approx((0, 0, -90), abs=1e-6)
    assert box.max == pytest.approx((5, 50, 10), abs=1e-6)


def test_a_value_outside_the_limits_is_refused_and_nothing_moves(session: Session) -> None:
    gate, _ = _gate(session)
    made = _add(session, "rail", gate, joint_type=JointType.slider, axis=X, min_value=0, max_value=60)

    with pytest.raises(AdapterError) as info:
        session.adapter.set_joint(SetJointRequest(operation_id="far", joint=made.joint.handle, value=61))  # type: ignore[attr-defined]

    assert "outside the limits" in info.value.info.message and "[0, 60] mm" in info.value.info.message
    assert _bbox(session, gate).min == pytest.approx((0, 0, 0))


def test_the_limits_must_include_the_pose_the_joint_is_made_at(session: Session) -> None:
    gate, _ = _gate(session)

    with pytest.raises(AdapterError, match="include 0"):
        _add(session, "rail", gate, joint_type=JointType.slider, axis=X, min_value=5, max_value=60)


def test_a_slider_needs_an_axis_and_a_fixed_joint_has_none(session: Session) -> None:
    gate, _ = _gate(session)

    with pytest.raises(AdapterError, match="needs an axis"):
        _add(session, "rail", gate, joint_type=JointType.slider)
    with pytest.raises(AdapterError, match="no axis"):
        _add(session, "weld", gate, joint_type=JointType.fixed, axis=X)


def test_a_fixed_joint_has_no_value(session: Session) -> None:
    gate, post = _gate(session)
    made = _add(session, "weld", gate, joint_type=JointType.fixed, a=post)

    with pytest.raises(AdapterError, match="no value"):
        session.adapter.set_joint(SetJointRequest(operation_id="x", joint=made.joint.handle, value=1))  # type: ignore[attr-defined]


def test_a_component_has_one_joint_and_two_different_components_are_joined(session: Session) -> None:
    gate, post = _gate(session)
    _add(session, "rail", gate, joint_type=JointType.slider, axis=X)

    with pytest.raises(AdapterError, match="already joined"):
        _add(session, "rail2", gate, joint_type=JointType.slider, axis=X)
    with pytest.raises(AdapterError, match="two different"):
        _add(session, "self", post, joint_type=JointType.fixed, a=post)


def test_a_jointed_component_is_not_placed_by_hand(session: Session) -> None:
    gate, post = _gate(session)
    _add(session, "rail", gate, joint_type=JointType.slider, axis=X, a=post)

    for component in (gate, post):  # the one it carries and the one it hangs from
        with pytest.raises(AdapterError, match="set_joint") as info:
            session.adapter.place_component(
                PlaceComponentRequest(operation_id="p", component=component, transform=Transform(origin=(1, 2, 3)))
            )
        assert info.value.info.code is ErrorCode.invalid_request


def test_undo_takes_back_the_move_and_then_the_joint(session: Session) -> None:
    gate, _ = _gate(session)
    made = _add(session, "rail", gate, joint_type=JointType.slider, axis=X)
    handle = made.joint.handle  # type: ignore[attr-defined]
    session.adapter.set_joint(SetJointRequest(operation_id="a", joint=handle, value=40))

    session.adapter.undo_last(Empty(operation_id="u1"))
    assert _bbox(session, gate).min == pytest.approx((0, 0, 0))
    assert session.adapter.joint_info(JointRequest(operation_id="q", joint=handle)).value == 0

    session.adapter.undo_last(Empty(operation_id="u2"))
    with pytest.raises(AdapterError):
        session.adapter.joint_info(JointRequest(operation_id="q", joint=handle))
    session.adapter.place_component(  # the joint is gone: the component is free again
        PlaceComponentRequest(operation_id="p", component=gate, transform=Transform(origin=(1, 0, 0)))
    )


def test_sweep_values_include_both_ends_and_a_short_last_step() -> None:
    assert sweep_values(0, 90, 1)[0] == 0 and sweep_values(0, 90, 1)[-1] == 90 and len(sweep_values(0, 90, 1)) == 91
    assert sweep_values(0, 10, 4) == [0, 4, 8, 10]
    assert sweep_values(10, 0, 5) == [10, 5, 0]


def test_sweep_reports_the_first_clash_the_least_distance_and_puts_the_joint_back(session: Session) -> None:
    gate, post = _gate(session)  # the gate's face at x=10, the post's at x=100
    made = _add(session, "rail", gate, joint_type=JointType.slider, axis=X, min_value=0, max_value=200)
    handle = made.joint.handle  # type: ignore[attr-defined]

    result = session.adapter.sweep_joint(SweepJointRequest(operation_id="s", joint=handle, start=0, end=150, step=5))

    assert result.positions == 31 and result.step == 5
    assert result.first_clash is not None
    assert result.first_clash.value == 95  # first multiple of 5 where the gate (10 wide) reaches x=100
    assert (result.first_clash.a, result.first_clash.b) == (gate, post)
    assert result.first_clash.volume_mm3 == pytest.approx(5 * 10 * 10)
    (pair,) = result.pairs
    assert pair.min_distance_mm == pytest.approx(0.0) and pair.first_overlap_value == 95
    assert _bbox(session, gate).min == pytest.approx((0, 0, 0))  # restored
    assert session.adapter.joint_info(JointRequest(operation_id="q", joint=handle)).value == 0


def test_sweep_without_a_clash_gives_the_least_distance_and_where(session: Session) -> None:
    gate, post = _gate(session)
    made = _add(session, "rail", gate, joint_type=JointType.slider, axis=X)

    result = session.adapter.sweep_joint(
        SweepJointRequest(operation_id="s", joint=made.joint.handle, start=0, end=60, step=10)  # type: ignore[attr-defined]
    )

    assert result.first_clash is None
    (pair,) = result.pairs
    assert pair.b == post and pair.min_distance_mm == pytest.approx(30.0) and pair.at_value == 60


def test_sweep_refuses_more_positions_than_the_cap_and_ranges_outside_the_limits(session: Session) -> None:
    gate, _ = _gate(session)
    made = _add(session, "rail", gate, joint_type=JointType.slider, axis=X, min_value=0, max_value=100)
    handle = made.joint.handle  # type: ignore[attr-defined]

    with pytest.raises(AdapterError, match="721"):
        session.adapter.sweep_joint(SweepJointRequest(operation_id="s", joint=handle, start=0, end=100, step=0.1))
    with pytest.raises(AdapterError, match="outside the limits"):
        session.adapter.sweep_joint(SweepJointRequest(operation_id="s", joint=handle, start=0, end=150, step=10))


def test_a_rotation_that_is_not_a_multiple_of_ninety_is_reported_as_not_measured_by_the_fake(session: Session) -> None:
    lid_part = _part(session, "lid", (100, 50, 5))
    other_part = _part(session, "other", (10, 10, 10))
    session.new_part("asm", DocumentKind.assembly)
    lid = _insert(session, "lid", lid_part, (0, 0, 10))
    _insert(session, "other", other_part, (0, 0, 200))
    made = _add(session, "hinge", lid, joint_type=JointType.revolute, axis=Y_THROUGH_TOP)

    result = session.adapter.sweep_joint(
        SweepJointRequest(operation_id="s", joint=made.joint.handle, start=0, end=90, step=45)  # type: ignore[attr-defined]
    )

    assert result.pairs[0].unmeasured == 1 and result.notes  # 45 degrees only; 0 and 90 are boxes
    assert result.first_clash is None


def test_joint_delta_of_a_revolute_keeps_the_axis_fixed() -> None:
    delta = joint_delta(JointType.revolute, Y_THROUGH_TOP, 37.0)

    assert delta.apply((0, 5, 10)) == pytest.approx((0, 5, 10))
    turned = (10 * math.cos(math.radians(37)), 0, 10 - 10 * math.sin(math.radians(37)))
    assert delta.apply((10, 0, 10)) == pytest.approx(turned)


def _stamp() -> DocumentStamp:
    return DocumentStamp(
        design_id="d",
        plan_id="p",
        plan_revision=1,
        save_seq=1,
        last_step_index=1,
        harness_version="0.0.0",
        plan_sha256="a" * 64,
    )


def test_the_assembly_saves_and_reopens_with_its_joint_and_value(tmp_path: Path) -> None:
    first = Session(FakeAdapter())
    gate_part = _part(first, "gate", (10, 10, 10))
    first.adapter.save_document(
        SaveDocumentRequest(operation_id="save", path=str(tmp_path / "gate.fakecad.json"), stamp=_stamp())
    )
    first.new_part("asm", DocumentKind.assembly)
    gate = _insert(first, "gate", gate_part, (0, 0, 0))
    made = _add(first, "rail", gate, joint_type=JointType.slider, axis=X, min_value=0, max_value=200)
    first.adapter.set_joint(SetJointRequest(operation_id="a", joint=made.joint.handle, value=40))  # type: ignore[attr-defined]
    first.adapter.save_document(
        SaveDocumentRequest(operation_id="save2", path=str(tmp_path / "asm.fakecad.json"), stamp=_stamp())
    )

    second = Session(FakeAdapter())
    second.adapter.open_document(OpenDocumentRequest(operation_id="o1", path=str(tmp_path / "gate.fakecad.json")))
    second.adapter.open_document(OpenDocumentRequest(operation_id="o2", path=str(tmp_path / "asm.fakecad.json")))

    info = second.adapter.joint_info(JointRequest(operation_id="q", joint=made.joint.handle))  # type: ignore[attr-defined]
    assert info.value == 40 and info.min_value == 0 and info.max_value == 200
    second.adapter.set_joint(SetJointRequest(operation_id="b", joint=info.handle, value=70))
    assert _bbox(second, gate).min == pytest.approx((70, 0, 0))
