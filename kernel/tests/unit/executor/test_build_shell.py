"""``build_shell``: request building and the refusals that run before any adapter call."""

import pytest

from cotaforge.contract import BodyHandle, DocumentHandle, FaceHandle, FeatureHandle, ShellRequest
from cotaforge.executor.build import DISPATCH, build_request
from cotaforge.executor.context import ExecutionContext
from cotaforge.executor.errors import ResolutionError
from cotaforge.executor.resolver import Resolver
from cotaforge.ir import OperationBase, parse_plan
from cotaforge.verification.thresholds import Thresholds
from tests.unit.verification.helpers import plate_faces

from .stub import StubAdapter

TOP = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}
BOTTOM = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, -1]}}


def _env(*, second_body: bool = False) -> tuple[StubAdapter, ExecutionContext, Resolver]:
    stub = StubAdapter()
    for face in plate_faces("feature:1"):
        if second_body and face.handle.id == "face:bottom":
            face = face.model_copy(update={"body": BodyHandle(id="body:2")})
        stub.add_face(face, feature_id="feature:1")
    ctx = ExecutionContext(document=DocumentHandle(id="doc:1"))
    ctx.feature_by_op["base"] = FeatureHandle(id="feature:1")
    return stub, ctx, Resolver(stub, ctx, Thresholds())


def _shell_op(faces: list[dict] | None = None, **extra: object) -> OperationBase:
    plan = parse_plan(
        {
            "id": "p",
            "kind": "part",
            "operations": [
                {"id": "base", "op": "box", "intent": "block", "size": [100, 60, 8]},
                {
                    "id": "sh",
                    "op": "shell",
                    "intent": "hollow it",
                    "faces": faces if faces is not None else [TOP],
                    "thickness_mm": 2,
                    **extra,
                },
            ],
        }
    )
    return plan.operations[1]


def test_dispatch_routes_shell_to_the_shell_tool() -> None:
    assert DISPATCH["shell"][0] == "shell"


def test_shell_builds_a_request_with_the_resolved_faces() -> None:
    _, ctx, resolver = _env()
    request = build_request(_shell_op([TOP, BOTTOM]), ctx, resolver)
    assert isinstance(request, ShellRequest)
    assert request.operation_id == "sh"
    assert request.faces == (FaceHandle(id="face:top"), FaceHandle(id="face:bottom"))
    assert (request.thickness_mm, request.outward, request.join) == (2.0, False, "intersection")
    assert resolver.resolved_refs["faces[0]"] == "face:top"
    assert resolver.resolved_refs["faces[1]"] == "face:bottom"


def test_shell_outward_and_arc_reach_the_request() -> None:
    _, ctx, resolver = _env()
    request = build_request(_shell_op(direction="outward", join="arc"), ctx, resolver)
    assert isinstance(request, ShellRequest)
    assert (request.outward, request.join) == (True, "arc")


def test_shell_refuses_faces_of_different_bodies() -> None:
    _, ctx, resolver = _env(second_body=True)
    with pytest.raises(ResolutionError) as info:
        build_request(_shell_op([TOP, BOTTOM]), ctx, resolver)
    assert "shell faces belong to different bodies" in str(info.value)
    assert info.value.path == "faces[1]"


def test_shell_refuses_the_same_face_twice() -> None:
    _, ctx, resolver = _env()
    with pytest.raises(ResolutionError) as info:
        build_request(_shell_op([TOP, TOP]), ctx, resolver)
    assert "twice" in str(info.value)
    assert info.value.path == "faces[1]"


def test_shell_reports_an_unresolvable_face_through_the_resolver() -> None:
    _, ctx, resolver = _env()
    missing = {"kind": "face", "from_op": "base", "select": {"normal": [1, 1, 0]}}
    with pytest.raises(ResolutionError) as info:
        build_request(_shell_op([missing]), ctx, resolver)
    assert info.value.path == "faces[0]"
