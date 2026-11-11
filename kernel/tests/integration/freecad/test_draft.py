"""``draft`` against the real FreeCAD worker (PartDesign::Draft about a PartDesign::Plane datum).

Every test drives an IR plan through ``Executor`` + ``Verifier`` so the Level 2 checks run on real FreeCAD answers, and
each expected number is derived by hand from the section of the drafted body at height ``w`` above the neutral plane
(a drafted wall sits ``w tan(angle)`` further inside). Run on both supported FreeCAD lines: they agree to 1e-9.
"""

import math
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    AdapterError,
    DraftRequest,
    ErrorCode,
    FaceHandle,
    MassRequest,
    PlaneName,
    frame_for_plane,
)
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier

pytestmark = pytest.mark.freecad

BOX = {"id": "block", "op": "box", "intent": "block", "size": [40, 30, 20]}
BOX_VOLUME = 24000.0
CYLINDER = {"id": "block", "op": "cylinder", "intent": "slug", "diameter_mm": 20, "height_mm": 30}  # R10 H30
XY = {"kind": "plane", "name": "XY"}
SIDES = [
    {"kind": "face", "from_op": "block", "select": {"normal": n}}
    for n in ([1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0])
]
XMAX = SIDES[0]
TOP = {"kind": "face", "from_op": "block", "select": {"normal": [0, 0, 1]}}


def _tan(angle: float) -> float:
    return math.tan(math.radians(angle))


def _draft(
    faces: list[dict], angle: float = 3.0, *, neutral: dict | None = None, pull: Any = "Z", op_id: str = "taper"
) -> dict:
    return {
        "id": op_id,
        "op": "draft",
        "intent": "let the mould open",
        "faces": faces,
        "neutral": neutral or XY,
        "pull": pull,
        "angle_deg": angle,
    }


def _datum(op_id: str, distance: float) -> dict:
    return {
        "id": op_id,
        "op": "ref_plane",
        "intent": "neutral plane",
        "define": {"how": "offset", "base": XY, "distance_mm": distance},
    }


def _box_taper(extents: tuple[float, float], k: tuple[int, int], angle: float, lo: float, hi: float) -> float:
    """``integral (a - k1 t w)(b - k2 t w) dw`` over ``lo .. hi`` (hand-derived, expanded)."""
    a, b = extents
    t = _tan(angle)
    return (
        a * b * (hi - lo) - (a * k[1] + b * k[0]) * t * (hi**2 - lo**2) / 2 + k[0] * k[1] * t * t * (hi**3 - lo**3) / 3
    )


def _run(adapter: FreeCADAdapter, *operations: dict[str, Any]) -> tuple[Executor, ExecutionTrace]:
    plan = parse_plan({"id": "tapers", "kind": "part", "operations": list(operations)})
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


def _check(trace: ExecutionTrace, op_id: str, name: str) -> Any:
    step = trace.step(op_id)
    assert step is not None
    return next(c for c in step.last_level2() if c.name == name)


def _asserted_ok(trace: ExecutionTrace, op_id: str) -> set[str]:
    step = trace.step(op_id)
    assert step is not None, op_id
    return {c.name for c in step.last_level2() if c.kind is CheckKind.asserted and c.ok}


def _final_volume(trace: ExecutionTrace) -> float:
    assert trace.final_mass is not None
    return trace.final_mass.volume_mm3


# --- a box: closed forms, every check asserted ----------------------------------------------------------------------


def test_four_sides_drafted_from_the_bottom(adapter: FreeCADAdapter) -> None:
    """40 x 30 x 20, 3 deg, neutral at the bottom, pulled up: the walls narrow upwards, V = 22561.879."""
    _, trace = _run(adapter, BOX, _draft(SIDES))
    _assert_green(trace)
    assert _asserted_ok(trace, "taper") == {"draft_angle", "volume_delta"}
    assert _final_volume(trace) == pytest.approx(_box_taper((40, 30), (2, 2), 3.0, 0, 20), abs=1e-6)
    assert _final_volume(trace) == pytest.approx(22561.879, abs=1e-3)
    assert _check(trace, "taper", "draft_angle").observed == pytest.approx(3.0, abs=1e-6)


def test_a_mid_height_pivot_keeps_the_waist_and_adds_only_the_second_order_term(adapter: FreeCADAdapter) -> None:
    """The neutral plane at z = 10: the wedge below adds what the wedge above removes, V = abh + t^2 h^3 / 3."""
    _, trace = _run(adapter, BOX, _datum("mid", 10), _draft(SIDES, neutral={"kind": "plane", "from_op": "mid"}))
    _assert_green(trace)
    assert _asserted_ok(trace, "taper") == {"draft_angle", "volume_delta"}
    expected = 24000.0 + _tan(3.0) ** 2 * 20**3 / 3
    assert _final_volume(trace) == pytest.approx(expected, abs=1e-6)
    assert _final_volume(trace) == pytest.approx(24007.3242, abs=1e-3)


def test_pulling_down_from_a_neutral_face_at_the_top_is_the_mirror_image(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, BOX, _draft(SIDES, neutral=TOP, pull=[0, 0, -1]))
    _assert_green(trace)
    assert _asserted_ok(trace, "taper") == {"draft_angle", "volume_delta"}
    assert _final_volume(trace) == pytest.approx(22561.879, abs=1e-3)


def test_a_signed_pull_decides_which_way_the_walls_lean(adapter: FreeCADAdapter) -> None:
    """Same plane, same angle, opposite pull: the walls WIDEN upwards (V = 24000 + 1467.4 + 29.3 = 25496.7146)."""
    _, trace = _run(adapter, BOX, _draft(SIDES, pull=[0, 0, -1]))
    _assert_green(trace)
    assert _asserted_ok(trace, "taper") == {"draft_angle", "volume_delta"}
    assert _final_volume(trace) == pytest.approx(_box_taper((40, 30), (2, 2), 3.0, -20, 0), abs=1e-6)
    assert _final_volume(trace) == pytest.approx(25496.7146, abs=1e-3)


def test_one_wall_drafted_alone(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, BOX, _draft([XMAX], 5.0))
    _assert_green(trace)
    assert _final_volume(trace) == pytest.approx(_box_taper((40, 30), (1, 0), 5.0, 0, 20), abs=1e-6)
    assert _asserted_ok(trace, "taper") == {"draft_angle", "volume_delta"}


def test_the_largest_angle_still_builds(adapter: FreeCADAdapter) -> None:
    """30 deg: the 30 mm wall narrows to 30 - 2 * 20 * tan(30) = 6.9 mm at the top and is still a valid solid."""
    _, trace = _run(adapter, BOX, _draft(SIDES, 30.0))
    _assert_green(trace)
    assert _final_volume(trace) == pytest.approx(_box_taper((40, 30), (2, 2), 30.0, 0, 20), abs=1e-6)


def test_a_moved_body_is_drafted_about_the_neutral_plane_where_it_now_stands(adapter: FreeCADAdapter) -> None:
    move = {
        "id": "lift",
        "op": "translate",
        "intent": "move it",
        "target": {"kind": "body", "from_op": "block"},
        "offset": [100, 0, 50],
    }
    _, trace = _run(
        adapter, BOX, move, _datum("floor", 50), _draft(SIDES, neutral={"kind": "plane", "from_op": "floor"})
    )
    _assert_green(trace)
    assert _asserted_ok(trace, "taper") == {"draft_angle", "volume_delta"}
    assert _final_volume(trace) == pytest.approx(22561.879, abs=1e-3)
    assert trace.final_bbox is not None
    assert trace.final_bbox.min[2] == pytest.approx(50.0) and trace.final_bbox.max[2] == pytest.approx(70.0)


# --- a cylinder becomes a cone -------------------------------------------------------------------------------------


def test_a_cylinder_side_becomes_a_cone(adapter: FreeCADAdapter) -> None:
    """R10 H30, 2 deg: V = pi (R^2 H - R t H^2 + t^2 H^3 / 3) = 8471.8959; the semi-angle is not asserted yet."""
    side = {"kind": "face", "from_op": "block", "select": {"surface": "cylinder"}}
    _, trace = _run(adapter, CYLINDER, _draft([side], 2.0))
    _assert_green(trace)
    t = _tan(2.0)
    assert _final_volume(trace) == pytest.approx(math.pi * (100 * 30 - 10 * t * 900 + t * t * 27000 / 3), abs=1e-6)
    assert _final_volume(trace) == pytest.approx(8471.8959, abs=1e-3)
    assert "volume_delta" in _asserted_ok(trace, "taper")
    assert _check(trace, "taper", "draft_angle").kind is CheckKind.reported


# --- pocket walls: reported volume, the lean is still asserted -----------------------------------------------------


def test_the_walls_of_a_pocket_are_drafted_and_only_their_lean_is_asserted(adapter: FreeCADAdapter) -> None:
    """A 10 x 10 pocket 10 deep from the top of a 40 x 30 x 20 block, its four walls drafted 3 deg about the mouth
    (z = 20) with the pull up: the void narrows downwards, V_void = integral (10 - 2 t d)^2 dd over 0 .. 10."""
    ops = [
        {
            "id": "mouth",
            "op": "sketch",
            "intent": "pocket outline",
            "on": TOP,
            "entities": [{"kind": "rect", "name": "r", "center": [0, 0], "width": 10, "height": 10}],
        },
        {"id": "pocket", "op": "cut", "intent": "pocket", "sketch": "mouth", "depth_mm": 10},
    ]
    walls = [
        {"kind": "face", "from_op": "pocket", "select": {"normal": n, "role": "created"}}
        for n in ([1, 0, 0], [-1, 0, 0], [0, 1, 0], [0, -1, 0])
    ]
    t = _tan(3.0)
    void = 100 * 10 - 40 * t * 100 / 2 + 4 * t * t * 1000 / 3
    _, trace = _run(adapter, BOX, *ops, _datum("lip", 20), _draft(walls, neutral={"kind": "plane", "from_op": "lip"}))
    _assert_green(trace)
    assert _check(trace, "taper", "volume_delta").kind is CheckKind.reported
    assert _check(trace, "taper", "draft_angle").ok is True
    assert _final_volume(trace) == pytest.approx(BOX_VOLUME - void, abs=1e-6)


# --- refusals and no-ops --------------------------------------------------------------------------------------------


def test_a_draft_that_would_close_a_tracked_wall_is_a_plan_error_before_the_adapter_is_called(
    adapter: FreeCADAdapter,
) -> None:
    thin = {"id": "block", "op": "box", "intent": "blade", "size": [40, 10, 20]}
    ys = [{"kind": "face", "from_op": "block", "select": {"normal": n}} for n in ([0, 1, 0], [0, -1, 0])]
    _, trace = _run(adapter, thin, _draft(ys, 20.0))
    step = trace.step("taper")
    assert step is not None and step.status is StepStatus.plan_error
    assert "narrows" in str(step.plan_error)
    assert not step.attempts
    assert _final_volume(trace) == pytest.approx(40 * 10 * 20)


def test_a_pull_off_the_neutral_normal_is_a_plan_error(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, BOX, _draft(SIDES, pull="X"))
    step = trace.step("taper")
    assert step is not None and step.status is StepStatus.plan_error
    assert "perpendicular to the neutral plane" in str(step.plan_error)


@pytest.mark.parametrize("faces", [[TOP], [XMAX, TOP], [TOP, XMAX]])
def test_a_face_facing_the_pull_is_a_plan_error_alone_or_among_drafted_ones(
    adapter: FreeCADAdapter, faces: list[dict]
) -> None:
    """On 1.0.2 [wall, top] used to SUCCEED with the top face silently left alone (and draft_angle asserted ok); on
    1.1.3 it failed the feature. Both are now refused before the adapter is called."""
    _, trace = _run(adapter, BOX, _draft(faces, 3.0))
    step = trace.step("taper")
    assert step is not None and step.status is StepStatus.plan_error
    assert "faces the pull" in str(step.plan_error) and "neutral plane" in str(step.plan_error)
    assert not step.attempts
    assert _final_volume(trace) == pytest.approx(BOX_VOLUME, abs=1e-9)


@pytest.mark.parametrize(("first", "second"), [(0.5, 3.0), (0.5, 0.8), (0.9, 2.0)])
def test_a_wall_drafted_again_lands_on_the_absolute_angle_and_is_still_verified(
    adapter: FreeCADAdapter, first: float, second: float
) -> None:
    """FreeCAD's draft is ABSOLUTE: 0.5 then 3.0 leaves 3.0 (one wall drafted once), not 3.5."""
    again = {"kind": "face", "from_op": "first", "select": {"surface": "plane", "nearest_to": [40, 15, 10]}}
    _, trace = _run(adapter, BOX, _draft([XMAX], first, op_id="first"), _draft([again], second, op_id="second"))
    _assert_green(trace)
    assert _asserted_ok(trace, "second") == {"draft_angle"}  # the wall already leans <= 1 deg: still along the pull
    assert _check(trace, "second", "draft_angle").observed == pytest.approx(second, abs=1e-6)
    assert _final_volume(trace) == pytest.approx(_box_taper((40, 30), (1, 0), second, 0, 20), abs=1e-6)


def test_the_adapter_rejects_an_unknown_face_and_leaves_the_part_alone(adapter: FreeCADAdapter) -> None:
    _run(adapter, BOX)
    request = DraftRequest(
        operation_id="direct",
        faces=(FaceHandle(id="face:nope"),),
        neutral=frame_for_plane(PlaneName.XY),
        pull=(0.0, 0.0, 1.0),
        angle_deg=3.0,
    )
    with pytest.raises(AdapterError) as info:
        adapter.draft(request)
    assert info.value.info.code is ErrorCode.invalid_reference
    assert adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(BOX_VOLUME, abs=1e-9)
