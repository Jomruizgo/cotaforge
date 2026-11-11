"""Contract tests for ``split`` and the scaled ``copy_body``: the requests and the optional tool."""

import pytest
from pydantic import ValidationError

from cotaforge.contract import (
    AdapterError,
    BodyHandle,
    CopyBodyRequest,
    ErrorCode,
    OperationResult,
    PlaneName,
    SplitRequest,
    Transform,
    frame_for_plane,
)
from cotaforge.contract.toolschema import TOOL_REGISTRY, Group
from tests.unit.contract.test_adapter import _MinimalAdapter

BLOCK = BodyHandle(id="body:block")


def _split(**overrides: object) -> SplitRequest:
    fields: dict = {"operation_id": "sp", "source": BLOCK, "plane": frame_for_plane(PlaneName.XY)}
    fields.update(overrides)
    return SplitRequest(**fields)


def test_split_is_an_optional_construction_tool() -> None:
    spec = TOOL_REGISTRY["split"]
    assert (spec.group, spec.optional, spec.request_model, spec.response_model) == (
        Group.CONSTRUCTION,
        True,
        SplitRequest,
        OperationResult,
    )


def test_split_keeps_both_pieces_by_default_and_round_trips() -> None:
    request = _split()
    assert request.keep == "both"
    assert SplitRequest.model_validate_json(request.model_dump_json()) == request
    assert _split(keep="negative").keep == "negative"
    assert request.plane.n == (0.0, 0.0, 1.0)


def test_split_rejects_an_unknown_keep() -> None:
    with pytest.raises(ValidationError):
        _split(keep="top")


def test_the_unoverridden_split_raises_an_actionable_not_supported_and_is_not_capable() -> None:
    adapter = _MinimalAdapter()
    with pytest.raises(AdapterError) as info:
        adapter.split(_split())
    assert info.value.info.code is ErrorCode.not_supported
    assert info.value.info.recoverable is False
    assert "split" in info.value.info.message
    assert "split" not in adapter.capabilities().tools


def test_copy_body_scale_defaults_to_one_about_the_origin() -> None:
    request = CopyBodyRequest(operation_id="c", source=BLOCK, transform=Transform())
    assert request.scale == 1.0
    assert request.scale_about == (0.0, 0.0, 0.0)


@pytest.mark.parametrize("scale", [0.0, -2.0, float("nan"), float("inf")])
def test_copy_body_scale_must_be_a_finite_positive_number(scale: float) -> None:
    with pytest.raises(ValidationError):
        CopyBodyRequest(operation_id="c", source=BLOCK, transform=Transform(), scale=scale)


def test_a_scaled_copy_request_round_trips() -> None:
    request = CopyBodyRequest(
        operation_id="c", source=BLOCK, transform=Transform(origin=(60.0, 0.0, 0.0)), scale=1.006, scale_about=(1, 2, 3)
    )
    assert CopyBodyRequest.model_validate_json(request.model_dump_json()) == request
