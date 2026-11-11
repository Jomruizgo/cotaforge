"""The user catalog has two roles: ``compra`` (a supplier he buys from) and ``medidas`` (a table of commercial sizes
taken from a catalog that is only a size reference). Temporary home only."""

import json
from pathlib import Path

from cotaforge_mcp.config import Settings
from cotaforge_mcp.parts import render
from cotaforge_mcp.parts_tools import RESULT_CHARS
from tests.conftest import Caller

COMPRA = {
    "name": "Tubo cuadrado 40x40x2 barra 6 m",
    "supplier": "Aceros Medellín",
    "date": "2026-10-20",
    "price": 98000,
    "unit": "barra 6 m",
}
MEDIDAS = {
    "role": "medidas",
    "name": "Pasadores de registro endurecidos",
    "standard": "DIN 7979 / ISO 8735",
    "source": "Proveedor Ejemplo, catálogo p.16",
    "date": "2026-10-24",
    "columns": ["d", "L"],
    "rows": [["6", "16"], ["6", "20"], ["8", "20"], ["8", "24"]],
    "notes": "se compra local o se mecaniza",
}


def _files(settings: Settings) -> list[Path]:
    return sorted((settings.home / "catalog").glob("*.json"))  # type: ignore[operator]


def test_a_legacy_file_without_role_loads_as_compra(call: Caller, settings: Settings) -> None:
    folder = settings.home / "catalog"  # type: ignore[operator]
    folder.mkdir(parents=True)
    legacy = {"name": "Tubo viejo 40x40x2", "supplier": "Aceros Medellín", "date": "2026-01-05", "price": 90000}
    legacy |= {"currency": "COP", "unit": "barra 6 m"}
    (folder / "viejo.json").write_text(json.dumps(legacy), encoding="utf-8")
    assert "skipped" not in call.ok("catalog", action="search", text="tubo viejo")
    text = call.ok("catalog", action="get", id="viejo")
    assert "supplier: Aceros Medellín" in text and "price: 90000 COP per barra 6 m" in text
    assert "catálogo del usuario (Aceros Medellín, 2026-01-05)" in text and "medidas" not in text


def test_a_compra_file_keeps_its_old_shape(call: Caller, settings: Settings) -> None:
    call.ok("catalog", action="add", entry=COMPRA)
    written = json.loads(_files(settings)[0].read_text(encoding="utf-8"))
    assert "role" not in written and written["currency"] == "COP" and written["supplier"] == "Aceros Medellín"
    explicit = call.ok("catalog", action="add", entry={**COMPRA, "role": "compra", "price": 99000})
    assert "saved:" in explicit and "never a supplier" not in explicit


def test_a_medidas_entry_is_saved_without_currency_and_read_back_as_a_table(call: Caller, settings: Settings) -> None:
    saved = call.ok("catalog", action="add", entry=MEDIDAS)
    assert saved.startswith("saved: proveedor-ejemplo-catalogo-p-16-pasadores-de-registro-endure-2026-10-24 in")
    assert "medidas según DIN 7979 / ISO 8735 (referencia de medidas: no es proveedor, sin precio)" in saved
    assert "catálogo del usuario, solo medidas (Proveedor Ejemplo, catálogo p.16, 2026-10-24)" in saved
    assert saved.endswith("add only tables the engineer gave or confirmed; this is a size reference, never a supplier")
    (path,) = _files(settings)
    written = json.loads(path.read_text(encoding="utf-8"))
    assert written["role"] == "medidas" and "currency" not in written and "supplier" not in written
    assert written["rows"][0] == ["6", "16"] and "id" not in written
    shown = call.ok("catalog", action="get", id=path.stem)
    assert shown.startswith(f"{path.stem} (user catalog, medidas): Pasadores de registro endurecidos")
    assert "standard: DIN 7979 / ISO 8735" in shown and "table source: Proveedor Ejemplo, catálogo p.16" in shown
    assert "date read: 2026-10-24" in shown and "notes: se compra local" in shown
    assert "| d | L |\n| --- | --- |\n| 6 | 16 |\n| 6 | 20 |\n| 8 | 20 |\n| 8 | 24 |" in shown
    assert "not a supplier" in shown and "no price" in shown and "never name the source as where to buy" in shown
    assert "«medida comercial según DIN 7979 / ISO 8735; conseguir local o fabricar»" in shown
    assert shown.index("never name the source") < shown.index("| d | L |")  # a cut table cannot take it away
    assert shown.splitlines()[-1].startswith("source: catálogo del usuario, solo medidas (")


def test_numbers_in_a_table_are_taken_as_text(call: Caller) -> None:
    entry = {**MEDIDAS, "columns": ["d", "L"], "rows": [[8, 20], [2.5, 12.0]]}
    entry_id = call.ok("catalog", action="add", entry=entry).split()[1]
    assert "| 8 | 20 |\n| 2.5 | 12 |" in call.ok("catalog", action="get", id=entry_id)


def test_a_medidas_entry_refuses_what_only_a_quote_has(call: Caller) -> None:
    refused = {
        "price": ("price", 12000, "no price"),
        "supplier": ("supplier", "Proveedor Ejemplo", "no supplier"),
        "unit": ("unit", "pieza", "no unit"),
        "availability": ("availability", "en stock", "no availability"),
        "part": ("part", "tube-sq-40x40x2", "do not link"),
        "currency": ("currency", "USD", "no currency"),
    }
    for field, value, why in refused.values():
        text = call.err("catalog", action="add", entry={**MEDIDAS, field: value})
        assert text.startswith("error: invalid_entry") and why in text, (field, text)
        assert 'catalog(action="add", entry={"role":"medidas"' in text  # the example is the medidas one
    price = call.err("catalog", action="add", entry={**MEDIDAS, "price": 1})
    assert "if you buy it somewhere, add a compra entry" in price


def test_a_medidas_entry_needs_its_standard_source_and_table(call: Caller) -> None:
    for field in ("standard", "source", "columns", "rows"):
        text = call.err("catalog", action="add", entry={k: v for k, v in MEDIDAS.items() if k != field})
        assert "a medidas entry needs" in text and field in text, (field, text)
    assert "date" in call.err("catalog", action="add", entry={k: v for k, v in MEDIDAS.items() if k != "date"})
    assert "future" in call.err("catalog", action="add", entry={**MEDIDAS, "date": "2999-01-01"})


def test_a_table_must_be_rectangular_and_inside_its_limits(call: Caller) -> None:
    ragged = call.err("catalog", action="add", entry={**MEDIDAS, "rows": [["6", "16"], ["8"], ["8", "20", "x"]]})
    assert "exactly 2 cells" in ragged and "2, 3" in ragged
    assert "columns" in call.err("catalog", action="add", entry={**MEDIDAS, "columns": [], "rows": [[]]})
    assert "columns" in call.err("catalog", action="add", entry={**MEDIDAS, "columns": list("abcdefghi")})
    assert "rows" in call.err("catalog", action="add", entry={**MEDIDAS, "rows": [["1", "2"]] * 201})
    assert "300 characters" in call.err("catalog", action="add", entry={**MEDIDAS, "rows": [["1", "x" * 301]]})
    assert "standard" in call.err("catalog", action="add", entry={**MEDIDAS, "standard": "x" * 121})
    assert "source" in call.err("catalog", action="add", entry={**MEDIDAS, "source": "x" * 201})
    assert "role" in call.err("catalog", action="add", entry={**MEDIDAS, "role": "otro"})


def test_a_compra_entry_refuses_a_table_and_needs_its_supplier(call: Caller) -> None:
    for field, value in (("columns", ["d"]), ("rows", [["6"]]), ("standard", "DIN 7979"), ("source", "Catálogo")):
        text = call.err("catalog", action="add", entry={**COMPRA, field: value})
        assert text.startswith("error: invalid_entry") and "medidas entry" in text, (field, text)
        assert 'catalog(action="add", entry={"name":"Tubo cuadrado' in text  # the compra example
    no_supplier = call.err("catalog", action="add", entry={k: v for k, v in COMPRA.items() if k != "supplier"})
    assert "needs the supplier" in no_supplier and "role='medidas'" in no_supplier


def test_search_finds_a_medidas_entry_by_standard_source_columns_and_cells(call: Caller) -> None:
    call.ok("catalog", action="add", entry=MEDIDAS)
    call.ok("catalog", action="add", entry=COMPRA)
    for words in ("din 7979", "iso 8735", "pasadores registro", "ejemplo catalogo p.16", "24"):
        found = call.ok("catalog", action="search", text=words)
        assert "user catalog (1)" in found, words
        assert "medidas según DIN 7979 / ISO 8735 (referencia de medidas: no es proveedor, sin precio)" in found
    assert "medidas según" not in call.ok("catalog", action="search", text="tubo cuadrado 40x40x2")


def test_the_overview_reports_the_split(call: Caller) -> None:
    assert "user catalog: 0 entries (0 compra:" in call.ok("catalog", action="search")
    call.ok("catalog", action="add", entry=COMPRA)
    call.ok("catalog", action="add", entry=MEDIDAS)
    text = call.ok("catalog", action="search")
    assert "user catalog: 2 entries (1 compra: suppliers, prices, availability; 1 medidas: size tables only; " in text
    assert "each with a date)" in text


def test_a_large_table_keeps_the_instruction_and_the_source_line(call: Caller) -> None:
    rows = [[str(10 + n), f"{40 + n} mm acero endurecido {n:03d}"] for n in range(200)]
    entry = {**MEDIDAS, "name": "Tabla grande", "rows": rows}
    saved = call.ok("catalog", action="add", entry=entry)
    entry_id = saved.split()[1]
    shown = call.ok("catalog", action="get", id=entry_id)
    assert len(shown) <= RESULT_CHARS and "chars cut" not in shown
    assert "never name the source as where to buy" in shown and shown.splitlines()[-1].startswith("source:")
    assert "| 10 | 40 mm acero endurecido 000 |" in shown and "more rows not shown (200 in all" in shown
    assert "acero endurecido 199" not in shown
    assert render.REPLY_CHARS == RESULT_CHARS
