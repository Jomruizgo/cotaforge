"""``run_native``: request/result models and its place in the optional-tool registry."""

import json

import pytest
from pydantic import ValidationError

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import (
    TOOL_REGISTRY,
    AdapterError,
    BoundingBox,
    BrokenObject,
    ErrorCode,
    Group,
    NativeError,
    NativeObject,
    RunNativeRequest,
    RunNativeResult,
)


def test_request_defaults_and_required_fields() -> None:
    request = RunNativeRequest(operation_id="op", code="x = 1")
    assert request.intent == ""
    assert request.timeout_s == 60.0
    with pytest.raises(ValidationError):
        RunNativeRequest(operation_id="op")  # type: ignore[call-arg]
    with pytest.raises(ValidationError):
        RunNativeRequest(operation_id="op", code="x", extra_field=1)  # type: ignore[call-arg]


@pytest.mark.parametrize("timeout", [0, -1, 3601])
def test_timeout_must_be_positive_and_bounded(timeout: float) -> None:
    with pytest.raises(ValidationError):
        RunNativeRequest(operation_id="op", code="x", timeout_s=timeout)


def test_request_never_rejects_code_for_size_or_style() -> None:
    request = RunNativeRequest(operation_id="op", code="x=1;" * 100_000)
    assert len(request.code) == 400_000
    assert RunNativeRequest(operation_id="op", code="").code == ""


def test_request_is_frozen() -> None:
    request = RunNativeRequest(operation_id="op", code="x")
    with pytest.raises(ValidationError):
        request.code = "y"  # type: ignore[misc]


def test_result_round_trips_through_json() -> None:
    result = RunNativeResult(
        ok=True,
        created=(NativeObject(name="Box", label="Box", type_id="Part::Box"),),
        modified=(NativeObject(name="Cut", label="Cut", type_id="Part::Cut"),),
        removed=(NativeObject(name="Old", label="Old", type_id="Part::Feature"),),
        volume_delta_mm3=-12.5,
        bbox_before=BoundingBox(min=(0, 0, 0), max=(1, 1, 1)),
        bbox_after=None,
        broken=(BrokenObject(name="Sketch", reason="conflicting constraints"),),
        stdout="hi\n",
        error=NativeError(type="NameError", message="boom", line=3, hint="define it"),
        rolled_back=True,
        warnings=("w",),
        duration_ms=1.5,
    )
    again = RunNativeResult.model_validate(json.loads(result.model_dump_json()))
    assert again == result


def test_result_defaults_describe_an_empty_ok_step() -> None:
    result = RunNativeResult(ok=True)
    assert (result.created, result.modified, result.removed, result.broken, result.warnings) == ((), (), (), (), ())
    assert result.error is None
    assert result.bbox_before is None
    assert result.rolled_back is False


def test_result_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        RunNativeResult.model_validate({"ok": True, "surprise": 1})


def test_error_line_is_optional_for_failures_not_tied_to_a_line() -> None:
    assert NativeError(type="BrokenResult", message="m").line is None


def test_run_native_is_a_registered_optional_native_tool() -> None:
    spec = TOOL_REGISTRY["run_native"]
    assert spec.group is Group.NATIVE
    assert spec.optional is True
    assert spec.request_model is RunNativeRequest
    assert spec.response_model is RunNativeResult


def test_fake_adapter_does_not_offer_run_native() -> None:
    adapter = FakeAdapter()
    assert "run_native" not in adapter.capabilities().tools
    with pytest.raises(AdapterError) as excinfo:
        adapter.run_native(RunNativeRequest(operation_id="op", code="x = 1"))
    assert excinfo.value.info.code is ErrorCode.not_supported
    assert excinfo.value.info.recoverable is False
