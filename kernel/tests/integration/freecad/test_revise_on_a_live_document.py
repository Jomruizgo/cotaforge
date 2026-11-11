"""Revising an operation of a live plan against the real FreeCAD worker, driven through the executor.

A revision is ``Executor.undo_to(<op before the revised one>)`` + ``Executor.extend([revised, *later])`` + a re-run of
the tail. What only the real CAD can answer is whether the document really comes back to the state before the revised
op (``undo_last`` per feature, sketches being registry entries that only become CAD objects when a feature consumes
them) and whether the re-run passes the verifier -- the pattern reads its seed's NEW volume delta -- on real geometry.
Each test drives that sequence on one live document.
"""

import copy
import math
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import Empty, MassRequest
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification.verifier import Verifier
from tests.conftest import plate_plan_dict

pytestmark = pytest.mark.freecad

HOLE = math.pi * 3.3**2


def _volume(adapter: FreeCADAdapter) -> float:
    return adapter.mass_properties(MassRequest(operation_id="q")).volume_mm3


def _failures(trace: ExecutionTrace) -> list[tuple[Any, ...]]:
    return [
        (
            s.op_id,
            s.status.value,
            s.plan_error,
            [(c.name, c.expected, c.observed) for c in s.last_level2() if c.ok is False],
        )
        for s in trace.active_steps()
        if s.status is not StepStatus.success
        or any(c.ok is False for c in s.last_level2() if c.kind.value == "asserted")
    ]


def _revise(executor: Executor, op_id: str, changes: dict[str, Any]) -> tuple[str, ...]:
    """Undo up to the op before ``op_id``, replace it and everything after it, and run the tail again."""
    operations = [op.model_dump(mode="python") for op in executor.plan.operations]
    index = next(i for i, op in enumerate(operations) if op["id"] == op_id)
    reverted = executor.undo_to(operations[index - 1]["id"])
    executor.extend([{**operations[index], **changes}, *operations[index + 1 :]])
    executor.run_to_end()
    return reverted


def _started(adapter: FreeCADAdapter, plan: dict[str, Any]) -> Executor:
    executor = Executor(parse_plan(plan), adapter, Verifier(), ExecutorConfig())
    executor.start()
    executor.run_to_end()
    assert executor.state is ExecutorState.completed
    return executor


def test_revising_the_plate_thickness_rebuilds_the_holes_and_the_pattern_on_real_geometry(
    adapter: FreeCADAdapter,
) -> None:
    executor = _started(adapter, plate_plan_dict())
    assert _volume(adapter) == pytest.approx(100 * 60 * 8 - 4 * HOLE * 8, abs=1e-2)

    assert _revise(executor, "base", {"depth_mm": 10}) == ("holes", "hole_1", "base")

    trace = executor.trace()
    assert executor.state is ExecutorState.completed and _failures(trace) == []
    assert [s.op_id for s in trace.active_steps()] == ["base_sketch", "base", "hole_1", "holes"]
    assert _volume(adapter) == pytest.approx(100 * 60 * 10 - 4 * HOLE * 10, abs=1e-2)


def test_revising_the_pattern_seed_makes_the_pattern_verify_against_the_new_hole(adapter: FreeCADAdapter) -> None:
    executor = _started(adapter, plate_plan_dict())

    assert _revise(executor, "hole_1", {"diameter_mm": 8.0}) == ("holes", "hole_1")

    assert executor.state is ExecutorState.completed and _failures(executor.trace()) == []
    assert _volume(adapter) == pytest.approx(100 * 60 * 8 - 4 * math.pi * 4.0**2 * 8, abs=1e-2)


@pytest.fixture
def hooked(freecad_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[FreeCADAdapter]:
    """A headless worker with the test hooks on (``test_save_document``): the only way to look at the objects of the
    live FreeCAD document itself instead of the registry's view of it."""
    with FreeCADAdapter(
        freecad_root=freecad_root,
        timeout_s=60.0,
        log_dir=tmp_path_factory.mktemp("revise-logs"),
        enable_test_hooks=True,
    ) as connected:
        yield connected


def _fc_objects(adapter: FreeCADAdapter, path: Path) -> list[tuple[str, str]]:
    """``(type, name)`` of every object of the live document, read from a real ``saveAs`` of it."""
    rpc = adapter._rpc  # noqa: SLF001 - integration test, same seam test_show_entities_visible.py uses
    assert rpc is not None
    rpc.call("test_save_document", {"operation_id": "save", "path": str(path)}, timeout_s=30.0)
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("Document.xml"))
    return [(o.get("type", ""), o.get("name", "")) for o in root.iter("Object") if o.get("type")]


def test_revising_a_sketch_leaves_no_orphan_in_the_document(hooked: FreeCADAdapter, tmp_path: Path) -> None:
    """``undo_to`` calls ``undo_last`` for features only. A reverted sketch must still leave no Sketcher object behind
    in the FreeCAD document: it is created inside the transaction of the feature that consumes it."""
    adapter = hooked
    plan = plate_plan_dict()
    plan["operations"] = [
        *plan["operations"][:2],
        {
            "id": "boss_sketch",
            "op": "sketch",
            "intent": "Boss profile on the top face",
            "on": {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}},
            "entities": [{"kind": "rect", "name": "boss", "center": [0, 0], "width": 20, "height": 20}],
        },
        {"id": "boss", "op": "extrude", "intent": "Boss height", "sketch": "boss_sketch", "depth_mm": 5},
    ]
    executor = _started(adapter, copy.deepcopy(plan))
    assert _volume(adapter) == pytest.approx(48000 + 20 * 20 * 5, abs=1e-2)
    before = [o for o in _fc_objects(adapter, tmp_path / "before.FCStd") if o[0] == "Sketcher::SketchObject"]
    assert len(before) == 2  # the plate's and the boss's

    entities = [{"kind": "rect", "name": "boss", "center": [0, 0], "width": 30, "height": 30}]
    assert _revise(executor, "boss_sketch", {"entities": entities}) == ("boss", "boss_sketch")

    assert executor.state is ExecutorState.completed and _failures(executor.trace()) == []
    assert _volume(adapter) == pytest.approx(48000 + 30 * 30 * 5, abs=1e-2)
    objects = _fc_objects(adapter, tmp_path / "after.FCStd")
    assert len([o for o in objects if o[0] == "Sketcher::SketchObject"]) == 2, objects  # no orphan sketch
    kinds = [node.kind for node in adapter.feature_tree(Empty(operation_id="t")).nodes]
    assert len(kinds) == 2  # the plate and the boss


def test_a_revision_that_breaks_a_later_op_stops_there_and_the_document_stays_usable(adapter: FreeCADAdapter) -> None:
    executor = _started(adapter, plate_plan_dict())
    # the seed hole moves far outside the 100 x 60 top face: valid IR, impossible geometry
    _revise(executor, "hole_1", {"position": [-400, -20]})
    assert executor.state is ExecutorState.failed
    failed = executor.trace().active_steps()[-1]
    assert failed.op_id == "hole_1" and failed.status is not StepStatus.success
    assert _volume(adapter) == pytest.approx(100 * 60 * 8, abs=1e-2)  # nothing half-applied

    # revising the very op that failed brings the part back
    assert _revise(executor, "hole_1", {"position": [-40, -20]}) == ("hole_1",)
    assert executor.state is ExecutorState.completed and _failures(executor.trace()) == []
    assert _volume(adapter) == pytest.approx(100 * 60 * 8 - 4 * HOLE * 8, abs=1e-2)
