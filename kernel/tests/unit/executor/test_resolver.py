"""Resolver: filtering rules on hand-built provenance tables."""

import pytest

from cotaforge.contract import (
    Axis,
    BodyHandle,
    BoundingBox,
    DocumentHandle,
    EdgeHandle,
    FeatureHandle,
    PlaneName,
    VertexHandle,
    VertexInfo,
)
from cotaforge.executor.context import ExecutionContext
from cotaforge.executor.errors import ResolutionError
from cotaforge.executor.resolver import Resolver
from cotaforge.ir.errors import PlanError
from cotaforge.ir.refs import BodyRef, EdgeRef, FaceRef, FaceSelector, PlaneRef, Range, VertexRef
from cotaforge.verification.thresholds import Thresholds
from tests.unit.verification.helpers import cylindrical_face, line_edge, plate_faces

from .stub import StubAdapter


def _setup() -> tuple[StubAdapter, Resolver]:
    stub = StubAdapter()
    for face in plate_faces("feature:1"):
        stub.add_face(face, feature_id="feature:1")
    hole = cylindrical_face(
        "face:cyl",
        Axis(origin=(10, 10, 8), direction=(0, 0, -1)),
        3.3,
        (10, 10, 4),
        BoundingBox(min=(6.7, 6.7, 0), max=(13.3, 13.3, 8)),
        area=165.9,
        created_by="feature:2",
    )
    stub.add_face(hole, feature_id="feature:2")
    top_modified = stub.faces["face:top"].model_copy(
        update={
            "provenance": stub.faces["face:top"].provenance.model_copy(
                update={"modified_by": (FeatureHandle(id="feature:2"),)}
            )
        }
    )
    stub.faces["face:top"] = top_modified
    stub.faces_of_feature["feature:2"].append(top_modified)
    stub.add_edge(line_edge("edge:1", ("face:top", "face:xmax"), (100, 0, 8), (100, 60, 8)))
    stub.add_edge(line_edge("edge:2", ("face:top", "face:ymax"), (0, 60, 8), (100, 60, 8)))
    ctx = ExecutionContext(document=DocumentHandle(id="doc:1"))
    ctx.feature_by_op["base"] = FeatureHandle(id="feature:1")
    ctx.feature_by_op["hole_1"] = FeatureHandle(id="feature:2")
    ctx.body_by_op["base"] = BodyHandle(id="body:1")
    return stub, Resolver(stub, ctx, Thresholds())


def test_face_by_normal_resolves_exactly_one_and_records_the_path() -> None:
    stub, resolver = _setup()
    face = resolver.face(FaceRef(from_op="base", select=FaceSelector(normal=(0, 0, 1))), "hole_1", "face")
    assert face.handle.id == "face:top"
    assert resolver.resolved_refs == {"face": "face:top"}
    assert stub.calls_to("faces_of")[0].feature.id == "feature:1"


def test_face_zero_and_many_candidates_are_plan_errors() -> None:
    _, resolver = _setup()
    with pytest.raises(ResolutionError) as many:
        resolver.face(FaceRef(from_op="base", select=FaceSelector(surface="plane")), "hole_1", "face")
    assert many.value.candidates == 6
    assert isinstance(many.value, PlanError)
    assert many.value.op_id == "hole_1"
    assert many.value.path == "face"
    with pytest.raises(ResolutionError) as zero:
        resolver.face(FaceRef(from_op="base", select=FaceSelector(normal=(1, 1, 0))), "hole_1", "face")
    assert zero.value.candidates == 0


def test_face_unknown_from_op_is_a_plan_error() -> None:
    _, resolver = _setup()
    with pytest.raises(ResolutionError):
        resolver.face(FaceRef(from_op="nope"), "x", "face")


def test_face_filters_axis_direction_either_sense_radius_range_area_center_and_role() -> None:
    _, resolver = _setup()
    up = FaceRef(from_op="hole_1", select=FaceSelector(surface="cylinder", axis_direction=(0, 0, 1)))
    assert resolver.face(up, "p", "axis").handle.id == "face:cyl"
    by_radius = FaceRef(from_op="hole_1", select=FaceSelector(radius=Range(min=3.0, max=3.5)))
    assert resolver.face(by_radius, "p", "axis").handle.id == "face:cyl"
    with pytest.raises(ResolutionError):
        resolver.face(FaceRef(from_op="hole_1", select=FaceSelector(radius=3.0)), "p", "axis")
    by_area = FaceRef(from_op="base", select=FaceSelector(area_mm2=480.0, center=(0, 30, 4)))
    assert resolver.face(by_area, "p", "f").handle.id == "face:xmin"
    modified = FaceRef(from_op="hole_1", select=FaceSelector(role="modified"))
    assert resolver.face(modified, "p", "f").handle.id == "face:top"
    created = FaceRef(from_op="hole_1", select=FaceSelector(role="created"))
    assert resolver.face(created, "p", "f").handle.id == "face:cyl"


def test_face_index_sorts_survivors_by_center() -> None:
    _, resolver = _setup()
    # the four side faces sorted by (x, y, z) of their centres: xmin(0,30), ymin(50,0), ymax(50,60), xmax(100,30)
    sides = FaceRef(from_op="base", select=FaceSelector(area_mm2=Range(min=400.0, max=900.0), index=1))
    assert resolver.face(sides, "p", "f").handle.id == "face:ymin"
    with pytest.raises(ResolutionError):
        resolver.face(sides.model_copy(update={"select": FaceSelector(area_mm2=Range(min=400.0), index=9)}), "p", "f")


def test_edge_between_and_of() -> None:
    _, resolver = _setup()
    between = EdgeRef(
        between=(
            FaceRef(from_op="base", select=FaceSelector(normal=(0, 0, 1))),
            FaceRef(from_op="base", select=FaceSelector(normal=(1, 0, 0))),
        )
    )
    edge = resolver.edge(between, "ch", "edges[0]")
    assert edge.handle.id == "edge:1"
    assert resolver.resolved_refs["edges[0]"] == "edge:1"
    assert resolver.resolved_refs["edges[0].between[0]"] == "face:top"
    of = EdgeRef(of=FaceRef(from_op="base", select=FaceSelector(normal=(0, 0, 1))), curve="line", length_mm=100.0)
    assert resolver.edge(of, "ch", "e").handle.id == "edge:2"
    with pytest.raises(ResolutionError) as exc:
        resolver.edge(
            EdgeRef(of=FaceRef(from_op="base", select=FaceSelector(normal=(0, 0, 1))), curve="line"), "ch", "e"
        )
    assert exc.value.candidates == 2


def test_vertex_by_faces_and_by_edge_end() -> None:
    stub, resolver = _setup()
    corner = VertexInfo(
        handle=VertexHandle(id="v:1"), point=(100, 60, 8), edges=(EdgeHandle(id="edge:1"), EdgeHandle(id="edge:2"))
    )
    stub.vertex_at_map[frozenset({"face:top", "face:xmax", "face:ymax"})] = [corner]
    stub.vertices_of_map["edge:1"] = [VertexInfo(handle=VertexHandle(id="v:0"), point=(100, 0, 8), edges=()), corner]
    faces = (
        FaceRef(from_op="base", select=FaceSelector(normal=(0, 0, 1))),
        FaceRef(from_op="base", select=FaceSelector(normal=(1, 0, 0))),
        FaceRef(from_op="base", select=FaceSelector(normal=(0, 1, 0))),
    )
    assert resolver.vertex(VertexRef(faces=faces), "op", "v").handle.id == "v:1"
    edge_ref = EdgeRef(between=(faces[0], faces[1]))
    assert resolver.vertex(VertexRef(edge=edge_ref, end="start"), "op", "v").handle.id == "v:0"
    assert resolver.vertex(VertexRef(edge=edge_ref, end="end"), "op", "v").handle.id == "v:1"
    stub.vertex_at_map.clear()
    with pytest.raises(ResolutionError):
        resolver.vertex(VertexRef(faces=faces), "op", "v")


def test_body_and_plane_or_face() -> None:
    _, resolver = _setup()
    assert resolver.body(BodyRef(from_op="base"), "op", "target").id == "body:1"
    with pytest.raises(ResolutionError):
        resolver.body(BodyRef(from_op="hole_1"), "op", "target")
    assert resolver.plane_or_face(PlaneRef(name="XZ"), "op", "on") == PlaneName.XZ
    assert (
        resolver.plane_or_face(FaceRef(from_op="base", select=FaceSelector(normal=(0, 0, -1))), "op", "on").handle.id
        == "face:bottom"
    )


def test_begin_step_clears_resolved_refs() -> None:
    _, resolver = _setup()
    resolver.face(FaceRef(from_op="base", select=FaceSelector(normal=(0, 0, 1))), "a", "face")
    resolver.begin_step()
    assert resolver.resolved_refs == {}


def test_ambiguous_face_lists_every_candidate_with_discriminating_attributes() -> None:
    _, resolver = _setup()
    with pytest.raises(ResolutionError) as exc:
        resolver.face(FaceRef(from_op="base", select=FaceSelector(area_mm2=480.0)), "hole_1", "face")
    error = exc.value
    assert error.rule == "ambiguous_reference" and error.candidates == 2
    assert len(error.candidate_details) == 2
    text = str(error)
    assert "face:xmin plane, normal (-1, 0, 0), centre (0, 30, 4), area 480 mm2, created by op `base`" in text
    assert "face:xmax plane, normal (1, 0, 0), centre (100, 30, 4), area 480 mm2, created by op `base`" in text
    assert "narrow the selector" in text
    with pytest.raises(ResolutionError) as zero:
        resolver.face(FaceRef(from_op="base", select=FaceSelector(normal=(1, 1, 0))), "hole_1", "face")
    # nothing matched: the faces `from_op` offers are listed instead, with the provenance hint
    assert zero.value.rule == "resolution" and len(zero.value.candidate_details) == 6
    assert "op 'base' offers 6 face(s) with role 'any'" in str(zero.value)
    assert "name the op that created or modified the face in from_op" in str(zero.value)


def test_nearest_to_picks_the_closest_centre_and_ties_stay_ambiguous() -> None:
    _, resolver = _setup()
    near = FaceRef(from_op="base", select=FaceSelector(surface="plane", nearest_to=(95.0, 30.0, 4.0)))
    assert resolver.face(near, "p", "f").handle.id == "face:xmax"
    tie = FaceRef(from_op="base", select=FaceSelector(area_mm2=480.0, nearest_to=(50.0, 30.0, 4.0)))
    with pytest.raises(ResolutionError) as exc:
        resolver.face(tie, "p", "f")
    assert exc.value.candidates == 2 and "face:xmin" in str(exc.value) and "face:xmax" in str(exc.value)


def test_largest_and_smallest_pick_by_area_and_ties_stay_ambiguous() -> None:
    _, resolver = _setup()
    largest = FaceRef(from_op="base", select=FaceSelector(normal=(0, 0, 1), largest=True))
    assert resolver.face(largest, "p", "f").handle.id == "face:top"
    smallest_side = FaceRef(from_op="base", select=FaceSelector(center=(100.0, 30.0, 4.0), smallest=True))
    assert resolver.face(smallest_side, "p", "f").handle.id == "face:xmax"
    with pytest.raises(ResolutionError) as exc:  # the two 6000 mm2 faces (top and bottom) tie
        resolver.face(FaceRef(from_op="base", select=FaceSelector(largest=True)), "p", "f")
    assert exc.value.candidates == 2
    with pytest.raises(ResolutionError) as exc:  # the two 480 mm2 faces tie
        resolver.face(FaceRef(from_op="base", select=FaceSelector(smallest=True)), "p", "f")
    assert exc.value.candidates == 2


def test_edge_nearest_to_breaks_the_tie_and_lists_edges_when_ambiguous() -> None:
    _, resolver = _setup()
    of = FaceRef(from_op="base", select=FaceSelector(normal=(0, 0, 1)))
    with pytest.raises(ResolutionError) as exc:
        resolver.edge(EdgeRef(of=of, curve="line"), "ch", "e")
    assert exc.value.rule == "ambiguous_reference"
    text = str(exc.value)
    assert "edge:1 line, midpoint (100, 30, 8)" in text and "edge:2 line, midpoint (50, 60, 8)" in text
    assert resolver.edge(EdgeRef(of=of, curve="line", nearest_to=(100.0, 20.0, 8.0)), "ch", "e").handle.id == "edge:1"
    assert resolver.edge(EdgeRef(of=of, curve="line", nearest_to=(40.0, 60.0, 8.0)), "ch", "e").handle.id == "edge:2"


# --- from_selection resolves a get_selection token directly, bypassing from_op/provenance --------


def test_face_from_selection_resolves_via_adapter_resolve_reference_and_face_info() -> None:
    stub, resolver = _setup()
    ref = FaceRef(from_selection="face:top")
    face = resolver.face(ref, "hole_2", "face")
    assert face.handle.id == "face:top"
    assert resolver.resolved_refs == {"face": "face:top"}
    resolve_calls = stub.calls_to("resolve_reference")
    assert len(resolve_calls) == 1
    assert resolve_calls[0].ref.token == "face:top"
    assert resolve_calls[0].ref.adapter == stub.name
    assert stub.calls_to("face_info")[0].face.id == "face:top"


def test_face_from_selection_stale_token_is_an_adapter_error() -> None:
    from cotaforge.contract import AdapterError

    _, resolver = _setup()
    with pytest.raises(AdapterError):
        resolver.face(FaceRef(from_selection="face:nope"), "hole_2", "face")


def test_edge_from_selection_resolves_via_adapter_resolve_reference_and_edge_info() -> None:
    stub, resolver = _setup()
    ref = EdgeRef(from_selection="edge:1")
    edge = resolver.edge(ref, "ch", "edges[0]")
    assert edge.handle.id == "edge:1"
    assert resolver.resolved_refs == {"edges[0]": "edge:1"}
    assert stub.calls_to("edge_info")[0].edge.id == "edge:1"


def test_face_selector_allows_one_tie_breaker_only() -> None:
    with pytest.raises(ValueError, match="at most one of nearest_to / largest / smallest / index"):
        FaceSelector(largest=True, smallest=True)
    with pytest.raises(ValueError, match="at most one"):
        FaceSelector(nearest_to=(0.0, 0.0, 0.0), index=0)
