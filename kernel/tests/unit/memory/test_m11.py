"""Memory M1.1: canonical sectors, presentation by sector, ids are not measures, related, replaces and sources."""

from pathlib import Path
from typing import Any

import pytest

from cotaforge.memory import (
    MemoryStore,
    Scope,
    base_memory_dir,
    base_sector_table,
    combined_index,
    load_sector_table,
    ordered_examples,
    practice_warnings,
    principle_findings,
    push_line,
    related_lines,
    relevant_to_sector,
    render_entry,
    render_full,
    without_entry_ids,
)
from tests.unit.memory.builders import make_entry

TABLE = base_sector_table()


def example(sector: str, text: str = "Un caso.") -> dict[str, Any]:
    return {"sector": sector, "text": text, "validated": False}


def principle(*sectors: str, **overrides: Any) -> Any:
    fields: dict[str, Any] = {
        "type": "principle",
        "recognize": "Una situación visible en el modelo",
        "examples": [example(s, f"Caso de {s}.") for s in sectors],
    }
    fields.update(overrides)
    return make_entry("regla-general", **fields)


# ---- normalisation ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "ids"),
    [
        ("fixtures", ("fixtures",)),  # an id
        ("Dispositivos de Manufactura", ("fixtures",)),  # an alias, case
        ("DISPOSITIVOS DE FIJACIÓN", ("fixtures",)),  # accents
        ("mecatrónica", ("special-machines",)),
        ("maquinaria de proceso", ("process-and-transport-equipment",)),
        ("transporte", ("process-and-transport-equipment",)),
        ("Chapa metálica", ("sheet-metal",)),
        ("sheet-metal", ("sheet-metal",)),  # the id itself
        ("Equipo higiénico (alimentos y farmacia)", ("hygienic-equipment",)),  # the name
        ("estructuras soldadas", ("welded-structures",)),
        ("Utillaje", ("fixtures",)),
    ],
)
def test_a_sector_resolves_by_id_name_or_alias_without_case_or_accents(text: str, ids: tuple[str, ...]) -> None:
    assert TABLE.resolve(text).ids == ids


def test_a_text_with_several_sectors_gives_a_list_in_order_of_appearance() -> None:
    assert TABLE.resolve("chapa metálica y soldadura").ids == ("sheet-metal", "welded-structures")
    mixed = TABLE.resolve("Mecanizado, fixtures y injection-molds")
    assert mixed.ids == ("precision-machining", "fixtures", "injection-molds")
    assert TABLE.resolve("chapa y chapa metálica").ids == ("sheet-metal",)  # no repeats
    assert TABLE.resolve("ejes y rodamientos").ids == ("power-transmission",)  # the longest alias, not two words


def test_an_ambiguous_word_is_never_assigned() -> None:
    alone = TABLE.resolve("matrices")
    assert alone.ids == () and alone.ambiguous == (("matrices", ("injection-molds", "sheet-metal")),)
    mixed = TABLE.resolve("chapa metálica y troqueles")
    assert mixed.ids == ("sheet-metal",) and mixed.ambiguous[0][0] == "troqueles"
    assert not alone.declared and alone.keys() == frozenset({"~matrices"})  # it stays literal


def test_an_unknown_sector_stays_literal_and_general_is_no_sector() -> None:
    unknown = TABLE.resolve("Mobiliario")
    assert unknown.ids == () and unknown.ambiguous == () and not unknown.resolved
    assert unknown.keys() == frozenset({"~mobiliario"})
    assert TABLE.resolve("").keys() == frozenset() and TABLE.resolve(None).ids == ()
    general = TABLE.resolve("general")
    assert general.ids == ("general",) and not general.declared
    assert TABLE.resolve("ninguno").ids == ("general",)
    # the word 'general' inside a longer text is not the sector 'general'
    assert TABLE.resolve("tolerancia general de chapa").ids == ("sheet-metal",)


def test_the_canonical_check_accepts_only_ids() -> None:
    assert TABLE.is_canonical("sheet-metal") and TABLE.is_canonical("general")
    assert not TABLE.is_canonical("chapa metálica") and not TABLE.is_canonical("mecatrónica")
    assert len(TABLE.ids) == 10 and "hygienic-equipment" in TABLE.ids


def test_sector_words_are_names_of_sectors_and_processes_but_not_general() -> None:
    words = TABLE.sector_words()
    assert {"chapa", "soldadura", "fixtures", "chapa metalica"} <= set(words) and "general" not in words


# ---- the engineer extends the table ----------------------------------------------------------------------------------


def test_the_engineer_adds_a_sector_and_aliases_to_an_existing_one(tmp_path: Path) -> None:
    folder = tmp_path / "memory"
    folder.mkdir()
    (folder / "sectors.yaml").write_text(
        "sectors:\n"
        "  - id: estampado\n    name: Estampado\n    aliases: [troqueles, estampacion]\n"
        "  - id: fixtures\n    aliases: [mis plantillas de soldar]\n",
        encoding="utf-8",
    )
    table, problems = load_sector_table(None, [("project", folder)])
    assert problems == []
    assert table.resolve("Estampación").ids == ("estampado",)
    assert table.resolve("troqueles").ids == ("estampado",)  # no longer ambiguous: he assigned it
    assert table.resolve("mis plantillas de soldar").ids == ("fixtures",)
    assert table.resolve("utillaje").ids == ("fixtures",)  # the base aliases remain
    assert TABLE.resolve("estampacion").ids == ()  # the shipped table is untouched


def test_a_broken_sectors_file_is_reported_and_never_breaks_the_table(tmp_path: Path) -> None:
    folder = tmp_path / "user"
    folder.mkdir()
    text = "sectors:\n  - id: Mal Id\n  - name: sin id\n  - id: nuevo\n"
    (folder / "sectors.yaml").write_text(text, encoding="utf-8")
    table, problems = load_sector_table(None, [("user", folder)])
    assert len(problems) == 3 and all(p.startswith("user sectors.yaml:") for p in problems)
    assert table.resolve("chapa").ids == ("sheet-metal",)
    (folder / "sectors.yaml").write_text("sectors: [", encoding="utf-8")
    assert load_sector_table(None, [("user", folder)])[1][0].startswith("user sectors.yaml:")
    (folder / "sectors.yaml").write_text("", encoding="utf-8")
    assert load_sector_table(None, [("user", folder)])[1] == []


def test_the_store_reads_the_project_table_after_the_user_one(tmp_path: Path) -> None:
    project, user = tmp_path / "proyecto", tmp_path / "usuario"
    for folder, text in (
        (user, "sectors:\n  - {id: vidrio, name: Vidrio, aliases: [cristal]}\n"),
        (project, "sectors:\n  - {id: vidrio, aliases: [vitral]}\n"),
    ):
        folder.mkdir()
        (folder / "sectors.yaml").write_text(text, encoding="utf-8")
    table = MemoryStore(project_dir=project, user_dir=user, base_dir=base_memory_dir()).sector_table()
    assert table.resolve("cristal").ids == ("vidrio",) and table.resolve("vitral").ids == ("vidrio",)
    assert table.name("vidrio") == "Vidrio"


def test_the_base_set_must_name_canonical_sectors(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base.mkdir()
    good = make_entry("base-buena", type="principle", recognize="x", examples=[example("fixtures")])
    bad = make_entry("base-mala", type="principle", recognize="x", examples=[example("fixtures de manufactura")])
    odd = make_entry("base-rara", sector="chapa metálica")
    for entry in (good, bad, odd):
        (base / f"{entry.id}.md").write_text(render_entry(entry), encoding="utf-8")
    store = MemoryStore(base_dir=base)
    assert [i.entry.id for i in store.scope_entries(Scope.base)] == ["base-buena"]
    messages = {p.file.stem: p.message for p in store.problems()}
    assert set(messages) == {"base-mala", "base-rara"} and "not canonical ids" in messages["base-mala"]


# ---- presentation by sector ------------------------------------------------------------------------------------------


def test_a_known_sector_matches_through_aliases_the_literal_text_would_miss() -> None:
    entry = principle("fixtures", "sheet-metal", "injection-molds", "hygienic-equipment")
    shown = ordered_examples(entry, "fixtures de manufactura", TABLE)
    assert [(e.sector, analogy) for e, analogy in shown] == [
        ("fixtures", False),
        ("sheet-metal", True),
        ("injection-molds", True),
    ]
    text = render_full(entry, "Dispositivos de manufactura", TABLE)
    assert "project sector: Dispositivos de manufactura = fixtures" in text
    assert text.index("[fixtures]") < text.index("analogy of sheet-metal: does it apply here?")
    assert "1 more example(s) of other sectors not shown" in text


def test_the_examples_of_a_user_entry_with_a_non_canonical_sector_still_match() -> None:
    entry = principle("fixtures de manufactura", "mecatrónica", "transporte")
    shown = ordered_examples(entry, "fixtures", TABLE)
    assert [(e.sector, analogy) for e, analogy in shown] == [
        ("fixtures de manufactura", False),
        ("mecatrónica", True),
        ("transporte", True),
    ]


def test_a_project_with_several_sectors_orders_by_the_first_then_the_rest_before_analogies() -> None:
    entry = principle("injection-molds", "welded-structures", "fixtures", "sheet-metal")
    shown = ordered_examples(entry, "chapa metálica y soldadura", TABLE)
    assert [(e.sector, analogy) for e, analogy in shown] == [
        ("sheet-metal", False),
        ("welded-structures", False),
        ("injection-molds", True),
        ("fixtures", True),
    ]


def test_analogies_prefer_distinct_sectors() -> None:
    entry = principle("injection-molds", "injection-molds", "fixtures", "sheet-metal")
    shown = ordered_examples(entry, "estructuras soldadas", TABLE)
    assert [e.sector for e, _ in shown] == ["injection-molds", "fixtures"]  # one per sector, not injection-molds twice


def test_an_undeclared_sector_shows_up_to_three_sectors_as_analogies_and_asks_to_declare_it() -> None:
    entry = principle("fixtures", "injection-molds", "injection-molds", "sheet-metal", "hygienic-equipment")
    for sector in (None, "", "general"):
        shown = ordered_examples(entry, sector, TABLE)
        assert [e.sector for e, _ in shown] == ["fixtures", "injection-molds", "sheet-metal"]
        assert all(analogy for _, analogy in shown)
    text = render_full(entry, None, TABLE)
    assert "project sector not declared" in text and "memory(action=brief, sector=...)" in text
    assert text.count("analogy of") == 3 and "2 more example(s)" in text


def test_an_unknown_sector_is_not_covered_and_says_so_but_its_own_literal_still_matches() -> None:
    entry = principle("fixtures", "mobiliario", "injection-molds")
    shown = ordered_examples(entry, "Mobiliario", TABLE)
    assert [(e.sector, analogy) for e, analogy in shown] == [
        ("mobiliario", False),
        ("fixtures", True),
        ("injection-molds", True),
    ]
    text = render_full(entry, "Mobiliario", TABLE)
    assert "'Mobiliario' is not in the sector table" in text and "sectors.yaml" in text
    nothing = render_full(principle("fixtures", "injection-molds"), "Mobiliario", TABLE)
    assert "analogies" in nothing and nothing.count("analogy of") == 2


def test_a_practice_of_another_declared_sector_is_not_relevant_but_everything_else_is() -> None:
    sheet = make_entry("chapa-x", sector="sheet-metal")
    assert relevant_to_sector(sheet, "chapa metálica", TABLE)
    assert not relevant_to_sector(sheet, "fixtures", TABLE)
    assert relevant_to_sector(sheet, "chapa y soldadura", TABLE)
    for undeclared in (None, "", "general", "mobiliario", "matrices"):
        assert relevant_to_sector(sheet, undeclared, TABLE)  # nothing can be judged
    assert relevant_to_sector(principle("injection-molds"), "fixtures", TABLE)  # a principle always is
    assert relevant_to_sector(make_entry("sin-sector"), "fixtures", TABLE)
    assert push_line(sheet).endswith("[sector: sheet-metal]")


# ---- ids are not measures --------------------------------------------------------------------------------------------


def test_an_entry_id_is_a_name_not_a_measure_a_code_or_a_sector_word() -> None:
    body = "Ver la práctica peine-5mm y chapa-agujero-a-borde, o la variante soldadura-mig-2x4mm."
    entry = principle("fixtures", body=body, related=["peine-5mm"])
    assert principle_findings(entry, ["chapa", "soldadura"]) == []
    assert without_entry_ids("el id peine-5mm y 5 mm", ["x"]).strip() == "el id   y 5 mm"
    real = principle("fixtures", body="Usa 5 mm de peine y la práctica peine-5mm.")
    assert [f for f in principle_findings(real, []) if "measured number" in f] == ["body has a measured number '5 mm'"]


def test_an_id_without_a_hyphen_is_ignored_only_when_the_store_knows_it() -> None:
    entry = principle("fixtures", body="Ver la entrada 5mm y 12 mm sueltos.")  # `5mm` is a valid one-word id
    assert [f for f in principle_findings(entry, []) if "measured" in f] == [
        "body has a measured number '5mm'",
        "body has a measured number '12 mm'",
    ]
    assert [f for f in principle_findings(entry, [], ["5mm"]) if "measured" in f] == [
        "body has a measured number '12 mm'"
    ]


def test_a_number_range_is_not_an_id() -> None:
    entry = principle("fixtures", body="Entre 10-20 mm de holgura.")
    assert any("measured number" in f for f in principle_findings(entry, []))


# ---- related -------------------------------------------------------------------------------------------------------


def _linked_set() -> list[Any]:
    return [
        make_entry("chapa-x", sector="sheet-metal", title="Chapa: agujeros", related=["regla-general"]),
        make_entry("moldes-x", sector="injection-molds", title="Moldes: pared"),
        make_entry("otra-regla", sector="fixtures", title="Dispositivos: pasadores", status="inactive"),
        make_entry("ajena", sector="injection-molds", title="No enlazada"),
    ]


def test_related_links_read_both_ways_with_the_project_sector_first() -> None:
    principle_entry = make_entry(
        "regla-general",
        type="principle",
        recognize="x",
        examples=[example("injection-molds")],
        related=["moldes-x", "chapa-x", "otra-regla"],
    )
    others = _linked_set()
    lines = related_lines(principle_entry, others, "chapa metálica", TABLE)
    assert lines[0].startswith("related") and len(lines) == 3  # the inactive one and the stranger are left out
    assert lines[1].startswith("- chapa-x [practice, sheet-metal]")
    assert lines[2].startswith("- moldes-x [practice, injection-molds]")
    # the other direction: a practice that lists the principle is found from the principle, and vice versa
    backwards = make_entry("regla-general", type="principle", recognize="x", examples=[example("injection-molds")])
    assert [line.split()[1] for line in related_lines(backwards, others, None, TABLE)[1:]] == ["chapa-x"]
    practice = next(e for e in others if e.id == "chapa-x")
    assert related_lines(practice, [backwards, *others], None, TABLE)[1].startswith("- regla-general [principle]")


def test_related_is_capped_and_marks_a_proposed_entry() -> None:
    hub = make_entry(
        "hub",
        type="principle",
        recognize="x",
        examples=[example("injection-molds")],
        related=[f"practica-{i}" for i in range(9)],
    )
    many = [
        make_entry(f"practica-{i}", sector="injection-molds", status="proposed" if i == 0 else "active")
        for i in range(9)
    ]
    lines = related_lines(hub, many, None, TABLE)
    assert len(lines) == 8 and lines[-1] == "(+3 more)" and "PROPOSED" in lines[1]
    assert related_lines(make_entry("sola"), many, None, TABLE) == []


def test_related_cannot_name_the_entry_itself_and_round_trips() -> None:
    with pytest.raises(ValueError, match="cannot list the entry itself"):
        make_entry("yo", related=["yo"])
    entry = make_entry("a", related=["b", "c"])
    from cotaforge.memory import parse_entry

    assert parse_entry(render_entry(entry), expected_id="a").related == ("b", "c")
    assert "related" not in render_entry(make_entry("b"))


def test_related_ids_that_do_not_exist_are_a_warning_not_an_error(tmp_path: Path) -> None:
    store = MemoryStore(project_dir=tmp_path / "memory")
    store.save(make_entry("a", related=["b", "fantasma"]), Scope.project)
    store.save(make_entry("b"), Scope.project)
    assert store.problems() == []
    assert store.warnings() == ["project/a.md: related 'fantasma': no entry with that id"]


# ---- replaces ------------------------------------------------------------------------------------------------------


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(project_dir=tmp_path / "memory", user_dir=tmp_path / "usuario", base_dir=base_memory_dir())


BASE_ID = "drawing-sufficient-views"


def test_an_engineer_entry_replaces_a_base_entry_which_is_then_neither_pushed_nor_required(store: MemoryStore) -> None:
    mine = make_entry("mis-vistas", replaces=BASE_ID, title="Mis vistas")
    store.save(mine, Scope.project)
    assert store.replacements() == {BASE_ID: "mis-vistas"}
    active = {i.entry.id for i in store.entries(include_inactive=False)}
    assert BASE_ID not in active and "mis-vistas" in active
    assert store.get(BASE_ID) is not None  # still readable
    assert BASE_ID not in {i.entry.id for i in store.search("vistas")}
    index = store.combined_index()
    assert f"{BASE_ID} [replaced by mis-vistas]" in index
    assert "project — practices: mis-vistas" in index
    assert store.warnings() == []


def test_a_replacement_in_the_user_scope_counts_and_a_proposed_or_inactive_one_does_not(store: MemoryStore) -> None:
    store.save(make_entry("u-vistas", replaces=BASE_ID), Scope.user)
    assert store.replacements() == {BASE_ID: "u-vistas"}
    for status in ("proposed", "inactive"):
        store.save(make_entry("u-vistas", replaces=BASE_ID, status=status), Scope.user)
        assert store.replacements() == {}, status
        assert BASE_ID in {i.entry.id for i in store.entries(include_inactive=False)}, status
        assert "replaced by" not in store.combined_index()


def test_a_replaced_base_entry_is_not_offered_as_similar(store: MemoryStore) -> None:
    twin = make_entry("nueva", title=store.get(BASE_ID).entry.title)
    assert BASE_ID in {i.entry.id for i in store.similar(twin)}
    store.save(make_entry("mis-vistas", replaces=BASE_ID, title="Mis reglas de vistas del plano"), Scope.project)
    assert BASE_ID not in {i.entry.id for i in store.similar(twin)}


def test_the_same_id_in_the_project_wins_and_replaces_has_nothing_left_to_replace(store: MemoryStore) -> None:
    store.save(make_entry(BASE_ID, title="La mía"), Scope.project)
    store.save(make_entry("extra", replaces=BASE_ID), Scope.user)
    assert store.replacements() == {}  # the base entry is not the effective one any more


def test_replaces_that_names_no_base_entry_is_a_warning(store: MemoryStore) -> None:
    store.save(make_entry("a", replaces="no-existe"), Scope.project)
    store.save(make_entry("b", replaces="a"), Scope.project)  # a project entry is not a base entry
    assert store.warnings() == [
        "project/a.md: replaces 'no-existe': no base entry with that id",
        "project/b.md: replaces 'a': no base entry with that id",
    ]
    assert store.replacements() == {}


def test_replaces_cannot_be_the_entry_itself() -> None:
    with pytest.raises(ValueError, match="cannot be the entry itself"):
        make_entry("a", replaces="a")


def test_a_long_title_never_hides_who_replaces_a_base_entry() -> None:
    from cotaforge.memory import index_line

    long = make_entry("larga", title="Un título larguísimo " * 10)
    line = index_line(long, "mi-entrada")
    assert len(line) <= 150 and line.endswith("(larga.md) · practice · replaced by mi-entrada") and "…" in line
    assert index_line(make_entry("corta", title="Corta"), "mi-entrada") == (
        "- [Corta](corta.md) · practice · replaced by mi-entrada"
    )


def test_the_index_of_the_replacing_scope_alone_does_not_hide_anything(store: MemoryStore) -> None:
    store.save(make_entry("mis-vistas", replaces=BASE_ID), Scope.project)
    assert "mis-vistas" in (store.directory(Scope.project) / "INDEX.md").read_text(encoding="utf-8")
    assert "replaced by mis-vistas" in combined_index(store.entries())


# ---- sources and provenance ----------------------------------------------------------------------------------------


def test_sources_are_for_principles_and_practices_only() -> None:
    assert make_entry("p", sources=["ISO 129:1985"]).sources == ("ISO 129:1985",)
    assert principle("injection-molds", sources=["Hubs, guía de moldeo"]).sources
    with pytest.raises(ValueError, match="only for principles and practices"):
        make_entry("m", type="correction", sources=["x"])
    with pytest.raises(ValueError):
        make_entry("v", sources=[""])


def test_a_book_and_a_manufacturer_guide_are_provenance_sources() -> None:
    for source in ("reference_book", "manufacturer_guide"):
        provenance = {"source": source, "reference": "Shigley, tabla 7-1", "by": "Ana", "date": "2026-10-29"}
        assert make_entry("l", provenance=provenance).provenance.source.value == source  # type: ignore[union-attr]


def test_a_practice_with_a_measure_and_no_source_gets_a_warning_never_a_refusal() -> None:
    bare = make_entry("p1", body="Deja 3 mm de holgura y 12,5 kg de masa máxima.")
    warning = practice_warnings(bare)[0]
    assert "3 mm" in warning and "12,5 kg" in warning and "`sources`" in warning
    assert practice_warnings(make_entry("p2", body="Deja 3 mm.", sources=["Guía del taller"])) == []
    provenance = {"source": "shop_measurement", "reference": "tabla", "by": "Ana", "date": "2026-10-29"}
    assert practice_warnings(make_entry("p3", body="Deja 3 mm.", provenance=provenance)) == []
    assert practice_warnings(make_entry("p4", body="Deja holgura suficiente.")) == []  # no measure: nothing to say
    assert practice_warnings(make_entry("p5", body="Ver peine-5mm.")) == []  # an id is not a measure
    assert practice_warnings(make_entry("p6", type="correction", body="3 mm")) == []  # only practices
    in_example = make_entry("p7", examples=[example("injection-molds", "La pared mide 2,5 mm.")])
    assert practice_warnings(in_example)
