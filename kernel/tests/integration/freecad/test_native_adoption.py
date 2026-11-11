"""Geometry a ``run_native`` fragment leaves outside any registered body is adopted as a body and therefore measured.

``mass_properties``, ``bounding_box`` and the checks only measure the registry's bodies, so a stray ``Part::Box``, a
boolean at the top level or a ``PartDesign::Body`` created by the fragment was reported in the step result (volume
change, envelope) and then invisible to every later measurement. The step adopts such solid results as bodies of its
own feature: the step result and the measurement agree, undoing the step removes the adoption with the objects, a step
that fails adopts nothing, and what carries no solid (sketches, planes, faces, empty containers) is never adopted.
"""

import math
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter, FreeCADSession
from cotaforge.contract import (
    BoxRequest,
    DocumentKind,
    Empty,
    FeatureRequest,
    MassRequest,
    NewDocumentRequest,
    OpenDocumentRequest,
    ProvenanceRole,
    RunNativeRequest,
    RunNativeResult,
    SaveDocumentRequest,
)
from tests.integration.freecad.worker_tools import stamp_for

pytestmark = pytest.mark.freecad

FAR_BOX = (
    'b = doc.addObject("Part::Box", "FarBox")\n'
    "b.Length, b.Width, b.Height = 10, 10, 10\n"
    "b.Placement = Placement(Vector(500, 0, 0), Rotation())\n"
)
# a 20 mm cube with a 10 mm cube cut out of one corner: 8000 - 1000
CUT = (
    'a = doc.addObject("Part::Box", "Base")\n'
    "a.Length, a.Width, a.Height = 20, 20, 20\n"
    'tool = doc.addObject("Part::Box", "Tool")\n'
    "tool.Length, tool.Width, tool.Height = 10, 10, 10\n"
    'cut = doc.addObject("Part::Cut", "Cut")\n'
    "cut.Base = a\ncut.Tool = tool\n"
)
EMPTY_CUT = (  # a box minus itself: the boolean comes out empty without raising
    'e = doc.addObject("Part::Cut", "Empty")\ne.Base = doc.getObject("FarBox")\ne.Tool = doc.getObject("FarBox")\n'
)
NOT_SOLIDS = (  # none of these carries a solid: nothing to measure, nothing to adopt
    'sk = doc.addObject("Sketcher::SketchObject", "Sketch")\n'
    "sk.addGeometry(Part.LineSegment(Vector(700, 0, 0), Vector(800, 0, 0)), False)\n"
    'plane = doc.addObject("Part::Plane", "Plane")\n'
    "plane.Length, plane.Width = 30, 30\n"
    "plane.Placement = Placement(Vector(900, 0, 0), Rotation())\n"
    'ref = doc.addObject("Part::Feature", "Face")\n'
    "ref.Shape = Part.makePlane(10, 10, Vector(1000, 0, 0))\n"
    'sheet = doc.addObject("Spreadsheet::Sheet", "Sheet")\n'
    'empty = doc.addObject("PartDesign::Body", "Empty")\n'
    'hidden = doc.addObject("Part::Plane", "HiddenPlane")\n'
    "hidden.Placement = Placement(Vector(1100, 0, 0), Rotation())\n"
    "hidden.Visibility = False\n"
)
PAD_THE_BODY = (
    'body = doc.getObject("Empty")\n'
    'sk = body.newObject("Sketcher::SketchObject", "Sketch")\n'
    'sk.AttachmentSupport = (body.Origin.OriginFeatures[3], [""])\n'
    'sk.MapMode = "FlatFace"\n'
    "pts = [Vector(0, 0, 0), Vector(50, 0, 0), Vector(50, 20, 0), Vector(0, 20, 0)]\n"
    "for i in range(4):\n"
    "    sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)\n"
    "for i in range(4):\n"
    '    sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))\n'
    "for i in (0, 2):\n"
    '    sk.addConstraint(Sketcher.Constraint("Horizontal", i))\n'
    "for i in (1, 3):\n"
    '    sk.addConstraint(Sketcher.Constraint("Vertical", i))\n'
    'sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, pts[1].x - pts[0].x))\n'
    'sk.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, pts[2].y - pts[1].y))\n'
    'sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, 0, 1, pts[0].x))\n'
    'sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, 0, 1, pts[0].y))\n'
    'pad = body.newObject("PartDesign::Pad", "Pad")\n'
    "pad.Profile = sk\n"
    "pad.Length = 8\n"
)


@pytest.fixture
def native(freecad_root: Path, tmp_path: Path) -> Iterator[FreeCADAdapter]:
    with FreeCADSession(
        freecad_root=freecad_root, timeout_s=120.0, log_dir=tmp_path / "logs", enable_test_hooks=True
    ) as adapter:
        adapter.new_document(NewDocumentRequest(operation_id="new", kind=DocumentKind.part, name="adoption"))
        yield adapter


def run(adapter: FreeCADAdapter, code: str, intent: str = "test") -> RunNativeResult:
    return adapter.run_native(RunNativeRequest(operation_id="native", code=code, intent=intent))


def volume(adapter: FreeCADAdapter) -> float:
    return adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3


def box_of(adapter: FreeCADAdapter) -> tuple[tuple[float, ...], tuple[float, ...]]:
    box = adapter.bounding_box(MassRequest(operation_id="b"))
    return tuple(box.min), tuple(box.max)


def state(adapter: FreeCADAdapter) -> dict[str, Any]:
    return adapter._raw_call("test_document_state", Empty(operation_id="state"))  # noqa: SLF001


def faces_of_step(adapter: FreeCADAdapter, result: RunNativeResult) -> int:
    assert result.feature is not None
    return len(
        adapter.faces_of(FeatureRequest(operation_id="f", feature=result.feature, role=ProvenanceRole.any)).faces
    )


def test_a_stray_box_is_measured_and_agrees_with_the_step_result(native: FreeCADAdapter) -> None:
    result = run(native, FAR_BOX, "a far block")

    assert result.ok and result.volume_delta_mm3 == pytest.approx(1000.0)
    assert volume(native) == pytest.approx(1000.0) == pytest.approx(result.volume_delta_mm3)
    low, high = box_of(native)
    assert low == pytest.approx((500.0, 0.0, 0.0)) and high == pytest.approx((510.0, 10.0, 10.0))
    assert result.bbox_after is not None and tuple(result.bbox_after.max) == pytest.approx(high)
    assert faces_of_step(native, result) == 6  # the box got handles of its own


def test_a_stray_box_next_to_a_registered_body_adds_to_it(native: FreeCADAdapter) -> None:
    native.box(BoxRequest(operation_id="box", size=(60, 40, 10)))
    assert volume(native) == pytest.approx(24000.0)

    result = run(native, FAR_BOX, "a far block")

    assert result.ok and result.volume_delta_mm3 == pytest.approx(1000.0)
    assert volume(native) == pytest.approx(25000.0)
    assert box_of(native)[1] == pytest.approx((510.0, 40.0, 10.0))


def test_a_boolean_result_is_measured_once_and_its_operands_are_not_counted(native: FreeCADAdapter) -> None:
    result = run(native, CUT, "a cut")

    assert result.ok and result.volume_delta_mm3 == pytest.approx(7000.0)
    assert volume(native) == pytest.approx(7000.0)
    assert faces_of_step(native, result) == 9  # one body (the cut), not three
    assert box_of(native)[1] == pytest.approx((20.0, 20.0, 20.0))


def test_a_native_boolean_over_a_registered_body_replaces_it_in_the_measurement(native: FreeCADAdapter) -> None:
    native.box(BoxRequest(operation_id="box", size=(20, 20, 20)))
    (name,) = [n for n, o in state(native)["objects"].items() if o["type"] == "Part::Box"]
    code = (
        f'base = doc.getObject("{name}")\n'
        'tool = doc.addObject("Part::Box", "Tool")\n'
        "tool.Length, tool.Width, tool.Height = 10, 10, 10\n"
        'cut = doc.addObject("Part::Cut", "Cut")\n'
        "cut.Base = base\ncut.Tool = tool\n"
    )

    result = run(native, code, "cut the registered box")

    assert result.ok and result.volume_delta_mm3 == pytest.approx(-1000.0)
    assert volume(native) == pytest.approx(7000.0)  # not 8000 + 7000
    assert native.undo_last(Empty(operation_id="undo")).reverted == result.feature
    assert volume(native) == pytest.approx(8000.0)  # the registered box is a body again


def test_undoing_the_step_takes_the_adoption_out_with_the_objects(native: FreeCADAdapter) -> None:
    native.box(BoxRequest(operation_id="box", size=(60, 40, 10)))
    before = state(native)["objects"]
    result = run(native, FAR_BOX, "a far block")
    assert volume(native) == pytest.approx(25000.0)

    assert native.undo_last(Empty(operation_id="undo")).reverted == result.feature

    assert state(native)["objects"] == before
    assert volume(native) == pytest.approx(24000.0)
    assert box_of(native)[1] == pytest.approx((60.0, 40.0, 10.0))
    again = run(native, FAR_BOX, "the same block again")  # the registry is as it was: the box is adopted afresh
    assert again.ok and volume(native) == pytest.approx(25000.0) and faces_of_step(native, again) == 6
    assert native.undo_last(Empty(operation_id="undo2")).reverted == again.feature
    assert native.undo_last(Empty(operation_id="undo3")).reverted is not None  # the harness box
    assert volume(native) == pytest.approx(0.0)


@pytest.mark.parametrize(
    "code",
    [
        FAR_BOX + 'raise RuntimeError("late")\n',
        FAR_BOX + EMPTY_CUT,
    ],
    ids=["raises", "leaves_an_empty_boolean"],
)
def test_a_failed_step_adopts_nothing(native: FreeCADAdapter, code: str) -> None:
    native.box(BoxRequest(operation_id="box", size=(60, 40, 10)))
    before = state(native)

    result = run(native, code, "fails")

    assert not result.ok and result.rolled_back
    assert state(native)["objects"] == before["objects"] and state(native)["features"] == before["features"]
    assert volume(native) == pytest.approx(24000.0)
    assert box_of(native)[1] == pytest.approx((60.0, 40.0, 10.0))
    ok = run(native, FAR_BOX, "then a good one")  # ids were not consumed by the rolled-back step
    assert ok.ok and faces_of_step(native, ok) == 6 and volume(native) == pytest.approx(25000.0)


def test_objects_without_a_solid_are_not_adopted(native: FreeCADAdapter) -> None:
    result = run(native, NOT_SOLIDS, "sketches, planes, a face, an empty body and a hidden plane")

    assert result.ok and result.volume_delta_mm3 == pytest.approx(0.0)
    assert volume(native) == pytest.approx(0.0)
    assert box_of(native) == ((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))  # a face at x=1000 in the envelope would show here
    assert faces_of_step(native, result) == 0


def test_a_body_created_empty_is_adopted_by_the_step_that_fills_it(native: FreeCADAdapter) -> None:
    run(native, 'doc.addObject("PartDesign::Body", "Empty")\n', "an empty body")
    assert volume(native) == pytest.approx(0.0)

    result = run(native, PAD_THE_BODY, "pad it")

    assert result.ok and result.volume_delta_mm3 == pytest.approx(8000.0)
    assert volume(native) == pytest.approx(8000.0)
    assert faces_of_step(native, result) == 6


def test_a_step_that_edits_an_adopted_box_remeasures_it(native: FreeCADAdapter) -> None:
    run(native, FAR_BOX, "a far block")

    result = run(native, 'doc.getObject("FarBox").Length = 40\n', "longer")

    assert result.ok and result.volume_delta_mm3 == pytest.approx(3000.0)
    assert volume(native) == pytest.approx(4000.0)
    assert box_of(native)[1] == pytest.approx((540.0, 10.0, 10.0))


def test_an_adopted_box_survives_save_and_reopen(freecad_root: Path, tmp_path: Path, native: FreeCADAdapter) -> None:
    native.box(BoxRequest(operation_id="box", size=(60, 40, 10)))
    run(native, FAR_BOX, "a far block")
    model = tmp_path / "adoption.FCStd"
    saved = native.save_document(
        SaveDocumentRequest(operation_id="save", path=str(model), stamp=stamp_for("adoption", 2))
    )

    with FreeCADSession(
        freecad_root=freecad_root, timeout_s=120.0, log_dir=tmp_path / "logs-b", enable_test_hooks=True
    ) as second:
        opened = second.open_document(
            OpenDocumentRequest(operation_id="open", path=str(model), adapter_state=saved.adapter_state)
        )
        assert opened.rebind.clean, opened.rebind
        assert volume(second) == pytest.approx(25000.0)
        assert box_of(second)[1] == pytest.approx((510.0, 40.0, 10.0))


def test_a_sphere_is_measured_although_its_poles_are_degenerated_edges(native: FreeCADAdapter) -> None:
    result = run(native, 'ball = doc.addObject("Part::Sphere", "Ball")\nball.Radius = 12\n', "a ball")

    assert result.ok and faces_of_step(native, result) == 1
    assert volume(native) == pytest.approx(4.0 / 3.0 * math.pi * 12**3, rel=1e-6)
    assert native.undo_last(Empty(operation_id="undo")).reverted == result.feature
    assert volume(native) == pytest.approx(0.0)
