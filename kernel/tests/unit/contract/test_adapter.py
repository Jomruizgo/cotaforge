"""Tests for cotaforge.contract.adapter and toolschema: registration, capabilities, export."""

import json

import pytest
from pydantic import BaseModel, ValidationError

from cotaforge.contract.adapter import Adapter, Capabilities, SelectionListener, SelectionProducer
from cotaforge.contract.enums import ErrorCode
from cotaforge.contract.errors import AdapterError
from cotaforge.contract.handles import FaceHandle, FeatureHandle
from cotaforge.contract.requests import CaptureRequest, Empty, Request, ShowEntitiesRequest, SketchStatusRequest
from cotaforge.contract.responses import Ack, SelectionResult, ShowEntitiesResult
from cotaforge.contract.toolschema import TOOL_REGISTRY, Group, export_mcp_tools

_REQUIRED_METHOD_COUNT = 47
_OPTIONAL_METHOD_COUNT = 54  # + place_component + save/open/close_document + run_native + 2 design attributes
# + tool_overlap + body_interference + add_joint, set_joint, joint_info, sweep_joint + export_dxf + sheet metal
# + style_bodies + section_plane + create_drawing + capture_drawing + export_drawing + import_geometry + list_holes
# + annotate_drawing + describe_drawing + list_features, remove_feature, set_feature_properties, focus_model
# + export_parasolid + window + set_reference_visibility


def test_registry_has_every_required_and_optional_method() -> None:
    assert len(TOOL_REGISTRY) == _REQUIRED_METHOD_COUNT + _OPTIONAL_METHOD_COUNT


def test_registry_names_are_unique_snake_case() -> None:
    for name in TOOL_REGISTRY:
        assert name == name.lower()
        assert " " not in name
        assert name.replace("_", "").isalnum()


def test_registry_names_match_adapter_attributes() -> None:
    for name in TOOL_REGISTRY:
        assert hasattr(Adapter, name)


def test_capabilities_not_in_registry() -> None:
    assert "capabilities" not in TOOL_REGISTRY


def test_every_request_model_subclasses_request() -> None:
    for spec in TOOL_REGISTRY.values():
        assert issubclass(spec.request_model, Request)


def test_every_response_model_is_a_pydantic_model() -> None:
    for spec in TOOL_REGISTRY.values():
        assert issubclass(spec.response_model, BaseModel)


def test_every_schema_is_json_serialisable_and_reloadable() -> None:
    for spec in TOOL_REGISTRY.values():
        input_schema = spec.request_model.model_json_schema()
        output_schema = spec.response_model.model_json_schema()
        reloaded_input = json.loads(json.dumps(input_schema))
        reloaded_output = json.loads(json.dumps(output_schema))
        assert reloaded_input == input_schema
        assert reloaded_output == output_schema


def test_export_mcp_tools_excludes_optional_by_default() -> None:
    tools = export_mcp_tools()
    assert len(tools) == _REQUIRED_METHOD_COUNT
    assert all(not t["annotations"]["optional"] for t in tools)


def test_export_mcp_tools_includes_optional_when_asked() -> None:
    tools = export_mcp_tools(include_optional=True)
    assert len(tools) == _REQUIRED_METHOD_COUNT + _OPTIONAL_METHOD_COUNT


def test_export_mcp_tools_is_deterministic_sorted_by_group_then_name() -> None:
    tools = export_mcp_tools(include_optional=True)
    keys = [(t["annotations"]["group"], t["name"]) for t in tools]
    assert keys == sorted(keys)


def test_export_mcp_tools_json_writes_a_file(tmp_path: "object") -> None:
    from cotaforge.contract.toolschema import export_mcp_tools_json

    path = str(tmp_path) + "/tools.json"  # type: ignore[operator]
    export_mcp_tools_json(path)
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    assert len(data) == _REQUIRED_METHOD_COUNT + _OPTIONAL_METHOD_COUNT


class _MinimalAdapter(Adapter):
    """A concrete Adapter that implements only the required methods, none of the optional ones."""

    name = "minimal"

    def connect(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def disconnect(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def new_document(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def set_active_document(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def active_document(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def set_timeout(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def suppress_interaction(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def restore_interaction(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def canary(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def validate_api(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def sketch(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def extrude(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def cut(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def revolve(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def hole(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def linear_pattern(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def circular_pattern(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def chamfer(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def fillet(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def box(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def cylinder(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def boolean(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def move_body(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def insert_component(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def fix_component(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def mate_concentric(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def mate_coincident(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def mate_distance(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def check_interference(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def component_bounding_box(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def component_transform(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def faces_of(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def edges_between(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def edges_of(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def vertices_of(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def vertex_at(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def bodies_of(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def is_valid(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def mass_properties(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def bounding_box(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def feature_tree(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def face_info(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def edge_info(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def persistent_reference(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def resolve_reference(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def signature(self, request: Request) -> BaseModel:
        raise NotImplementedError

    def undo_last(self, request: Request) -> BaseModel:
        raise NotImplementedError


class _OverridingAdapter(_MinimalAdapter):
    """Overrides the `capture` optional tool only."""

    name = "overriding"

    def capture(self, request: CaptureRequest) -> Ack:
        return Ack()


def test_minimal_adapter_cannot_be_instantiated_if_abstract_methods_missing() -> None:
    with pytest.raises(TypeError):

        class _Incomplete(Adapter):
            name = "incomplete"

        _Incomplete()  # type: ignore[abstract]


def test_default_capabilities_exclude_unoverridden_optional_tools() -> None:
    adapter = _MinimalAdapter()
    caps = adapter.capabilities()
    assert isinstance(caps, Capabilities)
    assert "capture" not in caps.tools
    assert "sketch_status" not in caps.tools  # an optional tool no adapter has to provide
    assert "connect" in caps.tools  # required tools always present


def test_sketch_status_is_registered_optional_query() -> None:
    spec = TOOL_REGISTRY["sketch_status"]
    assert spec.group is Group.QUERY
    assert spec.optional is True


def test_default_sketch_status_raises_not_supported_and_is_not_capable() -> None:
    """An adapter that does not override ``sketch_status`` answers with ``not_supported``: the
    optional-tool contract needs no change on an adapter that cannot report it."""
    adapter = _MinimalAdapter()
    with pytest.raises(AdapterError) as exc_info:
        adapter.sketch_status(SketchStatusRequest(operation_id="op", sketch=FeatureHandle(id="feature:1")))
    assert exc_info.value.info.code == ErrorCode.not_supported
    assert exc_info.value.info.recoverable is False
    assert "sketch_status" not in adapter.capabilities().tools


def test_overridden_optional_tool_appears_in_capabilities() -> None:
    adapter = _OverridingAdapter()
    caps = adapter.capabilities()
    assert "capture" in caps.tools
    assert "set_view" not in caps.tools  # still not overridden


def test_default_optional_method_raises_not_supported() -> None:
    adapter = _MinimalAdapter()
    with pytest.raises(AdapterError) as exc_info:
        adapter.capture(CaptureRequest(operation_id="op", path="x.png"))
    assert exc_info.value.info.code == ErrorCode.not_supported
    assert exc_info.value.info.recoverable is False


def test_empty_request_used_by_no_arg_methods() -> None:
    req = Empty(operation_id="op")
    assert req.operation_id == "op"


def test_get_selection_is_registered_optional_selection_tool() -> None:
    spec = TOOL_REGISTRY["get_selection"]
    assert spec.group is Group.SELECTION
    assert spec.optional is True
    assert spec.request_model is Empty
    assert spec.response_model is SelectionResult


def test_default_get_selection_raises_not_supported_and_is_not_capable() -> None:
    adapter = _MinimalAdapter()
    with pytest.raises(AdapterError) as exc_info:
        adapter.get_selection(Empty(operation_id="op"))
    assert exc_info.value.info.code == ErrorCode.not_supported
    assert exc_info.value.info.recoverable is False
    assert "get_selection" not in adapter.capabilities().tools


def test_show_entities_request_rejects_empty_entities_without_clear() -> None:
    """``entities`` may be empty only when asking to ``clear`` -- otherwise the
    request means nothing at all (no entity to show, no clear to perform) and must fail fast at
    construction, not turn into a silent no-op deep inside an adapter."""
    with pytest.raises(ValidationError):
        ShowEntitiesRequest(operation_id="show")
    with pytest.raises(ValidationError):
        ShowEntitiesRequest(operation_id="show", entities=(), clear=False)


def test_show_entities_request_accepts_empty_entities_with_clear() -> None:
    request = ShowEntitiesRequest(operation_id="show", clear=True)
    assert request.entities == ()


def test_show_entities_request_accepts_entities_without_clear() -> None:
    request = ShowEntitiesRequest(operation_id="show", entities=(FaceHandle(id="face:1"),))
    assert request.clear is False


def test_show_entities_is_registered_optional_selection_tool() -> None:
    spec = TOOL_REGISTRY["show_entities"]
    assert spec.group is Group.SELECTION
    assert spec.optional is True
    assert spec.request_model is ShowEntitiesRequest
    assert spec.response_model is ShowEntitiesResult


def test_default_show_entities_raises_not_supported_and_is_not_capable() -> None:
    adapter = _MinimalAdapter()
    with pytest.raises(AdapterError) as exc_info:
        adapter.show_entities(ShowEntitiesRequest(operation_id="op", clear=True))
    assert exc_info.value.info.code == ErrorCode.not_supported
    assert exc_info.value.info.recoverable is False
    assert "show_entities" not in adapter.capabilities().tools


def test_selection_producer_is_a_runtime_checkable_protocol() -> None:
    class _NotAProducer:
        pass

    class _Producer:
        def set_selection_listener(self, listener: SelectionListener) -> None:
            del listener

    assert not isinstance(_NotAProducer(), SelectionProducer)
    assert isinstance(_Producer(), SelectionProducer)
