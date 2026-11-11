"""``worker/sheet_model.py``, ``sheet_svg.py`` and ``sheet_dxf.py`` in isolation: the sheet as plain data and its two
writers. The DXF is checked with ezdxf (a dev-only dependency): it must read the file and find nothing to repair."""

import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))  # the worker modules import their siblings by bare name

import sheet_dxf as dxf  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_svg as svg  # noqa: E402

SVG_NS = "{http://www.w3.org/2000/svg}"


def _sheet() -> model.Sheet:
    sheet = model.Sheet(420.0, 297.0, "Plano de prueba")
    sheet.add(
        model.Line(10, 10, 100, 10, model.CONTOUR),
        model.Polyline(((10, 20), (50, 20), (50, 40)), model.CONTOUR),
        model.Polyline(((60, 20), (90, 20), (90, 40), (60, 40)), model.HIDDEN, closed=True),
        model.Circle(30, 60, 4.0, model.CONTOUR),
        model.Arc(70, 60, 10.0, 30.0, 120.0, model.DIMENSIONS),
        model.Line(0, 80, 40, 80, model.AXES),
        model.Text(10, 100, "Ø8 pasante ±0,1 90°", 3.5, model.TEXT),
        model.Text(100, 100, "centrado", 3.5, model.DIMENSIONS, anchor="center"),
        model.Text(100, 110, "derecha", 3.5, model.DIMENSIONS, anchor="right", angle=90.0),
        model.Solid(((0, 0), (3, 0.9), (3, -0.9)), model.DIMENSIONS),
        model.Glyph("diameter", 120, 100, 3.5, model.DIMENSIONS),
    )
    return sheet


# --- the model --------------------------------------------------------------------------------------------------------


def test_numbers_are_written_with_at_most_two_decimals_and_the_decimal_sign_of_the_language() -> None:
    assert model.fmt(60.0) == "60" and model.fmt(12.5) == "12.5" and model.fmt(12.504) == "12.5"
    assert model.fmt(-0.001) == "0"
    assert model.fmt(12.5, comma=True) == "12,5" and model.fmt(6.6, comma=True) == "6,6"
    assert model.fmt_scale(0.5) == "1 : 2" and model.fmt_scale(2.0) == "2 : 1" and model.fmt_scale(1.0) == "1 : 1"


def test_a_glyph_is_drawn_as_strokes_and_an_unknown_one_is_an_error() -> None:
    strokes = model.Glyph("diameter", 0, 0, 10.0).strokes()
    assert len(strokes) == 2 and all(isinstance(s, model.Polyline) for s in strokes)
    with pytest.raises(ValueError, match="unknown glyph"):
        model.Glyph("nope", 0, 0).strokes()


def test_every_layer_has_a_colour_and_a_known_line_type() -> None:
    assert set(model.LAYERS) >= {"CONTORNO", "OCULTAS", "COTAS", "TEXTO", "ROTULO", "EJES", "SOMBREADO", "ROSCAS"}
    assert all(style.linetype in model.LINETYPES for style in model.LAYERS.values())


# --- SVG --------------------------------------------------------------------------------------------------------------


def test_the_svg_has_the_page_size_one_group_per_layer_and_y_flipped() -> None:
    root = ET.fromstring(svg.to_svg(_sheet()))
    assert root.get("width") == "420mm" and root.get("height") == "297mm" and root.get("viewBox") == "0 0 420 297"
    groups = {g.get("id"): g for g in root.iter(f"{SVG_NS}g")}
    assert {"layer-CONTORNO", "layer-OCULTAS", "layer-COTAS", "layer-TEXTO", "layer-EJES"} <= set(groups)
    line = groups["layer-CONTORNO"].find(f"{SVG_NS}path")
    assert line is not None and line.get("d") == "M 10 287 L 100 287"  # y 10 (up) is 287 (down) on a 297 mm page


def test_a_hidden_or_centre_layer_is_dashed_and_a_continuous_one_is_not() -> None:
    root = ET.fromstring(svg.to_svg(_sheet()))
    by_layer = {g.get("id"): g for g in root.iter(f"{SVG_NS}g")}
    assert by_layer["layer-OCULTAS"].find(f"{SVG_NS}path").get("stroke-dasharray") == "3 1.5"
    assert by_layer["layer-EJES"].find(f"{SVG_NS}path").get("stroke-dasharray") is not None
    assert by_layer["layer-CONTORNO"].find(f"{SVG_NS}path").get("stroke-dasharray") is None


def test_svg_text_keeps_the_real_characters_the_anchor_and_the_rotation() -> None:
    text = svg.to_svg(_sheet())
    assert "Ø8 pasante ±0,1 90°" in text
    root = ET.fromstring(text)
    texts = {t.text: t for t in root.iter(f"{SVG_NS}text")}
    assert texts["centrado"].get("text-anchor") == "middle"
    assert texts["derecha"].get("text-anchor") == "end" and "rotate(-90" in texts["derecha"].get("transform", "")


def test_svg_text_is_escaped() -> None:
    sheet = model.Sheet(100, 100)
    sheet.add(model.Text(1, 1, "a < b & c"))
    root = ET.fromstring(svg.to_svg(sheet))  # parses: the markup characters were escaped
    assert next(root.iter(f"{SVG_NS}text")).text == "a < b & c"


def test_an_arc_is_a_counter_clockwise_svg_arc() -> None:
    sheet = model.Sheet(100, 100)
    sheet.add(model.Arc(50, 50, 10, 0, 90))
    path = next(ET.fromstring(svg.to_svg(sheet)).iter(f"{SVG_NS}path"))
    assert path.get("d") == "M 60 50 A 10 10 0 0 0 50 40"  # from 3 o'clock to 12 o'clock, anticlockwise on the page


def test_a_big_arc_sets_the_large_arc_flag() -> None:
    sheet = model.Sheet(100, 100)
    sheet.add(model.Arc(50, 50, 10, 0, 270))
    path = next(ET.fromstring(svg.to_svg(sheet)).iter(f"{SVG_NS}path"))
    assert " 0 1 0 " in path.get("d")


# --- DXF --------------------------------------------------------------------------------------------------------------


def test_the_dxf_is_cp1252_with_the_r12_codes_for_the_special_signs() -> None:
    data = dxf.to_dxf(_sheet())
    text = data.decode("cp1252")
    assert "AC1009" in text and "ANSI_1252" in text
    assert "%%c8 pasante %%p0,1 90%%d" in text
    assert "Ø" not in text and "±" not in text and "°" not in text


def test_accented_text_is_one_cp1252_byte_not_utf8() -> None:
    sheet = model.Sheet(100, 100)
    sheet.add(model.Text(1, 1, "Plano de fabricación — Niño ×"))
    data = dxf.to_dxf(sheet)
    assert b"fabricaci\xf3n" in data and b"Ni\xf1o" in data
    assert b"\xc3\xb3" not in data and b"\xc3\xb1" not in data  # what the old UTF-8 writer produced
    assert b"\xd7" in data  # the multiplication sign is in cp1252
    assert b"fabricaci\xf3n \x97 Ni" in data  # the em dash is 0x97 in cp1252


def test_what_cp1252_cannot_hold_never_becomes_a_utf8_byte() -> None:
    sheet = model.Sheet(100, 100)
    sheet.add(model.Text(1, 1, "日本 ⌴ →"))
    data = dxf.to_dxf(sheet)
    assert data.decode("cp1252")  # decodes cleanly
    assert not re.search(rb"[\xe2\xe6\xe3][\x80-\xbf]", data)


def test_the_dxf_declares_every_role_layer_with_its_line_type() -> None:
    data = dxf.to_dxf(_sheet()).decode("cp1252").replace("\r\n", "\n").split("\n")
    pairs = list(zip(data[0::2], data[1::2], strict=False))
    layers = [
        pairs[i + 1][1].strip() for i, (code, value) in enumerate(pairs) if (code.strip(), value) == ("0", "LAYER")
    ]
    assert layers[:1] == ["0"] and set(layers) >= set(model.LAYERS)
    assert "HIDDEN" in data and "CENTER" in data and "CONTINUOUS" in data


def test_ezdxf_reads_the_dxf_and_finds_nothing_to_repair(tmp_path: Path) -> None:
    ezdxf = pytest.importorskip("ezdxf")
    path = tmp_path / "sheet.dxf"
    path.write_bytes(dxf.to_dxf(_sheet()))
    doc = ezdxf.readfile(str(path))
    auditor = doc.audit()
    assert not auditor.has_errors, [e.message for e in auditor.errors]
    assert not auditor.has_fixes
    assert doc.dxfversion == "AC1009"
    names = {layer.dxf.name for layer in doc.layers}
    assert set(model.LAYERS) <= names
    assert doc.layers.get("OCULTAS").dxf.linetype == "HIDDEN"
    assert doc.layers.get("EJES").dxf.linetype == "CENTER"
    kinds = [e.dxftype() for e in doc.modelspace()]
    for kind in ("LINE", "POLYLINE", "CIRCLE", "ARC", "TEXT", "SOLID"):
        assert kind in kinds, kind


def test_ezdxf_sees_the_entities_where_the_model_put_them(tmp_path: Path) -> None:
    ezdxf = pytest.importorskip("ezdxf")
    path = tmp_path / "sheet.dxf"
    path.write_bytes(dxf.to_dxf(_sheet()))
    space = ezdxf.readfile(str(path)).modelspace()
    circle = space.query("CIRCLE").first
    assert (circle.dxf.center.x, circle.dxf.center.y, circle.dxf.radius) == (30.0, 60.0, 4.0)
    assert circle.dxf.layer == "CONTORNO"
    arc = space.query("ARC").first
    assert (arc.dxf.start_angle, arc.dxf.end_angle, arc.dxf.layer) == (30.0, 120.0, "COTAS")
    texts = {t.dxf.text: t for t in space.query("TEXT")}
    assert texts["%%c8 pasante %%p0,1 90%%d"].plain_text() == "Ø8 pasante ±0,1 90°"
    centred = texts["centrado"]
    assert centred.dxf.halign == 1 and (centred.dxf.align_point.x, centred.dxf.align_point.y) == (100.0, 100.0)
    assert texts["derecha"].dxf.rotation == 90.0 and texts["derecha"].dxf.halign == 2
    closed = [p for p in space.query("POLYLINE") if p.is_closed]
    assert len(closed) == 1 and len(list(closed[0].vertices)) == 4 and closed[0].dxf.layer == "OCULTAS"


def test_a_quad_solid_is_written_in_the_order_dxf_wants(tmp_path: Path) -> None:
    ezdxf = pytest.importorskip("ezdxf")
    sheet = model.Sheet(100, 100)
    sheet.add(model.Solid(((0, 0), (10, 0), (10, 10), (0, 10)), model.DIMENSIONS))
    path = tmp_path / "s.dxf"
    path.write_bytes(dxf.to_dxf(sheet))
    solid = ezdxf.readfile(str(path)).modelspace().query("SOLID").first
    # the corners of a SOLID are stored 1, 2, 4, 3: vtx2 is the opposite corner of vtx0 in the perimeter order
    corners = [(v.x, v.y) for v in solid.vertices()]
    assert corners[:2] == [(0.0, 0.0), (10.0, 0.0)] and (10.0, 10.0) in corners and (0.0, 10.0) in corners


def test_an_empty_sheet_is_still_a_valid_dxf(tmp_path: Path) -> None:
    ezdxf = pytest.importorskip("ezdxf")
    path = tmp_path / "empty.dxf"
    path.write_bytes(dxf.to_dxf(model.Sheet(297.0, 210.0)))
    doc = ezdxf.readfile(str(path))
    assert not doc.audit().has_errors and len(doc.modelspace()) == 0
