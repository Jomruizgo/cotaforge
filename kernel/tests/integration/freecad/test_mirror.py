"""``mirror`` and ``mirror_body`` against the real FreeCAD worker (PartDesign::Mirrored about a PartDesign::Plane datum,
and a history-free ``Shape.mirror`` body).

Every test drives an IR plan through ``Executor`` + ``Verifier`` so the Level 2 checks run on real FreeCAD answers; the
expected numbers are derived by hand: a reflected boss adds pi r^2 h again, a reflected hole or pocket removes its own
again, a reflected body keeps volume and surface and has its centroid and box reflected across the plane. Run on both
supported FreeCAD lines: they agree to 1e-9.
"""

import math
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import BodyHandle, MassRequest
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier

pytestmark = pytest.mark.freecad

XY = {"kind": "plane", "name": "XY"}
YZ = {"kind": "plane", "name": "YZ"}
PLATE_VOLUME = 48000.0
BOSS_VOLUME = math.pi * 25.0 * 5.0  # d 10 x 5
HOLE_VOLUME = math.pi * 9.0 * 8.0  # d 6 through 8 mm: 226.1947
TOP = {"kind": "face", "from_op": "plate", "select": {"normal": [0, 0, 1]}}


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


def _check(trace: ExecutionTrace, op_id: str, name: str) -> Any:
    step = trace.step(op_id)
    assert step is not None
    return next(c for c in step.last_level2() if c.name == name)


def _final_volume(trace: ExecutionTrace) -> float:
    assert trace.final_mass is not None
    return trace.final_mass.volume_mm3


def _body(trace: ExecutionTrace, op_id: str) -> BodyHandle:
    step = trace.step(op_id)
    assert step is not None and step.attempts and step.attempts[-1].result is not None
    return step.attempts[-1].result.created_bodies[0]


def _close(a: Any, b: Any, tol: float = 1e-6) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


def _plate() -> list[dict[str, Any]]:
    return [
        {
            "id": "plate_sketch",
            "op": "sketch",
            "intent": "plate outline",
            "on": XY,
            "entities": [{"kind": "rect", "name": "r", "center": [50, 30], "width": 100, "height": 60}],
        },
        {"id": "plate", "op": "extrude", "intent": "plate", "sketch": "plate_sketch", "depth_mm": 8},
    ]


def _boss(at: tuple[float, float] = (10.0, 10.0), op_id: str = "boss") -> list[dict[str, Any]]:
    """A d10 x 5 boss on the plate top at world ``at`` (the top face frame is centred on (50, 30))."""
    return [
        {
            "id": f"{op_id}_sketch",
            "op": "sketch",
            "intent": "boss outline",
            "on": TOP,
            "entities": [{"kind": "circle", "name": "c", "center": [at[0] - 50, at[1] - 30], "diameter": 10}],
        },
        {"id": op_id, "op": "extrude", "intent": "boss", "sketch": f"{op_id}_sketch", "depth_mm": 5},
    ]


def _hole(at: tuple[float, float] = (10.0, 10.0)) -> dict[str, Any]:
    return {
        "id": "bore",
        "op": "hole",
        "intent": "bore",
        "face": TOP,
        "position": [at[0] - 50, at[1] - 30],
        "standard": "simple",
        "diameter_mm": 6,
        "end_condition": "through_all",
    }


def _datum(op_id: str, distance: float) -> dict[str, Any]:
    return {
        "id": op_id,
        "op": "ref_plane",
        "intent": "mirror plane",
        "define": {"how": "offset", "base": YZ, "distance_mm": distance},
    }


def _mirror(plane: dict[str, Any], features: list[str], op_id: str = "twin") -> dict[str, Any]:
    return {"id": op_id, "op": "mirror", "intent": "reflect it", "features": features, "plane": plane}


def _datum_ref(op_id: str) -> dict[str, Any]:
    return {"kind": "plane", "from_op": op_id}


# --- mirror ---------------------------------------------------------------------------------------------------------


def test_a_boss_mirrored_about_a_datum_plane_adds_its_volume_again(adapter: FreeCADAdapter) -> None:
    """Boss at x = 10 about the plane x = 20 lands at x = 30: V = 48000 + 2 pi 25 5, every check asserted."""
    _, trace = _run(adapter, *_plate(), *_boss(), _datum("mid", 20), _mirror(_datum_ref("mid"), ["boss"]))
    _assert_green(trace)
    assert _asserted_ok(trace, "twin") == {"instance_count", "created_faces_count", "volume_delta", "bbox_contains"}
    assert _final_volume(trace) == pytest.approx(PLATE_VOLUME + 2 * BOSS_VOLUME, abs=1e-6)
    assert _final_volume(trace) == pytest.approx(48785.398, abs=1e-3)


def test_a_boss_mirrored_about_a_base_plane_hanging_off_the_body_is_refused_not_guessed(
    adapter: FreeCADAdapter,
) -> None:
    """YZ is x = 0: the reflected boss stands at x = -10, off the plate, and PartDesign keeps one solid."""
    _, trace = _run(adapter, *_plate(), *_boss(), _mirror(YZ, ["boss"]))
    assert trace.final_state is ExecutorState.failed
    step = trace.step("twin")
    assert step is not None
    message = " | ".join(a.error.message for a in step.attempts if a.error)
    # 1.0.2 drops the floating solid ("no material"), 1.1.3 keeps it as a second solid ("floating"): both say why
    assert "touch the body" in message
    assert len(step.attempts) == 1  # a deterministic kernel failure is not retried


def test_a_mirrored_through_hole_removes_its_volume_again(adapter: FreeCADAdapter) -> None:
    """The spike's number: a d6 hole through 8 mm removes 226.1947 mm3, and its reflection another 226.1947."""
    _, trace = _run(adapter, *_plate(), _hole(), _datum("mid", 50), _mirror(_datum_ref("mid"), ["bore"]))
    _assert_green(trace)
    assert _asserted_ok(trace, "twin") == {"instance_count", "created_faces_count", "volume_delta"}
    assert _final_volume(trace) == pytest.approx(PLATE_VOLUME - 2 * HOLE_VOLUME, abs=1e-6)
    assert _final_volume(trace) == pytest.approx(47547.6106, abs=1e-3)


def test_a_mirrored_pocket_removes_its_volume_again(adapter: FreeCADAdapter) -> None:
    sketch = {
        "id": "slot_sketch",
        "op": "sketch",
        "intent": "slot outline",
        "on": TOP,
        "entities": [{"kind": "rect", "name": "slot", "center": [20, -10], "width": 12, "height": 8}],  # world (70, 20)
    }
    pocket = {
        "id": "slot",
        "op": "cut",
        "intent": "slot",
        "sketch": "slot_sketch",
        "end_condition": "blind",
        "depth_mm": 3,
        "direction": "negative",
    }
    _, trace = _run(adapter, *_plate(), sketch, pocket, _datum("mid", 50), _mirror(_datum_ref("mid"), ["slot"]))
    _assert_green(trace)
    assert "volume_delta" in _asserted_ok(trace, "twin")
    assert _final_volume(trace) == pytest.approx(PLATE_VOLUME - 2 * 288.0, abs=1e-6)


def test_a_boss_mirrored_about_a_planar_face(adapter: FreeCADAdapter) -> None:
    """The +X face of a post (x = 60) is the plane: the boss at x = 30 lands at x = 90."""
    post_sketch = {
        "id": "post_sketch",
        "op": "sketch",
        "intent": "post outline",
        "on": TOP,
        "entities": [{"kind": "rect", "name": "p", "center": [0, 0], "width": 20, "height": 20}],  # x 40..60
    }
    post = {"id": "post", "op": "extrude", "intent": "post", "sketch": "post_sketch", "depth_mm": 10}
    post_face = {"kind": "face", "from_op": "post", "select": {"normal": [1, 0, 0]}}
    _, trace = _run(adapter, *_plate(), post_sketch, post, *_boss((30.0, 10.0)), _mirror(post_face, ["boss"]))
    _assert_green(trace)
    assert _asserted_ok(trace, "twin") >= {"instance_count", "volume_delta"}
    assert _final_volume(trace) == pytest.approx(PLATE_VOLUME + 20 * 20 * 10 + 2 * BOSS_VOLUME, abs=1e-6)


def test_an_oblique_plane_is_exact_in_the_kernel_and_reported_by_the_verifier(adapter: FreeCADAdapter) -> None:
    """A boss at (10, 10) across the plane through (0, 30, 0) with normal (1, 1, 0) lands at (20, 20). The reflected
    box is not the box of the reflection there, so the volume is REPORTED (and still right) and no box row exists."""
    tilted = {
        "id": "tilted",
        "op": "ref_plane",
        "intent": "oblique mirror plane",
        "define": {
            "how": "angle",
            "base": YZ,
            "about": {"origin": [0, 30, 0], "direction": [0, 0, 1]},
            "angle_deg": 45,
        },
    }
    _, trace = _run(adapter, *_plate(), *_boss((10.0, 10.0)), tilted, _mirror(_datum_ref("tilted"), ["boss"]))
    _assert_green(trace)
    volume = _check(trace, "twin", "volume_delta")
    assert volume.kind is CheckKind.reported
    assert volume.observed == pytest.approx(BOSS_VOLUME, abs=1e-6)
    assert "instance_count" in _asserted_ok(trace, "twin")
    step = trace.step("twin")
    assert step is not None
    assert "bbox_contains" not in {c.name for c in step.last_level2()}
    assert _final_volume(trace) == pytest.approx(PLATE_VOLUME + 2 * BOSS_VOLUME, abs=1e-6)


def test_a_primitive_body_cannot_be_mirrored_as_a_feature(adapter: FreeCADAdapter) -> None:
    block = {"id": "block", "op": "box", "intent": "block", "size": [10, 20, 30]}
    _, trace = _run(adapter, block, _mirror(YZ, ["block"]))
    assert trace.final_state is ExecutorState.failed
    step = trace.step("twin")
    assert step is not None
    assert "mirror_body" in " | ".join(a.error.message for a in step.attempts if a.error)


# --- mirror_body ----------------------------------------------------------------------------------------------------


def _mirror_body(plane: dict[str, Any], source: str = "block", op_id: str = "twin") -> dict[str, Any]:
    return {
        "id": op_id,
        "op": "mirror_body",
        "intent": "other hand",
        "source": {"kind": "body", "from_op": source},
        "plane": plane,
    }


def test_a_moved_and_rotated_body_is_mirrored_in_world_terms(adapter: FreeCADAdapter) -> None:
    """Box 10 x 20 x 30, turned 90 deg about Z (x in [-20, 0], y in [0, 10]) and moved 100 along Y, then reflected
    across x = 5 (x' = 10 - x): box (10, 100, 0)..(30, 110, 30), centroid (-10, 105, 15) -> (20, 105, 15)."""
    block = {"id": "block", "op": "box", "intent": "block", "size": [10, 20, 30]}
    turn = {
        "id": "turn",
        "op": "rotate",
        "intent": "turn",
        "target": {"kind": "body", "from_op": "block"},
        "axis": "Z",
        "angle_deg": 90,
    }
    slide = {
        "id": "slide",
        "op": "translate",
        "intent": "slide",
        "target": {"kind": "body", "from_op": "block"},
        "offset": [0, 100, 0],
    }
    _, trace = _run(adapter, block, turn, slide, _datum("mid", 5), _mirror_body(_datum_ref("mid")))
    _assert_green(trace)
    assert _asserted_ok(trace, "twin") == {
        "volume_delta",
        "copy_centroid",
        "copy_surface",
        "source_centroid",
        "copy_bbox",
    }
    twin = _body(trace, "twin")
    box = adapter.bounding_box(MassRequest(operation_id="q", body=twin))
    assert _close(box.min, (10, 100, 0)) and _close(box.max, (30, 110, 30)), box
    assert _close(adapter.mass_properties(MassRequest(operation_id="q", body=twin)).centroid, (20, 105, 15))
    assert _final_volume(trace) == pytest.approx(2 * 6000.0, abs=1e-6)


def test_a_partdesign_body_with_a_hole_is_mirrored_with_its_hole(adapter: FreeCADAdapter) -> None:
    plate = {"id": "block", "op": "extrude", "intent": "plate", "sketch": "plate_sketch", "depth_mm": 8}
    top = {"kind": "face", "from_op": "block", "select": {"normal": [0, 0, 1]}}
    bore = {**_hole(), "face": top}
    ops = [_plate()[0], plate, bore, _datum("far", 120), _mirror_body(_datum_ref("far"))]
    _, trace = _run(adapter, *ops)
    _assert_green(trace)
    twin = _body(trace, "twin")
    assert adapter.mass_properties(MassRequest(operation_id="q", body=twin)).volume_mm3 == pytest.approx(
        PLATE_VOLUME - HOLE_VOLUME, abs=1e-6
    )
    assert _final_volume(trace) == pytest.approx(2 * (PLATE_VOLUME - HOLE_VOLUME), abs=1e-6)


def test_a_mirrored_body_across_an_oblique_plane_reports_its_box(adapter: FreeCADAdapter) -> None:
    block = {"id": "block", "op": "box", "intent": "block", "size": [10, 20, 30]}
    tilted = {
        "id": "tilted",
        "op": "ref_plane",
        "intent": "oblique plane",
        "define": {"how": "angle", "base": YZ, "about": {"origin": [0, 0, 0], "direction": [0, 0, 1]}, "angle_deg": 20},
    }
    _, trace = _run(adapter, block, tilted, _mirror_body(_datum_ref("tilted")))
    _assert_green(trace)
    assert _check(trace, "twin", "copy_bbox").kind is CheckKind.reported
    assert {"volume_delta", "copy_centroid", "copy_surface", "source_centroid"} <= _asserted_ok(trace, "twin")


# --- an instance that lands on an earlier feature: reported, and still right in the kernel --------------------------


def _kind(trace: ExecutionTrace, op_id: str, name: str) -> CheckKind:
    return _check(trace, op_id, name).kind


def _bore(op_id: str, at: tuple[float, float]) -> dict[str, Any]:
    return {**_hole(at), "id": op_id}


def test_a_mirrored_bore_that_lands_on_an_earlier_bore_is_reported_not_asserted(adapter: FreeCADAdapter) -> None:
    """bore2 at (72, 30) and the seed at (25, 30) about x = 50: the reflection at (75, 30) overlaps bore2, so it removes
    142.06 mm3 instead of 226.19. The model is correct; the step must pass with the change REPORTED."""
    ops = [*_plate(), _bore("bore2", (72.0, 30.0)), _bore("bore", (25.0, 30.0)), _datum("mid", 50)]
    _, trace = _run(adapter, *ops, _mirror(_datum_ref("mid"), ["bore"]))
    _assert_green(trace)
    volume = _check(trace, "twin", "volume_delta")
    assert volume.kind is CheckKind.reported
    assert volume.observed == pytest.approx(-142.06, abs=0.01)
    assert _kind(trace, "twin", "created_faces_count") is CheckKind.reported
    assert "instance_count" in _asserted_ok(trace, "twin")


def test_a_mirrored_boss_that_lands_on_an_earlier_boss_is_reported_not_asserted(adapter: FreeCADAdapter) -> None:
    """boss2 at (88, 10) and the seed at (10, 10) about x = 50: the reflection at (90, 10) overlaps boss2, adding
    124.56 mm3 instead of 392.70 and merging a face."""
    ops = [*_plate(), *_boss((88.0, 10.0), "boss2"), *_boss((10.0, 10.0)), _datum("mid", 50)]
    _, trace = _run(adapter, *ops, _mirror(_datum_ref("mid"), ["boss"]))
    _assert_green(trace)
    volume = _check(trace, "twin", "volume_delta")
    assert volume.kind is CheckKind.reported
    assert volume.observed == pytest.approx(124.56, abs=0.01)
    assert _kind(trace, "twin", "created_faces_count") is CheckKind.reported


def test_a_linear_pattern_instance_that_lands_on_an_earlier_bore_is_reported_not_asserted(
    adapter: FreeCADAdapter,
) -> None:
    """A row of three bores 20 mm apart from (25, 30); bore2 at (45, 30) is where the second one lands."""
    row = {
        "id": "row",
        "op": "linear_pattern",
        "intent": "row of bores",
        "features": ["bore"],
        "direction": "X",
        "spacing_mm": 20,
        "count": 3,
    }
    _, trace = _run(adapter, *_plate(), _bore("bore2", (45.0, 30.0)), _bore("bore", (25.0, 30.0)), row)
    _assert_green(trace)
    assert _kind(trace, "row", "volume_delta") is CheckKind.reported
    assert _kind(trace, "row", "created_faces_count") is CheckKind.reported
    assert "instance_count" in _asserted_ok(trace, "row")


def test_an_earlier_feature_clear_of_the_reflection_keeps_the_checks_asserted(adapter: FreeCADAdapter) -> None:
    ops = [*_plate(), _bore("bore2", (90.0, 50.0)), _bore("bore", (25.0, 30.0)), _datum("mid", 50)]
    _, trace = _run(adapter, *ops, _mirror(_datum_ref("mid"), ["bore"]))
    _assert_green(trace)
    assert {"instance_count", "created_faces_count", "volume_delta"} <= _asserted_ok(trace, "twin")


# --- mixed seeds ----------------------------------------------------------------------------------------------------


def _slanted_plane() -> dict[str, Any]:
    """x + y = 100: three points on it, so its normal is (1, 1, 0)."""
    return {
        "id": "slant",
        "op": "ref_plane",
        "intent": "oblique mirror plane",
        "define": {"how": "three_points", "points": [[100, 0, 0], [0, 100, 0], [0, 100, 10]]},
    }


def test_a_mixed_mirror_that_leaves_a_boss_floating_is_refused(adapter: FreeCADAdapter) -> None:
    """The boss at (10, 10) lands at (90, 90), off the 100 x 60 plate; the bore at (60, 30) lands on it at (70, 40).
    The bore's volume must not hide the floating boss."""
    ops = [*_plate(), *_boss((10.0, 10.0)), _bore("bore", (60.0, 30.0)), _slanted_plane()]
    _, trace = _run(adapter, *ops, _mirror(_datum_ref("slant"), ["boss", "bore"]))
    assert trace.final_state is ExecutorState.failed
    step = trace.step("twin")
    assert step is not None
    message = " | ".join(a.error.message for a in step.attempts if a.error)
    assert "boss" in message and "touch" in message
    assert len(step.attempts) == 1


def test_a_mirror_of_a_pattern_says_to_mirror_its_seed_and_re_pattern(adapter: FreeCADAdapter) -> None:
    row = {
        "id": "row",
        "op": "linear_pattern",
        "intent": "row of bores",
        "features": ["bore"],
        "direction": "X",
        "spacing_mm": 15,
        "count": 2,
    }
    _, trace = _run(
        adapter, *_plate(), _bore("bore", (10.0, 10.0)), row, _datum("mid", 50), _mirror(_datum_ref("mid"), ["row"])
    )
    assert trace.final_state is ExecutorState.failed
    step = trace.step("twin")
    assert step is not None
    message = " | ".join(a.error.message for a in step.attempts if a.error)
    assert "re-pattern" in message and "mirror_body" not in message
