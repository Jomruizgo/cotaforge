"""The user manual (docs/user-manual.md in English, docs/manual-usuario.md in Spanish): it exists, it names every tool of the server and it carries no trace of the
place the product was built in."""

from pathlib import Path

import pytest

from cotaforge_mcp.schemas import TOOLS
from tests.unit.test_plugin_package import BANNED, REPO

MANUALS = (REPO / "docs" / "user-manual.md", REPO / "docs" / "manual-usuario.md")
README = REPO / "README.md"


def manual_text(manual: Path = MANUALS[0]) -> str:
    assert manual.is_file(), f"{manual} is missing"
    return manual.read_text(encoding="utf-8")


@pytest.mark.parametrize("manual", MANUALS, ids=lambda p: p.name)
def test_the_manual_exists_and_is_not_trivial(manual: Path) -> None:
    text = manual_text(manual)
    assert text.startswith("# ")
    assert len(text.splitlines()) > 100


@pytest.mark.parametrize("manual", MANUALS, ids=lambda p: p.name)
def test_the_manual_mentions_neither_the_workshop_nor_the_old_project(manual: Path) -> None:
    match = BANNED.search(manual_text(manual))
    assert match is None, match and match.group(0)


@pytest.mark.parametrize("manual", MANUALS, ids=lambda p: p.name)
@pytest.mark.parametrize("tool", [tool.name for tool in TOOLS])
def test_every_tool_of_the_server_is_in_the_manual(tool: str, manual: Path) -> None:
    assert f"`{tool}`" in manual_text(manual), f"the manual never names the tool `{tool}`"


def test_the_readmes_point_to_both_manuals() -> None:
    for readme in (README, README.with_name("README.es.md")):
        text = readme.read_text(encoding="utf-8")
        for manual in MANUALS:
            assert f"docs/{manual.name}" in text and manual.is_file()
