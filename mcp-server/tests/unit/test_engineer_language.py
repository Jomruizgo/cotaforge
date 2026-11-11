"""The engineer's language (``es`` / ``en``): where it lives, who reads it, and what follows it.

It lives in the project (``language`` of ``brief.yaml``), is seeded at install (``install.json``), is chosen by
``cotaforge init --language`` and changed with ``memory(action=brief, language=...)``. A project that states none is
Spanish, as every project was before the setting existed.
"""

import ast
import json
import re
from pathlib import Path
from typing import Any

import cotaforge
import pytest
import yaml
from cotaforge import language as codes
from cotaforge.language import normalize, resolve, say

from cotaforge_mcp import channel_tools, cli, design_memory, drawing_gate, guide, launcher, layout, plugin_registry
from cotaforge_mcp.config import Settings
from tests.conftest import Caller, DrawingFake, MakeSession, plate, set_review

# ---- the two codes -------------------------------------------------------------------------------------------------


def test_the_language_names_an_installer_or_a_locale_write_are_understood() -> None:
    assert [normalize(v) for v in ("es", "ES", " en ", "en_US", "es-CO", "spanish", "English", "Español")] == [
        "es",
        "es",
        "en",
        "en",
        "es",
        "es",
        "en",
        "es",
    ]


@pytest.mark.parametrize("value", [None, "", "  ", "fr", "klingon", 3, ["es"]])
def test_anything_else_is_no_language_and_resolves_to_the_default(value: Any) -> None:
    assert normalize(value) is None
    assert resolve(value) == codes.DEFAULT_LANGUAGE == "es"
    assert resolve(value, "en") == "en"


def test_say_picks_the_wording_of_the_language() -> None:
    assert say("es", "hola", "hello") == "hola"
    assert say("en", "hola", "hello") == "hello"


# ---- the project: brief.yaml ---------------------------------------------------------------------------------------


def test_a_project_that_states_no_language_is_spanish(tmp_path: Path) -> None:
    settings = Settings(adapter="fake", workspace=tmp_path, home=tmp_path / "home", base_memory=False)
    assert design_memory.project_language(settings) == "es"  # no brief.yaml at all
    (tmp_path / "brief.yaml").write_text("sector: mobiliario\n", encoding="utf-8")
    assert design_memory.project_language(settings) == "es" and design_memory.stated_language(tmp_path) is None
    (tmp_path / "brief.yaml").write_text("language: fr\n", encoding="utf-8")
    assert design_memory.project_language(settings) == "es"  # an unknown value is not a choice
    (tmp_path / "brief.yaml").write_text("language: en\n", encoding="utf-8")
    assert design_memory.project_language(settings) == "en" and design_memory.load_brief(settings).lang == "en"


def test_writing_the_language_keeps_the_rest_of_the_brief(tmp_path: Path) -> None:
    assert design_memory.write_language(tmp_path, "en") == "created"
    (tmp_path / "brief.yaml").write_text("sector: mobiliario\nprocesses: [soldadura]\n", encoding="utf-8")
    assert design_memory.write_language(tmp_path, "es") == "updated"
    assert design_memory.write_language(tmp_path, "es") == "unchanged"
    assert yaml.safe_load((tmp_path / "brief.yaml").read_text(encoding="utf-8")) == {
        "language": "es",
        "sector": "mobiliario",
        "processes": ["soldadura"],
    }
    with pytest.raises(ValueError, match="unknown language"):
        design_memory.write_language(tmp_path, "fr")


def test_the_brief_tool_changes_the_language_of_the_project(call: Caller, settings: Settings) -> None:
    assert call.session.language == "es"
    text = call.ok("memory", action="brief")
    assert "language es (the default: none stated)" in text
    text = call.ok("memory", action="brief", language="en")
    assert "language en" in text and "none stated" not in text
    assert call.session.language == "en"  # read from the file when asked: no restart
    assert yaml.safe_load((settings.workspace / "brief.yaml").read_text(encoding="utf-8")) == {"language": "en"}
    call.ok("memory", action="brief", sector="Mobiliario")  # saving the sector keeps the language
    assert design_memory.project_language(settings) == "en"
    call.err("memory", action="brief", language="fr")


def test_the_agent_is_told_the_language_in_the_conduct(session: Any, settings: Settings) -> None:
    assert "Spanish (es)" in guide.language_conduct("es") and "English (en)" in guide.language_conduct("en")
    assert any("Spanish (es)" in line for line in guide.conduct(session))
    (settings.workspace / "brief.yaml").write_text("language: en\n", encoding="utf-8")
    assert any("English (en)" in line for line in guide.conduct(session))


# ---- the sheet takes the project's language unless the call states one ---------------------------------------------


@pytest.fixture
def sheet(make: MakeSession, settings: Settings) -> tuple[Caller, DrawingFake]:
    set_review(settings, "ninguno")
    adapter = DrawingFake()
    call = Caller(make(adapter))
    plate(call)
    return call, adapter


def test_a_new_sheet_is_in_the_language_of_the_project(sheet: tuple[Caller, DrawingFake], settings: Settings) -> None:
    call, adapter = sheet
    call.ok("drawing", action="create", sheet="uno")
    assert adapter.created[-1].language == "es"
    design_memory.write_language(settings.workspace, "en")
    call.ok("drawing", action="create", sheet="dos")
    assert adapter.created[-1].language == "en"
    assert "language" in adapter.created[-1].unset  # a rebuild of an existing sheet keeps the language it has
    call.ok("drawing", action="create", sheet="tres", language="es")  # what the call says wins
    assert adapter.created[-1].language == "es" and "language" not in adapter.created[-1].unset


# ---- the installer: install.json -----------------------------------------------------------------------------------


def test_the_installer_records_the_language_it_was_run_in(tmp_path: Path) -> None:
    (tmp_path / "app").mkdir()
    app = tmp_path / "app"
    layout.write_install_info(app, version="1", language="english")  # Inno names the language, not its code
    assert layout.install_language(layout.read_install_info(app)) == "en"
    layout.write_install_info(app, version="1", language="klingon")
    assert "language" not in layout.read_install_info(app) and layout.install_language({}) is None
    assert cli.main(["install-info", "--version", "9", "--app-dir", str(app), "--language", "spanish"]) == 0
    assert json.loads((app / "install.json").read_text(encoding="utf-8"))["language"] == "es"


# ---- init: --language, the project's, the installer's, Spanish -----------------------------------------------------


def init(folder: Path, tmp_path: Path, **kwargs: Any) -> cli.Report:
    return cli.init_space(folder, interpreter="python", adapter="fake", home=tmp_path / "home", **kwargs)


def stated(folder: Path) -> Any:
    return yaml.safe_load((folder / "brief.yaml").read_text(encoding="utf-8")).get("language")


def test_init_with_a_language_writes_it_to_the_project_and_speaks_it(tmp_path: Path) -> None:
    folder = tmp_path / "p"
    report = init(folder, tmp_path, language="en")
    assert stated(folder) == "en" and report.language == "en"
    text = cli.format_report(report, "/plugin")
    assert "Design space ready" in text and "created" in text and "Next step" in text
    assert "listo" not in text and "creado" not in text


def test_init_without_a_language_gives_a_new_project_the_one_chosen_at_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(layout, "install_language", lambda info=None: "en")
    folder = tmp_path / "p"
    report = init(folder, tmp_path)
    assert stated(folder) == "en" and "Design space ready" in cli.format_report(report, "/plugin")


def test_init_with_nothing_chosen_is_spanish_and_says_so_in_the_project(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(layout, "install_language", lambda info=None: None)
    folder = tmp_path / "p"
    report = init(folder, tmp_path)
    assert stated(folder) == "es" and "Espacio de diseño listo" in cli.format_report(report, "/plugin")


def test_init_keeps_the_language_a_project_has_and_changes_it_only_when_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "p"
    init(folder, tmp_path, language="en")
    monkeypatch.setattr(layout, "install_language", lambda info=None: "es")  # the install says Spanish: not the project
    again = init(folder, tmp_path)
    assert stated(folder) == "en" and again.language == "en" and "unchanged" in "\n".join(again.lines)
    changed = init(folder, tmp_path, language="es")
    assert stated(folder) == "es" and changed.language == "es"
    assert any("brief.yaml" in line and "actualizado" in line for line in changed.lines)


def test_an_older_project_with_no_language_keeps_the_one_it_always_had(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    folder = tmp_path / "p"
    init(folder, tmp_path, language="es")
    (folder / "brief.yaml").unlink()  # a project of before the setting: files of the design space, no language
    monkeypatch.setattr(layout, "install_language", lambda info=None: "en")  # an English install must not flip it
    report = init(folder, tmp_path)
    assert report.language == "es" and not (folder / "brief.yaml").exists()
    assert "Espacio de diseño listo" in cli.format_report(report, "/plugin")


def test_the_command_line_takes_language_and_answers_in_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plugin_registry, "is_registered", lambda *a, **k: False)
    assert cli.main(["init", str(tmp_path / "p"), "--adapter", "fake", "--language", "en"]) == 0
    assert "Design space ready" in capsys.readouterr().out and stated(tmp_path / "p") == "en"
    assert "create or update a design space" in cli.build_parser("en").format_help()
    assert "crea o actualiza un espacio de diseño" in cli.build_parser("es").format_help()


def test_an_init_error_is_in_the_language_of_the_project(tmp_path: Path) -> None:
    folder = tmp_path / "p"
    (folder).mkdir()
    (folder / ".mcp.json").write_text("{", encoding="utf-8")
    with pytest.raises(cli.InitError, match="not valid JSON"):
        init(folder, tmp_path, language="en")
    with pytest.raises(cli.InitError, match="no es un JSON válido"):
        init(folder, tmp_path, language="es")


# ---- a sample of the messages of each module, in both languages ----------------------------------------------------


def test_the_launcher_dialogs_follow_the_language(tmp_path: Path) -> None:
    for language, word in (("es", "listo"), ("en", "ready")):
        report = cli.Report(tmp_path, language=language, warnings=["w"])
        text = launcher.instructions(tmp_path, report, [], None)
        assert word in text and ("Claude Code was not found" in text) == (language == "en")


def test_the_plugin_registry_follows_the_language(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin_registry, "find_claude", lambda *a, **k: None)
    monkeypatch.setattr(layout, "find_marketplace_dir", lambda: None)
    try:
        plugin_registry.use_language("en")
        assert "not found" in plugin_registry.status().lines[0] and "was not found" in plugin_registry.claude_missing()
        plugin_registry.use_language("es")
        assert (
            "no encontrado" in plugin_registry.status().lines[0]
            and "no se encontró" in plugin_registry.claude_missing()
        )
    finally:
        plugin_registry.use_language(None)


EVENTS = [
    {
        "n": 1,
        "kind": "ask",
        "q": 1,
        "by": "drafter",
        "via": "drafter",
        "at": "2026-01-02T10:11:00",
        "text": "Fit?",
        "proposal": "H7",
    },
    {
        "n": 2,
        "kind": "reply",
        "q": 1,
        "by": "ingeniero",
        "via": "drafter",
        "at": "2026-01-02T10:12:00",
        "text": "Yes",
    },
]


def test_the_channel_follows_the_language() -> None:
    assert "PREGUNTA 1" in channel_tools._line(EVENTS[0], "es") and "QUESTION 1" in channel_tools._line(EVENTS[0], "en")
    assert "RESPUESTA" in channel_tools._line(EVENTS[1], "es") and "REPLY" in channel_tools._line(EVENTS[1], "en")
    assert "# Preguntas del dibujante" in channel_tools.render_markdown("p", EVENTS, "es")
    english = channel_tools.render_markdown("p", EVENTS, "en")
    assert "# Drafter's questions" in english and "[answered]" in english and "Reply from the ingeniero" in english


def test_the_channel_file_is_written_in_the_language_of_the_project(tmp_path: Path) -> None:
    design_memory.write_language(tmp_path, "en")
    channel_tools._append(
        tmp_path, "p", {"kind": "ask", "via": "drafter", "by": "drafter", "text": "Fit?"}, lambda e: None
    )
    assert "# Drafter's questions: p" in (tmp_path / "drawings" / "p-questions.md").read_text(encoding="utf-8")


def test_the_question_he_reads_word_for_word_is_in_his_language() -> None:
    assert "¿Revisas tú la hoja S1" in drawing_gate.review_question("S1", "es")
    assert "Do you review sheet S1" in drawing_gate.review_question("S1", "en")
    from cotaforge_mcp import memory_tools

    assert set(memory_tools.USER_QUESTIONS) == {"es", "en"}


# ---- no engineer-facing message with one language only -------------------------------------------------------------

SPANISH_WORDS = {"de", "del", "la", "las", "los", "el", "que", "con", "sin", "una", "para", "por", "se", "su", "sus"}
SAYING = {"say", "_say", "s", "pick", "compile"}
"""Calls whose arguments are the two wordings (or a pattern that matches both languages)."""
AGENT_TEXT = ("channel(action=", "drawing(action=", "Aprobó", "approve is the engineer's gate")
"""Texts for the agent that quote a Spanish word the engineer or a tool uses (an example call, a label): English."""

ENGINEER_FACING = {
    "mcp": ["cli.py", "launcher.py", "plugin_registry.py", "channel_tools.py", "drawing_gate.py"],
    "worker": [
        "items_*.py",
        "sheet_*.py",
        "drawing.py",
        "drawing_assembly.py",
    ],
}
"""The modules whose messages the engineer reads, not the agent: every Spanish wording in them has an English one."""


def _spanish(text: str) -> bool:
    text = text.replace("designer", "disenador")  # a role of the channel: a protocol word, not a message
    if re.search(r"[áéíóúñ¿¡]", text):
        return True
    return len(set(re.findall(r"[a-záéíóúñ]+", text.lower())) & SPANISH_WORDS) >= 2


def _in_pair(node: ast.AST, parents: dict[ast.AST, ast.AST]) -> bool:
    """A wording that sits beside its translation: a ``{"es": ..., "en": ...}`` table or an ``(es, en)`` tuple."""
    parent = parents.get(node)
    while parent is not None and isinstance(parent, ast.BinOp | ast.JoinedStr | ast.FormattedValue):
        node, parent = parent, parents.get(parent)
    ancestor = parent
    while ancestor is not None:  # a table of wordings per language: {"es": {...}, "en": {...}}
        if isinstance(ancestor, ast.Dict) and any(
            isinstance(k, ast.Constant) and k.value in ("es", "en") for k in ancestor.keys
        ):
            return True
        ancestor = parents.get(ancestor)
    if isinstance(parent, ast.Tuple | ast.List):
        tail = parent.elts[-2:]  # (es, en), or (id, pattern, es, en)
        stringy = (ast.Constant, ast.JoinedStr, ast.BinOp, ast.Call)
        return len(tail) == 2 and all(isinstance(e, stringy) for e in tail) and node in tail
    return False


def one_language_only(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    docstrings = {
        id(node.body[0].value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef)
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    found = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)) or id(node) in docstrings:
            continue
        if len(node.value.split()) < 2 or not _spanish(node.value) or _in_pair(node, parents):
            continue
        if any(quote in node.value for quote in AGENT_TEXT):
            continue
        call, cursor = None, parents.get(node)
        while cursor is not None:
            if isinstance(cursor, ast.Call):
                name = getattr(cursor.func, "attr", getattr(cursor.func, "id", ""))
                if name in SAYING:
                    call = cursor
                    break
            cursor = parents.get(cursor)
        if call is None:
            found.append(f"{path.name}:{node.lineno}: {node.value[:70]!r}")
    return found


def _modules() -> list[Path]:
    server = Path(__file__).parents[2] / "src" / "cotaforge_mcp"
    worker = Path(cotaforge.__file__).parent / "adapters" / "freecad" / "worker"
    return sorted(
        path
        for root, patterns in ((server, ENGINEER_FACING["mcp"]), (worker, ENGINEER_FACING["worker"]))
        for pattern in patterns
        for path in root.glob(pattern)
    )


def test_no_message_the_engineer_reads_has_only_one_language() -> None:
    """A Spanish wording must be an argument of ``say(es, en)`` (or sit in a pair); a new message in one language fails
    here. Agent-facing texts (tool descriptions, guides, errors for the agent) are English and are not looked at."""
    modules = _modules()
    assert len(modules) > 20  # the globs found the modules
    problems = [line for path in modules for line in one_language_only(path)]
    assert not problems, "Spanish-only messages:\n" + "\n".join(problems)
