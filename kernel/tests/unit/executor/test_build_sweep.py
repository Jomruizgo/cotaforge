"""``build_sweep``: request building plus the placement/clearance checks that run before any adapter call."""

import math

import pytest

from cotaforge.contract import (
    CircleEntity,
    DocumentHandle,
    FeatureHandle,
    PlaneName,
    RectEntity,
    SketchFrame,
    SketchHandle,
    SweepRequest,
    frame_for_normal,
    frame_for_plane,
)
from cotaforge.executor.build import DISPATCH, build_request
from cotaforge.executor.context import ExecutionContext
from cotaforge.executor.errors import ResolutionError
from cotaforge.executor.resolver import Resolver
from cotaforge.ir import SweepOperation, parse_plan
from cotaforge.verification.thresholds import Thresholds

from .stub import StubAdapter

L_PATH = [[0, 0, 0], [0, 0, 100], [100, 0, 100]]
CIRCLE = CircleEntity(name="c", center=(0.0, 0.0), diameter=10.0)


def _env(*entities: object, frame: SketchFrame | None = None) -> tuple[ExecutionContext, Resolver]:
    stub = StubAdapter()
    ctx = ExecutionContext(document=DocumentHandle(id="doc:1"))
    ctx.sketch_by_op["s"] = SketchHandle(id="sk:1")
    ctx.sketch_frame_by_op["s"] = frame if frame is not None else frame_for_plane(PlaneName.XY)
    ctx.sketch_entities_by_op["s"] = tuple(entities) if entities else (CIRCLE,)
    ctx.feature_by_op["s"] = FeatureHandle(id="f:0")
    return ctx, Resolver(stub, ctx, Thresholds())


def _sweep_op(path: dict | None = None, **extra: object) -> SweepOperation:
    plan = parse_plan(
        {
            "id": "p",
            "kind": "part",
            "operations": [
                {
                    "id": "s",
                    "op": "sketch",
                    "intent": "section",
                    "on": {"kind": "plane", "name": "XY"},
                    "entities": [{"kind": "circle", "name": "c", "center": [0, 0], "diameter": 10}],
                },
                {
                    "id": "w",
                    "op": "sweep",
                    "intent": "bent tube",
                    "sketch": "s",
                    "path": path if path is not None else {"points": L_PATH},
                    **extra,
                },
            ],
        }
    )
    op = plan.operations[1]
    assert isinstance(op, SweepOperation)
    return op


def test_dispatch_routes_sweep_to_the_sweep_tool() -> None:
    assert DISPATCH["sweep"][0] == "sweep"


def test_sweep_builds_the_request_with_the_path_and_radius() -> None:
    ctx, resolver = _env()
    request = build_request(_sweep_op({"points": L_PATH, "corner_radius_mm": 20}, merge=False), ctx, resolver)
    assert request == SweepRequest(
        operation_id="w",
        sketch=SketchHandle(id="sk:1"),
        path=((0.0, 0.0, 0.0), (0.0, 0.0, 100.0), (100.0, 0.0, 100.0)),
        corner_radius_mm=20.0,
        merge=False,
    )


def test_a_mitred_sweep_defaults_to_no_radius_and_merge() -> None:
    ctx, resolver = _env()
    request = build_request(_sweep_op(), ctx, resolver)
    assert isinstance(request, SweepRequest)
    assert request.corner_radius_mm is None
    assert request.merge is True


def test_sweep_of_an_unknown_sketch_is_reported_on_sketch() -> None:
    ctx, resolver = _env()
    del ctx.sketch_by_op["s"]
    with pytest.raises(ResolutionError) as info:
        build_request(_sweep_op(), ctx, resolver)
    assert info.value.path == "sketch"


def test_sweep_needs_a_single_profile_and_reports_it_on_sketch() -> None:
    ctx, resolver = _env(CIRCLE, CircleEntity(name="inner", center=(0.0, 0.0), diameter=4.0))
    with pytest.raises(ResolutionError) as info:
        build_request(_sweep_op(), ctx, resolver)
    assert info.value.path == "sketch"
    assert "exactly ONE closed profile" in str(info.value)


@pytest.mark.parametrize(
    ("entities", "frame", "path", "needle"),
    [
        # centroid 10 mm off the path start
        ((CircleEntity(name="c", center=(10.0, 0.0), diameter=10.0),), None, {"points": L_PATH}, "centroid"),
        # sketch plane XY but the path leaves along X
        ((CIRCLE,), None, {"points": [[0, 0, 0], [100, 0, 0], [100, 0, 100]]}, "normal to the first path segment"),
        # plane does not pass through the start
        ((CIRCLE,), None, {"points": [[0, 0, 5], [0, 0, 100], [100, 0, 100]]}, "does not pass through"),
        # radius below the rect's reach 5.59
        (
            (RectEntity(name="r", center=(0.0, 0.0), width=10.0, height=5.0),),
            None,
            {"points": L_PATH, "corner_radius_mm": 4},
            "too small for the section",
        ),
        # radius too big for the segments
        ((CIRCLE,), None, {"points": L_PATH, "corner_radius_mm": 150}, "does not fit"),
        # mitre overhang longer than the middle segment
        (
            (CIRCLE,),
            None,
            {"points": [[0, 0, 0], [0, 0, 50], [4, 0, 50], [4, 0, 100]]},
            "too short for the mitred corners",
        ),
        # the last leg passes 4 mm from the first (section 10 mm wide)
        (
            (CIRCLE,),
            None,
            {"points": [[0, 0, 0], [0, 0, 100], [40, 0, 100], [40, 0, 6], [4, 0, 6]]},
            "touch itself",
        ),
    ],
)
def test_bad_sweeps_never_reach_the_adapter_and_name_the_fix_on_path(
    entities: tuple, frame: SketchFrame | None, path: dict, needle: str
) -> None:
    ctx, resolver = _env(*entities, frame=frame)
    with pytest.raises(ResolutionError) as info:
        build_request(_sweep_op(path), ctx, resolver)
    assert info.value.path == "path"
    assert needle in str(info.value)


def test_an_oblique_datum_frame_normal_to_the_first_segment_is_accepted() -> None:
    frame = frame_for_normal((5.0, 0.0, 0.0), (0.0, 0.6, 0.8))
    ctx, resolver = _env(CIRCLE, frame=frame)
    path = {"points": [[5, 0, 0], [5, 60, 80], [5, 60, 180]]}
    request = build_request(_sweep_op(path), ctx, resolver)
    assert isinstance(request, SweepRequest)
    assert request.path[0] == (5.0, 0.0, 0.0)


def test_a_flat_strap_bent_on_its_thin_side_is_accepted_and_on_its_wide_side_needs_more_radius() -> None:
    thin = RectEntity(name="r", center=(0.0, 0.0), width=5.0, height=40.0)  # extent 2.5 toward the bend (+X)
    ctx, resolver = _env(thin)
    request = build_request(_sweep_op({"points": L_PATH, "corner_radius_mm": 3}), ctx, resolver)
    assert isinstance(request, SweepRequest) and request.corner_radius_mm == 3.0
    wide = RectEntity(name="r", center=(0.0, 0.0), width=40.0, height=5.0)  # extent 20
    ctx, resolver = _env(wide)
    with pytest.raises(ResolutionError, match="reaches 20 mm toward the bend centre") as info:
        build_request(_sweep_op({"points": L_PATH, "corner_radius_mm": 15}), ctx, resolver)
    assert info.value.path == "path"
    assert isinstance(build_request(_sweep_op({"points": L_PATH, "corner_radius_mm": 21}), ctx, resolver), SweepRequest)


def test_a_path_tilted_from_the_sketch_normal_beyond_the_position_tolerance_is_refused() -> None:
    tilt = math.radians(0.45)
    path = [[0, 0, 0], [100 * math.sin(tilt), 0, 100 * math.cos(tilt)], [100, 0, 100]]
    ctx, resolver = _env()
    with pytest.raises(ResolutionError, match="exactly along the normal") as info:
        build_request(_sweep_op({"points": path}), ctx, resolver)
    assert info.value.path == "path"
