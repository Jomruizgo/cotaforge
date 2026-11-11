"""Contract tests for ``draft``: ``DraftRequest`` and the optional construction tool, default not_supported."""

import pytest
from pydantic import ValidationError

from cotaforge.contract import (
    AdapterError,
    DraftRequest,
    ErrorCode,
    FaceHandle,
    OperationResult,
    PlaneName,
    frame_for_plane,
)
from cotaforge.contract.toolschema import TOOL_REGISTRY, Group
from tests.unit.contract.test_adapter import _MinimalAdapter

SIDE = FaceHandle(id="face:side")


def _request(**overrides: object) -> DraftRequest:
    fields: dict = {
        "operation_id": "dr",
        "faces": (SIDE,),
        "neutral": frame_for_plane(PlaneName.XY),
        "pull": (0.0, 0.0, 1.0),
        "angle_deg": 3.0,
    }
    fields.update(overrides)
    return DraftRequest(**fields)


def test_draft_is_an_optional_construction_tool() -> None:
    spec = TOOL_REGISTRY["draft"]
    assert (spec.group, spec.optional, spec.request_model, spec.response_model) == (
        Group.CONSTRUCTION,
        True,
        DraftRequest,
        OperationResult,
    )


def test_draft_request_json_round_trip_keeps_the_neutral_frame_and_the_pull() -> None:
    request = _request(pull=(0.0, 0.0, -1.0))
    again = DraftRequest.model_validate_json(request.model_dump_json())
    assert again == request
    assert again.neutral.n == (0.0, 0.0, 1.0)
    assert again.pull == (0.0, 0.0, -1.0)


def test_draft_request_needs_a_face_and_an_angle_in_range() -> None:
    with pytest.raises(ValidationError):
        _request(faces=())
    for angle in (0.0, -1.0, 30.5, float("nan")):
        with pytest.raises(ValidationError):
            _request(angle_deg=angle)
    assert _request(angle_deg=30.0).angle_deg == 30.0


def test_unoverridden_draft_raises_actionable_not_supported_and_is_not_capable() -> None:
    adapter = _MinimalAdapter()
    with pytest.raises(AdapterError) as info:
        adapter.draft(_request())
    assert info.value.info.code is ErrorCode.not_supported
    assert info.value.info.recoverable is False
    assert "draft" in info.value.info.message
    assert "draft" not in adapter.capabilities().tools
