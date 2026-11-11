"""Contract tests for ``mirror`` / ``mirror_body``: the requests and the optional tools, default not_supported."""

import pytest
from pydantic import ValidationError

from cotaforge.contract import (
    AdapterError,
    BodyHandle,
    ErrorCode,
    FeatureHandle,
    MirrorBodyRequest,
    MirrorRequest,
    OperationResult,
    PlaneName,
    frame_for_plane,
)
from cotaforge.contract.toolschema import TOOL_REGISTRY, Group
from tests.unit.contract.test_adapter import _MinimalAdapter

BOSS = FeatureHandle(id="feature:boss")
BLOCK = BodyHandle(id="body:block")


def _mirror(**overrides: object) -> MirrorRequest:
    fields: dict = {"operation_id": "m", "features": (BOSS,), "plane": frame_for_plane(PlaneName.YZ)}
    fields.update(overrides)
    return MirrorRequest(**fields)


def _mirror_body(**overrides: object) -> MirrorBodyRequest:
    fields: dict = {"operation_id": "mb", "source": BLOCK, "plane": frame_for_plane(PlaneName.XZ)}
    fields.update(overrides)
    return MirrorBodyRequest(**fields)


@pytest.mark.parametrize(
    ("name", "model"),
    [("mirror", MirrorRequest), ("mirror_body", MirrorBodyRequest)],
)
def test_both_are_optional_construction_tools(name: str, model: type) -> None:
    spec = TOOL_REGISTRY[name]
    assert (spec.group, spec.optional, spec.request_model, spec.response_model) == (
        Group.CONSTRUCTION,
        True,
        model,
        OperationResult,
    )


def test_requests_round_trip_json_and_keep_the_plane_frame() -> None:
    for request in (_mirror(), _mirror_body()):
        again = type(request).model_validate_json(request.model_dump_json())
        assert again == request
    assert _mirror().plane.n == (1.0, 0.0, 0.0)
    assert _mirror_body().plane.n == (0.0, 1.0, 0.0)


def test_a_mirror_needs_at_least_one_feature() -> None:
    with pytest.raises(ValidationError):
        _mirror(features=())


@pytest.mark.parametrize("name", ["mirror", "mirror_body"])
def test_unoverridden_tools_raise_actionable_not_supported_and_are_not_capable(name: str) -> None:
    adapter = _MinimalAdapter()
    request = _mirror() if name == "mirror" else _mirror_body()
    with pytest.raises(AdapterError) as info:
        getattr(adapter, name)(request)
    assert info.value.info.code is ErrorCode.not_supported
    assert info.value.info.recoverable is False
    assert name in info.value.info.message
    assert name not in adapter.capabilities().tools
