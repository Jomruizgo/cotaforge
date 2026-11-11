"""Contract surface of datum planes / axes: handles, requests, results, optional tools."""

import pytest

from cotaforge.contract import (
    Adapter,
    AdapterError,
    DatumAxisHandle,
    DatumAxisRequest,
    DatumAxisResult,
    DatumPlaneHandle,
    DatumPlaneRequest,
    DatumPlaneResult,
    DatumPlaneTarget,
    ErrorCode,
    FaceHandle,
    PlaneName,
    SketchRequest,
    frame_for_plane,
)
from cotaforge.contract.geometry import Axis
from cotaforge.contract.sketch import RectEntity
from cotaforge.contract.toolschema import TOOL_REGISTRY, Group
from tests.unit.contract.test_adapter import _MinimalAdapter


def test_datum_tools_are_optional_construction_tools() -> None:
    plane = TOOL_REGISTRY["datum_plane"]
    axis = TOOL_REGISTRY["datum_axis"]
    assert (plane.group, plane.optional, plane.request_model, plane.response_model) == (
        Group.CONSTRUCTION,
        True,
        DatumPlaneRequest,
        DatumPlaneResult,
    )
    assert (axis.group, axis.optional, axis.request_model, axis.response_model) == (
        Group.CONSTRUCTION,
        True,
        DatumAxisRequest,
        DatumAxisResult,
    )


def test_unoverridden_datum_tools_raise_actionable_not_supported_and_are_not_capable() -> None:
    adapter = _MinimalAdapter()
    request = DatumPlaneRequest(operation_id="dp", how="offset", frame=frame_for_plane(PlaneName.XY))
    with pytest.raises(AdapterError) as info:
        adapter.datum_plane(request)
    assert info.value.info.code is ErrorCode.not_supported
    assert info.value.info.recoverable is False
    assert "ref_plane" in info.value.info.message
    with pytest.raises(AdapterError) as info:
        adapter.datum_axis(
            DatumAxisRequest(
                operation_id="da", how="two_points", axis=Axis(origin=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0))
            )
        )
    assert "ref_axis" in info.value.info.message
    assert not {"datum_plane", "datum_axis"} & adapter.capabilities().tools
    assert Adapter.datum_plane is not None


def test_datum_handles_are_distinct_handle_types() -> None:
    assert DatumPlaneHandle(id="datum_plane:1").id == "datum_plane:1"
    assert DatumAxisHandle(id="datum_axis:1").id == "datum_axis:1"


def test_sketch_request_on_a_datum_plane_round_trips_through_json() -> None:
    target = DatumPlaneTarget(handle=DatumPlaneHandle(id="datum_plane:2"), frame=frame_for_plane(PlaneName.XZ))
    rect = RectEntity(name="r", center=(0.0, 0.0), width=4.0, height=2.0)
    request = SketchRequest(operation_id="s", on=target, entities=(rect,))
    again = SketchRequest.model_validate_json(request.model_dump_json())
    assert again == request
    assert isinstance(again.on, DatumPlaneTarget)
    assert again.on.frame.v == (0.0, 0.0, -1.0)


def test_sketch_request_still_accepts_plane_names_and_faces() -> None:
    rect = RectEntity(name="r", center=(0.0, 0.0), width=4.0, height=2.0)
    plane = SketchRequest.model_validate_json(
        SketchRequest(operation_id="s", on=PlaneName.XY, entities=(rect,)).model_dump_json()
    )
    face = SketchRequest.model_validate_json(
        SketchRequest(operation_id="s", on=FaceHandle(id="face:1"), entities=(rect,)).model_dump_json()
    )
    assert plane.on == PlaneName.XY
    assert face.on == FaceHandle(id="face:1")
