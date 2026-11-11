"""Paths keep the form the user gave: a Windows ``subst`` drive (here: a symlink, its POSIX stand-in) must not be
expanded to its long real target, or FreeCAD's temporary files next to a document overrun MAX_PATH."""

import os
from pathlib import Path

import pytest

from cotaforge_mcp import cli
from cotaforge_mcp.config import ENV_ADAPTER, ENV_WORKSPACE, Settings

pytestmark = pytest.mark.skipif(os.name == "nt", reason="needs symlinks; the subst drive itself is Windows-only")


@pytest.fixture
def alias(tmp_path: Path) -> tuple[Path, Path]:
    """(real long folder, short alias to it)."""
    real = tmp_path / "a_rather_long_real_target" / "deeper"
    real.mkdir(parents=True)
    short = tmp_path / "W"
    short.symlink_to(real, target_is_directory=True)
    return real, short


def test_the_workspace_keeps_the_alias(alias: tuple[Path, Path]) -> None:
    real, short = alias
    settings = Settings.from_env({ENV_ADAPTER: "fake", ENV_WORKSPACE: str(short)})
    assert settings.workspace == short
    assert settings.workspace != real


def test_a_relative_path_is_anchored_on_the_alias_not_on_its_target(alias: tuple[Path, Path]) -> None:
    _, short = alias
    settings = Settings.from_env({ENV_ADAPTER: "fake", ENV_WORKSPACE: str(short)})
    target = settings.resolve("part.FCStd")
    assert target == short / "part.FCStd"
    assert str(target).startswith(str(short))


def test_init_reports_the_folder_as_given(alias: tuple[Path, Path]) -> None:
    _, short = alias
    space = short / "space"
    report = cli.init_space(space, interpreter="python", adapter="fake", home=short / "home")
    assert report.folder == space


def test_dotdot_is_still_normalised(tmp_path: Path) -> None:
    (tmp_path / "x").mkdir()
    settings = Settings.from_env({ENV_ADAPTER: "fake", ENV_WORKSPACE: str(tmp_path / "x" / ".." / "x")})
    assert settings.workspace == tmp_path / "x"
