# ruff: noqa: E501
"""The agent looks at the sheet before it leaves (fake CAD that keeps the sheets): export waits for a whole-sheet capture taken
after the last change of the sheet, in every review mode and in both drawing flows; the capture returns the whole sheet, the
zoomed crops with their labels, and the measured list in plain text."""

import pytest
from cotaforge.contract import SheetFinding

from cotaforge_mcp import design_memory
from cotaforge_mcp.config import Settings
from tests.conftest import Caller, DrawingFake
from tests.unit.test_drawing_finished import NOT_LOCATED, drawing_session

PLANTED = SheetFinding(
    kind="stack_order",
    text="front view (above): l340 (340 mm) stands nearer the part than l260 (260 mm) and is longer",
    box=(0.0, 45.0, 345.0, 62.0),
    view="front",
    items=("l340", "l260"),
)
OVERLAP = SheetFinding(kind="overlap", text="d3 on d4: '12' over '34'", box=(90.0, 100.0, 104.0, 110.0), view="top")


def session(settings: Settings, mode: str = "none") -> tuple[Caller, DrawingFake]:
    call, fake = drawing_session(settings, mode=mode)
    call.looks = False  # these tests look (or do not) by themselves
    return call, fake


def refused(call: Caller, action: str = "pdf") -> str:
    text = call.err("export", action=action)
    assert "error: sheet_not_looked_at" in text
    return text


# --- the gate ------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("mode", ["none", "own"])
def test_export_is_refused_until_the_sheet_is_captured_in_every_review_mode(settings: Settings, mode: str) -> None:
    call, fake = session(settings, mode)
    if mode == "own":  # the points of the review are closed first (nothing is asked of a clean fake sheet)
        call.ok("drawing", action="review")
    text = refused(call)
    assert "has not captured it" in text and 'drawing(action="capture", sheet="Drawing")' in text and fake.exported == []
    call.ok("drawing", action="capture")
    assert "exported pdf" in call.ok("export", action="pdf")
    assert "exported dxf" in call.ok("export", action="dxf", path="plate.dxf")


def test_the_refusal_says_why_the_capture_is_old(settings: Settings) -> None:
    call, _ = session(settings)
    call.ok("drawing", action="capture")
    call.ok("drawing", action="add", items=[{"type": "note", "text": "Sin rebabas"}])
    text = refused(call)
    assert "the last capture was at edit 0 and the sheet is now at edit 1" in text


def test_only_a_change_that_modifies_the_sheet_makes_the_capture_old(settings: Settings) -> None:
    call, _ = session(settings)
    call.ok("drawing", action="capture")
    call.ok("drawing", action="list")
    call.ok("drawing", action="read")
    call.ok("drawing", action="review")
    call.ok("drawing", action="requirements")
    call.ok("drawing", action="capture", region=[0, 0, 100, 100])  # a zoom looks at it, it does not replace the look
    assert "exported pdf" in call.ok("export", action="pdf")


def test_a_capture_of_a_region_is_not_a_look_at_the_sheet(settings: Settings) -> None:
    call, _ = session(settings)
    call.ok("drawing", action="capture", region=[0, 0, 100, 100])
    assert "has not captured it" in refused(call)


def test_a_change_of_the_part_after_the_capture_makes_it_old(settings: Settings) -> None:
    call, _ = session(settings)
    call.ok("drawing", action="capture")
    call.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 12})  # the views of the sheet are redrawn
    assert "the part changed after the last capture" in refused(call)
    call.ok("drawing", action="capture")
    assert "exported pdf" in call.ok("export", action="pdf")


def test_each_sheet_is_looked_at_on_its_own(settings: Settings) -> None:
    call, _ = session(settings)
    call.ok("drawing", action="create", sheet="Second")
    call.ok("drawing", action="capture", sheet="Drawing")
    assert "sheet Second" in call.err("export", action="pdf", sheet="Second")
    assert "exported pdf" in call.ok("export", action="pdf", sheet="Drawing", path="one.pdf")


def test_the_gate_holds_in_the_sheet_flow_too(settings: Settings) -> None:
    design_memory.save_brief(settings, None, None, None, None, None, None, "sheet")
    call, _ = session(settings)
    assert design_memory.load_brief(settings).drawing_flow == "sheet"
    refused(call)
    call.ok("drawing", action="capture")
    assert "exported pdf" in call.ok("export", action="pdf")


def test_the_unfinished_points_are_said_before_the_look(settings: Settings) -> None:
    call, fake = session(settings, "own")
    fake.findings = (NOT_LOCATED,)
    text = call.err("export", action="pdf")
    assert "drawing_unfinished" in text  # the agent closes the points first; the look is the last step before the file


def test_the_dxf_of_a_face_is_not_a_drawing_and_needs_no_look(settings: Settings) -> None:
    call, _ = session(settings)
    assert "sheet_not_looked_at" not in call("export", action="dxf", face="face:1", path="cut.dxf").text


# --- what the capture returns ----------------------------------------------------------------------------------------------------


def test_a_capture_lists_what_was_measured_in_plain_text_with_where_to_look(settings: Settings) -> None:
    call, fake = session(settings)
    fake.output = {"findings": (PLANTED, OVERLAP)}
    fake.crops = [("front: x 0..120, y 30..70 mm, 10 px/mm", (0,)), ("top: x 70..130, y 80..120 mm, 10 px/mm", (1,))]
    reply = call("drawing", action="capture")
    assert not reply.is_error and len(reply.images) == 3  # the whole sheet and the two crops
    text = reply.text
    assert "measured on this sheet, 2 to fix or to answer" in text
    assert "1. stack_order: front view (above): l340 (340 mm) stands nearer the part than l260" in text and "x 0..345, y 45..62 mm; image 2" in text
    assert "2. overlap: d3 on d4" in text and "image 3" in text
    assert "images: 1 = the whole sheet" in text and "2 = zoom, front: x 0..120" in text and "3 = zoom, top: x 70..130" in text
    assert fake.captured[-1].crops is True


def test_a_clean_capture_says_nothing_was_measured_and_what_is_not_measured(settings: Settings) -> None:
    call, _ = session(settings)
    text = call.ok("drawing", action="capture")
    assert "no overlap, crossing or stack out of order found" in text and "native TechDraw objects" in text


def test_the_crops_of_a_sheet_that_has_not_changed_are_not_sent_again(settings: Settings) -> None:
    call, fake = session(settings)
    fake.crops = [("front: x 0..120, y 30..70 mm, 10 px/mm", ())]
    first = call("drawing", action="capture")
    again = call("drawing", action="capture")
    assert len(first.images) == 2 and len(again.images) == 1 and "ask a region= to zoom" in again.text
    assert fake.captured[-1].crops_given_at == 0 and fake.captured[0].crops_given_at is None  # the CAD is told not to paint them
    call.ok("drawing", action="add", items=[{"type": "note", "text": "Sin rebabas"}])
    assert len(call("drawing", action="capture").images) == 2  # the sheet changed: its crops come again


def test_a_region_capture_returns_one_image_and_only_the_findings_inside_it(settings: Settings) -> None:
    call, fake = session(settings)
    fake.output = {"findings": (PLANTED, OVERLAP)}
    fake.crops = [("front: x 0..120", (0,))]
    reply = call("drawing", action="capture", region=[85, 95, 110, 115])
    assert len(reply.images) == 1 and fake.captured[-1].crops is False
    assert "overlap: d3 on d4" in reply.text and "stack_order" not in reply.text
    assert "measured in this region: nothing (2 on the whole sheet)" in call.ok("drawing", action="capture", region=[200, 150, 250, 190])


def test_many_findings_are_cut_and_the_rest_counted(settings: Settings) -> None:
    call, fake = session(settings)
    fake.output = {"findings": tuple(SheetFinding(kind="overlap", text=f"a{n} on b{n}", box=(1, 1, 5, 5)) for n in range(20))}
    text = call.ok("drawing", action="capture")
    assert "20 to fix" in text and "and 5 more" in text and "a14 on b14" in text and "a15 on b15" not in text


def test_crops_left_out_by_the_cap_are_listed(settings: Settings) -> None:
    call, fake = session(settings)
    fake.crops = [("front: x 0..120", ())]
    fake.output = {"left_out": ("side: x 0..90 (ask with region=[0, 0, 90, 60])",)}
    assert "not drawn (the cap on crops): side: x 0..90" in call.ok("drawing", action="capture")


def test_the_tools_say_in_the_capture_reply_that_export_waits_for_it(settings: Settings) -> None:
    call, _ = session(settings)
    assert "export waits for a whole-sheet capture taken after the last change" in call.ok("drawing", action="capture")
