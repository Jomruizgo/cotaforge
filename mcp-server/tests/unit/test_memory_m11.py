"""Memory M1.1 through the tools (fake CAD): sectors in the brief and in ``get``, related, replaces, sources and the
fixes to the base set."""

from typing import Any

import yaml
from cotaforge.adapters.fake import FakeAdapter
from cotaforge.memory import base_memory_dir

from cotaforge_mcp import design_memory, memory_tools
from cotaforge_mcp.config import Settings
from cotaforge_mcp.server import make_session
from tests.conftest import PLATE_SKETCH, TOP, Caller, declare, plate

BASE_ID = "drawing-sufficient-views"


def principle(entry_id: str = "precision-del-ensamble", **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": entry_id,
        "type": "principle",
        "title": "No pedir una precisión que la pieza sola no garantiza",
        "applies_when": "Una pieza lleva un patrón con tolerancia fina",
        "not_when": "La pieza se mecaniza completa después de unirla",
        "recognize": "Un patrón de agujeros con tolerancia fina en voladizo",
        "typical_error": "Pedir la tolerancia a la pieza sola",
        "examples": [
            {"sector": "mobiliario", "text": "Un herraje.", "validated": False},
            {"sector": "fixtures de manufactura", "text": "La placa del dispositivo.", "validated": True},
            {"sector": "injection-molds", "text": "Una pieza plástica.", "validated": False},
            {"sector": "chapa", "text": "Un soporte plegado.", "validated": False},
        ],
        "when": {"ops": ["extrude"], "stages": ["detail"]},
    }
    data.update(overrides)
    return data


def practice(entry_id: str, sector: str, **overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": entry_id,
        "type": "practice",
        "title": f"Práctica de {sector}",
        "applies_when": "Al hacer la pieza en ese proceso",
        "not_when": "El taller usa otra regla",
        "sector": sector,
        "related": ["precision-del-ensamble"],
        "when": {"ops": ["extrude"], "stages": ["detail"]},
    }
    data.update(overrides)
    return data


def base_call(settings: Settings) -> Caller:
    on = Settings(adapter="fake", workspace=settings.workspace, home=settings.home, base_memory=True)
    return Caller(make_session(on, FakeAdapter()))


# ---- the brief: what he wrote, and the ids it resolves to ---------------------------------------------------------


def test_the_brief_keeps_what_the_engineer_wrote_and_stores_the_resolved_ids(call: Caller, settings: Settings) -> None:
    done = call.ok("memory", action="brief", sector="Dispositivos de manufactura", processes=["soldadura"])
    assert "sector Dispositivos de manufactura (= fixtures); processes soldadura" in done
    stored = yaml.safe_load((settings.workspace / "brief.yaml").read_text(encoding="utf-8"))
    assert stored == {
        "sector": "Dispositivos de manufactura",
        "sector_ids": ["fixtures"],
        "processes": ["soldadura"],
    }
    brief = design_memory.load_brief(settings)
    assert brief.sector == "Dispositivos de manufactura" and brief.sector_ids == ("fixtures",) and brief.declared
    several = call.ok("memory", action="brief", sector="chapa metálica y soldadura")
    assert "(= sheet-metal + welded-structures)" in several


def test_an_unknown_sector_is_kept_literal_and_says_how_to_add_it(call: Caller, settings: Settings) -> None:
    text = call.ok("memory", action="brief", sector="Mobiliario")
    assert "sector Mobiliario;" in text and "not in the sector table" in text and "sectors.yaml" in text
    assert "fixtures" in text  # the known ids are listed
    brief = design_memory.load_brief(settings)
    assert brief.sector == "Mobiliario" and brief.sector_ids == () and not brief.declared
    assert "sector_ids" not in (settings.workspace / "brief.yaml").read_text(encoding="utf-8")


def test_an_ambiguous_sector_is_not_assigned_and_the_engineer_is_asked(call: Caller) -> None:
    text = call.ok("memory", action="brief", sector="matrices")
    assert "ambiguous, nothing assigned ('matrices' is injection-molds or sheet-metal)" in text
    assert "an id works" in text
    assert "sector injection-molds" in call.ok("memory", action="brief", sector="injection-molds")


def test_the_engineer_extends_the_sectors_and_the_brief_follows(call: Caller, settings: Settings) -> None:
    (settings.workspace / "memory").mkdir(exist_ok=True)
    (settings.workspace / "memory" / "sectors.yaml").write_text(
        "sectors:\n  - {id: estampado, name: Estampado, aliases: [troqueles, prensas]}\n", encoding="utf-8"
    )
    assert "sector prensas (= estampado)" in call.ok("memory", action="brief", sector="prensas")
    assert design_memory.load_brief(settings).sector_ids == ("estampado",)
    assert "troqueles" in " ".join(design_memory.sector_table(settings).sector_words())


def test_a_sector_added_later_takes_effect_on_the_stored_brief(call: Caller, settings: Settings) -> None:
    call.ok("memory", action="brief", sector="vitrales")
    assert design_memory.load_brief(settings).sector_ids == ()
    (settings.workspace / "memory").mkdir(exist_ok=True)
    (settings.workspace / "memory" / "sectors.yaml").write_text(
        "sectors:\n  - {id: vidrio, name: Vidrio, aliases: [vitrales]}\n", encoding="utf-8"
    )
    assert design_memory.load_brief(settings).sector_ids == ("vidrio",)


# ---- get: examples by sector, related -------------------------------------------------------------------------------


def test_get_compares_sectors_by_id_so_an_alias_in_the_brief_matches_the_examples(
    call: Caller, settings: Settings
) -> None:
    declare(settings, principle())
    call.ok("memory", action="brief", sector="fixtures")  # the example says "fixtures de manufactura"
    got = call.ok("memory", action="get", id="precision-del-ensamble")
    assert "project sector: fixtures" in got
    assert got.index("[fixtures de manufactura] (validated)") < got.index("analogy of mobiliario: does it apply here?")
    assert got.count("analogy of") == 2 and "1 more example(s)" in got


def test_get_without_a_sector_marks_every_example_as_an_analogy_and_asks_to_declare_it(
    call: Caller, settings: Settings
) -> None:
    declare(settings, principle())
    got = call.ok("memory", action="get", id="precision-del-ensamble")
    assert "project sector not declared" in got and "memory(action=brief, sector=...)" in got
    assert got.count("analogy of") == 3 and "[fixtures de manufactura]" not in got  # not the project's own


def test_get_of_a_principle_lists_its_practices_with_the_project_sector_first(call: Caller, settings: Settings) -> None:
    declare(
        settings,
        principle(),
        practice("moldes-pared", "injection-molds"),
        practice("chapa-borde", "sheet-metal"),
        practice("sin-enlace", "injection-molds", related=[]),
    )
    call.ok("memory", action="brief", sector="chapa")
    got = call.ok("memory", action="get", id="precision-del-ensamble")
    lines = got.split("related (memory(action=get, id) reads each):")[1].strip().splitlines()
    assert [line.split()[1] for line in lines] == ["chapa-borde", "moldes-pared"]  # the unlinked one is not listed
    assert lines[0].startswith("- chapa-borde [practice, sheet-metal] Práctica de sheet-metal")
    # the practice finds its principle back, though only the practice carries the link
    assert "- precision-del-ensamble [principle]" in call.ok("memory", action="get", id="moldes-pared")


def test_a_related_id_that_does_not_exist_is_a_warning_in_the_index_and_in_the_save(
    call: Caller, settings: Settings
) -> None:
    saved = call.ok("memory", action="save", entry=practice("chapa-borde", "sheet-metal", related=["fantasma"]))
    assert "warning: related 'fantasma': no entry with that id" in saved
    index = call.ok("memory", action="index")
    assert "1 memory warning(s)" in index and "project/chapa-borde.md: related 'fantasma'" in index


# ---- practices of another sector ---------------------------------------------------------------------------------


def _extrude(call: Caller) -> str:
    call.ok("doc", action="new", name="plate")
    call.ok("op", name="sketch", params=PLATE_SKETCH)
    return call.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 8})


def _two_practices(settings: Settings) -> None:
    declare(
        settings,
        practice("moldes-pared", "injection-molds", related=[]),
        practice("chapa-borde", "sheet-metal", related=[]),
    )


def test_a_practice_of_another_declared_sector_is_neither_pushed_nor_required(call: Caller, settings: Settings) -> None:
    _two_practices(settings)
    call.ok("memory", action="brief", sector="Chapa metálica")
    text = _extrude(call)
    assert "memory: chapa-borde" in text and "[sector: sheet-metal]" in text
    assert "moldes-pared" not in text
    refused = call.err("memory", action="review", stage="detail")
    assert "- chapa-borde" in refused and "moldes-pared" not in refused


def test_with_no_declared_sector_every_practice_is_pushed(call: Caller, settings: Settings) -> None:
    _two_practices(settings)
    text = _extrude(call)
    assert "memory: chapa-borde" in text and "memory: moldes-pared" in text


# ---- replaces -----------------------------------------------------------------------------------------------------


def test_an_entry_that_replaces_a_base_one_takes_its_place_in_index_push_and_review(settings: Settings) -> None:
    call = base_call(settings)
    declare(
        settings,
        practice(
            "mis-vistas",
            "fixtures",
            related=[],
            replaces=BASE_ID,
            when={"ops": ["drawing_create"], "stages": ["detail"]},
        ),
    )
    index = call.ok("memory", action="index")
    assert f"{BASE_ID} [replaced by mis-vistas]" in index
    got = call.ok("memory", action="get", id=BASE_ID)
    assert "REPLACED by mis-vistas" in got and "neither pushed nor required" in got
    assert BASE_ID not in call.ok("memory", action="search", text="vistas")
    base_total = len(list(base_memory_dir().glob("*.md")))  # the replaced one no longer counts
    assert f"base: {base_total - 1} generic entries" in memory_tools.instructions_section(call.session.settings)
    late = call.err("memory", action="answer", id=BASE_ID, text="applies: la hoja trae las vistas necesarias")
    assert "unknown_entry" in late  # not a rule any more
    in_force = {e.id for e in design_memory.active_entries(call.session.settings)}
    assert BASE_ID not in in_force and "drawing-hidden-lines-or-section" in in_force  # the rest of the base still is


def test_saving_a_replacement_stays_proposed_and_the_base_entry_stays_in_force(settings: Settings) -> None:
    call = base_call(settings)
    saved = call.ok("memory", action="save", entry=practice("mis-vistas", "fixtures", related=[], replaces=BASE_ID))
    assert f"base entry {BASE_ID} stays in force until the engineer approves this replacement" in saved
    assert BASE_ID in {e.id for e in design_memory.active_entries(call.session.settings)}
    call.ok("memory", action="approve", id="mis-vistas", by="Ana", text="Ana: sí, reemplaza la de la base")
    assert BASE_ID not in {e.id for e in design_memory.active_entries(call.session.settings)}


def test_replaces_that_names_nothing_in_the_base_is_a_warning(settings: Settings) -> None:
    call = base_call(settings)
    lost = practice("mis-vistas", "fixtures", related=[], replaces="no-existe")
    saved = call.ok("memory", action="save", entry=lost)
    assert "warning: replaces 'no-existe': no base entry with that id" in saved


# ---- saving: ids are not measures, sources, sector words -----------------------------------------------------------


def test_a_principle_may_cite_a_practice_whose_id_carries_a_measure(call: Caller) -> None:
    body = "Ver la práctica peine-5mm y sheet-metal-hole-edge-and-spacing."
    saved = call.ok("memory", action="save", entry=principle(body=body, related=["peine-5mm"]))
    assert "status: proposed" in saved and "warning: related 'peine-5mm'" in saved
    refused = call.err("memory", action="save", entry=principle("otro", body="Usa 5 mm de peine, ver peine-5mm."))
    assert "split_entry" in refused and "'5 mm'" in refused and refused.count("has a measured number") == 1


def test_a_principle_with_a_sector_word_of_the_table_is_refused_even_when_no_brief_was_declared(call: Caller) -> None:
    bad = principle("mala", title="En la chapa la tolerancia es la general")
    refused = call.err("memory", action="save", entry=bad)
    assert "split_entry" in refused and "names the sector/process 'chapa'" in refused


def test_a_practice_with_a_measure_and_no_source_saves_with_a_warning(call: Caller) -> None:
    bare = practice(
        "holgura",
        "injection-molds",
        title="Holgura de pared",
        related=[],
        body="La pared mide 2,5 mm y la holgura 0,3 mm.",
    )
    saved = call.ok("memory", action="save", entry=bare)
    assert "status: proposed" in saved and "warning: the practice has measures (2,5 mm, 0,3 mm)" in saved
    cited = practice(
        "holgura2",
        "injection-molds",
        title="Espesor de costilla",
        related=[],
        body="La pared mide 2,5 mm.",
        sources=["Hubs, guía de moldeo"],
    )
    assert "warning" not in call.ok("memory", action="save", entry=cited)
    book = {"source": "reference_book", "reference": "Shigley, tabla 7-1", "by": "Ana", "date": "2026-10-29"}
    booked = practice(
        "holgura3",
        "injection-molds",
        title="Ajuste del noyo",
        related=[],
        body="La pared mide 2,5 mm.",
        provenance=book,
    )
    assert "warning" not in call.ok("memory", action="save", entry=booked)


def test_sources_on_a_declaration_are_refused(call: Caller) -> None:
    from tests.conftest import material_entry

    refused = call.err("memory", action="save", entry={**material_entry(), "sources": ["un libro"]})
    assert "only for principles and practices" in refused


# ---- the base set, through the tools -------------------------------------------------------------------------------


def test_the_base_index_is_clean_and_has_no_warning(settings: Settings) -> None:
    index = base_call(settings).ok("memory", action="index")
    assert "memory warning" not in index and "could not be loaded" not in index
    for dropped in ("dibujo-cotas-fuera-de-la-pieza", "diseno-extremos-de-perfil-abiertos"):
        assert dropped not in index


def test_a_base_principle_lists_the_practice_with_the_numbers(settings: Settings) -> None:
    call = base_call(settings)
    call.ok("memory", action="brief", sector="chapa")
    got = call.ok("memory", action="get", id="design-support-and-edge-distance")
    assert "related (memory(action=get, id) reads each):" in got
    assert "- sheet-metal-hole-edge-and-spacing [practice, sheet-metal]" in got
    practice_text = call.ok("memory", action="get", id="sheet-metal-hole-edge-and-spacing")
    assert "Hubs" in practice_text and "sources" in practice_text and "una vez y media" not in practice_text
    assert "- design-support-and-edge-distance [principle]" in practice_text


def test_a_hole_calls_for_the_precision_principle_without_the_welded_role(settings: Settings) -> None:
    call = base_call(settings)
    plate(call)
    assert "design-precision-the-assembly-guarantees" not in call.err("memory", action="review", stage="layout")
    hole = {"face": TOP, "position": [20, 20], "standard": "simple", "diameter_mm": 5, "end_condition": "through_all"}
    drilled = call.ok("op", name="hole", params=hole)
    assert "memory: design-precision-the-assembly-guarantees" in drilled  # no role: the op alone calls for it
    assert "design-precision-the-assembly-guarantees" in call.err("memory", action="review", stage="detail")


# ---- the index by sector --------------------------------------------------------------------------------------------


def _base_index(settings: Settings, **brief: str) -> str:
    call = base_call(settings)
    if brief:
        call.ok("memory", action="brief", **brief)
    return call.ok("memory", action="index")


def test_with_no_sector_the_base_practices_collapse_to_one_line_per_sector(settings: Settings) -> None:
    index = _base_index(settings)
    assert "- sheet-metal: 6 practices (memory index sector=sheet-metal)" in index
    assert "- fixtures: 8 practices (memory index sector=fixtures)" in index
    assert "- special-machines: 1 práctica (memory index sector=special-machines)" in index
    assert "sheet-metal-min-flange-length" not in index and "fixtures-3-2-1-and-4-2-1-location" not in index
    assert "design-one-support-per-degree-of-freedom" in index and "drawing-sufficient-views" in index  # principles
    assert "sector not declared" in index
    assert len(index) < 3000


def test_the_declared_sector_lists_its_practices_in_full_and_the_others_collapse(settings: Settings) -> None:
    index = _base_index(settings, sector="Dispositivos de manufactura")
    assert "fixtures-3-2-1-and-4-2-1-location" in index and "- fixtures: " not in index
    assert "- sheet-metal: 6 practices" in index and "sheet-metal-min-flange-length" not in index
    assert "sector not declared" not in index
    chapa = _base_index(settings, sector="chapa")
    assert "sheet-metal-min-flange-length" in chapa and "fixtures-3-2-1-and-4-2-1-location" not in chapa
    assert len(index) < 3000 and len(chapa) < 3000


def test_index_sector_lists_one_more_sector_and_search_still_finds_everything(settings: Settings) -> None:
    call = base_call(settings)
    call.ok("memory", action="brief", sector="fixtures")
    asked = call.ok("memory", action="index", sector="chapa metálica")
    assert "sheet-metal-min-flange-length" in asked and "fixtures-3-2-1-and-4-2-1-location" in asked
    assert "molds-ribs" not in asked and "- injection-molds: 5 practices" in asked
    assert "is not in the sector table" in call.ok("memory", action="index", sector="astronomía")
    assert "sheet-metal-min-flange-length" in call.ok(
        "memory", action="search", text="flange"
    )  # search ignores the collapse
    assert call.ok("memory", action="get", id="molds-ribs")  # and so does get
    after = call.ok("memory", action="index")  # asking does not change the brief
    assert "sheet-metal-min-flange-length" not in after and "fixtures-3-2-1-and-4-2-1-location" in after


def test_every_entry_of_the_engineer_keeps_its_line_whatever_its_sector(settings: Settings) -> None:
    sectors = ("injection-molds", "sheet-metal", "fixtures")
    mine = [practice(f"mi-practica-sobre-un-asunto-concreto-{i:02d}", sectors[i % 3], related=[]) for i in range(30)]
    mine += [principle(f"mi-principio-sobre-otro-asunto-concreto-{i:02d}") for i in range(10)]
    declare(settings, *mine, scope="user")
    index = _base_index(settings, sector="fixtures")
    assert all(entry["id"] in index for entry in mine)  # 40 entries, each with its own line
    assert "sheet-metal-min-flange-length" not in index and "- sheet-metal: 6 practices" in index
    assert len(index) < 6000  # ids of ~45 characters: 40 of them cost about 2k on top of the base set
