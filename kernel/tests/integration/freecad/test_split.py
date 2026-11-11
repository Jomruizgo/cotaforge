"""``split``, ``copy_body.scale`` and ``boolean_subtract(keep_tools)`` with a featured PartDesign tool against the real
FreeCAD worker (``common`` with a half-space box, ``Shape.scale``, a ToolCopy of the tool's placed shape).

Every test drives an IR plan through ``Executor`` + ``Verifier`` so the Level 2 checks run on real FreeCAD answers; the
expected numbers are derived by hand: the pieces of a cut add up to the source, a plane through three points
(30, 0, 0), (0, 30, 0), (0, 0, 30) cuts off a tetrahedron of 30^3 / 6 = 4500 mm3 minus the 10^3 / 6 the 20 mm box does
not reach, a scaled copy has s^3 the volume. Run on both supported FreeCAD lines: they agree to 1e-9.
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
TOP = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}
BLOCK_VOLUME = 100.0 * 60.0 * 20.0
PLATE_VOLUME = 48000.0
HOLE_VOLUME = math.pi * 9.0 * 8.0  # d 6 through 8 mm
SPLIT_CHECKS = {"volume_delta", "split_volume_sum", "split_centroid", "split_sides"}


def _run(adapter: FreeCADAdapter, *operations: dict[str, Any]) -> tuple[Executor, ExecutionTrace]:
    plan = parse_plan({"id": "mould", "kind": "part", "operations": list(operations)})
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


def _bodies(trace: ExecutionTrace, op_id: str) -> tuple[BodyHandle, ...]:
    step = trace.step(op_id)
    assert step is not None and step.attempts and step.attempts[-1].result is not None
    return step.attempts[-1].result.created_bodies


def _volume_of(adapter: FreeCADAdapter, body: BodyHandle) -> float:
    return adapter.mass_properties(MassRequest(operation_id="q", body=body)).volume_mm3


def _block(op_id: str = "block", size: tuple[float, float, float] = (100, 60, 20)) -> dict[str, Any]:
    return {"id": op_id, "op": "box", "intent": "stock", "size": list(size)}


def _datum(op_id: str, define: dict[str, Any]) -> dict[str, Any]:
    return {"id": op_id, "op": "ref_plane", "intent": "parting plane", "define": define}


def _height(op_id: str, z: float) -> dict[str, Any]:
    return _datum(op_id, {"how": "offset", "base": XY, "distance_mm": z})


def _split(plane: str, source: dict[str, Any] | None = None, keep: str | None = None, op_id: str = "halves") -> dict:
    op: dict[str, Any] = {
        "id": op_id,
        "op": "split",
        "intent": "cut at the parting plane",
        "source": source or {"kind": "body", "from_op": "block"},
        "plane": {"kind": "plane", "from_op": plane},
    }
    if keep is not None:
        op["keep"] = keep
    return op


def _plate() -> list[dict[str, Any]]:
    return [
        {
            "id": "plate_sketch",
            "op": "sketch",
            "intent": "plate outline",
            "on": XY,
            "entities": [{"kind": "rect", "name": "r", "center": [50, 30], "width": 100, "height": 60}],
        },
        {"id": "base", "op": "extrude", "intent": "plate", "sketch": "plate_sketch", "depth_mm": 8},
    ]


def _hole() -> dict[str, Any]:
    return {
        "id": "bore",
        "op": "hole",
        "intent": "bore",
        "face": TOP,
        "position": [-40, -20],
        "standard": "simple",
        "diameter_mm": 6,
        "end_condition": "through_all",
    }


# --- split ----------------------------------------------------------------------------------------------------------


def test_a_box_split_at_an_axis_aligned_plane_passes_every_check(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, _block(), _height("part_line", 5), _split("part_line"))
    _assert_green(trace)
    assert _asserted_ok(trace, "halves") == SPLIT_CHECKS
    positive, negative = _bodies(trace, "halves")
    assert _volume_of(adapter, positive) == pytest.approx(90000.0, abs=1e-6)
    assert _volume_of(adapter, negative) == pytest.approx(30000.0, abs=1e-6)
    assert _final_volume(trace) == pytest.approx(BLOCK_VOLUME, abs=1e-6)


def test_an_oblique_plane_through_three_points_cuts_off_the_corner_tetrahedron(adapter: FreeCADAdapter) -> None:
    """x + y + z = 30 cuts a 30 mm tetrahedron (4500) off the corner, minus the 10 mm cap the box does not reach."""
    tilted = _datum("tilt", {"how": "three_points", "points": [[30, 0, 0], [0, 30, 0], [0, 0, 30]]})
    _, trace = _run(adapter, _block(), tilted, _split("tilt"))
    _assert_green(trace)
    assert _asserted_ok(trace, "halves") == SPLIT_CHECKS  # the sum, the centroid and the sides hold for any plane
    positive, negative = _bodies(trace, "halves")
    corner = 4500.0 - 1000.0 / 6.0
    assert _volume_of(adapter, negative) == pytest.approx(corner, abs=1e-6)
    assert _volume_of(adapter, positive) == pytest.approx(BLOCK_VOLUME - corner, abs=1e-6)
    assert _final_volume(trace) == pytest.approx(BLOCK_VOLUME, abs=1e-6)


def test_a_moved_and_rotated_box_is_split_where_it_stands(adapter: FreeCADAdapter) -> None:
    lift = {
        "id": "lift",
        "op": "translate",
        "intent": "up",
        "target": {"kind": "body", "from_op": "block"},
        "offset": [10, 20, 30],
    }
    turn = {
        "id": "turn",
        "op": "rotate",
        "intent": "quarter turn",
        "target": {"kind": "body", "from_op": "lift"},
        "axis": "Z",
        "angle_deg": 90,
        "about": [0, 0, 0],
    }
    source = {"kind": "body", "from_op": "turn"}
    _, trace = _run(adapter, _block(), lift, turn, _height("part_line", 40), _split("part_line", source))
    _assert_green(trace)
    positive, negative = _bodies(trace, "halves")
    assert _volume_of(adapter, positive) == pytest.approx(60000.0, abs=1e-6)  # the box now spans z 30..50
    assert _volume_of(adapter, negative) == pytest.approx(60000.0, abs=1e-6)
    assert _final_volume(trace) == pytest.approx(BLOCK_VOLUME, abs=1e-6)


def test_the_pieces_are_addressed_by_side_and_can_be_moved_apart(adapter: FreeCADAdapter) -> None:
    def move(side: str, dz: float, op_id: str) -> dict[str, Any]:
        target = {"kind": "body", "from_op": "halves", "side": side}
        return {"id": op_id, "op": "translate", "intent": "open the mould", "target": target, "offset": [0, 0, dz]}

    _, trace = _run(
        adapter,
        _block(),
        _height("part_line", 5),
        _split("part_line"),
        move("positive", 30, "lift"),
        move("negative", -30, "drop"),
    )
    _assert_green(trace)
    assert trace.final_bbox is not None
    assert tuple(trace.final_bbox.min) == pytest.approx((0.0, 0.0, -30.0), abs=1e-6)
    assert tuple(trace.final_bbox.max) == pytest.approx((100.0, 60.0, 50.0), abs=1e-6)
    assert _final_volume(trace) == pytest.approx(BLOCK_VOLUME, abs=1e-6)


def test_a_split_that_keeps_one_piece_asserts_the_kept_volume_of_a_tracked_box(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, _block(), _height("part_line", 5), _split("part_line", keep="negative"))
    _assert_green(trace)
    assert _asserted_ok(trace, "halves") == {"volume_delta", "split_sides"}
    assert _final_volume(trace) == pytest.approx(30000.0, abs=1e-6)
    assert len(_bodies(trace, "halves")) == 1


def test_the_kept_volume_is_reported_not_asserted_once_a_hole_edited_the_body(adapter: FreeCADAdapter) -> None:
    """A d6 hole in the 8 mm plate: the kept 3 mm slab is 100 x 60 x 3 - pi 9 x 3, which no box formula knows."""
    _, trace = _run(
        adapter,
        *_plate(),
        _hole(),
        _height("part_line", 3),
        _split("part_line", {"kind": "body", "from_op": "base"}, "negative"),
    )
    _assert_green(trace)
    assert _check(trace, "halves", "volume_delta").kind is CheckKind.reported
    assert "split_sides" in _asserted_ok(trace, "halves")
    assert _final_volume(trace) == pytest.approx(18000.0 - math.pi * 9.0 * 3.0, abs=1e-6)


def test_a_featured_body_is_split_with_its_hole_and_the_checks_hold(adapter: FreeCADAdapter) -> None:
    mid = _datum("mid", {"how": "offset", "base": {"kind": "plane", "name": "YZ"}, "distance_mm": 50})
    _, trace = _run(adapter, *_plate(), _hole(), mid, _split("mid", {"kind": "body", "from_op": "base"}))
    _assert_green(trace)
    assert _asserted_ok(trace, "halves") == SPLIT_CHECKS
    positive, negative = _bodies(trace, "halves")
    assert _volume_of(adapter, positive) == pytest.approx(24000.0, abs=1e-6)
    assert _volume_of(adapter, negative) == pytest.approx(24000.0 - HOLE_VOLUME, abs=1e-6)


def test_a_plane_that_misses_the_body_fails_once_with_a_clear_message(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, _block(), _height("part_line", 50), _split("part_line"))
    assert trace.final_state is ExecutorState.failed
    step = trace.step("halves")
    assert step is not None
    assert len(step.attempts) == 1  # deterministic: asking again cannot help
    assert "plane does not cut the body" in " | ".join(a.error.message for a in step.attempts if a.error)


# --- scale ----------------------------------------------------------------------------------------------------------


def _scaled(
    source: str, scale: float, about: list[float], offset: list[float], op_id: str = "shrunk"
) -> dict[str, Any]:
    return {
        "id": op_id,
        "op": "copy_body",
        "intent": "shrinkage allowance",
        "source": {"kind": "body", "from_op": source},
        "scale": scale,
        "scale_about": about,
        "offset": offset,
    }


def test_a_scaled_box_copy_has_exactly_the_cubed_volume_and_passes_every_check(adapter: FreeCADAdapter) -> None:
    s = 1.006
    _, trace = _run(adapter, _block(), _scaled("block", s, [50, 30, 10], [150, 0, 0]))
    _assert_green(trace)
    assert _asserted_ok(trace, "shrunk") == {
        "volume_delta",
        "copy_centroid",
        "copy_surface",
        "source_centroid",
        "copy_bbox",
    }
    assert _final_volume(trace) == pytest.approx(BLOCK_VOLUME * (1 + s**3), abs=1e-6)
    (twin,) = _bodies(trace, "shrunk")
    assert _volume_of(adapter, twin) == pytest.approx(s**3 * BLOCK_VOLUME, abs=1e-6)


def test_a_scaled_featured_part_copy_passes_every_check(adapter: FreeCADAdapter) -> None:
    s = 2.0
    _, trace = _run(adapter, *_plate(), _hole(), _scaled("base", s, [10, 10, 0], [0, 0, 100]))
    _assert_green(trace)
    assert {"volume_delta", "copy_centroid", "copy_surface"} <= _asserted_ok(trace, "shrunk")
    assert _final_volume(trace) == pytest.approx((PLATE_VOLUME - HOLE_VOLUME) * (1 + s**3), abs=1e-6)


# --- the mould flow: block - part (kept), split at the parting plane -------------------------------------------------


def test_core_cavity_and_part_fill_the_block_exactly(adapter: FreeCADAdapter) -> None:
    """Block - part (the featured part survives as the tool), then a cut at the parting plane: the cavity, the core
    and the part fill the block exactly."""
    block = {
        "id": "block",
        "op": "box",
        "intent": "mould block",
        "size": [120, 80, 38],
        "placement": {"origin": [-10, -10, -10]},
    }
    cavity = {
        "id": "cavity",
        "op": "boolean_subtract",
        "intent": "press the part into the block",
        "target": {"kind": "body", "from_op": "block"},
        "tools": [{"kind": "body", "from_op": "base"}],
        "keep_tools": True,
    }
    _, trace = _run(adapter, *_plate(), _hole(), block, cavity, _height("parting", 4), _split("parting"))
    _assert_green(trace)
    assert _asserted_ok(trace, "halves") == SPLIT_CHECKS
    positive, negative = _bodies(trace, "halves")
    block_volume = 120.0 * 80.0 * 38.0
    part_volume = PLATE_VOLUME - HOLE_VOLUME
    assert _volume_of(adapter, positive) + _volume_of(adapter, negative) + part_volume == pytest.approx(
        block_volume, abs=1e-6
    )
    assert _final_volume(trace) == pytest.approx(block_volume, abs=1e-6)
