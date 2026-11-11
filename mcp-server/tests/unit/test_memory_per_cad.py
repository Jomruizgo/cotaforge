"""An entry can belong to one CAD software (``cad``): two harnesses share one memory folder and each sees its own."""

from pathlib import Path
from typing import Any, ClassVar

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.memory import MemoryStore, Scope, parse_entry

from cotaforge_mcp import memory_tools
from cotaforge_mcp.config import Settings
from cotaforge_mcp.guide import server_instructions
from tests.conftest import Caller, MakeSession, declare


class AlphaFake(FakeAdapter):
    cad_key: ClassVar[str | None] = "alpha"


class BetaFake(FakeAdapter):
    cad_key: ClassVar[str | None] = "beta"


class KeylessFake(FakeAdapter):
    cad_key: ClassVar[str | None] = None


def practice(entry_id: str, **extra: Any) -> dict[str, Any]:
    return {
        "id": entry_id,
        "type": "practice",
        "title": f"Titulo de {entry_id}",
        "applies_when": "Siempre que se use",
        "not_when": "Nunca fuera de eso",
        **extra,
    }


def caller(make: MakeSession, adapter: FakeAdapter) -> Caller:
    return Caller(make(adapter))


def folder(settings: Settings) -> Path:
    return settings.workspace / "memory"


def test_an_entry_marked_for_one_software_loads_only_there(make: MakeSession, settings: Settings) -> None:
    declare(settings, practice("para-todos"), practice("solo-alpha", cad="alpha"), practice("solo-beta", cad="beta"))
    alpha = caller(make, AlphaFake())
    beta = caller(make, BetaFake())
    index_alpha = alpha.ok("memory", action="index")
    assert "para-todos" in index_alpha and "solo-alpha" in index_alpha and "solo-beta" not in index_alpha
    index_beta = beta.ok("memory", action="index")
    assert "para-todos" in index_beta and "solo-beta" in index_beta and "solo-alpha" not in index_beta
    # the conduct loaded at start is the same view
    assert "solo-alpha" in memory_tools.instructions_section(alpha.session.settings)
    assert "solo-beta" not in memory_tools.instructions_section(alpha.session.settings)
    assert "solo-beta" not in server_instructions(alpha.session)
    assert "solo-beta" in server_instructions(beta.session)
    assert "solo-beta" not in alpha.ok("memory", action="search", text="Titulo")
    assert "solo-beta" in beta.ok("memory", action="search", text="Titulo")


def test_get_says_an_entry_belongs_to_the_other_software(make: MakeSession, settings: Settings) -> None:
    declare(settings, practice("solo-beta", cad="beta", body="Usa la herramienta X."))
    alpha = caller(make, AlphaFake())
    text = alpha.ok("memory", action="get", id="solo-beta")
    assert "belongs to the CAD software" in text and "beta" in text and "not loaded" in text
    assert "herramienta X" not in text
    assert "solo-beta" not in alpha.session.mem.read  # reading it is not applying it
    assert "herramienta X" in caller(make, BetaFake()).ok("memory", action="get", id="solo-beta")


def test_the_engineer_can_list_what_is_hidden(make: MakeSession, settings: Settings) -> None:
    declare(settings, practice("para-todos"), practice("solo-beta", cad="beta"))
    alpha = caller(make, AlphaFake())
    assert "other CAD software" not in alpha.ok("memory", action="index")
    listed = alpha.ok("memory", action="index", text="all_cad")
    assert "other CAD software" in listed and "- solo-beta [beta]" in listed


def test_an_old_entry_without_the_field_holds_for_every_software_and_its_file_is_untouched(
    make: MakeSession, settings: Settings
) -> None:
    folder(settings).mkdir(parents=True)
    text = "---\nid: vieja\ntype: practice\ntitle: Una vieja\napplies_when: Siempre\nnot_when: Nunca\n---\nCuerpo.\n"
    path = folder(settings) / "vieja.md"
    path.write_text(text, encoding="utf-8")
    assert parse_entry(text).cad is None
    for adapter in (AlphaFake(), BetaFake(), KeylessFake()):
        assert "vieja" in caller(make, adapter).ok("memory", action="index")
    assert path.read_text(encoding="utf-8") == text


def test_a_software_with_no_key_still_works_and_sees_only_the_shared_entries(
    make: MakeSession, settings: Settings
) -> None:
    declare(settings, practice("para-todos"), practice("solo-alpha", cad="alpha"))
    keyless = caller(make, KeylessFake())
    index = keyless.ok("memory", action="index")
    assert "para-todos" in index and "solo-alpha" not in index
    refused = keyless.err("memory", action="save", entry=practice("nueva", cad="this"))
    assert "no memory key" in refused
    keyless.ok("memory", action="save", entry=practice("nueva"))
    assert "nueva" in keyless.ok("memory", action="index")


def test_saving_takes_the_key_from_the_adapter_not_from_the_agent(make: MakeSession, settings: Settings) -> None:
    alpha = caller(make, AlphaFake())
    saved = alpha.ok("memory", action="save", entry=practice("nota-alpha", cad="this"))
    assert "cad: alpha" in saved
    assert parse_entry((folder(settings) / "nota-alpha.md").read_text(encoding="utf-8")).cad == "alpha"
    alpha.ok("memory", action="save", entry=practice("otra-alpha", cad="alpha"), new=True)
    unknown = alpha.err("memory", action="save", entry=practice("de-beta", cad="beta"))
    assert "not the key of this software" in unknown
    invented = alpha.err("memory", action="save", entry=practice("inventada", cad="othercad-2030"))
    assert "not the key of this software" in invented
    assert not (folder(settings) / "de-beta.md").exists() and not (folder(settings) / "inventada.md").exists()
    beta = caller(make, BetaFake())
    assert "nota-alpha" not in beta.ok("memory", action="index")
    assert "nota-alpha" in alpha.ok("memory", action="index")


def test_an_entry_of_the_other_software_is_not_overwritten_by_a_save_with_its_id(
    make: MakeSession, settings: Settings
) -> None:
    declare(settings, practice("solo-beta", cad="beta"))
    refused = caller(make, AlphaFake()).err("memory", action="save", entry=practice("solo-beta"))
    assert "entry of the CAD software" in refused
    assert parse_entry((folder(settings) / "solo-beta.md").read_text(encoding="utf-8")).cad == "beta"


def test_two_harnesses_read_the_same_folder_through_the_store(settings: Settings) -> None:
    declare(settings, practice("para-todos"), practice("solo-alpha", cad="alpha"), practice("solo-beta", cad="beta"))
    project = folder(settings)

    def ids(cad: str | None, **kw: Any) -> list[str]:
        return [item.entry.id for item in MemoryStore(project, None, None, cad).entries(**kw)]

    assert ids("alpha") == ["para-todos", "solo-alpha"]
    assert ids("beta") == ["para-todos", "solo-beta"]
    assert ids(None) == ["para-todos"]
    assert ids(None, all_cad=True) == ["para-todos", "solo-alpha", "solo-beta"]
    assert MemoryStore(project, None, None, "alpha").get("solo-beta") is not None  # for `get` to say where it belongs
    assert MemoryStore(project, None, None, "alpha").scope_entries(Scope.project)[0].entry.id  # the file view is whole
