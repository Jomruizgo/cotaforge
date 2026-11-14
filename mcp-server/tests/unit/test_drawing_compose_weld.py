# ruff: noqa: E501
"""A welded part in one order: ``plan`` asks the designation of each body (with the measured section as the proposal) and the
weld of each group of joints (with the measured proposal), ``compose`` draws the cut list, the balloons, the weld symbols and a
weld note that comes from the decisions; a bigger paper keeps the scale instead of growing it."""

import json
import re
from typing import Any

import pytest
from cotaforge.contract import DrawingWeldBody, DrawingWeldJoint, DrawingWeldMember, DrawingWeldPlacement, DrawingWeldPlan
from cotaforge_mcp import drawing_compose, drawing_weld_questions
from cotaforge_mcp.config import Settings
from cotaforge_mcp.reply import ToolError
from tests.conftest import Caller, DrawingFake
from tests.unit.test_drawing_auto import bare_session, items_of
from tests.unit.test_drawing_compose import bigger_paper_fits, choose_sheet_flow, composed, na_all
from tests.unit.test_drawing_requirements import PLATE, finding

TUBES = tuple(f"body:{n}" for n in (1, 2, 3, 4))
BODIES = (
    DrawingWeldBody(position=1, instances=TUBES, reference="L 400", kind="tube", section_mm=(40.0, 40.0), wall_mm=3.0),
    DrawingWeldBody(position=2, instances=("body:5",), reference="340 × 340 × 6", kind="plate", section_mm=(6.0,)),
)
JOINTS = (
    DrawingWeldJoint(
        kind="tee", pairs=tuple((t, "body:5") for t in TUBES), length_mm=160.0, thin_mm=3.0, weld_kind="fillet", size_mm=3.0, all_around=True
    ),
)
WELD = {
    "type": "weld",
    "between": ["body:1", "body:5"],
    "also": [["body:2", "body:5"], ["body:3", "body:5"], ["body:4", "body:5"]],
    "kind": "fillet",
    "size_mm": 3,
    "size_kind": "throat",
    "sides": "arrow",
    "all_around": True,
}
ROWS = [{"component": "body:1", "name": "Tubo 40×40×3"}, {"component": "body:5", "name": "Chapa 6"}]
WELDING = {"process": "MAG", "quality": "C", "tolerance": "B"}
FRAME = {"bodies": ROWS, "welds": [WELD], "welding": WELDING}


def frame_session(settings: Settings, plan: DrawingWeldPlan | None = None) -> tuple[Caller, DrawingFake]:
    choose_sheet_flow(settings)
    fake = DrawingFake()
    fake.facts = {**PLATE, "holes": 0, "weldment": 1}
    fake.findings = (finding("weld_list_missing", "El plano de la pieza soldada no lleva lista de corte"),)
    fake.weldment = plan or DrawingWeldPlan(bodies=BODIES, joints=JOINTS)
    return bare_session(settings, fake), fake


def asked(call: Caller) -> str:
    return call.ok("drawing", action="plan").split("decisions it needs")[-1]


# --- plan ---------------------------------------------------------------------------------------------------------------------------


def test_plan_asks_the_designation_of_each_body_with_the_measured_section(settings: Settings) -> None:
    call, _ = frame_session(settings)
    text = asked(call)
    (line,) = [ln for ln in text.splitlines() if "[bodies]" in ln]
    assert "1 (body:1, x4, L 400): Tubo 40×40×3" in line and "2 (body:5, 340 × 340 × 6): Chapa 6" in line
    assert '"component":"body:1"' in line  # the shape of the answer


def test_plan_asks_the_weld_of_each_group_of_joints_with_the_measured_proposal(settings: Settings) -> None:
    call, _ = frame_session(settings)
    (line,) = [ln for ln in asked(call).splitlines() if "[welds]" in ln]
    assert "unión en T body:1+body:5, body:2+body:5, body:3+body:5, body:4+body:5 (4 iguales" in line
    assert "a=3 mm" in line and "0,7 × la pared más delgada (3 mm), con mínimo de 3 mm" in line and "todo alrededor" in line
    assert '"between":["body:1","body:5"]' in line and '"all_around":true' in line


def test_plan_asks_the_process_and_the_levels_of_the_weld(settings: Settings) -> None:
    call, _ = frame_session(settings)
    (line,) = [ln for ln in asked(call).splitlines() if "[welding]" in ln]
    assert "ISO 5817" in line and "ISO 13920" in line


def test_plan_derives_the_balloons_and_leaves_the_list_to_the_designer(settings: Settings) -> None:
    call, _ = frame_session(settings)
    text = call.ok("drawing", action="plan")
    assert "rq_balloons: a balloon per position" in text.split("decisions it needs")[0]
    assert "rq_parts_list" not in text  # the designations are his


def test_a_group_already_welded_is_not_asked_again(settings: Settings) -> None:
    joint = JOINTS[0].model_copy(update={"welded": True})
    bodies = tuple(b.model_copy(update={"named": True}) for b in BODIES)
    call, fake = frame_session(settings, DrawingWeldPlan(bodies=bodies, joints=(joint,)))
    fake.facts = {**fake.facts, "weld_class_on_sheet": 1}
    text = asked(call)
    assert "[bodies]" not in text and "[welds]" not in text and "[welding]" not in text


def test_a_part_that_is_not_welded_asks_nothing_of_the_kind(settings: Settings) -> None:
    assert drawing_weld_questions.ask(None, "es") == []


def test_the_english_questions_are_in_english(settings: Settings) -> None:
    asks = drawing_weld_questions.ask(DrawingWeldPlan(bodies=BODIES, joints=JOINTS), "en")
    text = " | ".join(a.text for a in asks)
    assert "Tube 40×40×3" in text and "Plate 6" in text and "Which weld does the T joint" in text and "0.7 x the thinner wall" in text


def test_a_solid_bar_and_a_butt_joint_get_their_own_proposals() -> None:
    bar = DrawingWeldBody(position=1, instances=("body:1",), kind="bar", section_mm=(50.0, 6.0))
    butt = DrawingWeldJoint(kind="butt", pairs=(("body:1", "body:2"),), length_mm=50.0, thin_mm=6.0, weld_kind="v", size_mm=6.0)
    asks = drawing_weld_questions.ask(DrawingWeldPlan(bodies=(bar,), joints=(butt,)), "en")
    assert "Flat bar 50×6" in asks[0].text and "V butt weld, s=6 mm (the thickness)" in asks[1].text and asks[1].joints == ("body:1+body:2",)


# --- compose ------------------------------------------------------------------------------------------------------------------------


def test_compose_refuses_a_weldment_without_the_decisions_of_its_welds(settings: Settings) -> None:
    call, fake = frame_session(settings)
    refused = call.err("drawing", action="compose", decisions={"answers": na_all(call)})
    assert "[bodies]" in refused and "[welds]" in refused and "[welding]" in refused
    assert not fake.annotated


def test_compose_draws_the_cut_list_the_balloons_the_symbols_and_the_note_from_the_decisions(settings: Settings) -> None:
    call, fake = frame_session(settings)
    text = composed(call, {**FRAME, "answers": na_all(call)})
    items = items_of(fake)
    assert items["dc_bodies1"]["type"] == "parts_list" and items["dc_bodies1"]["rows"] == ROWS
    assert items["dc_welds1"]["type"] == "weld" and items["dc_welds1"]["size_mm"] == 3
    assert items["rq_balloons"]["type"] == "balloons" and "rq_parts_list" not in items
    note = items["dc_welding1"]["text"]
    assert "MAG" in note and "ISO 5817-C" in note and "ISO 13920-B" in note and "a=4" not in note  # no size: the symbols say it
    assert "composed sheet" in text and "rq_balloons" in text
    assert fake.created[-1].general_tolerances == "ISO 13920-B"  # the title block says what the note says


def test_a_weld_that_leaves_a_joint_out_does_not_cover_the_group(settings: Settings) -> None:
    call, fake = frame_session(settings)
    half = {**WELD, "also": [["body:2", "body:5"]]}
    refused = call.err("drawing", action="compose", decisions={**FRAME, "welds": [half], "answers": na_all(call)})
    assert "[welds]" in refused and "[bodies]" not in refused and "[welding]" not in refused
    assert not fake.annotated


def test_a_second_compose_asks_everything_of_a_weldment_again_because_it_replaces_the_first(settings: Settings) -> None:
    call, fake = frame_session(settings)
    composed(call, {**FRAME, "answers": na_all(call)})
    make = fake.create_drawing

    def create(request: Any) -> Any:  # the CAD keeps the items of a sheet it builds again
        kept = list(fake.drawings["Drawing"]["items"])
        result = make(request)
        fake.drawings["Drawing"]["items"] = kept
        return result

    fake.create_drawing = create  # type: ignore[method-assign]
    fake.weldment = DrawingWeldPlan(
        bodies=tuple(b.model_copy(update={"named": True}) for b in BODIES), joints=(JOINTS[0].model_copy(update={"welded": True}),)
    )
    fake.facts = {**fake.facts, "weld_class_on_sheet": 1}
    plan = asked(call)
    assert "[bodies]" in plan and "[welds]" in plan and "[welding]" in plan
    text = composed(call, {**FRAME, "welding": {**WELDING, "quality": "B"}, "answers": na_all(call)})
    assert items_of(fake)["dc_welding1"]["text"].count("ISO 5817-B") == 1
    assert not any(f"open [{k}]" in text for k in ("bodies", "welds", "welding"))  # what is on the sheet answers them


@pytest.mark.parametrize(
    ("welding", "said"),
    [
        ({"process": "MAG", "quality": "E", "tolerance": "B"}, "welding.quality is one of B, C, D"),
        ({"process": "MAG", "quality": "C", "tolerance": "13920"}, "welding.tolerance"),
        ({"process": "", "quality": "C", "tolerance": "B"}, "welding is {"),
        ({"process": "MAG", "quality": "C"}, "welding is {"),
    ],
)
def test_a_welding_decision_that_is_not_valid_refuses_the_order(welding: dict[str, Any], said: str) -> None:
    with pytest.raises(ToolError) as caught:
        drawing_compose.normalise({"welding": welding})
    assert said in str(caught.value)


def test_the_levels_can_be_written_with_their_standard() -> None:
    items, sheet, _ = drawing_compose.normalise({"welding": {"process": "TIG", "quality": "ISO 5817-B", "tolerance": "ISO 13920-C"}}, "en")
    assert items[0]["text"].startswith("Welding process TIG; quality level ISO 5817-B") and "ISO 13920-C" in items[0]["text"]
    assert sheet == {"general_tolerances": "ISO 13920-C"}


def test_the_sheet_tolerance_the_designer_chose_wins_over_the_welding_class() -> None:
    _, sheet, _ = drawing_compose.normalise({"welding": WELDING, "sheet": {"general_tolerances": "ISO 2768-mK"}})
    assert sheet == {"general_tolerances": "ISO 2768-mK"}


@pytest.mark.parametrize("bodies", [[{"component": "body:1"}], [{"component": "body:1", "name": "  "}], [{"name": "Tubo"}], "body:1"])
def test_a_body_without_its_designation_refuses_the_order(bodies: Any) -> None:
    with pytest.raises(ToolError) as caught:
        drawing_compose.normalise({"bodies": bodies})
    assert "bodies is a list" in str(caught.value)


# --- the paper ----------------------------------------------------------------------------------------------------------------------


def test_a_bigger_paper_keeps_the_scale_of_the_smaller_one(settings: Settings) -> None:
    call, fake = frame_session(settings)
    bigger_paper_fits(fake)
    text = composed(call, {**FRAME, "answers": na_all(call)})
    assert "paper raised from A4 to A3" in text
    assert [c.scale for c in fake.created][-1] is not None and fake.created[-1].scale == 0.5  # the A4 sheet was at 1:2: still 1:2


def test_a_scale_below_the_legibility_floor_rises_to_it_on_the_bigger_paper(settings: Settings) -> None:
    call, fake = frame_session(settings)
    bigger_paper_fits(fake)
    make = fake.create_drawing

    def create(request: Any) -> Any:
        result = make(request)
        if request.template == "A4":
            fake.drawings["Drawing"]["scale"] = 0.1  # the part had to be drawn at 1:10 on A4
        return result

    fake.create_drawing = create  # type: ignore[method-assign]
    composed(call, {**FRAME, "answers": na_all(call)})
    assert fake.created[-1].template == "A3" and fake.created[-1].scale == pytest.approx(0.2)


def test_a_crowded_weldment_raises_the_paper_at_the_same_scale(settings: Settings) -> None:
    call, fake = frame_session(settings)
    crowd = finding("collision", "a note crosses a dimension")
    fake.findings = (*fake.findings, crowd)
    make = fake.create_drawing

    def create(request: Any) -> Any:
        result = make(request)
        fake.findings = tuple(f for f in fake.findings if f.code != "collision")
        if request.template == "A4":
            fake.findings = (*fake.findings, crowd)
        return result

    fake.create_drawing = create  # type: ignore[method-assign]
    text = composed(call, {**FRAME, "answers": na_all(call)})
    assert "paper raised from A4 to A3" in text and fake.created[-1].scale == 0.5


# --- where the members stand on the plate -------------------------------------------------------------------------------------------


def box(y: float, z: float, size: float = 40.0) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    return ((0.0, y, z), (400.0, y + size, z + size))


PLACEMENT = DrawingWeldPlacement(
    base="body:5",
    base_box=((-6.0, 0.0, 0.0), (0.0, 340.0, 340.0)),
    members=tuple(DrawingWeldMember(id=f"body:{n}", box=box(y, z)) for n, (y, z) in enumerate(((0, 0), (300, 0), (0, 300), (300, 300)), start=1)),
)


def located(settings: Settings, placement: DrawingWeldPlacement = PLACEMENT) -> tuple[Caller, DrawingFake]:
    call, fake = frame_session(settings, DrawingWeldPlan(bodies=BODIES, joints=JOINTS, placement=placement))
    fake.sheet_views = ("front", "right", "isometric")
    return call, fake


def test_plan_asks_where_the_members_stand_on_the_plate_with_the_measured_dimensions(settings: Settings) -> None:
    call, _ = located(settings)
    (line,) = [ln for ln in asked(call).splitlines() if "[positions]" in ln]
    assert "Medido (mm): y: a ras de los dos bordes; entre ellos 260 | z: a ras de los dos bordes; entre ellos 260" in line
    proposal = json.loads(re.search(r"positions: (\[\{.*\}\])", line).group(1))  # type: ignore[union-attr]
    assert [(d["along"], d["view"]) for d in proposal] == [("y", "right"), ("z", "right")]  # the view that looks along the thin axis
    assert proposal[0]["from"] == {"at": [0.0, 40.0, 0.0]} and proposal[0]["to"] == {"at": [0.0, 300.0, 0.0]}  # on the face the tubes stand on
    assert "positions: []" in line  # the way out: a jig fixes them


def test_a_member_set_in_from_the_edge_is_dimensioned_to_the_edge_as_well() -> None:
    placement = DrawingWeldPlacement(
        base="body:5",
        base_box=((-6.0, 0.0, 0.0), (0.0, 340.0, 340.0)),
        members=(DrawingWeldMember(id="body:1", box=box(50.0, 20.0)), DrawingWeldMember(id="body:2", box=box(250.0, 20.0))),
    )
    found = drawing_weld_questions.position_dims(placement, ("right",))
    assert found is not None
    dims, facts = found
    along_y = [(d["from"]["at"][1], d["to"]["at"][1]) for d in dims if d["along"] == "y"]
    assert along_y == [(0.0, 50.0), (90.0, 250.0), (290.0, 340.0)]  # edge, between, edge
    assert [(d["from"]["at"][2], d["to"]["at"][2]) for d in dims if d["along"] == "z"] == [(0.0, 20.0), (60.0, 340.0)]
    assert facts[0][0] == "y" and facts[0][1] == [False, False]


def test_nothing_is_asked_when_the_view_that_shows_the_plate_is_not_on_the_sheet(settings: Settings) -> None:
    assert drawing_weld_questions.position_dims(PLACEMENT, ("front", "isometric")) is None
    asks = drawing_weld_questions.ask(DrawingWeldPlan(bodies=BODIES, joints=JOINTS, placement=PLACEMENT), "es", views=("front",))
    assert not [a for a in asks if a.keys == ("positions",)]


def test_compose_refuses_a_weldment_whose_members_are_not_located_and_draws_the_dimensions_it_is_given(settings: Settings) -> None:
    call, fake = located(settings)
    refused = call.err("drawing", action="compose", decisions={**FRAME, "answers": na_all(call)})
    assert "[positions]" in refused and not fake.annotated
    line = next(ln for ln in asked(call).splitlines() if "[positions]" in ln)
    positions = json.loads(re.search(r"positions: (\[\{.*\}\])", line).group(1))  # type: ignore[union-attr]
    composed(call, {**FRAME, "positions": positions, "answers": na_all(call)})
    items = items_of(fake)
    assert items["dc_positions1"]["type"] == "dim" and items["dc_positions2"]["along"] == "z"


def test_an_engineer_who_says_no_dimension_is_needed_closes_the_question_with_an_empty_list(settings: Settings) -> None:
    call, fake = located(settings)
    composed(call, {**FRAME, "positions": [], "answers": na_all(call)})
    assert not [k for k in items_of(fake) if k.startswith("dc_positions")]
