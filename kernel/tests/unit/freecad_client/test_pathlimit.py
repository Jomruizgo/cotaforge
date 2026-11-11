"""The Windows path-length guard: paths handed to FreeCAD must leave room for its temporary files (MAX_PATH).

Windows is simulated (monkeypatch / parameter); nothing here depends on the real OS or on FreeCAD."""

import pytest

from cotaforge.adapters.freecad import pathlimit
from cotaforge.adapters.freecad.adapter import FreeCADAdapter
from cotaforge.contract import AdapterError, ErrorCode, ExportMeshRequest, ExportRequest, OpenDocumentRequest

LIMIT = pathlimit.SAFE_PATH_LENGTH


def _path_of_length(n: int) -> str:
    prefix = "W:\\data\\"
    return prefix + "a" * (n - len(prefix) - len(".FCStd")) + ".FCStd"


def test_the_limit_leaves_room_for_the_temporary_files() -> None:
    assert LIMIT == pathlimit.MAX_PATH - pathlimit.SUFFIX_MARGIN
    assert pathlimit.SUFFIX_MARGIN >= len(".tmp") + 1 + 37  # worker temp name + FreeCAD's ".<uuid>" temporary


def test_a_path_just_under_the_limit_passes_and_at_the_limit_fails() -> None:
    assert pathlimit.path_length_problem(_path_of_length(LIMIT - 1), "the document path", windows=True) is None
    problem = pathlimit.path_length_problem(_path_of_length(LIMIT), "the document path", windows=True)
    assert problem is not None
    assert str(LIMIT) in problem  # its length
    assert str(pathlimit.MAX_PATH) in problem
    assert "shorter" in problem


def test_the_message_names_the_path_and_what_it_is() -> None:
    path = _path_of_length(230)
    problem = pathlimit.path_length_problem(path, "the document path", windows=True)
    assert problem is not None
    assert path in problem
    assert "230 characters" in problem
    assert problem.startswith("the document path")


def test_it_is_skipped_outside_windows() -> None:
    assert pathlimit.path_length_problem(_path_of_length(400), "x", windows=False) is None


def test_the_platform_defaults_to_the_running_os(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pathlimit, "_is_windows", lambda: True)
    assert pathlimit.path_length_problem(_path_of_length(LIMIT), "x") is not None
    monkeypatch.setattr(pathlimit, "_is_windows", lambda: False)
    assert pathlimit.path_length_problem(_path_of_length(LIMIT), "x") is None


@pytest.mark.parametrize(
    "call",
    [
        lambda a, p: a.open_document(OpenDocumentRequest(operation_id="o", path=p)),
        lambda a, p: a.export_step(ExportRequest(operation_id="o", path=p)),
        lambda a, p: a.export_mesh(ExportMeshRequest(operation_id="o", path=p)),
    ],
)
def test_the_adapter_refuses_a_long_path_before_calling_the_worker(
    monkeypatch: pytest.MonkeyPatch, call: object
) -> None:
    monkeypatch.setattr(pathlimit, "_is_windows", lambda: True)
    adapter = FreeCADAdapter()  # never connected: reaching the worker would say "not connected" instead
    with pytest.raises(AdapterError) as caught:
        call(adapter, _path_of_length(LIMIT))  # type: ignore[operator]
    info = caught.value.info
    assert info.code is ErrorCode.invalid_request
    assert info.details["reason"] == "path_too_long"
    assert info.details["length"] == LIMIT
    assert not info.recoverable


def test_the_adapter_lets_a_short_path_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pathlimit, "_is_windows", lambda: True)
    adapter = FreeCADAdapter()
    with pytest.raises(AdapterError) as caught:
        adapter.export_step(ExportRequest(operation_id="o", path="W:\\data\\a.step"))
    assert caught.value.info.code is ErrorCode.session_lost  # got as far as the (absent) worker
