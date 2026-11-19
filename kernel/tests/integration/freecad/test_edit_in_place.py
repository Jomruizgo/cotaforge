"""In-place editing against the real FreeCAD worker, through the executor, on a 14-operation multi-body plan
(``tube_frame.py``: hollow tube, aimed arm trimmed by the tube, drilled lug, unions).

The oracle is a FRESH build of the modified plan in a second document of the same worker: every case compares the
edited document with it (volume, area, box, Placement, face/edge/solid counts and the type, size and centre of every
face and edge in element order, to 1e-9) and then edits the values back and checks the document returned bit for bit to
the baseline. The checks run on every supported FreeCAD line (``COTAFORGE_FREECAD_ROOT``); the failure classes,
atomicity and undo journals are covered in-process by ``tests/freecad_worker/check_edit_in_place.py``.
"""

import copy
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    DocumentHandle,
    DocumentKind,
    MassRequest,
    NewDocumentRequest,
    SetActiveDocumentRequest,
)
from cotaforge.executor.build import DISPATCH, build_request
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.context import ExecutionContext
from cotaforge.executor.executor import Executor
from cotaforge.executor.resolver import Resolver
from cotaforge.executor.trace import ExecutorState
from cotaforge.ir import Plan, parse_plan
from cotaforge.verification.thresholds import DEFAULT_THRESHOLDS
from cotaforge.verification.verifier import Verifier
from tests.conftest import plate_plan_dict
from tests.integration.freecad.tube_frame import tube_frame_plan

pytestmark = pytest.mark.freecad

BASE = tube_frame_plan()

CASES: dict[str, dict[str, dict[str, Any]]] = {
    "tube_radius": {"tube_o": {"diameter_mm": 30.0}},
    "tube_height": {"tube_o": {"height_mm": 140.0}, "tube_i": {"height_mm": 140.0}},
    "box_size": {"lug_plate": {"size": [24.0, 12.0, 40.0]}},
    "cut_tool_origin": {"arm_cutter": {"origin": [120.0, -37.0, 100.0]}},
    "rotate_angle_on_a_cut": {"arm_aimed": {"angle_deg": 170.0}},
    "early_op_feeding_a_union": {"arm_o": {"diameter_mm": 21.0}},
    "five_at_once": {
        "tube_o": {"diameter_mm": 30.0},
        "lug_plate": {"size": [24.0, 12.0, 40.0]},
        "arm_aimed": {"angle_deg": 170.0},
        "arm_o": {"height_mm": 110.0},
        "lug_hole": {"origin": [0.0, 15.0, 148.0]},
    },
}


def modified(changes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    plan = copy.deepcopy(BASE)
    for op in plan["operations"]:
        op.update(copy.deepcopy(changes.get(op["id"], {})))
    return plan


def signature(adapter: FreeCADAdapter, document: DocumentHandle) -> dict[str, Any]:
    adapter.set_active_document(SetActiveDocumentRequest(operation_id="sig", document=document))
    rpc = adapter._rpc  # noqa: SLF001 - the test hook is not part of the public contract
    assert rpc is not None
    return rpc.call("test_shape_signature", {"operation_id": "sig"}, timeout_s=120.0)["bodies"]


def fast_build(adapter: FreeCADAdapter, plan: Plan) -> DocumentHandle:
    """The geometry of ``plan`` in a new document with no per-op verification (the fresh-rebuild oracle)."""
    info = adapter.new_document(NewDocumentRequest(operation_id="fresh", kind=DocumentKind.part, name=plan.id))
    ctx = ExecutionContext(document=info.handle)
    resolver = Resolver(adapter, ctx, DEFAULT_THRESHOLDS)
    for op in plan.operations:
        resolver.begin_step()
        request = build_request(op, ctx, resolver)
        result = getattr(adapter, DISPATCH[op.op][0])(request)
        assert result.rebuild_ok, op.id
        if not hasattr(result, "feature"):  # a sketch
            ctx.sketch_by_op[op.id] = result.sketch
            continue
        ctx.feature_by_op[op.id] = result.feature
        if result.created_bodies:
            ctx.body_by_op[op.id] = result.created_bodies[0]
        elif op.op.startswith("boolean_"):
            ctx.body_by_op[op.id] = request.target
        elif op.op in ("translate", "rotate"):
            ctx.body_by_op[op.id] = request.body
    return info.handle


def assert_same(a: dict[str, Any], b: dict[str, Any], what: str) -> None:
    assert sorted(a) == sorted(b), f"{what}: bodies {sorted(a)} vs {sorted(b)}"
    for key in a:
        x, y = a[key], b[key]
        assert x["volume"] == pytest.approx(y["volume"], rel=1e-9), f"{what}: {key} volume"
        assert x["area"] == pytest.approx(y["area"], rel=1e-9), f"{what}: {key} area"
        assert x["bbox"] == pytest.approx(y["bbox"], abs=1e-9), f"{what}: {key} bbox"
        assert x["placement"] == pytest.approx(y["placement"], abs=1e-9), f"{what}: {key} placement"
        assert x["counts"] == y["counts"], f"{what}: {key} face/edge/solid counts"
        assert x["faces"] == y["faces"], f"{what}: {key} faces, per index"
        assert x["edges"] == y["edges"], f"{what}: {key} edges, per index"


@dataclass
class Built:
    adapter: FreeCADAdapter
    executor: Executor
    document: DocumentHandle
    baseline: dict[str, Any]


@pytest.fixture(scope="module")
def built(freecad_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[Built]:
    with FreeCADAdapter(
        freecad_root=freecad_root,
        timeout_s=600.0,
        log_dir=tmp_path_factory.mktemp("edit-logs"),
        enable_test_hooks=True,
    ) as adapter:
        executor = Executor(parse_plan(BASE), adapter, Verifier(), ExecutorConfig())
        executor.start()
        executor.run_to_end()
        assert executor.state is ExecutorState.completed, executor.trace().active_steps()[-1]
        document = executor.document()
        assert document is not None
        yield Built(adapter, executor, document, signature(adapter, document))


@pytest.mark.parametrize("name", sorted(CASES))
def test_an_in_place_edit_equals_a_fresh_rebuild_and_reverts_bit_for_bit(built: Built, name: str) -> None:
    changes = CASES[name]
    built.adapter.set_active_document(SetActiveDocumentRequest(operation_id="s", document=built.document))
    record = built.executor.edit_in_place(parse_plan(modified(changes)), list(changes), by="test", reason=name)
    assert record.status == "applied", (record.failure, record.result.flags if record.result else None)
    assert record.result is not None and not record.result.health and record.result.failure is None
    assert [f for f in record.result.flags if f.severity == "failure"] == []
    edited = signature(built.adapter, built.document)

    fresh = signature(built.adapter, fast_build(built.adapter, parse_plan(modified(changes))))
    assert_same(edited, fresh, f"{name}: in place vs fresh")

    built.adapter.set_active_document(SetActiveDocumentRequest(operation_id="s", document=built.document))
    back = built.executor.edit_in_place(parse_plan(BASE), list(changes), by="test", reason=f"{name} back")
    assert back.status == "applied", back
    assert_same(signature(built.adapter, built.document), built.baseline, f"{name}: round trip")
    # the recorded steps are the original ones, untouched: nothing was re-run
    assert built.executor.trace().reverts == ()


def test_an_empty_cut_is_rolled_back_on_the_real_part(built: Built) -> None:
    changes = {"tube_i": {"diameter_mm": 40.0}}  # the bore is wider than the tube: nothing would be left
    built.adapter.set_active_document(SetActiveDocumentRequest(operation_id="s", document=built.document))
    record = built.executor.edit_in_place(parse_plan(modified(changes)), ["tube_i"])
    assert record.status == "rolled_back" and record.failure and "no material" in record.failure
    assert record.failure_kind == "semantic"  # the part would break: a rebuild must not hide that
    assert built.executor.plan.operations[1].diameter_mm == 25.4  # type: ignore[union-attr]
    assert_same(signature(built.adapter, built.document), built.baseline, "after the rolled-back edit")


def test_a_pad_length_is_edited_in_place_and_what_cannot_be_said_by_a_dimension_is_refused(
    freecad_root: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    with FreeCADAdapter(
        freecad_root=freecad_root,
        timeout_s=120.0,
        log_dir=tmp_path_factory.mktemp("pd-logs"),
        enable_test_hooks=True,
    ) as adapter:
        executor = Executor(parse_plan(plate_plan_dict()), adapter, Verifier(), ExecutorConfig())
        executor.start()
        executor.run_to_end()
        document = executor.document()
        assert document is not None
        baseline = signature(adapter, document)
        longer = plate_plan_dict()
        longer["operations"][1]["depth_mm"] = 10
        record = executor.edit_in_place(parse_plan(longer), ["base"])
        assert record.status == "applied", (record.failure, record.result.flags if record.result else None)
        assert executor.trace().reverts == ()  # nothing was undone, nothing ran again
        fresh = signature(adapter, fast_build(adapter, parse_plan(longer)))
        assert_same(signature(adapter, document), fresh, "pad length: in place vs fresh")
        adapter.set_active_document(SetActiveDocumentRequest(operation_id="s", document=document))
        back = executor.edit_in_place(parse_plan(plate_plan_dict()), ["base"])
        assert back.status == "applied", back
        assert_same(signature(adapter, document), baseline, "pad length: round trip")
        # the position of a hole on a face is a datum of its sketch: moved in place (the pattern that repeats it
        # follows), the same part as a fresh build, and back where it was the same as the baseline
        moved = plate_plan_dict()
        moved["operations"][2]["position"] = [-30, -20]
        applied = executor.edit_in_place(parse_plan(moved), ["hole_1"])
        assert applied.status == "applied", (applied.failure, applied.result.flags if applied.result else None)
        assert executor.trace().reverts == ()
        fresh_moved = signature(adapter, fast_build(adapter, parse_plan(moved)))
        assert_same(signature(adapter, document), fresh_moved, "hole position: in place vs fresh")
        adapter.set_active_document(SetActiveDocumentRequest(operation_id="s", document=document))
        again = executor.edit_in_place(parse_plan(plate_plan_dict()), ["hole_1"])
        assert again.status == "applied", again
        assert_same(signature(adapter, document), baseline, "hole position: round trip")


def test_the_step_after_an_in_place_edit_takes_the_edited_part_as_its_before_state(
    freecad_root: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """The mass the last step measured is not the part's any more once a hole was resized and moved in place: the next
    step compares the part it finds, not the one it left."""
    plan = plate_plan_dict()
    plan["operations"] = plan["operations"][:3]
    second = {**plan["operations"][2], "id": "hole_2", "position": [30, 15]}
    plan["operations"].append(second)
    with FreeCADAdapter(
        freecad_root=freecad_root,
        timeout_s=120.0,
        log_dir=tmp_path_factory.mktemp("stale-logs"),
        enable_test_hooks=True,
    ) as adapter:
        executor = Executor(parse_plan(plan), adapter, Verifier(), ExecutorConfig())
        executor.start()
        for _ in range(3):  # sketch, pad, first hole: the last one leaves its measure for the next step
            assert executor.next_operation().status.value == "success"
        executor.pause_now()
        stale = adapter.mass_properties(MassRequest(operation_id="stale"))
        document = executor.document()
        assert document is not None

        changed = copy.deepcopy(plan)
        changed["operations"][2].update({"position": [-30, -5], "diameter_mm": 10.0})
        record = executor.edit_in_place(parse_plan(changed), ["hole_1"])
        assert record.status == "applied", (record.failure, record.result.flags if record.result else None)
        assert executor.trace().reverts == ()
        edited = adapter.mass_properties(MassRequest(operation_id="edited"))
        assert edited.volume_mm3 != pytest.approx(stale.volume_mm3, abs=1.0)
        assert edited.centroid != pytest.approx(stale.centroid, abs=1e-3)

        step = executor.next_operation()
        assert step.status.value == "success", step
        assert step.mass_before is not None
        assert step.mass_before.volume_mm3 == pytest.approx(edited.volume_mm3, abs=1e-6)
        assert step.mass_before.centroid == pytest.approx(edited.centroid, abs=1e-6)
        assert step.mass_after is not None and step.mass_after.volume_mm3 < edited.volume_mm3
