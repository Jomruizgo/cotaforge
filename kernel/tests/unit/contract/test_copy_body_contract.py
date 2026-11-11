"""Contract surface of ``copy_body``: request model, optional construction tool, default not_supported."""

import pytest
from pydantic import ValidationError

from cotaforge.contract import (
    AdapterError,
    BodyHandle,
    CopyBodyRequest,
    ErrorCode,
    OperationResult,
    Transform,
)
from cotaforge.contract.toolschema import TOOL_REGISTRY, Group
from tests.unit.contract.test_adapter import _MinimalAdapter


def test_copy_body_is_an_optional_construction_tool() -> None:
    spec = TOOL_REGISTRY["copy_body"]
    assert (spec.group, spec.optional, spec.request_model, spec.response_model) == (
        Group.CONSTRUCTION,
        True,
        CopyBodyRequest,
        OperationResult,
    )


def test_copy_body_request_round_trips_through_json() -> None:
    request = CopyBodyRequest(
        operation_id="cp", source=BodyHandle(id="body:1"), transform=Transform(origin=(0.0, 100.0, 0.0))
    )
    assert CopyBodyRequest.model_validate_json(request.model_dump_json()) == request


def test_copy_body_request_needs_a_source_and_a_transform() -> None:
    with pytest.raises(ValidationError):
        CopyBodyRequest(operation_id="cp", source=BodyHandle(id="body:1"))  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        CopyBodyRequest(operation_id="cp", transform=Transform.identity())  # type: ignore[call-arg]


def test_unoverridden_copy_body_raises_actionable_not_supported_and_is_not_capable() -> None:
    adapter = _MinimalAdapter()
    request = CopyBodyRequest(operation_id="cp", source=BodyHandle(id="body:1"), transform=Transform.identity())
    with pytest.raises(AdapterError) as info:
        adapter.copy_body(request)
    assert info.value.info.code is ErrorCode.not_supported
    assert info.value.info.recoverable is False
    assert "copy_body" in info.value.info.message
    assert "rebuild the body" in info.value.info.message
    assert "copy_body" not in adapter.capabilities().tools
