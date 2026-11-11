"""Hole and cut footprints must lie on the target face: the fake's analytic bookkeeping
would otherwise subtract a full cylinder or prism wherever a wrong face-frame position lands it."""

from __future__ import annotations

import math

import pytest

from cotaforge.adapters.fake.builders import circle_within_face, profile_touches_face
from cotaforge.adapters.fake.model import FakeFace
from cotaforge.contract import (
    AdapterError,
    BoundingBox,
    CutRequest,
    EndCondition,
    ErrorCode,
    FaceInfo,
    HoleRequest,
    HoleStandard,
    MassRequest,
    PlaneName,
    Profile,
    SketchFrame,
    SketchHandle,
    SurfaceType,
)
from tests.unit.fake.conftest import Session, face_with_normal


def _hole(session: Session, face: FaceInfo, position: tuple[float, float], diameter: float = 6.6) -> None:
    session.adapter.hole(
        HoleRequest(
            operation_id="hole",
            face=face.handle,
            position=position,
            standard=HoleStandard.simple,
            diameter_mm=diameter,
            end_condition=EndCondition.through_all,
        )
    )


def test_hole_inside_the_face_is_accepted_and_one_outside_is_geometry_failed(session: Session) -> None:
    session.new_part()
    plate = session.plate()
    top = face_with_normal(session.faces(plate.feature), (0.0, 0.0, 1.0))
    _hole(session, top, (-40.0, -20.0))  # world (10, 10): 10 mm from both edges, radius 3.3 -> inside
    with pytest.raises(AdapterError) as outside:
        _hole(session, top, (48.0, 0.0))  # world x = 98: the 6.6 mm circle crosses the x = 100 edge
    assert outside.value.info.code is ErrorCode.geometry_failed
    assert "not fully inside face" in outside.value.info.message and "(u, v) from the face centre" in (
        outside.value.info.message
    )
    with pytest.raises(AdapterError) as far_away:
        _hole(session, top, (100.0, 0.0))  # world x = 150: entirely off the plate
    assert far_away.value.info.code is ErrorCode.geometry_failed
    volume = session.adapter.mass_properties(MassRequest(operation_id="mass")).volume_mm3
    assert volume == pytest.approx(100 * 60 * 8 - math.pi * 3.3**2 * 8)  # only the first hole was drilled


def test_hole_on_a_side_face_uses_that_face_frame(session: Session) -> None:
    """+X face of the plate (100 x 60 x 8): frame u = +Y, v = +Z, origin (100, 30, 4); a 4 mm hole at (0, 0)
    fits (radius 2 < 4 mm half-thickness), at (0, 3) it would cross the top edge."""
    session.new_part()
    plate = session.plate()
    side = face_with_normal(session.faces(plate.feature), (1.0, 0.0, 0.0))
    _hole(session, side, (0.0, 0.0), diameter=4.0)
    with pytest.raises(AdapterError) as crossing:
        _hole(session, side, (0.0, 3.0), diameter=4.0)
    assert crossing.value.info.code is ErrorCode.geometry_failed


def test_hole_on_a_disc_face_is_bounded_by_the_outer_rim(session: Session) -> None:
    session.new_part()
    sketch = session.circle_sketch("disc_sketch", PlaneName.XY, (0.0, 0.0), 120.0)
    disc = session.extrude("disc", sketch, 10.0)
    top = face_with_normal(session.faces(disc.feature), (0.0, 0.0, 1.0))
    _hole(session, top, (45.0, 0.0), diameter=9.0)  # bolt circle: 45 + 4.5 < 60
    with pytest.raises(AdapterError) as outside:
        _hole(session, top, (57.0, 0.0), diameter=9.0)  # 57 + 4.5 > 60
    assert outside.value.info.code is ErrorCode.geometry_failed


def test_hole_inside_the_bbox_but_off_an_l_shaped_face_is_rejected(session: Session) -> None:
    """The L profile's +Z face: the notch (x > 10, y > 10) is inside the face bbox but not on the face."""
    session.new_part()
    bracket = session.l_bracket()
    top = face_with_normal(session.faces(bracket.feature), (0.0, 0.0, 1.0))
    with pytest.raises(AdapterError) as notch:
        _hole(session, top, (35.0 - top.center[0], 25.0 - top.center[1]), diameter=4.0)  # world (35, 25): the notch
    assert notch.value.info.code is ErrorCode.geometry_failed
    _hole(session, top, (35.0 - top.center[0], 5.0 - top.center[1]), diameter=4.0)  # world (35, 5): on the base leg


def test_cut_profile_entirely_outside_its_host_face_is_geometry_failed(session: Session) -> None:
    session.new_part()
    plate = session.plate()
    top = face_with_normal(session.faces(plate.feature), (0.0, 0.0, 1.0))
    off = session.circle_sketch("off_sketch", top.handle, (80.0, 0.0), 10.0)  # world x = 130: off the plate
    with pytest.raises(AdapterError) as outside:
        session.adapter.cut(
            CutRequest(operation_id="off", sketch=SketchHandle(id=off), end_condition=EndCondition.blind, depth_mm=2.0)
        )
    assert outside.value.info.code is ErrorCode.geometry_failed and "entirely outside its host face" in (
        outside.value.info.message
    )
    # a pocket that overhangs the edge (a notch) is still a cut: the profile touches the face
    notch = session.circle_sketch("notch_sketch", top.handle, (50.0, 0.0), 10.0)  # world x = 100: half on the edge
    session.adapter.cut(
        CutRequest(operation_id="notch", sketch=SketchHandle(id=notch), end_condition=EndCondition.blind, depth_mm=2.0)
    )


def _rect_face(width: float = 100.0, height: float = 60.0) -> FakeFace:
    polygon = [(0.0, 0.0, 0.0), (width, 0.0, 0.0), (width, height, 0.0), (0.0, height, 0.0)]
    return FakeFace(
        id="face:rect",
        surface=SurfaceType.plane,
        normal=(0.0, 0.0, 1.0),
        axis=None,
        center=(width / 2.0, height / 2.0, 0.0),
        radius=None,
        area_mm2=width * height,
        bbox=BoundingBox(min=(0.0, 0.0, 0.0), max=(width, height, 0.0)),
        body="body:1",
        created_by="feature:1",
        polygon=polygon,
    )


_IDENTITY_FRAME = SketchFrame(origin=(0.0, 0.0, 0.0), u=(1.0, 0.0, 0.0), v=(0.0, 1.0, 0.0), n=(0.0, 0.0, 1.0))


def _circle_profile(center: tuple[float, float], radius: float) -> Profile:
    cx, cy = center
    return Profile(
        kind="circle",
        area_mm2=math.pi * radius * radius,
        centroid=center,
        bbox_uv=((cx - radius, cy - radius), (cx + radius, cy + radius)),
        center=center,
        radius=radius,
    )


def test_profile_touches_face_accepts_a_circle_overlapping_only_a_corner_sliver() -> None:
    """A circle whose centre sits diagonally past a corner, close enough that the disc still reaches
    it, overlaps the face only in a thin diagonal sliver — none of the four cardinal sample points a naive
    5-point sample would use (N/S/E/W of the centre) land inside the polygon, and neither does the centre
    itself, yet the disc genuinely intersects the corner. The exact disc-polygon test must accept it."""
    face = _rect_face()
    # centre (-6, -6), radius 10: distance to the (0, 0) corner is 6*sqrt(2) ~= 8.49 < 10, so the disc reaches
    # past the corner; the four cardinal points ((4, -6), (-16, -6), (-6, 4), (-6, -16)) are all outside the
    # rectangle, and so is the centre itself.
    profile = _circle_profile((-6.0, -6.0), 10.0)
    assert profile_touches_face(face, _IDENTITY_FRAME, profile)


def test_profile_touches_face_rejects_a_circle_fully_outside_the_face() -> None:
    face = _rect_face()
    profile = _circle_profile((-50.0, -50.0), 5.0)  # nowhere near the (0, 0) corner
    assert not profile_touches_face(face, _IDENTITY_FRAME, profile)


def test_circle_within_face_falls_back_to_the_bbox_for_faces_without_a_loop() -> None:
    face = FakeFace(
        id="face:x",
        surface=SurfaceType.plane,
        normal=(0.0, 0.0, 1.0),
        axis=None,
        center=(5.0, 5.0, 0.0),
        radius=None,
        area_mm2=100.0,
        bbox=BoundingBox(min=(0.0, 0.0, 0.0), max=(10.0, 10.0, 0.0)),
        body="body:1",
        created_by="feature:1",
    )
    assert circle_within_face(face, (5.0, 5.0, 0.0), 4.0)
    assert not circle_within_face(face, (8.0, 5.0, 0.0), 4.0)
