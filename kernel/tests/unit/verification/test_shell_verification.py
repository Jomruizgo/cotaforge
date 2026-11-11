"""Level 2 expectations of ``shell`` and the edit-tracking contract around it.

``Thickness`` builds the walls only: inward the cavity is the offset of the body (the outer size is kept), outward the
ORIGINAL body becomes the cavity and the walls grow outside it. So the volume always falls for an inward shell, but
for an outward one it falls on an ordinary part and RISES on a thin one: only "it changed" is certain there. The
analytic volume (a tracked untouched box or cylinder) is asserted; everything else is reported, never guessed."""

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
    EndCondition,
    FaceHandle,
    HoleRequest,
    HoleStandard,
    MassProperties,
    ShellRequest,
)
from cotaforge.ir.ops_features import HoleOperation, ShellOperation
from cotaforge.ir.ops_primitives import BooleanSubtractOperation, BoxOperation, CylinderOperation
from cotaforge.ir.refs import BodyRef, FaceRef
from cotaforge.verification.expected import _NON_EDITING_OPS, ExpectedComputer, ShellThicknessError
from cotaforge.verification.formulas import BoxShape
from cotaforge.verification.level2 import Level2Check
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


def _box_faces(stub: StubAdapter, size: tuple[float, float, float] = BOX) -> None:
    """The six faces of a box at the origin, ids ``face:xmin`` ... ``face:zmax``."""
    ex, ey, ez = size
    spec = {
        "xmin": ((-1, 0, 0), (0, ey / 2, ez / 2), ey * ez, ((0, 0, 0), (0, ey, ez))),
        "xmax": ((1, 0, 0), (ex, ey / 2, ez / 2), ey * ez, ((ex, 0, 0), (ex, ey, ez))),
        "ymin": ((0, -1, 0), (ex / 2, 0, ez / 2), ex * ez, ((0, 0, 0), (ex, 0, ez))),
        "ymax": ((0, 1, 0), (ex / 2, ey, ez / 2), ex * ez, ((0, ey, 0), (ex, ey, ez))),
        "zmin": ((0, 0, -1), (ex / 2, ey / 2, 0), ex * ey, ((0, 0, 0), (ex, ey, 0))),
        "zmax": ((0, 0, 1), (ex / 2, ey / 2, ez), ex * ey, ((0, 0, ez), (ex, ey, ez))),
    }
    for name, (normal, center, area, (lo, hi)) in spec.items():
        stub.add_face(planar_face(f"face:{name}", normal, center, area, BoundingBox(min=lo, max=hi), "feature:1"))


def _tracked_box(size: tuple[float, float, float] = BOX) -> tuple[ExpectedComputer, StubAdapter]:
    stub = StubAdapter()
    _box_faces(stub, size)
    computer = ExpectedComputer(Thresholds())
    computer.compute(BoxOperation(id="b", intent="i", size=size), BoxRequest(operation_id="b", size=size), EMPTY, stub)
    return computer, stub


def _shell(
    names: tuple[str, ...] = ("zmax",), t: float = 2.0, *, outward: bool = False, join: str = "intersection"
) -> tuple[ShellOperation, ShellRequest]:
    op = ShellOperation(
        id="sh",
        intent="i",
        faces=tuple(FaceRef(from_op="b") for _ in names),
        thickness_mm=t,
        direction="outward" if outward else "inward",
        join=join,
    )
    request = ShellRequest(
        operation_id="sh",
        faces=tuple(FaceHandle(id=f"face:{n}") for n in names),
        thickness_mm=t,
        outward=outward,
        join=join,
    )
    return op, request


def _rows(expectations: tuple[Expectation, ...]) -> dict[str, Expectation]:
    return {e.name: e for e in expectations}


def _compute(
    computer: ExpectedComputer,
    stub: StubAdapter,
    names: tuple[str, ...] = ("zmax",),
    t: float = 2.0,
    before: StateSnapshot = DOC,
    **options: object,
) -> dict[str, Expectation]:
    op, request = _shell(names, t, **options)  # type: ignore[arg-type]
    return _rows(computer.compute(op, request, before, stub))


# --- analytic volume of a tracked untouched box ------


@pytest.mark.parametrize(
    ("names", "t", "after"),
    [
        (("zmax",), 2.0, 7152.0),  # cavity 36 x 26 x 18
        (("zmax", "zmin"), 2.0, 5280.0),  # cavity 36 x 26 x 20
        (("zmax", "xmax"), 2.0, 6216.0),  # cavity 38 x 26 x 18
        (("zmax",), 5.0, 24000.0 - 30 * 20 * 15),  # cavity 30 x 20 x 15
    ],
)
def test_an_inward_shell_of_a_tracked_box_has_an_exact_volume_delta(
    names: tuple[str, ...], t: float, after: float
) -> None:
    computer, stub = _tracked_box()
    rows = _compute(computer, stub, names, t)
    delta = rows["volume_delta"]
    assert delta.kind is CheckKind.asserted
    assert delta.value == pytest.approx(after - BOX_VOLUME)


def test_an_inward_shell_asserts_that_the_volume_falls_and_the_box_stays_inside_the_old_one() -> None:
    computer, stub = _tracked_box()
    rows = _compute(computer, stub)
    assert rows["volume_sign"].kind is CheckKind.asserted and rows["volume_sign"].value == -1
    assert rows["bbox_within"].kind is CheckKind.asserted and rows["bbox_within"].value == BOX_BBOX
    assert "new_faces_count" not in rows and "created_faces_count" not in rows


def test_an_outward_shell_of_a_tracked_box_has_the_skin_volume_and_the_grown_box() -> None:
    """Measured on FreeCAD 1.0.2 and 1.1.3: 40 x 30 x 20 with the top removed, 2 mm outward -> V 8912 (the skin
    alone), box (-2, -2, -2) .. (42, 32, 20)."""
    computer, stub = _tracked_box()
    rows = _compute(computer, stub, outward=True)
    assert rows["volume_delta"].kind is CheckKind.asserted
    assert rows["volume_delta"].value == pytest.approx(8912.0 - BOX_VOLUME)
    assert rows["bbox_after"].kind is CheckKind.asserted
    assert rows["bbox_after"].value == BoundingBox(min=(-2, -2, -2), max=(42, 32, 20))
    assert rows["volume_sign"].value == 0  # outward: no sign is certain
    assert rows["volume_sign"].kind is CheckKind.reported


def test_an_outward_shell_of_a_thin_part_can_raise_the_volume_and_its_sign_is_only_reported() -> None:
    """10 x 10 x 2 with the top removed, 5 mm outward: V 2600 against 200 before (measured)."""
    computer, stub = _tracked_box((10.0, 10.0, 2.0))
    before = StateSnapshot(mass=mass(200.0), bbox=BoundingBox(min=(0, 0, 0), max=(10, 10, 2)))
    rows = _compute(computer, stub, t=5.0, before=before, outward=True)
    assert rows["volume_delta"].value == pytest.approx(2400.0)
    assert rows["volume_sign"].kind is CheckKind.reported and rows["volume_sign"].value == 0


def test_the_arc_join_has_no_closed_form_so_its_volume_is_only_reported() -> None:
    computer, stub = _tracked_box()
    rows = _compute(computer, stub, join="arc")
    assert rows["volume_delta"].kind is CheckKind.reported
    assert rows["volume_sign"].kind is CheckKind.asserted  # an inward shell still removes material
    assert "arc" in rows["volume_delta"].note


# --- a wall that cannot fit is refused before the adapter is called ------


@pytest.mark.parametrize("t", [15.0, 16.0])
def test_a_wall_that_leaves_no_cavity_in_a_tracked_box_is_refused(t: float) -> None:
    """Y is 30 mm with two walls: t >= 15 leaves no cavity. The kernel gives either a silent no-op or invalid
    geometry there, so the plan is refused with the dimension named."""
    computer, stub = _tracked_box()
    with pytest.raises(ShellThicknessError, match=r"Y.*30"):
        _compute(computer, stub, t=t)


def test_a_wall_as_thick_as_the_height_with_one_wall_left_is_refused() -> None:
    computer, stub = _tracked_box((40.0, 30.0, 4.0))
    with pytest.raises(ShellThicknessError, match=r"Z.*4"):
        _compute(computer, stub, t=4.0)  # one bottom wall of 4 mm in a 4 mm part


def test_an_outward_shell_never_runs_out_of_room() -> None:
    computer, stub = _tracked_box()
    rows = _compute(computer, stub, t=100.0, outward=True)
    assert rows["volume_delta"].kind is CheckKind.asserted


# --- cylinders ------


def _tracked_cylinder(direction: tuple[float, float, float] = (0, 0, 1)) -> tuple[ExpectedComputer, StubAdapter]:
    """R 20, H 30: base at the origin, ids face:top (z = 30), face:bottom and face:side."""
    stub = StubAdapter()
    bbox = BoundingBox(min=(-20, -20, 0), max=(20, 20, 30))
    stub.add_face(planar_face("face:top", (0, 0, 1), (0, 0, 30), math.pi * 400, bbox, "feature:1"))
    stub.add_face(planar_face("face:bottom", (0, 0, -1), (0, 0, 0), math.pi * 400, bbox, "feature:1"))
    stub.add_face(cylindrical_face("face:side", Axis(origin=(0, 0, 15), direction=(0, 0, 1)), 20.0, (0, 0, 15), bbox))
    computer = ExpectedComputer(Thresholds())
    computer.compute(
        CylinderOperation(id="b", intent="i", diameter_mm=40, height_mm=30),
        CylinderRequest(
            operation_id="b", diameter_mm=40, height_mm=30, axis=Axis(origin=(0, 0, 0), direction=direction)
        ),
        EMPTY,
        stub,
    )
    return computer, stub


CYL_VOLUME = math.pi * 400 * 30
CYL_DOC = StateSnapshot(
    mass=MassProperties(volume_mm3=CYL_VOLUME, centroid=(0, 0, 15)),
    bbox=BoundingBox(min=(-20, -20, 0), max=(20, 20, 30)),
)


@pytest.mark.parametrize(
    ("names", "after"),
    [
        (("top",), math.pi * (400 * 30 - 18**2 * 28)),  # measured 9198.5833
        (("top", "bottom"), math.pi * (400 - 18**2) * 30),  # measured 7162.8313
    ],
)
def test_an_inward_shell_of_a_tracked_cylinder_has_an_exact_volume_delta(names: tuple[str, ...], after: float) -> None:
    computer, stub = _tracked_cylinder()
    op = ShellOperation(id="sh", intent="i", faces=tuple(FaceRef(from_op="b") for _ in names), thickness_mm=2.0)
    request = ShellRequest(operation_id="sh", faces=tuple(FaceHandle(id=f"face:{n}") for n in names), thickness_mm=2.0)
    rows = _rows(computer.compute(op, request, CYL_DOC, stub))
    assert rows["volume_delta"].kind is CheckKind.asserted
    assert rows["volume_delta"].value == pytest.approx(after - CYL_VOLUME)
    assert rows["volume_sign"].value == -1


def test_an_outward_shell_of_a_tracked_cylinder_has_the_skin_volume() -> None:
    computer, stub = _tracked_cylinder()
    op = ShellOperation(id="sh", intent="i", faces=(FaceRef(from_op="b"),), thickness_mm=2.0, direction="outward")
    request = ShellRequest(operation_id="sh", faces=(FaceHandle(id="face:top"),), thickness_mm=2.0, outward=True)
    rows = _rows(computer.compute(op, request, CYL_DOC, stub))
    assert rows["volume_delta"].kind is CheckKind.asserted
    assert rows["volume_delta"].value == pytest.approx(math.pi * (22**2 * 32 - 20**2 * 30) - CYL_VOLUME)  # 10957.8752


def test_removing_the_side_of_a_cylinder_has_no_closed_form_and_is_reported() -> None:
    computer, stub = _tracked_cylinder()
    op = ShellOperation(id="sh", intent="i", faces=(FaceRef(from_op="b"),), thickness_mm=2.0)
    request = ShellRequest(operation_id="sh", faces=(FaceHandle(id="face:side"),), thickness_mm=2.0)
    rows = _rows(computer.compute(op, request, CYL_DOC, stub))
    assert rows["volume_delta"].kind is CheckKind.reported
    assert rows["volume_sign"].kind is CheckKind.asserted


def test_a_wall_thicker_than_the_cylinder_radius_is_refused() -> None:
    computer, stub = _tracked_cylinder()
    op = ShellOperation(id="sh", intent="i", faces=(FaceRef(from_op="b"),), thickness_mm=20.0)
    request = ShellRequest(operation_id="sh", faces=(FaceHandle(id="face:top"),), thickness_mm=20.0)
    with pytest.raises(ShellThicknessError, match="radius"):
        computer.compute(op, request, CYL_DOC, stub)


# --- never assert a volume the tracker cannot vouch for ------


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


def test_a_box_a_hole_already_edited_is_not_shelled_analytically() -> None:
    computer, stub = _tracked_box()
    _hole_on_top(computer, stub)
    rows = _compute(computer, stub)
    assert rows["volume_delta"].kind is CheckKind.reported
    assert "volume_sign" in rows and rows["volume_sign"].kind is CheckKind.asserted


def test_faces_picked_from_a_live_selection_cannot_be_traced_to_a_tracked_body() -> None:
    computer, stub = _tracked_box()
    op = ShellOperation(id="sh", intent="i", faces=(FaceRef(from_selection="doc|body|Face6"),), thickness_mm=2.0)
    request = ShellRequest(operation_id="sh", faces=(FaceHandle(id="face:zmax"),), thickness_mm=2.0)
    rows = _rows(computer.compute(op, request, DOC, stub))
    assert rows["volume_delta"].kind is CheckKind.reported


def test_a_face_that_is_not_a_face_of_the_tracked_box_is_not_shelled_analytically() -> None:
    """The observed face says z = 18 while the tracked box ends at z = 20: the shapes disagree, so report."""
    computer, stub = _tracked_box()
    stub.add_face(
        planar_face("face:lower", (0, 0, 1), (20, 15, 18), 1200.0, BoundingBox(min=(0, 0, 18), max=(40, 30, 18)))
    )
    op = ShellOperation(id="sh", intent="i", faces=(FaceRef(from_op="b"),), thickness_mm=2.0)
    request = ShellRequest(operation_id="sh", faces=(FaceHandle(id="face:lower"),), thickness_mm=2.0)
    rows = _rows(computer.compute(op, request, DOC, stub))
    assert rows["volume_delta"].kind is CheckKind.reported


def test_a_second_shell_of_the_same_body_is_reported_because_the_first_one_forgot_the_shape() -> None:
    computer, stub = _tracked_box()
    assert _compute(computer, stub)["volume_delta"].kind is CheckKind.asserted
    assert computer._shape_of("b") is None
    op = ShellOperation(id="sh2", intent="i", faces=(FaceRef(from_op="b"),), thickness_mm=1.0)
    request = ShellRequest(operation_id="sh2", faces=(FaceHandle(id="face:zmin"),), thickness_mm=1.0)
    rows = _rows(computer.compute(op, request, DOC, stub))
    assert rows["volume_delta"].kind is CheckKind.reported


def test_shell_is_recorded_as_an_edit_so_it_is_not_in_the_non_editing_allowlist() -> None:
    assert "shell" not in _NON_EDITING_OPS


def test_a_boolean_after_a_shell_is_no_longer_analytic() -> None:
    """The shell hollowed the box, so the primitive overlap with a tool is wrong: the union/subtract goes reported."""
    computer, stub = _tracked_box()
    _compute(computer, stub)
    assert not isinstance(computer._shape_of("b"), BoxShape)
    stub.mass_by_body["body:1"] = mass(7152.0)
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


# --- the volume_sign evaluator ------


def _observation(before: float, after: float) -> Observation:
    return Observation(
        mass_after=MassProperties(volume_mm3=after, centroid=(0, 0, 0)),
        bbox_after=BOX_BBOX,
        created_faces=(),
        modified_faces=(),
        created_edges=(),
        instance_count=None,
        component_bbox=None,
        component_transform=None,
        interference=None,
        mass_before=MassProperties(volume_mm3=before, centroid=(0, 0, 0)),
    )


def _sign_result(value: int, before: float, after: float) -> CheckResult:
    expectation = Expectation(name="volume_sign", kind=CheckKind.asserted, unit="count", value=value)
    (result,) = Level2Check().check((expectation,), _observation(before, after))
    return result


@pytest.mark.parametrize(
    ("before", "after", "ok"), [(24000.0, 7152.0, True), (24000.0, 24000.0, False), (24000.0, 25000.0, False)]
)
def test_volume_sign_minus_one_needs_a_fall(before: float, after: float, ok: bool) -> None:
    assert _sign_result(-1, before, after).ok is ok


@pytest.mark.parametrize(
    ("before", "after", "ok"), [(200.0, 2600.0, True), (24000.0, 8912.0, True), (24000.0, 24000.0, False)]
)
def test_volume_sign_zero_needs_any_change_and_catches_a_silent_no_op(before: float, after: float, ok: bool) -> None:
    result = _sign_result(0, before, after)
    assert result.ok is ok
    if not ok:
        assert "unchanged" in result.message


def test_a_silent_no_op_inward_shell_is_caught_by_the_sign_and_the_volume() -> None:
    """The kernel can leave the shape unchanged (empty face list, a wall too thick) and report success."""
    computer, stub = _tracked_box()
    rows = _compute(computer, stub)
    results = Level2Check().check(tuple(rows.values()), _observation(BOX_VOLUME, BOX_VOLUME))
    failed = {r.name for r in results if r.ok is False}
    assert {"volume_sign", "volume_delta"} <= failed
