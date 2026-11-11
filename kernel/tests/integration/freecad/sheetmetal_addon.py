"""The external SheetMetal add-on for the integration tests: a PINNED release, cloned once into a cache outside the
repository and copied into a temporary FreeCAD user home per test module. The real profile is never touched.

The checkout is third-party code and is treated as data: it lives in its own empty directory, nothing in it is run
(only the FreeCAD worker imports it, from the temporary user home), and the tag is pinned to a commit so a moved tag
is refused.

Set ``COTAFORGE_TEST_ADDON_CACHE`` to a directory to choose where the clone is kept (default: a folder in the system
temp directory). When the clone is impossible (no git, no network) the tests that need the add-on are skipped with the
reason.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ADDON_REPOSITORY = "https://github.com/shaise/FreeCAD_SheetMetal.git"
ADDON_TAG = (
    "V0.8.26-beta"  # released 2026-10-15; the add-on's package.xml says 0.8.26. Loads and unfolds on FreeCAD 1.1.3
)
ADDON_COMMIT = "8177569b67ed8b0cd278bf8e9ce0fe220c1d7f03"
ADDON_VERSION = "0.8.26"
ENV_CACHE = "COTAFORGE_TEST_ADDON_CACHE"
CLONE_TIMEOUT_S = 180


def _cache_directory() -> Path:
    configured = os.environ.get(ENV_CACHE)
    return Path(configured) if configured else Path(tempfile.gettempdir()) / "cotaforge-test-addons"


def _git(*args: str, cwd: Path | None = None) -> str:
    done = subprocess.run(  # noqa: S603 - fixed git arguments; the checkout is never executed
        ["git", "-c", "core.hooksPath=/dev/null", *args],  # noqa: S607
        check=True,
        capture_output=True,
        text=True,
        timeout=CLONE_TIMEOUT_S,
        cwd=cwd,
        env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
    )
    return done.stdout.strip()


def addon_checkout() -> Path:
    """The pinned checkout, cloned on first use; skips the calling test when it cannot be had."""
    target = _cache_directory() / f"FreeCAD_SheetMetal-{ADDON_TAG}"
    if not target.is_dir():
        target.parent.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix="sheetmetal-clone-", dir=target.parent))
        try:
            _git("clone", "--quiet", "--depth", "1", "--branch", ADDON_TAG, ADDON_REPOSITORY, str(staging / "repo"))
            staging.joinpath("repo").replace(target)
        except (OSError, subprocess.SubprocessError) as exc:
            pytest.skip(f"cannot clone the SheetMetal add-on {ADDON_TAG} ({type(exc).__name__}): {exc}")
        finally:
            shutil.rmtree(staging, ignore_errors=True)
    try:
        head = _git("rev-parse", "HEAD", cwd=target)
    except (OSError, subprocess.SubprocessError) as exc:
        pytest.skip(f"cannot read the cached SheetMetal checkout ({type(exc).__name__}): {exc}")
    if head != ADDON_COMMIT:
        pytest.skip(f"the cached SheetMetal checkout is {head}, not the pinned {ADDON_COMMIT}")
    return target


def make_user_home(parent: Path, *, with_addon: bool) -> Path:
    """A fresh FreeCAD user home (``FREECAD_USER_HOME``) under ``parent``, with the add-on in its ``Mod`` folder or
    with an empty one."""
    home = Path(tempfile.mkdtemp(prefix="freecad-home-", dir=parent))
    (home / "Mod").mkdir()
    if with_addon:
        shutil.copytree(addon_checkout(), home / "Mod" / "SheetMetal", ignore=shutil.ignore_patterns(".git"))
    return home
