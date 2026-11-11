"""Memory v2: the new optional fields, triggers, example ordering, the saving rules, the proposed status and the base set."""

from pathlib import Path
from typing import Any

import pytest

from cotaforge.memory import (
    OPS_VOCABULARY,
    EntryStatus,
    MemoryEntry,
    MemoryStore,
    MemoryStoreError,
    Scope,
    SectorView,
    Situation,
    Stage,
    base_memory_dir,
    combined_index,
    index_line,
    ordered_examples,
    parse_entry,
    practice_warnings,
    principle_findings,
    push_line,
    pushed_by,
    related_entries,
    render_entry,
    render_full,
    triggered_at_milestone,
)
from tests.unit.memory.builders import make_entry

# Base principles with no trigger by design: a trigger on every op or every review would be noise. They are read on
# demand (memory index / search) and through the `related` list of a principle that does push.
PULL_ONLY_PRINCIPLES = {
    "design-consistent-datums-function-manufacturing-inspection",  # its drawing side fires through dibujo-cotas-*
    "design-tolerance-the-process-holds",
    "design-precision-and-stiffness-in-sensitive-direction",
}

EXAMPLES = [
    {"sector": "fixtures", "text": "Una placa soldada con agujeros de ubicación.", "validated": True},
    {"sector": "automotriz", "text": "Un soporte de chasis.", "validated": False},
    {"sector": "agricola", "text": "Una pieza de un implemento.", "validated": False},
    {"sector": "mobiliario", "text": "Un herraje.", "validated": False},
]


def principle(**overrides: Any) -> MemoryEntry:
    fields: dict[str, Any] = {
        "type": "principle",
        "recognize": "Una pieza soldada que lleva un patrón de agujeros con tolerancia fina",
        "examples": EXAMPLES,
        "typical_error": "Pedir la tolerancia a la pieza soldada",
        "when": {"roles": ["soldado"], "stages": ["detail"]},
    }
    fields.update(overrides)
    return make_entry("precision-del-ensamble", **fields)


# ---- the model -------------------------------------------------------------------------------------------------------


def test_an_entry_without_the_new_fields_loads_and_renders_unchanged() -> None:
    entry = make_entry()
    text = render_entry(entry)
    for key in ("recognize", "examples", "typical_error", "cases", "when", "sector", "case"):
        assert f"\n{key}:" not in text
    assert parse_entry(text, expected_id=entry.id) == entry
    assert entry.is_active and not entry.is_proposed


def test_the_new_fields_round_trip() -> None:
    entry = principle(cases=["caso-placa"], sector="fixtures")
    assert parse_entry(render_entry(entry), expected_id=entry.id) == entry
    assert entry.when is not None and entry.when.roles == ("soldado",) and entry.when.stages == (Stage.detail,)


def test_when_ops_come_from_a_closed_vocabulary() -> None:
    assert {"hole", "insert_component", "mates", "drawing_export", "delete_feature", "edit_feature"} <= OPS_VOCABULARY
    with pytest.raises(ValueError, match="unknown ops"):
        principle(when={"ops": ["weld"]})
    with pytest.raises(ValueError):
        principle(when={"roles": ["Not A Role"]})


def test_a_case_entry_needs_its_block_and_only_it() -> None:
    block = {
        "paths": ["casos/placa.FCStd"],
        "facts": ["masa 4.2 kg (pesada)"],
        "teaches": "El patrón se ubica en el ensamble",
    }
    case = make_entry("caso-placa", type="case", case=block)
    assert case.case is not None and case.case.teaches.startswith("El patrón")
    with pytest.raises(ValueError, match="needs a 'case' block"):
        make_entry("caso-x", type="case")
    with pytest.raises(ValueError, match="only for type 'case'"):
        make_entry("regla", case=block)


# ---- triggers -------------------------------------------------------------------------------------------------------


def test_an_op_pushes_an_entry_by_role_or_op_and_stage() -> None:
    entry = principle()
    detail = Stage.detail
    assert pushed_by(entry, Situation(op="hole", roles=frozenset({"soldado"}), stage=detail))
    assert not pushed_by(entry, Situation(op="hole", roles=frozenset({"estructura"}), stage=detail))
    assert not pushed_by(entry, Situation(op="hole", roles=frozenset({"soldado"}), stage=Stage.concept))
    by_op = principle(when={"ops": ["mates"]})
    assert pushed_by(by_op, Situation(op="mate_coincident"))
    assert not pushed_by(by_op, Situation(op="hole"))
    assert not pushed_by(principle(when={"stages": ["detail"]}), Situation(op="hole", stage=detail))  # milestone only
    assert not pushed_by(principle(when=None), Situation(op="hole"))


def test_a_proposed_or_inactive_entry_is_never_pushed_nor_required() -> None:
    for status in (EntryStatus.proposed, EntryStatus.inactive):
        entry = principle(status=status.value)
        assert not pushed_by(entry, Situation(op="hole", roles=frozenset({"soldado"}), stage=Stage.detail))
        assert not triggered_at_milestone(entry, stage=Stage.detail, roles=["soldado"], ops_run=["hole"])


def test_the_milestone_looks_at_roles_present_ops_run_and_the_stage() -> None:
    entry = principle()
    assert triggered_at_milestone(entry, stage=Stage.detail, roles=["soldado"], ops_run=[])
    assert not triggered_at_milestone(entry, stage=Stage.layout, roles=["soldado"], ops_run=[])
    assert not triggered_at_milestone(entry, stage=Stage.detail, roles=["sensor"], ops_run=[])
    both = principle(when={"roles": ["soldado"], "ops": ["hole"]})
    assert not triggered_at_milestone(both, stage=Stage.detail, roles=["soldado"], ops_run=["extrude"])
    assert triggered_at_milestone(both, stage=Stage.detail, roles=["soldado"], ops_run=["extrude", "hole"])
    only_stage = principle(when={"stages": ["layout"]})
    assert triggered_at_milestone(only_stage, stage=Stage.layout, roles=[], ops_run=[])


def test_the_push_line_is_one_short_line() -> None:
    line = push_line(principle(recognize="x " * 200))
    assert line.startswith("memory: precision-del-ensamble — recognize: ") and "\n" not in line and len(line) < 180


# ---- examples ------------------------------------------------------------------------------------------------------


def test_examples_of_the_project_sector_come_first_then_two_analogies() -> None:
    shown = ordered_examples(principle(), "Dispositivos")
    assert [(e.sector, analogy) for e, analogy in shown] == [
        ("fixtures", False),
        ("automotriz", True),
        ("agricola", True),
    ]
    text = render_full(principle(), "fixtures")
    assert text.index("[fixtures] (validated)") < text.index("analogy of automotriz: does it apply here?")
    assert "(NOT validated)" in text and "mobiliario" not in text and "1 more example(s)" in text
    assert "analogy" in render_full(principle(), None)  # undeclared sector: every example is an analogy


# ---- the saving rules ------------------------------------------------------------------------------------------------


def test_a_clean_principle_has_no_findings() -> None:
    assert principle_findings(principle(), ["fixtures", "soldadura"]) == []


def test_a_principle_with_sector_brand_or_numbers_is_refused() -> None:
    sectors = ["fixtures", "soldadura"]
    dirty = principle(body="En fixtures se usa un perno Acme X200 y una tolerancia de 0.05 mm, pieza 123-456.")
    found = " | ".join(principle_findings(dirty, sectors))
    assert "sector/process 'fixtures'" in found
    assert "'X200'" in found and "'123-456'" in found and "'0.05 mm'" in found
    vendor = principle(title="Usar los soportes de Festolux siempre")
    assert "brand or vendor 'Festolux'" in " | ".join(principle_findings(vendor, sectors))
    assert "'19 mm'" in " | ".join(principle_findings(principle(applies_when="Agujeros de 19 mm"), sectors))


def test_a_principle_needs_recognize_and_an_example_but_a_practice_does_not() -> None:
    bare = make_entry("regla", type="principle")
    found = principle_findings(bare, [])
    assert any("recognize" in f for f in found) and any("example" in f for f in found)
    assert principle_findings(make_entry("practica", body="En fixtures, 19 mm de pared."), ["fixtures"]) == []


# ---- the store: proposed, approve, similar, base ---------------------------------------------------------------------


def test_a_proposed_entry_is_pending_in_the_index_and_approve_activates_it(tmp_path: Path) -> None:
    store = MemoryStore(project_dir=tmp_path / "memory")
    store.save(principle(status="proposed"), Scope.project)
    index = store.combined_index()
    assert "precision-del-ensamble [proposed]" in index
    assert store.entries(include_inactive=False) == []
    assert store.search("precision") == []
    store.approve("precision-del-ensamble", Scope.project)
    assert [i.entry.id for i in store.entries(include_inactive=False)] == ["precision-del-ensamble"]
    assert "[proposed]" not in store.combined_index()
    with pytest.raises(MemoryStoreError, match="not proposed"):
        store.approve("precision-del-ensamble", Scope.project)
    with pytest.raises(MemoryStoreError, match="no entry"):
        store.approve("nada", Scope.project)


def test_similar_entries_are_found_by_title_tags_and_recognize(tmp_path: Path) -> None:
    store = MemoryStore(project_dir=tmp_path / "memory")
    store.save(principle(), Scope.project)
    twin = make_entry("otra", title="Pieza soldada con patrón de agujeros")
    assert [i.entry.id for i in store.similar(twin)] == ["precision-del-ensamble"]
    assert store.similar(make_entry("ajena", title="Rodamientos", tags=["eje"])) == []
    assert store.similar(principle()) == []  # itself is not similar


def test_the_base_set_loads_is_read_only_and_is_overridden_by_id(tmp_path: Path) -> None:
    store = MemoryStore(project_dir=tmp_path / "memory", base_dir=base_memory_dir())
    base_ids = {i.entry.id for i in store.entries() if i.scope is Scope.base}
    assert len(base_ids) >= 15 and not store.problems()
    with pytest.raises(MemoryStoreError, match="read-only"):
        store.save(make_entry("drawing-sufficient-views"), Scope.base)
    mine = make_entry("drawing-sufficient-views", title="La mía")
    store.save(mine, Scope.project)
    assert store.get("drawing-sufficient-views").scope is Scope.project  # type: ignore[union-attr]
    off = mine.model_copy(update={"status": EntryStatus.inactive})
    store.save(off, Scope.project)
    assert "drawing-sufficient-views" not in {i.entry.id for i in store.entries(include_inactive=False)}
    assert "base — principles:" in combined_index(store.entries())


def test_every_base_principle_is_valid_generic_and_free_of_sector_words() -> None:
    store = MemoryStore(base_dir=base_memory_dir())
    table = store.sector_table()
    items = [i for i in store.scope_entries(Scope.base) if i.entry.type.value == "principle"]
    assert len({e.sector for i in items for e in i.entry.examples}) >= 5
    ids = [i.entry.id for i in store.scope_entries(Scope.base)]
    for item in items:
        entry = item.entry
        assert entry.recognize, entry.id
        if entry.id in PULL_ONLY_PRINCIPLES:  # read on demand (memory index / search): no trigger by design
            assert entry.when is None, entry.id
        else:
            assert entry.when is not None and not entry.when.is_empty, entry.id
        assert len(entry.examples) >= 2 and len({e.sector for e in entry.examples}) == len(entry.examples), entry.id
        assert not any(e.validated for e in entry.examples), entry.id  # none is validated by an engineer yet
        assert all(table.is_canonical(e.sector) for e in entry.examples), entry.id
        assert principle_findings(entry, table.sector_words(), ids) == [], entry.id


def test_every_base_practice_names_its_sector_and_where_it_comes_from() -> None:
    store = MemoryStore(base_dir=base_memory_dir())
    practices = [i.entry for i in store.scope_entries(Scope.base) if i.entry.type.value == "practice"]
    wanted = {"sheet-metal-hole-edge-and-spacing", "welded-hollow-section-vented-closure"}
    assert {p.id for p in practices} >= wanted
    for practice in practices:
        assert practice.sector and store.sector_table().is_canonical(practice.sector), practice.id
        assert practice.sources, practice.id
        assert practice_warnings(practice) == [], practice.id


def test_the_shop_word_registro_finds_the_locating_dowel_entries() -> None:
    """In some shops a «registro» is the pin that locates two parts of a fixture or machine (not the pilot, not the guide)."""
    store = MemoryStore(base_dir=base_memory_dir())
    for text in ("registro", "registros", "Registro"):
        found = {item.entry.id for item in store.search(text)}
        assert {"design-how-each-part-is-located", "fixtures-body-and-component-joining"} <= found, text
    practice = "fixtures-body-and-component-joining"
    body = next(i.entry.body for i in store.scope_entries(Scope.base) if i.entry.id == practice)
    assert "registration pins" in body and "pilot" in body and "guide" in body and "DIN 7979 / ISO 8735" in body


def test_the_base_set_is_consistent_in_itself() -> None:
    store = MemoryStore(base_dir=base_memory_dir())
    assert store.problems() == [] and store.warnings() == []
    ids = {i.entry.id for i in store.scope_entries(Scope.base)}
    dropped = {"dibujo-cotas-fuera-de-la-pieza", "diseno-extremos-de-perfil-abiertos"}  # dropped / turned practice
    assert not dropped & ids
    assert "welded-hollow-section-vented-closure" in ids


def test_the_base_triggers_of_precision_and_location_do_not_need_the_welded_role() -> None:
    items = {i.entry.id: i.entry for i in MemoryStore(base_dir=base_memory_dir()).scope_entries(Scope.base)}
    precision = items["design-precision-the-assembly-guarantees"]
    for op in ("hole", "unfold", "edge_flange", "base_flange"):  # sheet metal and machined parts, no role needed
        assert pushed_by(precision, Situation(op=op, stage=Stage.detail)), op
    location = items["design-how-each-part-is-located"]
    for role in ("soldado", "piloto", "calza"):
        assert pushed_by(location, Situation(op="insert_component", roles=frozenset({role}), stage=Stage.detail)), role


LEGACY_BASE_IDS = {
    *("sheet-metal-hole-edge-and-spacing", "drawing-feature-size-and-position"),
    "drawing-dimensions-from-functional-datums",
    *(
        "drawing-manufacturing-data-in-title-block",
        "drawing-hidden-lines-or-section",
        "drawing-hatching-cut-material-only",
    ),
    *(
        "drawing-thread-designation",
        "drawing-general-tolerance-declared",
        "drawing-one-dimension-per-feature-direction",
    ),
    *("drawing-sufficient-views", "design-support-and-edge-distance", "design-how-each-part-is-located"),
    *("design-escape-directions", "design-target-mass", "design-precision-the-assembly-guarantees"),
    *("design-actuator-limit-and-confirmation", "welded-hollow-section-vented-closure"),
}


def test_the_sector_set_adds_no_push_nor_question_to_a_plain_detail_part() -> None:
    """A part with holes, cuts and fillets (no role, or welded) at detail: the entries of the sector set have narrow
    triggers (flanges, draft, shell, assembly ops with locator / clamp / actuator roles) and stay out of its way."""
    entries = [i.entry for i in MemoryStore(base_dir=base_memory_dir()).scope_entries(Scope.base)]
    ops = ["sketch", "extrude", "hole", "cut", "fillet", "chamfer", "linear_pattern", "boolean_union", "mirror"]
    for roles in (frozenset(), frozenset({"soldado"}), frozenset({"estructura", "soporte"})):
        situations = [Situation(op=op, roles=roles, stage=Stage.detail) for op in ops]
        pushed = {e.id for e in entries for situation in situations if pushed_by(e, situation)}
        asked = {e.id for e in entries if triggered_at_milestone(e, stage=Stage.detail, roles=roles, ops_run=ops)}
        assert (pushed | asked) <= LEGACY_BASE_IDS, sorted((pushed | asked) - LEGACY_BASE_IDS)


def test_the_sector_set_triggers_where_the_process_or_the_role_says_so() -> None:
    items = {i.entry.id: i.entry for i in MemoryStore(base_dir=base_memory_dir()).scope_entries(Scope.base)}
    detail, layout = Stage.detail, Stage.layout

    def pushes(entry_id: str, op: str, roles: tuple[str, ...] = (), stage: Stage = detail) -> bool:
        return pushed_by(items[entry_id], Situation(op=op, roles=frozenset(roles), stage=stage))

    assert pushes("sheet-metal-min-inner-bend-radius", "base_flange") and pushes("molds-draft-angle", "draft")
    assert pushes("design-tool-reaches-the-feature", "edge_flange")
    assert pushes("design-outlet-for-what-accumulates", "shell")
    assert pushes("design-one-support-per-degree-of-freedom", "mate_coincident", ("apoyo",))
    assert pushes("design-clamping-force-toward-supports", "mate_distance", ("fijacion",))
    assert pushes("design-assembly-only-one-way", "insert_component", ("contacto",))
    assert pushes("design-side-load-off-the-actuator", "add_joint", ("actuador",), layout)
    assert pushes("design-distortion-from-heat-or-forming", "extrude", ("soldado",), layout)
    assert pushes("design-access-for-assembly-and-maintenance", "check_interference")
    assert pushes("design-how-each-part-is-located", "mate_coincident", ("fijacion",))  # screws fold in here
    assert not pushes("design-one-support-per-degree-of-freedom", "hole", ("apoyo",))  # a role alone is not enough
    for pull_only in PULL_ONLY_PRINCIPLES:
        assert items[pull_only].when is None


def test_a_pushed_principle_lists_the_practices_that_carry_its_numbers() -> None:
    store = MemoryStore(base_dir=base_memory_dir())
    entries = [i.entry for i in store.scope_entries(Scope.base)]
    tool = next(e for e in entries if e.id == "design-tool-reaches-the-feature")
    linked = {e.id for e in related_entries(tool, entries)}
    assert {"sheet-metal-min-flange-length", "sheet-metal-bend-relief", "molds-draft-angle"} <= linked
    assert any(i.startswith("machining-") for i in linked)


def test_the_index_view_collapses_base_practices_by_sector_and_nothing_else() -> None:
    store = MemoryStore(base_dir=base_memory_dir())
    table = store.sector_table()
    full = store.combined_index()
    assert (
        "sheet-metal-min-flange-length" in full and "practices (memory index" not in full
    )  # no view: every practice listed
    view = SectorView(table, frozenset({"sheet-metal"}), declared=True)
    text = store.combined_index(view)
    assert "base — practices sheet-metal:" in text and "sheet-metal-min-flange-length" in text
    assert "fixtures-3-2-1-and-4-2-1-location" not in text
    assert "- fixtures: 8 practices (memory index sector=fixtures)" in text
    assert "- sheet-metal:" not in text and "sector not declared" not in text
    principles = [e.id for e in (i.entry for i in store.scope_entries(Scope.base)) if e.type.value == "principle"]
    assert all(principle in text for principle in principles)  # a principle is always listed
    undeclared = store.combined_index(SectorView(table))
    assert "sector not declared" in undeclared and "sheet-metal-min-flange-length" not in undeclared
    assert len(text) < len(full) and len(undeclared) < len(full)


def test_every_index_line_of_the_base_set_carries_the_id_to_read_it_with() -> None:
    for item in MemoryStore(base_dir=base_memory_dir()).scope_entries(Scope.base):
        line = index_line(item.entry)
        assert f"({item.entry.id}.md)" in line and len(line) <= 150, item.entry.id


def test_the_base_set_covers_the_drawing_and_review_questions() -> None:
    ids = {i.entry.id for i in MemoryStore(base_dir=base_memory_dir()).scope_entries(Scope.base)}
    drawing = {i for i in ids if i.startswith("drawing-")}
    review = {i for i in ids if i.startswith("design-")}
    assert len(drawing) >= 9 and len(review) >= 6
