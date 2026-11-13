"""The engineer's hand edits reach the agent with the next tool reply: once, short, coalesced, only changes of the
design, and never the selection."""

import threading

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import FaceHandle, ManualEditNotice

from cotaforge_mcp.hand_edits import HEADER, MAX_SHOWN, HandEdits
from tests.conftest import Caller, MakeSession, plate
from tests.unit.test_crash_recovery import crashing


def _edit(
    cad: FakeAdapter, name: str, *changes: str, ref: str | None = None, kind: str = "object", **extra: str
) -> None:
    cad.notify_manual_edit(
        artefact_kind=kind,
        ref=ref or name,
        by="engineer",
        reason=extra.pop("reason", "hand edit in the FreeCAD GUI"),
        changes=list(changes),
        document=extra.pop("document", "plate"),
        name=name,
        action=extra.pop("action", "edited"),
    )


def _plate_with_cad(make: MakeSession) -> tuple[Caller, FakeAdapter]:
    cad = FakeAdapter()
    call = Caller(make(cad))
    plate(call)
    return call, cad


def test_the_edit_heads_the_next_reply_of_any_tool_and_only_that_one(make: MakeSession) -> None:
    call, cad = _plate_with_cad(make)
    quiet = call.ok("doc", action="status")
    _edit(cad, "Pocket", "Length: 5.0 -> 8.0")
    text = call.ok("doc", action="status")
    assert text.splitlines()[0] == HEADER
    assert text.splitlines()[1] == "- Pocket: Length: 5.0 -> 8.0"
    assert text.splitlines()[2:] == quiet.splitlines()  # the reply itself is untouched
    assert call.ok("doc", action="status") == quiet  # delivered once


def test_it_also_heads_a_tool_that_never_touches_the_cad_and_an_error(make: MakeSession) -> None:
    call, cad = _plate_with_cad(make)
    _edit(cad, "Pocket", "Length: 5.0 -> 8.0")
    assert call.ok("cad_guide", topic="traps").startswith(HEADER)
    _edit(cad, "Pocket", "Length: 8.0 -> 9.0")
    assert call.err("op", name="nope", params={}).startswith(HEADER)


def test_nothing_changed_means_not_a_character_added(make: MakeSession) -> None:
    call, _ = _plate_with_cad(make)
    assert HEADER not in call.ok("doc", action="status")


def test_a_feature_of_the_plan_is_named_by_its_step(make: MakeSession) -> None:
    call, cad = _plate_with_cad(make)
    extrude = call.session.steps()[-1]
    assert extrude.feature is not None
    _edit(cad, "Pad", "Length: 8.0 -> 12.0", ref=extrude.feature.id, kind="feature")
    assert call.ok("doc", action="status").splitlines()[1] == f"- Pad (step {extrude.id}): Length: 8.0 -> 12.0"


def test_several_edits_of_one_property_are_one_line_from_the_first_value_to_the_last(make: MakeSession) -> None:
    call, cad = _plate_with_cad(make)
    _edit(cad, "Pad", "Length: 8.0 -> 10.0", "Reversed: False -> True")
    _edit(cad, "Pad", "Length: 10.0 -> 15.0")
    lines = call.ok("doc", action="status").splitlines()
    assert lines[1] == "- Pad: Length: 8.0 -> 15.0; Reversed: False -> True"


def test_an_edit_put_back_and_an_object_added_then_removed_are_not_reported(make: MakeSession) -> None:
    call, cad = _plate_with_cad(make)
    quiet = call.ok("doc", action="status")
    _edit(cad, "Pad", "Length: 8.0 -> 10.0")
    _edit(cad, "Pad", "Length: 10.0 -> 8.0")
    _edit(cad, "Box2", "added Part::Box", action="added")
    _edit(cad, "Box2", "removed Part::Box", action="removed")
    assert call.ok("doc", action="status") == quiet


def test_objects_added_and_removed_are_told_with_their_type(make: MakeSession) -> None:
    call, cad = _plate_with_cad(make)
    _edit(cad, "Box2", "added Part::Box", action="added")
    _edit(cad, "Old", "removed PartDesign::Pocket", action="removed")
    lines = call.ok("doc", action="status").splitlines()
    assert lines[1:3] == ["- Box2: added Part::Box", "- Old: removed PartDesign::Pocket"]


def test_an_undo_by_the_engineer_says_so(make: MakeSession) -> None:
    call, cad = _plate_with_cad(make)
    _edit(cad, "Pad", "Length: 12.0 -> 8.0", reason="undo in the FreeCAD GUI")
    assert call.ok("doc", action="status").splitlines()[1] == "- Pad: Length: 12.0 -> 8.0 (undo)"


def test_an_edit_in_another_open_document_names_the_document(make: MakeSession) -> None:
    call, cad = _plate_with_cad(make)
    _edit(cad, "Pad", "Length: 8.0 -> 9.0", document="bracket")
    assert call.ok("doc", action="status").splitlines()[1] == "- [bracket] Pad: Length: 8.0 -> 9.0"


def test_a_long_burst_is_capped_and_counted_and_the_block_stays_small(make: MakeSession) -> None:
    call, cad = _plate_with_cad(make)
    for number in range(MAX_SHOWN + 9):
        _edit(cad, f"Obj{number}", "Length: 1.0 -> 2.0", "Width: 3.0 -> 4.0", "Height: 5.0 -> 6.0", "Radius: 1 -> 2")
    cad.notify_manual_edit(
        artefact_kind="object", ref="", by="engineer", reason="r", changes=["25 more objects changed"], action="more"
    )
    text = call.ok("doc", action="status")
    lines = text.splitlines()
    assert lines[0] == HEADER
    assert len([line for line in lines if line.startswith("- Obj")]) == MAX_SHOWN
    assert lines[MAX_SHOWN + 1] == "... and 34 more changed objects (inspect(action=tree) shows the current state)"
    assert "Radius" not in text  # four changes per object: three shown and "+1 more"
    assert "+1 more" in lines[1]
    assert len("\n".join(lines[: MAX_SHOWN + 2])) < 900


def test_a_typical_notice_costs_little_context(make: MakeSession) -> None:
    call, cad = _plate_with_cad(make)
    quiet = call.ok("doc", action="status")
    _edit(cad, "Pad", "Length: 8.0 -> 12.0")
    assert len(call.ok("doc", action="status")) - len(quiet) < 200


def test_the_selection_is_never_pushed_only_read_when_asked(make: MakeSession) -> None:
    call, cad = _plate_with_cad(make)
    quiet = call.ok("doc", action="status")
    top = call.ok("inspect", action="faces", op="s2")
    assert top
    cad.set_selection([FaceHandle(id="face:6")])  # the engineer clicks a face
    assert cad._selection_listener is None  # nobody listens: nothing is queued for the agent
    assert call.ok("doc", action="status") == quiet
    assert "selection: 1 usable" in call.ok("view", action="selection")  # asked: answered


def test_a_relaunch_forgets_the_notices_of_the_model_that_died_and_keeps_listening(make: MakeSession) -> None:
    call, cad = crashing(make)
    plate(call)
    call.ok("doc", action="save", path="plate.fake")
    _edit(cad, "Pad", "Length: 8.0 -> 9.0")
    cad.crash()
    text = call.err("doc", action="status")
    assert HEADER not in text
    assert "relaunched" in text
    assert HEADER not in call.ok("doc", action="status")
    _edit(cad, "Pad", "Length: 8.0 -> 11.0")  # the same adapter object, the same listener
    assert call.ok("doc", action="status").splitlines()[0] == HEADER


def test_notices_arriving_from_other_threads_are_none_lost_none_repeated() -> None:
    queue = HandEdits()
    pushers, each = 6, 20

    def pusher(index: int) -> None:
        for number in range(each):
            queue.push(
                ManualEditNotice(
                    artefact_kind="object",
                    ref=f"O{index}_{number}",
                    by="engineer",
                    reason="r",
                    changes=("Length: 1.0 -> 2.0",),
                    document="d",
                    name=f"O{index}_{number}",
                )
            )

    class FakeSession:
        doc_name = "d"

        @staticmethod
        def step_of_feature(_ref: str) -> None:
            return None

    counted = [0]
    done = threading.Event()

    def count(text: str | None) -> None:
        if text is None:
            return
        for line in text.splitlines()[1:]:
            counted[0] += 1 if line.startswith("- ") else int(line.split()[2])

    def taker() -> None:
        while not done.is_set():
            count(queue.take(FakeSession()))  # type: ignore[arg-type]

    reader = threading.Thread(target=taker)
    writers = [threading.Thread(target=pusher, args=(i,)) for i in range(pushers)]
    reader.start()
    for thread in writers:
        thread.start()
    for thread in writers:
        thread.join()
    done.set()
    reader.join()
    count(queue.take(FakeSession()))  # type: ignore[arg-type]
    assert counted[0] == pushers * each
    assert not queue.pending()


def test_the_instructions_say_it_in_one_line_only_where_there_is_a_window(make: MakeSession) -> None:
    from cotaforge_mcp.guide import server_instructions
    from tests.conftest import fake_without

    with_window = server_instructions(make(FakeAdapter()))
    assert with_window.count('("hand edits in the CAD")') == 1
    assert "hand edits in the CAD" not in server_instructions(make(fake_without("get_selection")))


# --- what still differs from the plan --------------------------------------------------------------------------------


class _Session:
    doc_name = "D"

    @staticmethod
    def step_of_feature(_ref: str) -> None:
        return None


def _notice(*changes: str, document: str = "D", name: str = "Pad", action: str = "edited") -> ManualEditNotice:
    return ManualEditNotice(
        artefact_kind="feature",
        ref="f1",
        by="hand",
        reason="x",
        changes=changes,
        document=document,
        name=name,
        action=action,
    )


def test_a_hand_edit_that_is_put_back_stops_blocking_a_rebuild() -> None:
    hand = HandEdits()
    hand.push(_notice("Length: 10 -> 12"))
    assert hand.touched() == ["Pad"]
    hand.push(_notice("Length: 12 -> 10"))  # undone
    assert hand.touched() == [] and not hand.pending()


def test_a_hand_edit_put_back_after_the_agent_was_told_stops_blocking_too() -> None:
    hand = HandEdits()
    hand.push(_notice("Length: 10 -> 12"))
    hand.take(_Session())  # type: ignore[arg-type]
    assert hand.touched() == ["Pad"]  # delivered, still not in the plan
    hand.push(_notice("Length: 12 -> 10"))
    assert hand.touched() == []


def test_one_property_put_back_does_not_clear_another_that_is_still_changed() -> None:
    hand = HandEdits()
    hand.push(_notice("Length: 10 -> 12", "Offset: 0 -> 2"))
    hand.push(_notice("Length: 12 -> 10"))
    assert hand.touched() == ["Pad"]
    hand.push(_notice("Offset: 2 -> 0"))
    assert hand.touched() == []


def test_an_edit_that_changes_again_blocks_until_it_is_back_at_the_start() -> None:
    hand = HandEdits()
    hand.push(_notice("Length: 10 -> 12"))
    hand.push(_notice("Length: 12 -> 15"))
    hand.push(_notice("Length: 15 -> 12"))
    assert hand.touched() == ["Pad"]
    hand.push(_notice("Length: 12 -> 10"))
    assert hand.touched() == []


def test_an_added_object_blocks_until_it_is_removed_and_an_edit_does_not_hide_it() -> None:
    hand = HandEdits()
    hand.push(_notice("added Pad", action="added"))
    hand.push(_notice("Length: 1 -> 2"))
    hand.push(_notice("Length: 2 -> 1"))
    assert hand.touched() == ["Pad"]
    hand.push(_notice("removed Pad", action="removed"))
    assert hand.touched() == []


def test_a_change_without_a_before_and_after_blocks_until_the_object_is_removed() -> None:
    hand = HandEdits()
    hand.push(_notice("constraint added"))
    hand.push(_notice("Length: 1 -> 2"))
    hand.push(_notice("Length: 2 -> 1"))
    assert hand.touched() == ["Pad"]


def test_a_hand_edit_of_another_document_does_not_block_this_one() -> None:
    hand = HandEdits()
    hand.push(_notice("added Other", document="OtherDoc", name="Other", action="added"))
    hand.push(_notice("Length: 10 -> 12", document="plate"))
    assert hand.touched("plate") == ["Pad"]
    assert hand.touched("third") == []
    assert sorted(hand.touched()) == ["Other", "Pad"]


def test_the_refusal_of_a_rebuild_ignores_the_other_document_and_says_how_to_go_on(call: Caller) -> None:
    from tests.unit.test_edit_step import drilled_plate

    drilled_plate(call)
    hand = call.session.hand_edits
    hand.push(_notice("Length: 8 -> 9", document="elsewhere"))
    call.ok("op", edit="s3", params={"end_condition": "blind", "depth_mm": 4})  # a rebuild: no hand edit here
    hand.push(_notice("Length: 8 -> 9", document=call.session.doc_name))
    text = call.err("op", edit="s3", params={"end_condition": "through_all", "depth_mm": None})
    assert "drop what the engineer made by hand" in text and "undo" in text and "new step" in text
    hand.push(_notice("Length: 9 -> 8", document=call.session.doc_name))
    call.ok("op", edit="s3", params={"end_condition": "through_all", "depth_mm": None})
