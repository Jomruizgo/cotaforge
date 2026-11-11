"""Integration fixtures: everything here needs the FreeCAD root (``@pytest.mark.freecad``) and skips
without it; while the worker (implementer W) has not landed the tests are ``xfail(strict=False)``."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter, FreeCADSession, find_freecad_root
from cotaforge.adapters.freecad.rpc import WORKER_DIR

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
