"""``build_draft``: request building and the refusals that run before any adapter call."""

import math

import pytest

from cotaforge.contract import BodyHandle, DocumentHandle, DraftRequest, FaceHandle, FeatureHandle
from cotaforge.executor.build import DISPATCH, build_request
from cotaforge.executor.context import ExecutionContext
from cotaforge.executor.errors import ResolutionError
from cotaforge.executor.resolver import Resolver
from cotaforge.ir import OperationBase, parse_plan
from cotaforge.verification.thresholds import Thresholds
from tests.unit.verification.helpers import plate_faces

from .stub import StubAdapter

XMAX = {"kind": "face", "from_op": "base", "select": {"normal": [1, 0, 0]}}
XMIN = {"kind": "face", "from_op": "base", "select": {"normal": [-1, 0, 0]}}
TOP = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}


def _env(*, second_body: bool = False) -> tuple[StubAdapter, ExecutionContext, Resolver]:
    stub = StubAdapter()
    for face in plate_faces("feature:1"):
        if second_body and face.handle.id == "face:xmin":
            face = face.model_copy(update={"body": BodyHandle(id="body:2")})
        stub.add_face(face, feature_id="feature:1")
    ctx = ExecutionContext(document=DocumentHandle(id="doc:1"))
    ctx.feature_by_op["base"] = FeatureHandle(id="feature:1")
    return stub, ctx, Resolver(stub, ctx, Thresholds())


def _draft_op(**extra: object) -> OperationBase:
    fields: dict = {
        "id": "dr",
        "op": "draft",
        "intent": "taper",
        "faces": [XMAX],
        "neutral": {"kind": "plane", "name": "XY"},
        "pull": "Z",
        "angle_deg": 3,
    }
    fields.update(extra)
    plan = parse_plan(
        {
            "id": "p",
            "kind": "part",
            "operations": [{"id": "base", "op": "box", "intent": "block", "size": [100, 60, 8]}, fields],
        }
    )
    return plan.operations[1]


def test_dispatch_routes_draft_to_the_draft_tool() -> None:
    assert DISPATCH["draft"][0] == "draft"


def test_draft_builds_a_request_with_the_resolved_faces_the_neutral_frame_and_a_unit_pull() -> None:
    _, ctx, resolver = _env()
    request = build_request(_draft_op(faces=[XMAX, XMIN]), ctx, resolver)
    assert isinstance(request, DraftRequest)
    assert request.operation_id == "dr"
    assert request.faces == (FaceHandle(id="face:xmax"), FaceHandle(id="face:xmin"))
    assert request.neutral.origin == (0.0, 0.0, 0.0)
    assert request.neutral.n == (0.0, 0.0, 1.0)
    assert request.pull == (0.0, 0.0, 1.0)
    assert request.angle_deg == 3.0
    assert resolver.resolved_refs["faces[0]"] == "face:xmax"
    assert resolver.resolved_refs["neutral"] == "XY"


def test_a_vector_pull_is_normalised_and_keeps_its_sign() -> None:
    _, ctx, resolver = _env()
    request = build_request(_draft_op(pull=[0, 0, -4]), ctx, resolver)
    assert isinstance(request, DraftRequest)
    assert request.pull == (0.0, 0.0, -1.0)


def test_a_face_neutral_gives_its_own_frame_and_either_pull_sense_is_accepted() -> None:
    _, ctx, resolver = _env()
    for pull in ("Z", [0, 0, -1]):
        request = build_request(_draft_op(neutral=TOP, pull=pull), ctx, resolver)
        assert isinstance(request, DraftRequest)
        assert request.neutral.origin == (50.0, 30.0, 8.0)
        assert request.neutral.n == (0.0, 0.0, 1.0)


def test_a_pull_within_one_degree_of_the_neutral_normal_is_snapped_onto_it() -> None:
    _, ctx, resolver = _env()
    tilt = math.radians(0.9)
    request = build_request(_draft_op(pull=[math.sin(tilt), 0, math.cos(tilt)]), ctx, resolver)
    assert isinstance(request, DraftRequest)
    assert request.pull == (0.0, 0.0, 1.0)
    flipped = build_request(_draft_op(pull=[math.sin(tilt), 0, -math.cos(tilt)]), ctx, resolver)
    assert isinstance(flipped, DraftRequest)
    assert flipped.pull == (0.0, 0.0, -1.0)


def test_a_pull_that_is_not_perpendicular_to_the_neutral_plane_is_an_actionable_error() -> None:
    _, ctx, resolver = _env()
    with pytest.raises(ResolutionError) as info:
        build_request(_draft_op(pull="X"), ctx, resolver)
    message = str(info.value)
    assert info.value.path == "pull"
    assert "pull must be perpendicular to the neutral plane" in message
    assert "90" in message  # the angle it makes with the plane's normal
    assert "(0, 0, 1)" in message  # the normal to pull along


def test_a_pull_two_degrees_off_the_neutral_normal_is_refused() -> None:
    _, ctx, resolver = _env()
    tilt = math.radians(2.0)
    with pytest.raises(ResolutionError):
        build_request(_draft_op(pull=[math.sin(tilt), 0, math.cos(tilt)]), ctx, resolver)


def test_draft_refuses_faces_of_different_bodies_and_a_face_named_twice() -> None:
    _, ctx, resolver = _env(second_body=True)
    with pytest.raises(ResolutionError) as info:
        build_request(_draft_op(faces=[XMAX, XMIN]), ctx, resolver)
    assert "draft faces belong to different bodies" in str(info.value)
    assert info.value.path == "faces[1]"
    _, ctx, resolver = _env()
    with pytest.raises(ResolutionError) as info:
        build_request(_draft_op(faces=[XMAX, XMAX]), ctx, resolver)
    assert "twice" in str(info.value)
    assert info.value.path == "faces[1]"


def test_draft_reports_an_unresolvable_face_through_the_resolver() -> None:
    _, ctx, resolver = _env()
    missing = {"kind": "face", "from_op": "base", "select": {"normal": [1, 1, 0]}}
    with pytest.raises(ResolutionError) as info:
        build_request(_draft_op(faces=[missing]), ctx, resolver)
    assert info.value.path == "faces[0]"


@pytest.mark.parametrize("pull", ["Z", [0, 0, -1]])
@pytest.mark.parametrize("faces", [[TOP], [XMAX, TOP], [TOP, XMAX]])
def test_a_face_facing_the_pull_is_refused_alone_or_among_drafted_ones(pull: object, faces: list[dict]) -> None:
    """1.1.3 fails the whole feature and 1.0.2 silently leaves the face undrafted while drafting the others."""
    _, ctx, resolver = _env()
    with pytest.raises(ResolutionError) as info:
        build_request(_draft_op(faces=faces, pull=pull), ctx, resolver)
    index = faces.index(TOP)
    assert info.value.path == f"faces[{index}]"
    message = str(info.value)
    assert "faces the pull" in message and "neutral plane" in message and "face:top" in message


def test_the_face_the_neutral_plane_lies_in_is_refused_too() -> None:
    _, ctx, resolver = _env()
    with pytest.raises(ResolutionError, match="faces the pull"):
        build_request(_draft_op(faces=[XMAX, TOP], neutral=TOP), ctx, resolver)
