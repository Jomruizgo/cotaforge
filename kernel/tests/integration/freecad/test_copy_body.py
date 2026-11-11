"""``copy_body`` against the real FreeCAD worker.

Every test drives an IR plan through ``Executor`` + ``Verifier`` (so the body-scoped Level 2 checks run on real
FreeCAD answers) and compares body-scoped mass, surface, centroid and bounding box of the copy and its source with
analytic values. A copy is a history-free ``Part::Feature`` body (limitation): the source's sketches and features are
not duplicated, but the copy can be boolean-ed, moved and upgraded by a later extrude/cut like any other body.
"""

import math
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import BodyHandle, FaceRequest, FeatureRequest, MassRequest, Transform
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier

pytestmark = pytest.mark.freecad

XY = {"kind": "plane", "name": "XY"}
BLOCK = 10.0 * 20.0 * 30.0
BLOCK_AREA = 2 * (10 * 20 + 10 * 30 + 20 * 30)
Z = (0.0, 0.0, 1.0)


def _run(adapter: FreeCADAdapter, *operations: dict[str, Any]) -> tuple[Executor, ExecutionTrace]:
    plan = parse_plan({"id": "twins", "kind": "part", "operations": list(operations)})
    executor = Executor(plan, adapter, Verifier(), ExecutorConfig())
    executor.start()
    return executor, executor.run_to_end()


def _assert_green(trace: ExecutionTrace) -> None:
    detail = [
        (
            s.op_id,
            s.status.value,
            s.plan_error,
            s.attempts[-1].error if s.attempts else None,
            [(c.name, c.expected, c.observed) for c in s.last_level2() if c.ok is False] if s.attempts else None,
        )
        for s in trace.steps
        if s.status is not StepStatus.success
    ]
    assert trace.final_state is ExecutorState.completed, detail
    assert all(s.status is StepStatus.success for s in trace.steps), detail


def _asserted_ok(trace: ExecutionTrace, op_id: str) -> set[str]:
    step = trace.step(op_id)
    assert step is not None, op_id
    return {c.name for c in step.last_level2() if c.kind is CheckKind.asserted and c.ok}


def _result(trace: ExecutionTrace, op_id: str) -> Any:
    step = trace.step(op_id)
    assert step is not None and step.attempts and step.attempts[-1].result is not None
    return step.attempts[-1].result


def _body(trace: ExecutionTrace, op_id: str) -> BodyHandle:
    return _result(trace, op_id).created_bodies[0]


def _close(a: Any, b: Any, tol: float = 1e-6) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def _block() -> dict[str, Any]:
    return {"id": "block", "op": "box", "intent": "block", "size": [10, 20, 30]}


def _copy(op_id: str = "twin", source: str = "block", **extra: Any) -> dict[str, Any]:
    return {
        "id": op_id,
        "op": "copy_body",
        "intent": "twin",
        "source": {"kind": "body", "from_op": source},
        **extra,
    }


def _plate() -> list[dict[str, Any]]:
    rect = {"kind": "rect", "name": "r", "center": [50, 30], "width": 100, "height": 60}
    return [
        {"id": "s0", "op": "sketch", "intent": "i", "on": XY, "entities": [rect]},
        {"id": "base", "op": "extrude", "intent": "i", "sketch": "s0", "depth_mm": 8},
    ]


def _mass(adapter: FreeCADAdapter, body: BodyHandle) -> Any:
    return adapter.mass_properties(MassRequest(operation_id="q", body=body))


def _bbox(adapter: FreeCADAdapter, body: BodyHandle) -> Any:
    return adapter.bounding_box(MassRequest(operation_id="q", body=body))


def test_a_translated_box_copy_doubles_the_volume_and_keeps_the_source(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, _block(), _copy(offset=[0, 100, 0]))
    _assert_green(trace)
    assert _asserted_ok(trace, "twin") == {
        "volume_delta",
        "copy_centroid",
        "copy_surface",
        "source_centroid",
        "copy_bbox",
    }
    source, twin = _body(trace, "block"), _body(trace, "twin")
    assert source != twin
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(2 * BLOCK, abs=1e-6)
    assert _mass(adapter, twin).volume_mm3 == pytest.approx(BLOCK, abs=1e-6)
    assert _mass(adapter, twin).surface_mm2 == pytest.approx(BLOCK_AREA, abs=1e-6)
    assert _close(_mass(adapter, twin).centroid, (5, 110, 15))
    assert _close(_mass(adapter, source).centroid, (5, 10, 15))
    assert _close(_bbox(adapter, twin).min, (0, 100, 0)) and _close(_bbox(adapter, twin).max, (10, 120, 30))
    assert _close(_bbox(adapter, source).min, (0, 0, 0)) and _close(_bbox(adapter, source).max, (10, 20, 30))


def test_the_copy_of_a_partdesign_body_with_a_hole_has_the_same_volume_and_surface(adapter: FreeCADAdapter) -> None:
    hole = {
        "id": "hole",
        "op": "hole",
        "intent": "i",
        "face": {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}},
        "position": [0, 0],
        "standard": "simple",
        "diameter_mm": 10,
        "end_condition": "through_all",
    }
    twin = _copy(source="base", offset=[0, 100, 0])
    _, trace = _run(adapter, *_plate(), hole, twin)
    _assert_green(trace)
    source, copy = _body(trace, "base"), _body(trace, "twin")
    expected = 100 * 60 * 8 - math.pi * 25 * 8
    assert _mass(adapter, source).volume_mm3 == pytest.approx(expected, abs=1e-3)
    assert _mass(adapter, copy).volume_mm3 == pytest.approx(expected, abs=1e-3)
    assert _mass(adapter, copy).surface_mm2 == pytest.approx(_mass(adapter, source).surface_mm2, abs=1e-6)
    moved = Transform(origin=(0.0, 100.0, 0.0)).apply(_mass(adapter, source).centroid)
    assert _close(_mass(adapter, copy).centroid, moved, 1e-6)


@pytest.mark.parametrize(("angle", "asserts_bbox"), [(90.0, True), (30.0, False)])
def test_a_rotated_copy_lands_at_the_rotated_pose(adapter: FreeCADAdapter, angle: float, asserts_bbox: bool) -> None:
    about = [10.0, 0.0, 0.0]
    _, trace = _run(
        adapter, _block(), _copy(offset=[0, 100, 0], rotate={"axis": "Z", "angle_deg": angle, "about": about})
    )
    _assert_green(trace)
    turn = Transform.from_axis_angle(Z, angle, origin=(0.0, 0.0, 0.0))
    spin = Transform(origin=(0.0, 100.0, 0.0)).compose(
        Transform(origin=(10.0, 0.0, 0.0)).compose(turn).compose(Transform(origin=(-10.0, 0.0, 0.0)))
    )
    twin = _body(trace, "twin")
    assert _close(_mass(adapter, twin).centroid, spin.apply((5.0, 10.0, 15.0)), 1e-6)
    assert _mass(adapter, twin).volume_mm3 == pytest.approx(BLOCK, abs=1e-6)
    kinds = {c.name: c.kind for c in trace.step("twin").last_level2()}  # type: ignore[union-attr]
    assert (kinds["copy_bbox"] is CheckKind.asserted) is asserts_bbox
    if asserts_bbox:
        box = _bbox(adapter, twin)
        assert _close(box.min, (-10, 90, 0), 1e-6) and _close(box.max, (10, 100, 30), 1e-6)


def test_a_cylinder_copy_keeps_the_face_axis_sense(adapter: FreeCADAdapter) -> None:
    cylinder = {"id": "rod", "op": "cylinder", "intent": "rod", "diameter_mm": 20, "height_mm": 40, "axis": "Z"}
    twin = _copy(source="rod", offset=[0, 0, 0], rotate={"axis": "X", "angle_deg": 90})
    _, trace = _run(adapter, cylinder, twin)
    _assert_green(trace)
    faces = adapter.faces_of(FeatureRequest(operation_id="q", feature=_result(trace, "twin").feature)).faces
    (side,) = [f for f in faces if f.axis is not None]
    rotated = Transform.from_axis_angle((1.0, 0.0, 0.0), 90.0).apply_direction(Z)
    assert _close(side.axis.direction, rotated, 1e-6), (side.axis, rotated)
    assert side.radius == pytest.approx(10.0)
    assert _mass(adapter, _body(trace, "twin")).volume_mm3 == pytest.approx(math.pi * 100 * 40, abs=1e-6)


def test_the_original_and_its_copy_boolean_like_two_bodies(adapter: FreeCADAdapter) -> None:
    union = {
        "id": "both",
        "op": "boolean_union",
        "intent": "fuse",
        "target": {"kind": "body", "from_op": "block"},
        "tools": [{"kind": "body", "from_op": "twin"}],
    }
    _, trace = _run(adapter, _block(), _copy(offset=[5, 0, 0]), union)
    _assert_green(trace)
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(1.5 * BLOCK, abs=1e-6)
    assert "volume_delta" in _asserted_ok(trace, "both")


def test_translating_the_copy_moves_only_the_copy(adapter: FreeCADAdapter) -> None:
    lift = {
        "id": "lift",
        "op": "translate",
        "intent": "lift the twin",
        "target": {"kind": "body", "from_op": "twin"},
        "offset": [0, 0, 50],
    }
    _, trace = _run(adapter, _block(), _copy(offset=[0, 100, 0]), lift)
    _assert_green(trace)
    assert _close(_mass(adapter, _body(trace, "block")).centroid, (5, 10, 15))
    assert _close(_mass(adapter, _body(trace, "twin")).centroid, (5, 110, 65))


def test_a_boss_extruded_on_the_copys_face_grows_only_the_copy(adapter: FreeCADAdapter) -> None:
    """The copy starts as a Part body; a feature op upgrades it lazily (``ensure_partdesign``), like any Part body."""
    top = {"kind": "face", "from_op": "twin", "select": {"normal": [0, 0, 1]}}
    rect = {"kind": "rect", "name": "boss", "center": [0, 0], "width": 4, "height": 4}
    sketch = {"id": "bs", "op": "sketch", "intent": "i", "on": top, "entities": [rect]}
    boss = {"id": "boss", "op": "extrude", "intent": "i", "sketch": "bs", "depth_mm": 5}
    _, trace = _run(adapter, _block(), _copy(offset=[0, 100, 0]), sketch, boss)
    _assert_green(trace)
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(2 * BLOCK + 4 * 4 * 5, abs=1e-3)
    assert _mass(adapter, _body(trace, "block")).volume_mm3 == pytest.approx(BLOCK, abs=1e-6)
    assert _mass(adapter, _body(trace, "twin")).volume_mm3 == pytest.approx(BLOCK + 80, abs=1e-3)


def test_undo_to_the_source_removes_the_copy(adapter: FreeCADAdapter) -> None:
    executor, trace = _run(adapter, _block(), _copy(offset=[0, 100, 0]))
    _assert_green(trace)
    assert executor.undo_to("block") == ("twin",)
    assert adapter.mass_properties(MassRequest(operation_id="q")).volume_mm3 == pytest.approx(BLOCK, abs=1e-6)
    twin = _body(trace, "twin")
    with pytest.raises(Exception, match="body"):
        adapter.mass_properties(MassRequest(operation_id="q", body=twin))


def test_copy_faces_resolve_to_the_copy_not_to_the_source(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, _block(), _copy(offset=[0, 100, 0]))
    _assert_green(trace)
    faces = _result(trace, "twin").created_faces
    assert len(faces) == 6
    for handle in faces:
        info = adapter.face_info(FaceRequest(operation_id="q", face=handle))
        assert info.bbox.min[1] >= 100.0 - 1e-6
