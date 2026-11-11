"""A pattern (or a mirror) instance is a promise about the HOST: a cut instance removes its volume only where there is
material, a boss instance adds its own only where no earlier feature already stands. The expectations know the host's
outline (the base pad's profile) and the regions of the features recorded before, so an instance that lands in a void or
on an earlier feature is REPORTED (volume and face count), never asserted; ``instance_count`` stays certain."""

import math

import pytest

from cotaforge.contract import (
    Axis,
    BoundingBox,
    CircularPatternRequest,
    ExtrudeRequest,
    FeatureHandle,
    LinearPatternRequest,
    PlaneName,
    SketchHandle,
    SketchRequest,
)
from cotaforge.ir.ops_features import (
    CircularPatternOperation,
    ExtrudeOperation,
    LinearPatternOperation,
    RevolveOperation,
    SketchOperation,
)
from cotaforge.ir.refs import PlaneRef
from cotaforge.verification.expected import ExpectedComputer
from cotaforge.verification.observe import StateSnapshot
from cotaforge.verification.results import CheckKind
from cotaforge.verification.thresholds import Thresholds
from tests.unit.executor.stub import EMPTY_BBOX, EMPTY_MASS, StubAdapter

from .helpers import computer_with_pad, cylindrical_face, mass

EMPTY = StateSnapshot(mass=EMPTY_MASS, bbox=EMPTY_BBOX)
HOLE_VOLUME = math.pi * 9.0 * 8.0  # d6 through 8
PLATE_BBOX = BoundingBox(min=(0.0, 0.0, 0.0), max=(100.0, 60.0, 8.0))


def _by_name(expectations: tuple) -> dict:
    return {e.name: e for e in expectations}


def _computer_with_pad(entities: list[dict], _bbox: BoundingBox, depth: float = 8.0) -> ExpectedComputer:
    return computer_with_pad(entities, depth)


def _hole_stub(center: tuple[float, float], radius: float = 3.0, depth: float = 8.0) -> StubAdapter:
    stub = StubAdapter()
    stub.add_face(
        cylindrical_face(
            "face:cyl",
            Axis(origin=(center[0], center[1], depth), direction=(0, 0, -1)),
            radius,
            (center[0], center[1], depth / 2),
            BoundingBox(
                min=(center[0] - radius, center[1] - radius, 0.0), max=(center[0] + radius, center[1] + radius, depth)
            ),
            created_by="feature:2",
        ),
        feature_id="feature:2",
    )
    return stub


def _row(spacing: float, count: int = 2) -> tuple[LinearPatternOperation, LinearPatternRequest]:
    op = LinearPatternOperation(
        id="row", intent="i", features=["hole_1"], direction="X", spacing_mm=spacing, count=count
    )
    request = LinearPatternRequest(
        operation_id="row",
        features=(FeatureHandle(id="feature:2"),),
        direction=(1, 0, 0),
        spacing_mm=spacing,
        count=count,
    )
    return op, request


def _before(bbox: BoundingBox) -> StateSnapshot:
    return StateSnapshot(mass=mass(1.0), bbox=bbox, seed_volume_delta=-HOLE_VOLUME)


RECT = {"kind": "rect", "name": "outline", "center": [50, 30], "width": 100, "height": 60}
# an L: 60 x 40 with legs 10 thick, the void being x > 10, y > 10
L_SHAPE = [
    {"kind": "line", "name": f"l{i}", "start": a, "end": b}
    for i, (a, b) in enumerate(
        [
            ((0, 0), (60, 0)),
            ((60, 0), (60, 10)),
            ((60, 10), (10, 10)),
            ((10, 10), (10, 40)),
            ((10, 40), (0, 40)),
            ((0, 40), (0, 0)),
        ]
    )
]
L_BBOX = BoundingBox(min=(0.0, 0.0, 0.0), max=(60.0, 40.0, 8.0))
DISC = {"kind": "circle", "name": "outline", "center": [0, 0], "diameter": 120}
DISC_BBOX = BoundingBox(min=(-60.0, -60.0, 0.0), max=(60.0, 60.0, 8.0))


def test_a_cut_row_inside_a_rectangular_pad_stays_asserted() -> None:
    computer = _computer_with_pad([RECT], PLATE_BBOX)
    op, request = _row(30.0, 3)
    by = _by_name(computer.compute(op, request, _before(PLATE_BBOX), _hole_stub((10.0, 30.0))))
    assert all(
        by[name].kind is CheckKind.asserted for name in ("instance_count", "created_faces_count", "volume_delta")
    )


def test_a_cut_row_that_runs_into_the_void_of_an_l_shaped_pad_is_reported() -> None:
    computer = _computer_with_pad(L_SHAPE, L_BBOX)
    op, request = _row(20.0, 2)  # (5, 30) -> (25, 30): inside the bbox of the L, in the void of the L
    by = _by_name(computer.compute(op, request, _before(L_BBOX), _hole_stub((5.0, 30.0))))
    assert by["volume_delta"].kind is CheckKind.reported
    assert by["created_faces_count"].kind is CheckKind.reported
    assert "outline" in by["volume_delta"].note
    assert by["instance_count"].kind is CheckKind.asserted


def test_a_cut_row_that_stays_on_the_material_of_an_l_shaped_pad_is_asserted() -> None:
    computer = _computer_with_pad(L_SHAPE, L_BBOX)
    op, request = _row(20.0, 2)  # (20, 5) -> (40, 5): along the foot of the L
    by = _by_name(computer.compute(op, request, _before(L_BBOX), _hole_stub((20.0, 5.0))))
    assert by["volume_delta"].kind is CheckKind.asserted
    assert by["created_faces_count"].kind is CheckKind.asserted


def test_a_bolt_circle_inside_a_disc_is_asserted_and_one_that_leaves_it_is_reported() -> None:
    def ring(radius: float) -> tuple[ExpectedComputer, CircularPatternOperation, CircularPatternRequest]:
        axis = Axis(origin=(0, 0, 0), direction=(0, 0, 1))
        op = CircularPatternOperation(id="ring", intent="i", features=["hole_1"], axis=axis, count=6)
        request = CircularPatternRequest(
            operation_id="ring", features=(FeatureHandle(id="feature:2"),), axis=axis, count=6
        )
        return _computer_with_pad([DISC], DISC_BBOX), op, request

    computer, op, request = ring(45.0)
    by = _by_name(computer.compute(op, request, _before(DISC_BBOX), _hole_stub((45.0, 0.0), 4.5)))
    assert by["volume_delta"].kind is CheckKind.asserted
    computer, op, request = ring(58.0)
    by = _by_name(computer.compute(op, request, _before(DISC_BBOX), _hole_stub((58.0, 0.0), 4.5)))
    assert by["volume_delta"].kind is CheckKind.reported


def test_a_cut_row_on_a_host_whose_outline_is_not_known_is_reported() -> None:
    computer = ExpectedComputer(Thresholds())  # never saw a base pad
    op, request = _row(30.0, 3)
    by = _by_name(computer.compute(op, request, _before(PLATE_BBOX), _hole_stub((10.0, 30.0))))
    assert by["volume_delta"].kind is CheckKind.reported
    assert by["instance_count"].kind is CheckKind.asserted


def test_a_revolved_base_is_not_a_known_outline_either() -> None:
    computer = ExpectedComputer(Thresholds())
    stub = StubAdapter()
    computer.compute(
        RevolveOperation(id="base", intent="i", sketch="s", axis="u"),
        ExtrudeRequest(operation_id="base", sketch=SketchHandle(id="s"), depth_mm=1.0),
        EMPTY,
        stub,
    )
    op, request = _row(30.0, 3)
    by = _by_name(computer.compute(op, request, _before(PLATE_BBOX), _hole_stub((10.0, 30.0))))
    assert by["volume_delta"].kind is CheckKind.reported


# --- earlier features ---------------------------------------------------------------------------------------------


def _with_boss(computer: ExpectedComputer, center: tuple[float, float]) -> None:
    stub = StubAdapter()
    circle = {"kind": "circle", "name": "c", "center": list(center), "diameter": 10}
    sketch = SketchOperation(id="boss2_sketch", intent="i", on=PlaneRef(name="XY"), entities=[circle])
    snapshot = StateSnapshot(mass=mass(48000.0), bbox=PLATE_BBOX)
    computer.compute(
        sketch, SketchRequest(operation_id="boss2_sketch", on=PlaneName.XY, entities=sketch.entities), snapshot, stub
    )
    computer.compute(
        ExtrudeOperation(id="boss2", intent="i", sketch="boss2_sketch", depth_mm=5.0),
        ExtrudeRequest(operation_id="boss2", sketch=SketchHandle(id="s"), depth_mm=5.0),
        snapshot,
        stub,
    )


def test_an_instance_landing_on_an_earlier_feature_is_reported_and_one_clear_of_it_is_asserted() -> None:
    op, request = _row(30.0, 2)  # the hole at (10, 30) repeated at (40, 30)
    computer = _computer_with_pad([RECT], PLATE_BBOX)
    _with_boss(computer, (40.0, 30.0))
    by = _by_name(computer.compute(op, request, _before(PLATE_BBOX), _hole_stub((10.0, 30.0))))
    assert by["volume_delta"].kind is CheckKind.reported
    assert by["created_faces_count"].kind is CheckKind.reported
    assert "earlier" in by["volume_delta"].note
    assert by["instance_count"].kind is CheckKind.asserted
    computer = _computer_with_pad([RECT], PLATE_BBOX)
    _with_boss(computer, (80.0, 50.0))
    by = _by_name(computer.compute(op, request, _before(PLATE_BBOX), _hole_stub((10.0, 30.0))))
    assert by["volume_delta"].kind is CheckKind.asserted
    assert by["created_faces_count"].kind is CheckKind.asserted


def test_an_earlier_feature_of_unknown_extent_makes_every_instance_suspect() -> None:
    """A dress-up feature records no region (``None``): the pattern cannot tell where it is."""
    computer = _computer_with_pad([RECT], PLATE_BBOX)
    computer._feature_regions["round"] = None  # noqa: SLF001
    op, request = _row(30.0, 2)
    by = _by_name(computer.compute(op, request, _before(PLATE_BBOX), _hole_stub((10.0, 30.0))))
    assert by["volume_delta"].kind is CheckKind.reported


def test_the_seeds_and_the_base_pad_are_not_earlier_features() -> None:
    computer = _computer_with_pad([RECT], PLATE_BBOX)
    computer._feature_regions["hole_1"] = BoundingBox(min=(5.0, 25.0, 0.0), max=(15.0, 35.0, 8.0))  # noqa: SLF001
    op, request = _row(30.0, 2)
    by = _by_name(computer.compute(op, request, _before(PLATE_BBOX), _hole_stub((10.0, 30.0))))
    assert by["volume_delta"].kind is CheckKind.asserted


@pytest.mark.parametrize("name", ["volume_delta", "created_faces_count"])
def test_the_certain_rows_are_unchanged(name: str) -> None:
    computer = _computer_with_pad([RECT], PLATE_BBOX)
    op, request = _row(30.0, 3)
    by = _by_name(computer.compute(op, request, _before(PLATE_BBOX), _hole_stub((10.0, 30.0))))
    assert by[name].kind is CheckKind.asserted
    assert by["volume_delta"].value == pytest.approx(-2 * HOLE_VOLUME)
    assert by["created_faces_count"].value == 2
