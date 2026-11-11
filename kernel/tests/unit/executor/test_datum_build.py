"""build_request / Resolver for the datum ops ``ref_plane`` / ``ref_axis`` (StubAdapter, no fake needed)."""

import math

import pytest
from pydantic import TypeAdapter

from cotaforge.contract import (
    Axis,
    DatumAxisHandle,
    DatumAxisRequest,
    DatumPlaneHandle,
    DatumPlaneRequest,
    DatumPlaneTarget,
    LinearPatternRequest,
    PlaneName,
    SketchRequest,
    frame_for_plane,
)
from cotaforge.contract.requests import CircularPatternRequest
from cotaforge.executor.build import DISPATCH, NON_FEATURE_OPS, build_request
from cotaforge.executor.errors import ResolutionError
from cotaforge.executor.resolver import Resolver
from cotaforge.ir.ops_features import Operation
from tests.unit.executor.test_build import _env
from tests.unit.verification.helpers import line_edge

_OP_ADAPTER = TypeAdapter(Operation)
DEFINE_XY_OFFSET = {"how": "offset", "base": {"kind": "plane", "name": "XY"}, "distance_mm": 10.0}


def _ref_plane(op_id: str, define: dict) -> dict:
    return {"id": op_id, "op": "ref_plane", "intent": "datum", "define": define}


def _ref_axis(op_id: str, define: dict) -> dict:
    return {"id": op_id, "op": "ref_axis", "intent": "datum axis", "define": define}


def _build(ctx: object, resolver: Resolver, *ops: dict) -> list:
    # ops are validated one by one: the environment already holds the features/datums they reference
    return [build_request(_OP_ADAPTER.validate_python(op), ctx, resolver) for op in ops]  # type: ignore[arg-type]


def test_dispatch_maps_datum_ops_to_the_optional_tools() -> None:
    assert DISPATCH["ref_plane"][0] == "datum_plane"
    assert DISPATCH["ref_axis"][0] == "datum_axis"
    assert {"ref_plane", "ref_axis"} <= NON_FEATURE_OPS


def test_offset_of_a_base_plane() -> None:
    _, ctx, resolver = _env()
    (request,) = _build(ctx, resolver, _ref_plane("dp", DEFINE_XY_OFFSET))
    assert isinstance(request, DatumPlaneRequest)
    assert request.how == "offset"
    assert request.frame.origin == (0.0, 0.0, 10.0)
    assert request.frame.n == (0.0, 0.0, 1.0)


def test_offset_of_a_planar_face_uses_the_face_rule_frame() -> None:
    _, ctx, resolver = _env()
    top = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}
    (request,) = _build(ctx, resolver, _ref_plane("dp", {"how": "offset", "base": top, "distance_mm": 5.0}))
    assert request.frame.origin == pytest.approx((50.0, 30.0, 13.0))
    assert request.frame.n == pytest.approx((0.0, 0.0, 1.0))
    assert resolver.resolved_refs["define.base"] == "face:top"


def test_offset_of_a_non_planar_face_is_a_resolution_error() -> None:
    _, ctx, resolver = _env()
    cyl = {"kind": "face", "from_op": "hole_1"}
    with pytest.raises(ResolutionError, match="not planar"):
        _build(ctx, resolver, _ref_plane("dp", {"how": "offset", "base": cyl, "distance_mm": 5.0}))


def test_datum_chain_uses_the_registered_target_frame() -> None:
    _, ctx, resolver = _env()
    first = frame_for_plane(PlaneName.XZ)
    ctx.datum_plane_by_op["dp1"] = DatumPlaneTarget(handle=DatumPlaneHandle(id="datum_plane:1"), frame=first)
    (request,) = _build(
        ctx,
        resolver,
        _ref_plane("dp2", {"how": "offset", "base": {"kind": "plane", "from_op": "dp1"}, "distance_mm": 3.0}),
    )
    assert request.frame.origin == (0.0, 3.0, 0.0)
    assert request.frame.v == (0.0, 0.0, -1.0)
    assert resolver.resolved_refs["define.base"] == "datum_plane:1"


def test_unregistered_datum_plane_is_a_resolution_error() -> None:
    _, ctx, resolver = _env()
    with pytest.raises(ResolutionError, match="produced no datum plane"):
        _build(
            ctx,
            resolver,
            _ref_plane("dp2", {"how": "offset", "base": {"kind": "plane", "from_op": "dp1"}, "distance_mm": 3.0}),
        )


def test_angle_about_a_literal_axis_and_about_an_edge() -> None:
    _, ctx, resolver = _env()
    about_x = {"origin": [0, 0, 0], "direction": [1, 0, 0]}
    (request,) = _build(
        ctx,
        resolver,
        _ref_plane("dp", {"how": "angle", "base": {"kind": "plane", "name": "XY"}, "about": about_x, "angle_deg": 30}),
    )
    assert request.frame.n == pytest.approx((0.0, -math.sin(math.radians(30)), math.cos(math.radians(30))))
    # edge:1 runs (100,0,8)->(100,60,8): along Y at z=8, so it lies in the XY plane offset to z=8
    edge = {
        "kind": "edge",
        "between": [
            {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}},
            {"kind": "face", "from_op": "base", "select": {"normal": [1, 0, 0]}},
        ],
        "curve": "line",
    }
    (offset,) = _build(
        ctx, resolver, _ref_plane("dp", {"how": "offset", "base": {"kind": "plane", "name": "XY"}, "distance_mm": 8.0})
    )
    ctx.datum_plane_by_op["dp"] = DatumPlaneTarget(handle=DatumPlaneHandle(id="datum_plane:1"), frame=offset.frame)
    (angled,) = _build(
        ctx,
        resolver,
        _ref_plane("dp2", {"how": "angle", "base": {"kind": "plane", "from_op": "dp"}, "about": edge, "angle_deg": 90}),
    )
    # rotating +Z by +90 deg about the edge direction +Y gives +X
    assert angled.frame.n == pytest.approx((1.0, 0.0, 0.0))


def test_angle_about_an_axis_outside_the_base_plane_is_a_resolution_error() -> None:
    _, ctx, resolver = _env()
    lifted = {"origin": [0, 0, 5], "direction": [1, 0, 0]}
    with pytest.raises(ResolutionError, match="does not lie in base plane"):
        _build(
            ctx,
            resolver,
            _ref_plane(
                "dp", {"how": "angle", "base": {"kind": "plane", "name": "XY"}, "about": lifted, "angle_deg": 30}
            ),
        )


def test_midplane_of_non_parallel_planes_is_a_resolution_error() -> None:
    _, ctx, resolver = _env()
    with pytest.raises(ResolutionError, match="not parallel"):
        _build(
            ctx,
            resolver,
            _ref_plane(
                "dp", {"how": "midplane", "a": {"kind": "plane", "name": "XY"}, "b": {"kind": "plane", "name": "XZ"}}
            ),
        )


def test_normal_to_a_cylindrical_face_axis_via_ref_axis() -> None:
    _, ctx, resolver = _env()
    (axis_request,) = _build(
        ctx, resolver, _ref_axis("da", {"how": "entity", "entity": {"kind": "face", "from_op": "hole_1"}})
    )
    assert isinstance(axis_request, DatumAxisRequest)
    assert axis_request.how == "entity"
    assert axis_request.axis.direction == (0.0, 0.0, -1.0)
    # adapters describe the same cylinder axis by different points (base centre, face centre): an entity axis is
    # canonicalised to the point of the line nearest the world origin so at_mm means the same on every adapter
    assert axis_request.axis.origin == pytest.approx((10.0, 10.0, 0.0))
    ctx.datum_axis_by_op["da"] = axis_request.axis
    (plane_request,) = _build(
        ctx,
        resolver,
        _ref_plane("dp", {"how": "normal_to", "axis": {"kind": "axis_ref", "from_op": "da"}, "at_mm": 4.0}),
    )
    assert plane_request.frame.n == (0.0, 0.0, -1.0)
    assert plane_request.frame.origin == pytest.approx((10.0, 10.0, -4.0))


def test_entity_axis_of_a_linear_edge_and_of_a_planar_face() -> None:
    _, ctx, resolver = _env()
    edge = {
        "kind": "edge",
        "between": [
            {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}},
            {"kind": "face", "from_op": "base", "select": {"normal": [1, 0, 0]}},
        ],
        "curve": "line",
    }
    (request,) = _build(ctx, resolver, _ref_axis("da", {"how": "entity", "entity": edge}))
    assert request.axis.origin == (100.0, 0.0, 8.0)
    assert request.axis.direction == pytest.approx((0.0, 1.0, 0.0))
    with pytest.raises(ResolutionError, match="has no axis"):
        _build(
            ctx,
            resolver,
            _ref_axis(
                "db", {"how": "entity", "entity": {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}}
            ),
        )


def test_axis_definitions_from_points_direction_and_plane_intersection() -> None:
    _, ctx, resolver = _env()
    two, pd, named, inter = _build(
        ctx,
        resolver,
        _ref_axis("a1", {"how": "two_points", "points": [[0, 0, 0], [0, 3, 0]]}),
        _ref_axis("a2", {"how": "point_direction", "point": [1, 1, 1], "direction": [0, 0, 2]}),
        _ref_axis("a3", {"how": "point_direction", "point": [1, 1, 1], "direction": "X"}),
        _ref_axis(
            "a4",
            {"how": "plane_intersection", "a": {"kind": "plane", "name": "XY"}, "b": {"kind": "plane", "name": "YZ"}},
        ),
    )
    assert two.axis.direction == (0.0, 1.0, 0.0)
    assert pd.axis.direction == (0.0, 0.0, 1.0)
    assert named.axis.direction == (1.0, 0.0, 0.0)
    assert abs(inter.axis.direction[1]) == pytest.approx(1.0)
    assert inter.axis.origin == pytest.approx((0.0, 0.0, 0.0))


def test_unregistered_datum_axis_is_a_resolution_error() -> None:
    _, ctx, resolver = _env()
    with pytest.raises(ResolutionError, match="produced no datum axis"):
        _build(
            ctx,
            resolver,
            _ref_plane("dp", {"how": "normal_to", "axis": {"kind": "axis_ref", "from_op": "da"}, "at_mm": 4}),
        )


def test_sketch_on_a_datum_plane_carries_handle_and_frame() -> None:
    _, ctx, resolver = _env()
    frame = frame_for_plane(PlaneName.YZ)
    target = DatumPlaneTarget(handle=DatumPlaneHandle(id="datum_plane:7"), frame=frame)
    ctx.datum_plane_by_op["dp"] = target
    rect = {"kind": "rect", "name": "r", "center": [0, 0], "width": 4, "height": 2}
    (request,) = _build(
        ctx,
        resolver,
        {"id": "s", "op": "sketch", "intent": "i", "on": {"kind": "plane", "from_op": "dp"}, "entities": [rect]},
    )
    assert isinstance(request, SketchRequest)
    assert request.on == target
    assert resolver.resolved_refs["on"] == "datum_plane:7"


def test_patterns_accept_a_datum_axis() -> None:
    _, ctx, resolver = _env()
    ctx.datum_axis_by_op["da"] = Axis(origin=(1.0, 2.0, 3.0), direction=(0.0, 1.0, 0.0))
    linear, circular = _build(
        ctx,
        resolver,
        {
            "id": "lp",
            "op": "linear_pattern",
            "intent": "i",
            "features": ["hole_1"],
            "direction": {"kind": "axis_ref", "from_op": "da"},
            "spacing_mm": 5,
            "count": 2,
        },
        {
            "id": "cp",
            "op": "circular_pattern",
            "intent": "i",
            "features": ["hole_1"],
            "axis": {"kind": "axis_ref", "from_op": "da"},
            "count": 3,
        },
    )
    assert isinstance(linear, LinearPatternRequest)
    assert linear.direction == (0.0, 1.0, 0.0)
    assert isinstance(circular, CircularPatternRequest)
    assert circular.axis.origin == (1.0, 2.0, 3.0)
    assert DatumAxisHandle(id="datum_axis:1")  # the handle type exists for adapters


# --- review fixes: every geometry-derived axis is anchored the same way ------------------------------------


def test_a_face_or_edge_used_directly_as_an_axis_is_anchored_like_ref_axis_entity() -> None:
    stub, ctx, resolver = _env()
    stub.add_edge(line_edge("edge:2", ("face:top", "face:ymin"), (5.0, 20.0, 8.0), (5.0, 40.0, 8.0)))
    edge = {
        "kind": "edge",
        "between": [
            {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}},
            {"kind": "face", "from_op": "base", "select": {"normal": [0, -1, 0]}},
        ],
        "curve": "line",
    }
    cylinder = {"kind": "face", "from_op": "hole_1"}
    (via_edge, via_face) = _build(
        ctx,
        resolver,
        _ref_plane("pe", {"how": "normal_to", "axis": edge, "at_mm": 10.0}),
        _ref_plane("pf", {"how": "normal_to", "axis": cylinder, "at_mm": 4.0}),
    )
    # the edge runs (5,20,8)->(5,40,8) along +Y: its point nearest the world origin is (5,0,8)
    assert via_edge.frame.origin == pytest.approx((5.0, 10.0, 8.0))
    # the hole axis (10,10,z) runs along -Z: nearest point (10,10,0), then 4 mm along -Z
    assert via_face.frame.origin == pytest.approx((10.0, 10.0, -4.0))
    (via_axis,) = _build(ctx, resolver, _ref_axis("da", {"how": "entity", "entity": edge}))
    assert via_axis.axis.origin == pytest.approx((5.0, 0.0, 8.0))


def test_literal_and_point_based_axes_keep_the_origin_they_were_given() -> None:
    _, ctx, resolver = _env()
    literal = {"origin": [3, 4, 5], "direction": [0, 0, 1]}
    (plane,) = _build(ctx, resolver, _ref_plane("pl", {"how": "normal_to", "axis": literal, "at_mm": 2.0}))
    assert plane.frame.origin == pytest.approx((3.0, 4.0, 7.0))
    (axis,) = _build(ctx, resolver, _ref_axis("da", {"how": "point_direction", "point": [3, 4, 5], "direction": "Z"}))
    assert axis.axis.origin == (3.0, 4.0, 5.0)


def test_circular_pattern_about_a_straight_edge_uses_the_same_axis_rules_as_the_datum_ops() -> None:
    _, ctx, resolver = _env()
    edge = {
        "kind": "edge",
        "between": [
            {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}},
            {"kind": "face", "from_op": "base", "select": {"normal": [1, 0, 0]}},
        ],
        "curve": "line",
    }
    pattern = {"id": "cp", "op": "circular_pattern", "intent": "i", "features": ["hole_1"], "axis": edge, "count": 3}
    (request,) = _build(ctx, resolver, pattern)
    assert request.axis.direction == pytest.approx((0.0, 1.0, 0.0))
    assert request.axis.origin == pytest.approx((100.0, 0.0, 8.0))
    cylinder = {"kind": "face", "from_op": "hole_1"}
    (request,) = _build(ctx, resolver, {**pattern, "axis": cylinder})
    assert request.axis.direction == pytest.approx((0.0, 0.0, -1.0))
    (request,) = _build(ctx, resolver, {**pattern, "axis": {"origin": [1, 2, 3], "direction": [0, 0, 1]}})
    assert request.axis.origin == (1.0, 2.0, 3.0)
