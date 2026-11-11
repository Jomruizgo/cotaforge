"""Arcs and slots through the executor on the real worker: every Level 2 check of the verifier (expected volume, faces,
box) agrees with FreeCAD, and the profile of the fake adapter (an arc sampled every degree) agrees with it too."""

import math
from typing import Any

import pytest

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import Adapter, MassRequest
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutorState
from cotaforge.ir import parse_plan
from cotaforge.verification.verifier import Verifier

pytestmark = pytest.mark.freecad

XY = {"kind": "plane", "name": "XY"}
PLATE = [
    {"id": "outline", "op": "sketch", "intent": "plate", "on": XY, "entities": [
        {"kind": "rect", "name": "r", "center": [50, 30], "width": 100, "height": 60}]},
    {"id": "plate", "op": "extrude", "intent": "plate", "sketch": "outline", "depth_mm": 8},
]
SLOT_AREA = 40 * 10 + math.pi * 25
KEYWAY = [
    {"kind": "line", "name": "top", "start": [10, 5], "end": [50, 5]},
    {"kind": "arc3", "name": "right", "start": [50, 5], "through": [55, 0], "end": [50, -5]},
    {"kind": "line", "name": "bottom", "start": [50, -5], "end": [10, -5]},
    {"kind": "arc_ends", "name": "left", "center": [10, 0], "start": [10, -5], "end": [10, 5], "ccw": False},
]


def plan(*operations: dict[str, Any]) -> Any:
    return parse_plan({"id": "arcs", "kind": "part", "operations": list(operations)})


def run(adapter: Adapter, parsed: Any) -> Executor:
    executor = Executor(parsed, adapter, Verifier(), ExecutorConfig())
    executor.start()
    executor.run_to_end()
    assert executor.state is ExecutorState.completed, executor.trace().active_steps()[-1]
    return executor


def volume(adapter: Adapter) -> float:
    return adapter.mass_properties(MassRequest(operation_id="v")).volume_mm3


def cut_slot() -> Any:
    sketch = {"id": "slot_sk", "op": "sketch", "intent": "slot", "on": XY, "entities": [
        {"kind": "slot", "name": "s", "start": [30, 30], "end": [70, 30], "width": 10}]}
    cut = {"id": "slot", "op": "cut", "intent": "slot", "sketch": "slot_sk", "end_condition": "through_all",
           "direction": "positive"}
    return plan(*PLATE, sketch, cut)


def test_a_slot_cut_on_freecad_matches_the_analytic_volume_and_the_fake(adapter: FreeCADAdapter) -> None:
    run(adapter, cut_slot())
    assert volume(adapter) == pytest.approx(48000 - SLOT_AREA * 8, rel=1e-6)
    fake = FakeAdapter()
    run(fake, cut_slot())
    assert volume(fake) == pytest.approx(volume(adapter), rel=1e-4)


def test_a_boss_of_two_lines_and_two_arcs_verifies_on_freecad(adapter: FreeCADAdapter) -> None:
    boss = {"id": "boss_sk", "op": "sketch", "intent": "boss", "on": XY, "entities": KEYWAY}
    extrude = {"id": "boss", "op": "extrude", "intent": "boss", "sketch": "boss_sk", "depth_mm": 6}
    run(adapter, plan(boss, extrude))
    assert volume(adapter) == pytest.approx(SLOT_AREA * 6, rel=1e-6)


def test_a_turned_profile_with_an_arc_revolves_on_freecad(adapter: FreeCADAdapter) -> None:
    """A bar with a domed shoulder: 10 wide at the ends, bulging to r = 14 half way up (turned about the axis line)."""
    entities = [
        {"kind": "line", "name": "axis", "start": [0, 0], "end": [0, 20]},
        {"kind": "line", "name": "foot", "start": [0, 0], "end": [10, 0]},
        {"kind": "arc3", "name": "shoulder", "start": [10, 0], "through": [14, 10], "end": [10, 20]},
        {"kind": "line", "name": "head", "start": [10, 20], "end": [0, 20]},
    ]
    sketch = {"id": "prof", "op": "sketch", "intent": "profile", "on": XY, "entities": entities}
    revolve = {"id": "turned", "op": "revolve", "intent": "turned", "sketch": "prof", "axis": "axis"}
    run(adapter, plan(sketch, revolve))
    assert volume(adapter) > math.pi * 10**2 * 20  # the dome adds material outside the cylinder of r = 10
