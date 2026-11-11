"""The static parts of the product that Claude Code loads: ``plugin/`` and ``templates/``.

Nothing here talks to a CAD: the files must parse and carry no trace of the place they were built in.
"""

import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

from cotaforge_mcp.schemas import TOOLS
from cotaforge_mcp.schemas import TOOLS_BY_NAME as TOOLS_BY_NAME_FOR_AGENT

REPO = Path(__file__).resolve().parents[3]
PLUGIN = REPO / "plugin"
TEMPLATES = REPO / "templates"
DESIGN_SPACE = TEMPLATES / "design-space"
SERVER_PACKAGE = REPO / "mcp-server" / "src" / "cotaforge_mcp"
PACKAGED_DESIGN_SPACE = SERVER_PACKAGE / "data" / "design-space"  # the copy `cotaforge init` ships with the package
CLI_SOURCE = SERVER_PACKAGE / "cli.py"

SERVER_NAME = "cad"
RUN_NATIVE_TOOL = f"mcp__{SERVER_NAME}__run_native"

# The names of the place the product was built in and of the project it came from must not ship.
BANNED = re.compile(
    r"cadharness|cad-harness|workshop|taller de construcci|bicicleta|bicycle|v0\.6\.1|claude-code-session|"
    r"/home/jomruizgo|(?<!github\.com/)jomruizgo(?!\.github\.io)|\\\\wsl",  # the public repository's URLs are fine
    re.IGNORECASE,
)
TEXT_SUFFIXES = {".md", ".json", ".yaml", ".yml", ".txt", ".toml", ".py", ".sh", ""}


def shipped_files() -> list[Path]:
    return sorted(
        path
        for root in (PLUGIN, TEMPLATES, PACKAGED_DESIGN_SPACE)
        for path in root.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )


def split_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    match = re.match(r"---\n(.*?)\n---\n(.*)", text, re.DOTALL)
    assert match, f"{path} has no YAML frontmatter"
    data = yaml.safe_load(match.group(1))
    assert isinstance(data, dict), f"{path} frontmatter is not a mapping"
    return data, match.group(2)


def skill_files() -> list[Path]:
    return sorted(PLUGIN.glob("skills/*/SKILL.md"))


def agent_files() -> list[Path]:
    return sorted(PLUGIN.glob("agents/*.md"))


# --- JSON files -----------------------------------------------------------------------------------------------------


def test_every_json_file_parses() -> None:
    files = [p for p in shipped_files() if p.suffix == ".json"]
    assert files
    for path in files:
        json.loads(path.read_text(encoding="utf-8"))


def test_manifest_names_the_plugin() -> None:
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert manifest["name"] == "cotaforge"
    assert manifest["description"]
    assert manifest["version"]


def test_plugin_ships_no_mcp_json_nor_permissions_nor_claude_md() -> None:
    """``init`` writes ``.mcp.json`` with the absolute interpreter path; a plugin cannot carry permissions or a
    ``CLAUDE.md``."""
    assert not (PLUGIN / ".mcp.json").exists()
    assert not (PLUGIN / "CLAUDE.md").exists()
    assert not (PLUGIN / ".claude").exists()
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert "mcpServers" not in manifest
    assert "permissions" not in manifest


# --- skills and agents ----------------------------------------------------------------------------------------------


def test_the_expected_skills_exist() -> None:
    names = {path.parent.name for path in skill_files()}
    assert {"resume", "fix", "declare", "review", "friction", "plan"} <= names


@pytest.mark.parametrize("path", skill_files(), ids=lambda p: p.parent.name)
def test_skill_frontmatter_has_name_and_description(path: Path) -> None:
    data, body = split_frontmatter(path)
    assert data["name"] == path.parent.name
    assert isinstance(data["description"], str)
    assert 20 < len(data["description"]) <= 400
    assert body.strip()
    assert len(body.splitlines()) < 40, "a skill stays short"


@pytest.mark.parametrize("path", agent_files(), ids=lambda p: p.stem)
def test_agent_frontmatter_has_name_and_description(path: Path) -> None:
    data, body = split_frontmatter(path)
    assert data["name"] == path.stem
    assert isinstance(data["description"], str)
    assert data["description"]
    assert body.strip()


def test_the_reviewer_agent_exists_and_cannot_edit_the_model() -> None:
    data, _ = split_frontmatter(PLUGIN / "agents" / "reviewer.md")
    tools = {tool.strip() for tool in str(data["tools"]).split(",")}
    assert tools
    server_tools = {tool.name for tool in TOOLS}
    assert "Read" in tools, "the reviewer reads assumptions-and-decisions.md"
    for tool in tools - {"Read"}:  # Read is the only built-in tool allowed: it cannot change anything
        prefix, _, name = tool.removeprefix("mcp__").partition("__")
        assert prefix == SERVER_NAME
        assert name in server_tools, f"{tool} is not a tool of the server"
    for forbidden in ("op", "undo", "run_native", "export", "doc"):
        assert f"mcp__{SERVER_NAME}__{forbidden}" not in tools


def test_tool_names_the_skills_cite_exist() -> None:
    server_tools = {tool.name for tool in TOOLS}
    cited = set()
    for path in [*skill_files(), *agent_files()]:
        cited |= set(re.findall(r"`(\w+)` with `action=", path.read_text(encoding="utf-8")))
    assert cited
    assert cited <= server_tools


# --- hook -----------------------------------------------------------------------------------------------------------


# The ``run_native`` PreToolUse hook is written by ``cotaforge init`` (with the absolute interpreter that has the
# product installed) and tested through that command in test_cli.py and in the acceptance script. The plugin has none.


def test_run_native_is_a_tool_of_the_server() -> None:
    assert RUN_NATIVE_TOOL.rsplit("__", 1)[1] in {tool.name for tool in TOOLS}


def test_the_plugin_ships_no_hooks_that_name_an_interpreter() -> None:
    """A bare ``python`` may not be the interpreter that has cotaforge: the only hook is the one ``init`` writes."""
    assert not (PLUGIN / "hooks").exists()
    manifest = json.loads((PLUGIN / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    assert "hooks" not in manifest
    for path in [*skill_files(), *agent_files()]:
        data, _ = split_frontmatter(path)
        assert "hooks" not in data, f"{path.relative_to(REPO)} declares hooks"
    interpreter = re.compile(r"\bpython[\d.]*(\.exe)?['\"]?\s+-m\b")
    offenders = [
        path.relative_to(REPO).as_posix()
        for path in shipped_files()
        if path.is_relative_to(PLUGIN)
        and path.suffix in TEXT_SUFFIXES
        and interpreter.search(path.read_text(encoding="utf-8"))
    ]
    assert not offenders, offenders


# --- design space template ------------------------------------------------------------------------------------------


def load_settings() -> dict[str, Any]:
    return json.loads((DESIGN_SPACE / ".claude" / "settings.local.json").read_text(encoding="utf-8"))


def test_design_space_template_has_conduct_settings_and_empty_memory() -> None:
    claude_md = (DESIGN_SPACE / "CLAUDE.md").read_text(encoding="utf-8")
    assert "CAD software" in claude_md
    assert len(claude_md.splitlines()) < 43, (
        "the conduct stays short"
    )  # 41 since "Commercial first"; 42 with the hole colours
    memory = sorted(p.name for p in (DESIGN_SPACE / "memory").iterdir())
    assert memory == [".gitkeep"]


def test_settings_allow_the_server_tools_and_exclude_inherited_claude_md() -> None:
    settings = load_settings()
    allow = settings["permissions"]["allow"]
    assert f"mcp__{SERVER_NAME}" in allow, "run_native stays in allow: the hook turns a risky call into a question"
    assert not any("deny" in key for key in settings["permissions"])
    assert settings["claudeMdExcludes"]
    assert all(isinstance(pattern, str) and pattern for pattern in settings["claudeMdExcludes"])


# --- banned words ---------------------------------------------------------------------------------------------------


def test_there_are_files_to_scan() -> None:
    assert len(shipped_files()) >= 10


def test_no_file_mentions_the_workshop_or_the_old_project() -> None:
    offenders = []
    for path in shipped_files():
        if path.suffix not in TEXT_SUFFIXES:
            continue
        text = path.read_text(encoding="utf-8")
        match = BANNED.search(text) or BANNED.search(path.relative_to(REPO).as_posix())
        if match:
            offenders.append(f"{path.relative_to(REPO)}: {match.group(0)!r}")
    assert not offenders, "\n".join(offenders)


def test_the_init_command_source_mentions_neither_the_workshop_nor_the_old_project() -> None:
    match = BANNED.search(CLI_SOURCE.read_text(encoding="utf-8"))
    assert match is None, match and match.group(0)


@pytest.mark.parametrize("name", ["layout.py", "plugin_registry.py", "launcher.py"])
def test_the_plugin_and_launcher_sources_mention_neither_the_workshop_nor_the_old_project(name: str) -> None:
    match = BANNED.search((SERVER_PACKAGE / name).read_text(encoding="utf-8"))
    assert match is None, match and match.group(0)


def test_the_installer_files_mention_neither_the_workshop_nor_the_old_project() -> None:
    files = [
        *(REPO / "packaging").rglob("*"),
        REPO / ".claude-plugin" / "marketplace.json",
        REPO / ".github" / "workflows" / "windows-installer.yml",
    ]
    scanned = [p for p in files if p.is_file() and p.suffix in {".iss", ".ps1", ".json", ".yml", ".md", ".txt"}]
    assert len(scanned) >= 4
    for path in scanned:
        match = BANNED.search(path.read_text(encoding="utf-8"))
        assert match is None, f"{path.relative_to(REPO)}: {match and match.group(0)!r}"


def test_what_init_prints_and_writes_mentions_neither_the_workshop_nor_the_old_project(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Paths are the machine's own (this test's folder is under the developer's home), so they are replaced before the
    scan: what remains is the text the command adds."""
    from cotaforge_mcp import cli  # noqa: PLC0415

    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "CLAUDE.md").write_text("x", encoding="utf-8")
    monkeypatch.setattr(sys, "executable", "/opt/python/bin/python")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    folder = tmp_path / "space"
    argv = [
        "init",
        str(folder),
        "--freecad-root",
        str(tmp_path / "freecad"),
        "--visible",
        "--plugin-dir",
        str(tmp_path),
    ]
    assert cli.main(argv) == 0
    texts = [capsys.readouterr().out]
    texts += [p.read_text(encoding="utf-8") for p in folder.rglob("*") if p.is_file() and p.name != ".gitkeep"]
    for text in texts:
        generic = text.replace(tmp_path.as_posix(), "<tmp>").replace(str(tmp_path), "<tmp>")
        generic = generic.replace(json.dumps(str(tmp_path))[1:-1], "<tmp>")
        assert BANNED.search(generic) is None, generic
    assert len(texts) >= 4


def test_no_build_context_ships_in_the_product() -> None:
    claude_md_files = sorted(p for p in REPO.rglob("CLAUDE.md") if ".venv" not in p.parts)
    assert claude_md_files == sorted([DESIGN_SPACE / "CLAUDE.md", PACKAGED_DESIGN_SPACE / "CLAUDE.md"]), (
        "only the design-space template (and its packaged copy) may carry a CLAUDE.md"
    )
    assert not (REPO / ".claude").exists()


def test_the_manufacturer_agent_sees_the_drawing_and_never_the_model() -> None:
    data, body = split_frontmatter(PLUGIN / "agents" / "fabricator.md")
    tools = {tool.strip() for tool in str(data["tools"]).split(",")}
    assert tools == {f"mcp__{SERVER_NAME}__{name}" for name in ("drawing", "memory", "cad_guide")}
    for forbidden in ("inspect", "view", "op", "undo", "run_native", "export", "doc", "catalog"):
        assert f"mcp__{SERVER_NAME}__{forbidden}" not in tools  # no model, no model views, nothing that edits
    for needle in (
        'action="capture"',
        'action="read"',
        'action="review_report"',
        'by="fabricator"',
        "items=[]",
        "drawing-",
    ):
        assert needle in body, needle
    drawing = TOOLS_BY_NAME_FOR_AGENT["drawing"]
    assert "review_report" in drawing.model.model_json_schema()["properties"]["action"]["enum"]


def test_the_drafting_agent_runs_as_a_main_session_and_never_changes_the_model() -> None:
    """``claude --agent cotaforge:drafter``: the engineer watches and corrects it, so it has the tools of a session
    (documents, export, the question channel, the manufacturer through ``Agent``) but no operation that edits the part."""
    data, body = split_frontmatter(PLUGIN / "agents" / "drafter.md")
    tools = {tool.strip() for tool in str(data["tools"]).split(",")}
    server_tools = {tool.name for tool in TOOLS}
    for tool in tools - {"Read", "Agent"}:
        prefix, _, name = tool.removeprefix("mcp__").partition("__")
        assert prefix == SERVER_NAME and name in server_tools, tool
    wanted = ("drawing", "doc", "export", "inspect", "memory", "cad_guide", "channel")
    assert {f"mcp__{SERVER_NAME}__{n}" for n in wanted} <= tools
    for forbidden in ("op", "undo", "run_native", "catalog"):
        assert f"mcp__{SERVER_NAME}__{forbidden}" not in tools
    assert not {"Bash", "Write", "Edit"} & tools, "it writes no file: the channel keeps its questions"
    text = (str(data["description"]) + body).lower()
    for needle in (
        "claude --agent cotaforge:drafter",
        "one session at a time",
        'action="open"',
        "drawings/<part>-intent.md",
        'role="drafter"',
        'by="engineer"',
        "to the designer",
        'action="dispute"',
        'action="decide"',
        "objection=",
        "never answer your own question",
        "request_review",
        "fabricator",
    ):
        assert needle.lower() in text, needle


def test_the_designer_template_hands_the_part_over_and_listens_to_the_drafting_session() -> None:
    claude_md = (DESIGN_SPACE / "CLAUDE.md").read_text(encoding="utf-8")
    assert "claude --agent cotaforge:drafter" in claude_md
    assert "drawings/<part>-intent.md" in claude_md
    assert "`doc` with `close`" in claude_md, "the file is open in one session at a time"
    assert "`channel`" in claude_md and 'role="designer"' in claude_md and "assumptions-and-decisions.md" in claude_md
    drafting = (PLUGIN / "agents" / "drafter.md").read_text(encoding="utf-8")
    for heading in ("function", "mating faces", "fits", "datums", "process", "material", "welds", "sheet"):
        assert heading in claude_md.lower() and heading in drafting.lower(), heading


def test_the_template_lets_both_sessions_write_the_handoff_file() -> None:
    assert "Edit(./drawings/**)" in load_settings()["permissions"]["allow"]
