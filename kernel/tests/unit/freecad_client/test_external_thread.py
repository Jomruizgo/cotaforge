# ruff: noqa: E501
"""``external_thread``: the model the agent writes (``adapters/freecad/external_thread.py``), what the adapter sends the
worker, where a thread starts on the shaft (``worker/thread_records.py``) and that the worker serves it. No FreeCAD."""

import sys
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from cotaforge.adapters.freecad import FreeCADAdapter, external_thread
from cotaforge.contract import ExtensionRequest, OperationResult, extension_op_name
from cotaforge.ir import FaceRef

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import thread_records as records  # noqa: E402

CYLINDER = {"kind": "face", "from_op": "shaft", "select": {"surface": "cylinder"}}
END = {"kind": "face", "from_op": "shaft", "select": {"normal": [0, 0, 1]}}


def params(**over: Any) -> dict[str, Any]:
    return {"face": CYLINDER, "size": "M12", "length_mm": 20, "end": END, **over}


# --- the model ----------------------------------------------------------------------------------------------------------


def test_the_model_takes_a_coarse_and_a_fine_designation_in_either_decimal_mark() -> None:
    assert external_thread.ExternalThread.model_validate(params()).size == "M12"
    assert external_thread.ExternalThread.model_validate(params(size="M12x1.25")).size == "M12x1.25"
    assert external_thread.ExternalThread.model_validate(params(size="M12x1,25")).size == "M12x1.25"


@pytest.mark.parametrize("size", ["M13", "12", "M12x9", "1/2-13 UNC", ""])
def test_a_size_that_is_not_in_the_table_is_refused_with_what_is_accepted(size: str) -> None:
    with pytest.raises(ValidationError, match="not an ISO metric thread of the table"):
        external_thread.ExternalThread.model_validate(params(size=size))


@pytest.mark.parametrize("length", [0, -5])
def test_the_length_must_be_positive(length: float) -> None:
    with pytest.raises(ValidationError):
        external_thread.ExternalThread.model_validate(params(length_mm=length))


def test_both_faces_are_references_the_executor_resolves() -> None:
    model = external_thread.ExternalThread.model_validate(params())
    assert isinstance(model.face, FaceRef) and isinstance(model.end, FaceRef)


def test_the_spec_is_the_one_the_guide_lists_and_its_example_is_valid() -> None:
    spec = external_thread.spec()
    assert (spec.name, spec.method, spec.category) == ("external_thread", "external_thread", "shafts")
    assert spec.makes_feature and not spec.read_only
    assert spec.example is not None
    external_thread.ExternalThread.model_validate(spec.example)
    assert extension_op_name(spec.name) == "ext:external_thread"


# --- the numbers of a size ----------------------------------------------------------------------------------------------


def test_the_record_carries_major_pitch_and_the_basic_minor_diameter() -> None:
    assert external_thread.record("M12") == {"size": "M12", "major_mm": 12, "pitch_mm": 1.75, "minor_mm": 10.106}
    fine = external_thread.record("M12x1.25")
    assert (fine["pitch_mm"], fine["minor_mm"]) == (1.25, 10.647)


def test_the_basic_minor_diameter_is_d_minus_1_0825_p() -> None:
    assert external_thread.minor_diameter_mm(24.0, 3.0) == pytest.approx(20.7525, abs=1e-3)
    assert external_thread.minor_diameter_mm(6.0, 1.0) == pytest.approx(4.9175, abs=1e-3)


# --- the adapter --------------------------------------------------------------------------------------------------------


def test_the_freecad_adapter_offers_the_operation_and_sends_the_numbers_with_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    adapter = FreeCADAdapter.__new__(FreeCADAdapter)
    assert [s.name for s in adapter.extra_operations()] == ["external_thread"]
    sent: dict[str, Any] = {}

    def fake_call(method: str, request: ExtensionRequest, model: type) -> str:
        sent.update(method=method, request=request, model=model)
        return "done"

    monkeypatch.setattr(adapter, "_call", fake_call)
    request = ExtensionRequest(
        operation_id="end_thread",
        name="external_thread",
        params={"face": {"id": "face:4"}, "size": "M12", "length_mm": 20, "end": {"id": "face:5"}},
    )
    assert adapter.external_thread(request) == "done"
    assert (sent["method"], sent["model"]) == ("external_thread", OperationResult)
    assert sent["request"].params == {
        "face": {"id": "face:4"},
        "size": "M12",
        "length_mm": 20,
        "end": {"id": "face:5"},
        "major_mm": 12,
        "pitch_mm": 1.75,
        "minor_mm": 10.106,
    }
    assert request.params == {"face": {"id": "face:4"}, "size": "M12", "length_mm": 20, "end": {"id": "face:5"}}


# --- where the thread starts --------------------------------------------------------------------------------------------


def test_the_thread_runs_down_from_the_top_end_and_up_from_the_bottom_end() -> None:
    assert records.end_placement(60.0, 0.0, 60.0, 12.0) == (-1.0, 60.0, 60.0)
    assert records.end_placement(0.0, 0.0, 60.0, 12.0) == (1.0, 0.0, 60.0)


def test_a_chamfered_end_leaves_the_cylinder_short_of_the_end_face_and_is_still_that_end() -> None:
    assert records.end_placement(61.0, 0.0, 60.0, 12.0) == (-1.0, 61.0, 61.0)  # a 1 mm chamfer
    assert records.end_placement(-1.5, 0.0, 60.0, 12.0) == (1.0, -1.5, 61.5)  # a 1.5 mm one


@pytest.mark.parametrize("at", [30.0, 59.0, 1.0, 64.5, -3.5])
def test_a_face_in_the_middle_or_far_beyond_is_not_the_end_face(at: float) -> None:
    assert records.end_placement(at, 0.0, 60.0, 12.0) is None


def test_the_allowed_gap_grows_with_the_diameter_and_never_below_a_millimetre() -> None:
    assert records.end_placement(61.0, 0.0, 60.0, 2.0) == (-1.0, 61.0, 61.0)
    assert records.end_placement(61.5, 0.0, 60.0, 2.0) is None
    assert records.end_placement(66.0, 0.0, 60.0, 30.0) == (-1.0, 66.0, 66.0)


# --- the worker serves it -----------------------------------------------------------------------------------------------


def test_the_worker_dispatches_the_operation_and_repaints_after_it() -> None:
    source = (WORKER_DIR / "main.py").read_text(encoding="utf-8")
    assert '"external_thread": lambda p: ops_threads.external_thread(d(p), p)' in source
    assert source.count('"external_thread",') == 1  # MUTATING_METHODS
    assert "import ops_threads" in source


def test_the_record_properties_are_what_the_worker_sets_and_the_sheet_reads() -> None:
    ops = (WORKER_DIR / "ops_threads.py").read_text(encoding="utf-8")
    for name in records.PROPERTIES:
        assert f'"{name}"' in ops, name  # every property of the record is written by the operation
    assert records.read.__doc__
    assert {"ThreadSize", "ThreadStart", "ThreadDirection", "ThreadCylinder"} <= set(records.PROPERTIES)
