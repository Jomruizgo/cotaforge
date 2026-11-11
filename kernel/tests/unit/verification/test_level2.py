"""Tests for cotaforge.verification.level2: every check name's comparison semantics."""

import math

from cotaforge.contract import (
    Axis,
    BoundingBox,
    ComponentHandle,
    FaceHandle,
    FaceInfo,
    InterferencePair,
    InterferenceResult,
    MassProperties,
    SurfaceType,
    Transform,
)
from cotaforge.verification.level2 import Level2Check
from cotaforge.verification.observe import Observation
from cotaforge.verification.results import CheckKind, Expectation
from cotaforge.verification.thresholds import Thresholds

from .helpers import PLATE_BBOX, PLATE_MASS, cylindrical_face, mass, planar_face

HOLE_AXIS = Axis(origin=(10.0, 10.0, 8.0), direction=(0.0, 0.0, -1.0))
HOLE_FACE = cylindrical_face(
    "face:cyl", HOLE_AXIS, 3.3, (10.0, 10.0, 4.0), BoundingBox(min=(6.7, 6.7, 0.0), max=(13.3, 13.3, 8.0))
)


def _obs(
    mass_after: MassProperties = PLATE_MASS,
    bbox_after: BoundingBox = PLATE_BBOX,
    created: tuple[FaceInfo, ...] = (),
    instance_count: int | None = None,
    component_bbox: BoundingBox | None = None,
    component_transform: Transform | None = None,
    interference: InterferenceResult | None = None,
    mass_before: MassProperties = PLATE_MASS,
    modified: tuple[FaceInfo, ...] = (),
    hole_entry_face: FaceHandle | None = None,
    hole_entry_face_info: FaceInfo | None = None,
) -> Observation:
    return Observation(
        mass_after=mass_after,
        bbox_after=bbox_after,
        created_faces=created,
        modified_faces=modified,
        created_face_handles=tuple(f.handle for f in created),
        modified_face_handles=tuple(f.handle for f in modified),
        instance_count=instance_count,
        component_bbox=component_bbox,
        component_transform=component_transform,
        interference=interference,
        mass_before=mass_before,
        hole_entry_face=hole_entry_face,
        hole_entry_face_info=hole_entry_face_info,
    )


def _exp(name: str, unit: str, value: object, kind: CheckKind = CheckKind.asserted) -> Expectation:
    return Expectation(name=name, kind=kind, unit=unit, value=value)  # type: ignore[arg-type]


def _check(expectation: Expectation, observation: Observation) -> tuple[bool | None, object]:
    results = Level2Check(Thresholds()).check((expectation,), observation)
    assert len(results) == 1
    assert results[0].name == expectation.name
    assert results[0].kind == expectation.kind
    return results[0].ok, results[0].observed


def test_volume_delta_uses_mass_before_and_after() -> None:
    obs = _obs(mass_after=mass(48000.0 - 273.6955), mass_before=PLATE_MASS)
    ok, observed = _check(_exp("volume_delta", "mm3", -273.6955), obs)
    assert ok is True
    assert observed == -273.6955 + 0.0 or math.isclose(observed, -273.6955)  # type: ignore[arg-type]
    ok, _ = _check(_exp("volume_delta", "mm3", -136.8), obs)
    assert ok is False


def test_reported_expectation_gives_ok_none_but_still_observes() -> None:
    obs = _obs(mass_after=mass(47000.0))
    ok, observed = _check(_exp("volume_delta", "mm3", -999.0, kind=CheckKind.reported), obs)
    assert ok is None
    assert observed == -1000.0


def test_bbox_after_and_bbox_within() -> None:
    obs = _obs(bbox_after=PLATE_BBOX)
    assert _check(_exp("bbox_after", "bbox", PLATE_BBOX), obs)[0] is True
    assert _check(_exp("bbox_after", "bbox", BoundingBox(min=(0.0, 0.0, 0.0), max=(100.0, 60.0, 9.0))), obs)[0] is False
    assert _check(_exp("bbox_within", "bbox", BoundingBox(min=(0.0, 0.0, 0.0), max=(100.0, 60.0, 9.0))), obs)[0] is True
    assert (
        _check(_exp("bbox_within", "bbox", BoundingBox(min=(0.0, 0.0, 0.0), max=(100.0, 60.0, 7.0))), obs)[0] is False
    )


def test_created_faces_count_and_new_faces_count() -> None:
    obs = _obs(created=(HOLE_FACE,))
    assert _check(_exp("created_faces_count", "count", 1), obs)[0] is True
    assert _check(_exp("new_faces_count", "count", 2), obs)[0] is False


def test_hole_checks_on_the_single_cylindrical_face() -> None:
    obs = _obs(created=(HOLE_FACE,))
    assert _check(_exp("hole_radius", "mm", 3.3), obs)[0] is True
    assert _check(_exp("hole_radius", "mm", 3.0), obs)[0] is False
    # direction is matched in either sense
    assert _check(_exp("hole_axis_direction", "vec", (0.0, 0.0, -1.0)), obs)[0] is True
    assert _check(_exp("hole_axis_direction", "vec", (0.0, 0.0, 1.0)), obs)[0] is True
    assert _check(_exp("hole_axis_direction", "vec", (1.0, 0.0, 0.0)), obs)[0] is False
    # position: distance from the expected point to the observed axis line
    ok, observed = _check(_exp("hole_axis_position", "vec", (10.0, 10.0, 8.0)), obs)
    assert ok is True
    assert observed == (10.0, 10.0, 8.0)
    assert _check(_exp("hole_axis_position", "vec", (10.0, 10.0, 0.0)), obs)[0] is True
    assert _check(_exp("hole_axis_position", "vec", (10.5, 10.0, 8.0)), obs)[0] is False


def test_hole_within_face_compares_the_cylinder_footprint_with_the_entry_face_box() -> None:
    """Hole of radius 3.3 at world (10, 10) on the plate's top face: footprint [6.7, 13.3]^2 at z = 8 lies inside
    the face box [0, 100] x [0, 60]; the same hole on a face box that ends at x = 12 does not; a hole on the
    +X side face (normal +X, box x = 100) is judged on that plane, not on z."""
    top = planar_face("face:top", (0.0, 0.0, 1.0), (50.0, 30.0, 8.0), 6000.0, PLATE_BBOX)
    obs = _obs(created=(HOLE_FACE,), hole_entry_face=FaceHandle(id="face:top"), hole_entry_face_info=top)
    ok, observed = _check(_exp("hole_within_face", "bbox", top.bbox), obs)
    assert ok is True
    assert observed == BoundingBox(min=(6.7, 6.7, 8.0), max=(13.3, 13.3, 8.0))
    narrow = BoundingBox(min=(0.0, 0.0, 8.0), max=(12.0, 60.0, 8.0))
    assert _check(_exp("hole_within_face", "bbox", narrow), obs)[0] is False

    side_axis = Axis(origin=(100.0, 30.0, 4.0), direction=(-1.0, 0.0, 0.0))
    side_hole = cylindrical_face(
        "face:cyl_x", side_axis, 2.0, (50.0, 30.0, 4.0), BoundingBox(min=(0.0, 28.0, 2.0), max=(100.0, 32.0, 6.0))
    )
    side = planar_face(
        "face:xmax",
        (1.0, 0.0, 0.0),
        (100.0, 30.0, 4.0),
        480.0,
        BoundingBox(min=(100.0, 0.0, 0.0), max=(100.0, 60.0, 8.0)),
    )
    obs_side = _obs(created=(side_hole,), hole_entry_face=FaceHandle(id="face:xmax"), hole_entry_face_info=side)
    ok, observed = _check(_exp("hole_within_face", "bbox", side.bbox), obs_side)
    assert ok is True and observed == BoundingBox(min=(100.0, 28.0, 2.0), max=(100.0, 32.0, 6.0))

    unobservable = _obs(created=(HOLE_FACE,), hole_entry_face=FaceHandle(id="face:top"), hole_entry_face_info=None)
    assert _check(_exp("hole_within_face", "bbox", top.bbox), unobservable)[0] is False


def test_hole_entry_face_modified_checks_the_request_face_against_observed_modified_faces() -> None:
    top = planar_face("face:top", (0.0, 0.0, 1.0), (50.0, 30.0, 8.0), 6000.0, PLATE_BBOX)
    obs = _obs(modified=(top,), hole_entry_face=FaceHandle(id="face:top"))
    assert _check(_exp("hole_entry_face_modified", "bool", True), obs)[0] is True

    obs_missing = _obs(modified=(), hole_entry_face=FaceHandle(id="face:top"))
    assert _check(_exp("hole_entry_face_modified", "bool", True), obs_missing)[0] is False

    obs_unobservable = _obs(modified=(top,), hole_entry_face=None)
    assert _check(_exp("hole_entry_face_modified", "bool", True), obs_unobservable)[0] is False


def _renamed(face: FaceInfo, face_id: str, **update: object) -> FaceInfo:
    return face.model_copy(update={"handle": face.handle.model_copy(update={"id": face_id}), **update})


def test_hole_checks_fail_without_a_cylindrical_face() -> None:
    assert _check(_exp("hole_radius", "mm", 3.3), _obs())[0] is False


def test_a_hole_through_both_walls_of_a_tube_creates_coaxial_faces_that_are_one_hole() -> None:
    """A through-all hole across a tube leaves one cylindrical face per wall: same axis line, same radius (the
    axial midpoint each face reports differs). They are judged as the one hole they are."""
    far_wall = _renamed(
        HOLE_FACE, "x", axis=Axis(origin=(10.0, 10.0, 2.0), direction=(0.0, 0.0, 1.0)), center=(10.0, 10.0, 2.0)
    )
    obs = _obs(created=(HOLE_FACE, far_wall))
    assert _check(_exp("hole_radius", "mm", 3.3), obs)[0] is True
    assert _check(_exp("hole_axis_direction", "vec", (0.0, 0.0, -1.0)), obs)[0] is True
    assert _check(_exp("hole_axis_position", "vec", (10.0, 10.0, 8.0)), obs)[0] is True


def test_created_cylinders_that_are_not_one_hole_are_not_judged_as_one() -> None:
    other_radius = _obs(created=(HOLE_FACE, _renamed(HOLE_FACE, "x", radius=2.0)))
    shifted_axis = _obs(
        created=(HOLE_FACE, _renamed(HOLE_FACE, "y", axis=Axis(origin=(14.0, 10.0, 8.0), direction=(0.0, 0.0, -1.0))))
    )
    skewed_axis = _obs(
        created=(HOLE_FACE, _renamed(HOLE_FACE, "z", axis=Axis(origin=(10.0, 10.0, 8.0), direction=(1.0, 0.0, 0.0))))
    )
    for obs in (shifted_axis, skewed_axis):
        for name, unit, value in (("hole_radius", "mm", 3.3), ("hole_axis_direction", "vec", (0.0, 0.0, -1.0))):
            ok, _ = _check(_exp(name, unit, value), obs)
            assert ok is False, (name, obs.created_faces)
    # a second cylinder on the SAME axis is a counterbored hole: the axis is the hole's, the radius is the narrowest
    # one (here 2.0, not the 3.3 asked for)
    assert _check(_exp("hole_radius", "mm", 3.3), other_radius)[0] is False
    assert _check(_exp("hole_axis_direction", "vec", (0.0, 0.0, -1.0)), other_radius)[0] is True


def test_hole_thread_metadata_is_reported_and_never_fails() -> None:
    ok, observed = _check(_exp("hole_thread_metadata", "mm", 6.0, kind=CheckKind.reported), _obs())
    assert ok is None and observed is None


def test_cylinder_aliases_share_the_hole_semantics() -> None:
    obs = _obs(created=(HOLE_FACE,))
    assert _check(_exp("cylinder_radius", "mm", 3.3), obs)[0] is True
    assert _check(_exp("cylinder_axis_direction", "vec", (0.0, 0.0, 1.0)), obs)[0] is True


def test_fillet_radius_requires_all_created_faces_cylindrical_with_that_radius() -> None:
    obs = _obs(created=(HOLE_FACE,))
    assert _check(_exp("fillet_radius", "mm", 3.3), obs)[0] is True
    mixed = _obs(created=(HOLE_FACE, planar_face("p", (0.0, 0.0, 1.0), (0.0, 0.0, 0.0), 1.0, PLATE_BBOX)))
    assert _check(_exp("fillet_radius", "mm", 3.3), mixed)[0] is False


def test_the_fillet_of_a_round_edge_is_a_torus_whose_tube_has_the_radius() -> None:
    # the root radius of a boss on a plate: the fillet of a circular edge, a torus (bend radius 12, tube radius 2)
    root = HOLE_FACE.model_copy(update={"surface": SurfaceType.torus, "radius": 12.0, "minor_radius": 2.0})
    assert _check(_exp("fillet_radius", "mm", 2.0), _obs(created=(root,)))[0] is True
    assert _check(_exp("fillet_radius", "mm", 3.0), _obs(created=(root,)))[0] is False  # the tube, not the bend radius
    assert _check(_exp("fillet_radius", "mm", 12.0), _obs(created=(root,)))[0] is False
    # an adapter that does not say the tube radius cannot be judged: it does not pass, like any unobserved value
    silent = root.model_copy(update={"minor_radius": None})
    assert _check(_exp("fillet_radius", "mm", 2.0), _obs(created=(silent,)))[0] is False


def test_cut_extent_along_n_uses_the_companion_direction() -> None:
    obs = _obs(created=(HOLE_FACE,))
    direction = Expectation(name="cut_direction", kind=CheckKind.reported, unit="vec", value=(0.0, 0.0, -1.0))
    extent = _exp("cut_extent_along_n", "mm", 8.0)
    results = Level2Check(Thresholds()).check((direction, extent), obs)
    by_name = {r.name: r for r in results}
    assert by_name["cut_direction"].ok is None
    assert by_name["cut_extent_along_n"].ok is True
    assert by_name["cut_extent_along_n"].observed == 8.0
    half = _obs(
        created=(HOLE_FACE.model_copy(update={"bbox": BoundingBox(min=(6.7, 6.7, 4.0), max=(13.3, 13.3, 8.0))}),)
    )
    results = Level2Check(Thresholds()).check((direction, extent), half)
    assert results[1].ok is False
    # without a companion direction the check cannot be evaluated -> failed with a message
    results = Level2Check(Thresholds()).check((extent,), obs)
    assert results[0].ok is False
    assert results[0].message


def test_instance_count_and_centroid_after() -> None:
    assert _check(_exp("instance_count", "count", 4), _obs(instance_count=4))[0] is True
    assert _check(_exp("instance_count", "count", 4), _obs(instance_count=None))[0] is False
    assert _check(_exp("centroid_after", "vec", (50.0, 30.0, 4.0)), _obs())[0] is True
    assert _check(_exp("centroid_after", "vec", (50.0, 30.0, 4.5)), _obs())[0] is False


def test_component_checks() -> None:
    box = BoundingBox(min=(1.0, 1.0, 1.0), max=(2.0, 2.0, 2.0))
    t = Transform.from_axis_angle((0.0, 0.0, 1.0), 90.0, (1.0, 1.0, 1.0))
    obs = _obs(component_bbox=box, component_transform=t)
    assert _check(_exp("component_bbox", "bbox", box), obs)[0] is True
    assert _check(_exp("component_bbox", "bbox", PLATE_BBOX), obs)[0] is False
    assert _check(_exp("component_transform", "transform", t), obs)[0] is True
    assert _check(_exp("component_transform", "transform", Transform.identity()), obs)[0] is False
    assert _check(_exp("component_bbox", "bbox", box), _obs())[0] is False


def test_interference_and_assembly_volume() -> None:
    clean = InterferenceResult(pairs=(), total_volume_mm3=0.0)
    dirty = InterferenceResult(
        pairs=(InterferencePair(a=ComponentHandle(id="a"), b=ComponentHandle(id="b"), volume_mm3=3.0),),
        total_volume_mm3=3.0,
    )
    assert _check(_exp("interference_total", "mm3", 0.0), _obs(interference=clean))[0] is True
    assert _check(_exp("interference_total", "mm3", 0.0), _obs(interference=dirty))[0] is False
    assert _check(_exp("interference_total", "mm3", 0.0), _obs())[0] is False
    assert _check(_exp("assembly_volume", "mm3", 48000.0), _obs())[0] is True
    assert _check(_exp("assembly_volume", "mm3", 40000.0), _obs())[0] is False


def test_unknown_check_name_fails_loudly_when_asserted() -> None:
    ok, _ = _check(_exp("no_such_check", "mm", 1.0), _obs())
    assert ok is False
