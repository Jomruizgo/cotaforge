"""How many reads (measures, face/edge descriptions, canaries) a step asks of the adapter.

Every read is a round trip to the CAD, and on some CADs one answer is many calls: the step asks for what its checks use
and nothing else, and does not measure the part again when the last step measured it and nothing has changed it since.
The numbers in the docstrings are what the same plan cost before (mass, box, face_info, edge_info, canary).
"""

from collections import Counter
from typing import Any

from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification.verifier import Verifier
from tests.unit.executor.test_with_fake import _fake

READS = (
    "mass_properties",
    "bounding_box",
    "face_info",
    "edge_info",
    "faces_info",
    "edges_info",
    "canary",
)


class _Counting:
    """The adapter seen through a counter of the calls made to it."""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.calls: Counter[str] = Counter()

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._inner, name)
        if not callable(attr):
            return attr

        def call(*args: Any, **kwargs: Any) -> Any:
            self.calls[name] += 1
            return attr(*args, **kwargs)

        return call


def _start(plan: dict[str, Any], faults: dict[str, Any] | None = None) -> tuple[Executor, _Counting]:
    adapter = _Counting(_fake(faults))
    executor = Executor(parse_plan(plan), adapter, Verifier(), ExecutorConfig(), {})  # type: ignore[arg-type]
    executor.start()
    return executor, adapter


def _step(executor: Executor, adapter: _Counting) -> tuple[StepStatus, dict[str, int]]:
    before = Counter(adapter.calls)
    record = executor.next_operation()
    delta = adapter.calls - before
    return record.status, {name: delta[name] for name in READS if delta[name]}


def test_reads_of_a_sketch_an_extrude_and_a_hole(plate_plan: dict[str, Any]) -> None:
    executor, adapter = _start(plate_plan)
    # the first step measures the part; the others reuse what the step before left
    # before: sketch 2/2, extrude 2/2 + 6 faces + 12 edges, hole 2/3 + 5 faces + 2 edges
    assert _step(executor, adapter) == (StepStatus.success, {"mass_properties": 2, "bounding_box": 2})
    assert _step(executor, adapter) == (StepStatus.success, {"mass_properties": 1, "bounding_box": 1})
    # the hole is the one op whose checks read the faces it made (one batch) and the entry face
    assert _step(executor, adapter) == (
        StepStatus.success,
        {"mass_properties": 1, "bounding_box": 2, "faces_info": 1, "face_info": 1},
    )
    # before: 2/3, 5 faces and 2 edges
    assert _step(executor, adapter) == (StepStatus.success, {"mass_properties": 1, "bounding_box": 1})
    assert executor.state is ExecutorState.completed


def test_a_step_the_plan_cannot_resolve_costs_no_read_at_all(plate_plan: dict[str, Any]) -> None:
    plate_plan["operations"][2]["face"]["select"] = {"surface": "plane"}  # ambiguous: every plane matches
    executor, adapter = _start(plate_plan)
    _step(executor, adapter)
    _step(executor, adapter)
    # before: one mass and one box measured just to be thrown away
    assert _step(executor, adapter) == (StepStatus.plan_error, {})


def test_a_request_the_adapter_refuses_costs_no_measure_and_no_canary(plate_plan: dict[str, Any]) -> None:
    faults = {
        "faults": [
            {
                "operation_id": "hole_1",
                "attempt": 1,
                "kind": "raise_error",
                "error_code": "invalid_request",
                "recoverable": False,
            }
        ]
    }
    executor, adapter = _start(plate_plan, faults)
    _step(executor, adapter)
    _step(executor, adapter)
    # before: mass 1, box 2, face 1, canary 1
    status, reads = _step(executor, adapter)
    assert status is StepStatus.failed
    assert "canary" not in reads and "mass_properties" not in reads
    assert [h.trigger for h in executor.trace().health if h.trigger == "after_failure"] == []


def test_the_part_is_measured_again_after_anything_that_may_have_changed_it(plate_plan: dict[str, Any]) -> None:
    executor, adapter = _start(plate_plan)
    _step(executor, adapter)
    executor.invalidate_measure()  # a hand edit in the CAD
    status, reads = _step(executor, adapter)
    assert status is StepStatus.success and reads == {"mass_properties": 2, "bounding_box": 2}
    executor.pause_now()
    executor.undo_to("base_sketch")
    executor.resume()
    status, reads = _step(executor, adapter)
    assert status is StepStatus.success and reads["mass_properties"] == 2


def test_a_failed_attempt_that_reached_the_cad_drops_the_reused_measure(plate_plan: dict[str, Any]) -> None:
    faults = {"faults": [{"operation_id": "hole_1", "attempt": 1, "kind": "rebuild_failed"}]}
    executor, adapter = _start(plate_plan, faults)
    _step(executor, adapter)
    _step(executor, adapter)
    status, reads = _step(executor, adapter)  # the first attempt fails, is undone, the second succeeds
    assert status is StepStatus.success
    assert reads.get("canary") == 1  # a failure that reached the CAD still asks whether it is alive
    # the step ended on a verdict that measured the part: the next one reuses it
    assert _step(executor, adapter) == (StepStatus.success, {"mass_properties": 1, "bounding_box": 1})
