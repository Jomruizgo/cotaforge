"""Joints of an assembly on the real FreeCAD worker: a plate and a lid from two part documents, a revolute joint on the
plate's edge, a fixed obstacle above. The joint is also a real ``JointObject`` of the Assembly workbench (its solver
accepts the poses the harness sets), the value moves the lid exactly, a sweep finds the clash at the analytic angle,
and the assembly saves and reopens with the joint."""

import math
from pathlib import Path
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter, FreeCADSession
from cotaforge.contract import (
    AdapterError,
    AddJointRequest,
    Axis,
    ComponentHandle,
    ComponentRequest,
    ConnectRequest,
    DocumentHandle,
    DocumentKind,
    Empty,
    InsertComponentRequest,
    JointRequest,
    JointType,
    NewDocumentRequest,
    OpenDocumentRequest,
    PlaceComponentRequest,
    RunNativeRequest,
    SaveDocumentRequest,
    SetJointRequest,
    SweepJointRequest,
    Transform,
)
from cotaforge.contract.requests import BoxRequest
from tests.integration.freecad.worker_tools import kill_worker, stamp_for

pytestmark = pytest.mark.freecad

# the hinge: along the plate's top edge at x=0, turning the lid up for a positive angle
HINGE = Axis(origin=(0, 0, 10), direction=(0, -1, 0))
LID_LENGTH, LID_THICKNESS = 100.0, 5.0
CEILING_HEIGHT = 90.0  # of the obstacle's underside above the hinge


def analytic_clash_angle() -> float:
    """The angle at which the lid's far top corner reaches the underside of the obstacle."""
    reach = math.hypot(LID_LENGTH, LID_THICKNESS)
    return math.degrees(math.asin(CEILING_HEIGHT / reach) - math.atan2(LID_THICKNESS, LID_LENGTH))


def part(adapter: FreeCADAdapter, name: str, size: tuple[float, float, float], at: Path) -> str:
    doc = adapter.new_document(NewDocumentRequest(operation_id=f"new_{name}", kind=DocumentKind.part, name=name))
    adapter.box(BoxRequest(operation_id=f"{name}_box", size=size))
    save(adapter, at / f"{name}.FCStd")
    return doc.handle.id


def save(adapter: FreeCADAdapter, path: Path, seq: int = 1) -> Any:
    return adapter.save_document(
        SaveDocumentRequest(operation_id="save", path=str(path), stamp=stamp_for("p", 1, save_seq=seq))
    )


def insert(adapter: FreeCADAdapter, op: str, part_id: str, origin: tuple[float, float, float]) -> ComponentHandle:
    made = adapter.insert_component(
        InsertComponentRequest(operation_id=op, part=DocumentHandle(id=part_id), transform=Transform(origin=origin))
    )
    assert made.component is not None
    return made.component


def native(adapter: FreeCADAdapter, code: str) -> list[str]:
    result = adapter.run_native(RunNativeRequest(operation_id="native", code=code))
    assert result.ok, result
    return result.stdout.split()


def build_scene(adapter: FreeCADAdapter, at: Path) -> tuple[ComponentHandle, ComponentHandle, ComponentHandle]:
    plate = part(adapter, "plate", (100, 100, 10), at)
    lid = part(adapter, "lid", (LID_LENGTH, 100, LID_THICKNESS), at)
    block = part(adapter, "block", (120, 100, 20), at)
    adapter.new_document(NewDocumentRequest(operation_id="new_asm", kind=DocumentKind.assembly, name="asm"))
    save(adapter, at / "asm.FCStd")
    return (
        insert(adapter, "plate", plate, (0, 0, 0)),
        insert(adapter, "lid", lid, (0, 0, 10)),
        insert(adapter, "block", block, (0, 0, 10 + CEILING_HEIGHT)),
    )


def lid_box(adapter: FreeCADAdapter, lid: ComponentHandle) -> Any:
    return adapter.component_bounding_box(ComponentRequest(operation_id="q", component=lid))


def joint_objects(adapter: FreeCADAdapter) -> list[str]:
    """What FreeCAD itself holds: the type of each joint object, and what its own solver says about the assembly."""
    return native(
        adapter,
        "asm = [o for o in doc.Objects if o.TypeId == 'Assembly::AssemblyObject'][0]\n"
        "kinds = [o.JointType for o in doc.Objects if hasattr(o, 'JointType')]\n"
        "grounds = [o.Name for o in doc.Objects if 'ObjectToGround' in o.PropertiesList]\n"
        "print(len(kinds), *kinds, len(grounds), asm.solve())",
    )


def test_a_revolute_joint_moves_the_lid_and_is_a_real_freecad_joint(freecad_root: Path, tmp_path: Path) -> None:
    with FreeCADSession(freecad_root=freecad_root, timeout_s=300.0, log_dir=tmp_path / "logs") as adapter:
        plate, lid, _ = build_scene(adapter, tmp_path)

        made = adapter.add_joint(
            AddJointRequest(
                operation_id="hinge",
                joint_type=JointType.revolute,
                a=plate,
                b=lid,
                axis=HINGE,
                min_value=0,
                max_value=120,
            )
        )
        assert made.joint is not None and made.joint.unit == "deg" and made.joint.value == 0
        assert not made.warnings  # a joint between two components has its FreeCAD joint object
        count, kind, grounded, solved = joint_objects(adapter)
        assert (count, kind, grounded, solved) == ("1", "Revolute", "1", "0")  # one joint, the plate grounded, solved

        adapter.set_joint(SetJointRequest(operation_id="open", joint=made.joint.handle, value=30))

        box = lid_box(adapter, lid)
        # the far end (x=100) rose by 100*sin(30); the lid still starts at the hinge
        assert box.max[2] == pytest.approx(10 + 100 * math.sin(math.radians(30)) + 5 * math.cos(math.radians(30)))
        before = adapter.component_transform(ComponentRequest(operation_id="q", component=lid))
        # FreeCAD's own solver agrees the lid is where the joint says: solving moves nothing
        assert joint_objects(adapter)[-1] == "0"
        after = adapter.component_transform(ComponentRequest(operation_id="q", component=lid))
        assert after.origin == pytest.approx(before.origin, abs=1e-4)
        assert [x for row in after.rotation for x in row] == pytest.approx(
            [x for row in before.rotation for x in row], abs=1e-6
        )


def test_the_sweep_finds_the_first_clash_at_the_analytic_angle_and_restores_the_joint(
    freecad_root: Path, tmp_path: Path
) -> None:
    with FreeCADSession(freecad_root=freecad_root, timeout_s=300.0, log_dir=tmp_path / "logs") as adapter:
        plate, lid, block = build_scene(adapter, tmp_path)
        made = adapter.add_joint(
            AddJointRequest(
                operation_id="hinge",
                joint_type=JointType.revolute,
                a=plate,
                b=lid,
                axis=HINGE,
                min_value=0,
                max_value=120,
            )
        )
        assert made.joint is not None
        adapter.set_joint(SetJointRequest(operation_id="a", joint=made.joint.handle, value=20))
        pose_before = adapter.component_transform(ComponentRequest(operation_id="q", component=lid))

        result = adapter.sweep_joint(
            SweepJointRequest(operation_id="sweep", joint=made.joint.handle, start=0, end=90, step=1)
        )

        expected = analytic_clash_angle()
        assert result.positions == 91 and result.step == 1
        assert result.first_clash is not None
        assert abs(result.first_clash.value - expected) <= 1.0 + 1e-9
        assert (result.first_clash.a, result.first_clash.b) == (lid, block)
        assert result.first_clash.volume_mm3 > 0
        by_other = {p.b.id: p for p in result.pairs}
        assert by_other[block.id].first_overlap_value == result.first_clash.value
        closest = by_other[block.id]  # the least distance up to the clash: just under the clash angle, and small
        assert closest.min_distance_mm is not None and 0 < closest.min_distance_mm < 2.0
        assert closest.at_value == result.first_clash.value - 1
        assert by_other[plate.id].min_distance_mm == pytest.approx(0.0)  # the lid starts on the plate
        # restored: the pose and the value are the ones before the sweep
        assert adapter.component_transform(ComponentRequest(operation_id="q", component=lid)) == pose_before
        assert adapter.joint_info(JointRequest(operation_id="q", joint=made.joint.handle)).value == 20


def test_a_sweep_that_stays_clear_reports_the_least_clearance_and_where(freecad_root: Path, tmp_path: Path) -> None:
    with FreeCADSession(freecad_root=freecad_root, timeout_s=300.0, log_dir=tmp_path / "logs") as adapter:
        plate, lid, block = build_scene(adapter, tmp_path)
        made = adapter.add_joint(
            AddJointRequest(operation_id="hinge", joint_type=JointType.revolute, a=plate, b=lid, axis=HINGE)
        )
        assert made.joint is not None

        result = adapter.sweep_joint(
            SweepJointRequest(operation_id="sweep", joint=made.joint.handle, start=0, end=50, step=5)
        )

        assert result.first_clash is None
        pair = {p.b.id: p for p in result.pairs}[block.id]
        assert pair.at_value == 50 and pair.min_distance_mm is not None and pair.min_distance_mm > 0


def test_a_joint_to_the_ground_has_no_freecad_object_but_works(freecad_root: Path, tmp_path: Path) -> None:
    with FreeCADSession(freecad_root=freecad_root, timeout_s=300.0, log_dir=tmp_path / "logs") as adapter:
        _, lid, _ = build_scene(adapter, tmp_path)

        made = adapter.add_joint(
            AddJointRequest(
                operation_id="g", joint_type=JointType.slider, b=lid, axis=Axis(origin=(0, 0, 0), direction=(1, 0, 0))
            )
        )
        assert made.joint is not None and made.joint.a is None
        assert any("ground" in w for w in made.warnings)
        adapter.set_joint(SetJointRequest(operation_id="s", joint=made.joint.handle, value=25))

        assert lid_box(adapter, lid).min == pytest.approx((25, 0, 10))
        assert joint_objects(adapter)[0] == "0"


def test_undo_takes_back_the_move_and_the_joint_with_its_freecad_objects(freecad_root: Path, tmp_path: Path) -> None:
    with FreeCADSession(freecad_root=freecad_root, timeout_s=300.0, log_dir=tmp_path / "logs") as adapter:
        plate, lid, _ = build_scene(adapter, tmp_path)
        made = adapter.add_joint(
            AddJointRequest(operation_id="hinge", joint_type=JointType.revolute, a=plate, b=lid, axis=HINGE)
        )
        assert made.joint is not None
        adapter.set_joint(SetJointRequest(operation_id="a", joint=made.joint.handle, value=45))

        adapter.undo_last(Empty(operation_id="u1"))
        assert lid_box(adapter, lid).min == pytest.approx((0, 0, 10))
        assert adapter.joint_info(JointRequest(operation_id="q", joint=made.joint.handle)).value == 0

        adapter.undo_last(Empty(operation_id="u2"))
        assert joint_objects(adapter)[0] == "0"  # the joint objects went with the joint
        with pytest.raises(AdapterError):
            adapter.joint_info(JointRequest(operation_id="q", joint=made.joint.handle))
        adapter.place_component(
            PlaceComponentRequest(operation_id="p", component=lid, transform=Transform(origin=(0, 0, 12)))
        )


def test_a_jointed_component_is_not_placed_by_hand(freecad_root: Path, tmp_path: Path) -> None:
    with FreeCADSession(freecad_root=freecad_root, timeout_s=300.0, log_dir=tmp_path / "logs") as adapter:
        plate, lid, _ = build_scene(adapter, tmp_path)
        adapter.add_joint(
            AddJointRequest(operation_id="hinge", joint_type=JointType.revolute, a=plate, b=lid, axis=HINGE)
        )

        for component in (lid, plate):
            with pytest.raises(AdapterError, match="set_joint"):
                adapter.place_component(
                    PlaceComponentRequest(operation_id="p", component=component, transform=Transform(origin=(0, 0, 1)))
                )


def test_the_assembly_saves_and_reopens_with_the_joint(freecad_root: Path, tmp_path: Path) -> None:
    logs = tmp_path / "logs"
    first = FreeCADAdapter(freecad_root=freecad_root, timeout_s=300.0, log_dir=logs, enable_test_hooks=True)
    first.connect(ConnectRequest(operation_id="c"))
    plate, lid, _ = build_scene(first, tmp_path)
    made = first.add_joint(
        AddJointRequest(
            operation_id="hinge", joint_type=JointType.revolute, a=plate, b=lid, axis=HINGE, min_value=0, max_value=120
        )
    )
    assert made.joint is not None
    first.set_joint(SetJointRequest(operation_id="a", joint=made.joint.handle, value=30))
    saved = save(first, tmp_path / "asm.FCStd", seq=2)
    kill_worker(first)

    second = FreeCADAdapter(freecad_root=freecad_root, timeout_s=300.0, log_dir=logs, enable_test_hooks=True)
    second.connect(ConnectRequest(operation_id="c"))
    for name in ("plate", "lid", "block"):
        second.open_document(OpenDocumentRequest(operation_id=f"o_{name}", path=str(tmp_path / f"{name}.FCStd")))
    second.open_document(
        OpenDocumentRequest(operation_id="o", path=str(tmp_path / "asm.FCStd"), adapter_state=saved.adapter_state)
    )

    info = second.joint_info(JointRequest(operation_id="q", joint=made.joint.handle))
    assert info.joint_type is JointType.revolute and info.value == 30
    assert (info.min_value, info.max_value) == (0, 120)
    assert joint_objects(second)[:2] == ["1", "Revolute"]  # FreeCAD's own joint came back with the file
    second.set_joint(SetJointRequest(operation_id="b", joint=made.joint.handle, value=60))
    assert lid_box(second, lid).max[2] == pytest.approx(
        10 + 100 * math.sin(math.radians(60)) + 5 * math.cos(math.radians(60))
    )
    swept = second.sweep_joint(SweepJointRequest(operation_id="s", joint=made.joint.handle, start=0, end=90, step=1))
    assert swept.first_clash is not None and abs(swept.first_clash.value - analytic_clash_angle()) <= 1.0 + 1e-9
    kill_worker(second)
