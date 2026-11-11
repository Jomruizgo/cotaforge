"""Everything inside is English (folders, files, commands, agents, tokens); old Spanish projects keep working.

A new project gets English names. A project with Spanish names on disk is read and written where its files are, never
renamed, and its old tokens (channel roles, review authors, ``drawing_review`` values) are still understood.
"""

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from cotaforge.contract import AnnotateDrawingRequest, DrawingReport
from cotaforge.memory import Scope

from cotaforge_mcp import channel_tools, cli, conduct, design_memory, drawing_window, overflow, rules
from cotaforge_mcp.config import Settings
from cotaforge_mcp.schemas import ChannelArgs

REPO = Path(__file__).resolve().parents[3]
OLD_TEMPLATE = (REPO / "mcp-server" / "tests" / "data" / "conduct_template_0_10_es.md").read_bytes()
SPANISH_NAMES = ("memoria", "calculos", "planos", "supuestos-y-decisiones.md", "informes", "friccion.md")


def init(folder: Path, tmp_path: Path, **kwargs: Any) -> cli.Report:
    return cli.init_space(folder, interpreter="python", adapter="fake", home=tmp_path / "home", **kwargs)


def settings_for(folder: Path, tmp_path: Path) -> Settings:
    return Settings(adapter="fake", workspace=folder, home=tmp_path / "home", base_memory=False)


# ---- a new project -------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["en", "es"])
def test_a_new_project_has_english_names_in_either_language(tmp_path: Path, language: str) -> None:
    folder = tmp_path / "new"
    init(folder, tmp_path, language=language)
    names = {p.name for p in folder.iterdir()}
    assert {"memory", "calculations", "assumptions-and-decisions.md", "CLAUDE.md"} <= names
    assert not names & set(SPANISH_NAMES)
    claude = (folder / "CLAUDE.md").read_text(encoding="utf-8")
    assert claude.startswith("# Design space") and "assumptions-and-decisions.md" in claude  # the rules are English
    allow = json.loads((folder / ".claude" / "settings.local.json").read_text(encoding="utf-8"))["permissions"]["allow"]
    assert "Edit(./drawings/**)" in allow and "Edit(./calculations/**)" in allow


def test_the_register_of_a_new_project_is_in_its_language(tmp_path: Path) -> None:
    init(tmp_path / "en", tmp_path, language="en")
    init(tmp_path / "es", tmp_path, language="es")
    assert (tmp_path / "en" / "assumptions-and-decisions.md").read_text(encoding="utf-8").startswith("# Assumptions")
    assert (tmp_path / "es" / "assumptions-and-decisions.md").read_text(encoding="utf-8").startswith("# Supuestos")


def test_a_new_project_writes_its_files_with_english_names(tmp_path: Path) -> None:
    init(tmp_path / "p", tmp_path, language="en")
    settings = settings_for(tmp_path / "p", tmp_path)
    assert rules.memory_store(settings).directory(Scope.project) == tmp_path / "p" / "memory"
    assert overflow.reports_dir(tmp_path / "p") == tmp_path / "p" / "reports"
    assert drawing_window.record_path(tmp_path / "p", "plate") == tmp_path / "p" / "drawings" / "plate-window.json"
    log, readable, lock = channel_tools._paths(tmp_path / "p", "plate")
    assert (log.name, readable.name, lock.name) == (
        "plate-questions.jsonl",
        "plate-questions.md",
        "plate-questions.lock",
    )


# ---- an old project (Spanish names on disk) ------------------------------------------------------------------------


def old_project(tmp_path: Path) -> Path:
    folder = tmp_path / "old"
    for name in ("memoria", "calculos", "planos"):
        (folder / name).mkdir(parents=True)
    (folder / "supuestos-y-decisiones.md").write_text("# Supuestos y decisiones\n", encoding="utf-8")
    return folder


def test_init_on_an_old_project_creates_no_english_twin(tmp_path: Path) -> None:
    folder = old_project(tmp_path)
    init(folder, tmp_path, language="es")
    names = {p.name for p in folder.iterdir()}
    assert {"memoria", "calculos", "planos", "supuestos-y-decisiones.md"} <= names
    assert not names & {"memory", "calculations", "assumptions-and-decisions.md"}


def test_an_old_project_reads_and_writes_where_its_files_are(tmp_path: Path) -> None:
    folder = old_project(tmp_path)
    settings = settings_for(folder, tmp_path)
    assert rules.memory_store(settings).directory(Scope.project) == folder / "memoria"
    (folder / "planos" / "bracket-preguntas.jsonl").write_text("", encoding="utf-8")
    assert channel_tools._paths(folder, "bracket")[1].name == "bracket-preguntas.md"
    assert overflow.reports_dir(folder).name == "reports", "no old folder: the English one"
    (folder / "informes").mkdir()
    assert overflow.reports_dir(folder).name == "informes"


def test_an_old_channel_log_with_spanish_tokens_is_read_and_continued_in_its_file(tmp_path: Path) -> None:
    folder = old_project(tmp_path)
    log = folder / "planos" / "bracket-preguntas.jsonl"
    event = {"n": 1, "q": 1, "at": "2026-10-01T10:00:00+00:00", "kind": "ask", "via": "dibujante", "by": "dibujante"}
    log.write_text(json.dumps({**event, "text": "¿Ajuste?"}, ensure_ascii=False) + "\n", encoding="utf-8")
    assert channel_tools.read_events(log)[0]["by"] == "drafter"
    answer = ChannelArgs(action="reply", role="diseñador", name="bracket", q=1, by="diseñador", text="H7")
    assert "recorded" in channel_tools.run(folder, answer).text
    lines = log.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[1])["by"] == "designer", "the new event goes to the old file"
    assert not list((folder / "planos").glob("*-questions.*"))
    assert "bracket" in channel_tools.run(folder, ChannelArgs(action="list")).text


def test_the_spanish_tokens_of_the_channel_are_accepted_as_input() -> None:
    args = ChannelArgs(action="reply", role="dibujante", by="ingeniero", name="x", q=1, text="y")
    assert (args.role, args.by) == ("drafter", "engineer")
    assert channel_tools._agent_name("cotaforge:dibujante") == "cotaforge:drafter"


def test_the_spanish_review_authors_are_accepted_as_input_and_read_from_old_sheets() -> None:
    request = AnnotateDrawingRequest(operation_id="a", action="review_report", sheet="Drawing", by="fabricante", at=1.0)
    assert request.by == "fabricator"
    choice = AnnotateDrawingRequest(operation_id="b", action="review_choice", sheet="Drawing", by="ingeniero")
    assert choice.by == "engineer"
    approved = AnnotateDrawingRequest(operation_id="c", action="approve", sheet="Drawing", by="ingeniero", text="Ok")
    assert approved.by == "ingeniero", "an approver's name is not a token"
    assert DrawingReport.model_validate({"n": 1, "by": "ingeniero"}).by == "engineer"


def test_an_old_brief_with_spanish_review_values_is_read(tmp_path: Path) -> None:
    folder = old_project(tmp_path)
    for old, new in design_memory.OLD_DRAWING_REVIEWS.items():
        (folder / "brief.yaml").write_text(yaml.safe_dump({"drawing_review": old}), encoding="utf-8")
        assert design_memory.load_brief(settings_for(folder, tmp_path)).drawing_review == new


# ---- the conduct file: an unedited Spanish template becomes the English one ----------------------------------------


def test_an_unedited_spanish_template_is_replaced_by_the_english_one_and_the_agent_is_told(tmp_path: Path) -> None:
    assert conduct.digest(OLD_TEMPLATE) in conduct.product_source().history, "the old template is in the history"
    folder = old_project(tmp_path)
    (folder / ".mcp.json").write_text(json.dumps({"mcpServers": {"cad": {}}}), encoding="utf-8")
    (folder / "CLAUDE.md").write_bytes(OLD_TEMPLATE)
    outcome = conduct.refresh(folder, conduct.product_source())
    assert outcome.state is conduct.State.REFRESHED and outcome.backup is not None
    assert outcome.backup.read_bytes() == OLD_TEMPLATE, "the Spanish one is kept beside it"
    assert (folder / "CLAUDE.md").read_text(encoding="utf-8").startswith("# Design space")
    text = conduct.notice(outcome, "0.10.0")
    assert "in his language" in text and "project language" in text


def test_an_edited_spanish_conduct_file_gets_the_proposal_with_the_new_name(tmp_path: Path) -> None:
    folder = old_project(tmp_path)
    (folder / "CLAUDE.md").write_bytes(OLD_TEMPLATE + b"\n- Mi regla.\n")
    outcome = conduct.refresh(folder, conduct.product_source())
    assert outcome.state is conduct.State.EDITED and outcome.proposal is not None
    assert outcome.proposal.name == "CLAUDE.md.new" and "in his language" in conduct.notice(outcome, "0.10.0")
    (outcome.proposal).unlink()
    (folder / "CLAUDE.md.nuevo").write_text("pending, made by an older version", encoding="utf-8")
    again = conduct.refresh(folder, conduct.product_source())
    assert again.proposal is not None and again.proposal.name == "CLAUDE.md.nuevo"


# ---- the rules the agent reads are English ---------------------------------------------------------------------------


def test_the_template_the_skills_and_the_agents_are_english() -> None:
    files = [
        REPO / "templates" / "design-space" / "CLAUDE.md",
        *(REPO / "plugin" / "skills").glob("*/SKILL.md"),
        REPO / "plugin" / "agents" / "reviewer.md",
        REPO / "plugin" / "agents" / "fabricator.md",
    ]
    assert len(files) == 9
    for path in files:
        text = path.read_text(encoding="utf-8")
        found = [c for c in text if c in "áéíóúñ¿¡"]
        assert not found, f"{path.name} has Spanish text: {found[:5]}"
    assert {p.parent.name for p in (REPO / "plugin" / "skills").glob("*/SKILL.md")} == {
        "plan",
        "fix",
        "declare",
        "friction",
        "resume",
        "review",
    }
    assert {p.stem for p in (REPO / "plugin" / "agents").glob("*.md")} == {"drafter", "fabricator", "reviewer"}
