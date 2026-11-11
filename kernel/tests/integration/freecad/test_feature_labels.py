"""The FreeCAD model tree reads like the plan: every object an op creates is labelled after the op.

The op ids (``f_block``, ``base``, ``holes``) live in the plan and the harness records; FreeCAD used to show its own
defaults (``Fusion002``, ``Cylinder005``). A real plan is driven through the executor on a real worker, the objects of
the live document are read back from a genuine ``saveAs`` of it, and the labels are compared with the op ids -- also
after ``revise_operation``/``undo_to`` rebuild the part (the undone objects take their labels with them, so a rebuild
never ends up with ``base001``) and after the exported macro is replayed in a fresh FreeCAD.
"""

import json
import subprocess
import sys
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import Empty, ExportRequest
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutorState
from cotaforge.ir import parse_plan
from cotaforge.verification.verifier import Verifier
from tests.conftest import plate_plan_dict

pytestmark = pytest.mark.freecad

# the objects FreeCAD itself puts inside every PartDesign::Body / App::Part (not created by an op)
ORIGIN_TYPES = {"App::Origin", "App::Line", "App::Plane", "App::Point"}
# per object (internal name -> (type, label)): a swap between two objects would fail too
PLATE_OBJECTS = {
    "Body": ("PartDesign::Body", "base_body"),
    "Sketch": ("Sketcher::SketchObject", "base_sketch"),  # the sketch STEP ``base`` consumed
    "Pad": ("PartDesign::Pad", "base"),
    "HoleSketch": ("Sketcher::SketchObject", "hole_1_sketch"),
    "Hole": ("PartDesign::Hole", "hole_1"),
    "PatternDir1": ("PartDesign::Line", "holes_dir1"),
    "PatternDir2": ("PartDesign::Line", "holes_dir2"),
    "LinearPattern": ("PartDesign::LinearPattern", "holes_pattern1"),
    "LinearPattern001": ("PartDesign::LinearPattern", "holes_pattern2"),
    "MultiTransform": ("PartDesign::MultiTransform", "holes"),
}
SHELL_PLAN = {
    "id": "hollowed",
    "kind": "part",
    "operations": [
        {"id": "block", "op": "box", "intent": "block", "size": [40, 30, 20]},
        {
            "id": "hollow",
            "op": "shell",
            "intent": "hollow it",
            "faces": [{"kind": "face", "from_op": "block", "select": {"normal": [0, 0, 1]}}],
            "thickness_mm": 2.0,
        },
    ],
}
SHELL_OBJECTS = {
    "Box": ("Part::Box", "block"),
    "Base": ("Part::Feature", "hollow_base"),
    "BaseFeature": ("PartDesign::FeatureBase", "hollow_base_feature"),  # FreeCAD's own link, owned by the op
    "Body": ("PartDesign::Body", "hollow_body"),
    "Thickness": ("PartDesign::Thickness", "hollow"),
}


@pytest.fixture
def hooked(freecad_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[FreeCADAdapter]:
    """A headless worker with the test hooks on (``test_save_document``): the only way to look at the objects of the
    live FreeCAD document itself instead of the registry's view of it."""
    with FreeCADAdapter(
        freecad_root=freecad_root,
        timeout_s=60.0,
        log_dir=tmp_path_factory.mktemp("labels-logs"),
        enable_test_hooks=True,
    ) as connected:
        yield connected


def _objects(adapter: FreeCADAdapter, path: Path) -> dict[str, tuple[str, str]]:
    """``{internal name: (type, tree label)}`` of every object an op made, read from a real ``saveAs`` of the live
    document."""
    rpc = adapter._rpc  # noqa: SLF001 - integration test, same seam test_revise_operation.py uses
    assert rpc is not None
    rpc.call("test_save_document", {"operation_id": "save", "path": str(path)}, timeout_s=30.0)
    with zipfile.ZipFile(path) as archive:
        root = ElementTree.fromstring(archive.read("Document.xml"))
    objects = root.find("Objects")
    data = root.find("ObjectData")
    assert objects is not None and data is not None
    types = {o.get("name"): o.get("type") for o in objects.iter("Object")}
    found: dict[str, tuple[str, str]] = {}
    for obj in data.iter("Object"):
        name = obj.get("name")
        type_name = str(types.get(name))
        if type_name in ORIGIN_TYPES:
            continue
        value = obj.find("Properties/Property[@name='Label']/String")
        assert name is not None and value is not None
        found[name] = (type_name, str(value.get("value")))
    return found


def _labels_by_name(adapter: FreeCADAdapter, path: Path) -> dict[str, str]:
    return {name: label for name, (_, label) in _objects(adapter, path).items()}


def _assert_no_manual_nodes(adapter: FreeCADAdapter) -> None:
    """Nothing the plan made reads as a hand-made feature in the feature tree (that would be a unexplained
    entry in the harness records)."""
    nodes = adapter.feature_tree(Empty(operation_id="t")).nodes
    assert not [n for n in nodes if n.parameters.get("manual")], nodes


def _started(adapter: FreeCADAdapter, plan: dict[str, Any]) -> Executor:
    executor = Executor(parse_plan(plan), adapter, Verifier(), ExecutorConfig())
    executor.start()
    executor.run_to_end()
    assert executor.state is ExecutorState.completed
    return executor


def _revise(executor: Executor, op_id: str, changes: dict[str, Any]) -> tuple[str, ...]:
    """What ``revise_operation`` does to the executor, minus the tool-layer bookkeeping."""
    operations = [op.model_dump(mode="python") for op in executor.plan.operations]
    index = next(i for i, op in enumerate(operations) if op["id"] == op_id)
    reverted = executor.undo_to(operations[index - 1]["id"])
    executor.extend([{**operations[index], **changes}, *operations[index + 1 :]])
    executor.run_to_end()
    return reverted


def _tree_names(adapter: FreeCADAdapter) -> list[str]:
    return [node.name for node in adapter.feature_tree(Empty(operation_id="t")).nodes]


def test_a_concept_plan_labels_its_objects_with_the_op_ids(hooked: FreeCADAdapter, tmp_path: Path) -> None:
    plan = {
        "id": "concept",
        "kind": "part",
        "operations": [
            {"id": "f_block", "op": "box", "intent": "block", "size": [100, 60, 20]},
            {
                "id": "f_tool",
                "op": "cylinder",
                "intent": "bore tool",
                "diameter_mm": 10,
                "height_mm": 30,
                "origin": [50, 30, -5],
            },
            {
                "id": "f_bored",
                "op": "boolean_subtract",
                "intent": "bore",
                "target": {"kind": "body", "from_op": "f_block"},
                "tools": [{"kind": "body", "from_op": "f_tool"}],
            },
            {
                "id": "f_shift",
                "op": "translate",
                "intent": "place",
                "target": {"kind": "body", "from_op": "f_bored"},
                "offset": [0, 10, 0],
            },
        ],
    }
    _started(hooked, plan)

    objects = _objects(hooked, tmp_path / "concept.FCStd")
    # move_body creates nothing: the moved object keeps the label of the op that made it
    assert objects == {
        "Box": ("Part::Box", "f_block"),
        "Cylinder": ("Part::Cylinder", "f_tool"),
        "Cut": ("Part::Cut", "f_bored"),
    }, objects
    assert _tree_names(hooked) == ["f_block", "f_tool", "f_bored", "f_shift"]
    _assert_no_manual_nodes(hooked)


def test_a_detail_plan_labels_sketches_features_and_helpers(hooked: FreeCADAdapter, tmp_path: Path) -> None:
    _started(hooked, plate_plan_dict())

    assert _objects(hooked, tmp_path / "plate.FCStd") == PLATE_OBJECTS
    assert _tree_names(hooked) == ["base", "hole_1", "holes"]
    _assert_no_manual_nodes(hooked)


def test_converting_a_primitive_leaves_no_hand_made_feature_in_the_tree(hooked: FreeCADAdapter, tmp_path: Path) -> None:
    """``PartDesign::FeatureBase`` is made by FreeCAD when the shell turns the box into a PartDesign body: it belongs
    to the shell op and must not read as a hand-made feature."""
    _started(hooked, SHELL_PLAN)

    assert _objects(hooked, tmp_path / "shell.FCStd") == SHELL_OBJECTS
    assert _tree_names(hooked) == ["block", "hollow"]
    _assert_no_manual_nodes(hooked)


def test_an_op_id_equal_to_another_ops_helper_label_is_reported_as_freecad_made_it(
    hooked: FreeCADAdapter, tmp_path: Path
) -> None:
    """Op ``base`` labels its Body ``base_body``; a later op NAMED ``base_body`` gets FreeCAD's de-duplicated
    ``base_body001``, and the feature tree says so instead of claiming a name the model tree does not show."""
    plan = plate_plan_dict()
    plan["operations"] = [
        *plan["operations"][:2],
        {"id": "base_body", "op": "box", "intent": "a block called like the plate's body", "size": [5, 5, 5]},
    ]
    _started(hooked, plan)

    labels = _labels_by_name(hooked, tmp_path / "collision.FCStd")
    assert labels["Body"] == "base_body" and labels["Box"] == "base_body001", labels
    assert _tree_names(hooked) == ["base", "base_body001"]


def test_a_rebuild_through_revise_and_undo_keeps_the_labels_clean(hooked: FreeCADAdapter, tmp_path: Path) -> None:
    executor = _started(hooked, plate_plan_dict())
    assert _objects(hooked, tmp_path / "before.FCStd") == PLATE_OBJECTS

    assert _revise(executor, "base", {"depth_mm": 10}) == ("holes", "hole_1", "base")
    # no ``base001`` / ``holes001``: the rebuilt objects get exactly the names and labels of the first build
    assert _objects(hooked, tmp_path / "after.FCStd") == PLATE_OBJECTS

    # the undone ops take their objects, and so their labels, with them
    assert executor.undo_to("base_sketch") == ("holes", "hole_1", "base")
    assert _objects(hooked, tmp_path / "undone.FCStd") == {}

    # rebuilt again from the undone state, with a different plate this time
    assert _revise(executor, "base", {"depth_mm": 12}) == ()
    assert executor.state is ExecutorState.completed
    assert _objects(hooked, tmp_path / "final.FCStd") == PLATE_OBJECTS
    assert _tree_names(hooked) == ["base", "hole_1", "holes"]
    _assert_no_manual_nodes(hooked)


@pytest.mark.parametrize(("plan_name", "expected"), [("plate", PLATE_OBJECTS), ("shell", SHELL_OBJECTS)])
def test_the_exported_macro_replays_the_labels_in_a_fresh_freecad(
    hooked: FreeCADAdapter, freecad_root: Path, tmp_path: Path, plan_name: str, expected: dict[str, tuple[str, str]]
) -> None:
    """The shell plan includes the ``FeatureBase`` link FreeCAD makes by itself on the conversion."""
    _started(hooked, plate_plan_dict() if plan_name == "plate" else SHELL_PLAN)
    live = _labels_by_name(hooked, tmp_path / "live.FCStd")
    assert live == {name: label for name, (_, label) in expected.items()}
    macro = tmp_path / "plate_macro.py"
    hooked.export_macro(ExportRequest(operation_id="macro", path=str(macro)))
    macro.write_text(
        macro.read_text(encoding="utf-8")
        + "\nimport json\n"
        + "skip = ('App::Origin', 'App::Line', 'App::Plane', 'App::Point')\n"
        + "print('LABELS=' + json.dumps({o.Name: o.Label for o in docs['doc:1'].Objects if o.TypeId not in skip}))\n",
        encoding="utf-8",
    )
    freecadcmd = freecad_root / ("bin/FreeCADCmd.exe" if sys.platform == "win32" else "usr/bin/freecadcmd")
    done = subprocess.run(
        [str(freecadcmd), "-c", f"exec(open({str(macro)!r}).read())"],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    output = done.stdout + done.stderr
    line = next((entry for entry in output.splitlines() if entry.startswith("LABELS=")), None)
    assert line is not None, output
    assert json.loads(line.removeprefix("LABELS=")) == live
