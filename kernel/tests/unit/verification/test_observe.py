"""Tests for cotaforge.verification.observe.gather_observation using the StubAdapter."""

from cotaforge.contract import (
    Axis,
    BoundingBox,
    ComponentHandle,
    FaceHandle,
    FeatureHandle,
    InterferencePair,
    InterferenceRequest,
    InterferenceResult,
    MateCoincidentRequest,
    OperationResult,
    Transform,
)
from cotaforge.ir.ops_features import (
    CheckInterferenceOperation,
    ExtrudeOperation,
    HoleOperation,
    InsertComponentOperation,
    MateCoincidentOperation,
)
from cotaforge.ir.refs import FaceRef
from cotaforge.verification.observe import gather_observation
from tests.unit.executor.stub import StubAdapter

from .helpers import PLATE_BBOX, PLATE_MASS, cylindrical_face, line_edge, mass, planar_face, plate_faces

_HOLE_OP = HoleOperation(
    id="hole_1",
    intent="clearance",
    face=FaceRef(from_op="base"),
    position=(10.0, 10.0),
    standard="simple",
    diameter_mm=6.6,
    end_condition="through_all",
)


def _snapshot() -> object:
    from cotaforge.executor.context import StateSnapshot

    return StateSnapshot(mass=PLATE_MASS, bbox=PLATE_BBOX)


def test_gathers_mass_bbox_faces_and_edges() -> None:
    stub = StubAdapter()
    stub.mass = mass(47726.3)
    stub.bbox = PLATE_BBOX
    for face in plate_faces():
        stub.add_face(face)
    stub.add_face(
        cylindrical_face(
            "face:cyl",
            Axis(origin=(10.0, 10.0, 8.0), direction=(0.0, 0.0, -1.0)),
            3.3,
            (10.0, 10.0, 4.0),
            BoundingBox(min=(6.7, 6.7, 0.0), max=(13.3, 13.3, 8.0)),
        )
    )
    stub.add_edge(line_edge("edge:1", ("face:top", "face:xmax"), (100.0, 0.0, 8.0), (100.0, 60.0, 8.0)))
    result = OperationResult(
        operation_id="hole_1",
        feature=FeatureHandle(id="feature:2"),
        rebuild_ok=True,
        created_faces=(FaceHandle(id="face:cyl"),),
        modified_faces=(FaceHandle(id="face:top"),),
    )
    obs = gather_observation(stub, _HOLE_OP, result, _snapshot())
    assert obs.mass_after.volume_mm3 == 47726.3
    assert obs.mass_before == PLATE_MASS
    assert obs.bbox_after == PLATE_BBOX
    assert [f.handle.id for f in obs.created_faces] == ["face:cyl"]
    assert [f.handle.id for f in obs.modified_faces] == ["face:top"]
    assert stub.calls_to("edge_info") == []  # nothing reads the edges of a step: none is described
    assert obs.instance_count is None
    assert obs.component_bbox is None
    assert obs.interference is None


def test_instance_count_comes_from_the_result() -> None:
    stub = StubAdapter()
    op = ExtrudeOperation(id="base", intent="i", sketch="s", depth_mm=8.0)
    result = OperationResult(operation_id="base", feature=FeatureHandle(id="f"), rebuild_ok=True, instance_count=4)
    assert gather_observation(stub, op, result, _snapshot()).instance_count == 4


def test_component_bbox_and_transform_for_insert_component() -> None:
    stub = StubAdapter()
    stub.component_bboxes["comp:1"] = BoundingBox(min=(1.0, 1.0, 1.0), max=(2.0, 2.0, 2.0))
    stub.component_transforms["comp:1"] = Transform(origin=(1.0, 1.0, 1.0))
    op = InsertComponentOperation(id="ins", intent="i", part="block", placement={})
    result = OperationResult(
        operation_id="ins", feature=FeatureHandle(id="f"), rebuild_ok=True, component=ComponentHandle(id="comp:1")
    )
    obs = gather_observation(stub, op, result, _snapshot())
    assert obs.component_bbox == stub.component_bboxes["comp:1"]
    assert obs.component_transform == stub.component_transforms["comp:1"]


def test_an_insert_that_counts_its_faces_and_edges_describes_none_of_them() -> None:
    """The faces and edges of a component are the part's: thousands for a vendor part, and describing each one is what
    made an insert take minutes (or stop at the first edge that two faces do not share)."""
    stub = StubAdapter()
    stub.component_bboxes["comp:1"] = BoundingBox(min=(0.0, 0.0, 0.0), max=(1.0, 1.0, 1.0))
    stub.component_transforms["comp:1"] = Transform(origin=(0.0, 0.0, 0.0))
    op = InsertComponentOperation(id="ins", intent="i", part="block", placement={})
    result = OperationResult(
        operation_id="ins",
        feature=FeatureHandle(id="f"),
        rebuild_ok=True,
        component=ComponentHandle(id="comp:1"),
        face_count=32400,
        edge_count=97200,
    )
    obs = gather_observation(stub, op, result, _snapshot())
    assert obs.created_faces == () and obs.created_face_handles == ()
    assert stub.calls_to("face_info") == [] and stub.calls_to("edge_info") == []


def test_an_assembly_op_reads_the_volume_only_where_a_feature_op_reads_the_centroid_and_the_surface_too() -> None:
    stub = StubAdapter()
    insert = InsertComponentOperation(id="ins", intent="i", part="block", placement={})
    result = OperationResult(operation_id="x", feature=FeatureHandle(id="f"), rebuild_ok=True)
    gather_observation(stub, insert, result, _snapshot())
    gather_observation(stub, _HOLE_OP, result, _snapshot())
    light, full = stub.calls_to("mass_properties")
    assert (light.surface, light.centroid) == (False, False)  # type: ignore[attr-defined]
    assert (full.surface, full.centroid) == (True, True)  # type: ignore[attr-defined]


def test_an_assembly_op_reads_the_quick_box_where_a_feature_op_reads_the_exact_one() -> None:
    stub = StubAdapter()
    insert = InsertComponentOperation(id="ins", intent="i", part="block", placement={})
    result = OperationResult(operation_id="x", feature=FeatureHandle(id="f"), rebuild_ok=True)
    gather_observation(stub, insert, result, _snapshot())
    gather_observation(stub, _HOLE_OP, result, _snapshot())
    quick, exact = stub.calls_to("bounding_box")
    assert quick.exact_box is False and exact.exact_box is True  # type: ignore[attr-defined]


def test_mate_observes_the_component_owning_face_b() -> None:
    stub = StubAdapter()
    stub.add_face(planar_face("face:a", (0.0, 0.0, 1.0), (0.0, 0.0, 0.0), 1.0, PLATE_BBOX, component="comp:1"))
    stub.add_face(planar_face("face:b", (0.0, 0.0, -1.0), (0.0, 0.0, 5.0), 1.0, PLATE_BBOX, component="comp:2"))
    stub.component_bboxes["comp:2"] = BoundingBox(min=(0.0, 0.0, 0.0), max=(1.0, 1.0, 1.0))
    op = MateCoincidentOperation(id="m", intent="i", a=FaceRef(from_op="x"), b=FaceRef(from_op="y"))
    request = MateCoincidentRequest(operation_id="m", a=FaceHandle(id="face:a"), b=FaceHandle(id="face:b"))
    result = OperationResult(operation_id="m", feature=FeatureHandle(id="f"), rebuild_ok=True)
    obs = gather_observation(stub, op, result, _snapshot(), request)
    assert obs.component_bbox == stub.component_bboxes["comp:2"]


def test_check_interference_uses_the_raw_result_without_requerying_the_adapter() -> None:
    stub = StubAdapter()
    raw = InterferenceResult(
        pairs=(InterferencePair(a=ComponentHandle(id="c1"), b=ComponentHandle(id="c2"), volume_mm3=2.0),),
        total_volume_mm3=2.0,
    )
    op = CheckInterferenceOperation(id="chk", intent="i")
    request = InterferenceRequest(operation_id="chk")
    result = OperationResult(operation_id="chk", feature=None, rebuild_ok=True)
    obs = gather_observation(stub, op, result, _snapshot(), request, raw_result=raw)
    assert obs.interference is not None
    assert obs.interference.total_volume_mm3 == 2.0
    # gather_observation must be read-only: it must never call check_interference itself.
    assert stub.calls_to("check_interference") == []


def test_faces_are_described_only_for_the_checks_that_read_them() -> None:
    stub = StubAdapter()
    stub.add_face(
        cylindrical_face(
            "face:cyl",
            Axis(origin=(10.0, 10.0, 8.0), direction=(0.0, 0.0, -1.0)),
            3.3,
            (10.0, 10.0, 4.0),
            BoundingBox(min=(6.7, 6.7, 0.0), max=(13.3, 13.3, 8.0)),
        )
    )
    result = OperationResult(
        operation_id="hole_1",
        feature=FeatureHandle(id="feature:2"),
        rebuild_ok=True,
        created_faces=(FaceHandle(id="face:cyl"),),
        modified_faces=(FaceHandle(id="face:top"),),
    )
    counts = gather_observation(stub, _HOLE_OP, result, _snapshot(), check_names=frozenset({"created_faces_count"}))
    assert stub.calls_to("face_info") == []
    assert counts.created_faces == () and len(counts.created_face_handles) == 1
    assert len(counts.modified_face_handles) == 1

    gather_observation(stub, _HOLE_OP, result, _snapshot(), check_names=frozenset({"hole_radius"}))
    assert [r.face.id for r in stub.calls_to("face_info")] == ["face:cyl"]  # the modified face is not read
