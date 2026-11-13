"""Feature-first modelling aids: hole by a world point, sketch with world points, the document purpose, the hole hint on
a boolean subtraction and the recipe of cad_guide. Over the fake adapter."""

import math
from pathlib import Path

import pytest

from cotaforge_mcp import catalog, checkpoint, recipes
from cotaforge_mcp.guide import guide
from tests.conftest import TOP, Caller, plate

XY = {"kind": "plane", "name": "XY"}
# the plate of tests.conftest is 100 x 60 x 8 with its corner at the origin: the top face is centred at (50, 30, 8)
HOLE_AT = {"face": TOP, "at": [20, 10, 8], "standard": "simple", "diameter_mm": 6, "end_condition": "through_all"}
HOLE_AREA = math.pi * 9


def volume(call: Caller) -> float:
    return call.session.measure()[0].volume_mm3


# --- hole at a world point ---------------------------------------------------------------------------------------


def test_hole_at_drills_where_position_does(call: Caller) -> None:
    plate(call)
    call.ok("op", name="hole", params=HOLE_AT)
    assert volume(call) == pytest.approx(48000 - HOLE_AREA * 8)
    call.ok("undo")
    call.ok("op", name="hole", params={**HOLE_AT, "at": None, "position": [-30, -20]})
    assert volume(call) == pytest.approx(48000 - HOLE_AREA * 8)


def test_hole_at_is_projected_and_refused_outside_the_face_by_how_much(call: Caller) -> None:
    plate(call)
    call.ok("op", name="hole", params={**HOLE_AT, "at": [20, 10, 99]})  # off the plane: projected onto the face
    call.ok("undo")
    text = call.err("op", name="hole", params={**HOLE_AT, "at": [120, 10, 8]})
    assert "20.000 mm outside" in text and "state: unchanged" in text


def test_hole_needs_exactly_one_of_position_and_at(call: Caller) -> None:
    plate(call)
    both = call.err("op", name="hole", params={**HOLE_AT, "position": [0, 0]})
    assert "exactly one of position" in both and "got both" in both
    neither = call.err("op", name="hole", params={**HOLE_AT, "at": None})
    assert "got neither" in neither


def test_editing_a_hole_switches_between_at_and_position(call: Caller) -> None:
    plate(call)
    call.ok("op", name="hole", id="h", params=HOLE_AT)
    call.ok("op", edit="h", params={"at": [30, 10, 8]})
    assert volume(call) == pytest.approx(48000 - HOLE_AREA * 8)
    call.ok("op", edit="h", params={"position": [0, 0]})
    assert call.session.executor is not None
    hole = next(op for op in call.session.executor.plan.operations if op.id == "h")
    assert hole.at is None and hole.position == (0.0, 0.0)  # type: ignore[attr-defined]


def test_the_hole_reference_documents_at() -> None:
    reference = catalog.op_reference("hole")
    assert "at: [number x3]" in reference and "world point" in reference
    assert catalog.example_params("hole") is not None and "at" in catalog.example_params("hole")  # type: ignore[operator]


# --- sketch with world points ------------------------------------------------------------------------------------


def top_slot(**extra: object) -> dict[str, object]:
    slot = {"kind": "slot", "name": "s", "start": [30, 30, 8], "end": [70, 30, 8], "width": 10}
    return {"on": TOP, "frame": "world", "entities": [slot], **extra}


def test_a_world_sketch_on_a_face_lands_where_the_points_say(call: Caller) -> None:
    plate(call)
    text = call.ok("op", name="sketch", params=top_slot())
    assert "world frame: u=(1, 0, 0) v=(0, 1, 0)" in text.replace("[", "(").replace("]", ")")
    call.ok("op", name="cut", params={"sketch": "s3", "end_condition": "through_all"})
    assert volume(call) == pytest.approx(48000 - (40 * 10 + math.pi * 25) * 8, rel=1e-4)
    box = call.session.measure()[1]
    assert box.max == pytest.approx((100, 60, 8))


def test_the_stored_sketch_is_in_the_face_frame(call: Caller) -> None:
    plate(call)
    call.ok("op", name="sketch", id="w", params=top_slot())
    assert call.session.executor is not None
    stored = next(op for op in call.session.executor.plan.operations if op.id == "w")
    assert stored.entities[0].start == (-20.0, 0.0)  # type: ignore[attr-defined]
    assert not hasattr(stored, "frame")


def test_a_world_sketch_refuses_points_off_the_plane_with_the_distance(call: Caller) -> None:
    plate(call)
    off = top_slot()
    off["entities"][0]["end"] = [70, 30, 5]  # type: ignore[index]
    text = call.err("op", name="sketch", params=off)
    assert "s.end" in text and "3 mm off the sketch plane" in text


def test_a_world_sketch_wants_three_numbers_and_only_on_sketch(call: Caller) -> None:
    plate(call)
    flat = top_slot()
    flat["entities"][0]["end"] = [70, 30]  # type: ignore[index]
    assert "needs [x, y, z] mm points" in call.err("op", name="sketch", params=flat)
    assert "frame is a parameter of sketch only" in call.err("op", name="hole", params={**HOLE_AT, "frame": "world"})
    assert "frame is a parameter of sketch only" in call.err("op", name="sketch", params={**top_slot(), "frame": "x"})


def test_a_world_sketch_on_a_base_plane_maps_to_its_axes(call: Caller) -> None:
    call.ok("doc", action="new", name="d")
    rect = {"kind": "rect", "name": "r", "center": [10, 20, 0], "width": 8, "height": 6}
    call.ok("op", name="sketch", id="w", params={"on": XY, "frame": "world", "entities": [rect]})
    assert call.session.executor is not None
    stored = call.session.executor.plan.operations[0]
    assert stored.entities[0].center == (10.0, 20.0)  # type: ignore[attr-defined]
    off = {**rect, "center": [10, 20, 1]}
    assert "1 mm off the sketch plane" in call.err(
        "op", name="sketch", params={"on": XY, "frame": "world", "entities": [off]}
    )


def test_editing_a_sketch_does_not_take_a_frame(call: Caller) -> None:
    plate(call)
    assert "applies to a new sketch step" in call.err("op", edit="s1", params={"frame": "world"})


# --- the purpose of the document ---------------------------------------------------------------------------------


def test_the_purpose_defaults_to_detail_and_is_set_with_doc(call: Caller) -> None:
    call.ok("doc", action="new", name="d")
    assert "purpose: detail" in call.ok("doc", action="status")
    assert call.ok("doc", action="set_purpose", purpose="sketch") == "purpose: sketch"
    assert "purpose: sketch" in call.ok("doc", action="status")
    assert "purpose" in call.err("doc", action="set_purpose")


def test_the_purpose_survives_save_and_reopen(call: Caller, tmp_path: Path) -> None:
    plate(call)
    call.ok("doc", action="set_purpose", purpose="sketch")
    call.ok("doc", action="save", path="plate.fake")
    record = checkpoint.read(tmp_path / "plate.cotaforge.json")
    assert record is not None and record.purpose == "sketch"
    call.ok("doc", action="close")
    assert call.session.purpose == "detail"  # nothing leaks to the next document
    assert "purpose: sketch" in call.ok("doc", action="open", path="plate.fake")
    assert call.session.purpose == "sketch"


def test_a_record_without_a_purpose_means_detail(call: Caller, tmp_path: Path) -> None:
    plate(call)
    call.ok("doc", action="save", path="plate.fake")
    call.ok("doc", action="close")
    call.ok("doc", action="open", path="plate.fake")
    assert call.session.purpose == "detail"


# --- the hint on a boolean subtraction that is a hole ------------------------------------------------------------


def drilled_block(call: Caller, **cylinder: object) -> str:
    call.ok("doc", action="new", name="block")
    call.ok("op", name="box", id="b", params={"size": [100, 60, 20]})
    params = {"diameter_mm": 10, "height_mm": 30, "origin": [30, 30, -5], **cylinder}
    call.ok("op", name="cylinder", id="t", params=params)
    return call.ok(
        "op",
        name="boolean_subtract",
        id="c",
        params={"target": {"kind": "body", "from_op": "b"}, "tools": [{"kind": "body", "from_op": "t"}]},
    )


def test_a_cylinder_piercing_a_face_square_on_gets_the_hole_hint(call: Caller) -> None:
    text = drilled_block(call)
    assert "hint: this is a hole; use hole" in text
    example = next(line for line in text.splitlines() if line.startswith("hint:"))
    assert '"at":[30,30,20]' in example and '"diameter_mm":10' in example and '"end_condition":"through_all"' in example
    assert '"from_op":"b"' in example and '"normal":[0,0,1]' in example
    assert "doc(action=set_purpose, purpose=sketch)" in text


def test_the_hint_example_is_a_valid_hole(call: Caller) -> None:
    text = drilled_block(call)
    example = next(line for line in text.splitlines() if line.startswith("hint:"))
    import json  # noqa: PLC0415

    params = json.loads(example.split("params=", 1)[1][:-1])
    call.ok("undo")
    drilled = call.ok("op", name="hole", params=params)
    assert "valid: yes" in drilled
    tool_body = math.pi * 25 * 30  # still there: undo gave the tool back
    assert volume(call) == pytest.approx(100 * 60 * 20 + tool_body - math.pi * 25 * 20)


def test_a_blind_tool_hints_a_blind_hole(call: Caller) -> None:
    text = drilled_block(call, height_mm=12, origin=[30, 30, 8])
    assert '"end_condition":"blind"' in text and '"depth_mm":12' in text


def test_no_hint_in_a_sketch_document(call: Caller) -> None:
    call.ok("doc", action="new", name="block")
    call.ok("doc", action="set_purpose", purpose="sketch")
    call.ok("op", name="box", id="b", params={"size": [100, 60, 20]})
    call.ok("op", name="cylinder", id="t", params={"diameter_mm": 10, "height_mm": 30, "origin": [30, 30, -5]})
    text = call.ok(
        "op",
        name="boolean_subtract",
        params={"target": {"kind": "body", "from_op": "b"}, "tools": [{"kind": "body", "from_op": "t"}]},
    )
    assert "hint" not in text


def test_the_hint_follows_the_face_the_tool_enters(call: Caller) -> None:
    text = drilled_block(call, axis="X", origin=[-5, 30, 10])  # in through the face at x=0, whose normal is -X
    assert '"normal":[-1,0,0]' in text and '"at":[0,30,10]' in text


@pytest.mark.parametrize(
    "tool",
    [
        {"origin": [30, 30, 30], "height_mm": 5},  # nowhere near the material
        {"origin": [30, 30, 5], "height_mm": 5},  # a closed cavity: it never reaches a face
    ],
)
def test_no_hint_when_the_cylinder_is_not_a_hole(call: Caller, tool: dict[str, object]) -> None:
    assert "hint" not in drilled_block(call, **tool)


# --- wording and the recipe --------------------------------------------------------------------------------------


def test_box_and_cylinder_are_described_as_auxiliary_bodies() -> None:
    for name in ("box", "cylinder"):
        assert "auxiliary" in catalog.purpose(name) and "boolean tool" in catalog.purpose(name)


def test_the_recipe_runs_against_the_fake_and_every_call_is_valid(call: Caller) -> None:
    call.ok("doc", action="new", name="plate")
    for _comment, arguments in recipes.PLATE:
        call.ok("op", **arguments)
    names = [op.id for op in call.session.executor.plan.operations]  # type: ignore[union-attr]
    assert names == ["outline", "base", "slot_sk", "slot", "hole_a", "hole_b", "corners"]
    # 120 x 60 x 12 after the edits, the slot and the two (now D8 and D6) holes, corners rounded r5
    plate_area = 120 * 60 - (4 - math.pi) * 25
    slot_area = 40 * 10 + math.pi * 25
    expected = (plate_area - slot_area - math.pi * 16 - math.pi * 9) * 12
    assert volume(call) == pytest.approx(expected, rel=1e-3)


def test_the_recipe_topic_prints_the_same_calls(session: object) -> None:
    text = guide(session, "recipe")  # type: ignore[arg-type]
    for _comment, arguments in recipes.PLATE:
        assert recipes.call_text(arguments) in text
    assert 'frame":"world"' in text and '"at":[' in text and 'edit="base"' in text
