"""Arcs and slots in a sketch operation: IR validation, plan rules (V6) and a verified run on the fake adapter."""

import math
from typing import Any

import pytest

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutorState
from cotaforge.ir import PlanError, parse_plan
from cotaforge.verification.verifier import Verifier

XY = {"kind": "plane", "name": "XY"}
PLATE = {"id": "outline", "op": "sketch", "intent": "plate", "on": XY, "entities": [
    {"kind": "rect", "name": "r", "center": [50, 30], "width": 100, "height": 60}]}


def plan(*operations: dict[str, Any]) -> dict[str, Any]:
    return {"id": "arcs", "kind": "part", "operations": list(operations)}


def base() -> list[dict[str, Any]]:
    return [PLATE, {"id": "plate", "op": "extrude", "intent": "plate", "sketch": "outline", "depth_mm": 8}]


def run(data: dict[str, Any]) -> Executor:
    executor = Executor(parse_plan(data), FakeAdapter(), Verifier())
    executor.start()
    executor.run_to_end()
    return executor


def test_a_slot_cut_runs_and_every_level_two_check_agrees_with_the_fake() -> None:
    slot = {"id": "slot_sk", "op": "sketch", "intent": "slot", "on": XY, "entities": [
        {"kind": "slot", "name": "s", "start": [30, 30], "end": [70, 30], "width": 10}]}
    cut = {"id": "slot", "op": "cut", "intent": "slot", "sketch": "slot_sk", "end_condition": "through_all",
           "direction": "positive"}
    executor = run(plan(*base(), slot, cut))
    assert executor.state is ExecutorState.completed, executor.trace().active_steps()[-1]
    assert executor.trace().final_mass is not None
    assert executor.trace().final_mass.volume_mm3 == pytest.approx(48000 - (400 + math.pi * 25) * 8, rel=1e-4)


def test_a_boss_with_rounded_ends_made_of_two_lines_and_two_arcs() -> None:
    """A slot spelled out entity by entity: 40 long between the end centres, 10 wide."""
    entities = [
        {"kind": "line", "name": "top", "start": [10, 5], "end": [50, 5]},
        {"kind": "arc3", "name": "right", "start": [50, 5], "through": [55, 0], "end": [50, -5]},
        {"kind": "line", "name": "bottom", "start": [50, -5], "end": [10, -5]},
        {"kind": "arc_ends", "name": "left", "center": [10, 0], "start": [10, -5], "end": [10, 5], "ccw": False},
    ]
    boss = {"id": "boss_sk", "op": "sketch", "intent": "boss", "on": XY, "entities": entities}
    extrude = {"id": "boss", "op": "extrude", "intent": "boss", "sketch": "boss_sk", "depth_mm": 6}
    executor = run(plan(boss, extrude))
    assert executor.state is ExecutorState.completed, executor.trace().active_steps()[-1]
    assert executor.trace().final_mass.volume_mm3 == pytest.approx((400 + math.pi * 25) * 6, rel=1e-4)  # type: ignore[union-attr]
    step = executor.trace().step("boss")
    assert step is not None
    faces = {c.name: c for c in step.last_level2()}["new_faces_count"]
    assert faces.kind.value == "reported" and faces.expected == 6  # 2 caps + 2 lines + 2 arcs; the fake facets the arcs


def test_a_degenerate_arc_is_refused_when_the_plan_is_read() -> None:
    bad = {"id": "bad", "op": "sketch", "intent": "x", "on": XY, "entities": [
        {"kind": "arc3", "name": "a", "start": [0, 0], "through": [5, 0], "end": [10, 0]}]}
    with pytest.raises(ValueError, match="collinear"):
        parse_plan(plan(bad))


def test_an_open_chain_with_an_arc_is_a_plan_error_naming_the_break() -> None:
    entities = [
        {"kind": "line", "name": "l", "start": [0, 0], "end": [10, 0]},
        {"kind": "arc3", "name": "a", "start": [10, 0], "through": [12, 3], "end": [10, 6]},
    ]
    sketch = {"id": "open", "op": "sketch", "intent": "x", "on": XY, "entities": entities}
    extrude = {"id": "e", "op": "extrude", "intent": "x", "sketch": "open", "depth_mm": 1}
    with pytest.raises(PlanError, match="open chain"):
        parse_plan(plan(sketch, extrude))
