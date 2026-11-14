# ruff: noqa: E501
"""The lines of a unit that TechDraw has a cosmetic edge for (``sheet_units.with_cosmetics``) and the notes that belong in the
title block (``items_notes.route_title_block_notes``): no FreeCAD."""

import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import items_notes  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_units as su  # noqa: E402

TOP = (100.0, 100.0, 200.0, 160.0)  # the page box of the geometry of a view
SECTION = (100.0, 200.0, 200.0, 260.0)


def unit_of(key: str, *entities: model.Entity, kind: str = su.KIND_ITEM) -> su.Unit:
    return su.Unit(key, key, kind, tuple(entities))


def test_a_centre_line_on_a_view_is_a_cosmetic_edge_of_that_view() -> None:
    line = model.Line(150, 95, 150, 165, model.AXES)
    (done,) = su.with_cosmetics([unit_of("vl", line)], {"top": TOP}, [])
    assert done.natives == (su.NativeCosmetic("vl", "top", "line", (150, 95, 150, 165), "centre"),)
    assert done.residue == ()  # nothing is left for a symbol: the unit is the view's lines


def test_what_has_no_cosmetic_form_stays_the_units_drawing() -> None:
    axis = model.Line(150, 95, 150, 165, model.AXES)
    text = model.Text(150, 150, "A")
    (done,) = su.with_cosmetics([unit_of("dt", axis, text)], {"top": TOP}, [])
    assert done.residue == (text,)  # no dimension or text layer is dropped because lines are cosmetic


def test_a_line_that_lies_on_no_view_stays_where_it_was() -> None:
    far = model.Line(10, 10, 20, 10, model.AXES)
    (done,) = su.with_cosmetics([unit_of("loc", far)], {"top": TOP}, [])
    assert done.natives == () and done.residue == (far,)


def test_a_line_inside_a_view_the_sheet_drew_is_not_a_cosmetic_edge_of_another() -> None:
    inside = model.Line(120, 230, 180, 230, model.AXES)
    (done,) = su.with_cosmetics([unit_of("sec", inside)], {"top": TOP}, [SECTION])
    assert done.natives == () and done.residue == (inside,)


def test_the_nearest_view_takes_a_line_that_two_boxes_reach() -> None:
    other = (210.0, 100.0, 310.0, 160.0)
    line = model.Line(204, 130, 220, 130, model.AXES)
    (done,) = su.with_cosmetics([unit_of("x", line)], {"top": TOP, "right": other}, [])
    assert [n.view for n in done.natives] == ["right"]


def test_arcs_circles_and_polylines_become_cosmetic_edges() -> None:
    arc = model.Arc(150, 130, 3.0, 90.0, 360.0, model.THREAD)
    ring = model.Circle(150, 130, 7.0, model.AXES)
    poly = model.Polyline(((120, 110), (130, 110), (130, 120)), model.PARTING)
    (done,) = su.with_cosmetics([unit_of("threads", arc, ring, poly, kind=su.KIND_EXTRA)], {"top": TOP}, [])
    kinds = [(n.kind, n.style) for n in done.natives]
    assert kinds == [("arc", "thin"), ("circle", "centre"), ("line", "parting"), ("line", "parting")]


def test_the_same_line_drawn_twice_is_one_edge_and_both_are_covered() -> None:
    a = model.Line(150, 95, 150, 165, model.AXES)
    b = model.Line(150, 95, 150, 165, model.AXES)
    (done,) = su.with_cosmetics([unit_of("vl", a, b)], {"top": TOP}, [])
    assert len(done.natives) == 1 and done.residue == ()


def test_the_views_of_an_hlr_sheet_are_left_alone() -> None:
    view = unit_of("view:front", model.Line(0, 0, 1, 1, model.AXES), kind=su.KIND_VIEW)
    assert su.with_cosmetics([view], {"top": (-5, -5, 5, 5)}, []) == [view]


def test_cosmetic_edges_are_not_part_of_the_signature_of_the_objects_of_a_unit() -> None:
    dim = su.NativeDim("h1", "d", "diameter", "top", (), (0.0, 0.0))
    cosmetic = su.NativeCosmetic("h1", "top", "line", (0, 0, 1, 1), "centre")
    plain = su.Unit("h1", "h1", su.KIND_ITEM, (), natives=(dim,))
    with_edges = su.Unit("h1", "h1", su.KIND_ITEM, (), natives=(dim, cosmetic))
    assert su.signature(plain) == su.signature(with_edges)  # adding a centre line does not remake the dimension
    assert su.object_count(with_edges) == 1
    assert su.cosmetic_signature([cosmetic]) != su.cosmetic_signature(
        [su.NativeCosmetic("h1", "top", "line", (0, 0, 2, 2), "centre")]
    )


FIELDS = {"part_material", "general_tolerances", "scale", "creator"}


def route(*texts: str, assembly: bool = False) -> tuple[list[str], list[str], dict[str, str]]:
    fields: dict[str, str] = {}
    kept, moved = items_notes.route_title_block_notes(
        [{"type": "note", "text": t} for t in texts], FIELDS, fields, assembly
    )
    return [k["text"] for k in kept], moved, fields


def test_a_note_that_states_the_material_is_the_material_field_of_the_title_block() -> None:
    kept, moved, fields = route("Material: AISI 1045, lámina 19 mm", "Rebabas eliminadas")
    assert kept == ["Rebabas eliminadas"]
    assert fields == {"part_material": "AISI 1045, lámina 19 mm"} and "part_material" in moved[0]


def test_general_tolerances_and_the_scale_have_their_field_too() -> None:
    kept, moved, fields = route("Tolerancias generales ISO 2768-mK", "Escala: 1:2")
    assert kept == [] and fields == {"general_tolerances": "ISO 2768-mK"}
    assert any("scale" in m for m in moved)


def test_a_note_on_a_view_or_at_a_place_stays_a_note() -> None:
    fields: dict[str, str] = {}
    items = [
        {"type": "note", "text": "Material: ST37", "view": "A-A"},
        {"type": "note", "text": "Material: ST37", "at_mm": [10, 10]},
    ]
    kept, moved = items_notes.route_title_block_notes(items, FIELDS, fields)
    assert kept == items and moved == [] and fields == {}


def test_a_template_without_the_field_keeps_the_note_and_an_assembly_has_no_one_material() -> None:
    fields: dict[str, str] = {}
    note = [{"type": "note", "text": "Material: ST37"}]
    assert items_notes.route_title_block_notes(note, {"creator"}, fields)[0] == note
    kept, _, got = route("Material: ST37", assembly=True)
    assert kept == ["Material: ST37"] and got == {}


def test_a_note_that_contradicts_the_title_block_stays_for_the_review_to_report() -> None:
    fields: dict[str, str] = {}
    note = [{"type": "note", "text": "Material: AISI 304"}]
    kept, moved = items_notes.route_title_block_notes(note, FIELDS, fields, current={"part_material": "AISI 1045"})
    assert kept == note and moved == [] and fields == {}  # a declared material is never overwritten


def test_a_note_that_repeats_the_title_block_is_not_added() -> None:
    fields: dict[str, str] = {}
    kept, moved = items_notes.route_title_block_notes(
        [{"type": "note", "text": "material: aisi 1045"}], FIELDS, fields, current={"part_material": "AISI 1045"}
    )
    assert kept == [] and fields == {"part_material": "aisi 1045"} and moved
