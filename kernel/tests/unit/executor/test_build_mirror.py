"""``build_mirror`` / ``build_mirror_body``: request building through the plane resolver."""

import pytest

from cotaforge.contract import (
    BodyHandle,
    DatumPlaneHandle,
    DatumPlaneTarget,
    DocumentHandle,
    FeatureHandle,
    MirrorBodyRequest,
    MirrorRequest,
    PlaneName,
    SketchFrame,
    frame_for_plane,
)
from cotaforge.executor.build import DISPATCH, build_request
from cotaforge.executor.context import ExecutionContext
from cotaforge.executor.errors import ResolutionError
from cotaforge.executor.executor import PATTERN_OPS
from cotaforge.executor.resolver import Resolver
from cotaforge.ir import OperationBase, parse_plan
from cotaforge.verification.thresholds import Thresholds
from tests.unit.verification.helpers import plate_faces

from .stub import StubAdapter

TOP = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}
XMAX = {"kind": "face", "from_op": "base", "select": {"normal": [1, 0, 0]}}


def _env() -> tuple[StubAdapter, ExecutionContext, Resolver]:
    stub = StubAdapter()
    for face in plate_faces("feature:1"):
        stub.add_face(face, feature_id="feature:1")
    ctx = ExecutionContext(document=DocumentHandle(id="doc:1"))
    ctx.feature_by_op["base"] = FeatureHandle(id="feature:1")
    ctx.feature_by_op["boss"] = FeatureHandle(id="feature:2")
    ctx.body_by_op["base"] = BodyHandle(id="body:1")
    return stub, ctx, Resolver(stub, ctx, Thresholds())


def _op(name: str, **extra: object) -> OperationBase:
    fields: dict = {"id": "mi", "op": name, "intent": "mirror", "plane": {"kind": "plane", "name": "YZ"}}
    fields.update(extra)
    if name == "mirror":
        fields.setdefault("features", ["boss"])
    else:
        fields.setdefault("source", {"kind": "body", "from_op": "base"})
    plan = parse_plan(
        {
            "id": "p",
            "kind": "part",
            "operations": [
                {"id": "base", "op": "box", "intent": "block", "size": [100, 60, 8]},
                {
                    "id": "sk",
                    "op": "sketch",
                    "intent": "boss profile",
                    "on": {"kind": "plane", "name": "XY"},
                    "entities": [{"kind": "circle", "name": "c", "center": [10, 10], "diameter": 8}],
                },
                {"id": "boss", "op": "extrude", "intent": "boss", "sketch": "sk", "depth_mm": 5},
                {
                    "id": "pl",
                    "op": "ref_plane",
                    "intent": "mirror plane",
                    "define": {"how": "offset", "base": {"kind": "plane", "name": "YZ"}, "distance_mm": 20},
                },
                fields,
            ],
        }
    )
    return plan.operations[-1]


def test_dispatch_routes_both_to_their_tools_and_mirror_is_a_pattern_op() -> None:
    assert DISPATCH["mirror"][0] == "mirror"
    assert DISPATCH["mirror_body"][0] == "mirror_body"
    assert "mirror" in PATTERN_OPS
    assert "mirror_body" not in PATTERN_OPS


def test_mirror_builds_a_request_with_the_seed_handles_and_the_base_plane_frame() -> None:
    _, ctx, resolver = _env()
    request = build_request(_op("mirror", features=["boss", "base"]), ctx, resolver)
    assert isinstance(request, MirrorRequest)
    assert request.operation_id == "mi"
    assert request.features == (FeatureHandle(id="feature:2"), FeatureHandle(id="feature:1"))
    assert request.plane == frame_for_plane(PlaneName.YZ)
    assert resolver.resolved_refs["plane"] == "YZ"


def test_mirror_body_builds_a_request_with_the_resolved_source_body() -> None:
    _, ctx, resolver = _env()
    request = build_request(_op("mirror_body", plane={"kind": "plane", "name": "XZ"}), ctx, resolver)
    assert isinstance(request, MirrorBodyRequest)
    assert request.source == BodyHandle(id="body:1")
    assert request.plane == frame_for_plane(PlaneName.XZ)


def test_the_plane_may_be_a_datum_plane_with_its_own_frame_or_a_planar_face() -> None:
    _, ctx, resolver = _env()
    frame = SketchFrame(origin=(20.0, 0.0, 0.0), u=(0.0, 1.0, 0.0), v=(0.0, 0.0, 1.0), n=(1.0, 0.0, 0.0))
    ctx.datum_plane_by_op["pl"] = DatumPlaneTarget(handle=DatumPlaneHandle(id="datum_plane:1"), frame=frame)
    plane = {"kind": "plane", "from_op": "pl"}
    request = build_request(_op("mirror", plane=plane), ctx, resolver)
    assert isinstance(request, MirrorRequest)
    assert request.plane == frame
    face_request = build_request(_op("mirror_body", plane=XMAX), ctx, resolver)
    assert isinstance(face_request, MirrorBodyRequest)
    assert face_request.plane.origin == (100.0, 30.0, 4.0)
    assert face_request.plane.n == (1.0, 0.0, 0.0)


def test_a_seed_without_a_feature_is_an_actionable_error() -> None:
    _, ctx, resolver = _env()
    del ctx.feature_by_op["boss"]
    with pytest.raises(ResolutionError) as info:
        build_request(_op("mirror"), ctx, resolver)
    assert info.value.path == "features[0]"
    assert "boss" in str(info.value)


def test_a_plane_face_that_cannot_be_resolved_is_reported_through_the_resolver() -> None:
    _, ctx, resolver = _env()
    missing = {"kind": "face", "from_op": "base", "select": {"normal": [1, 1, 0]}}
    with pytest.raises(ResolutionError) as info:
        build_request(_op("mirror", plane=missing), ctx, resolver)
    assert info.value.path == "plane"
