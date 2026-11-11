"""Soundness of the verifier's claims around split pieces and kept PartDesign tools on the real kernel: a piece whose
box equals the document box is not alone in it, and a kept tool is part of the document box."""

from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace
from cotaforge.ir import parse_plan
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier
from tests.integration.freecad.test_split import (
    _assert_green,
    _block,
    _final_volume,
    _hole,
    _plate,
    _split,
)

pytestmark = pytest.mark.freecad

BLOCK_VOLUME = 100.0 * 60.0 * 20.0


def _run(adapter: FreeCADAdapter, *operations: dict[str, Any]) -> tuple[Executor, ExecutionTrace]:
    plan = parse_plan({"id": "sound", "kind": "part", "operations": list(operations)})
    executor = Executor(plan, adapter, Verifier(), ExecutorConfig())
    executor.start()
    return executor, executor.run_to_end()


def test_moving_the_big_piece_of_an_oblique_split_is_not_asserted_as_if_it_were_alone(adapter: FreeCADAdapter) -> None:
    """x + y + z = 30 cuts a small corner off: the big piece reaches every extreme of the document box, so its box
    equals the document box, yet the small piece stands inside it."""
    tilted = {
        "id": "tilt",
        "op": "ref_plane",
        "intent": "oblique parting plane",
        "define": {"how": "three_points", "points": [[30, 0, 0], [0, 30, 0], [0, 0, 30]]},
    }
    move = {
        "id": "open",
        "op": "translate",
        "intent": "slide the big piece away",
        "target": {"kind": "body", "from_op": "halves", "side": "positive"},
        "offset": [200, 0, 0],
    }
    _, trace = _run(adapter, _block(), tilted, _split("tilt"), move)
    _assert_green(trace)
    step = trace.step("open")
    assert step is not None
    box = next(c for c in step.last_level2() if c.name == "bbox_after")
    assert box.kind is CheckKind.reported
    assert _final_volume(trace) == pytest.approx(BLOCK_VOLUME, abs=1e-6)


def test_a_kept_featured_tool_is_part_of_the_document_box(adapter: FreeCADAdapter) -> None:
    """The part (0..100) sticks out of the block (50..150) on the low side: the document box is the union of both."""
    block = {"id": "block", "op": "box", "intent": "block", "size": [100, 60, 20], "placement": {"origin": [50, 0, 0]}}
    cavity = {
        "id": "cavity",
        "op": "boolean_subtract",
        "intent": "press the part into the block",
        "target": {"kind": "body", "from_op": "block"},
        "tools": [{"kind": "body", "from_op": "base"}],
        "keep_tools": True,
    }
    _, trace = _run(adapter, *_plate(), _hole(), block, cavity)
    _assert_green(trace)
    step = trace.step("cavity")
    assert step is not None
    box = next(c for c in step.last_level2() if c.name == "bbox_within")
    assert box.kind is CheckKind.asserted and box.ok
    assert trace.final_bbox is not None and tuple(trace.final_bbox.min) == pytest.approx((0.0, 0.0, 0.0), abs=1e-6)


def test_a_kept_tool_beside_a_primitive_target_still_has_the_box_asserted(adapter: FreeCADAdapter) -> None:
    sub = {
        "id": "cope",
        "op": "boolean_subtract",
        "intent": "press A into B",
        "target": {"kind": "body", "from_op": "b"},
        "tools": [{"kind": "body", "from_op": "a"}],
        "keep_tools": True,
    }
    b = {"id": "b", "op": "box", "intent": "block", "size": [100, 60, 20], "placement": {"origin": [50, 0, 0]}}
    _, trace = _run(adapter, _block("a"), b, sub)
    _assert_green(trace)
    step = trace.step("cope")
    assert step is not None
    box = next(c for c in step.last_level2() if c.name == "bbox_within")
    assert box.kind is CheckKind.asserted and box.ok
