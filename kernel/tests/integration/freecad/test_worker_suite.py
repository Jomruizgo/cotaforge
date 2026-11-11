"""Run W's worker unit suite (plain asserts inside ``freecadcmd``) as one pytest entry point."""

import subprocess
import sys
from pathlib import Path

import pytest

from tests.integration.freecad.conftest import PROJECT_ROOT, WORKER_SUITE

pytestmark = pytest.mark.freecad


def test_worker_suite_passes_inside_freecadcmd(freecad_root: Path) -> None:
    if not WORKER_SUITE.exists():
        pytest.skip(f"{WORKER_SUITE.relative_to(PROJECT_ROOT)} does not exist yet (implementer W)")
    freecadcmd = freecad_root / ("bin/FreeCADCmd.exe" if sys.platform == "win32" else "usr/bin/freecadcmd")
    command = [str(freecadcmd), "-c", f"exec(open({str(WORKER_SUITE)!r}).read())"]
    completed = subprocess.run(command, cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=1800, check=False)
    output = completed.stdout + completed.stderr
    assert completed.returncode == 0, output
    assert "FAIL" not in output, output
