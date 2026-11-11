"""List, edit and delete the features of a file the harness did not build, on the real FreeCAD worker.

The part is made the way an engineer would, inside FreeCAD (a plate with a pad, a pocket, a fillet on the pocket's
corners and a chamfer on the plate's top edges), written with the engineer's own ``saveAs`` and opened again: its
history is adopted, no IR describes it. Every change goes through FreeCAD's own mechanism (a property set,
``Body.removeObject``) as one undoable step, and the volume is checked against what the geometry says it must be.
"""

import math
from collections.abc import Iterator
from pathlib import Path

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    AdapterError,
    DocumentKind,
    Empty,
    FaceRequest,
    FeatureEntry,
    FeatureList,
    FeatureRequest,
    FocusRequest,
    ListFeaturesRequest,
    MassRequest,
    NewDocumentRequest,
    OpenDocumentRequest,
    ProvenanceRole,
    RemoveFeatureRequest,
    RunNativeRequest,
    SetFeaturePropertiesRequest,
)
from tests.integration.freecad.worker_tools import call_hook

pytestmark = pytest.mark.freecad

PLATE = """
def rect(body, name, w, h, z=0.0):
    sk = body.newObject("Sketcher::SketchObject", name)
    sk.AttachmentSupport = (body.Origin.OriginFeatures[3], [""])
    sk.MapMode = "FlatFace"
    sk.AttachmentOffset = Placement(Vector(0, 0, z), Rotation())
    pts = [Vector(-w / 2, -h / 2, 0), Vector(w / 2, -h / 2, 0), Vector(w / 2, h / 2, 0), Vector(-w / 2, h / 2, 0)]
    for i in range(4):
        sk.addGeometry(Part.LineSegment(pts[i], pts[(i + 1) % 4]), False)
    for i in range(4):
        sk.addConstraint(Sketcher.Constraint("Coincident", i, 2, (i + 1) % 4, 1))
    for i in (0, 2):
        sk.addConstraint(Sketcher.Constraint("Horizontal", i))
    for i in (1, 3):
        sk.addConstraint(Sketcher.Constraint("Vertical", i))
    sk.addConstraint(Sketcher.Constraint("DistanceX", 0, 1, 0, 2, pts[1].x - pts[0].x))
    sk.addConstraint(Sketcher.Constraint("DistanceY", 1, 1, 1, 2, pts[2].y - pts[1].y))
    sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, 0, 1, pts[0].x))
    sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, 0, 1, pts[0].y))
    return sk

body = doc.addObject("PartDesign::Body", "Body")
sk = rect(body, "Sketch", 100, 60)
pad = body.newObject("PartDesign::Pad", "Pad")
pad.Profile = sk
pad.Length = 10
doc.recompute()
sk2 = rect(body, "Sketch001", 30, 20, 10)
pocket = body.newObject("PartDesign::Pocket", "Pocket")
pocket.Profile = sk2
pocket.Type = 1
doc.recompute()
inner = ["Edge%d" % (i + 1) for i, e in enumerate(pocket.Shape.Edges)
         if e.BoundBox.ZLength > 9 and abs(e.BoundBox.XMin) < 16 and abs(e.BoundBox.YMin) < 11]
fillet = body.newObject("PartDesign::Fillet", "Fillet")
fillet.Base = (pocket, inner)
fillet.Radius = 5
doc.recompute()
outer = ["Edge%d" % (i + 1) for i, e in enumerate(fillet.Shape.Edges)
         if e.BoundBox.ZMin > 9.9 and (e.BoundBox.XLength > 99 or e.BoundBox.YLength > 59
                                       or abs(e.BoundBox.XMax) > 49.9 or abs(e.BoundBox.YMax) > 29.9)]
chamfer = body.newObject("PartDesign::Chamfer", "Chamfer")
chamfer.Base = (fillet, outer)
chamfer.Size = 1
doc.recompute()
"""
PLATE_VOLUME = 100 * 60 * 10 - 30 * 20 * 10  # pad - pocket
FILLET_GAIN = 4 * 10 * (5**2 - math.pi * 5**2 / 4)  # the four pocket corners, R5, 10 mm tall


def fillet_gain(radius: float) -> float:
    return 4 * 10 * (radius**2 - math.pi * radius**2 / 4)


@pytest.fixture
def plate(freecad_root: Path, tmp_path: Path) -> Iterator[FreeCADAdapter]:
    """The plate file, reopened: adopted, with its history inside the body and no IR."""
    adapter = FreeCADAdapter(
        freecad_root=freecad_root, timeout_s=120.0, log_dir=tmp_path / "logs", enable_test_hooks=True
    )
    adapter.__enter__()
    try:
        adapter.new_document(NewDocumentRequest(operation_id="n", kind=DocumentKind.part, name="made_elsewhere"))
        made = adapter.run_native(RunNativeRequest(operation_id="r", code=PLATE, intent="a plate made by hand"))
        assert made.ok, made.error
        path = tmp_path / "plate.FCStd"
        call_hook(adapter, "test_save_document", {"path": str(path)})
        adapter.close_document(Empty(operation_id="close"))
        adapter.open_document(OpenDocumentRequest(operation_id="open", path=str(path)))
        yield adapter
    finally:
        adapter.__exit__(None, None, None)


def volume(adapter: FreeCADAdapter) -> float:
    return adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3


def features(adapter: FreeCADAdapter, name: str | None = None) -> FeatureList:
    return adapter.list_features(ListFeaturesRequest(operation_id="list", feature=name))


def by_name(listing: FeatureList) -> dict[str, FeatureEntry]:
    return {f.name: f for f in listing.features}


def set_props(adapter: FreeCADAdapter, feature: str, **properties: object):  # noqa: ANN201
    return adapter.set_feature_properties(
        SetFeaturePropertiesRequest(operation_id="set", feature=feature, properties=properties)
    )


def visible(adapter: FreeCADAdapter) -> dict[str, bool]:
    return {f.name: f.visible for f in features(adapter).features}


def test_the_features_of_an_adopted_file_are_listed_with_their_tip_and_parameters(plate: FreeCADAdapter) -> None:
    listing = features(plate)

    assert [f.name for f in listing.features] == ["Sketch", "Pad", "Sketch001", "Pocket", "Fillet", "Chamfer"]
    (body,) = listing.bodies
    assert body.name == "Body" and body.tip == "Chamfer" and body.body is not None
    found = by_name(listing)
    assert found["Chamfer"].is_tip and not found["Fillet"].is_tip
    assert found["Fillet"].properties["Radius"] == pytest.approx(5.0)
    assert found["Chamfer"].properties["Size"] == pytest.approx(1.0)
    assert found["Chamfer"].ref_count == 4 and found["Fillet"].ref_count == 4
    assert found["Pad"].properties["Length"] == pytest.approx(10.0)
    assert found["Fillet"].used_by == ("Chamfer",) and found["Fillet"].base == "Pocket"
    assert all(f.feature is None for f in listing.features)  # the file's history has no harness record
    assert all(f.body == body.body for f in listing.features)
    detail = features(plate, "Fillet").features[0]
    assert any(p.startswith("Radius:length") for p in detail.editable_properties)
    assert len(detail.refs) == 4 and all(r.kind == "edge" and r.size == pytest.approx(10.0) for r in detail.refs)


def test_the_fillet_radius_and_the_chamfer_size_change_in_place_with_the_volume_that_geometry_says(
    plate: FreeCADAdapter,
) -> None:
    start = volume(plate)
    assert start == pytest.approx(PLATE_VOLUME + FILLET_GAIN - 1280, rel=0.05)

    result = set_props(plate, "Fillet", Radius=4)

    assert result.ok and result.feature is not None
    assert volume(plate) - start == pytest.approx(fillet_gain(4) - FILLET_GAIN, rel=1e-3)
    assert by_name(features(plate))["Fillet"].properties["Radius"] == pytest.approx(4.0)

    before = volume(plate)
    assert set_props(plate, "Chamfer", Size=3).ok
    assert volume(plate) - before == pytest.approx(-0.5 * (3**2 - 1**2) * 320, rel=0.08)  # the top edges, 320 mm
    assert by_name(features(plate))["Chamfer"].properties["Size"] == pytest.approx(3.0)


def test_a_property_that_the_cad_refuses_or_cannot_rebuild_changes_nothing(plate: FreeCADAdapter) -> None:
    start = volume(plate)
    for bad in (-1, 0, "big"):
        with pytest.raises(AdapterError) as refused:
            set_props(plate, "Fillet", Radius=bad)
        assert "Radius" in refused.value.info.message
    with pytest.raises(AdapterError) as unknown:
        set_props(plate, "Fillet", Colour=3)
    assert "Radius" in unknown.value.info.message  # names what it does have

    too_big = set_props(plate, "Fillet", Radius=200)  # a fillet no corner can hold

    assert not too_big.ok and too_big.rolled_back and too_big.feature is None
    assert volume(plate) == pytest.approx(start)
    assert by_name(features(plate))["Fillet"].properties["Radius"] == pytest.approx(5.0)


def test_enumerations_and_booleans_are_checked_against_the_property(plate: FreeCADAdapter) -> None:
    with pytest.raises(AdapterError) as refused:
        set_props(plate, "Pocket", Type="Sideways")
    assert "ThroughAll" in refused.value.info.message
    assert set_props(plate, "Chamfer", ChamferType="Two distances", Size2=2).ok
    entry = by_name(features(plate))["Chamfer"]
    assert entry.properties["ChamferType"] == "Two distances" and entry.properties["Size2"] == pytest.approx(2.0)
    assert set_props(plate, "Chamfer", Suppressed=True).ok
    assert volume(plate) == pytest.approx(PLATE_VOLUME + FILLET_GAIN)


def test_the_edges_of_a_dress_up_can_be_reassigned_and_dropped(plate: FreeCADAdapter) -> None:
    kept = list(by_name(features(plate))["Chamfer"].ref_names)
    start = volume(plate)

    dropped = set_props(plate, "Chamfer", edges=kept[:2])

    assert dropped.ok and len(by_name(features(plate))["Chamfer"].ref_names) == 2
    assert volume(plate) > start  # fewer chamfered edges, more material
    with pytest.raises(AdapterError) as missing:
        set_props(plate, "Chamfer", edges=["Edge9999"])
    assert "Edge9999" in missing.value.info.message
    with pytest.raises(AdapterError):
        set_props(plate, "Pad", edges=["Edge1"])  # a pad works on no list of edges


def test_deleting_the_tip_moves_it_back_and_shows_the_body_with_its_sketches_hidden(plate: FreeCADAdapter) -> None:
    result = plate.remove_feature(RemoveFeatureRequest(operation_id="del", feature="Chamfer"))

    assert result.ok and result.feature is not None
    assert [o.name for o in result.removed] == ["Chamfer"]
    assert volume(plate) == pytest.approx(PLATE_VOLUME + FILLET_GAIN)
    listing = features(plate)
    assert "Chamfer" not in by_name(listing) and listing.bodies[0].tip == "Fillet"
    shown = visible(plate)
    assert shown["Fillet"] and not shown["Sketch"] and not shown["Sketch001"] and not shown["Pocket"]


def test_a_delete_is_one_undoable_step(plate: FreeCADAdapter) -> None:
    start = volume(plate)
    assert plate.remove_feature(RemoveFeatureRequest(operation_id="del", feature="Chamfer")).ok

    plate.undo_last(Empty(operation_id="undo"))

    assert volume(plate) == pytest.approx(start)
    listing = features(plate)
    assert listing.bodies[0].tip == "Chamfer" and "Chamfer" in by_name(listing)
    assert by_name(listing)["Chamfer"].properties["Size"] == pytest.approx(1.0)
    assert by_name(listing)["Chamfer"].ref_count == 4


def test_a_feature_other_features_need_is_not_deleted_unless_cascade_is_asked(plate: FreeCADAdapter) -> None:
    start = volume(plate)
    with pytest.raises(AdapterError) as refused:
        plate.remove_feature(RemoveFeatureRequest(operation_id="del", feature="Sketch001"))
    assert "Pocket" in refused.value.info.message and "cascade" in refused.value.info.message
    assert volume(plate) == pytest.approx(start)

    # the Pocket carries the Fillet, which carries the Chamfer: FreeCAD would re-link the Fillet to the Pad, but its
    # edges are the Pocket's, so the model cannot rebuild and the delete is rolled back
    rolled = plate.remove_feature(RemoveFeatureRequest(operation_id="del", feature="Pocket"))
    assert not rolled.ok and rolled.rolled_back and "Fillet" in (rolled.error.message if rolled.error else "")
    assert volume(plate) == pytest.approx(start)

    cascade = plate.remove_feature(RemoveFeatureRequest(operation_id="del", feature="Pocket", cascade=True))
    assert cascade.ok
    assert {o.name for o in cascade.removed} == {"Pocket", "Fillet", "Chamfer"}
    assert volume(plate) == pytest.approx(100 * 60 * 10)
    assert features(plate).bodies[0].tip == "Pad"


def test_the_last_solid_feature_of_a_body_is_not_deleted(plate: FreeCADAdapter) -> None:
    with pytest.raises(AdapterError) as refused:
        plate.remove_feature(RemoveFeatureRequest(operation_id="del", feature="Pad", cascade=True))
    assert "without any solid feature" in refused.value.info.message
    assert volume(plate) > 0


def test_focus_shows_each_tip_and_hides_the_sketches_and_the_intermediate_features(plate: FreeCADAdapter) -> None:
    plate.run_native(
        RunNativeRequest(
            operation_id="vis",
            code='for n in ("Sketch", "Pad", "Fillet"):\n    doc.getObject(n).Visibility = True\n'
            'doc.getObject("Chamfer").Visibility = False\n',
            intent="a bad view",
        )
    )
    assert not visible(plate)["Chamfer"] and visible(plate)["Sketch"]

    result = plate.focus_model(FocusRequest(operation_id="focus"))

    assert "Chamfer" in result.shown and {"Sketch", "Pad", "Fillet"} <= set(result.hidden)
    after = visible(plate)
    assert after["Chamfer"] and not any(after[n] for n in ("Sketch", "Sketch001", "Pad", "Pocket", "Fillet"))


def test_an_edge_of_the_current_part_can_be_added_to_a_dress_up_by_its_id(plate: FreeCADAdapter) -> None:
    nodes = plate.feature_tree(Empty(operation_id="tree")).nodes
    (body,) = [n for n in nodes if n.kind == "adopted"]
    faces = plate.faces_of(FeatureRequest(operation_id="f", feature=body.handle, role=ProvenanceRole.any)).faces
    bottom = next(f for f in faces if f.normal is not None and f.normal[2] < -0.99)
    edges = plate.edges_of(FaceRequest(operation_id="e", face=bottom.handle)).edges
    long_side = next(e for e in edges if e.length_mm == pytest.approx(100.0))
    kept = list(by_name(features(plate))["Fillet"].ref_names)
    start = volume(plate)

    result = set_props(plate, "Fillet", edges=[*kept, long_side.handle.id])

    assert result.ok
    assert len(by_name(features(plate))["Fillet"].ref_names) == len(kept) + 1
    assert volume(plate) - start == pytest.approx(-(1 - math.pi / 4) * 5**2 * 100, rel=0.02)  # the bottom edge, R5
    with pytest.raises(AdapterError):
        set_props(plate, "Fillet", edges=["edge:99999"])
