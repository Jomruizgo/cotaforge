"""Integration fixtures: everything here needs the FreeCAD root (``@pytest.mark.freecad``) and skips
without it; while the worker (implementer W) has not landed the tests are ``xfail(strict=False)``."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter, FreeCADSession, find_freecad_root
from cotaforge.adapters.freecad.rpc import WORKER_DIR
from tests.integration.freecad import sheet_gate

PROJECT_ROOT = Path(__file__).resolve().parents[3]
WORKER_MAIN = WORKER_DIR / "main.py"
WORKER_SUITE = PROJECT_ROOT / "tests" / "freecad_worker" / "run.py"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    here = Path(__file__).parent
    root = find_freecad_root()
    for item in items:
        if not Path(str(item.fspath)).is_relative_to(here):
            continue
        item.add_marker(pytest.mark.freecad)
        if root is None:
            item.add_marker(pytest.mark.skip(reason="FreeCAD root not found (COTAFORGE_FREECAD_ROOT)"))
        elif not WORKER_MAIN.exists():
            item.add_marker(pytest.mark.xfail(strict=False, reason="worker pending (adapters/freecad/worker/main.py)"))


@pytest.fixture(scope="module")
def freecad_root() -> Path:
    root = find_freecad_root()
    if root is None:
        pytest.skip("FreeCAD root not found")
    return root


@pytest.fixture(scope="module")
def adapter(freecad_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[FreeCADAdapter]:
    log_dir = tmp_path_factory.mktemp("freecad-logs")
    with FreeCADSession(freecad_root=freecad_root, timeout_s=60.0, log_dir=log_dir) as connected:
        yield connected


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]) -> Iterator[None]:
    """A test that failed already says what is wrong: its sheet is not reviewed on top of that."""
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed:
        item.rep_call_failed = True  # type: ignore[attr-defined]


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_teardown(item: pytest.Item, nextitem: pytest.Item | None) -> None:
    """No test sheet without a review (``sheet_gate``): before the fixtures of the test are torn down (the CAD it built the sheet in
    is still running), every adapter that built a sheet in it has its review run, and the test fails on a blocking finding of the
    drawing rules it did not list with ``@pytest.mark.expected_findings(...)``."""
    built = getattr(item, "sheet_gate_built", None)
    if built and not getattr(item, "rep_call_failed", False):
        expected = tuple(code for mark in item.iter_markers("expected_findings") for code in mark.args)
        sheet_gate.check_built(built, expected)


@pytest.fixture(autouse=True)
def every_sheet_is_reviewed(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> None:
    """Remember the adapters that build a sheet in the test (``pytest_runtest_teardown`` reviews them)."""
    built: list[FreeCADAdapter] = []
    request.node.sheet_gate_built = built
    original = FreeCADAdapter.create_drawing

    def watched(self: FreeCADAdapter, *args: object, **kwargs: object) -> object:
        result = original(self, *args, **kwargs)  # type: ignore[arg-type]
        if self not in built:
            built.append(self)
        return result

    monkeypatch.setattr(FreeCADAdapter, "create_drawing", watched)
