"""Several holes in one ``hole`` op (``positions`` / ``bolt_circle``) and the origin their (u, v) is from."""

import math
from typing import Any

import pytest
from pydantic import ValidationError

from cotaforge.adapters.fake.adapter import FakeAdapter
from cotaforge.contract import (
    AdapterError,
    BoundingBox,
    ConnectRequest,
    ErrorCode,
    ErrorInfo,
    FeatureRequest,
    ProvenanceRole,
    SurfaceType,
    frame_for_face,
    stable_face_origin,
)
from cotaforge.executor.build import build_request
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.errors import ResolutionError
from cotaforge.executor.executor import Executor, _in_place_of
from cotaforge.executor.trace import ExecutorState
from cotaforge.ir import parse_plan
from cotaforge.verification.verifier import Verifier
from tests.unit.verification.helpers import planar_face, plate_faces

from .test_build import _env, _plan

TOP = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}
BASE = [
    {
        "id": "s1",
        "op": "sketch",
        "intent": "outline",
        "on": {"kind": "plane", "name": "XY"},
        "entities": [{"kind": "rect", "name": "r", "center": [50, 30], "width": 100, "height": 60}],
    },
    {"id": "base", "op": "extrude", "intent": "plate", "sketch": "s1", "depth_mm": 8},
]


def _hole(op_id: str, **fields: object) -> dict:
    return {
        "id": op_id,
        "op": "hole",
        "intent": "holes",
        "face": TOP,
        "standard": "simple",
        "diameter_mm": 6,
        "end_condition": "through_all",
        **fields,
    }


def _run(*holes: dict) -> tuple[Executor, FakeAdapter]:
    adapter = FakeAdapter()
    adapter.connect(ConnectRequest(operation_id="connect"))
    plan = parse_plan({"id": "p", "kind": "part", "operations": [*BASE, *holes]})
    executor = Executor(plan, adapter, Verifier(), ExecutorConfig())
    executor.start()
    executor.run_to_end()
    return executor, adapter


def _axes(executor: Executor, adapter: FakeAdapter, op_id: str) -> list[tuple[float, float]]:
    """(x, y) of the axis of every cylinder the op created."""
    feature = executor.feature_for(op_id)
    assert feature is not None
    faces = adapter.faces_of(FeatureRequest(operation_id="q", feature=feature, role=ProvenanceRole.created)).faces
    return sorted(
        (round(f.axis.origin[0], 6), round(f.axis.origin[1], 6))
        for f in faces
        if f.surface is SurfaceType.cylinder and f.axis is not None
    )


def test_four_holes_of_a_rectangular_pattern_are_one_feature_at_the_requested_points() -> None:
    corners = [[-30, -15], [30, -15], [30, 15], [-30, 15]]
    executor, adapter = _run(_hole("holes", positions=corners))
    assert executor.state == ExecutorState.completed, executor.trace().final_state
    # the plate's top face is centred at (50, 30): every axis is the face centre plus the requested (u, v)
    wanted = sorted((50.0 + u, 30.0 + v) for u, v in corners)
    assert _axes(executor, adapter, "holes") == pytest.approx(wanted)
    assert len(executor.trace().active_steps()) == 3  # sketch, extrude and ONE hole step


def test_a_bolt_circle_of_six_holes_is_one_feature_on_the_circle() -> None:
    executor, adapter = _run(_hole("bc", bolt_circle={"diameter_mm": 40, "count": 6, "center": [10, 0]}))
    assert executor.state == ExecutorState.completed, executor.trace().final_state
    axes = _axes(executor, adapter, "bc")
    assert len(axes) == 6
    for x, y in axes:
        assert math.hypot(x - 60.0, y - 30.0) == pytest.approx(20.0)
    assert (80.0, 30.0) in [(pytest.approx(x), pytest.approx(y)) for x, y in axes]  # start angle 0: the +u point


def test_the_second_hole_of_a_face_is_measured_from_the_same_point_as_the_first() -> None:
    first = _hole("h1", position=[-30, -15])
    second = _hole("h2", position=[0, 0])
    executor, adapter = _run(first, second)
    assert executor.state == ExecutorState.completed, executor.trace().final_state
    assert _axes(executor, adapter, "h2") == pytest.approx([(50.0, 30.0)])


def test_the_origin_of_a_hole_is_the_centre_of_the_box_of_the_face_not_its_centre_of_mass() -> None:
    top = next(f for f in plate_faces("feature:1") if f.handle.id == "face:top")
    holed = top.model_copy(update={"center": (45.0, 31.0, 8.0)})  # holes in the face moved its centre of mass
    assert stable_face_origin(holed) == pytest.approx((50.0, 30.0, 8.0))
    assert stable_face_origin(top) == pytest.approx((50.0, 30.0, 8.0))


def test_the_request_carries_every_point_and_the_first_one_as_position() -> None:
    _, ctx, resolver = _env()
    plan = _plan([*BASE, _hole("h", positions=[[1, 2], [20, 2]])])
    request = build_request(plan.operations[2], ctx, resolver)
    assert request.position == (1.0, 2.0) and request.positions == ((1.0, 2.0), (20.0, 2.0))
    assert request.origin == pytest.approx((50.0, 30.0, 8.0))


@pytest.mark.parametrize(
    ("fields", "why"),
    [
        ({"positions": [[0, 0], [2, 0]]}, "closer than"),
        ({"positions": [[0, 0]]}, "at least 2"),
        ({"positions": [[0, 0], [20, 0]], "position": [1, 1]}, "exactly one of"),
        ({"positions": [[0, 0], [20, 0]], "at": [1, 1, 8]}, "exactly one of"),
        ({"bolt_circle": {"diameter_mm": 10, "count": 6}}, "closer than"),
        ({"positions": [[0, 0], [20, 0]], "end_condition": "up_to_face", "up_to_face": TOP}, "up_to_face"),
    ],
)
def test_several_holes_are_refused_when_they_cannot_be_one_feature(fields: dict, why: str) -> None:
    with pytest.raises((ValidationError, ValueError), match=why):
        _plan([*BASE, _hole("h", **fields)])


def test_several_holes_on_a_curved_face_are_refused_with_the_way_out() -> None:
    _, ctx, resolver = _env()
    cyl = {"kind": "face", "from_op": "hole_1", "select": {"surface": "cylinder"}}
    plan = _plan([*BASE, _hole("hole_1", position=[0, 0]), _hole("h", positions=[[1, 2], [20, 2]], face=cyl)])
    with pytest.raises(ResolutionError, match="planar face"):
        build_request(plan.operations[3], ctx, resolver)


def test_on_an_l_shaped_face_the_origin_is_the_box_centre_even_where_there_is_no_material() -> None:
    l_face = planar_face(
        "face:l", (0.0, 0.0, 1.0), (35.0, 17.0, 8.0), 3200.0, BoundingBox(min=(0, 0, 8), max=(100, 60, 8))
    )
    assert stable_face_origin(l_face) == pytest.approx((50.0, 30.0, 8.0))  # the notch of the L: no material there
    assert frame_for_face(l_face, stable_face_origin(l_face)).to_world(-40.0, 20.0) == pytest.approx((10.0, 50.0, 8.0))


def test_a_model_saved_before_keeps_its_holes_where_they_were() -> None:
    """A request recorded without origin (every one saved before) is measured from the centre of mass, as it was; an
    edit or a rebuild of it keeps the recorded origin, never the new one."""
    face = planar_face(
        "face:l", (0.0, 0.0, 1.0), (35.0, 17.0, 8.0), 3200.0, BoundingBox(min=(0, 0, 8), max=(100, 60, 8))
    )
    assert frame_for_face(face, None).origin == pytest.approx(
        (35.0, 17.0, 8.0)
    )  # legacy: no origin, the centre of mass
    _, ctx, resolver = _env()
    plan = _plan([*BASE, _hole("h", position=[1, 2])])
    request = build_request(plan.operations[2], ctx, resolver)
    assert request.origin is not None  # new requests always carry it
    assert _in_place_of(request, {"origin": None}).origin is None  # the record says "legacy": it stays legacy
    assert _in_place_of(request, {"origin": [35.0, 17.0, 8.0]}).origin == (35.0, 17.0, 8.0)


def test_the_probe_that_verifies_a_hole_is_measured_from_the_same_origin_as_the_hole() -> None:
    """The boolean the verifier asks the CAD for (``tool_overlap``) must place the tool where the hole goes."""
    asked: list = []

    class Recording(FakeAdapter):
        def tool_overlap(self, request: Any) -> Any:
            asked.append(request)
            raise AdapterError(ErrorInfo(code=ErrorCode.not_supported, message="not measured here", recoverable=False))

    adapter = Recording()
    adapter.connect(ConnectRequest(operation_id="connect"))
    plan = parse_plan({"id": "p", "kind": "part", "operations": [*BASE, _hole("h", position=[5, 5])]})
    executor = Executor(plan, adapter, Verifier(), ExecutorConfig())
    executor.start()
    executor.run_to_end()
    assert executor.state == ExecutorState.completed
    assert asked and all(r.hole.origin == pytest.approx((50.0, 30.0, 8.0)) for r in asked), asked
