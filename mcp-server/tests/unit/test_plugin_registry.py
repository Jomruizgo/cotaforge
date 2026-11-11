"""``cotaforge plugin register | unregister | status`` against a fake ``claude`` (a runner that keeps its own state like
the real ``claude plugin`` commands do). The real Claude Code configuration is never touched."""

import json
import subprocess
from collections.abc import Sequence
from pathlib import Path

import pytest

from cotaforge_mcp import layout, plugin_registry
from cotaforge_mcp.plugin_registry import PluginError

CLAUDE = Path("claude")


class FakeClaude:
    """The parts of ``claude plugin ...`` that cotaforge uses, with the output shapes of Claude Code 2.1."""

    def __init__(self) -> None:
        self.marketplaces: dict[str, str] = {}
        self.plugins: set[str] = set()
        self.calls: list[list[str]] = []
        self.fail: tuple[str, ...] | None = None  # an argument sequence whose command fails
        self.stdout_override: str | None = None

    def __call__(self, argv: Sequence[str]) -> "subprocess.CompletedProcess[str]":
        args = list(argv)[1:]
        self.calls.append(args)
        assert args[0] == "plugin"
        sub = args[1:]
        if self.fail and tuple(sub[: len(self.fail)]) == self.fail:
            return subprocess.CompletedProcess(argv, 1, "", "✘ Failed\nsomething broke\n")
        out = ""
        if sub[:2] == ["marketplace", "list"]:
            out = json.dumps(
                [
                    {"name": n, "source": "directory", "path": p, "installLocation": p}
                    for n, p in self.marketplaces.items()
                ]
            )
        elif sub[:1] == ["list"]:
            out = json.dumps([{"id": plugin, "scope": "user", "enabled": True} for plugin in sorted(self.plugins)])
        elif sub[:2] == ["marketplace", "add"]:
            self.marketplaces["cotaforge"] = sub[2]
        elif sub[:1] == ["install"]:
            self.plugins.add(sub[1])
        elif sub[:1] == ["uninstall"]:
            self.plugins.discard(sub[1])
        elif sub[:2] == ["marketplace", "remove"]:
            self.marketplaces.pop(sub[2], None)
        if self.stdout_override is not None:
            out = self.stdout_override
        return subprocess.CompletedProcess(argv, 0, out, "")

    def mutations(self) -> list[list[str]]:
        return [c for c in self.calls if c[1:3] != ["marketplace", "list"] and c[1:2] != ["list"]]


@pytest.fixture
def marketplace(tmp_path: Path) -> Path:
    folder = tmp_path / "my app" / "marketplace"  # a space on purpose
    (folder / ".claude-plugin").mkdir(parents=True)
    (folder / ".claude-plugin" / "marketplace.json").write_text("{}", encoding="utf-8")
    (folder / "plugin" / ".claude-plugin").mkdir(parents=True)
    (folder / "plugin" / ".claude-plugin" / "plugin.json").write_text("{}", encoding="utf-8")
    return folder


# --- finding claude -------------------------------------------------------------------------------------------------


def test_claude_is_found_on_the_path_first(tmp_path: Path) -> None:
    assert plugin_registry.find_claude(which=lambda name: "/bin/claude", home=tmp_path, info={}) == Path("/bin/claude")


def test_claude_is_found_in_the_native_installer_folder(tmp_path: Path) -> None:
    binary = tmp_path / ".local" / "bin" / "claude.exe"
    binary.parent.mkdir(parents=True)
    binary.write_bytes(b"")
    assert plugin_registry.find_claude(which=lambda name: None, home=tmp_path, info={}) == binary


def test_claude_is_found_where_the_installer_recorded_it(tmp_path: Path) -> None:
    binary = tmp_path / "elsewhere" / "claude.exe"
    binary.parent.mkdir()
    binary.write_bytes(b"")
    found = plugin_registry.find_claude(which=lambda name: None, home=tmp_path / "home", info={"claude": str(binary)})
    assert found == binary


def test_claude_missing_is_none(tmp_path: Path) -> None:
    assert plugin_registry.find_claude(which=lambda name: None, home=tmp_path, info={"claude": "/no/such"}) is None


# --- register -------------------------------------------------------------------------------------------------------


def test_register_adds_the_marketplace_and_installs_the_plugin_for_the_user(marketplace: Path) -> None:
    fake = FakeClaude()
    outcome = plugin_registry.register(fake, claude=CLAUDE, marketplace=marketplace)
    assert outcome.ok and outcome.changed
    assert fake.mutations() == [
        ["plugin", "marketplace", "add", str(marketplace)],
        ["plugin", "install", "cotaforge@cotaforge", "--scope", "user"],
    ]
    assert plugin_registry.is_registered(fake, claude=CLAUDE, marketplace=marketplace)


def test_register_twice_says_so_and_changes_nothing(marketplace: Path) -> None:
    fake = FakeClaude()
    plugin_registry.register(fake, claude=CLAUDE, marketplace=marketplace)
    before = len(fake.mutations())
    outcome = plugin_registry.register(fake, claude=CLAUDE, marketplace=marketplace)
    assert outcome.ok and not outcome.changed
    assert "ya estaba registrado" in outcome.lines[0]
    assert len(fake.mutations()) == before


def test_register_points_the_marketplace_here_when_it_pointed_elsewhere(marketplace: Path) -> None:
    fake = FakeClaude()
    fake.marketplaces["cotaforge"] = "/old/clone"
    fake.plugins.add("cotaforge@cotaforge")
    outcome = plugin_registry.register(fake, claude=CLAUDE, marketplace=marketplace)
    assert outcome.ok and outcome.changed
    assert "/old/clone" in outcome.lines[0]
    assert fake.marketplaces["cotaforge"] == str(marketplace)


def test_register_without_claude_is_a_clear_spanish_error_not_a_crash(
    marketplace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plugin_registry, "find_claude", lambda **kwargs: None)
    outcome = plugin_registry.register(FakeClaude(), marketplace=marketplace)
    assert not outcome.ok
    assert outcome.lines[0].startswith("error: no se encontró Claude Code")


def test_register_without_a_marketplace_folder_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(layout, "find_marketplace_dir", lambda: None)
    outcome = plugin_registry.register(FakeClaude(), claude=CLAUDE)
    assert not outcome.ok and "carpeta del plugin" in outcome.lines[0]


def test_a_failing_claude_command_is_reported_and_stops(marketplace: Path) -> None:
    fake = FakeClaude()
    fake.fail = ("install",)
    outcome = plugin_registry.register(fake, claude=CLAUDE, marketplace=marketplace)
    assert not outcome.ok
    assert "something broke" in outcome.lines[0]


def test_a_claude_that_cannot_run_is_reported(marketplace: Path) -> None:
    def broken(argv: Sequence[str]) -> "subprocess.CompletedProcess[str]":
        raise PluginError("no se pudo ejecutar claude: boom")

    outcome = plugin_registry.register(broken, claude=CLAUDE, marketplace=marketplace)
    assert not outcome.ok and "boom" in outcome.lines[0]
    assert plugin_registry.is_registered(broken, claude=CLAUDE, marketplace=marketplace) is False


def test_output_that_is_not_json_is_an_error_and_never_counts_as_registered(marketplace: Path) -> None:
    fake = FakeClaude()
    fake.stdout_override = "this is not json"
    assert not plugin_registry.register(fake, claude=CLAUDE, marketplace=marketplace).ok
    assert plugin_registry.is_registered(fake, claude=CLAUDE, marketplace=marketplace) is False


# --- unregister -----------------------------------------------------------------------------------------------------


def test_unregister_removes_the_plugin_and_the_marketplace(marketplace: Path) -> None:
    fake = FakeClaude()
    plugin_registry.register(fake, claude=CLAUDE, marketplace=marketplace)
    outcome = plugin_registry.unregister(fake, claude=CLAUDE, marketplace=marketplace)
    assert outcome.ok and outcome.changed
    assert fake.marketplaces == {} and fake.plugins == set()
    assert fake.mutations()[-2:] == [
        ["plugin", "uninstall", "cotaforge@cotaforge", "--scope", "user"],
        ["plugin", "marketplace", "remove", "cotaforge"],
    ]


def test_unregister_when_nothing_is_registered_is_not_an_error(marketplace: Path) -> None:
    fake = FakeClaude()
    outcome = plugin_registry.unregister(fake, claude=CLAUDE, marketplace=marketplace)
    assert outcome.ok and not outcome.changed
    assert fake.mutations() == []


def test_unregister_leaves_a_marketplace_that_lives_elsewhere(marketplace: Path, tmp_path: Path) -> None:
    fake = FakeClaude()
    clone = tmp_path / "clone"
    clone.mkdir()
    fake.marketplaces["cotaforge"] = str(clone)
    fake.plugins.add("cotaforge@cotaforge")
    outcome = plugin_registry.unregister(fake, claude=CLAUDE, marketplace=marketplace)
    assert outcome.ok and not outcome.changed
    assert fake.plugins == {"cotaforge@cotaforge"} and fake.marketplaces == {"cotaforge": str(clone)}


def test_unregister_cleans_a_marketplace_whose_folder_is_gone(marketplace: Path, tmp_path: Path) -> None:
    fake = FakeClaude()
    fake.marketplaces["cotaforge"] = str(tmp_path / "deleted")
    fake.plugins.add("cotaforge@cotaforge")
    assert plugin_registry.unregister(fake, claude=CLAUDE, marketplace=marketplace).changed
    assert fake.marketplaces == {} and fake.plugins == set()


def test_unregister_without_claude_fails_with_a_message(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plugin_registry, "find_claude", lambda **kwargs: None)
    assert not plugin_registry.unregister(FakeClaude()).ok


# --- status and the command -----------------------------------------------------------------------------------------


def test_status_tells_registered_from_not(marketplace: Path) -> None:
    fake = FakeClaude()
    assert not plugin_registry.status(fake, claude=CLAUDE, marketplace=marketplace).ok
    plugin_registry.register(fake, claude=CLAUDE, marketplace=marketplace)
    outcome = plugin_registry.status(fake, claude=CLAUDE, marketplace=marketplace)
    assert outcome.ok and outcome.lines[-1] == "estado: registrado"


def test_status_without_claude_names_what_is_missing(monkeypatch: pytest.MonkeyPatch, marketplace: Path) -> None:
    monkeypatch.setattr(plugin_registry, "find_claude", lambda **kwargs: None)
    outcome = plugin_registry.status(FakeClaude(), marketplace=marketplace)
    assert not outcome.ok and "claude:       no encontrado" in outcome.lines


def test_run_command_prints_and_returns_the_exit_status(
    marketplace: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(plugin_registry, "find_claude", lambda **kwargs: CLAUDE)
    monkeypatch.setattr(layout, "find_marketplace_dir", lambda: marketplace)
    fake = FakeClaude()
    assert plugin_registry.run_command("status", fake) == 1  # not registered yet
    assert "NO registrado" in capsys.readouterr().out
    assert plugin_registry.run_command("register", fake) == 0
    assert "registrado" in capsys.readouterr().out
    assert plugin_registry.run_command("status", fake) == 0
    capsys.readouterr()
    fake.fail = ("marketplace", "list")
    assert plugin_registry.run_command("status", fake) == 1
    assert "error:" in capsys.readouterr().err


OTHER = layout.Plugin("cotaforge-x", "cotaforge-x", "cotaforge-x.ico")


def test_the_plugin_of_another_product_is_registered_and_removed_under_its_own_id(marketplace: Path) -> None:
    fake = FakeClaude()
    fake.marketplaces["cotaforge-x"] = str(marketplace)
    fake.plugins.add("cotaforge-x@cotaforge-x")
    assert plugin_registry.is_registered(fake, claude=CLAUDE, marketplace=marketplace, plugin=OTHER)
    assert not plugin_registry.is_registered(fake, claude=CLAUDE, marketplace=marketplace)  # cotaforge's own is not
    status = plugin_registry.status(fake, claude=CLAUDE, marketplace=marketplace, plugin=OTHER, command="cotaforge-x")
    assert status.ok and "plugin cotaforge-x@cotaforge-x: instalado" in status.lines
    outcome = plugin_registry.unregister(fake, claude=CLAUDE, marketplace=marketplace, plugin=OTHER)
    assert outcome.ok and "cotaforge-x@cotaforge-x" in outcome.lines[0]
    assert ["plugin", "uninstall", "cotaforge-x@cotaforge-x", "--scope", "user"] in fake.calls
    assert ["plugin", "marketplace", "remove", "cotaforge-x"] in fake.calls


def test_status_names_the_command_that_registers(marketplace: Path) -> None:
    fake = FakeClaude()
    assert (
        "usa: cotaforge plugin register"
        in plugin_registry.status(fake, claude=CLAUDE, marketplace=marketplace).lines[-1]
    )
    other = plugin_registry.status(fake, claude=CLAUDE, marketplace=marketplace, plugin=OTHER, command="cotaforge-x")
    assert "usa: cotaforge-x plugin register" in other.lines[-1]


def test_the_icon_is_the_one_asked_for(tmp_path: Path) -> None:
    (tmp_path / "cotaforge.ico").write_bytes(b"")
    (tmp_path / "cotaforge-x.ico").write_bytes(b"")
    assert layout.icon_path(tmp_path) == tmp_path / "cotaforge.ico"
    assert layout.icon_path(tmp_path, "cotaforge-x.ico") == tmp_path / "cotaforge-x.ico"
    assert layout.icon_path(tmp_path, "missing.ico") is None
    assert layout.default_plugin().id == "cotaforge@cotaforge"
