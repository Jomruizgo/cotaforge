# ruff: noqa: E501
"""The server side of the baseline stacks: the findings that hold an export, the decision that carries the datum face, the
question the plan asks for a turned part, and the guide that says all of it."""

from cotaforge.contract import drawing_items
from cotaforge_mcp import drawing_compose as compose
from cotaforge_mcp import drawing_requirements as reqs
from cotaforge_mcp import guide_drawing


def test_the_stack_findings_are_measurements_the_agent_cannot_dismiss_on_its_own_word() -> None:
    assert {"dimension_stack_order", "dimension_gap", "dimension_crossing"} <= reqs.MEASURED_FINDINGS
    assert "collision" not in reqs.MEASURED_FINDINGS  # a judgement: accepted when no free place exists


def test_the_datum_face_is_a_decision_of_the_relations_never_defaulted() -> None:
    items, _, _ = compose.normalise({"relations": [{"type": "baseline_dims", "from": "face:6", "side": "below"}]})
    assert items == [{"type": "baseline_dims", "from": "face:6", "side": "below", "id": "dc_relations1"}]
    assert drawing_items.REQUIRED_FIELDS["baseline_dims"] == ("from",)
    missing = drawing_items.problems([{"type": "baseline_dims"}])
    assert any("from is required" in m for m in missing), missing


def test_keyway_dims_takes_the_class_of_the_width_and_the_deviations_of_the_depth() -> None:
    assert {"fit", "depth_tol", "section", "letter"} <= drawing_items.ITEM_FIELDS["keyway_dims"]
    assert "removed" in drawing_items.ITEM_FIELDS["section"]
    assert drawing_items.problems([{"type": "keyway_dims", "fit": "P9", "depth_tol": [0.2, 0.0]}]) == []


def test_the_turned_part_question_asks_which_face_the_lengths_are_counted_from() -> None:
    row = next(r for r in reqs.ROWS if r.id == "req.turned_part")
    assert "baseline_dims" in row.proposal[1] and "face" in row.question[1]


def test_the_guide_says_who_chooses_the_datum_and_what_holds_the_export() -> None:
    text = guide_drawing.drawing_text({"annotate_drawing", "describe_drawing", "create_drawing"}, guide_drawing.TOPIC_DIMS)
    assert "never chosen for the engineer" in text and "ONE datum" in text
    assert "dimension_stack_order" in text and "dimension_gap" in text and "6,5 mm" in text
    assert "REMOVED section" in text and "no end view is made for a keyway" in text
