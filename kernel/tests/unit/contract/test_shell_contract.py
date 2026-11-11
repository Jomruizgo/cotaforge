"""Contract tests for ``shell``: ``ShellRequest`` and the optional construction tool, default not_supported."""

import pytest
from pydantic import ValidationError

from cotaforge.contract import (
    AdapterError,
    ErrorCode,
    FaceHandle,
    OperationResult,
    ShellRequest,
)
from cotaforge.contract.toolschema import TOOL_REGISTRY, Group
from tests.unit.contract.test_adapter import _MinimalAdapter

TOP = FaceHandle(id="face:top")


def _request(**overrides: object) -> ShellRequest:
    fields: dict = {"operation_id": "sh", "faces": (TOP,), "thickness_mm": 2.0}
    fields.update(overrides)
    return ShellRequest(**fields)


def test_shell_is_an_optional_construction_tool() -> None:
    spec = TOOL_REGISTRY["shell"]
    assert (spec.group, spec.optional, spec.request_model, spec.response_model) == (
        Group.CONSTRUCTION,
        True,
        ShellRequest,
        OperationResult,
    )


def test_shell_request_defaults_and_json_round_trip() -> None:
    request = _request()
    assert request.outward is False
    assert request.join == "intersection"
    assert ShellRequest.model_validate_json(_request(outward=True, join="arc").model_dump_json()) == _request(
        outward=True, join="arc"
    )


def test_shell_request_needs_a_face_a_positive_thickness_and_a_known_join() -> None:
    with pytest.raises(ValidationError):
        _request(faces=())
    with pytest.raises(ValidationError):
        _request(thickness_mm=0.0)
    with pytest.raises(ValidationError):
        _request(thickness_mm=-1.0)
    with pytest.raises(ValidationError):
        _request(join="round")


def test_unoverridden_shell_raises_actionable_not_supported_and_is_not_capable() -> None:
    adapter = _MinimalAdapter()
    with pytest.raises(AdapterError) as info:
        adapter.shell(_request())
    assert info.value.info.code is ErrorCode.not_supported
    assert info.value.info.recoverable is False
    assert "shell" in info.value.info.message
    assert "extrude" in info.value.info.message
    assert "shell" not in adapter.capabilities().tools
