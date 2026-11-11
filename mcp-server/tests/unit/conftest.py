"""Unit-test guard: no test may run the real ``claude`` (``claude plugin ...`` changes the user's Claude Code
configuration). A test that needs it passes a fake runner or patches ``plugin_registry.run_claude`` itself."""

from collections.abc import Sequence

import pytest

from cotaforge_mcp import plugin_registry


def refuse_real_claude(argv: Sequence[str]) -> "plugin_registry.subprocess.CompletedProcess[str]":
    msg = f"a unit test tried to run the real claude: {list(argv)}"
    raise plugin_registry.PluginError(msg)


@pytest.fixture(autouse=True)
def no_real_claude(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin_registry, "run_claude", refuse_real_claude)
