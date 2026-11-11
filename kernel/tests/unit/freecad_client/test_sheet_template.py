"""``worker/sheet_template.py`` and the shipped Spanish templates: the title block as sheet entities, its fields
and the promise that the names TechDraw uses are the ones to fill."""

import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_model as model  # noqa: E402
import sheet_svg as svg  # noqa: E402
import sheet_template as tpl  # noqa: E402

TEMPLATES = WORKER_DIR / "templates"
# the fields the stock TechDraw templates have: a template of ours that renames one breaks the fields of the GUI sheet
TECHDRAW_FIELDS = {
    "legal_owner_1",
    "drawing_number",
    "revision_index",
    "date_of_issue",
    "sheet_number",
    "language_code",
    "title",
    "approval_person",
    "creator",
    "document_type",
    "scale",
    "general_tolerances",
    "part_material",
}


def _read(name: str) -> str:
    return (TEMPLATES / f"{name}.svg").read_text(encoding="utf-8")


@pytest.mark.parametrize(("name", "size"), [("A4_es", (297.0, 210.0)), ("A3_es", (420.0, 297.0))])
def test_the_spanish_templates_have_the_iso_page_sizes(name: str, size: tuple[float, float]) -> None:
    width, height, entities = tpl.parse(_read(name), {})
    assert (width, height) == size and entities


@pytest.mark.parametrize("name", ["A4_es", "A3_es"])
def test_the_spanish_templates_keep_the_field_names_of_techdraw(name: str) -> None:
    fields = set(tpl.editable_fields(_read(name)))
    assert TECHDRAW_FIELDS <= fields
    assert {f"rev_{i}_{k}" for i in range(1, 6) for k in ("index", "description", "date", "by")} <= fields


@pytest.mark.parametrize("name", ["A4_es", "A3_es"])
def test_the_fixed_labels_are_in_spanish(name: str) -> None:
    _, _, entities = tpl.parse(_read(name), {})
    texts = {e.text for e in entities if isinstance(e, model.Text)}
    for label in (
        "Título:",
        "Dibujó:",
        "Aprobó:",
        "Número de plano:",
        "Revisión:",
        "Fecha:",
        "Hoja:",
        "Escala:",
        "Material:",
        "Tolerancias generales:",
        "Tipo de documento:",
        "Empresa:",
        "Descripción",
    ):
        assert label in texts, label
    assert not {"Title:", "Created by:", "Drawing number:", "Sheet:", "Scale:"} & texts


def test_the_fields_fill_the_editable_text_and_an_empty_field_leaves_nothing() -> None:
    fields = {"title": "Placa base", "general_tolerances": "ISO 2768-mK", "drawing_number": "", "revision_index": "B"}
    _, _, entities = tpl.parse(_read("A3_es"), fields)
    texts = [e.text for e in entities if isinstance(e, model.Text)]
    assert "Placa base" in texts and "ISO 2768-mK" in texts and "B" in texts
    assert "ISO 5457 template" not in texts and "A. Nemesis" not in texts  # the stock sample text is not in ours


def test_a_long_value_is_made_smaller_to_fit_its_cell() -> None:
    _, _, short = tpl.parse(_read("A3_es"), {"title": "Placa"})
    _, _, long = tpl.parse(_read("A3_es"), {"title": "Placa de anclaje del brazo de la pinza, versión larga " * 2})
    size = {e.text: e.height for e in short if isinstance(e, model.Text) and e.text == "Placa"}["Placa"]
    smaller = [e.height for e in long if isinstance(e, model.Text) and e.text.startswith("Placa de anclaje")][0]
    assert smaller < size and smaller >= tpl.MIN_TEXT_MM


def test_the_title_block_is_inside_the_frame_at_the_bottom_right() -> None:
    width, height, entities = tpl.parse(_read("A3_es"), {"title": "x"})
    frame = [e for e in entities if isinstance(e, model.Polyline) and e.width == 0.7 and len(e.points) == 4]
    boxes = {(min(p[0] for p in e.points), min(p[1] for p in e.points), max(p[0] for p in e.points)) for e in frame}
    assert (20.0, 10.0, 410.0) in boxes  # the drawing-space frame, y up: its bottom is 10 mm above the page's bottom
    assert (230.0, 10.0, 410.0) in boxes  # the title block: 180 x 36 mm in the corner
    title = next(e for e in entities if isinstance(e, model.Text) and e.text == "x")
    assert 230.0 <= title.x <= 410.0 and 10.0 <= title.y <= 46.0


def test_the_revision_table_sits_left_of_the_title_block_and_never_overlaps_it() -> None:
    _, _, entities = tpl.parse(_read("A3_es"), {"rev_1_index": "A", "rev_1_description": "Emisión inicial"})
    texts = {e.text: e for e in entities if isinstance(e, model.Text)}
    assert texts["A"].x < 230.0 and texts["Emisión inicial"].x < 230.0
    assert texts["Emisión inicial"].y > texts["Descripción"].y - 20  # a row under the header (y is up)


def test_the_first_angle_symbol_is_a_centre_line_layer_and_circles() -> None:
    _, _, entities = tpl.parse(_read("A3_es"), {})
    assert len([e for e in entities if isinstance(e, model.Circle)]) == 2
    assert any(isinstance(e, model.Line) and e.layer == model.AXES for e in entities)


def test_a_template_is_the_same_in_the_svg_and_in_the_model() -> None:
    sheet = model.Sheet(420, 297)
    _, _, entities = tpl.parse(_read("A3_es"), {"title": "Soporte"})
    sheet.extend(entities)
    text = svg.to_svg(sheet)
    ET.fromstring(text)  # well formed
    assert "Soporte" in text and "Título:" in text and "Tolerancias generales:" in text


def test_the_reader_refuses_an_element_it_does_not_know() -> None:
    bad = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"><ellipse cx="1" cy="1" rx="1" ry="1"/></svg>'
    with pytest.raises(ValueError, match="ellipse"):
        tpl.parse(bad, {})


def test_path_commands_relative_absolute_and_implicit_lineto() -> None:
    paths = tpl._subpaths("m 10,10 h 5 v 5 l -5,0 z M 1,1 L 2,2")
    assert paths[0] == ([(10.0, 10.0), (15.0, 10.0), (15.0, 15.0), (10.0, 15.0)], True)
    assert paths[1] == ([(1.0, 1.0), (2.0, 2.0)], False)
    assert tpl._subpaths("m 389,234 -10,2.5 v 5 l 10,2.5 z")[0][0] == [
        (389.0, 234.0),
        (379.0, 236.5),
        (379.0, 241.5),
        (389.0, 244.0),
    ]


def test_the_stock_english_template_is_read_too() -> None:
    from cotaforge.adapters.freecad import find_freecad_root  # noqa: PLC0415

    root = find_freecad_root()
    stock = next(iter(root.rglob("A3_Landscape_ISO5457_minimal.svg")), None) if root else None
    if stock is None:
        pytest.skip("no FreeCAD install to read the stock template from")
    width, height, entities = tpl.parse(stock.read_text(encoding="utf-8"), {"title": "Part"})
    assert (width, height) == (420.0, 297.0)
    assert any(isinstance(e, model.Solid) for e in entities)  # the trimming marks are filled
    assert "Drawing number:" in {e.text for e in entities if isinstance(e, model.Text)}


def test_the_templates_travel_with_the_installation() -> None:
    import tomllib  # noqa: PLC0415

    pyproject = tomllib.loads((Path(__file__).resolve().parents[3] / "pyproject.toml").read_text(encoding="utf-8"))
    shipped = pyproject["tool"]["setuptools"]["package-data"]["cotaforge.adapters.freecad.worker"]
    assert "templates/*.svg" in shipped
    assert {p.name for p in TEMPLATES.glob("*.svg")} == {"A3_es.svg", "A4_es.svg"}
