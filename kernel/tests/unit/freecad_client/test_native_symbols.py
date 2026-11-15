# ruff: noqa: E501
"""The datums, frames, finish symbols and hole table as the software's own TechDraw objects, asked for by the items (no FreeCAD).

An item says WHAT it asks of TechDraw (``sheet_units.NativeSymbol`` / ``NativeTable``): the file of the symbol library, the values of
its editable texts, where the origin of the drawing lands and the leader that joins it to the feature. These tests check what the
items ask for, that what the headless sheet draws is the same drawing as the library's file (the same widths and the same
triangle), and the review's findings for what is left of the harness's own drawing. The painted result in the real FreeCAD is
``tests/integration/freecad/test_visible_native_gdt.py``.
"""

import sys
from pathlib import Path

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import gdt_frames as gf  # noqa: E402
import items_gdt  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_symbols  # noqa: E402
import sheet_units as su  # noqa: E402

from .test_sheet_items import draw as draw_items  # noqa: E402
from .test_sheet_items import make_context as items_context  # noqa: E402
from .test_sheet_tolerances import BORE, DATUM_A, draw, make_context  # noqa: E402


def natives_of(ctx: object, item: str) -> list[su.Native]:
    return list(ctx.extras.get("natives", {}).get(item, []))  # type: ignore[attr-defined]


def symbols_of(ctx: object, item: str) -> list[su.NativeSymbol]:
    return [n for n in natives_of(ctx, item) if isinstance(n, su.NativeSymbol)]


# --- what the units say about themselves ---------------------------------------------------------------------------------------


def test_a_symbol_and_its_leader_are_two_objects_and_a_symbol_on_the_feature_is_one() -> None:
    frame = su.NativeSymbol("g", "f", "frame", "flatness/flatness-0-datums.svg", ("0,05",), (10.0, 20.0), "front", ((50.0, 30.0), (10.0, 13.5)))
    datum = su.NativeSymbol("d", "s", "datum", "datum/datum-down.svg", ("A",), (50.0, 30.0), "front")
    table = su.NativeTable("t", "t", (("N", "X"), ("1", "10")), (9.0, 18.0), (100.0, 100.0), "TABLA")
    assert su.object_count(su.Unit("g", "g", su.KIND_ITEM, (), natives=(frame,))) == 2
    assert su.object_count(su.Unit("d", "d", su.KIND_ITEM, (), natives=(datum,))) == 1
    assert su.object_count(su.Unit("t", "t", su.KIND_ITEM, (), natives=(table,))) == 1  # the spreadsheet it shows is not a view of the page


def test_what_the_engineer_would_type_in_a_value_is_part_of_the_signature() -> None:
    def unit(text: str) -> su.Unit:
        symbol = su.NativeSymbol("g", "f", "frame", "flatness/flatness-0-datums.svg", (text,), (10.0, 20.0), "front")
        return su.Unit("g", "g", su.KIND_ITEM, (), natives=(symbol,))

    assert su.signature(unit("0,05")) != su.signature(unit("0,08"))  # a changed requirement remakes the symbol, an unchanged one leaves it alone
    assert su.signature(unit("0,05")) == su.signature(unit("0,05"))


def test_the_native_symbols_and_tables_take_the_drawing_of_their_item_with_them() -> None:
    entities = (model.Text(10.0, 10.0, "A", 3.5, model.DIMENSIONS, "center"), model.Line(0.0, 0.0, 5.0, 5.0, model.DIMENSIONS))
    symbol = su.NativeSymbol("d", "s", "datum", "datum/datum-down.svg", ("A",), (50.0, 30.0), "front")
    assert su.Unit("d", "d", su.KIND_ITEM, entities, natives=(symbol,)).residue == ()  # nothing of it is left to draw as a symbol of ours
    ruled = (model.Line(0.0, 0.0, 5.0, 0.0, model.TITLE_BLOCK), model.Text(1.0, 1.0, "N", 3.5, model.TITLE_BLOCK, "left"))
    table = su.NativeTable("t", "t", (("N",),), (9.0,), (0.0, 10.0))
    assert su.Unit("t", "t", su.KIND_ITEM, ruled, natives=(table,)).residue == ()
    assert su.Unit("t", "t", su.KIND_ITEM, ruled).residue == ruled  # without the native it is still all there


# --- the items ask for library symbols ------------------------------------------------------------------------------------------


def test_a_datum_on_a_plane_is_the_library_symbol_standing_on_its_base() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "datum", "id": "dB", "letter": "B", "face": "face:bottom"})
    assert r.status == "ok"
    (symbol,) = symbols_of(ctx, "dB")
    assert (symbol.kind, symbol.view, symbol.texts, symbol.leader) == ("datum", "front", ("B",), ())
    assert symbol.file == "datum/datum-down.svg" and symbol.file in gf.library_files()
    triangle = next(e for e in ctx.sheet.entities if isinstance(e, model.Solid))
    base = ((triangle.points[0][0] + triangle.points[1][0]) / 2, (triangle.points[0][1] + triangle.points[1][1]) / 2)
    assert symbol.at == pytest.approx(base)  # the origin of the drawing is the middle of the base ...
    assert symbol.at == pytest.approx((150.0, 150.0))  # ... on the bottom edge of the front view


def test_the_headless_datum_is_the_drawing_of_the_library_file() -> None:
    """The sheet the PDF is written from and the window the engineer sees are one drawing: same triangle, same stem, same box."""
    entities, box = items_gdt.datum_entities((0.0, 0.0), "up", "A")
    triangle = next(e for e in entities if isinstance(e, model.Solid))
    root = gf.library_files()["datum/datum-up.svg"]
    numbers = [float(n) for n in root.split('<path d="M ')[1].split('"')[0].replace("L", " ").replace("Z", " ").split()]
    from_file = sorted(zip(numbers[0::2], [-y for y in numbers[1::2]], strict=True))  # the file runs y down, the page up
    assert [v for p in sorted(triangle.points) for v in p] == pytest.approx([v for p in from_file for v in p], abs=1e-3)
    assert (box[2] - box[0], box[3] - box[1]) == pytest.approx((7.0, 7.0))
    assert box[1] == pytest.approx(5.5426 + 8.9596)  # the frame stands one triangle and one stem away from the feature


def test_a_datum_on_an_axis_is_the_symbol_on_the_shelf_of_the_callout_with_the_view_of_the_callout() -> None:
    ctx = make_context()
    bore, datum = draw(ctx, BORE, DATUM_A)
    assert bore.status == datum.status == "ok"
    (symbol,) = [s for found in ctx.extras["natives"].values() for s in found if isinstance(s, su.NativeSymbol)]
    anchor = ctx.extras["anchors"]["bore"][0]
    assert symbol.file == "datum/datum-down.svg" and symbol.at[1] == pytest.approx(anchor["elbow"][1])
    callout = next(n for n in natives_of(ctx, "bore") if isinstance(n, su.NativeDim))
    assert symbol.view == callout.view  # it follows the view its dimension is on


def test_a_frame_on_a_face_is_the_library_frame_with_a_leader_from_the_face_to_the_frame() -> None:
    ctx = make_context()
    results = draw(
        ctx,
        {"type": "datum", "id": "dA", "letter": "A", "face": "face:bottom"},
        {"type": "gdt", "id": "perp", "char": "perpendicularity", "value": 0.05, "datums": ["A"], "face": "face:side"},
    )
    assert [r.status for r in results] == ["ok", "ok"], [r.detail for r in results]
    (symbol,) = symbols_of(ctx, "perp")
    assert (symbol.kind, symbol.file, symbol.texts, symbol.view) == ("frame", "perpendicularity/perpendicularity-1-datum.svg", ("0,05", "A"), "top")
    tip, anchor = symbol.leader
    side = ctx.geometry.face("face:side")
    assert abs(tip[0] - 150.0) < 100 and symbol.arrow  # the first waypoint is on the face, with the arrow
    frame_width = gf.frame_width(gf.library_files()[symbol.file])
    left, top = symbol.at  # the origin of the file is the top-left corner of the frame
    assert frame_width == pytest.approx(39.0)
    assert any(abs(anchor[0] - x) < 1e-6 for x in (left, left + frame_width)) or any(abs(anchor[1] - y) < 1e-6 for y in (top, top - 7.0))  # the leader ends on the frame
    del side


def test_the_headless_frame_has_the_width_of_the_library_file_cell_by_cell() -> None:
    """What the sheet reserves for a frame is what the window shows: the cells of the file are 7 mm, the tolerance cell, 10 mm."""
    ctx = make_context()
    draw(
        ctx,
        {"type": "datum", "id": "dA", "letter": "A", "face": "face:bottom"},
        {"type": "gdt", "id": "par", "char": "parallelism", "value": 0.02, "datums": ["A"], "face": "face:end"},
    )
    (symbol,) = symbols_of(ctx, "par")
    dividers = sorted(
        e.x1 for e in ctx.sheet.entities if isinstance(e, model.Line) and e.x1 == e.x2 and e.y2 - e.y1 == 7.0 and symbol.at[0] <= e.x1 <= symbol.at[0] + 40
    )
    assert [round(d - symbol.at[0], 3) for d in dividers] == pytest.approx([7.0, 29.0])  # symbol | tolerance (22) | datum


def test_a_long_tolerance_picks_the_wide_file_and_a_frame_the_library_cannot_make_is_an_item_error() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "datum", "id": "dA", "letter": "A", "face": "face:bottom"})
    results = draw(ctx, {"type": "gdt", "id": "p", "char": "position", "value": 0.125, "diameter": True, "modifier": "M", "datums": ["A"], "on": "bore"})
    assert results[0].status == "error"  # no callout to hang from: the item says so, no native is made
    assert natives_of(ctx, "p") == [] and r.status == "ok"


def test_a_frame_hung_from_a_callout_is_the_library_frame_with_the_view_of_the_callout() -> None:
    ctx = make_context()
    results = draw(
        ctx,
        {"type": "datum", "id": "dA", "letter": "A", "face": "face:bottom"},
        {"type": "hole_dim", "face": "face:tapped", "id": "t"},
        {"type": "gdt", "id": "pos", "char": "position", "value": 0.1, "diameter": True, "modifier": "M", "datums": ["A"], "on": "t"},
    )
    assert [r.status for r in results] == ["ok"] * 3, [r.detail for r in results]
    (symbol,) = symbols_of(ctx, "pos")
    callout = next(n for n in natives_of(ctx, "t") if isinstance(n, su.NativeDim))
    assert symbol.leader == () and symbol.view == callout.view  # it touches the shelf of the callout: no leader, and it follows the view
    assert symbol.file == "position/position-1-datum.svg" and symbol.texts == ("Ø0,1 Ⓜ", "A")
    anchor = ctx.extras["anchors"]["t"][0]
    assert symbol.at[1] == pytest.approx(anchor["elbow"][1], abs=0.1)  # its top edge is the shelf line


def test_a_finish_on_a_face_is_the_library_symbol_with_its_roughness_in_the_editable_text() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "finish", "id": "ra", "face": "face:end", "ra": 1.6, "view": "front"})
    assert r.status in ("ok", "warning")
    (symbol,) = symbols_of(ctx, "ra")
    assert (symbol.kind, symbol.file, symbol.texts, symbol.view) == ("finish", "finish/finish-required.svg", ("Ra 1,6",), "front")
    bar = [e for e in ctx.sheet.entities if isinstance(e, model.Line) and e.y1 == e.y2 and abs((e.x2 - e.x1) - gf.FINISH_BAR_MM) < 1e-6]
    assert len(bar) == 1  # the bar of the headless drawing is the bar of the file: 18 mm


def test_the_finish_forms_are_the_library_ones_and_the_general_symbol_belongs_to_the_page() -> None:
    for how, expected in (("forbidden", "finish/finish-forbidden.svg"), ("any", "finish/finish-any.svg")):
        ctx = make_context()
        draw(ctx, {"type": "finish", "id": "f", "face": "face:end", "ra": 3.2, "machining": how, "view": "front"})
        assert symbols_of(ctx, "f")[0].file == expected
    ctx = make_context()
    draw(ctx, {"type": "finish", "id": "f", "face": "face:end", "machining": "forbidden", "view": "front"})
    (bare,) = symbols_of(ctx, "f")
    assert bare.file == "finish/finish-forbidden-bare.svg" and bare.texts == ()  # no value: no bar
    ctx = make_context()
    (g,) = draw(ctx, {"type": "finish", "id": "g", "general": True, "ra": 3.2})
    (general,) = symbols_of(ctx, "g")
    assert g.status == "ok" and general.view == "" and general.file == "finish/finish-general-required.svg" and general.texts == ("Ra 3,2",)


def test_a_roughness_that_does_not_fit_the_bar_is_an_item_error() -> None:
    ctx = make_context()
    (r,) = draw(ctx, {"type": "finish", "id": "f", "face": "face:end", "ra": 999.999, "view": "front"})
    assert r.status == "error" and "too long" in r.detail


def test_the_hole_table_is_a_spreadsheet_with_a_title_a_header_and_a_row_per_hole() -> None:
    ctx = items_context()
    (r,) = draw_items(ctx, {"type": "datum_dims", "id": "tab", "mode": "table"})
    assert r.status == "ok", r.detail
    tables = [n for n in natives_of(ctx, "tab") if isinstance(n, su.NativeTable)]
    (table,) = tables
    assert table.title == "TABLA DE AGUJEROS" and table.rows[0][0] == "N" and len(table.rows) == 5  # the header and four holes
    assert [row[0] for row in table.rows[1:]] == ["1", "2", "3", "4"] and all(row[3] == "Ø6,6 pasante" for row in table.rows[1:])
    rule = [e for e in ctx.sheet.entities if isinstance(e, model.Line) and e.layer == model.TITLE_BLOCK]
    assert min(e.x1 for e in rule) == pytest.approx(table.at[0]) and max(e.x2 for e in rule) == pytest.approx(table.at[0] + sum(table.widths))
    numbers = [n for n in natives_of(ctx, "tab") if isinstance(n, su.NativeNote)]
    assert [n.lines[0] for n in numbers] == ["1", "2", "3", "4"]  # the number of each hole beside it, as TechDraw annotations


# --- the library and what is left of the harness's own drawing -------------------------------------------------------------------


def test_the_symbols_of_the_product_library_are_not_foreign_and_the_other_ones_are() -> None:
    class Symbol:
        TypeId = "TechDraw::DrawViewSymbol"

        def __init__(self, text: str) -> None:
            self.Symbol = text

    class Page:
        Views = [
            Symbol(gf.fill_editable_texts(gf.library_files()["perpendicularity/perpendicularity-1-datum.svg"], ["0,08", "B"])),
            Symbol(gf.library_files()["datum/datum-left.svg"]),
            Symbol('<svg viewBox="0 0 10 10"><path d="M 0 0 L 5 5"/></svg>'),
        ]

    foreign = sheet_symbols.foreign_symbols(Page(), "no-freecad-here")
    assert len(foreign) == 1 and "M 0 0 L 5 5" in str(foreign[0].Symbol)  # a frame with the engineer's values is the library's still


def test_the_items_that_are_native_are_the_ones_a_loose_symbol_is_a_defect_for() -> None:
    assert sheet_symbols.MIGRATED_ITEMS == {"datum", "gdt", "finish", "datum_dims"}
