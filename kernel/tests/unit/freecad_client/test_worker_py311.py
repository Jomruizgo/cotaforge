"""The worker sub-package must stay Python 3.11-compatible (``freecadcmd`` 1.0.2/1.1.3 bundle 3.11) and
import only stdlib, FreeCAD, or sibling worker modules -- never anything from ``cotaforge`` (the
dependency rule, checked for the rest of the tree by
``tests/unit/test_dependency_rule.py``). A ``target-version = "py312"`` ruff config (or a stray
``pyproject.toml`` in the worker directory) could otherwise let ``UP`` rules rewrite this code to
3.12-only syntax that ``freecadcmd`` cannot run."""

import ast
import functools
import json
import subprocess
import sys
from pathlib import Path

import pytest

from cotaforge.adapters.freecad import find_freecad_root
from tests.platform_support import freecad_headless

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"

# ``FreeCADGui``, FreeCAD's ``PySide`` shim and ``pivy`` exist only in the GUI binary; ``worker/gui.py`` imports them
# behind ``App.GuiUp`` so the headless worker never touches them. ``TechDraw`` (``worker/drawing.py``) is imported on
# use: a FreeCAD build without it gets a ``not_supported`` answer.
# ``AssemblyApp`` is the Assembly workbench's C++ module (``Assembly::AssemblyObject``), imported lazily by
# ``ops_assembly``.
FREECAD_MODULES = {
    "FreeCAD",
    "Part",
    "Import",
    "Sketcher",
    "Draft",  # FreeCAD's own module (the linked clone of shrink_body)
    "PartDesign",
    "AssemblyApp",
    "JointObject",
    "UtilsAssembly",
    "Mesh",
    "MeshPart",
    "App",
    "FreeCADGui",
    "PySide",
    "TechDraw",
    "TechDrawGui",
    "PySide6",
    "pivy",
    "importDXF",
}

# Sentinel line prefix used to pick the JSON payload out of freecadcmd's stdout, which also carries
# FreeCAD's own startup banners/warnings.
_STDLIB_PROBE_SENTINEL = "COTAFORGE_STDLIB_MODULES: "

# Vendored, frozen Python 3.11 stdlib module names -- last-resort fallback for environments where
# the FreeCAD AppImage (and therefore a real 3.11 interpreter) cannot be found at all.
#
# Preferred provenance: run `python3.11 -c "import sys, json; print(json.dumps(sorted(
# sys.stdlib_module_names)))"` on a machine with Python 3.11 installed, and paste the result here.
#
# Actual provenance of the list below (no `python3.11` binary was available on the machine that
# generated it): this test runner's own `sys.stdlib_module_names` (Python 3.12), minus the four
# modules added between 3.11 and 3.12 (`_pydatetime`, `_pylong`, `_sha2`, `_tokenize`). This is
# only an approximation -- verified against a real `freecadcmd` 3.11.13, it is missing stdlib
# internals 3.11 still exposes that 3.12 dropped from the public `stdlib_module_names` set
# (`_bootsubprocess`, `_sha256`, `_sha512`, `asynchat`, `asyncore`, `distutils`, `imp`, `smtpd`).
# That gap only matters when this frozen list is actually used, i.e. when no FreeCAD AppImage can
# be found at all -- whenever one is present (the normal case for this project's dev/CI machines),
# `_worker_runtime_stdlib_modules` probes it live instead and this constant is never consulted.
_FROZEN_PY311_STDLIB_MODULES = frozenset(
    sorted(set(sys.stdlib_module_names) - {"_pydatetime", "_pylong", "_sha2", "_tokenize"})
)


@functools.lru_cache(maxsize=1)
def _worker_runtime_stdlib_modules() -> frozenset[str]:
    """The stdlib module names of the interpreter that actually runs the worker.

    The test runner's own interpreter is not necessarily the one executing worker code --
    ``freecadcmd`` 1.0.2/1.1.3 bundle Python 3.11, while this suite may run under a newer Python. When
    the FreeCAD AppImage is available, spawn ``freecadcmd`` once (result cached module-wide for the
    process) and ask it directly; otherwise fall back to the vendored, approximate 3.11 list above.
    """
    root = find_freecad_root()
    if root is None:
        return _FROZEN_PY311_STDLIB_MODULES

    freecadcmd = freecad_headless(root)
    script = f"import sys, json; print({_STDLIB_PROBE_SENTINEL!r} + json.dumps(sorted(sys.stdlib_module_names)))"
    try:
        result = subprocess.run(
            [str(freecadcmd), "-c", script],
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
    except OSError:
        return _FROZEN_PY311_STDLIB_MODULES

    sentinel_lines = [line for line in result.stdout.splitlines() if line.startswith(_STDLIB_PROBE_SENTINEL)]
    if not sentinel_lines:
        return _FROZEN_PY311_STDLIB_MODULES

    payload = sentinel_lines[-1][len(_STDLIB_PROBE_SENTINEL) :]
    return frozenset(json.loads(payload))


STDLIB_MODULES = _worker_runtime_stdlib_modules()


def _worker_files() -> list[Path]:
    files = sorted(p for p in WORKER_DIR.glob("*.py"))
    assert files, f"no worker files found under {WORKER_DIR}"
    return files


def _sibling_modules() -> set[str]:
    return {f.stem for f in _worker_files()}


def _imported_top_levels(tree: ast.Module) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_worker_files_parse_as_python_3_11() -> None:
    for path in _worker_files():
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=(3, 11))


def test_worker_files_import_only_stdlib_freecad_or_sibling_modules() -> None:
    allowed = FREECAD_MODULES | STDLIB_MODULES | _sibling_modules()
    for path in _worker_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        disallowed = _imported_top_levels(tree) - allowed
        assert not disallowed, f"{path.name} imports non-stdlib/FreeCAD modules: {sorted(disallowed)}"


def test_worker_stdlib_allowlist_reflects_the_freecad_runtime_not_this_test_runner() -> None:
    """``STDLIB_MODULES`` must describe the interpreter that actually executes the worker
    ``freecadcmd``'s bundled Python 3.11 (1.0.2 or 1.1.3), not this test runner's interpreter (3.12) --
    otherwise the allow-list would accept an import that only exists on 3.12 and then blow up at
    runtime inside ``freecadcmd``."""
    py312_only_additions = {"_pydatetime", "_pylong", "_sha2"}
    assert not py312_only_additions & STDLIB_MODULES


@pytest.mark.skipif(
    find_freecad_root() is None,
    reason="FreeCAD AppImage not found; cannot probe the real worker runtime",
)
def test_worker_stdlib_allowlist_is_probed_live_from_freecadcmd_when_available() -> None:
    """When the FreeCAD AppImage is present, the allow-list must come from actually asking
    ``freecadcmd`` for its own ``sys.stdlib_module_names`` -- not from a vendored guess -- so it
    also includes stdlib internals 3.11 still exposes there that 3.12 no longer lists publicly
    (e.g. ``distutils``, ``imp``)."""
    assert {"distutils", "imp"} <= STDLIB_MODULES
