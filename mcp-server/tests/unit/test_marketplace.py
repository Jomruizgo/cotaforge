"""The repository is a Claude Code marketplace (``.claude-plugin/marketplace.json``) and the installer ships the same
file: one version everywhere (marketplace entry, ``plugin.json``, both packages)."""

import json
import tomllib
from pathlib import Path
from typing import Any

import cotaforge

import cotaforge_mcp
from cotaforge_mcp import layout

REPO = Path(__file__).resolve().parents[3]
MARKETPLACE = REPO / ".claude-plugin" / "marketplace.json"


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def project_version(package_dir: str) -> str:
    return tomllib.loads((REPO / package_dir / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def test_the_marketplace_lists_the_plugin_from_its_folder() -> None:
    data = load(MARKETPLACE)
    assert data["name"] == layout.MARKETPLACE_NAME
    assert data["owner"]["name"]
    (entry,) = data["plugins"]
    assert entry["name"] == layout.PLUGIN_NAME
    assert entry["source"] == "./plugin"
    assert entry["description"]
    assert (REPO / "plugin" / ".claude-plugin" / "plugin.json").is_file()


def test_the_plugin_id_is_what_the_registry_installs() -> None:
    assert layout.PLUGIN_ID == "cotaforge@cotaforge"


def test_one_version_in_the_marketplace_the_plugin_and_both_packages() -> None:
    manifest = load(REPO / "plugin" / ".claude-plugin" / "plugin.json")
    (entry,) = load(MARKETPLACE)["plugins"]
    versions = {
        "marketplace.json": entry["version"],
        "plugin.json": manifest["version"],
        "kernel/pyproject.toml": project_version("kernel"),
        "mcp-server/pyproject.toml": project_version("mcp-server"),
        "cotaforge.__version__": cotaforge.__version__,
        "cotaforge_mcp.__version__": cotaforge_mcp.__version__,
    }
    assert len(set(versions.values())) == 1, versions


def test_the_repository_root_counts_as_a_marketplace_folder() -> None:
    assert layout.is_marketplace_dir(REPO)
