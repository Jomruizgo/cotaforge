"""``build_split``, the side-aware body resolver and the scale of ``build_copy_body``."""

import pytest

from cotaforge.contract import (
    BodyHandle,
    CopyBodyRequest,
    DatumPlaneHandle,
    DatumPlaneTarget,
    DocumentHandle,
    PlaneName,
    SketchFrame,
    SplitRequest,
    frame_for_plane,
)
from cotaforge.executor.build import DISPATCH, build_request
from cotaforge.executor.context import ExecutionContext
from cotaforge.executor.errors import ResolutionError
from cotaforge.executor.resolver import Resolver
from cotaforge.ir import OperationBase, parse_plan
from cotaforge.ir.refs import BodyRef
from cotaforge.verification.thresholds import Thresholds

from .stub import StubAdapter

POSITIVE = BodyHandle(id="body:2")
NEGATIVE = BodyHandle(id="body:3")


def _env() -> tuple[ExecutionContext, Resolver]:
    ctx = ExecutionContext(document=DocumentHandle(id="doc:1"))
    ctx.body_by_op["base"] = BodyHandle(id="body:1")
    ctx.body_by_op["halves"] = POSITIVE
    ctx.body_sides["halves"] = {"positive": POSITIVE, "negative": NEGATIVE}
    return ctx, Resolver(StubAdapter(), ctx, Thresholds())


def _op(name: str = "split", **extra: object) -> OperationBase:
    fields: dict = {"id": "sp", "op": name, "intent": "cut", "source": {"kind": "body", "from_op": "base"}}
    if name == "split":
        fields["plane"] = {"kind": "plane", "name": "XY"}
    fields.update(extra)
    plan = parse_plan(
        {
            "id": "p",
            "kind": "part",
            "operations": [
                {"id": "base", "op": "box", "intent": "block", "size": [40, 30, 20]},
                {
                    "id": "pl",
                    "op": "ref_plane",
                    "intent": "parting plane",
                    "define": {"how": "offset", "base": {"kind": "plane", "name": "XY"}, "distance_mm": 10},
                },
                {
                    "id": "halves",
                    "op": "split",
                    "intent": "two halves",
                    "source": {"kind": "body", "from_op": "base"},
                    "plane": {"kind": "plane", "from_op": "pl"},
                },
                fields,
            ],
        }
    )
    return plan.operations[-1]


def test_dispatch_routes_split_to_its_tool() -> None:
    assert DISPATCH["split"][0] == "split"


def test_split_builds_a_request_with_the_source_the_plane_frame_and_keep() -> None:
    ctx, resolver = _env()
    request = build_request(_op(keep="negative"), ctx, resolver)
    assert isinstance(request, SplitRequest)
    assert request.operation_id == "sp"
    assert request.source == BodyHandle(id="body:1")
    assert request.plane == frame_for_plane(PlaneName.XY)
    assert request.keep == "negative"
    assert _op().keep == "both"


def test_the_plane_may_be_a_datum_plane_with_its_own_frame() -> None:
    ctx, resolver = _env()
    frame = SketchFrame(origin=(0.0, 0.0, 10.0), u=(1.0, 0.0, 0.0), v=(0.0, 1.0, 0.0), n=(0.0, 0.0, 1.0))
    ctx.datum_plane_by_op["pl"] = DatumPlaneTarget(handle=DatumPlaneHandle(id="datum_plane:1"), frame=frame)
    request = build_request(_op(plane={"kind": "plane", "from_op": "pl"}), ctx, resolver)
    assert isinstance(request, SplitRequest)
    assert request.plane == frame


def test_a_side_picks_the_matching_piece_of_a_split_that_kept_both() -> None:
    ctx, resolver = _env()
    assert resolver.body(BodyRef(from_op="halves", side="positive"), "x", "source") == POSITIVE
    assert resolver.body(BodyRef(from_op="halves", side="negative"), "x", "source") == NEGATIVE
    assert resolver.resolved_refs["source"] == "body:3"
    request = build_request(_op(source={"kind": "body", "from_op": "halves", "side": "negative"}), ctx, resolver)
    assert isinstance(request, SplitRequest)
    assert request.source == NEGATIVE


def test_a_ref_without_a_side_still_resolves_a_plain_body() -> None:
    _, resolver = _env()
    assert resolver.body(BodyRef(from_op="base"), "x", "source") == BodyHandle(id="body:1")


def test_a_side_the_split_did_not_keep_is_an_actionable_error() -> None:
    ctx, resolver = _env()
    ctx.body_sides["halves"] = {"positive": POSITIVE}
    with pytest.raises(ResolutionError) as info:
        resolver.body(BodyRef(from_op="halves", side="negative"), "x", "source")
    assert info.value.path == "source"
    assert "negative" in str(info.value) and "halves" in str(info.value)


def test_a_side_on_an_op_that_is_not_a_split_is_an_actionable_error() -> None:
    _, resolver = _env()
    with pytest.raises(ResolutionError) as info:
        resolver.body(BodyRef(from_op="base", side="positive"), "x", "source")
    assert "split" in str(info.value)


def test_copy_body_carries_scale_and_scale_about_next_to_the_rigid_transform() -> None:
    ctx, resolver = _env()
    op = _op(
        "copy_body",
        scale=1.006,
        scale_about=[20, 15, 0],
        offset=[60, 0, 0],
        rotate={"axis": "Z", "angle_deg": 90, "about": [0, 0, 0]},
    )
    request = build_request(op, ctx, resolver)
    assert isinstance(request, CopyBodyRequest)
    assert request.scale == 1.006
    assert request.scale_about == (20.0, 15.0, 0.0)
    assert request.transform.origin == (60.0, 0.0, 0.0)  # the rigid part only: no scale in the rotation
    assert request.transform.apply((1.0, 0.0, 0.0)) == pytest.approx((60.0, 1.0, 0.0))


def test_copy_body_scale_defaults_to_a_rigid_copy() -> None:
    ctx, resolver = _env()
    request = build_request(_op("copy_body", offset=[5, 0, 0]), ctx, resolver)
    assert isinstance(request, CopyBodyRequest)
    assert (request.scale, request.scale_about) == (1.0, (0.0, 0.0, 0.0))


def test_a_ref_without_a_side_to_a_split_that_made_two_bodies_is_refused_not_guessed() -> None:
    _, resolver = _env()
    with pytest.raises(ResolutionError) as info:
        resolver.body(BodyRef(from_op="halves"), "x", "source")
    assert "side" in str(info.value) and "halves" in str(info.value)
    assert info.value.path == "source"


def test_a_ref_without_a_side_still_resolves_the_one_piece_a_split_kept() -> None:
    ctx, resolver = _env()
    ctx.body_sides["halves"] = {"negative": NEGATIVE}
    ctx.body_by_op["halves"] = NEGATIVE
    assert resolver.body(BodyRef(from_op="halves"), "x", "source") == NEGATIVE
