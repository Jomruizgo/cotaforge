"""``undo_to`` / ``revise_operation`` past FreeCAD's 20-transaction undo cap, through the real executor and worker.

FreeCAD keeps at most 20 undo transactions and ``doc.undo()`` beyond that does nothing, silently. The worker's
``undo_last`` used to rely on it, so reverting more than 20 ops left the older ops' objects in the FreeCAD document as
ghost geometry (``mass_properties`` sums only registry bodies, so volume checks never noticed) while the registry
forgot them. These tests look at the live document itself (a real ``saveAs`` of it, as ``test_revise_operation.py``
does), never just at the registry's view of it. The exhaustive differential oracle (a reverted document equals a fresh
one for cylinders, booleans, moves, long PartDesign plans, converted bodies) lives in
``tests/freecad_worker/check_undo_depth.py``.
"""

import math
import os
import sys
import time
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    BoxRequest,
    DocumentKind,
    Empty,
    MassRequest,
    NewDocumentRequest,
)
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification.verifier import Verifier
from tests.conftest import plate_plan_dict

pytestmark = pytest.mark.freecad

CYLINDER_VOLUME = math.pi * 5.0**2 * 10.0


def _cylinder_plan(count: int) -> dict[str, Any]:
    return {
        "id": "many_cylinders",
        "kind": "part",
        "description": "Independent cylinders, one body each",
        "operations": [
            {
                "id": f"c{i}",
                "op": "cylinder",
                "intent": "one more independent cylinder",
                "diameter_mm": 10,
                "height_mm": 10,
                "origin": [i * 30.0, 0, 0],
            }
            for i in range(count)
        ],
    }


def _holes_plan(count: int, depth: float = 8.0) -> dict[str, Any]:
    """The plate of the shared plan with ``count`` simple holes (2 rows) instead of the pattern: every hole is one
    feature, so reverting the plate thickness reverts ``count + 1`` ops."""
    plan = plate_plan_dict()
    plan["operations"] = [
        plan["operations"][0],
        {**plan["operations"][1], "depth_mm": depth},
        *(
            {
                "id": f"hole_{i}",
                "op": "hole",
                "intent": "one more hole on the top face",
                "face": {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}},
                "position": [-45 + 8 * (i // 2), -15 if i % 2 else 15],
                "standard": "simple",
                "diameter_mm": 4,
                "end_condition": "through_all",
            }
            for i in range(count)
        ),
    ]
    return plan


def _started(adapter: FreeCADAdapter, plan: dict[str, Any]) -> Executor:
    executor = Executor(parse_plan(plan), adapter, Verifier(), ExecutorConfig())
    executor.start()
    executor.run_to_end()
    assert executor.state is ExecutorState.completed, executor.trace().active_steps()[-1]
    return executor


def _fc_objects(adapter: FreeCADAdapter, path: Path) -> list[tuple[str, str]]:
    """``(type, name)`` of every object of the live document, read from a real ``saveAs`` of it."""
    rpc = adapter._rpc  # noqa: SLF001 - integration test, same seam test_revise_operation.py uses
    assert rpc is not None
    rpc.call("test_save_document", {"operation_id": "save", "path": str(path)}, timeout_s=60.0)
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("Document.xml"))
    return [(o.get("type", ""), o.get("name", "")) for o in root.iter("Object") if o.get("type")]


def _volume(adapter: FreeCADAdapter) -> float:
    return adapter.mass_properties(MassRequest(operation_id="q")).volume_mm3


def _manual(adapter: FreeCADAdapter) -> list[str]:
    return [n.name for n in adapter.feature_tree(Empty(operation_id="t")).nodes if n.parameters.get("manual")]


@pytest.fixture
def hooked(freecad_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[FreeCADAdapter]:
    with FreeCADAdapter(
        freecad_root=freecad_root,
        timeout_s=120.0,
        log_dir=tmp_path_factory.mktemp("undo-depth-logs"),
        enable_test_hooks=True,
    ) as connected:
        yield connected


@pytest.fixture
def fresh(freecad_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[FreeCADAdapter]:
    """A second, independent worker: the oracle that ran only the surviving plan."""
    with FreeCADAdapter(
        freecad_root=freecad_root,
        timeout_s=120.0,
        log_dir=tmp_path_factory.mktemp("undo-depth-fresh-logs"),
        enable_test_hooks=True,
    ) as connected:
        yield connected


def test_undo_to_after_seventy_eight_ops_leaves_exactly_the_surviving_objects(
    hooked: FreeCADAdapter, tmp_path: Path
) -> None:
    """The long-session case: 78 ops, keep 23. Before the fix 35 cylinders (55 - 20) stayed in the document as 'manual'
    nodes of the feature tree while the volume (registry bodies only) looked right."""
    executor = _started(hooked, _cylinder_plan(78))
    assert len([o for o in _fc_objects(hooked, tmp_path / "full.FCStd") if o[0] == "Part::Cylinder"]) == 78

    reverted = executor.undo_to("c22")

    assert len(reverted) == 55
    objects = _fc_objects(hooked, tmp_path / "after.FCStd")
    assert [o for o in objects if o[0] == "Part::Cylinder"] == [
        ("Part::Cylinder", f"Cylinder{i:03d}" if i else "Cylinder") for i in range(23)
    ]
    assert len(objects) == 23, objects  # nothing else at all
    assert _manual(hooked) == []
    assert len(hooked.feature_tree(Empty(operation_id="t")).nodes) == 23
    assert _volume(hooked) == pytest.approx(23 * CYLINDER_VOLUME, abs=1e-3)


def test_undo_all_the_way_then_redo_then_undo_again_never_resurrects_or_leaves_anything(
    hooked: FreeCADAdapter, tmp_path: Path
) -> None:
    executor = _started(hooked, _cylinder_plan(30))
    executor.undo_to("c4")
    assert len(_fc_objects(hooked, tmp_path / "a.FCStd")) == 5

    executor.resume()
    executor.run_to_end()  # redo the reverted tail unchanged
    assert executor.state is ExecutorState.completed
    assert len(_fc_objects(hooked, tmp_path / "b.FCStd")) == 30
    assert _volume(hooked) == pytest.approx(30 * CYLINDER_VOLUME, abs=1e-3)

    executor.undo_to("c9")
    objects = _fc_objects(hooked, tmp_path / "c.FCStd")
    assert len(objects) == 10 and _manual(hooked) == []


def test_revise_operation_on_a_tail_longer_than_the_undo_cap_equals_a_fresh_run(
    hooked: FreeCADAdapter, fresh: FreeCADAdapter, tmp_path: Path
) -> None:
    """``revise_operation`` is ``undo_to(<op before>)`` + ``extend`` + re-run: with the plate thickness revised under 24
    holes it reverts 25 features (> the 20-transaction cap). The result must be the document of a fresh run of the
    revised plan: same objects, same volume, nothing left over."""
    executor = _started(hooked, _holes_plan(24))
    operations = [op.model_dump(mode="python") for op in executor.plan.operations]
    base_index = next(i for i, op in enumerate(operations) if op["id"] == "base")

    reverted = executor.undo_to(operations[base_index - 1]["id"])
    assert len(reverted) == 25
    executor.extend([{**operations[base_index], "depth_mm": 10}, *operations[base_index + 1 :]])
    executor.run_to_end()

    assert executor.state is ExecutorState.completed
    assert [s.op_id for s in executor.trace().active_steps() if s.status is not StepStatus.success] == []
    assert _volume(hooked) == pytest.approx(100 * 60 * 10 - 24 * math.pi * 2.0**2 * 10, abs=1e-2)
    assert _manual(hooked) == []

    _started(fresh, _holes_plan(24, depth=10.0))
    assert _fc_objects(hooked, tmp_path / "revised.FCStd") == _fc_objects(fresh, tmp_path / "oracle.FCStd")
    assert _volume(hooked) == pytest.approx(_volume(fresh), abs=1e-6)


# --- the visible worker (needs a display; the module-level fixtures above do not) -----------------------------------

needs_display = pytest.mark.skipif(
    sys.platform != "win32" and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")),
    reason="the visible worker needs a display (DISPLAY / WAYLAND_DISPLAY)",
)


@needs_display
@pytest.mark.visible
def test_visible_worker_reverts_past_the_undo_cap_without_a_manual_edit_notice(
    freecad_root: Path, tmp_path: Path
) -> None:
    """Same bug in the GUI binary: the revert runs while the harness is busy, so the document watcher reports nothing,
    and the visible document ends with exactly the surviving objects."""
    from cotaforge.contract import ManualEditNotice  # noqa: PLC0415 - only this test listens

    with FreeCADAdapter(
        freecad_root=freecad_root,
        timeout_s=120.0,
        log_dir=tmp_path / "logs",
        visible=True,
        enable_test_hooks=True,
    ) as visible:
        received: list[ManualEditNotice] = []
        visible.set_manual_edit_listener(received.append)
        visible.new_document(NewDocumentRequest(operation_id="d", kind=DocumentKind.part, name="visible-undo"))
        for i in range(30):
            visible.box(BoxRequest(operation_id=f"b{i}", size=(10.0, 10.0, 10.0)))
        for _ in range(25):
            assert visible.undo_last(Empty(operation_id="u")).reverted is not None

        objects = _fc_objects(visible, tmp_path / "visible.FCStd")
        assert [o for o in objects if o[0] == "Part::Box"] == [
            ("Part::Box", f"Box{i:03d}" if i else "Box") for i in range(5)
        ]
        assert len(objects) == 5
        assert _manual(visible) == []
        assert _volume(visible) == pytest.approx(5 * 1000.0, abs=1e-6)
        time.sleep(1.0)  # > the watcher's debounce: a hand-edit notice would have arrived by now
        assert received == []
