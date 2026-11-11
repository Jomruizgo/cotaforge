"""Contract surface of ``sweep``: request model, optional construction tool, default not_supported."""

import pytest
from pydantic import ValidationError

from cotaforge.contract import (
    AdapterError,
    ErrorCode,
    OperationResult,
    SketchHandle,
    SweepRequest,
)
from cotaforge.contract.toolschema import TOOL_REGISTRY, Group
from tests.unit.contract.test_adapter import _MinimalAdapter

PATH = ((0.0, 0.0, 0.0), (0.0, 0.0, 100.0), (100.0, 0.0, 100.0))


def _request(**overrides: object) -> SweepRequest:
    fields: dict = {"operation_id": "sw", "sketch": SketchHandle(id="sketch:1"), "path": PATH}
    fields.update(overrides)
    return SweepRequest(**fields)


def test_sweep_is_an_optional_construction_tool() -> None:
    spec = TOOL_REGISTRY["sweep"]
    assert (spec.group, spec.optional, spec.request_model, spec.response_model) == (
        Group.CONSTRUCTION,
        True,
        SweepRequest,
        OperationResult,
    )


def test_sweep_request_defaults_and_json_round_trip() -> None:
    request = _request()
    assert request.corner_radius_mm is None
    assert request.merge is True
    assert SweepRequest.model_validate_json(_request(corner_radius_mm=20.0, merge=False).model_dump_json()) == _request(
        corner_radius_mm=20.0, merge=False
    )


def test_sweep_request_needs_a_sketch_and_at_least_two_path_points() -> None:
    with pytest.raises(ValidationError):
        SweepRequest(operation_id="sw", path=PATH)  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        _request(path=((0.0, 0.0, 0.0),))
    with pytest.raises(ValidationError):
        _request(corner_radius_mm=0.0)


def test_unoverridden_sweep_raises_actionable_not_supported_and_is_not_capable() -> None:
    adapter = _MinimalAdapter()
    with pytest.raises(AdapterError) as info:
        adapter.sweep(_request())
    assert info.value.info.code is ErrorCode.not_supported
    assert info.value.info.recoverable is False
    assert "sweep" in info.value.info.message
    assert "extrude/revolve" in info.value.info.message
    assert "sweep" not in adapter.capabilities().tools
