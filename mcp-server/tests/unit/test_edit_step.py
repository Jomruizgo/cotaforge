"""``op(edit=<step id>, params={changes})``: change the parameters of an earlier step and let the model regenerate, with
no chained undo. Over the fake adapter, which sets a dimension (of a primitive, a sketch or a feature) in place and
refuses the rest (so the replay path -- undo to the step, run it edited, run the steps after it again -- is exercised
by what a dimension cannot say: a hole that moves, a sketch with other entities)."""

import math
from typing import Any

import pytest
from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import AdapterError, ErrorCode, ErrorInfo, ManualEditNotice

from tests.conftest import PLATE_SKETCH, TOP, Caller, MakeSession, NativeFake, plate

HOLE = {"face": TOP, "position": [30, 0], "standard": "simple", "diameter_mm": 6, "end_condition": "through_all"}
PLATE_VOLUME = 100 * 60 * 8


def volume(call: Caller) -> float:
    return call.session.measure()[0].volume_mm3


def step_ids(call: Caller) -> list[str]:
    return [s.id for s in call.session.steps()]


def drilled_plate(call: Caller) -> None:
    plate(call)
    call.ok("op", name="hole", params=HOLE)


def bored_block(call: Caller) -> None:
    """block 100 x 60 x 20 (b), D30 through tool (t), the bore (c = b - t)."""
    call.ok("doc", action="new", name="block")
    call.ok("op", name="box", id="b", params={"size": [100, 60, 20]})
    call.ok("op", name="cylinder", id="t", params={"diameter_mm": 30, "height_mm": 20, "origin": [50, 30, 0]})
    call.ok(
        "op",
        name="boolean_subtract",
        id="c",
        params={"target": {"kind": "body", "from_op": "b"}, "tools": [{"kind": "body", "from_op": "t"}]},
    )


# --- in place -----------------------------------------------------------------------------------------------------


def test_a_primitive_is_edited_in_place_and_the_reply_has_the_measured_change(call: Caller) -> None:
    bored_block(call)
    text = call.ok("op", edit="t", params={"diameter_mm": 40}, intent="wider bore")
    assert text.startswith("ok: t edited (cylinder)")
    assert "diameter_mm 30 -> 40" in text
    assert "in place" in text and "recomputed: t, c" in text
    assert volume(call) == pytest.approx(100 * 60 * 20 - math.pi * 20**2 * 20)
    assert "volume: " in text and "bbox: " in text and "valid: yes" in text
    assert step_ids(call) == ["b", "t", "c"]  # nothing was undone or re-run: the history is the same


def test_an_in_place_edit_leaves_undo_working(call: Caller) -> None:
    bored_block(call)
    call.ok("op", edit="b", params={"size": [120, 60, 20]})
    assert volume(call) == pytest.approx(120 * 60 * 20 - math.pi * 15**2 * 20)
    call.ok("undo", to="b")
    assert step_ids(call) == ["b"] and volume(call) == pytest.approx(120 * 60 * 20)


def test_a_change_the_cad_never_sees_is_recorded_without_touching_the_model(call: Caller) -> None:
    bored_block(call)
    text = call.ok("op", edit="t", params={}, intent="the cutter")
    assert "no change to the model" in text
    assert call.session.executor.plan.operations[1].intent == "the cutter"  # type: ignore[union-attr]


# --- a dimension of a feature or a sketch is set in place ------------------------------------------------------------


def test_a_hole_diameter_is_set_in_place_and_the_ids_stay(call: Caller) -> None:
    drilled_plate(call)
    text = call.ok("op", edit="s3", params={"diameter_mm": 10})
    assert text.startswith("ok: s3 edited (hole)")
    assert "diameter_mm 6 -> 10" in text and "changed in place; recomputed: s3" in text
    assert volume(call) == pytest.approx(PLATE_VOLUME - math.pi * 5**2 * 8)
    assert step_ids(call) == ["s1", "s2", "s3"]  # same ids: later references stay valid
    assert "volume: " in text and "(-" in text  # the volume change is measured, not estimated


def test_an_extrude_depth_is_set_in_place_and_what_follows_is_recomputed(call: Caller) -> None:
    drilled_plate(call)
    text = call.ok("op", edit="s2", params={"depth_mm": 12})
    assert "depth_mm 8 -> 12" in text and "changed in place; recomputed: s2, s3" in text
    assert volume(call) == pytest.approx(100 * 60 * 12 - math.pi * 3**2 * 12)
    assert call.session.measure()[1].size()[2] == pytest.approx(12)
    assert step_ids(call) == ["s1", "s2", "s3"]


def test_a_sketch_named_dimension_is_set_in_place_by_its_name(call: Caller) -> None:
    call.ok("doc", action="new", name="plate")
    sketch = {**PLATE_SKETCH, "named_dimensions": [{"name": "plate_w", "entity": "r", "attribute": "width"}]}
    call.ok("op", name="sketch", params=sketch)
    call.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 8})
    text = call.ok("op", edit="s1", params={"dimensions": {"plate_w": 120}})
    assert "plate_w 100 -> 120" in text and "changed in place; recomputed: s2" in text
    assert volume(call) == pytest.approx(120 * 60 * 8)
    assert step_ids(call) == ["s1", "s2"]


def test_an_entitys_own_dimension_is_named_entity_dot_attribute(call: Caller) -> None:
    drilled_plate(call)
    text = call.ok("op", edit="s1", params={"dimensions": {"r.width": 120}})
    assert "r.width 100 -> 120" in text and "changed in place" in text
    assert volume(call) == pytest.approx(120 * 60 * 8 - math.pi * 3**2 * 8)
    assert step_ids(call) == ["s1", "s2", "s3"]
    assert "r.nope" in call.err("op", edit="s1", params={"dimensions": {"r.nope": 5}})


# --- by replay: what a dimension cannot say ---------------------------------------------------------------------------


def test_a_hole_that_moves_is_rebuilt_and_says_so(call: Caller) -> None:
    drilled_plate(call)
    text = call.ok("op", edit="s3", params={"position": [-30, 0]})
    assert "method: rebuilt from s3" in text and "regenerated: s3" in text and "position" in text
    assert step_ids(call) == ["s1", "s2", "s3"]


def test_a_sketch_with_another_number_of_entities_is_rebuilt_and_says_why(call: Caller) -> None:
    plate(call)
    entities = [
        PLATE_SKETCH["entities"][0],
        {"kind": "rect", "name": "r2", "center": [200, 30], "width": 20, "height": 20},
    ]
    text = call.ok("op", edit="s1", params={"entities": entities})
    assert "method: rebuilt from s1" in text and "other entities" in text
    assert "regenerated: s1, s2" in text


def test_a_rebuild_that_would_drop_a_hand_made_change_is_refused(call: Caller) -> None:
    drilled_plate(call)
    call.session.hand_edits.push(
        ManualEditNotice(
            artefact_kind="feature",
            ref="f1",
            by="engineer",
            reason="gui",
            changes=("Length: 8 -> 9",),
            document=call.session.doc_name,
            name="Pad",
        )
    )
    text = call.err("op", edit="s3", params={"position": [-30, 0]})
    assert "drop what the engineer made by hand" in text and "Pad" in text and "state: unchanged" in text
    assert call.session.executor.plan.operations[2].position == (30, 0)  # type: ignore[union-attr]
    call.ok("op", edit="s3", params={"diameter_mm": 10})  # a dimension is set in place: nothing is dropped


def test_an_unknown_named_dimension_lists_the_known_ones(call: Caller) -> None:
    call.ok("doc", action="new", name="plate")
    sketch = {**PLATE_SKETCH, "named_dimensions": [{"name": "plate_w", "entity": "r", "attribute": "width"}]}
    call.ok("op", name="sketch", params=sketch)
    text = call.err("op", edit="s1", params={"dimensions": {"nope": 5}})
    assert "plate_w" in text and "nope" in text


def test_a_size_change_refills_the_hole_diameter_of_a_standard_hole(call: Caller) -> None:
    plate(call)
    iso = {**HOLE, "standard": "iso_clearance", "size": "M6"}
    iso.pop("diameter_mm")
    call.ok("op", name="hole", params=iso)
    before = volume(call)
    call.ok("op", edit="s3", params={"size": "M10"})
    assert volume(call) < before  # the bigger clearance hole took more material


def test_an_edit_after_an_undo_regenerates_from_the_steps_in_effect(call: Caller) -> None:
    drilled_plate(call)
    call.ok("op", name="hole", id="extra", params={**HOLE, "position": [-30, 0]})
    call.ok("undo")  # the second hole is gone
    call.ok("op", edit="s3", params={"diameter_mm": 10})
    assert step_ids(call) == ["s1", "s2", "s3"]
    assert volume(call) == pytest.approx(PLATE_VOLUME - math.pi * 5**2 * 8)


# --- a change that cannot be applied --------------------------------------------------------------------------------


def test_a_failing_regeneration_changes_nothing_and_names_the_step_that_failed(call: Caller) -> None:
    drilled_plate(call)  # the hole sits at u=30 from the face centre
    before = volume(call)
    narrow = [{"kind": "rect", "name": "r", "center": [50, 30], "width": 40, "height": 60}]
    text = call.err("op", edit="s1", params={"entities": narrow})
    assert "edit s1 was not applied" in text
    assert "s3" in text and "hole footprint" in text  # the step the part breaks at
    assert "state: unchanged" in text
    assert volume(call) == pytest.approx(before)
    assert step_ids(call) == ["s1", "s2", "s3"]
    # the original model is back, and it still takes edits
    call.ok("op", edit="s3", params={"diameter_mm": 8})
    assert volume(call) == pytest.approx(PLATE_VOLUME - math.pi * 4**2 * 8)


def test_an_invalid_value_is_refused_before_anything_moves(call: Caller) -> None:
    drilled_plate(call)
    text = call.err("op", edit="s3", params={"diameter_mm": -2})
    assert "params.diameter_mm" in text and 'op(name="hole"' in text
    assert step_ids(call) == ["s1", "s2", "s3"]


def test_an_unknown_parameter_lists_the_ones_the_step_has(call: Caller) -> None:
    drilled_plate(call)
    text = call.err("op", edit="s3", params={"diametre": 8})
    assert "diametre" in text and "diameter_mm" in text


def test_the_step_must_exist(call: Caller) -> None:
    drilled_plate(call)
    text = call.err("op", edit="s9", params={"depth_mm": 3})
    assert "s9" in text and "s1, s2, s3" in text


def test_name_and_edit_are_exclusive_and_one_is_required(call: Caller) -> None:
    drilled_plate(call)
    assert "not both" in call.err("op", name="box", edit="s3", params={})
    assert "name" in call.err("op", params={"depth_mm": 3}) and "edit" in call.last.text  # type: ignore[union-attr]


def test_a_native_step_cannot_be_edited_and_blocks_the_edits_before_it(make: MakeSession) -> None:
    call = Caller(make(NativeFake()))
    drilled_plate(call)
    call.ok("run_native", code="box 5", id="n1")
    text = call.err("op", edit="n1", params={"size": [1, 1, 1]})
    assert "run_native" in text
    text = call.err("op", edit="s3", params={"diameter_mm": 8})
    assert "n1" in text and "run_native" in text  # regenerating s3 would drop the native step
    assert volume(call) > 0


def test_the_op_schema_offers_edit_without_a_new_tool(call: Caller) -> None:
    from cotaforge_mcp.schemas import TOOLS, TOOLS_BY_NAME, input_schema

    schema = input_schema(TOOLS_BY_NAME["op"], call.session)
    assert "edit" in schema["properties"] and "required" not in schema
    assert [t.name for t in TOOLS].count("edit") == 0
    assert "edit" in call.ok("cad_guide", topic="op:hole")  # the reference says how to change a step later


def test_a_failed_edit_leaves_the_session_usable(make: MakeSession) -> None:
    call = Caller(make(FakeAdapter()))
    drilled_plate(call)
    call.err("op", edit="s2", params={"depth_mm": 0})
    call.ok("op", name="hole", id="more", params={**HOLE, "position": [-30, 0]})
    assert step_ids(call) == ["s1", "s2", "s3", "more"]


def test_a_chamfered_rect_is_edited_by_the_name_of_its_chamfer(call: Caller) -> None:
    call.ok("doc", action="new", name="plate")
    rect = {**PLATE_SKETCH["entities"][0], "chamfer_mm": 4}
    sketch = {
        **PLATE_SKETCH,
        "entities": [rect],
        "named_dimensions": [{"name": "plate_c", "entity": "r", "attribute": "chamfer"}],
    }
    call.ok("op", name="sketch", params=sketch)
    call.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 8})
    assert volume(call) == pytest.approx((100 * 60 - 2 * 4**2) * 8)
    text = call.ok("op", edit="s1", params={"dimensions": {"plate_c": 6}})
    assert "chamfer_mm 4 -> 6" in text
    assert volume(call) == pytest.approx((100 * 60 - 2 * 6**2) * 8)
    assert "chamfer_mm" in call.err("op", name="sketch", params={**sketch, "entities": [{**rect, "chamfer_mm": 40}]})


# --- a line's own dimension keeps the profile closed ------------------------------------------------------------------


def _line(name: str, start: list[float], end: list[float]) -> dict[str, object]:
    return {"kind": "line", "name": name, "start": start, "end": end}


def test_setting_the_length_of_a_line_drags_the_lines_joined_to_its_end() -> None:
    from cotaforge_mcp.session import _set_dimensions

    data = {
        "entities": [_line("a", [0, 0], [10, 0]), _line("b", [10, 0], [10, 10]), _line("c", [10, 10], [0, 0])],
        "named_dimensions": [],
    }
    _set_dimensions(data, {"a.length": 20})
    a, b, c = data["entities"]
    assert a["end"] == [20, 0] and b["start"] == [20, 0]  # type: ignore[index]
    assert b["end"] == c["start"] and c["end"] == a["start"]  # type: ignore[index]  # the rest was joined already


def test_setting_the_position_of_a_line_drags_the_lines_joined_to_both_its_ends() -> None:
    from cotaforge_mcp.session import _set_dimensions

    data = {
        "entities": [_line("a", [0, 0], [10, 0]), _line("b", [10, 0], [10, 10]), _line("c", [10, 10], [0, 0])],
        "named_dimensions": [],
    }
    _set_dimensions(data, {"a.v": 5})
    a, b, c = data["entities"]
    assert a["start"] == [0, 5] and a["end"] == [10, 5]  # type: ignore[index]
    assert b["start"] == a["end"] and c["end"] == a["start"]  # type: ignore[index]


def test_an_arc_joined_to_the_moved_end_is_a_refusal_that_says_what_to_do() -> None:
    from cotaforge_mcp.session import ToolError, _set_dimensions

    arc = {"kind": "arc", "name": "r", "center": [10, 5], "radius": 5, "start_deg": -90, "end_deg": 90}
    data = {"entities": [_line("a", [0, 0], [10, 0]), arc, _line("b", [10, 10], [0, 10])], "named_dimensions": []}
    with pytest.raises(ToolError) as refused:
        _set_dimensions(data, {"a.length": 20})
    assert "arc r" in str(refused.value) and "params.entities" in str(refused.value)


def test_a_dimension_that_would_collapse_a_neighbour_is_refused() -> None:
    from cotaforge_mcp.session import ToolError, _set_dimensions

    data = {"entities": [_line("a", [0, 0], [10, 0]), _line("b", [10, 0], [20, 0])], "named_dimensions": []}
    with pytest.raises(ToolError):
        _set_dimensions(data, {"a.length": 20})


def test_the_length_of_a_line_of_a_triangle_edits_a_closed_profile(call: Caller) -> None:
    call.ok("doc", action="new", name="wedge")
    sketch = {
        "on": {"kind": "plane", "name": "XY"},
        "entities": [_line("a", [0, 0], [40, 0]), _line("b", [40, 0], [40, 30]), _line("c", [40, 30], [0, 0])],
    }
    call.ok("op", name="sketch", params=sketch)
    call.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 5})
    before = volume(call)
    call.ok("op", edit="s1", params={"dimensions": {"a.length": 60}})
    assert volume(call) > before  # a profile that stayed closed: a solid, not an open wire
    entities = call.session.executor.plan.operations[0].entities  # type: ignore[union-attr]
    assert entities[0].end == entities[1].start


def test_a_rebuild_puts_the_steps_after_the_edited_one_back_where_they_were(call: Caller) -> None:
    drilled_plate(call)
    call.ok("op", name="hole", params={**HOLE, "position": [-30, -20], "diameter_mm": 4})
    text = call.ok("op", edit="s3", params={"position": [-30, 0]})
    assert "method: rebuilt from s3" in text and "regenerated: s3, s4" in text
    assert "could not stay" not in text
    steps = {s.op_id: s for s in call.session.require_executor().trace().active_steps()}
    assert steps["s4"].attempts[-1].request["origin"] is not None  # built where the first build had it
    assert steps["s3"].attempts[-1].request["origin"] is not None  # the edited hole keeps its face too


def test_a_rebuild_that_names_another_face_for_the_edited_step_takes_the_face_as_it_is(call: Caller) -> None:
    drilled_plate(call)
    other = {"kind": "face", "from_op": "s2", "select": {"normal": [0, 0, -1]}}
    text = call.ok("op", edit="s3", params={"face": other, "position": [10, 0]})
    assert "method: rebuilt from s3" in text
    step = next(s for s in call.session.require_executor().trace().active_steps() if s.op_id == "s3")
    assert step.attempts[-1].request["origin"] is None


# --- a rebuild that cannot put the steps back ------------------------------------------------------------------------


class BreaksAtS4(FakeAdapter):
    """Cannot drill the step s4 once broken is set: the edited run fails there, and so does the restore."""

    broken = False

    def hole(self, request: Any) -> Any:
        if self.broken and request.operation_id == "s4":
            raise AdapterError(ErrorInfo(code=ErrorCode.geometry_failed, message="no room for it", recoverable=True))
        return super().hole(request)


def test_a_rebuild_that_cannot_restore_says_what_the_model_holds_and_the_record_agrees(make: MakeSession) -> None:
    adapter = BreaksAtS4()
    call = Caller(make(adapter))
    drilled_plate(call)
    call.ok("op", name="hole", params={**HOLE, "position": [-30, -20], "diameter_mm": 4})
    adapter.broken = True
    text = call.err("op", edit="s3", params={"position": [-30, 0]})
    assert "edit_restore_failed" in text
    assert "the model now holds: s1, s2, s3" in text and "lost (not rebuilt): s4" in text
    assert "volume:" in text  # the measure of the model as it is
    # the record is the model: three steps, in the status and in the plan, not the four that were asked for
    assert "steps: 3, last s3 hole" in call.ok("doc", action="status")
    assert [op.id for op in call.session.require_executor().plan.operations] == ["s1", "s2", "s3"]
    adapter.broken = False
    call.ok("op", name="hole", params={**HOLE, "position": [-30, 20], "diameter_mm": 4})  # still usable
    assert len(step_ids(call)) == 4


# --- a selector by an absolute position follows its face ------------------------------------------------------------

CENTRED_TOP = {"kind": "face", "from_op": "s2", "select": {"normal": [0, 0, 1], "center": [50, 30, 8]}}


def test_a_face_selected_by_its_centre_follows_the_face_when_an_earlier_step_moves_it(call: Caller) -> None:
    plate(call)
    call.ok("op", name="hole", params={**HOLE, "face": CENTRED_TOP, "end_condition": "blind", "depth_mm": 3})
    text = call.ok("op", edit="s2", params={"depth_mm": 12})
    assert "method: changed in place; recomputed: s2, s3" in text
    assert "followed: s3.face followed its face: centre (50, 30, 8) -> (50, 30, 12)" in text
    selector = call.session.require_executor().plan.operations[-1].face.select  # type: ignore[attr-defined]
    assert tuple(selector.center) == pytest.approx((50, 30, 12))  # the plan says where the face is now
    # the edit that failed in the field: a later step is edited, and the steps are rebuilt from the plan
    text = call.ok("op", edit="s3", params={"position": [-30, 0]})
    assert "method: rebuilt from s3" in text
    assert step_ids(call) == ["s1", "s2", "s3"]


def test_a_face_given_by_the_arguments_of_an_adapter_operation_follows_too(make: MakeSession) -> None:
    from tests.unit.test_adapter_operations import WizardFake

    call = Caller(make(WizardFake()))
    plate(call)
    face = {"kind": "face", "from_op": "s2", "select": {"normal": [0, 0, 1], "center": [50, 30, 8]}}
    call.ok("op", name="clearance_hole", params={"face": face, "position": [10, 10], "diameter_mm": 6})
    text = call.ok("op", edit="s2", params={"depth_mm": 12})
    assert "followed: s3.face followed its face: centre (50, 30, 8) -> (50, 30, 12)" in text
    params = call.session.require_executor().plan.operations[-1].params  # type: ignore[attr-defined]
    assert tuple(params["face"]["select"]["center"]) == pytest.approx((50, 30, 12))
    call.ok("op", edit="s3", params={"diameter_mm": 8})  # rebuilt through the selector that follows
    assert step_ids(call) == ["s1", "s2", "s3"]


class LosesFace(FakeAdapter):
    """Its registry no longer knows the faces in ``dead`` (the edit made them go away) and counts the undos."""

    def __init__(self) -> None:
        super().__init__()
        self.dead: set[str] = set()
        self.undone = 0

    def face_info(self, request: Any) -> Any:
        if request.face.id in self.dead:
            raise AdapterError(ErrorInfo(code=ErrorCode.invalid_reference, message="no such face", recoverable=True))
        return super().face_info(request)

    def undo_last(self, request: Any) -> Any:
        self.undone += 1
        return super().undo_last(request)


def test_an_edit_that_makes_a_later_face_go_away_names_the_step_it_breaks_and_rebuilds_nothing(
    make: MakeSession,
) -> None:
    adapter = LosesFace()
    call = Caller(make(adapter))
    plate(call)
    call.ok("op", name="hole", params={**HOLE, "face": CENTRED_TOP, "end_condition": "blind", "depth_mm": 3})
    steps = {s.op_id: s for s in call.session.require_executor().trace().active_steps()}
    adapter.dead = {steps["s3"].resolved_refs["face"]}
    before, undone = volume(call), adapter.undone
    text = call.err("op", edit="s2", params={"depth_mm": 12})
    assert "edit s2 was not applied" in text and "whose face is gone" in text
    assert "s3.face no longer resolves" in text
    assert "state: unchanged (nothing was rebuilt)" in text
    assert adapter.undone == undone  # no step was undone to try
    assert volume(call) == pytest.approx(before) and step_ids(call) == ["s1", "s2", "s3"]
    adapter.dead = set()
    call.ok("op", edit="s3", params={"diameter_mm": 8})  # and the session takes edits as before


def test_what_the_adapter_warns_of_after_an_in_place_edit_reaches_the_agent(
    call: Caller, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A sheet that could not be redrawn after the edit is a warning of the adapter: it must be in the reply, or the
    agent trusts a drawing that shows the old value."""
    bored_block(call)
    real = FakeAdapter.edit_feature

    def warned(self: FakeAdapter, request: object) -> object:
        result = real(self, request)  # type: ignore[arg-type]
        return result.model_copy(update={"warnings": ("the sheet Sheet could not be redrawn after the edit",)})

    monkeypatch.setattr(FakeAdapter, "edit_feature", warned)
    text = call.ok("op", edit="t", params={"diameter_mm": 40})
    assert "method: changed in place" in text
    assert "warning: the sheet Sheet could not be redrawn after the edit" in text
