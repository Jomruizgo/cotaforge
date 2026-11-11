"""Direct checks of ``ops_features._chamfer_extent_ok``: the created
chamfer face's extent along n(distance1_face) must equal ``distance2_mm``, match ``distance1_mm``
only when a flip is needed, and must never silently pass when it matches neither distance (a face
truncated near a corner, or a genuine software bug)."""

import math
from types import SimpleNamespace
from typing import Any

import ops_features
import Part
from FreeCAD import Vector
from protocol import WorkerError

D1 = 3.0
D2 = 2.0
N1 = (0.0, 0.0, 1.0)  # normal of distance1_face, matches check_part03's L-bracket chamfer geometry


def _tilted_face(z_extent: float, x_len: float = 10.0) -> Any:
    """A planar face whose normal is ``(0, -2, 3)/sqrt(13)``: a rectangle spanning ``x_len`` along
    world X and sized along the in-plane direction ``(0, 3, 2)/sqrt(13)`` so its bounding box
    extends exactly ``z_extent`` along Z (``N1``)."""
    s13 = math.sqrt(13.0)
    dy, dz = 3.0 / s13, 2.0 / s13  # in-plane direction, orthogonal to X and to the face normal
    length_along_d = z_extent / dz
    hx, hd = x_len / 2.0, length_along_d / 2.0
    corners = [
        Vector(-hx, -hd * dy, -hd * dz),
        Vector(hx, -hd * dy, -hd * dz),
        Vector(hx, hd * dy, hd * dz),
        Vector(-hx, hd * dy, hd * dz),
        Vector(-hx, -hd * dy, -hd * dz),
    ]
    return Part.Face(Part.makePolygon(corners))


def _fake_chamfer(candidate_faces: list[Any]) -> Any:
    """A duck-typed stand-in for the ``PartDesign::Chamfer`` object ``_chamfer_extent_ok`` reads:
    ``ch.Base[0].Shape.Faces`` (the faces the chamfer started from) and ``ch.Shape.Faces`` (the
    faces after the chamfer, base faces plus the new ones)."""
    base_faces = list(Part.makeBox(20, 20, 20).Faces)
    base = SimpleNamespace(Shape=SimpleNamespace(Faces=base_faces))
    shape = SimpleNamespace(Faces=[base_faces[0], *candidate_faces])
    return SimpleNamespace(Base=(base,), Shape=shape)


def check_extent_matching_d2_is_ok_without_flip() -> None:
    ch = _fake_chamfer([_tilted_face(D2)])
    assert ops_features._chamfer_extent_ok(ch, N1, D2, D1, "op") is True


def check_extent_matching_d1_needs_a_flip() -> None:
    ch = _fake_chamfer([_tilted_face(D1)])
    assert ops_features._chamfer_extent_ok(ch, N1, D2, D1, "op") is False


def check_extent_matching_neither_distance_raises_geometry_failed() -> None:
    truncated = D2 / 2.0  # e.g. a face cut short near a corner: matches neither d1 nor d2
    ch = _fake_chamfer([_tilted_face(truncated)])
    try:
        ops_features._chamfer_extent_ok(ch, N1, D2, D1, "op")
    except WorkerError as exc:
        assert exc.code == "geometry_failed", exc.code
        assert exc.operation_id == "op"
        assert exc.details["d1"] == D1 and exc.details["d2"] == D2, exc.details
        assert abs(exc.details["extent"] - truncated) < 1e-6, exc.details
    else:
        raise AssertionError("expected geometry_failed for an extent matching neither distance")
