"""``shell`` against the real FreeCAD worker (PartDesign::Thickness, Mode Skin).

Thickness builds the WALLS alone. Inward the cavity is the offset of the body and the outer size stays; outward the old
body becomes the cavity and the walls grow outside it (the volume falls for an ordinary part and RISES for a thin one).
Every test drives an IR plan through ``Executor`` + ``Verifier`` so the Level 2 checks run on real FreeCAD answers, and
each expected number is derived by hand in the test. Run on both supported FreeCAD lines: they agree to 1e-9.
"""

import math
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import AdapterError, ErrorCode, FaceHandle, MassRequest, ShellRequest
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.ir.refs import FaceRef
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier

pytestmark = pytest.mark.freecad

TOP = {"kind": "face", "from_op": "block", "select": {"normal": [0, 0, 1]}}
BOTTOM = {"kind": "face", "from_op": "block", "select": {"normal": [0, 0, -1]}}
XMAX = {"kind": "face", "from_op": "block", "select": {"normal": [1, 0, 0]}}
BOX = {"id": "block", "op": "box", "intent": "block", "size": [40, 30, 20]}
CYLINDER = {"id": "block", "op": "cylinder", "intent": "slug", "diameter_mm": 40, "height_mm": 30}  # R20 H30
BOX_VOLUME = 24000.0
CYLINDER_VOLUME = math.pi * 400.0 * 30.0


def _shell(faces: list[dict], t: float = 2.0, op_id: str = "hollow", **extra: Any) -> dict[str, Any]:
    return {"id": op_id, "op": "shell", "intent": "hollow it", "faces": faces, "thickness_mm": t, **extra}


def _run(adapter: FreeCADAdapter, *operations: dict[str, Any]) -> tuple[Executor, ExecutionTrace]:
    plan = parse_plan({"id": "hollows", "kind": "part", "operations": list(operations)})
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


def _close(a: Any, b: Any, tol: float = 1e-6) -> bool:
    return all(abs(x - y) <= tol for x, y in zip(a, b, strict=True))


# --- inward: exact, every check asserted ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("faces", "after"),
    [
        ([TOP], 24000.0 - 36 * 26 * 18),  # 7152: two walls on X and Y, one on Z
        ([TOP, BOTTOM], 24000.0 - 36 * 26 * 20),  # 5280: a through cavity
        ([TOP, XMAX], 24000.0 - 38 * 26 * 18),  # 6216: two adjacent faces open
    ],
)
def test_inward_box_walls_match_the_closed_form(adapter: FreeCADAdapter, faces: list[dict], after: float) -> None:
    _, trace = _run(adapter, BOX, _shell(faces))
    _assert_green(trace)
    assert _asserted_ok(trace, "hollow") == {"volume_sign", "volume_delta", "bbox_within"}
    assert _final_volume(trace) == pytest.approx(after, abs=1e-6)
    assert trace.final_bbox is not None
    assert _close(trace.final_bbox.min, (0, 0, 0)) and _close(trace.final_bbox.max, (40, 30, 20))


def test_inward_cylinder_cup_and_tube(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, CYLINDER, _shell([TOP]))
    _assert_green(trace)
    assert _asserted_ok(trace, "hollow") == {"volume_sign", "volume_delta", "bbox_within"}
    assert _final_volume(trace) == pytest.approx(math.pi * (400 * 30 - 18**2 * 28), abs=1e-6)  # 9198.5833
    _, trace = _run(adapter, CYLINDER, _shell([TOP, BOTTOM]))
    _assert_green(trace)
    assert _final_volume(trace) == pytest.approx(math.pi * (400 - 18**2) * 30, abs=1e-6)  # 7162.8313


def test_the_removed_face_is_reported_modified_and_the_cavity_faces_are_created(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, BOX, _shell([TOP]))
    _assert_green(trace)
    step = trace.step("hollow")
    assert step is not None and step.attempts[-1].result is not None
    result = step.attempts[-1].result
    assert len(result.created_faces) == 5  # four inner walls and the floor
    assert step.resolved_refs["faces[0]"] in {f.id for f in result.modified_faces}


# --- outward: the old body becomes the cavity ------------------------------------------------------------------------


def test_outward_box_keeps_only_the_skin_and_grows_the_box(adapter: FreeCADAdapter) -> None:
    """Grown box 44 x 34 x 22 minus the 24000 it contains = 8912 (the volume FALLS); box (-2,-2,-2)..(42,32,20)."""
    _, trace = _run(adapter, BOX, _shell([TOP], direction="outward"))
    _assert_green(trace)
    assert _asserted_ok(trace, "hollow") == {"volume_delta", "bbox_after"}
    assert _final_volume(trace) == pytest.approx(44 * 34 * 22 - BOX_VOLUME, abs=1e-6)
    assert trace.final_bbox is not None
    assert _close(trace.final_bbox.min, (-2, -2, -2)) and _close(trace.final_bbox.max, (42, 32, 20))
    sign = _check(trace, "hollow", "volume_sign")  # outward: only reported
    assert sign.kind is CheckKind.reported and sign.observed == pytest.approx(8912.0 - BOX_VOLUME)


def test_outward_on_a_thin_part_raises_the_volume_and_still_passes(adapter: FreeCADAdapter) -> None:
    """10 x 10 x 2, top removed, 5 mm outward: 20 x 20 x 7 - 200 = 2600 against 200 before. A sign check that assumed
    'outward adds material' or 'shell removes material' would fail one of the two cases."""
    thin = {"id": "block", "op": "box", "intent": "chip", "size": [10, 10, 2]}
    _, trace = _run(adapter, thin, _shell([TOP], 5.0, direction="outward"))
    _assert_green(trace)
    assert _final_volume(trace) == pytest.approx(2600.0, abs=1e-6)
    assert _check(trace, "hollow", "volume_delta").observed == pytest.approx(2400.0)
    assert "volume_delta" in _asserted_ok(trace, "hollow")
    assert _check(trace, "hollow", "volume_sign").kind is CheckKind.reported


def test_outward_cylinder_cap_matches_the_closed_form(adapter: FreeCADAdapter) -> None:
    _, trace = _run(adapter, CYLINDER, _shell([TOP], direction="outward"))
    _assert_green(trace)
    assert _final_volume(trace) == pytest.approx(math.pi * (22**2 * 32 - 20**2 * 30), abs=1e-6)  # 10957.8752
    assert "volume_delta" in _asserted_ok(trace, "hollow")


def test_the_arc_join_is_reported_with_its_hand_derived_volume(adapter: FreeCADAdapter) -> None:
    """Rounded outside: 8000 (slabs) + 80 pi (vertical edges) + 140 pi (bottom edges) + 16 pi / 3 (corners)."""
    _, trace = _run(adapter, BOX, _shell([TOP], direction="outward", join="arc"))
    _assert_green(trace)
    delta = _check(trace, "hollow", "volume_delta")
    assert delta.kind is CheckKind.reported
    assert _final_volume(trace) == pytest.approx(8000.0 + 220.0 * math.pi + 16.0 * math.pi / 3.0, abs=1e-6)
    assert _check(trace, "hollow", "volume_sign").kind is CheckKind.reported


# --- what the tracker cannot vouch for is reported, not asserted ----------------------------------------------------


def test_a_partdesign_plate_is_reported_but_the_sign_and_the_box_are_asserted(adapter: FreeCADAdapter) -> None:
    ops = [
        {
            "id": "sk",
            "op": "sketch",
            "intent": "outline",
            "on": {"kind": "plane", "name": "XY"},
            "entities": [{"kind": "rect", "name": "r", "center": [50, 30], "width": 100, "height": 60}],
        },
        {"id": "block", "op": "extrude", "intent": "plate", "sketch": "sk", "depth_mm": 8},
    ]
    _, trace = _run(adapter, *ops, _shell([TOP]))
    _assert_green(trace)
    assert _check(trace, "hollow", "volume_delta").kind is CheckKind.reported
    assert _asserted_ok(trace, "hollow") == {"volume_sign", "bbox_within"}
    assert _final_volume(trace) == pytest.approx(100 * 60 * 8 - 96 * 56 * 6, abs=1e-6)  # 15744


def test_a_second_shell_is_reported_because_the_first_one_forgot_the_box(adapter: FreeCADAdapter) -> None:
    # 0.5 mm: a second wall of 1 mm would meet the first 2 mm wall from both sides and the guard rightly refuses it
    again = _shell([BOTTOM], 0.5, op_id="again")
    _, trace = _run(adapter, BOX, _shell([TOP]), again)
    _assert_green(trace)
    assert _check(trace, "again", "volume_delta").kind is CheckKind.reported
    assert "volume_sign" in _asserted_ok(trace, "again")


def test_five_of_six_faces_removed_leaves_a_slab_and_is_not_mistaken_for_a_no_op(adapter: FreeCADAdapter) -> None:
    """Only the bottom stays: no wall on X or Y, one on Z, so the cavity is 40 x 30 x 18 and the slab is 2400.
    The face count does not grow (6 before, 6 after), which a face-count test would take for a silent no-op."""
    xmin = {"kind": "face", "from_op": "block", "select": {"normal": [-1, 0, 0]}}
    ymax = {"kind": "face", "from_op": "block", "select": {"normal": [0, 1, 0]}}
    ymin = {"kind": "face", "from_op": "block", "select": {"normal": [0, -1, 0]}}
    _, trace = _run(adapter, BOX, _shell([TOP, XMAX, xmin, ymax, ymin]))
    _assert_green(trace)
    assert _final_volume(trace) == pytest.approx(40 * 30 * 2, abs=1e-6)
    assert {"volume_sign", "volume_delta"} <= _asserted_ok(trace, "hollow")


# --- the silent no-ops of Thickness never reach a green trace -------------------------------------------------------


def test_a_wall_too_thick_for_a_tracked_box_is_a_plan_error_before_the_adapter_is_called(
    adapter: FreeCADAdapter,
) -> None:
    _, trace = _run(adapter, BOX, _shell([TOP], 16.0))
    step = trace.step("hollow")
    assert step is not None and step.status is StepStatus.plan_error
    assert "leaves no cavity along Y" in str(step.plan_error)
    assert not step.attempts
    assert _final_volume(trace) == pytest.approx(BOX_VOLUME)


@pytest.mark.parametrize("thickness", [15.0, 16.0, 19.0])
def test_the_adapter_itself_refuses_a_wall_that_does_not_fit_and_changes_nothing(
    adapter: FreeCADAdapter, thickness: float
) -> None:
    """Straight on the adapter (no tracker in front of it): Thickness answers 'Up-to-date' with the shape unchanged
    (16, 19) or invalid (exactly half, 15)."""
    executor, trace = _run(adapter, BOX)
    _assert_green(trace)
    top = executor.resolve_display_refs([FaceRef.model_validate(TOP)])[0]
    handle = top.handle  # type: ignore[union-attr]
    with pytest.raises(AdapterError) as info:
        adapter.shell(ShellRequest(operation_id="direct", faces=(handle,), thickness_mm=thickness))
    assert info.value.info.code is ErrorCode.geometry_failed
    assert "kernel could not build this shell" in info.value.info.message
    assert info.value.info.recoverable is False  # the kernel is deterministic: no identical retry
    assert adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(BOX_VOLUME, abs=1e-9)


def test_an_unknown_face_is_an_invalid_reference(adapter: FreeCADAdapter) -> None:
    _run(adapter, BOX)
    with pytest.raises(AdapterError) as info:
        adapter.shell(ShellRequest(operation_id="direct", faces=(FaceHandle(id="face:nope"),), thickness_mm=2.0))
    assert info.value.info.code is ErrorCode.invalid_reference
