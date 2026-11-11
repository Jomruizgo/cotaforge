# ruff: noqa: E501
"""The pieces the window mirrors (``sheet_units``), the SVG fragment a symbol is made of and the tap-drill rule: plain data,
no document needed."""

import xml.etree.ElementTree as ET

import sheet_model as model
import sheet_svg
import sheet_thread
import sheet_units as su


def check_a_unit_has_the_box_of_its_entities_and_its_centre_is_the_centre_of_that_box_plus_margin() -> None:
    entities = (
        model.Line(10.0, 20.0, 50.0, 20.0, model.DIMENSIONS),
        model.Text(30.0, 22.0, "40", 3.5, model.DIMENSIONS, "center"),
    )
    unit = su.Unit("d1", "d1 dim", su.KIND_ITEM, entities, "dim")
    x0, y0, x1, y1 = unit.rect
    assert x0 <= 10.0 and x1 >= 50.0 and y0 <= 20.0 and y1 >= 22.0 + 3.5  # the text is inside, whatever the font
    cx, cy = unit.centre
    px0, py0, px1, py1 = su.padded(unit.rect)
    assert abs(cx - (px0 + px1) / 2) < 1e-9 and abs(cy - (py0 + py1) / 2) < 1e-9
    assert unit.texts == ["40"]


def check_the_svg_fragment_covers_the_box_and_keeps_the_texts_and_the_weights() -> None:
    entities = (
        model.Line(10.0, 20.0, 50.0, 20.0, model.DIMENSIONS),
        model.Line(10.0, 30.0, 50.0, 30.0, model.CONTOUR),
        model.Text(30.0, 22.0, "Ø8 pasante", 3.5, model.TEXT, "center"),
    )
    unit = su.Unit("h1", "h1 hole_dim", su.KIND_ITEM, entities, "hole_dim")
    rect = su.padded(unit.rect)
    svg = sheet_svg.fragment(unit.entities, 297.0, rect, unit.label)
    root = ET.fromstring(svg.encode("utf-8"))
    x, y, w, h = (float(v) for v in root.attrib["viewBox"].split())
    assert abs(x - rect[0]) < 1e-3 and abs(w - (rect[2] - rect[0])) < 1e-3 and abs(h - (rect[3] - rect[1])) < 1e-3
    assert abs(y - (297.0 - rect[3])) < 1e-3  # page y runs down in an SVG
    assert root.attrib["width"].endswith("mm")
    assert f'stroke-width="{model.THICK_MM:g}"' in svg and f'stroke-width="{model.THIN_MM:g}"' in svg
    assert "Ø8 pasante" in svg


def check_the_dxf_colours_map_the_iso_128_weights() -> None:
    layers = model.LAYERS
    assert layers[model.CONTOUR].width_mm >= 1.8 * layers[model.DIMENSIONS].width_mm
    assert layers[model.CONTOUR].color == 7 and layers[model.DIMENSIONS].color == 3
    assert layers[model.HATCH].width_mm < layers[model.DIMENSIONS].width_mm
    pens = {}
    for layer in layers.values():
        pens.setdefault(layer.color, set()).add(layer.width_mm)
    assert all(len(widths) == 1 for color, widths in pens.items() if color != 7), pens  # one colour, one pen


def check_only_a_tap_drill_diameter_can_be_a_thread() -> None:
    assert sheet_thread.tap_drill_thread(5.0) == "M6" and sheet_thread.tap_drill_thread(6.8) == "M8"
    assert sheet_thread.tap_drill_thread(8.5) == "M10"
    for diameter in (6.0, 9.0, 8.0, 11.0, 3.0, 10.0):  # dowels, clearance holes
        assert sheet_thread.tap_drill_thread(diameter) is None, diameter
