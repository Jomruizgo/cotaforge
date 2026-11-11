"""Level 2 expectations of ``draft`` and the edit-tracking contract around it.

A draft tilts side faces about a neutral plane. The analytic volume (a tracked untouched box or cylinder, the pull along
a world axis / the axis) is asserted from closed forms measured on FreeCAD 1.0.2 and 1.1.3; the new ``draft_angle``
check wants an observed planar face leaning by the angle towards the pull. No volume sign is ever asserted, and a
pocket wall (or an edited or untracked body) is reported."""

import math

import pytest

from cotaforge.contract import (
    Axis,
    BodyHandle,
    BooleanKind,
    BooleanRequest,
    BoundingBox,
    BoxRequest,
    CylinderRequest,
    DraftRequest,
    EndCondition,
    FaceHandle,
    FaceInfo,
    HoleRequest,
    HoleStandard,
    MassProperties,
    MoveBodyRequest,
    PlaneName,
    Transform,
    frame_for_plane,
)
from cotaforge.ir.ops_features import DraftOperation, HoleOperation
from cotaforge.ir.ops_primitives import BooleanSubtractOperation, BoxOperation, CylinderOperation, TranslateOperation
from cotaforge.ir.refs import BodyRef, FaceRef, PlaneRef
from cotaforge.verification.expected import _NON_EDITING_OPS, DraftAngleError, ExpectedComputer
from cotaforge.verification.formulas import BoxShape
from cotaforge.verification.level2 import DRAFT_PULL, Level2Check
from cotaforge.verification.observe import Observation, StateSnapshot
from cotaforge.verification.results import CheckKind, CheckResult, Expectation
from cotaforge.verification.thresholds import Thresholds
from tests.unit.executor.stub import EMPTY_BBOX, EMPTY_MASS, StubAdapter

from .helpers import cylindrical_face, mass, planar_face

EMPTY = StateSnapshot(mass=EMPTY_MASS, bbox=EMPTY_BBOX)
BOX = (40.0, 30.0, 20.0)
BOX_VOLUME = 24000.0
BOX_BBOX = BoundingBox(min=(0, 0, 0), max=BOX)
DOC = StateSnapshot(mass=mass(BOX_VOLUME, (20, 15, 10)), bbox=BOX_BBOX)
UP = (0.0, 0.0, 1.0)
DOWN = (0.0, 0.0, -1.0)
SIDES = ("xmin", "xmax", "ymin", "ymax")


def _box_faces(
    stub: StubAdapter, size: tuple[float, float, float] = BOX, offset: tuple[float, float, float] = (0, 0, 0)
) -> None:
    """The six faces of a box with its min corner at ``offset``, ids ``face:xmin`` ... ``face:zmax``."""
    ex, ey, ez = size
    ox, oy, oz = offset
    spec = {
        "xmin": ((-1, 0, 0), (0, ey / 2, ez / 2), ey * ez, ((0, 0, 0), (0, ey, ez))),
        "xmax": ((1, 0, 0), (ex, ey / 2, ez / 2), ey * ez, ((ex, 0, 0), (ex, ey, ez))),
        "ymin": ((0, -1, 0), (ex / 2, 0, ez / 2), ex * ez, ((0, 0, 0), (ex, 0, ez))),
        "ymax": ((0, 1, 0), (ex / 2, ey, ez / 2), ex * ez, ((0, ey, 0), (ex, ey, ez))),
        "zmin": ((0, 0, -1), (ex / 2, ey / 2, 0), ex * ey, ((0, 0, 0), (ex, ey, 0))),
        "zmax": ((0, 0, 1), (ex / 2, ey / 2, ez), ex * ey, ((0, 0, ez), (ex, ey, ez))),
    }
    for name, (normal, center, area, (lo, hi)) in spec.items():
        stub.add_face(
            planar_face(
                f"face:{name}",
                normal,
                (center[0] + ox, center[1] + oy, center[2] + oz),
                area,
                BoundingBox(min=(lo[0] + ox, lo[1] + oy, lo[2] + oz), max=(hi[0] + ox, hi[1] + oy, hi[2] + oz)),
                "feature:1",
            )
        )


def _tracked_box(size: tuple[float, float, float] = BOX) -> tuple[ExpectedComputer, StubAdapter]:
    stub = StubAdapter()
    _box_faces(stub, size)
    computer = ExpectedComputer(Thresholds())
    computer.compute(BoxOperation(id="b", intent="i", size=size), BoxRequest(operation_id="b", size=size), EMPTY, stub)
    return computer, stub


def _draft(
    names: tuple[str, ...] = SIDES,
    angle: float = 3.0,
    *,
    pull: tuple[float, float, float] = UP,
    neutral_z: float = 0.0,
    neutral: PlaneRef | FaceRef | None = None,
) -> tuple[DraftOperation, DraftRequest]:
    op = DraftOperation(
        id="dr",
        intent="i",
        faces=tuple(FaceRef(from_op="b") for _ in names),
        neutral=neutral or PlaneRef(name="XY"),
        pull=pull,
        angle_deg=angle,
    )
    frame = frame_for_plane(PlaneName.XY)
    request = DraftRequest(
        operation_id="dr",
        faces=tuple(FaceHandle(id=f"face:{n}") for n in names),
        neutral=frame.model_copy(update={"origin": (0.0, 0.0, neutral_z)}),
        pull=pull,
        angle_deg=angle,
    )
    return op, request


def _rows(expectations: tuple[Expectation, ...]) -> dict[str, Expectation]:
    return {e.name: e for e in expectations}


def _compute(
    computer: ExpectedComputer,
    stub: StubAdapter,
    names: tuple[str, ...] = SIDES,
    angle: float = 3.0,
    before: StateSnapshot = DOC,
    **options: object,
) -> dict[str, Expectation]:
    op, request = _draft(names, angle, **options)  # type: ignore[arg-type]
    return _rows(computer.compute(op, request, before, stub))


# --- analytic volume of a tracked untouched box ------


def test_four_sides_of_a_box_drafted_from_the_bottom_have_the_measured_volume() -> None:
    """Measured on FreeCAD 1.0.2 and 1.1.3: 40 x 30 x 20, four sides, 3 deg, neutral at the bottom -> 22561.879."""
    computer, stub = _tracked_box()
    rows = _compute(computer, stub)
    assert rows["volume_delta"].kind is CheckKind.asserted
    assert rows["volume_delta"].value == pytest.approx(22561.879 - BOX_VOLUME, abs=1e-3)


def test_a_mid_height_pivot_gives_the_second_order_volume_measured_on_both_versions() -> None:
    """The linear terms cancel about the middle: V = abh + t^2 h^3 / 3 = 24007.3242."""
    computer, stub = _tracked_box()
    rows = _compute(computer, stub, neutral_z=10.0)
    assert rows["volume_delta"].kind is CheckKind.asserted
    assert rows["volume_delta"].value == pytest.approx(24007.3242 - BOX_VOLUME, abs=1e-3)


def test_pulling_down_from_a_neutral_plane_at_the_top_is_the_mirror_image_of_the_bottom_case() -> None:
    computer, stub = _tracked_box()
    rows = _compute(computer, stub, pull=DOWN, neutral_z=20.0)
    assert rows["volume_delta"].value == pytest.approx(22561.879 - BOX_VOLUME, abs=1e-3)


def test_the_closed_form_matches_a_numeric_integral_for_one_and_two_drafted_walls() -> None:
    t = math.tan(math.radians(5.0))

    def numeric(kx: int, ky: int) -> float:
        steps = 20000
        total = 0.0
        for i in range(steps):
            w = (i + 0.5) * 20.0 / steps
            total += (40.0 - kx * t * w) * (30.0 - ky * t * w) * (20.0 / steps)
        return total

    for names, kx, ky in ((("xmax",), 1, 0), (("xmax", "ymin"), 1, 1), (("xmin", "xmax"), 2, 0)):
        computer, stub = _tracked_box()
        rows = _compute(computer, stub, names, 5.0)
        assert rows["volume_delta"].kind is CheckKind.asserted
        assert rows["volume_delta"].value == pytest.approx(numeric(kx, ky) - BOX_VOLUME, abs=1e-2)


def test_a_single_wall_drafted_about_the_middle_changes_no_volume_and_that_is_exact() -> None:
    """Equal wedges are added and removed: delta 0 is the true value, so a "volume changed" check would be wrong."""
    computer, stub = _tracked_box()
    rows = _compute(computer, stub, ("xmax",), 4.0, neutral_z=10.0)
    assert rows["volume_delta"].kind is CheckKind.asserted
    assert rows["volume_delta"].value == pytest.approx(0.0, abs=1e-9)
    assert not any(name.startswith("volume_sign") for name in rows)


def test_a_moved_box_is_still_analytic_with_the_neutral_plane_where_the_request_puts_it() -> None:
    computer, stub = _tracked_box()
    stub.bbox_by_body["body:1"] = BOX_BBOX
    shift = (100.0, 0.0, 50.0)
    computer.compute(
        TranslateOperation(id="mv", intent="i", target=BodyRef(from_op="b"), offset=shift),
        MoveBodyRequest(operation_id="mv", body=BodyHandle(id="body:1"), transform=Transform(origin=shift)),
        DOC,
        stub,
    )
    assert isinstance(computer._shape_of("b"), BoxShape)
    op, request = _draft(neutral_z=50.0)  # the moved bottom
    moved = StubAdapter()
    _box_faces(moved, BOX, shift)
    rows = _rows(computer.compute(op, request, StateSnapshot(mass=DOC.mass, bbox=BOX_BBOX), moved))
    assert rows["volume_delta"].kind is CheckKind.asserted
    assert rows["volume_delta"].value == pytest.approx(22561.879 - BOX_VOLUME, abs=1e-3)


def test_a_datum_neutral_plane_does_not_hide_the_host_body() -> None:
    computer, stub = _tracked_box()
    rows = _compute(computer, stub, neutral=PlaneRef(from_op="pl"))
    assert rows["volume_delta"].kind is CheckKind.asserted


# --- an angle that collapses the primitive is refused before the adapter is called ------


def test_a_draft_that_narrows_a_wall_to_nothing_is_refused_with_the_dimension() -> None:
    """10 mm wide, two walls tilted 20 deg over 20 mm: 10 - 2 * 20 * tan(20) < 0."""
    computer, stub = _tracked_box((40.0, 10.0, 20.0))
    with pytest.raises(DraftAngleError, match=r"10 mm wide.*Y"):
        _compute(computer, stub, ("ymin", "ymax"), 20.0)


def test_the_same_angle_against_the_pull_is_fine() -> None:
    """With the neutral plane at the top the walls only grow towards the bottom."""
    computer, stub = _tracked_box((40.0, 10.0, 20.0))
    rows = _compute(computer, stub, ("ymin", "ymax"), 20.0, neutral_z=20.0)
    assert rows["volume_delta"].kind is CheckKind.asserted
    assert rows["volume_delta"].value > 0


# --- cylinders ------


def _tracked_cylinder(direction: tuple[float, float, float] = (0, 0, 1)) -> tuple[ExpectedComputer, StubAdapter]:
    """R 10, H 30: base at the origin, ids face:top (z = 30), face:bottom and face:side."""
    stub = StubAdapter()
    bbox = BoundingBox(min=(-10, -10, 0), max=(10, 10, 30))
    stub.add_face(planar_face("face:top", (0, 0, 1), (0, 0, 30), math.pi * 100, bbox, "feature:1"))
    stub.add_face(planar_face("face:bottom", (0, 0, -1), (0, 0, 0), math.pi * 100, bbox, "feature:1"))
    stub.add_face(cylindrical_face("face:side", Axis(origin=(0, 0, 15), direction=direction), 10.0, (0, 0, 15), bbox))
    computer = ExpectedComputer(Thresholds())
    computer.compute(
        CylinderOperation(id="b", intent="i", diameter_mm=20, height_mm=30),
        CylinderRequest(
            operation_id="b", diameter_mm=20, height_mm=30, axis=Axis(origin=(0, 0, 0), direction=direction)
        ),
        EMPTY,
        stub,
    )
    return computer, stub


CYL_VOLUME = math.pi * 100 * 30
CYL_DOC = StateSnapshot(
    mass=MassProperties(volume_mm3=CYL_VOLUME, centroid=(0, 0, 15)),
    bbox=BoundingBox(min=(-10, -10, 0), max=(10, 10, 30)),
)


def test_a_cylinder_drafted_into_a_cone_has_the_measured_volume() -> None:
    """Measured on both versions: R 10, H 30, 2 deg from the bottom -> pi (R^2 H - R t H^2 + t^2 H^3 / 3)."""
    computer, stub = _tracked_cylinder()
    op, request = _draft(("side",), 2.0)
    rows = _rows(computer.compute(op, request, CYL_DOC, stub))
    assert rows["volume_delta"].kind is CheckKind.asserted
    assert rows["volume_delta"].value == pytest.approx(8471.8959 - CYL_VOLUME, abs=1e-3)
    # a cone's semi-angle is not in the face descriptor yet: the angle is reported, not asserted
    assert rows["draft_angle"].kind is CheckKind.reported


def test_a_cylinder_cap_is_not_a_side_face_so_nothing_analytic_is_claimed() -> None:
    computer, stub = _tracked_cylinder()
    op, request = _draft(("top",), 2.0)
    rows = _rows(computer.compute(op, request, CYL_DOC, stub))
    assert rows["volume_delta"].kind is CheckKind.reported


def test_a_cone_that_closes_before_the_far_end_is_refused() -> None:
    computer, stub = _tracked_cylinder()
    op, request = _draft(("side",), 30.0)  # radius 10, 30 deg over 30 mm: 10 - 17.3 < 0
    with pytest.raises(DraftAngleError, match="radius"):
        computer.compute(op, request, CYL_DOC, stub)


# --- what is only reported, never guessed ------


def test_a_face_perpendicular_to_the_pull_is_not_modelled_and_gets_no_drafted_angle_assertion() -> None:
    computer, stub = _tracked_box()
    rows = _compute(computer, stub, ("zmax",))
    assert rows["volume_delta"].kind is CheckKind.reported
    assert rows["draft_angle"].kind is CheckKind.reported


def test_a_pull_off_the_world_axes_is_reported() -> None:
    computer, stub = _tracked_box()
    tilt = (math.sin(math.radians(30)), 0.0, math.cos(math.radians(30)))
    rows = _compute(computer, stub, pull=tilt)
    assert rows["volume_delta"].kind is CheckKind.reported


def test_a_face_that_is_not_a_whole_face_of_the_tracked_box_is_reported() -> None:
    computer, stub = _tracked_box()
    stub.add_face(
        planar_face("face:part", (1, 0, 0), (40, 15, 5), 100.0, BoundingBox(min=(40, 10, 0), max=(40, 20, 10)))
    )
    op = DraftOperation(
        id="dr",
        intent="i",
        faces=(FaceRef(from_op="b"),),
        neutral=PlaneRef(name="XY"),
        pull=UP,
        angle_deg=3.0,
    )
    _, request = _draft(("part",))
    rows = _rows(computer.compute(op, request, DOC, stub))
    assert rows["volume_delta"].kind is CheckKind.reported
    assert rows["draft_angle"].kind is CheckKind.asserted  # still a plane lying along the pull


def _hole_on_top(computer: ExpectedComputer, stub: StubAdapter) -> None:
    op = HoleOperation(
        id="bore",
        intent="i",
        face=FaceRef(from_op="b"),
        position=(0.0, 0.0),
        standard=HoleStandard.simple,
        diameter_mm=4.0,
        end_condition=EndCondition.through_all,
    )
    request = HoleRequest(
        operation_id="bore",
        face=FaceHandle(id="face:zmax"),
        position=(0.0, 0.0),
        standard=HoleStandard.simple,
        diameter_mm=4.0,
        end_condition=EndCondition.through_all,
    )
    computer.compute(op, request, DOC, stub)


def test_a_body_with_a_pocket_or_hole_is_not_drafted_analytically() -> None:
    computer, stub = _tracked_box()
    _hole_on_top(computer, stub)
    rows = _compute(computer, stub)
    assert rows["volume_delta"].kind is CheckKind.reported
    assert "pocket" in rows["volume_delta"].note
    assert rows["draft_angle"].kind is CheckKind.asserted  # the angle of a planar wall is still checkable


def test_faces_picked_from_a_live_selection_cannot_be_traced_to_a_tracked_body() -> None:
    computer, stub = _tracked_box()
    op = DraftOperation(
        id="dr",
        intent="i",
        faces=(FaceRef(from_selection="doc|body|Face1"),),
        neutral=PlaneRef(name="XY"),
        pull=UP,
        angle_deg=3.0,
    )
    _, request = _draft(("xmax",))
    rows = _rows(computer.compute(op, request, DOC, stub))
    assert rows["volume_delta"].kind is CheckKind.reported


def test_a_second_draft_of_the_same_body_is_reported_because_the_first_one_forgot_the_shape() -> None:
    computer, stub = _tracked_box()
    assert _compute(computer, stub)["volume_delta"].kind is CheckKind.asserted
    assert computer._shape_of("b") is None
    op, request = _draft(("xmax",), 1.0)
    op = op.model_copy(update={"id": "dr2"})
    rows = _rows(computer.compute(op, request.model_copy(update={"operation_id": "dr2"}), DOC, stub))
    assert rows["volume_delta"].kind is CheckKind.reported


def test_draft_is_recorded_as_an_edit_so_it_is_not_in_the_non_editing_allowlist() -> None:
    assert "draft" not in _NON_EDITING_OPS


def test_a_boolean_after_a_draft_is_no_longer_analytic() -> None:
    computer, stub = _tracked_box()
    _compute(computer, stub)
    assert not isinstance(computer._shape_of("b"), BoxShape)
    stub.mass_by_body["body:1"] = mass(22561.879)
    stub.bbox_by_body["body:1"] = BOX_BBOX
    stub.mass_by_body["body:2"] = mass(1000.0)
    stub.bbox_by_body["body:2"] = BoundingBox(min=(0, 0, 0), max=(10, 10, 10))
    computer.compute(
        BoxOperation(id="tool", intent="i", size=(10, 10, 10)),
        BoxRequest(operation_id="tool", size=(10, 10, 10)),
        DOC,
        stub,
    )
    op = BooleanSubtractOperation(id="cut", intent="i", target=BodyRef(from_op="b"), tools=(BodyRef(from_op="tool"),))
    request = BooleanRequest(
        operation_id="cut", kind=BooleanKind.subtract, target=BodyHandle(id="body:1"), tools=(BodyHandle(id="body:2"),)
    )
    rows = _rows(computer.compute(op, request, DOC, stub))
    assert rows["volume_delta"].kind is CheckKind.reported


def test_no_volume_sign_or_box_is_ever_asserted_for_a_draft() -> None:
    computer, stub = _tracked_box()
    rows = _compute(computer, stub)
    assert set(rows) == {DRAFT_PULL, "draft_angle", "volume_delta"}
    assert rows[DRAFT_PULL].kind is CheckKind.reported
    assert rows[DRAFT_PULL].value == UP


# --- the draft_angle evaluator ------


def _leaning(tilt_deg: float, face_id: str = "face:lean") -> FaceInfo:
    """A planar face whose outward normal leans ``tilt_deg`` towards +Z from the horizontal."""
    t = math.radians(tilt_deg)
    return planar_face(face_id, (math.cos(t), 0.0, math.sin(t)), (40, 15, 10), 600.0, BOX_BBOX)


def _observation(created: tuple[FaceInfo, ...] = (), modified: tuple[FaceInfo, ...] = ()) -> Observation:
    return Observation(
        mass_after=MassProperties(volume_mm3=1.0, centroid=(0, 0, 0)),
        bbox_after=BOX_BBOX,
        created_faces=created,
        modified_faces=modified,
        created_edges=(),
        instance_count=None,
        component_bbox=None,
        component_transform=None,
        interference=None,
        mass_before=MassProperties(volume_mm3=1.0, centroid=(0, 0, 0)),
    )


def _angle_result(observation: Observation, angle: float = 3.0, pull: tuple[float, float, float] = UP) -> CheckResult:
    rows = (
        Expectation(name=DRAFT_PULL, kind=CheckKind.reported, unit="vec", value=pull),
        Expectation(name="draft_angle", kind=CheckKind.asserted, unit="deg", value=angle),
    )
    return Level2Check().check(rows, observation)[1]


def test_a_face_leaning_by_the_angle_towards_the_pull_passes() -> None:
    result = _angle_result(_observation(created=(_leaning(3.0),)))
    assert result.ok is True
    assert result.observed == pytest.approx(3.0)


def test_undrafted_vertical_faces_and_the_caps_do_not_break_a_correct_draft() -> None:
    vertical = _leaning(0.0, "face:vertical")
    cap = planar_face("face:cap", UP, (20, 15, 20), 1200.0, BOX_BBOX)
    result = _angle_result(_observation(created=(_leaning(3.0),), modified=(vertical, cap)))
    assert result.ok is True


def test_a_face_leaning_the_wrong_way_fails() -> None:
    result = _angle_result(_observation(created=(_leaning(-3.0),)))
    assert result.ok is False
    assert "leaning 3 deg" in result.message


def test_a_wrong_angle_fails_and_the_tolerance_is_the_angle_threshold() -> None:
    assert _angle_result(_observation(created=(_leaning(5.0),))).ok is False
    assert _angle_result(_observation(created=(_leaning(3.4),))).ok is True
    assert _angle_result(_observation(created=(_leaning(3.6),))).ok is False


def test_a_pull_pointing_down_reads_the_lean_against_down() -> None:
    assert _angle_result(_observation(created=(_leaning(-3.0),)), pull=DOWN).ok is True
    assert _angle_result(_observation(created=(_leaning(3.0),)), pull=DOWN).ok is False


def test_a_draft_that_changed_nothing_has_no_face_to_judge_and_fails() -> None:
    result = _angle_result(_observation())
    assert result.ok is False
    assert "no created or modified planar face" in result.message


def test_a_non_planar_face_is_never_a_candidate() -> None:
    cone = cylindrical_face("face:cone", Axis(origin=(0, 0, 0), direction=UP), 10.0, (0, 0, 15), BOX_BBOX)
    assert _angle_result(_observation(modified=(cone,))).ok is False


def test_a_reported_draft_angle_observes_nothing_instead_of_a_misleading_face() -> None:
    """A cone (or an already tilted face) is reported: the planar faces seen around it (a cap at 90 deg, an undrafted
    wall at 0) say nothing about its angle, so the evaluator must not offer one."""
    rows = (
        Expectation(name=DRAFT_PULL, kind=CheckKind.reported, unit="vec", value=UP),
        Expectation(name="draft_angle", kind=CheckKind.reported, unit="deg", value=2.0),
    )
    cap = planar_face("face:cap", UP, (0, 0, 30), 300.0, BOX_BBOX)
    wall = _leaning(0.0, "face:wall")
    result = Level2Check().check(rows, _observation(created=(cap,), modified=(wall,)))[1]
    assert result.ok is None and result.observed is None
    assert "not observable" in result.message or "not described" in result.message


def test_a_mix_of_checkable_and_tilted_faces_says_the_tilted_ones_are_unchecked() -> None:
    computer, stub = _tracked_box()
    stub.add_face(planar_face("face:slope", (0.8, 0, 0.6), (40, 15, 10), 600.0, BOX_BBOX))
    op = DraftOperation(
        id="dr",
        intent="i",
        faces=(FaceRef(from_op="b"), FaceRef(from_op="b")),
        neutral=PlaneRef(name="XY"),
        pull=UP,
        angle_deg=3.0,
    )
    _, request = _draft(("xmax", "slope"))
    rows = _rows(computer.compute(op, request, DOC, stub))
    assert rows["draft_angle"].kind is CheckKind.asserted  # the wall along the pull is still checkable
    assert "unchecked" in rows["draft_angle"].note and "tilted" in rows["draft_angle"].note


def test_faces_all_along_the_pull_carry_no_unchecked_remark() -> None:
    computer, stub = _tracked_box()
    assert "unchecked" not in _compute(computer, stub)["draft_angle"].note
