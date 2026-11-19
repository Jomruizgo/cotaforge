# ruff: noqa: E501
"""The functional dimensions the intent file declares must be on the sheet, given directly (ISO 129-1), or the engineer is
asked which they are. Two plates held apart by spacers: the gap between the plates is the spacer length."""

from typing import Any

from cotaforge.contract import DrawingComponent, DrawingMeasure

from cotaforge_mcp import drawing_requirements as dr
from tests.unit.test_drawing_requirements import PLATE

ROW = "req.functional_dimensions"
ASSEMBLY = {**PLATE, "part": 0, "assembly": 1}
INTENT = dr.parse_intent(
    "# Assembly\n\n## Functional relations\n- Gap between the base plate and the top plate: 18 mm, critical\n", "clamp-intent.md"
)
TOLERANCED = dr.parse_intent(
    "## Functional relations\n- Gap between the plates: 18 mm ±0.1, critical\n", "clamp-intent.md"
)


def measure(id: str, value: float, toleranced: bool = False, kind: str = "distance") -> DrawingMeasure:
    return DrawingMeasure(id=id, kind=kind, value_mm=value, toleranced=toleranced)  # type: ignore[arg-type]


def row(intent: dr.Intent, measures: tuple[Any, ...] = (), components: tuple[Any, ...] = (), facts: dict[str, int] | None = None) -> dr.Requirement:
    found = {r.id: r for r in dr.evaluate(facts or ASSEMBLY, (), {}, intent, (), None, measures, components)}
    return found[ROW]


# the overall height 36 and the two plates of 10 and 8 mm: the gap is only the difference
CHAIN = (measure("overall_h", 36.0, kind="overall"), measure("t_base", 10.0), measure("t_top", 8.0))


def test_a_declared_gap_with_no_spacer_length_on_the_sheet_blocks_the_export() -> None:
    req = row(INTENT, (measure("width", 120.0),))
    assert req.state == dr.MISSING and not req.closed and req.measured
    assert "Gap between the base plate and the top plate" in req.lacking[0] and "18 mm" in req.lacking[0]
    assert 'drawing(action="add"' in req.lacking[0]  # it names the command that adds it


def test_the_same_gap_given_directly_passes() -> None:
    req = row(INTENT, (measure("width", 120.0), measure("spacer_len", 18.0)))
    assert req.state == dr.MET and req.closed and not req.lacking


def test_a_gap_that_only_comes_out_of_a_chain_is_blocked_as_a_chain() -> None:
    measures = (measure("overall_h", 40.0, kind="overall"), measure("t_base", 10.0), measure("t_top", 8.0))
    req = row(dr.parse_intent("## Functional relations\n- Gap between the plates: 22 mm, critical\n"), measures)
    assert req.state == dr.MISSING
    assert "only comes out as a chain (40 - 10 - 8)" in req.lacking[0] and "given directly" in req.lacking[0]
    assert not req.closed


def test_a_chain_is_told_from_a_value_nothing_adds_up_to() -> None:
    req = row(INTENT, CHAIN)  # 36 - 10 - 8 = 18 as well
    assert "chain" in req.lacking[0]
    plain = row(INTENT, (measure("width", 120.0), measure("t", 7.0)))
    assert "chain" not in plain.lacking[0] and "no dimension of the sheet gives 18 mm" in plain.lacking[0]


def test_no_intent_is_a_decision_question_that_blocks_the_export() -> None:
    req = row(dr.Intent(), (measure("spacer_len", 18.0),))
    assert req.state == dr.NEEDS_ANSWER and not req.closed
    assert "¿De qué medidas depende la función" in dr.question_of(req, "es")
    assert not req.measured  # nobody declared anything: "does not apply: all free" is an answer the engineer can give


def test_an_intent_that_names_a_dimension_without_a_value_asks_for_it() -> None:
    silent = dr.parse_intent("## Functional relations\n- The gap between the plates is critical\n")
    req = row(silent)
    assert req.state == dr.MISSING and "gives no value" in req.lacking[0]


def test_the_declared_tolerance_must_be_on_the_dimension_that_gives_it() -> None:
    bare = row(TOLERANCED, (measure("spacer_len", 18.0),))
    assert bare.state == dr.MISSING and "not its tolerance «±0.1»" in bare.lacking[0] and "spacer_len" in bare.lacking[0]
    assert row(TOLERANCED, (measure("spacer_len", 18.0, toleranced=True),)).state == dr.MET


def test_a_fit_class_in_the_intent_is_the_tolerance_asked() -> None:
    fit = dr.parse_intent("## Fits\n- Critical: pin bore Ø12 H7 against the dowel\n")
    assert row(fit, (measure("bore", 12.0, kind="hole"),)).state == dr.MISSING
    assert row(fit, (measure("bore", 12.0, True, kind="hole"),)).state == dr.MET


def test_in_an_assembly_the_detail_sheet_of_the_part_carries_it_if_the_parts_list_cites_the_sheet() -> None:
    intent = dr.parse_intent("## Functional relations\n- Spacer length 18 mm, critical (gap between the plates)\n")
    uncited = (DrawingComponent(name="Spacer", reference=""),)
    cited = (DrawingComponent(name="Spacer", reference="PL-102"),)
    blocked = row(intent, (measure("width", 120.0),), uncited)
    assert blocked.state == dr.MISSING and "prints no drawing number" in blocked.lacking[0]
    assert row(intent, (measure("width", 120.0),), cited).state == dr.MET


def test_the_table_knows_no_part_type() -> None:
    row_ = next(r for r in dr.ROWS if r.id == ROW)
    for word in ("plate", "spacer", "shaft", "bolt", "gear"):
        assert word not in row_.intent_pattern.lower()
