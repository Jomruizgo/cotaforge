"""The conduct file of a design space keeps up with the template: an old template nobody edited is replaced (the old one
kept beside it), one the engineer edited is never touched. Every folder is temporary."""

import json
from pathlib import Path

import pytest
from cotaforge.adapters.fake import FakeAdapter

from cotaforge_mcp import cli, conduct
from cotaforge_mcp.conduct import Source, State
from cotaforge_mcp.config import Settings
from cotaforge_mcp.guide import INSTRUCTIONS_MAX_CHARS, server_instructions
from tests.conftest import make_session

REPO = Path(__file__).resolve().parents[3]
TEMPLATE = REPO / "templates" / "design-space" / "CLAUDE.md"
HISTORY = REPO / "mcp-server" / "src" / "cotaforge_mcp" / "data" / conduct.HISTORY_FILE

OLD = b"# Conduct\n\nAsk before modelling.\n"
NEW = b"# Conduct\n\nAsk before modelling.\n\nA rib clears its corner.\n"


def source(version: str = "9.9.9") -> Source:
    return Source(NEW, {conduct.digest(OLD): "0.8.1", conduct.digest(NEW): "9.9.9"}, version)


def space(folder: Path, content: bytes | None) -> Path:
    """A design space as ``init`` leaves it: the ``cad`` server declared, and a ``CLAUDE.md`` with ``content``."""
    folder.mkdir(parents=True, exist_ok=True)
    (folder / ".mcp.json").write_text(json.dumps({"mcpServers": {"cad": {"command": "python"}}}), encoding="utf-8")
    if content is not None:
        (folder / "CLAUDE.md").write_bytes(content)
    return folder


def names(folder: Path) -> set[str]:
    return {p.name for p in folder.iterdir()}


# --- the list of released versions ------------------------------------------------------------------------------------


def test_the_current_template_is_in_the_list_of_released_versions() -> None:
    """Fails when CLAUDE.md changes and the new version is not added: run mcp-server/scripts/conduct_history.py
    (never remove an entry: a design space made with that version would look edited)."""
    history = conduct.parse_history(HISTORY.read_text(encoding="utf-8"))
    assert conduct.digest(TEMPLATE.read_bytes()) in history, "run mcp-server/scripts/conduct_history.py"
    assert conduct.product_source().history == history
    assert conduct.digest(conduct.product_source().template) in history


def test_the_list_has_every_version_once_and_the_current_one_last() -> None:
    versions = json.loads(HISTORY.read_text(encoding="utf-8"))["versions"]
    hashes = [item["sha256"] for item in versions]
    assert len(hashes) == len(set(hashes)) > 1
    assert hashes[-1] == conduct.digest(TEMPLATE.read_bytes())
    assert all(len(h) == 64 and item["since"] for h, item in zip(hashes, versions, strict=True))


def test_adding_a_version_is_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "history.json"
    path.write_text(json.dumps({"versions": [{"sha256": conduct.digest(OLD), "since": "0.8.1"}]}), encoding="utf-8")
    assert conduct.add_version(path, NEW, "0.9.1") is True
    assert conduct.add_version(path, NEW.replace(b"\n", b"\r\n"), "0.9.2") is False, "same text, other line endings"
    assert conduct.parse_history(path.read_text(encoding="utf-8")) == {
        conduct.digest(OLD): "0.8.1",
        conduct.digest(NEW): "0.9.1",
    }


def test_line_endings_and_a_byte_order_mark_do_not_change_the_digest() -> None:
    assert conduct.digest(OLD) == conduct.digest(OLD.replace(b"\n", b"\r\n")) == conduct.digest(b"\xef\xbb\xbf" + OLD)
    assert conduct.digest(OLD) != conduct.digest(OLD + b" ")


# --- refresh ----------------------------------------------------------------------------------------------------------


def test_an_unedited_old_template_is_replaced_and_the_old_one_kept(tmp_path: Path) -> None:
    folder = space(tmp_path / "d", OLD)
    outcome = conduct.refresh(folder, source())
    assert outcome.state is State.REFRESHED and outcome.since == "0.8.1"
    assert (folder / "CLAUDE.md").read_bytes() == NEW
    assert outcome.backup is not None and outcome.backup.parent == folder
    assert outcome.backup.name == f"CLAUDE.md.0.8.1-{conduct.digest(OLD)[:8]}.bak"
    assert outcome.backup.read_bytes() == OLD
    text = conduct.notice(outcome, "9.9.9")
    assert "9.9.9" in text and outcome.backup.name in text and "read CLAUDE.md again" in text
    assert names(folder) == {".mcp.json", "CLAUDE.md", outcome.backup.name}, "no other file written"


def test_the_next_start_has_nothing_to_say(tmp_path: Path) -> None:
    folder = space(tmp_path / "d", OLD)
    conduct.refresh(folder, source())
    again = conduct.refresh(folder, source())
    assert again.state is State.CURRENT and conduct.notice(again, "9.9.9") == ""


def test_an_old_template_with_windows_line_endings_is_still_unedited(tmp_path: Path) -> None:
    folder = space(tmp_path / "d", OLD.replace(b"\n", b"\r\n"))
    outcome = conduct.refresh(folder, source())
    assert outcome.state is State.REFRESHED
    assert outcome.backup is not None and outcome.backup.read_bytes() == OLD.replace(b"\n", b"\r\n")
    assert (folder / "CLAUDE.md").read_bytes() == NEW


def test_the_current_template_is_left_untouched(tmp_path: Path) -> None:
    folder = space(tmp_path / "d", NEW)
    before = (folder / "CLAUDE.md").stat().st_mtime_ns
    outcome = conduct.refresh(folder, source())
    assert outcome.state is State.CURRENT
    assert (folder / "CLAUDE.md").stat().st_mtime_ns == before
    assert names(folder) == {".mcp.json", "CLAUDE.md"}


def test_the_current_template_with_other_line_endings_counts_as_unedited_and_is_not_rewritten(tmp_path: Path) -> None:
    crlf = NEW.replace(b"\n", b"\r\n")
    folder = space(tmp_path / "d", crlf)
    outcome = conduct.refresh(folder, source())
    assert outcome.state is State.CURRENT
    assert (folder / "CLAUDE.md").read_bytes() == crlf
    assert names(folder) == {".mcp.json", "CLAUDE.md"}


def test_an_edited_file_is_kept_and_the_current_template_is_left_beside_it(tmp_path: Path) -> None:
    mine = OLD + b"\nMy own rule: always H7.\n"
    folder = space(tmp_path / "d", mine)
    outcome = conduct.refresh(folder, source())
    assert outcome.state is State.EDITED and outcome.announce
    assert (folder / "CLAUDE.md").read_bytes() == mine
    assert outcome.proposal == folder / "CLAUDE.md.new" and outcome.proposal.read_bytes() == NEW
    text = conduct.notice(outcome, "9.9.9")
    assert "edited CLAUDE.md" in text and "CLAUDE.md.new" in text and "never replace his file" in text
    assert names(folder) == {".mcp.json", "CLAUDE.md", "CLAUDE.md.new", ".cotaforge"}


def test_an_edited_file_is_told_at_every_start_while_the_proposal_exists(tmp_path: Path) -> None:
    folder = space(tmp_path / "d", OLD + b"mine\n")
    first = conduct.refresh(folder, source())
    for _ in range(2):
        again = conduct.refresh(folder, source())
        assert again.state is State.EDITED and again.announce and "CLAUDE.md.new" in conduct.notice(again, "9.9.9")
    assert first.announce and (folder / "CLAUDE.md").read_bytes() == OLD + b"mine\n"


def test_removing_the_proposal_closes_it_until_the_template_changes(tmp_path: Path) -> None:
    folder = space(tmp_path / "d", OLD + b"mine\n")
    conduct.refresh(folder, source())
    (folder / "CLAUDE.md.new").unlink()  # the agent merged what he accepted and removed it
    closed = conduct.refresh(folder, source())
    assert closed.state is State.EDITED and not closed.announce and conduct.notice(closed, "9.9.9") == ""
    assert not (folder / "CLAUDE.md.new").exists(), "not written again"
    newer = Source(NEW + b"One more rule.\n", {}, "10.0.0")
    third = conduct.refresh(folder, newer)
    assert third.announce, "a newer template is worth telling again"
    assert (folder / "CLAUDE.md.new").read_bytes() == newer.template
    assert (folder / "CLAUDE.md").read_bytes() == OLD + b"mine\n"


def test_the_proposal_goes_away_when_the_engineer_has_merged_it(tmp_path: Path) -> None:
    folder = space(tmp_path / "d", OLD + b"mine\n")
    conduct.refresh(folder, source())
    (folder / "CLAUDE.md").write_bytes(NEW)  # he took the template as it is
    assert conduct.refresh(folder, source()).state is State.CURRENT
    assert not (folder / "CLAUDE.md.new").exists()


def test_a_proposal_the_engineer_changed_is_never_deleted(tmp_path: Path) -> None:
    folder = space(tmp_path / "d", NEW)
    (folder / "CLAUDE.md.new").write_bytes(b"my notes")
    conduct.refresh(folder, source())
    assert (folder / "CLAUDE.md.new").read_bytes() == b"my notes"


def test_without_a_conduct_file_nothing_is_written(tmp_path: Path) -> None:
    folder = space(tmp_path / "d", None)
    assert conduct.refresh(folder, source()).state is State.ABSENT
    assert names(folder) == {".mcp.json"}


# --- at server start ---------------------------------------------------------------------------------------------


class Named(FakeAdapter):
    """The FreeCAD adapter's name over the fake CAD."""

    name = "freecad"


class OwnConduct(FakeAdapter):
    """A harness for another CAD: it gives its own template."""

    def conduct_source(self) -> Source:
        return source("2.0.0")


def test_the_server_start_refreshes_the_space_of_its_harness_only(tmp_path: Path) -> None:
    folder = space(tmp_path / "d", OLD)
    assert conduct.server_notice(FakeAdapter(), folder) == "", "an adapter with no template of its own: nothing"
    assert (folder / "CLAUDE.md").read_bytes() == OLD
    text = conduct.server_notice(OwnConduct(), folder)
    assert "2.0.0" in text and (folder / "CLAUDE.md").read_bytes() == NEW


def test_the_freecad_adapter_uses_the_packaged_template() -> None:
    found = conduct.source_of(Named())
    assert found is not None and found.template == conduct.product_source().template
    assert found.version == conduct.product_source().version


def test_the_packaged_template_is_what_a_fresh_space_gets_and_nothing_is_said(tmp_path: Path) -> None:
    folder = space(tmp_path / "d", conduct.product_source().template)
    assert conduct.server_notice(Named(), folder) == ""


def test_a_folder_that_is_not_a_design_space_is_never_touched(tmp_path: Path) -> None:
    folder = tmp_path / "other"
    folder.mkdir()
    (folder / "CLAUDE.md").write_bytes(b"someone else's file")
    assert conduct.server_notice(OwnConduct(), folder) == ""
    assert names(folder) == {"CLAUDE.md"}
    (folder / ".mcp.json").write_text("{not json", encoding="utf-8")
    assert conduct.server_notice(OwnConduct(), folder) == ""
    assert (folder / "CLAUDE.md").read_bytes() == b"someone else's file"


def test_a_conduct_file_that_cannot_be_written_does_not_stop_the_server(tmp_path: Path) -> None:
    folder = space(tmp_path / "d", OLD)
    (folder / f"CLAUDE.md.0.8.1-{conduct.digest(OLD)[:8]}.bak").mkdir()  # the backup path is a folder: write fails
    assert conduct.server_notice(OwnConduct(), folder) == ""
    assert (folder / "CLAUDE.md").read_bytes() == OLD


def test_the_notice_reaches_the_agent_in_the_instructions_within_the_limit(tmp_path: Path) -> None:
    folder = space(tmp_path / "d", OLD + b"mine\n")
    settings = Settings(adapter="fake", workspace=folder, home=tmp_path / "home", base_memory=False)
    session = make_session(settings, OwnConduct())
    notice = conduct.server_notice(session.adapter, settings.workspace)
    text = server_instructions(session, notice)
    assert notice and notice in text and text.index("Conduct:") < text.index(notice)
    assert len(text) <= INSTRUCTIONS_MAX_CHARS
    assert server_instructions(session) == server_instructions(session, "")


@pytest.mark.parametrize("state", ["refreshed", "edited"])
def test_the_notice_is_short(state: str, tmp_path: Path) -> None:
    folder = space(tmp_path / "d", OLD if state == "refreshed" else OLD + b"mine\n")
    assert 0 < len(conduct.server_notice(OwnConduct(), folder)) < 700


# --- init --------------------------------------------------------------------------------------------------------


@pytest.fixture
def past(monkeypatch: pytest.MonkeyPatch) -> None:
    """The product's current template is ``NEW`` and ``OLD`` is a version it released."""
    monkeypatch.setattr(conduct, "product_source", source)


def init(folder: Path, home: Path, **kwargs: object) -> cli.Report:
    return cli.init_space(folder, interpreter="/venv/python", adapter="fake", home=home, **kwargs)  # type: ignore[arg-type]


def test_init_again_updates_an_unedited_conduct_file(tmp_path: Path, past: None) -> None:
    home = tmp_path / "home"
    home.mkdir()
    folder = tmp_path / "d"
    (folder / "memory").mkdir(parents=True)
    (folder / "memory" / "mine.md").write_text("my memory", encoding="utf-8")
    (folder / "CLAUDE.md").write_bytes(OLD)
    report = init(folder, home)
    line = next(line for line in report.lines if " CLAUDE.md" in line)
    assert line.split()[0] == "actualizado" and ".bak" in line
    assert (folder / "CLAUDE.md").read_bytes() == NEW
    assert (folder / f"CLAUDE.md.0.8.1-{conduct.digest(OLD)[:8]}.bak").read_bytes() == OLD
    assert (folder / "memory" / "mine.md").read_text(encoding="utf-8") == "my memory"
    assert not (folder / "memory" / ".gitkeep").exists()


def test_init_again_keeps_an_edited_conduct_file_and_says_where_the_template_is(tmp_path: Path, past: None) -> None:
    home = tmp_path / "home"
    home.mkdir()
    folder = tmp_path / "d"
    folder.mkdir()
    (folder / "CLAUDE.md").write_bytes(b"my own conduct")
    report = init(folder, home)
    line = next(line for line in report.lines if " CLAUDE.md" in line)
    assert line.split()[0] == "conservado" and "CLAUDE.md.new" in line and "--force" in line
    assert (folder / "CLAUDE.md").read_bytes() == b"my own conduct"
    assert (folder / "CLAUDE.md.new").read_bytes() == NEW


def test_init_twice_on_a_fresh_space_changes_nothing(tmp_path: Path) -> None:
    home = tmp_path / "home"
    home.mkdir()
    folder = tmp_path / "d"
    init(folder, home)
    report = init(folder, home)
    assert not any(" CLAUDE.md" in line and line.split()[0] != "sin" for line in report.lines)
    assert not any(name.startswith("CLAUDE.md.") for name in names(folder))


def test_init_again_after_the_proposal_was_removed_does_not_point_at_it(tmp_path: Path, past: None) -> None:
    home = tmp_path / "home"
    home.mkdir()
    folder = tmp_path / "d"
    folder.mkdir()
    (folder / "CLAUDE.md").write_bytes(b"my own conduct")
    init(folder, home)
    (folder / "CLAUDE.md.new").unlink()
    line = next(line for line in init(folder, home).lines if " CLAUDE.md" in line)
    assert line.split()[0] == "conservado" and "CLAUDE.md.new" not in line.replace("quitaste", "") and "--force" in line
    assert not (folder / "CLAUDE.md.new").exists()
