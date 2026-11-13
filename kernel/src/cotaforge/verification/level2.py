"""Level 2: compare analytic expectations with the observation.

Each check name has fixed comparison semantics, documented on the corresponding ``_observe_*``
function. ``reported`` expectations are evaluated the same way but never yield ``ok`` True/False.
"""

import math
from collections.abc import Callable

from cotaforge.contract import (
    Axis,
    BoundingBox,
    FaceInfo,
    FlatPattern,
    MassProperties,
    SketchFrame,
    SurfaceType,
    Transform,
    Vec3,
)
from cotaforge.verification import formulas
from cotaforge.verification.observe import Observation
from cotaforge.verification.results import CheckKind, CheckResult, Expectation, Value
from cotaforge.verification.thresholds import DEFAULT_THRESHOLDS, Thresholds

REAL_GEOMETRY = "from the real geometry"
"""Start of the note of an expectation computed from the boolean of the op's tool with the previous solid."""
CUT_DIRECTION = "cut_direction"
CUT_AREA = "cut_area"
DRAFT_PULL = "draft_pull"


class _UnobservableError(Exception):
    """Raised when an observation lacks what a check needs; carries the message."""


class _Outcome:
    __slots__ = ("observed", "ok", "message")

    def __init__(self, ok: bool, observed: Value | None, message: str = "") -> None:
        self.ok = ok
        self.observed = observed
        self.message = message


Evaluator = Callable[["Level2Check", Expectation, Observation, dict[str, Expectation]], _Outcome]


class Level2Check:
    def __init__(self, thresholds: Thresholds = DEFAULT_THRESHOLDS) -> None:
        self.thresholds = thresholds

    def check(self, expectations: tuple[Expectation, ...], observation: Observation) -> tuple[CheckResult, ...]:
        by_name = {e.name: e for e in expectations}
        results: list[CheckResult] = []
        for expectation in expectations:
            evaluator = _EVALUATORS.get(expectation.name)
            if evaluator is None:
                outcome = _Outcome(False, None, f"unknown check name {expectation.name!r}")
            else:
                try:
                    outcome = evaluator(self, expectation, observation, by_name)
                except _UnobservableError as exc:
                    outcome = _Outcome(False, None, str(exc))
            ok: bool | None = outcome.ok if expectation.kind == CheckKind.asserted else None
            message = outcome.message
            if ok is False and not message:
                message = f"expected {expectation.value!r}, observed {outcome.observed!r}"
                if expectation.note:
                    message += f" ({expectation.note})"
            results.append(
                CheckResult(
                    name=expectation.name,
                    kind=expectation.kind,
                    ok=ok,
                    expected=expectation.value,
                    observed=outcome.observed,
                    message=message,
                    basis=expectation.note if expectation.note.startswith(REAL_GEOMETRY) else "",
                )
            )
        return tuple(results)

    # --- scalar helpers ---------------------------------------------------------------------

    def _scalar(self, expectation: Expectation, observed: float | int) -> _Outcome:
        unit = expectation.unit
        if unit not in ("mm", "mm2", "mm3", "count", "deg"):
            return _Outcome(False, observed, f"unit {unit!r} is not scalar")
        return _Outcome(self.thresholds.close(float(expectation.value), float(observed), unit), observed)  # type: ignore[arg-type]

    def _vec(self, expectation: Expectation, observed: Vec3) -> _Outcome:
        return _Outcome(self.thresholds.close_position(expectation.value, observed), observed)  # type: ignore[arg-type]

    def _bbox(self, expectation: Expectation, observed: BoundingBox, *, within: bool = False) -> _Outcome:
        expected = expectation.value
        if not isinstance(expected, BoundingBox):
            return _Outcome(False, observed, "expected value is not a bounding box")
        if within:
            return _Outcome(self.thresholds.bbox_within(observed, expected), observed)
        return _Outcome(self.thresholds.close_bbox(expected, observed), observed)


# --- evaluators (one per check name) ----------------------------------------------------------


def _volume_delta(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """observed = mass_after.volume - mass_before.volume."""
    if obs.mass_before is None:
        raise _UnobservableError("mass before the op is unknown")
    return check._scalar(e, obs.mass_after.volume_mm3 - obs.mass_before.volume_mm3)


def _bbox_after(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """observed bbox_after equals the expected box (position tolerance per corner)."""
    return check._bbox(e, obs.bbox_after)


def _bbox_within(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """observed bbox_after lies inside the expected box."""
    return check._bbox(e, obs.bbox_after, within=True)


def _bbox_contains(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """observed bbox_after CONTAINS the expected box (position tolerance per component): a lower bound. Used
    where the kernel's box is loose (curved swept faces) but always contains the true one."""
    expected = e.value
    if not isinstance(expected, BoundingBox):
        return _Outcome(False, obs.bbox_after, "expected value is not a bounding box")
    return _Outcome(check.thresholds.bbox_within(expected, obs.bbox_after), obs.bbox_after)


def _created_faces_count(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    return check._scalar(e, len(obs.created_face_handles))


def _modified_faces_count(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """Count of modified faces (e.g. the host body's side faces extended when an on-face extrude's
    profile ``covers`` the host face)."""
    return check._scalar(e, len(obs.modified_face_handles))


def _hole_groups(check: Level2Check, obs: Observation) -> list[list[FaceInfo]]:
    """The cylinders the op created, grouped by axis LINE: one group per hole. A hole through both walls of a tube
    leaves one cylindrical face per wall (each reports the axial midpoint of its own piece, so the origins differ) and a
    counterbored hole a second, wider cylinder: both are one group. An op with several holes (a pattern in one
    feature) leaves one group per hole."""
    cylinders = [f for f in obs.created_faces if f.surface == SurfaceType.cylinder]
    if not cylinders:
        raise _UnobservableError("expected a created cylindrical face, found 0")
    t = check.thresholds
    groups: list[list[FaceInfo]] = []
    for face in cylinders:
        if face.axis is None or face.radius is None:
            raise _UnobservableError("cylindrical face has no axis/radius")
        for group in groups:
            first = group[0]
            assert first.axis is not None
            if t.parallel(first.axis.direction, face.axis.direction, either_sense=True) and (
                formulas.distance_point_to_axis(face.axis.origin, first.axis) <= t.position_mm
            ):
                group.append(face)
                break
        else:
            groups.append([face])
    return groups


def _pick(group: list[FaceInfo], widest: bool) -> FaceInfo:
    pick = max if widest else min
    return pick(group, key=lambda c: c.radius)  # type: ignore[arg-type,return-value]


def _holes(check: Level2Check, obs: Observation, others: dict[str, Expectation]) -> list[list[FaceInfo]]:
    """The holes the op created, one axis group each. One hole is expected unless the op also states ``hole_count``
    (several holes in one feature): cylinders that are not one coaxial hole are then not judged as one."""
    groups = _hole_groups(check, obs)
    if len(groups) > 1 and "hole_count" not in others:
        raise _UnobservableError(
            f"{len(obs.created_faces)} created faces, but the cylinders are not one coaxial hole (one axis)"
        )
    return groups


def _hole_count(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """The op made as many holes (axis groups of cylinders) as it asked for."""
    return check._scalar(e, len(_hole_groups(check, obs)))


def _single_cylinder(
    check: Level2Check, obs: Observation, others: dict[str, Expectation], *, near: Vec3 | None = None
) -> FaceInfo:
    """The cylinder the hole created: the narrowest of its axis group (the hole itself). With several holes
    (``near`` = a point of the axis of the one asked about) the group whose axis passes nearest that point."""
    groups = _holes(check, obs, others)
    if near is not None:
        return _pick(min(groups, key=lambda g: formulas.distance_point_to_axis(near, g[0].axis)), False)  # type: ignore[arg-type]
    return _pick(groups[0], False)


def _hole_axis_direction(
    check: Level2Check, e: Expectation, obs: Observation, others: dict[str, Expectation]
) -> _Outcome:
    """The created cylindrical face's axis (every hole's, with several) is parallel (either sense) to the expected
    direction."""
    first: _Outcome | None = None
    for group in _holes(check, obs, others):
        face = _pick(group, False)
        assert face.axis is not None
        observed = face.axis.direction
        outcome = _Outcome(check.thresholds.parallel(e.value, observed, either_sense=True), observed)  # type: ignore[arg-type]
        if not outcome.ok:
            return outcome
        first = first or outcome
    assert first is not None
    return first


def _hole_axis_position(
    check: Level2Check, e: Expectation, obs: Observation, others: dict[str, Expectation]
) -> _Outcome:
    """The expected point lies on the axis of the created cylindrical face nearest to it (observed = closest axis
    point); with several holes each has its own row."""
    face = _single_cylinder(check, obs, others, near=e.value)  # type: ignore[arg-type]
    assert face.axis is not None
    closest = formulas.closest_point_on_axis(e.value, face.axis)  # type: ignore[arg-type]
    return check._vec(e, closest)


def _hole_radius(check: Level2Check, e: Expectation, obs: Observation, others: dict[str, Expectation]) -> _Outcome:
    first: _Outcome | None = None
    for group in _holes(check, obs, others):
        face = _pick(group, False)
        assert face.radius is not None
        outcome = check._scalar(e, face.radius)
        if not outcome.ok:
            return outcome
        first = first or outcome
    assert first is not None
    return first


def _hole_entry_face_modified(
    check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]
) -> _Outcome:
    """The hole's entry face (the request's ``face``) must be reported as modified."""
    if obs.hole_entry_face is None:
        raise _UnobservableError("hole entry face not observed")
    observed = obs.hole_entry_face in obs.modified_face_handles
    return _Outcome(observed == bool(e.value), observed)


def _hole_within_face(
    check: Level2Check, e: Expectation, obs: Observation, others: dict[str, Expectation]
) -> _Outcome:
    """The created cylinder's footprint on the entry face's plane (observed: its flat bounding box) lies inside
    the entry face's bounding box (expected: the face's box before the hole). Exact when the face is a full
    axis-aligned rectangle; the ExpectedComputer asserts it only then and reports it otherwise."""
    face = obs.hole_entry_face_info
    if face is None or face.normal is None:
        raise _UnobservableError("hole entry face not observed after the hole")
    footprint: BoundingBox | None = None
    for group in _holes(check, obs, others):  # every hole of the op, one footprint each
        cylinder = _pick(group, True)
        assert cylinder.axis is not None and cylinder.radius is not None
        try:
            one = formulas.disc_footprint_bbox(cylinder.axis, cylinder.radius, face.center, face.normal)
        except ValueError as exc:
            raise _UnobservableError(str(exc)) from exc
        footprint = one if footprint is None else formulas.bbox_union(footprint, one)
    assert footprint is not None
    return check._bbox(e, footprint, within=True)


def _fillet_radius(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """Every created face is a cylinder of the expected radius, or a torus whose tube has it (the fillet of a round
    edge, such as the root of a boss: its faces are tori)."""
    if not obs.created_faces:
        raise _UnobservableError("no created faces")
    radii: list[float] = []
    for face in obs.created_faces:
        if face.surface == SurfaceType.torus:
            if face.minor_radius is None:
                raise _UnobservableError(f"created torus {face.handle.id} does not report the radius of its tube")
            radii.append(face.minor_radius)
            continue
        if face.surface != SurfaceType.cylinder or face.radius is None:
            return _Outcome(False, None, f"created face {face.handle.id} is not cylindrical")
        radii.append(face.radius)
    worst = max(radii, key=lambda r: abs(r - float(e.value)))  # type: ignore[arg-type]
    return check._scalar(e, worst)


def _cut_extent_along_n(
    check: Level2Check, e: Expectation, obs: Observation, by_name: dict[str, Expectation]
) -> _Outcome:
    """Extent of the union of created-face boxes along the companion ``cut_direction`` vector.

    Alone it must equal the expected value. With the companion ``cut_area`` (a through cut or hole) the expected value
    is only the UPPER bound, the reach of the body ahead of the sketch, and the lower bound is the material the cut
    removed over that section: the walls of a through cut run through the material under its profile, which is
    shorter than the part when the part is taller there (a thin flange under a hub)."""
    direction = by_name.get(CUT_DIRECTION)
    if direction is None:
        raise _UnobservableError("cut_extent_along_n needs a cut_direction companion expectation")
    if not obs.created_faces:
        raise _UnobservableError("no created faces")
    union = obs.created_faces[0].bbox
    for face in obs.created_faces[1:]:
        union = formulas.bbox_union(union, face.bbox)
    observed = union.extent_along(direction.value)  # type: ignore[arg-type]
    section = by_name.get(CUT_AREA)
    if section is None:
        return check._scalar(e, observed)
    reach = float(e.value)  # type: ignore[arg-type]
    tolerance = check.thresholds.abs_mm + check.thresholds.rel * reach
    if observed > reach + tolerance:
        return _Outcome(
            False,
            observed,
            f"the faces of the cut run {observed:.6g} mm along its direction, past the {reach:.6g} mm the body "
            "reaches ahead of the sketch",
        )
    if obs.mass_before is not None and float(section.value) > 0.0:  # type: ignore[arg-type]
        removed = obs.mass_before.volume_mm3 - obs.mass_after.volume_mm3
        needed = removed / float(section.value)  # type: ignore[arg-type]
        if observed < needed - tolerance:
            return _Outcome(
                False,
                observed,
                f"the cut removed {removed:.6g} mm3, at least {needed:.6g} mm of material over its section, but its "
                f"faces run only {observed:.6g} mm along its direction: material may be left where it should go "
                "through",
            )
    return _Outcome(True, observed)


def _cut_volume_bound(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """observed = volume removed by the op; it must be positive and at most the expected bound (a cut along an
    oblique normal has no exact analytic volume, but it can neither add material nor remove more than
    profile area x the extent of the body along the normal)."""
    if obs.mass_before is None:
        raise _UnobservableError("mass before the op is unknown")
    removed = obs.mass_before.volume_mm3 - obs.mass_after.volume_mm3
    bound = float(e.value)  # type: ignore[arg-type]
    # coarse sanity bound, not an oracle (the final mass is the real guard): "something was removed" must not be
    # forgiven by the relative tolerance, only the upper side gets it
    lower = check.thresholds.abs_mm3
    upper = bound + check.thresholds.abs_mm3 + check.thresholds.rel * bound
    return _Outcome(lower < removed <= upper, removed)


def _volume_sign(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """The direction the document volume moved: -1 must fall, +1 must rise, 0 must change either way. A shell has no
    other certain volume fact when its closed form is unknown, and the kernel's silent no-op leaves it unchanged."""
    if obs.mass_before is None:
        raise _UnobservableError("mass before the op is unknown")
    change = obs.mass_after.volume_mm3 - obs.mass_before.volume_mm3
    tolerance = check.thresholds.abs_mm3
    sign = int(e.value)  # type: ignore[arg-type]
    if sign < 0:
        ok, message = change < -tolerance, f"expected the volume to fall, observed {change:+.6g} mm3"
    elif sign > 0:
        ok, message = change > tolerance, f"expected the volume to rise, observed {change:+.6g} mm3"
    else:
        ok, message = abs(change) > tolerance, "the volume is unchanged: the op was a silent no-op"
    return _Outcome(ok, change, "" if ok else message)


def _draft_pull(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    return _Outcome(True, None)


def _draft_angle(check: Level2Check, e: Expectation, obs: Observation, by_name: dict[str, Expectation]) -> _Outcome:
    """Some created or modified PLANAR face leans by the drafted angle, with the sign the op promises: its outward
    normal has component sin(angle) along the companion ``draft_pull``, so ``asin(n . pull)`` equals the angle (within
    ``thresholds.angle_deg``). Only a face of the right sign counts: a face the draft merely trimmed may lean any way
    (an earlier draft pulled the other way, a chamfer), so a face that does not match proves nothing. A wrong angle, a
    wrong sense and a draft that changed nothing leave no matching face. Observed: the tilt closest to the angle."""
    if e.kind is CheckKind.reported:
        # a cone's semi-angle or an already tilted face: the planar faces seen around it (a cap, an undrafted wall) say
        # nothing about it, so no lean is offered as if they did
        raise _UnobservableError("not observable: the face observations do not describe this draft's angle")
    pull = by_name.get(DRAFT_PULL)
    if pull is None:
        raise _UnobservableError("draft_angle needs a draft_pull companion expectation")
    direction = formulas.normalise(pull.value)  # type: ignore[arg-type]
    wanted = float(e.value)  # type: ignore[arg-type]
    tilts = [
        math.degrees(math.asin(max(-1.0, min(1.0, formulas.dot(formulas.normalise(face.normal), direction)))))
        for face in (*obs.created_faces, *obs.modified_faces)
        if face.surface == SurfaceType.plane and face.normal is not None
    ]
    if not tilts:
        raise _UnobservableError("no created or modified planar face was observed after the draft")
    best = min(tilts, key=lambda tilt: abs(tilt - wanted))
    ok = abs(best - wanted) <= check.thresholds.angle_deg
    message = (
        ""
        if ok
        else f"expected a face leaning {wanted:g} deg towards the pull, the closest observed lean is {best:+.4g} deg"
    )
    return _Outcome(ok, best, message)


def _cut_direction(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    return _Outcome(True, None)


def _hole_thread_metadata(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """The cosmetic thread of a tapped hole: nothing observable in the geometry confirms it, so this row only
    reports what was requested (it is never asserted)."""
    return _Outcome(True, None)


def _instance_count(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    if obs.instance_count is None:
        raise _UnobservableError("adapter reported no instance_count")
    return check._scalar(e, obs.instance_count)


def _centroid_after(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    return check._vec(e, obs.mass_after.centroid)


def _component_bbox(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    if obs.component_bbox is None:
        raise _UnobservableError("component bbox not observed")
    return check._bbox(e, obs.component_bbox)


def _component_transform(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    if obs.component_transform is None:
        raise _UnobservableError("component transform not observed")
    expected = e.value
    observed = obs.component_transform
    if not isinstance(expected, Transform):
        return _Outcome(False, observed, "expected value is not a transform")
    ok = check.thresholds.close_position(expected.origin, observed.origin) and all(
        abs(a - b) <= 1e-6
        for ra, rb in zip(expected.rotation, observed.rotation, strict=True)
        for a, b in zip(ra, rb, strict=True)
    )
    return _Outcome(ok, observed)


def _interference_total(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    if obs.interference is None:
        raise _UnobservableError("interference not observed")
    return check._scalar(e, obs.interference.total_volume_mm3)


def _assembly_volume(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    return check._scalar(e, obs.mass_after.volume_mm3)


def _observed_frame(obs: Observation) -> SketchFrame:
    if obs.datum_frame is None:
        raise _UnobservableError("datum frame not observed (the adapter returned no DatumPlaneResult)")
    return obs.datum_frame


def _observed_axis(obs: Observation) -> Axis:
    if obs.datum_axis is None:
        raise _UnobservableError("datum axis not observed (the adapter returned no DatumAxisResult)")
    return obs.datum_axis


def _datum_origin(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """The realised datum plane's frame origin equals the requested one (position tolerance). Adapter-drift
    guard only: the requested pose comes from the executor, so this cannot catch a wrong executor pose."""
    return check._vec(e, _observed_frame(obs).origin)


def _datum_u(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """The realised frame's ``u`` axis points the requested way: sketches on the datum map (u, v) with it."""
    observed = _observed_frame(obs).u
    return _Outcome(check.thresholds.parallel(e.value, observed), observed)  # type: ignore[arg-type]


def _datum_normal(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """The realised datum plane's normal points the requested way (same sense: it fixes the offset side)."""
    observed = _observed_frame(obs).n
    return _Outcome(check.thresholds.parallel(e.value, observed), observed)  # type: ignore[arg-type]


def _datum_axis_position(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """The requested axis point lies on the realised axis (observed = closest point of the axis to it)."""
    closest = formulas.closest_point_on_axis(e.value, _observed_axis(obs))  # type: ignore[arg-type]
    return check._vec(e, closest)


def _datum_axis_direction(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """The realised axis points the requested way (same sense)."""
    observed = _observed_axis(obs).direction
    return _Outcome(check.thresholds.parallel(e.value, observed), observed)  # type: ignore[arg-type]


def _copy_mass(obs: Observation) -> MassProperties:
    if obs.copy_mass is None:
        raise _UnobservableError("the copy's body-scoped mass properties were not observed")
    return obs.copy_mass


def _copy_centroid(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """The copy's own centroid equals the source's centroid moved by the transform (position tolerance)."""
    return check._vec(e, _copy_mass(obs).centroid)


def _copy_surface(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """The copy's own surface area equals the source's (a rigid copy keeps its skin)."""
    surface = _copy_mass(obs).surface_mm2
    if surface is None:
        raise _UnobservableError("the adapter reported no surface area for the copy")
    return check._scalar(e, surface)


def _copy_bbox(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """The copy's own bounding box equals the source's box moved by the transform."""
    if obs.copy_bbox is None:
        raise _UnobservableError("the copy's body-scoped bounding box was not observed")
    return check._bbox(e, obs.copy_bbox)


def _source_centroid(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """The source did not move: its centroid after the op equals the one before (guards a copy done as a move)."""
    if obs.source_mass_after is None:
        raise _UnobservableError("the source's body-scoped mass properties were not observed after the copy")
    return check._vec(e, obs.source_mass_after.centroid)


def _split_pieces(obs: Observation, *, count: int | None = None) -> tuple[MassProperties, ...]:
    if obs.split_masses is None:
        raise _UnobservableError("the split pieces' body-scoped mass properties were not observed")
    if count is not None and len(obs.split_masses) != count:
        raise _UnobservableError(f"expected {count} split pieces, observed {len(obs.split_masses)}")
    return obs.split_masses


def _split_volume_sum(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """The two pieces add up to the source's volume (a cut loses nothing and invents nothing)."""
    pieces = _split_pieces(obs, count=2)
    return check._scalar(e, sum(piece.volume_mm3 for piece in pieces))


def _split_centroid(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """The volume-weighted centroid of the two pieces is the source's centroid."""
    pieces = _split_pieces(obs, count=2)
    total = sum(piece.volume_mm3 for piece in pieces)
    if total <= 0.0:
        raise _UnobservableError("the split pieces have no volume")
    weighted = tuple(sum(piece.volume_mm3 * piece.centroid[i] for piece in pieces) / total for i in range(3))
    return check._vec(e, weighted)  # type: ignore[arg-type]


def _split_sides(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """Every piece sits on the side of the parting plane it claims: its centroid (inside the piece, which is inside
    the half-space) is on that side of the plane, within the position tolerance."""
    pieces = _split_pieces(obs)
    if obs.split_plane is None or len(obs.split_sides) != len(pieces) or not pieces:
        raise _UnobservableError("the split pieces' sides or plane were not observed")
    normal = formulas.normalise(obs.split_plane.n)
    tolerance = check.thresholds.position_mm
    offsets = [formulas.dot(formulas.sub(piece.centroid, obs.split_plane.origin), normal) for piece in pieces]
    ok = all(
        offset >= -tolerance if side == "positive" else offset <= tolerance
        for side, offset in zip(obs.split_sides, offsets, strict=True)
    )
    message = (
        "" if ok else f"piece centroids sit {offsets} mm from the plane along its normal, claimed {obs.split_sides}"
    )
    return _Outcome(ok, ok, message)


def _flat_pattern(obs: Observation) -> FlatPattern:
    if obs.flat_pattern is None:
        msg = "the adapter reported no flat pattern"
        raise _UnobservableError(msg)
    return obs.flat_pattern


def _flat_length(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """The longer side of the flat pattern's bounding rectangle."""
    return check._scalar(e, _flat_pattern(obs).size_mm[0])


def _flat_width(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """The shorter side of the flat pattern's bounding rectangle."""
    return check._scalar(e, _flat_pattern(obs).size_mm[1])


def _flat_thickness(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """The thickness of the flat solid (its volume over its blank area) is the sheet's."""
    return check._scalar(e, _flat_pattern(obs).thickness_mm)


def _flat_valid(check: Level2Check, e: Expectation, obs: Observation, _: dict[str, Expectation]) -> _Outcome:
    """The flat pattern is one valid solid (1) or not (0)."""
    return check._scalar(e, 1 if _flat_pattern(obs).valid else 0)


_EVALUATORS: dict[str, Evaluator] = {
    "volume_delta": _volume_delta,
    "bbox_after": _bbox_after,
    "bbox_within": _bbox_within,
    "bbox_contains": _bbox_contains,
    "new_faces_count": _created_faces_count,
    "created_faces_count": _created_faces_count,
    "modified_faces_count": _modified_faces_count,
    "hole_axis_direction": _hole_axis_direction,
    "hole_axis_position": _hole_axis_position,
    "hole_count": _hole_count,
    "hole_radius": _hole_radius,
    "hole_entry_face_modified": _hole_entry_face_modified,
    "hole_within_face": _hole_within_face,
    "hole_thread_metadata": _hole_thread_metadata,
    "cylinder_axis_direction": _hole_axis_direction,
    "cylinder_axis_position": _hole_axis_position,
    "cylinder_radius": _hole_radius,
    "fillet_radius": _fillet_radius,
    "cut_extent_along_n": _cut_extent_along_n,
    CUT_DIRECTION: _cut_direction,
    CUT_AREA: _cut_direction,
    DRAFT_PULL: _draft_pull,
    "draft_angle": _draft_angle,
    "cut_volume_bound": _cut_volume_bound,
    "volume_sign": _volume_sign,
    "instance_count": _instance_count,
    "centroid_after": _centroid_after,
    "component_bbox": _component_bbox,
    "component_transform": _component_transform,
    "interference_total": _interference_total,
    "assembly_volume": _assembly_volume,
    "datum_origin": _datum_origin,
    "datum_u": _datum_u,
    "datum_normal": _datum_normal,
    "datum_axis_position": _datum_axis_position,
    "datum_axis_direction": _datum_axis_direction,
    "copy_centroid": _copy_centroid,
    "copy_surface": _copy_surface,
    "copy_bbox": _copy_bbox,
    "source_centroid": _source_centroid,
    "split_volume_sum": _split_volume_sum,
    "split_centroid": _split_centroid,
    "split_sides": _split_sides,
    "flat_length_mm": _flat_length,
    "flat_width_mm": _flat_width,
    "flat_thickness_mm": _flat_thickness,
    "flat_pattern_valid": _flat_valid,
}

CHECK_NAMES: frozenset[str] = frozenset(_EVALUATORS)
