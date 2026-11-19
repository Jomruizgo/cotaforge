# ruff: noqa: E501
"""The process route of a feature and the size each stage gets on the sheet: the intent file declares the route, the requirement
holds the export until every stage has its dimension, and the harness only PROPOSES the stock of each stage (the engineer
confirms the numbers). The routes are ones a shop uses: a round diameter and a thickness are turned with a few tenths of
allowance and then ground; the profile of a toothed ring that turning cannot make is wire cut from a turned blank."""

from types import SimpleNamespace

from cotaforge_mcp import drawing_requirements as dr
from cotaforge_mcp import process_stages as ps
from tests.unit.test_drawing_requirements import PLATE

ROW = "req.process_stages"
ROUTE = "## Process\n- Outside diameter: turned Ø60,3 -> ground\n- Thickness: turned 12,3 -> ground\n"
RING = dr.parse_intent("# Ring\n\n" + ROUTE, "ring-intent.md")
TOOTHED = dr.parse_intent("# Toothed ring\n\n## Process\n- Outer profile: turned Ø66,5 -> wire cut\n", "toothed-intent.md")


def dim(id: str, stage: str, size_mm: float | None = None, face: str = "face:1") -> SimpleNamespace:
    spec = {"id": id, "type": "dim", "kind": "diameter", "face": [face], "stage": stage, **({"size_mm": size_mm} if size_mm else {})}
    return SimpleNamespace(id=id, type="dim", spec=spec)


def row(intent: dr.Intent, items: tuple[object, ...] = ()) -> dr.Requirement | None:
    found = {r.id: r for r in dr.evaluate(PLATE, (), {}, intent, (), None, (), (), items)}
    return found.get(ROW)


def test_a_route_line_names_the_feature_the_stages_in_order_and_the_sizes_it_gives() -> None:
    diameter, thickness = ps.declared(ROUTE)
    assert diameter.feature == "Outside diameter"
    assert [(s.process, s.size_mm) for s in diameter.stages] == [("turn", 60.3), ("grind", None)]
    assert [(s.process, s.size_mm) for s in thickness.stages] == [("turn", 12.3), ("grind", None)]
    (profile,) = ps.declared("- Outer profile: turned Ø66,5 -> wire cut")
    assert [(s.process, s.size_mm) for s in profile.stages] == [("turn", 66.5), ("wire_cut", None)]
    (english,) = ps.declared("- Width: turned -> ground 20 mm")
    assert [s.process for s in english.stages] == ["turn", "grind"] and english.stages[1].size_mm == 20.0


def test_one_process_is_no_route_and_a_line_with_no_arrow_is_not_one() -> None:
    assert ps.declared("- Milled on the CNC") == []
    assert ps.declared("- Outside diameter: ground") == []
    assert ps.declared("- Turned and ground") == []  # two processes named, no order given: the drafter asks


def test_a_declared_route_with_no_stage_dimension_blocks_the_export_and_says_what_to_add() -> None:
    req = row(RING)
    assert req is not None and req.state == dr.MISSING and req.measured and not req.closed
    assert len(req.lacking) == 4  # two stages of two routes
    text = " | ".join(req.lacking)
    assert "«torneado»" in text and "size_mm=60.3" in text and "size_mm=12.3" in text  # the sizes the intent gives are the ones to draw
    assert "«rectificado»" in text
    assert "final size is the one the model measures" in text  # the last stage takes no size_mm


def test_a_stage_the_intent_gives_no_size_for_is_a_proposal_with_the_usual_stock_never_a_decision() -> None:
    req = row(dr.parse_intent("## Process\n- Width: turned -> ground\n"))
    assert req is not None
    text = " | ".join(req.lacking)
    assert "the engineer confirms it" in text and "0.2 to 0.4 mm" in text  # a few tenths before grinding is a proposal
    blank = row(dr.parse_intent("## Process\n- Outer profile: turned -> wire cut\n"))
    assert blank is not None and "tip diameter of the profile" in " | ".join(blank.lacking)


def test_every_stage_with_its_dimension_meets_the_row() -> None:
    items = (dim("s1", "torneado", 60.3), dim("s2", "rectificado"), dim("s3", "torneado", 12.3, "face:9"), dim("s4", "rectificado", None, "face:9"))
    req = row(RING, items)
    assert req is not None and req.state == dr.MET and req.closed and not req.lacking
    blank = row(TOOTHED, (dim("p1", "torneado", 66.5), dim("p2", "electroerosión")))
    assert blank is not None and blank.state == dr.MET  # the wire cut is the last stage: the model's own profile size


def test_a_stage_size_that_disagrees_with_the_intent_file_is_flagged_and_the_final_size_takes_none() -> None:
    wrong = (dim("s1", "torneado", 60.9), dim("s2", "rectificado", 60.0), dim("s3", "torneado", 12.3), dim("s4", "rectificado"))
    req = row(RING, wrong)
    assert req is not None and req.state == dr.MISSING
    assert any("s1 states 60.9" in problem for problem in req.lacking)
    assert any("final size" in problem and "s2" in problem for problem in req.lacking)

def test_the_labels_follow_the_language_of_the_sheet() -> None:
    assert ps.label("grind", "es") == "rectificado" and ps.label("grind", "en") == "ground"
    assert ps.process_of("Electroerosión por hilo") == "wire_cut" and ps.process_of("wire cut") == "wire_cut"


def test_a_part_that_names_several_processes_but_no_route_is_asked_for_it() -> None:
    req = row(dr.parse_intent("## Process\n- Turned and ground, quantity 20\n"))
    assert req is not None and req.state == dr.NEEDS_ANSWER and not req.closed
    assert "route" in dr.question_of(req, "en").lower() or "ruta" in dr.question_of(req, "es").lower()


def test_a_part_with_one_process_or_nothing_to_size_or_no_intent_file_is_not_asked() -> None:
    assert row(dr.parse_intent("## Process\n- Torneado y fresado, cantidad 10\n")) is None  # no process there leaves stock to size
    assert row(dr.parse_intent("## Function\n- A ring\n")) is None  # the process section is silent: the plan reminds, nothing blocks
    assert row(dr.parse_intent("## Process\n- Milled on a CNC\n")) is None
    assert row(dr.Intent()) is None  # nothing is declared yet: the plan cannot know the part passes several processes
