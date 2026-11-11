"""Tests for cotaforge.ir.refs: symbolic references."""

import pytest
from pydantic import ValidationError

from cotaforge.contract import PlaneName, ProvenanceRole, SurfaceType
from cotaforge.ir.refs import (
    BodyRef,
    EdgeRef,
    FaceRef,
    FaceSelector,
    PlaneRef,
    Range,
    VertexRef,
)


def test_range_defaults_to_open() -> None:
    r = Range()
    assert r.min is None
    assert r.max is None


def test_face_selector_defaults() -> None:
    sel = FaceSelector()
    assert sel.surface is None
    assert sel.role == ProvenanceRole.any
    assert sel.index is None


def test_face_ref_requires_from_op() -> None:
    with pytest.raises(ValidationError):
        FaceRef()  # type: ignore[call-arg]


def test_face_ref_default_selector() -> None:
    ref = FaceRef(from_op="extrude1")
    assert ref.kind == "face"
    assert ref.select == FaceSelector()


def test_face_ref_with_surface_selector() -> None:
    ref = FaceRef(from_op="extrude1", select=FaceSelector(surface=SurfaceType.plane, normal=(0.0, 0.0, 1.0)))
    assert ref.select.surface == SurfaceType.plane


def test_edge_ref_between_two_faces() -> None:
    a = FaceRef(from_op="op1")
    b = FaceRef(from_op="op1")
    ref = EdgeRef(between=(a, b))
    assert ref.of is None


def test_edge_ref_of_face_requires_curve() -> None:
    face = FaceRef(from_op="op1")
    with pytest.raises(ValidationError):
        EdgeRef(of=face)


def test_edge_ref_of_face_with_curve_ok() -> None:
    face = FaceRef(from_op="op1")
    ref = EdgeRef(of=face, curve="circle")
    assert ref.curve == "circle"


def test_edge_ref_rejects_both_between_and_of() -> None:
    a = FaceRef(from_op="op1")
    b = FaceRef(from_op="op1")
    with pytest.raises(ValidationError):
        EdgeRef(between=(a, b), of=a, curve="line")


def test_edge_ref_rejects_neither_between_nor_of() -> None:
    with pytest.raises(ValidationError):
        EdgeRef()


def test_vertex_ref_from_three_faces() -> None:
    faces = (FaceRef(from_op="op1"), FaceRef(from_op="op1"), FaceRef(from_op="op1"))
    ref = VertexRef(faces=faces)
    assert ref.edge is None


def test_vertex_ref_from_edge_and_end() -> None:
    edge = EdgeRef(of=FaceRef(from_op="op1"), curve="circle")
    ref = VertexRef(edge=edge, end="start")
    assert ref.faces is None


def test_vertex_ref_rejects_both_faces_and_edge() -> None:
    faces = (FaceRef(from_op="op1"), FaceRef(from_op="op1"), FaceRef(from_op="op1"))
    edge = EdgeRef(of=FaceRef(from_op="op1"), curve="circle")
    with pytest.raises(ValidationError):
        VertexRef(faces=faces, edge=edge, end="start")


def test_vertex_ref_rejects_neither() -> None:
    with pytest.raises(ValidationError):
        VertexRef()


def test_vertex_ref_rejects_edge_without_end() -> None:
    edge = EdgeRef(of=FaceRef(from_op="op1"), curve="circle")
    with pytest.raises(ValidationError):
        VertexRef(edge=edge)


def test_plane_ref() -> None:
    ref = PlaneRef(name=PlaneName.XY)
    assert ref.kind == "plane"


def test_body_ref() -> None:
    ref = BodyRef(from_op="box1")
    assert ref.kind == "body"


# --- unknown fields must raise, never be silently dropped ------------------------------


def test_face_selector_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError, match="nrml"):
        FaceSelector(nrml=(0.0, 0.0, 1.0))  # type: ignore[call-arg]


def test_face_ref_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        FaceRef(from_op="op1", selct={"normal": (0.0, 0.0, 1.0)})  # type: ignore[call-arg]


def test_edge_ref_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        EdgeRef(of=FaceRef(from_op="op1"), curve="line", lenght_mm=10.0)  # type: ignore[call-arg]


def test_vertex_ref_rejects_unknown_field() -> None:
    faces = (FaceRef(from_op="op1"), FaceRef(from_op="op1"), FaceRef(from_op="op1"))
    with pytest.raises(ValidationError):
        VertexRef(faces=faces, extra_field=True)  # type: ignore[call-arg]


def test_plane_ref_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        PlaneRef(name=PlaneName.XY, nam="XY")  # type: ignore[call-arg]


def test_body_ref_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        BodyRef(from_op="box1", from_operation="box1")  # type: ignore[call-arg]


def test_range_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        Range(minimum=1.0)  # type: ignore[call-arg]


# --- from_selection as an alternative to from_op/provenance ----------------------------


def test_face_ref_from_selection_ok() -> None:
    ref = FaceRef(from_selection="doc:1|body:1|face:3")
    assert ref.from_op is None
    assert ref.select == FaceSelector()


def test_face_ref_requires_exactly_one_of_from_op_or_from_selection() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        FaceRef()  # neither
    with pytest.raises(ValidationError, match="exactly one"):
        FaceRef(from_op="op1", from_selection="doc:1|body:1|face:3")  # both


def test_face_ref_from_selection_rejects_select() -> None:
    with pytest.raises(ValidationError, match="from_selection"):
        FaceRef(from_selection="doc:1|body:1|face:3", select=FaceSelector(normal=(0.0, 0.0, 1.0)))


def test_edge_ref_from_selection_ok() -> None:
    ref = EdgeRef(from_selection="doc:1|body:1|edge:3")
    assert ref.between is None
    assert ref.of is None


def test_edge_ref_from_selection_rejects_filters() -> None:
    with pytest.raises(ValidationError, match="from_selection"):
        EdgeRef(from_selection="doc:1|body:1|edge:3", curve="line")
    with pytest.raises(ValidationError, match="from_selection"):
        EdgeRef(from_selection="doc:1|body:1|edge:3", nearest_to=(0.0, 0.0, 0.0))
