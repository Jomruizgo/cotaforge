"""The repository holds no AI development context: no agent instructions, no tool folders, no prompt files.

Contributors may use any AI tool they like, but its context stays in their own folder outside the repository. What is
allowed is what the product itself ships: the design-space template (``templates/``, and its packaged copy in the
server), and the Claude Code plugin (``plugin/``: its agents and skills are product files).
"""

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]

# What the product ships: the design-space template and the plugin.
SHIPPED = ("templates/", "plugin/", "mcp-server/src/cotaforge_mcp/data/design-space/")

FILE_NAMES = re.compile(
    r"^(claude|agents|gemini|copilot-instructions|conventions)\.md$|"
    r"^\.(cursorrules|windsurfrules|clinerules|aider.*|claudeignore|cursorignore)$|"
    r"\.prompt\.md$|^\.?prompts?\.(md|txt)$",
    re.IGNORECASE,
)
FOLDERS = {".claude", ".cursor", ".windsurf", ".continue", ".codex", ".gemini", ".aider", ".cline", ".roo", ".kiro"}


def _tracked_files() -> list[str]:
    try:
        out = subprocess.run(
            ["git", "-C", str(ROOT), "ls-files", "-z"], capture_output=True, check=True, timeout=60
        ).stdout
    except (OSError, subprocess.SubprocessError):
        pytest.skip("not a git checkout")
    return [name for name in out.decode("utf-8").split("\0") if name]


def _is_ai_context(name: str) -> bool:
    parts = name.split("/")
    return bool(FILE_NAMES.search(parts[-1])) or any(part in FOLDERS for part in parts[:-1])


def test_no_ai_context_outside_what_the_product_ships() -> None:
    found = [name for name in _tracked_files() if not name.startswith(SHIPPED) and _is_ai_context(name)]
    assert not found, (
        "AI development context does not belong in the repository (keep it in your own folder outside it): "
        + ", ".join(found)
    )


def test_the_rule_sees_what_it_should() -> None:
    for name in ("CLAUDE.md", "AGENTS.md", ".claude/settings.json", "kernel/.cursor/rules.mdc", "docs/x.prompt.md"):
        assert _is_ai_context(name), name
    for name in ("README.md", "docs/user-manual.md", ".claude-plugin/marketplace.json", "plugin/skills/fix/SKILL.md"):
        assert not _is_ai_context(name), name
