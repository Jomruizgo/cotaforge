"""The optional ``save_document`` tool: write the live document to a native file
atomically, with a neutral identity stamp inside it, and hand back an opaque, versioned ``adapter_state`` the harness
keeps next to the file. A save that fails is recoverable: the live document is untouched."""

import pytest
from pydantic import ValidationError

from cotaforge.contract import (
    TOOL_REGISTRY,
    Adapter,
    AdapterError,
    DocumentHandle,
    DocumentInfo,
    DocumentStamp,
    ErrorCode,
    Group,
    OpenDocumentRequest,
    OpenDocumentResult,
    RebindReport,
    SaveDocumentRequest,
    SaveDocumentResult,
    export_mcp_tools,
)
from cotaforge.contract.document import STAMP_META_PREFIX, STAMP_SCHEMA


def _stamp(**changes: object) -> DocumentStamp:
    fields: dict[str, object] = {
        "design_id": "demo",
        "plan_id": "part_a",
        "plan_revision": 3,
        "save_seq": 7,
        "last_step_index": 41,
        "harness_version": "0.4.0",
        "plan_sha256": "a" * 64,
        "sidecar_sha256": "b" * 64,
    }
    fields.update(changes)
    return DocumentStamp.model_validate(fields)


def test_save_document_is_an_optional_session_tool() -> None:
    spec = TOOL_REGISTRY["save_document"]
    assert spec.optional is True
    assert spec.group is Group.SESSION
    assert spec.request_model is SaveDocumentRequest
    assert spec.response_model is SaveDocumentResult
    assert "save_document" in {t["name"] for t in export_mcp_tools(include_optional=True)}
    assert "save_document" not in {t["name"] for t in export_mcp_tools()}


def test_the_default_adapter_method_refuses_with_not_supported() -> None:
    request = SaveDocumentRequest(operation_id="s", path="/w/models/part_a.FCStd", stamp=_stamp())
    with pytest.raises(AdapterError) as info:
        Adapter.save_document(None, request)  # type: ignore[arg-type]
    assert info.value.info.code is ErrorCode.not_supported
    assert info.value.info.recoverable is False


def test_the_stamp_is_neutral_and_maps_onto_flat_string_metadata() -> None:
    stamp = _stamp()
    meta = stamp.to_meta()
    assert all(key.startswith(STAMP_META_PREFIX) for key in meta)
    assert all(isinstance(value, str) for value in meta.values())
    assert meta[f"{STAMP_META_PREFIX}schema"] == str(STAMP_SCHEMA)
    assert meta[f"{STAMP_META_PREFIX}plan_id"] == "part_a" and meta[f"{STAMP_META_PREFIX}save_seq"] == "7"
    assert DocumentStamp.from_meta({**meta, "unrelated": "kept by the CAD"}) == stamp


def test_a_stamp_without_a_sidecar_hash_round_trips() -> None:
    stamp = _stamp(sidecar_sha256=None)
    assert DocumentStamp.from_meta(stamp.to_meta()) == stamp


def test_from_meta_without_a_stamp_is_none_and_a_newer_schema_is_refused() -> None:
    assert DocumentStamp.from_meta({}) is None
    newer = {**_stamp().to_meta(), f"{STAMP_META_PREFIX}schema": str(STAMP_SCHEMA + 1)}
    with pytest.raises(ValueError, match="newer"):
        DocumentStamp.from_meta(newer)


def test_the_stamp_rejects_negative_counters() -> None:
    with pytest.raises(ValidationError):
        _stamp(save_seq=-1)
    with pytest.raises(ValidationError):
        _stamp(plan_revision=0)


def test_a_request_and_a_result_round_trip_through_json() -> None:
    request = SaveDocumentRequest(
        operation_id="s", path="/w/models/part_a.FCStd", stamp=_stamp(), expected_sha256="c" * 64
    )
    assert request.overwrite is True
    assert SaveDocumentRequest.model_validate_json(request.model_dump_json()) == request
    result = SaveDocumentResult(
        path="/w/models/part_a.FCStd",
        sha256="d" * 64,
        size_bytes=1234,
        duration_ms=12.5,
        document_uid="uid-1",
        cad_version="1.1.3",
        adapter_state={"schema": "freecad-registry/1", "bodies": [{"id": "body:1"}]},
        warnings=("the engineer saved by hand",),
    )
    assert SaveDocumentResult.model_validate_json(result.model_dump_json()) == result


def test_a_result_needs_a_schema_in_its_adapter_state() -> None:
    with pytest.raises(ValidationError, match="schema"):
        SaveDocumentResult(path="/p", sha256="d" * 64, size_bytes=1, duration_ms=1.0, adapter_state={})


# --- open_document ---


def test_open_document_is_an_optional_session_tool() -> None:
    spec = TOOL_REGISTRY["open_document"]
    assert spec.optional is True
    assert spec.group is Group.SESSION
    assert spec.request_model is OpenDocumentRequest
    assert spec.response_model is OpenDocumentResult
    assert "open_document" not in {t["name"] for t in export_mcp_tools()}


def test_the_default_open_document_refuses_with_not_supported() -> None:
    request = OpenDocumentRequest(operation_id="o", path="/w/models/part_a.FCStd", adapter_state={"schema": "x/1"})
    with pytest.raises(AdapterError) as info:
        Adapter.open_document(None, request)  # type: ignore[arg-type]
    assert info.value.info.code is ErrorCode.not_supported


def test_an_open_result_round_trips_and_says_whether_the_rebind_was_clean() -> None:
    clean = RebindReport(bodies_ok=("body:1",))
    assert clean.clean
    drifted = RebindReport(bodies_ok=("body:1",), bodies_drifted=("body:2",), foreign_objects=("Pocket001",))
    assert not drifted.clean
    assert not RebindReport(records_unmatched=("face:9",)).clean
    assert not RebindReport(objects_invalid=("Pad",)).clean
    result = OpenDocumentResult(
        document=DocumentInfo(handle=DocumentHandle(id="doc:1"), kind="part", name="part_a", units="mm"),
        stamp=_stamp(),
        sha256="e" * 64,
        size_bytes=10,
        document_uid="uid-1",
        cad_version="1.1.3",
        rebind=drifted,
        duration_ms=3.0,
    )
    assert OpenDocumentResult.model_validate_json(result.model_dump_json()) == result
