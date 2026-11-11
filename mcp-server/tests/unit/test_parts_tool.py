"""The ``catalog`` tool: search / get over the shipped tables, add to the user catalog (temporary home only), and the
provenance every answer carries."""

import json
from pathlib import Path

from cotaforge_mcp.config import Settings
from cotaforge_mcp.schemas import TOOLS_BY_NAME, input_schema
from tests.conftest import Caller

ENTRY = {
    "name": "Tubo cuadrado 40x40x2 barra 6 m",
    "supplier": "Aceros Medellín",
    "date": "2026-10-20",
    "price": 98000,
    "unit": "barra 6 m",
    "availability": "en stock, entrega en 2 días",
    "part": "tube-sq-40x40x2",
}


def test_the_tool_is_listed_small_and_not_gated_by_the_cad(call: Caller) -> None:
    tool = TOOLS_BY_NAME["catalog"]
    assert tool.visible(call.session)
    schema = input_schema(tool, call.session)
    assert schema["properties"]["action"]["enum"] == ["search", "get", "add", "file"]
    assert "NOT the only source" in tool.description and "SIN VERIFICAR" in tool.description


def test_search_without_text_gives_the_overview(call: Caller) -> None:
    text = call.ok("catalog", action="search")
    assert "269 standard parts" in text and "square structural tube: 43" in text
    assert "SIN VERIFICAR" in text and "user catalog: 0 entries" in text
    assert "not the only source" in text


def test_search_rows_carry_their_provenance(call: Caller) -> None:
    text = call.ok("catalog", action="search", text="tubo cuadrado 40x40x2")
    assert "tube-sq-40x40x2 | Tubo estructural cuadrado 40x40x2 mm | 2.45 kg/m | catálogo (NTC 4526:2021" in text
    angle = call.ok("catalog", action="search", text="angulo 50x50x5")
    assert "angle-50x50x5" in angle and "SIN VERIFICAR:" in angle
    verified = call.ok("catalog", action="search", text="angulo 3x3x3/16")
    assert "angle-3x3x3-16in" in verified and "SIN VERIFICAR" not in verified


def test_a_miss_sends_the_agent_to_the_web_and_asks_to_label_it(call: Caller) -> None:
    text = call.ok("catalog", action="search", text="rodamiento 6204")
    assert text.startswith("no match") and "search the web" in text and "web (URL, date)" in text
    assert "assumption" in text


def test_a_long_result_is_cut_and_says_so(call: Caller) -> None:
    text = call.ok("catalog", action="search", text="tubo")
    assert "(135)" in text and "+120 more" in text and text.count("\n- ") == 15


def test_get_shows_dimensions_both_masses_properties_and_the_full_source(call: Caller) -> None:
    text = call.ok("catalog", action="get", id="tube-sq-40x40x2")
    assert "side_mm=40.0, t_mm=2.0" in text
    assert "mass: 2.45 kg/m (publicado; catálogo (NTC 4526:2021, Ternium Colombia oct-2023))" in text
    assert "from the nominal geometry: 2.3" in text
    assert "A=2.937 cm2" in text and "Ix=6.94 cm4" in text and "calculado de las dimensiones nominales" in text
    assert "https://co.ternium.com/media/52qja44b" in text
    assert "producción bajo pedido" not in text


def test_get_flags_an_unverified_row_and_a_made_to_order_one(call: Caller) -> None:
    unverified = call.ok("catalog", action="get", id="angle-5x5x3-8in")
    assert "status: SIN VERIFICAR" in unverified and "18.90" in unverified and "calculado" in unverified
    assert "producción bajo pedido" in call.ok("catalog", action="get", id="tube-sq-26x26x1.5")


def test_get_errors_name_the_alternative(call: Caller) -> None:
    text = call.err("catalog", action="get", id="tube-sq-1x1x1")
    assert text.startswith("error: unknown_part") and "search the web" in text
    assert call.err("catalog", action="get").startswith("error: invalid_input")
    assert call.err("catalog", action="remove", id="x").startswith("error: invalid_input")


def test_add_search_get_round_trip_in_the_user_catalog(call: Caller, settings: Settings) -> None:
    saved = call.ok("catalog", action="add", entry=ENTRY)
    assert saved.startswith("saved: aceros-medellin-tubo-cuadrado-40x40x2-barra-6-m-2026-10-20 in the user catalog")
    assert "catálogo del usuario (Aceros Medellín, 2026-10-20)" in saved
    files = list((settings.home / "catalog").glob("*.json"))  # type: ignore[operator]
    assert len(files) == 1
    assert json.loads(files[0].read_text(encoding="utf-8"))["price"] == 98000
    found = call.ok("catalog", action="search", text="tubo 40x40x2")
    assert found.index("user catalog (1)") < found.index("\ncatalog (")  # the engineer's own quote comes first
    assert "98000 COP/barra 6 m" in found and "en stock" in found
    assert "catálogo del usuario (Aceros Medellín, 2026-10-20)" in found
    shown = call.ok("catalog", action="get", id="tube-sq-40x40x2")
    assert "user catalog for this part:" in shown and "98000 COP/barra 6 m" in shown
    own = call.ok("catalog", action="get", id=files[0].stem)
    assert "supplier: Aceros Medellín" in own and "date: 2026-10-20" in own and "prices go stale" in own


def test_two_quotes_the_same_day_do_not_overwrite_each_other(call: Caller, settings: Settings) -> None:
    call.ok("catalog", action="add", entry=ENTRY)
    call.ok("catalog", action="add", entry={**ENTRY, "price": 99000})
    assert len(list((settings.home / "catalog").glob("*.json"))) == 2  # type: ignore[operator]
    assert "user catalog (2)" in call.ok("catalog", action="search", text="aceros medellin")


def test_add_validates_the_entry_with_actionable_errors(call: Caller) -> None:
    assert "name" in call.err("catalog", action="add", entry={**ENTRY, "name": "x"})
    assert "date" in call.err("catalog", action="add", entry={k: v for k, v in ENTRY.items() if k != "date"})
    assert "future" in call.err("catalog", action="add", entry={**ENTRY, "date": "2999-01-01"})
    assert "unit" in call.err("catalog", action="add", entry={**ENTRY, "unit": None})
    assert "colour" in call.err("catalog", action="add", entry={**ENTRY, "colour": "red"})
    assert "catalog id" in call.err("catalog", action="add", entry={**ENTRY, "part": "tube-sq-9x9x9"})
    assert call.err("catalog", action="add").startswith("error: invalid_input")
    assert "slug" in call.err("catalog", action="add", entry={**ENTRY, "id": "Mal Id"})


def test_an_explicit_existing_id_is_refused(call: Caller) -> None:
    call.ok("catalog", action="add", entry={**ENTRY, "id": "cotizacion-1"})
    text = call.err("catalog", action="add", entry={**ENTRY, "id": "cotizacion-1"})
    assert text.startswith("error: entry_exists") and "new entry" in text


def test_a_broken_user_file_is_skipped_and_reported(call: Caller, settings: Settings) -> None:
    folder = settings.home / "catalog"  # type: ignore[operator]
    folder.mkdir(parents=True)
    (folder / "roto.json").write_text("{not json", encoding="utf-8")
    call.ok("catalog", action="add", entry=ENTRY)
    text = call.ok("catalog", action="search", text="tubo 40x40x2")
    assert "1 user catalog file(s) skipped" in text and "roto.json" in text
    assert "user catalog (1)" in text


def test_the_user_catalog_lives_under_the_configured_home(tmp_path: Path) -> None:
    assert Settings(home=tmp_path / "h").user_catalog_dir() == tmp_path / "h" / "catalog"
    assert Settings().user_catalog_dir() == Path.home() / ".cotaforge" / "catalog"
