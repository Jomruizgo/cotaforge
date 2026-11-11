"""The fake adapter does not model a draft: a tapered primitive would need its own faces, outline and booleans, and
a draft it approximated would verify against geometry the real kernels never produce. It says so instead."""

import pytest

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import AdapterError, DraftRequest, ErrorCode, FaceHandle, PlaneName, frame_for_plane


def test_the_fake_reports_draft_as_not_supported_with_the_fix() -> None:
    adapter = FakeAdapter()
    request = DraftRequest(
        operation_id="dr",
        faces=(FaceHandle(id="face:1"),),
        neutral=frame_for_plane(PlaneName.XY),
        pull=(0.0, 0.0, 1.0),
        angle_deg=3.0,
    )
    with pytest.raises(AdapterError) as info:
        adapter.draft(request)
    assert info.value.info.code is ErrorCode.not_supported
    assert "draft" in info.value.info.message
    assert "draft" not in adapter.capabilities().tools
