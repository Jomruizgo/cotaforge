"""Soundness of the expectations around multibody ops, on the real FakeAdapter: a moved body whose box merely EQUALS
the document box is not alone in it, a kept boolean tool is part of the document box, and the verifier's tracking state
follows the executor (a failed op leaves no trace of itself; ``undo_to`` forgets what it undid)."""

from typing import Any

import pytest

from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState
from cotaforge.ir import parse_plan
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier
from tests.unit.executor.test_with_fake import _asserted_ok, _fake, _run

XY = {"kind": "plane", "name": "XY"}


def _box(op_id: str, size: list[float], origin: list[float] | None = None) -> dict[str, Any]:
    op: dict[str, Any] = {"id": op_id, "op": "box", "intent": "block", "size": size}
    if origin is not None:
        op["placement"] = {"origin": origin}
    return op


def _plan(*ops: dict[str, Any]) -> dict[str, Any]:
    return {"id": "multi", "kind": "part", "operations": list(ops)}


def _why(trace: ExecutionTrace) -> str:
    return str([(s.op_id, s.status, s.plan_error, [a.error for a in s.attempts if a.error]) for s in trace.steps])


def _datum(op_id: str, z: float) -> dict[str, Any]:
    return {
        "id": op_id,
        "op": "ref_plane",
        "intent": "parting plane",
        "define": {"how": "offset", "base": XY, "distance_mm": z},
    }


def _split(plane: str, keep: str | None = None, source: dict[str, Any] | None = None, op_id: str = "halves") -> dict:
    op: dict[str, Any] = {
        "id": op_id,
        "op": "split",
        "intent": "cut",
        "source": source or {"kind": "body", "from_op": "a"},
        "plane": {"kind": "plane", "from_op": plane},
    }
    if keep is not None:
        op["keep"] = keep
    return op


def test_a_moved_body_whose_box_equals_the_document_box_is_not_alone_in_it() -> None:
    """B stands inside A's box: the document box IS A's box, yet moving A must not be asserted as if it were alone."""
    move = {
        "id": "mv",
        "op": "translate",
        "intent": "away",
        "target": {"kind": "body", "from_op": "a"},
        "offset": [200, 0, 0],
    }
    traces, _ = _run([_plan(_box("a", [100, 60, 20]), _box("b", [10, 10, 10], [20, 20, 5]), move)], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, _why(trace)
    step = trace.step("mv")
    assert step is not None
    box = next(c for c in step.last_level2() if c.name == "bbox_after")
    assert box.kind is CheckKind.reported
    assert "several bodies" in next(e.note for e in step.expectations if e.name == "bbox_after")


def test_a_lone_moved_body_still_has_its_box_asserted() -> None:
    move = {
        "id": "mv",
        "op": "translate",
        "intent": "away",
        "target": {"kind": "body", "from_op": "a"},
        "offset": [200, 0, 0],
    }
    traces, _ = _run([_plan(_box("a", [100, 60, 20]), move)], _fake())
    assert "bbox_after" in _asserted_ok(traces[0], "mv")


def test_a_moved_piece_of_a_split_is_not_asserted_against_the_document_box() -> None:
    plan = _plan(
        _box("a", [100, 60, 20]),
        _datum("cut_at", 5),
        _split("cut_at"),
        {
            "id": "mv",
            "op": "translate",
            "intent": "open",
            "target": {"kind": "body", "from_op": "halves", "side": "negative"},
            "offset": [200, 0, 0],
        },
    )
    traces, _ = _run([plan], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, _why(trace)
    assert "bbox_after" not in _asserted_ok(trace, "mv")


def test_a_kept_boolean_tool_is_part_of_the_document_box() -> None:
    """A at the origin is subtracted from B at x = 50 and survives: the document box is the union of the two."""
    cope = {
        "id": "cope",
        "op": "boolean_subtract",
        "intent": "press A into B",
        "target": {"kind": "body", "from_op": "b"},
        "tools": [{"kind": "body", "from_op": "a"}],
        "keep_tools": True,
    }
    traces, _ = _run([_plan(_box("a", [100, 60, 20]), _box("b", [100, 60, 20], [50, 0, 0]), cope)], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, _why(trace)
    assert "bbox_within" in _asserted_ok(trace, "cope")


def test_a_subtract_beside_an_unrelated_body_does_not_assert_the_target_box_alone() -> None:
    far = _box("far", [10, 10, 10], [500, 500, 500])
    cut = {
        "id": "cut",
        "op": "boolean_subtract",
        "intent": "bore",
        "target": {"kind": "body", "from_op": "a"},
        "tools": [{"kind": "body", "from_op": "tool"}],
    }
    plan = _plan(_box("a", [100, 60, 20]), far, _box("tool", [10, 10, 30], [20, 20, -5]), cut)
    traces, _ = _run([plan], _fake())
    trace = traces[0]
    assert trace.final_state == ExecutorState.completed, _why(trace)


# --- the verifier follows the executor -----------------------------------------------------------------------------


def _executor(*ops: dict[str, Any]) -> Executor:
    executor = Executor(parse_plan(_plan(*ops)), _fake(), Verifier(), ExecutorConfig(), {})
    executor.start()
    return executor


def test_a_failed_split_leaves_no_trace_in_the_tracking_state() -> None:
    executor = _executor(
        _box("a", [100, 60, 20]), _box("b", [10, 10, 10], [200, 0, 0]), _datum("far", 50), _split("far")
    )
    trace = executor.run_to_end()
    assert trace.final_state == ExecutorState.failed
    computer = executor.verifier.expected_computer
    assert computer._consumed == set()
    assert computer._split_pieces == {}
    assert "halves" not in computer._body_of_op and "halves#negative" not in computer._body_of_op
    assert computer._live_body_count() == 2
    assert computer._shape_of("a") is not None  # the box A is still tracked exactly


def test_undo_to_forgets_what_the_undone_ops_registered() -> None:
    executor = _executor(_box("a", [100, 60, 20]), _datum("cut_at", 5), _split("cut_at"))
    assert executor.run_to_end().final_state == ExecutorState.completed
    computer = executor.verifier.expected_computer
    assert "a" in computer._consumed and computer._split_pieces
    executor.undo_to("cut_at")
    assert computer._consumed == set() and computer._split_pieces == {}
    assert "halves" not in computer._body_of_op
    assert computer._shape_of("a") is not None
    # a different split of the same box runs green from the restored state
    executor.extend([_split("cut_at", keep="positive", op_id="upper")])
    trace = executor.run_to_end()
    assert trace.final_state == ExecutorState.completed, _why(trace)
    assert "volume_delta" in _asserted_ok(trace, "upper")
    assert "halves#negative" not in computer._body_of_op


def test_undo_to_the_last_op_keeps_the_state_of_everything_before_it() -> None:
    executor = _executor(_box("a", [100, 60, 20]), _datum("cut_at", 5), _split("cut_at"))
    executor.run_to_end()
    computer = executor.verifier.expected_computer
    executor.undo_to("halves")  # nothing is undone
    assert "a" in computer._consumed and "halves#negative" in computer._body_of_op


# --- an op that blows up, a stale seed, bodies the computer cannot see ----------------------------------------------


def test_an_exception_escaping_the_step_takes_back_what_the_expectations_recorded() -> None:
    """The adapter raises something that is not an AdapterError: the exception reaches the caller, and the verifier must
    not keep the consumed tool of an op that never completed."""
    adapter = _fake()
    cut = {
        "id": "s",
        "op": "boolean_subtract",
        "intent": "bore",
        "target": {"kind": "body", "from_op": "a"},
        "tools": [{"kind": "body", "from_op": "b"}],
    }
    executor = Executor(
        parse_plan(_plan(_box("a", [100, 60, 20]), _box("b", [10, 10, 30], [20, 20, -5]), cut)),
        adapter,
        Verifier(),
        ExecutorConfig(),
        {},
    )
    executor.start()
    executor.next_operation()
    executor.next_operation()

    def explode(request: Any) -> Any:
        raise RuntimeError("kernel crashed")

    real = adapter.boolean
    adapter.boolean = explode  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="kernel crashed"):
        executor.next_operation()
    computer = executor.verifier.expected_computer
    assert computer._consumed == set() and "s" not in computer._body_of_op
    assert computer._live_body_count() == 2
    adapter.boolean = real  # type: ignore[method-assign]
    record = executor.next_operation()  # the retry computes its expectations on the state before the op
    assert record.status.value == "success", _why(executor.trace())
    assert computer._consumed == {"b"}


def test_a_pattern_reads_the_delta_of_the_seed_it_follows_not_of_one_that_was_undone() -> None:
    plate = [
        {
            "id": "plate_sketch",
            "op": "sketch",
            "intent": "plate outline",
            "on": XY,
            "entities": [{"kind": "rect", "name": "outline", "center": [50, 30], "width": 100, "height": 60}],
        },
        {"id": "plate", "op": "extrude", "intent": "plate", "sketch": "plate_sketch", "depth_mm": 8},
    ]

    def hole(diameter: float) -> dict[str, Any]:
        return {
            "id": "hole_1",
            "op": "hole",
            "intent": "bore",
            "face": {"kind": "face", "from_op": "plate", "select": {"normal": [0, 0, 1]}},
            "position": [-30, -10],
            "standard": "simple",
            "diameter_mm": diameter,
            "end_condition": "through_all",
        }

    row = {
        "id": "row",
        "op": "linear_pattern",
        "intent": "second bore",
        "features": ["hole_1"],
        "direction": "X",
        "spacing_mm": 30,
        "count": 2,
    }
    executor = _executor(*plate, hole(6.0), row)
    assert executor.run_to_end().final_state == ExecutorState.completed
    executor.undo_to("plate")
    executor.extend([hole(10.0), row])
    trace = executor.run_to_end()
    assert trace.final_state == ExecutorState.completed, _why(trace)
    assert "volume_delta" in _asserted_ok(trace, "row")  # asserted against the d10 seed, not the undone d6 one


def test_a_document_with_adopted_hand_made_features_is_not_assumed_to_hold_only_the_plans_bodies() -> None:
    from cotaforge.contract import FeatureHandle

    executor = _executor(_box("a", [100, 60, 20]), _box("b", [10, 10, 30], [20, 20, -5]))
    assert executor.run_to_end().final_state == ExecutorState.completed
    cut = {
        "id": "s",
        "op": "boolean_subtract",
        "intent": "bore",
        "target": {"kind": "body", "from_op": "a"},
        "tools": [{"kind": "body", "from_op": "b"}],
    }
    executor.extend([cut], external_features={"hand": FeatureHandle(id="feature:1")})
    trace = executor.run_to_end()
    assert trace.final_state == ExecutorState.completed, _why(trace)
    step = trace.step("s")
    assert step is not None
    limit = next(e for e in step.expectations if e.name == "bbox_within")
    assert "other bodies" in limit.note  # the box of the whole document bounds it, not the target's alone
