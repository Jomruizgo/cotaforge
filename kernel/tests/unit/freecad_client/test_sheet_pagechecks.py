# ruff: noqa: E501
"""What a shop reader finds wrong on the page of a sheet (``sheet_pagechecks``, and the review checks that use it): proof text on
the sheet, a cell wider than its column, balloons that do not match the list or have no leader, a mark that runs over itself, a
view label nearer to another view, a title-block text over the next field. Plain data, no FreeCAD."""

import math
import sys
from pathlib import Path

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import items_mould as mould  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_pagechecks as pc  # noqa: E402
import sheet_review as review  # noqa: E402
import sheet_template as tpl  # noqa: E402
import sheet_template_gen as gen  # noqa: E402

from .test_sheet_mould import context  # noqa: E402

# --- proof text -----------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["Pos. editada", "test", "TODO revisar", "lorem ipsum", "XXX", "[TEST] a4", "asdf", "edited by hand"])
def test_a_proof_or_placeholder_text_is_a_marker(text: str) -> None:
    assert pc.marker_texts([pc.PageText("cell A1 of cut list", text)])


@pytest.mark.parametrize("text", ["Pos.", "Tubo 40×40×3", "Proceso MAG, ISO 5817-C", "Edición A", "a3", "ISO 2553, 4×", "", "Prueba hidrostática 6 bar"])
def test_the_texts_a_sheet_really_holds_are_not_markers(text: str) -> None:
    assert not pc.marker_texts([pc.PageText("cell A1 of cut list", text)])


def test_the_marker_finding_names_where_the_text_is() -> None:
    found = pc.marker_texts([pc.PageText("a", "ok"), pc.PageText("cell A1 of Cut", "Pos. editada")])
    assert [t.where for t in found] == ["cell A1 of Cut"]


# --- cells ----------------------------------------------------------------------------------------------------------------------


def test_a_cell_text_wider_than_its_column_is_found() -> None:
    narrow = pc.PageCell("cell A1", "Pos. editada", 8.0, 3.0, "A")
    fits = pc.PageCell("cell B1", "Chapa 6", 40.0, 3.0, "B")
    found = pc.cell_overflows([narrow, fits])
    assert [c.where for c, _ in found] == ["cell A1"] and found[0][1] > 8.0


# --- balloons -------------------------------------------------------------------------------------------------------------------


def _balloon(text: str, bubble: tuple[float, float], origin: tuple[float, float], scale: float = 0.2) -> pc.PageBalloon:
    return pc.PageBalloon(f"B{text}", text, "isometric", scale, bubble, origin)


def test_the_leader_is_measured_in_page_mm_past_the_circle() -> None:
    far = _balloon("1", (100.0, 0.0), (0.0, 0.0))  # 100 view units at 1:5 = 20 mm, 3.5 mm of it the circle
    assert math.isclose(pc.leader_mm(far), 16.5)
    on_the_dot = _balloon("2", (4.0, 3.0), (4.0, 3.0))  # the page mm taken for view units: a leader of no length
    assert pc.leader_mm(on_the_dot) == 0.0


def test_a_balloon_with_no_leader_that_shows_is_a_problem_and_a_long_one_is_not() -> None:
    found = pc.balloon_problems([_balloon("1", (100.0, 0.0), (0.0, 0.0)), _balloon("2", (5.0, 0.0), (5.0, 1.0))], ["1", "2"])
    assert [t for t, _ in found.no_leader] == ["2"] and not found.no_balloon and not found.no_row


def test_every_row_has_one_balloon_and_every_balloon_one_row() -> None:
    balloons = [_balloon("1", (100.0, 0.0), (0.0, 0.0)), _balloon("1", (90.0, 20.0), (0.0, 0.0)), _balloon("9", (100.0, 50.0), (0.0, 0.0))]
    found = pc.balloon_problems(balloons, ["1", "2", "3"])
    assert found.no_balloon == ["2", "3"] and found.no_row == ["9"] and found.twice == ["1"]


def test_a_matching_page_is_clean() -> None:
    found = pc.balloon_problems([_balloon("1", (100.0, 0.0), (0.0, 0.0)), _balloon("2", (0.0, 100.0), (0.0, 0.0))], ["2", "1"], {"B1": True, "B2": True})
    assert not found


def test_a_balloon_whose_dot_is_off_its_part_is_a_problem_and_an_unknown_one_is_not() -> None:
    balloons = [_balloon("1", (100.0, 0.0), (0.0, 0.0)), _balloon("2", (0.0, 100.0), (0.0, 0.0))]
    found = pc.balloon_problems(balloons, ["1", "2"], {"B1": False, "B2": None})
    assert found.off_part == ["1"]


# --- a mark that runs over itself ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("direction", [(0.0, 1.0), (0.0, -1.0), (1.0, 0.0), (-1.0, 0.0), (0.7071, 0.7071), (-0.7071, 0.7071)])
def test_the_opening_arrow_never_prints_its_label_or_its_draft_over_itself(direction: tuple[float, float]) -> None:
    entities = mould._pull_symbol(context(), (100.0, 100.0), direction, 1.5)
    assert any(isinstance(e, model.Text) and e.text.startswith("1") for e in entities)  # the draft value
    assert pc.self_overlaps(entities) == []


def test_two_texts_of_one_mark_on_top_of_each_other_are_found() -> None:
    """The opening label and the draft value of the old layout printed over each other (``Apertl1,5°a`` on the sheet)."""
    label = model.Text(100.0, 108.0, "Apertura", 3.5, model.TEXT, "left")
    value = model.Text(112.0, 108.4, "1,5°", 3.5, model.TEXT, "left")
    assert sorted(pc.self_overlaps([label, value])) == ["1,5°", "Apertura"]


def test_a_label_printed_over_its_shaft_is_found() -> None:
    shaft = model.Line(100.0, 100.0, 100.0, 116.0, model.DIMENSIONS)
    label = model.Text(97.5, 108.0, "Apertura", 3.5, model.TEXT, "left")
    assert pc.self_overlaps([shaft, label]) == ["Apertura"]


def test_a_text_that_only_touches_a_line_is_not_crossed() -> None:
    assert pc.self_overlaps([model.Line(0.0, 0.0, 0.0, 10.0, model.DIMENSIONS), model.Text(0.2, 2.0, "A", 3.5, model.TEXT, "left")]) == []


def test_the_review_says_which_mould_mark_runs_over_itself() -> None:
    import sheet_units as su  # noqa: PLC0415

    ctx = context()
    shaft = model.Line(100.0, 100.0, 100.0, 116.0, model.DIMENSIONS)
    label = model.Text(97.5, 108.0, "Apertura", 3.5, model.TEXT, "left")
    ctx.extras["units"] = [su.Unit("k1", "k1 pull_direction", su.KIND_ITEM, (shaft, label), "pull_direction")]
    data = review.ReviewInput([], {}, ctx, ctx.sheet)
    found = review._marks_run_over_themselves(data)
    assert [f.code for f in found] == ["mark_overlaps_itself"] and "Apertura" in found[0].message


# --- labels under their views -----------------------------------------------------------------------------------------------------


def test_a_label_nearer_to_another_view_is_found_and_one_under_its_own_view_is_not() -> None:
    views = {"isometric": (250.0, 100.0, 350.0, 180.0), "detail B": (270.0, 70.0, 330.0, 88.0)}
    under_detail = (285.0, 91.0, 315.0, 94.5)  # 5.5 mm under the isometric view, 3 mm over the detail
    assert pc.label_problems({"isometric": under_detail}, views) == [("isometric", "detail B", 5.5, 3.0)]
    snug = (285.0, 96.0, 315.0, 99.5)
    assert pc.label_problems({"isometric": snug}, views) == []


def test_the_review_reports_an_ambiguous_label() -> None:
    ctx = context()
    top = ctx.views["top"].box
    ctx.extras["view_labels"] = {"front": (top[0] + 5.0, top[1] + 5.0, top[0] + 30.0, top[1] + 8.5)}  # inside the top view
    found = review._view_labels(review.ReviewInput([], {}, ctx, ctx.sheet))
    assert [f.code for f in found] == ["view_label_ambiguous"] and "front" in found[0].message


# --- the title block ----------------------------------------------------------------------------------------------------------------

MATERIAL = "PC/ABS UL 94 V-0 (Covestro Bayblend FR3010 o equivalente)"
A3 = gen.build("A3", "es", "first")


def test_a_long_material_does_not_fit_one_line_of_its_field() -> None:
    assert [name for name, _, _ in tpl.overflowing(A3, {"part_material": MATERIAL})] == ["part_material"]


def test_a_long_material_goes_on_the_two_lines_of_its_field_and_then_fits() -> None:
    names = tpl.editable_fields(A3)
    wrapped = tpl.wrap_fields({"part_material": MATERIAL, "general_tolerances": "ISO 2768-mK"}, names)
    assert wrapped["part_material"] == "" and wrapped["part_material_1"] and wrapped["part_material_2"]
    assert " ".join([wrapped["part_material_1"], wrapped["part_material_2"]]) == MATERIAL
    assert wrapped["general_tolerances"] == "ISO 2768-mK"
    assert tpl.overflowing(A3, wrapped) == []


def test_a_short_value_stays_on_its_one_line() -> None:
    names = tpl.editable_fields(A3)
    assert tpl.wrap_fields({"part_material": "S235JR"}, names) == {"part_material": "S235JR"}


def test_a_value_too_long_even_for_two_lines_is_left_for_the_review_to_say() -> None:
    names = tpl.editable_fields(A3)
    huge = " ".join(["aleacion-de-aluminio-endurecida"] * 6)
    wrapped = tpl.wrap_fields({"part_material": huge}, names)
    assert wrapped["part_material"] == huge  # not cut: nothing is thrown away
    assert [name for name, _, _ in tpl.overflowing(A3, wrapped)] == ["part_material"]


def test_a_template_without_the_two_lines_keeps_the_value_whole() -> None:
    names = [n for n in tpl.editable_fields(A3) if n not in tpl.continuation_fields()]
    assert tpl.wrap_fields({"part_material": MATERIAL}, names) == {"part_material": MATERIAL}


def test_the_two_lines_are_in_both_shipped_templates_and_nobody_sets_them_by_hand() -> None:
    for size in ("A3", "A4"):
        shipped = (WORKER_DIR / "templates" / f"{size}_es.svg").read_text(encoding="utf-8")
        assert tpl.continuation_fields() <= set(tpl.editable_fields(shipped))
        assert tpl.overflowing(shipped, tpl.wrap_fields({"part_material": MATERIAL}, tpl.editable_fields(shipped))) == []


def test_the_review_reports_a_title_block_field_that_overflows() -> None:
    ctx = context()
    ctx.extras["title_overflow"] = [("part_material", 117.6, 78.0)]
    found = review._title_block_overflow(review.ReviewInput([], {}, ctx, ctx.sheet))
    assert [f.code for f in found] == ["title_block_overflow"] and "part_material" in found[0].message


# --- the weld symbol against its line ----------------------------------------------------------------------------------------------


def test_a_weld_symbol_pulled_off_its_reference_line_is_found_and_one_on_it_is_not() -> None:
    measured = [("a3", 0.0, 3.8), ("40", 0.0, 0.0), ("z4", 0.2, -0.2)]
    assert pc.weld_offsets(measured) == [("a3", 3.8)]


# --- reading the page -----------------------------------------------------------------------------------------------------------------


class _Obj:
    def __init__(self, type_id: str, name: str, **props: object) -> None:
        self.TypeId, self.Name, self.Label = type_id, name, name
        self.__dict__.update(props)


class _Sheet(_Obj):
    def __init__(self, cells: dict[str, str], widths: dict[str, float]) -> None:
        super().__init__("Spreadsheet::Sheet", "Cells")
        self._cells, self._widths = cells, widths

    def getNonEmptyCells(self) -> list[str]:  # noqa: N802 - FreeCAD's name
        return list(self._cells)

    def getContents(self, address: str) -> str:  # noqa: N802
        return self._cells[address]

    def getColumnWidth(self, column: str) -> float:  # noqa: N802
        return self._widths[column]


class _View(_Obj):
    def getScale(self) -> float:  # noqa: N802
        return 0.2


def test_the_page_is_read_into_texts_cells_balloons_and_the_positions_of_the_list() -> None:
    iso = _View("TechDraw::DrawViewPart", "Iso")
    cells = _Sheet({"A1": "'2", "B1": "'Chapa 6", "A2": "'1", "B2": "'Pos. editada"}, {"A": 30.0, "B": 150.0})
    table = _Obj("TechDraw::DrawViewSpreadsheet", "Table", Source=cells, TextSize=12.0)
    balloon = _Obj("TechDraw::DrawViewBalloon", "B1", Text="2", SourceView=iso, X=100.0, Y=50.0, OriginX=0.0, OriginY=0.0)
    tail = _Obj("TechDraw::DrawWeldSymbol", "W", TailText="4×")
    parent = _Obj("TechDraw::DrawWeldSymbol", "W", Label="W")
    tile = _Obj("TechDraw::DrawTileWeld", "T", TileParent=parent, LeftText="a3", RightText="40", CenterText="")
    note = _Obj("TechDraw::DrawViewAnnotation", "N", Text=["Notas", "1. MAG"])
    objects = [iso, cells, table, balloon, tail, tile, note]
    page = _Obj("TechDraw::DrawPage", "Page", Views=[o for o in objects if o is not cells and o is not tile], Document=_Obj("", "Doc", Objects=objects))
    read = pc.read_page(page, lambda view: "isometric")
    assert read.positions == ["2", "1"] and read.tile_texts == ["a3", "40"]
    assert [b.text for b in read.balloons] == ["2"] and read.balloons[0].view == "isometric" and read.balloons[0].scale == 0.2
    assert [t.text for t in pc.marker_texts(read.texts)] == ["Pos. editada"]
    assert {t.text for t in read.texts} >= {"Notas", "1. MAG", "4×", "a3", "40", "Chapa 6"}
    assert pc.cell_overflows(read.cells) == []  # the columns hold their texts


# --- the standard of the weld size --------------------------------------------------------------------------------------------------


def test_a_sheet_with_welds_names_the_standard_the_size_is_read_by() -> None:
    import sheet_context as sc  # noqa: PLC0415
    import sheet_weldreview as wr  # noqa: PLC0415

    ctx = context()
    items = [{"type": "weld", "id": "w1"}]
    done = {"w1": sc.ItemResult("w1", "weld")}
    found = wr.standard_findings(review.ReviewInput(items, done, ctx, ctx.sheet))
    assert [f.code for f in found] == ["weld_standard_unstated"] and found[0].items == ("w1",)
    ctx.sheet.add(model.Text(20.0, 20.0, "Símbolos de soldadura según ISO 2553: a = garganta", 3.5, model.TEXT, "left"))
    assert wr.standard_findings(review.ReviewInput(items, done, ctx, ctx.sheet)) == []
    assert wr.standard_findings(review.ReviewInput([], {}, context(), context().sheet)) == []  # no weld, nothing to say
