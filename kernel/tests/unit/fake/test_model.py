"""Tests for the fake adapter's internal representation."""

import math

from cotaforge.adapters.fake.model import (
    FakeDocument,
    FakeFace,
    Primitive,
    PrimitiveKind,
    bbox_of_points,
    cylinder_bbox,
)
from cotaforge.contract import Axis, BoundingBox, DocumentKind, SurfaceType, Transform


def test_ids_are_kind_colon_counter_per_document() -> None:
    doc = FakeDocument(id="doc:1", kind=DocumentKind.part, name="p")
    assert doc.next_id("face") == "face:1"
    assert doc.next_id("face") == "face:2"
    assert doc.next_id("edge") == "edge:1"
    other = FakeDocument(id="doc:2", kind=DocumentKind.part, name="q")
    assert other.next_id("face") == "face:1"


def test_empty_document_fingerprint_is_stable_and_ignores_counters() -> None:
    doc = FakeDocument(id="doc:1", kind=DocumentKind.part, name="p")
    before = doc.fingerprint()
    doc.next_id("face")
    assert doc.fingerprint() == before


def test_cylinder_bbox_is_exact_for_tilted_axis() -> None:
    box = cylinder_bbox((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 3.0, 10.0)
    assert box == BoundingBox(min=(-3.0, -3.0, 0.0), max=(3.0, 3.0, 10.0))
    tilted = cylinder_bbox((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), 2.0, 5.0)
    assert tilted == BoundingBox(min=(0.0, -2.0, -2.0), max=(5.0, 2.0, 2.0))


def test_prism_primitive_transform_rotates_polygon_and_recomputes_bbox() -> None:
    polygon = ((0.0, 0.0, 0.0), (10.0, 0.0, 0.0), (10.0, 4.0, 0.0), (0.0, 4.0, 0.0))
    prim = Primitive(
        id="prim:1",
        kind=PrimitiveKind.prism,
        sign=1,
        volume_mm3=80.0,
        centroid=(5.0, 2.0, 1.0),
        bbox=bbox_of_points(polygon + tuple((x, y, 2.0) for x, y, _ in polygon)),
        feature="feature:1",
        polygon=polygon,
        direction=(0.0, 0.0, 1.0),
        depth=2.0,
    )
    rotated = prim.transformed(Transform.from_axis_angle((0.0, 0.0, 1.0), 90.0), new_id="prim:9")
    assert rotated.id == "prim:9"
    assert rotated.volume_mm3 == 80.0
    assert all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(rotated.centroid, (-2.0, 5.0, 1.0), strict=True))
    assert all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(rotated.bbox.min, (-4.0, 0.0, 0.0), strict=True))
    assert all(math.isclose(a, b, abs_tol=1e-9) for a, b in zip(rotated.bbox.max, (0.0, 10.0, 2.0), strict=True))


def test_cylindrical_face_transform_moves_axis() -> None:
    face = FakeFace(
        id="face:1",
        surface=SurfaceType.cylinder,
        normal=None,
        axis=Axis(origin=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0)),
        center=(0.0, 0.0, 5.0),
        radius=3.0,
        area_mm2=2 * math.pi * 3.0 * 10.0,
        bbox=cylinder_bbox((0.0, 0.0, 0.0), (0.0, 0.0, 1.0), 3.0, 10.0),
        body="body:1",
        created_by="feature:1",
        length=10.0,
    )
    moved = face.transformed(Transform(origin=(1.0, 2.0, 3.0)))
    assert moved.axis is not None
    assert moved.axis.origin == (1.0, 2.0, 3.0)
    assert moved.center == (1.0, 2.0, 8.0)
    assert moved.bbox == BoundingBox(min=(-2.0, -1.0, 3.0), max=(4.0, 5.0, 13.0))
