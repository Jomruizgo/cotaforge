"""The optional ``edit_feature`` tool: edit the properties of EXISTING features in place and let the CAD recompute
their dependents. The request carries the FULL new contract request of each op (built by the
same builders the executor uses), never property names, so the contract stays CAD-neutral."""

import pytest
from pydantic import ValidationError

from cotaforge.contract import (
    REQUEST_BY_KIND,
    TOOL_REGISTRY,
    Adapter,
    AdapterError,
    Axis,
    BodyHandle,
    BoxRequest,
    CylinderRequest,
    EditFeatureRequest,
    EditResult,
    ErrorCode,
    ExtensionRequest,
    FaceHandle,
    FeatureEdit,
    FeatureHandle,
    Group,
    HoleRequest,
    MateDistanceRequest,
    MoveBodyRequest,
    Transform,
    export_mcp_tools,
    kind_of_request,
)


def _box_edit(feature: str = "feature:1", size: tuple[float, float, float] = (10.0, 20.0, 30.0)) -> FeatureEdit:
    return FeatureEdit(feature=FeatureHandle(id=feature), kind="box", request=BoxRequest(operation_id="b", size=size))


def test_edit_feature_is_an_optional_construction_tool() -> None:
    spec = TOOL_REGISTRY["edit_feature"]
    assert spec.optional is True
    assert spec.group is Group.CONSTRUCTION
    assert spec.request_model is EditFeatureRequest
    assert spec.response_model is EditResult
    assert "edit_feature" in {t["name"] for t in export_mcp_tools(include_optional=True)}
    assert "edit_feature" not in {t["name"] for t in export_mcp_tools()}


def test_a_request_round_trips_through_json_with_its_typed_requests() -> None:
    cylinder = FeatureEdit(
        feature=FeatureHandle(id="feature:2"),
        kind="cylinder",
        request=CylinderRequest(
            operation_id="c", diameter_mm=8.0, height_mm=40.0, axis=Axis(origin=(1, 2, 3), direction=(0, 0, 1))
        ),
    )
    move = FeatureEdit(
        feature=FeatureHandle(id="feature:3"),
        kind="move_body",
        request=MoveBodyRequest(operation_id="m", body=BodyHandle(id="body:1"), transform=Transform(origin=(5, 0, 0))),
    )
    request = EditFeatureRequest(operation_id="edit", edits=(_box_edit(), cylinder, move))

    data = request.model_dump(mode="json")
    assert data["dry_run"] is False
    assert [e["kind"] for e in data["edits"]] == ["box", "cylinder", "move_body"]
    assert data["edits"][1]["request"]["diameter_mm"] == 8.0  # the subclass fields survive the base-typed field

    again = EditFeatureRequest.model_validate(data)
    assert again == request
    assert isinstance(again.edits[0].request, BoxRequest)
    assert isinstance(again.edits[1].request, CylinderRequest)
    assert isinstance(again.edits[2].request, MoveBodyRequest)


def test_a_request_needs_at_least_one_edit_and_distinct_features() -> None:
    with pytest.raises(ValidationError):
        EditFeatureRequest(operation_id="edit", edits=())
    with pytest.raises(ValidationError, match="more than once"):
        EditFeatureRequest(operation_id="edit", edits=(_box_edit("feature:1"), _box_edit("feature:1")))


def test_the_kind_must_match_the_request_type() -> None:
    with pytest.raises(ValidationError, match="does not match"):
        FeatureEdit(
            feature=FeatureHandle(id="feature:1"), kind="cylinder", request=BoxRequest(operation_id="b", size=(1, 1, 1))
        )
    with pytest.raises(ValidationError, match="unknown"):
        FeatureEdit.model_validate(
            {"feature": {"id": "feature:1"}, "kind": "teleport", "request": {"operation_id": "t"}}
        )


def test_an_operation_the_adapter_declared_is_edited_with_its_extension_request() -> None:
    own = ExtensionRequest(operation_id="h", name="holes", params={"width_mm": 40, "diameter_mm": 6})
    edit = FeatureEdit(feature=FeatureHandle(id="feature:4"), kind="holes", request=own)
    assert kind_of_request(own) == "holes"
    request = EditFeatureRequest(operation_id="edit", edits=(_box_edit(), edit))

    data = request.model_dump(mode="json")
    assert data["edits"][1]["request"]["params"] == {"width_mm": 40, "diameter_mm": 6}  # params survive the base type
    again = EditFeatureRequest.model_validate(data)
    assert again == request and type(again.edits[1].request) is ExtensionRequest
    assert isinstance(again.edits[0].request, BoxRequest)  # the common kinds are read as before


def test_an_extension_that_replaces_a_common_name_is_told_apart_by_its_request() -> None:
    own = ExtensionRequest(operation_id="h", name="hole", params={"style": "simple"})
    edit = FeatureEdit(feature=FeatureHandle(id="feature:4"), kind="hole", request=own)
    again = FeatureEdit.model_validate(edit.model_dump(mode="json"))
    assert type(again.request) is ExtensionRequest and again == edit
    common = FeatureEdit.model_validate(
        {
            "feature": {"id": "feature:5"},
            "kind": "hole",
            "request": {
                "operation_id": "c",
                "face": {"id": "face:1"},
                "position": [1, 2],
                "standard": "simple",
                "diameter_mm": 5,
                "end_condition": "through_all",
            },
        }
    )
    assert type(common.request) is HoleRequest


def test_the_kind_of_an_extension_edit_is_the_name_of_its_operation() -> None:
    own = ExtensionRequest(operation_id="h", name="holes")
    with pytest.raises(ValidationError, match="does not match the operation 'holes'"):
        FeatureEdit(feature=FeatureHandle(id="feature:4"), kind="drill", request=own)
    with pytest.raises(ValidationError, match="unknown"):
        FeatureEdit.model_validate({"feature": {"id": "feature:4"}, "kind": "holes", "request": {"operation_id": "h"}})


def test_dry_run_is_part_of_the_request() -> None:
    assert EditFeatureRequest(operation_id="e", edits=(_box_edit(),), dry_run=True).dry_run is True


def test_a_result_describes_what_happened_in_neutral_terms() -> None:
    result = EditResult(rebuild_ok=True, applied=True)
    assert result.refused == {} and result.recomputed == () and result.flags == () and result.health == ()
    assert result.refs.created == () and result.refs.removed == () and result.refs.modified == ()
    assert result.failure is None and result.failure_kind is None and result.duration_ms == 0.0
    assert EditResult(rebuild_ok=False, applied=False, failure="x", failure_kind="semantic").failure_kind == "semantic"
    with pytest.raises(ValidationError):
        EditResult(rebuild_ok=False, applied=False, failure="x", failure_kind="whatever")  # type: ignore[arg-type]


def test_the_default_adapter_method_refuses_with_not_supported() -> None:
    with pytest.raises(AdapterError) as info:
        Adapter.edit_feature(None, EditFeatureRequest(operation_id="e", edits=(_box_edit(),)))  # type: ignore[arg-type]
    assert info.value.info.code is ErrorCode.not_supported
    assert "rebuild" in info.value.info.message


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_a_request_with_a_non_finite_number_is_rejected(bad: float) -> None:
    from cotaforge.contract import Axis, BoxRequest, CylinderRequest

    with pytest.raises(ValidationError):
        BoxRequest(operation_id="x", size=(bad, 1.0, 1.0))
    axis = Axis(origin=(0.0, 0.0, 0.0), direction=(0.0, 0.0, 1.0))
    with pytest.raises(ValidationError):
        CylinderRequest(operation_id="x", diameter_mm=bad, height_mm=1.0, axis=axis)


def test_measure_bodies_is_an_optional_read_only_query_tool() -> None:
    from cotaforge.contract import Empty, MeasureBodiesResult

    spec = TOOL_REGISTRY["measure_bodies"]
    assert spec.optional is True and spec.group is Group.QUERY
    assert spec.request_model is Empty and spec.response_model is MeasureBodiesResult
    assert "measure_bodies" not in {t["name"] for t in export_mcp_tools()}
    with pytest.raises(AdapterError) as info:
        Adapter.measure_bodies(None, Empty(operation_id="m"))  # type: ignore[arg-type]
    assert info.value.info.code is ErrorCode.not_supported


def test_body_interference_is_an_optional_read_only_query_tool() -> None:
    from cotaforge.contract import BodyInterferenceRequest, BodyInterferenceResult, BodyPairReading

    spec = TOOL_REGISTRY["body_interference"]
    assert spec.optional is True and spec.group is Group.QUERY
    assert spec.request_model is BodyInterferenceRequest and spec.response_model is BodyInterferenceResult
    with pytest.raises(AdapterError) as info:
        Adapter.body_interference(None, BodyInterferenceRequest(operation_id="i"))  # type: ignore[arg-type]
    assert info.value.info.code is ErrorCode.not_supported
    unmeasured = BodyPairReading(
        a=BodyHandle(id="body:1"), b=BodyHandle(id="body:2"), common_mm3=0.0, distance_mm=None, note="why"
    )
    result = BodyInterferenceResult.model_validate(BodyInterferenceResult(pairs=(unmeasured,)).model_dump(mode="json"))
    assert result.pairs[0].distance_mm is None and result.pairs[0].note == "why"  # not measured is never a 0


def test_a_body_measurement_round_trips_through_json() -> None:
    from cotaforge.contract import (
        BodyMeasure,
        BodyReading,
        BoundingBox,
        CutReading,
        MeasureBodiesResult,
        OverlapReading,
    )

    box = BoundingBox(min=(0.0, 0.0, 0.0), max=(1.0, 2.0, 3.0))
    result = MeasureBodiesResult(
        bodies=(
            BodyReading(
                body=BodyHandle(id="body:1"),
                label="Fuse",
                measure=BodyMeasure(volume_mm3=6.0, bbox=box, solids=1, faces=6, edges=12),
            ),
        ),
        cuts=(CutReading(feature=FeatureHandle(id="feature:3"), label="Cut", removed_mm3=1.5),),
        overlaps=(OverlapReading(a=BodyHandle(id="body:1"), b=BodyHandle(id="body:2"), volume_mm3=0.25),),
    )
    assert MeasureBodiesResult.model_validate_json(result.model_dump_json()) == result
    assert MeasureBodiesResult().bodies == () and MeasureBodiesResult().warnings == ()


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_nested_models_of_a_request_refuse_non_finite_numbers_too(bad: float) -> None:
    """``allow_inf_nan`` is a per-model setting: the base ``Request`` does not reach the models nested in it."""
    from cotaforge.contract import (
        Axis,
        BodyHandle,
        CircleEntity,
        CylinderRequest,
        LineEntity,
        MoveBodyRequest,
        SketchFrame,
        Transform,
    )

    with pytest.raises(ValidationError):
        Transform(origin=(bad, 0.0, 0.0))
    with pytest.raises(ValidationError):
        MoveBodyRequest(operation_id="m", body=BodyHandle(id="b"), transform=Transform(origin=(0.0, bad, 0.0)))
    with pytest.raises(ValidationError):
        Transform(origin=(0.0, 0.0, 0.0), rotation=((bad, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)))
    with pytest.raises(ValidationError):
        Axis(origin=(bad, 0.0, 0.0), direction=(0.0, 0.0, 1.0))
    with pytest.raises(ValidationError):
        CylinderRequest(
            operation_id="c",
            diameter_mm=1.0,
            height_mm=1.0,
            axis={"origin": (0, bad, 0), "direction": (0, 0, 1)},  # type: ignore[arg-type]
        )
    with pytest.raises(ValidationError):
        SketchFrame(origin=(bad, 0.0, 0.0), u=(1.0, 0.0, 0.0), v=(0.0, 1.0, 0.0), n=(0.0, 0.0, 1.0))
    with pytest.raises(ValidationError):
        CircleEntity(name="c", center=(bad, 0.0), diameter=1.0)
    with pytest.raises(ValidationError):
        LineEntity(name="l", start=(0.0, 0.0), end=(bad, 1.0))


def test_a_mate_distance_is_an_edit_kind_and_round_trips_through_json() -> None:

    assert REQUEST_BY_KIND["mate_distance"] is MateDistanceRequest
    mate = FeatureEdit(
        feature=FeatureHandle(id="feature:7"),
        kind="mate_distance",
        request=MateDistanceRequest(
            operation_id="m", a=FaceHandle(id="face:1"), b=FaceHandle(id="face:2"), distance_mm=3
        ),
    )
    again = EditFeatureRequest.model_validate(
        EditFeatureRequest(operation_id="e", edits=(mate,)).model_dump(mode="json")
    )
    assert isinstance(again.edits[0].request, MateDistanceRequest) and again.edits[0].request.distance_mm == 3
    with pytest.raises(ValidationError):  # the kind and the request must still agree
        FeatureEdit(feature=FeatureHandle(id="feature:7"), kind="mate_distance", request=_box_edit().request)


def test_every_document_kind_has_the_suffix_of_the_adapter_unless_it_says_otherwise() -> None:
    from cotaforge.adapters.fake import FakeAdapter
    from cotaforge.contract import DocumentKind

    fake = FakeAdapter()
    assert {fake.suffix_for(kind) for kind in DocumentKind} == {FakeAdapter.document_suffix}


def test_an_adapter_without_its_own_guide_says_nothing_and_so_the_server_default_stays() -> None:
    from cotaforge.adapters.fake import FakeAdapter

    assert FakeAdapter().guide_topic("drawing") is None and FakeAdapter().guide_topic("note:joint") is None
