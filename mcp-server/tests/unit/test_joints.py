"""Joints through the tools: add_joint / set_joint as ops, the sweep as an inspect action, the wording of its result,
the refusals that keep a jointed component consistent, and a joint that survives save and reopen."""

import json
from pathlib import Path

from cotaforge.adapters.fake import FakeAdapter

from cotaforge_mcp.schemas import TOOLS, TOOLS_BY_NAME, ToolDef, input_schema
from cotaforge_mcp.session import Session
from tests.conftest import Caller, MakeSession, fake_without
from tests.unit.test_part_documents import cube_part, insert

RAIL = {"origin": [0, 0, 0], "direction": [1, 0, 0]}


def gate_and_post(call: Caller) -> None:
    """A 10 mm cube (the gate) and another 100 mm further along +X (the post), both instances of the same part."""
    cube_part(call, "cube")
    call.ok("doc", action="new", name="asm", kind="assembly")
    call.ok("doc", action="save", path="asm.fake")
    insert(call, "gate", "cube")
    insert(call, "post", "cube", (100, 0, 0))


def rail(call: Caller, **extra: object) -> str:
    params = {"type": "slider", "b": "gate", "axis": RAIL, "min": 0, "max": 200, **extra}
    return call.ok("op", name="add_joint", id="rail", params=params)


def test_a_slider_is_made_at_the_pose_the_component_has_and_set_joint_moves_it(call: Caller) -> None:
    gate_and_post(call)

    made = rail(call)
    moved = call.ok("op", name="set_joint", params={"joint": "rail", "value": 40})

    assert "joint rail (joint:1): slider" in made and "to the ground" in made
    assert "value 0 mm, limits [0, 200]" in made and "axis origin (0, 0, 0) direction (1, 0, 0)" in made
    assert "value 40 mm" in moved and "ok: s" in moved


def test_the_value_outside_the_limits_changes_nothing_and_says_the_limits(call: Caller) -> None:
    gate_and_post(call)
    rail(call)

    text = call.err("op", name="set_joint", params={"joint": "rail", "value": 250})

    assert "outside the limits of joint joint:1: [0, 200] mm" in text
    assert "value 0 mm" in call.ok("op", name="set_joint", params={"joint": "rail", "value": 0})


def test_a_jointed_instance_is_not_placed_by_hand_and_undo_gives_it_back(call: Caller) -> None:
    gate_and_post(call)
    rail(call)

    text = call.err("op", name="place_component", params={"component": "gate", "placement": {"origin": [1, 2, 3]}})
    assert "joined by joint joint:1" in text and "set_joint" in text

    call.ok("undo")  # the joint
    call.ok("op", name="place_component", params={"component": "gate", "placement": {"origin": [1, 2, 3]}})


def test_the_ops_say_what_a_joint_needs(call: Caller) -> None:
    gate_and_post(call)

    assert "needs the axis" in call.err("op", name="add_joint", params={"type": "slider", "b": "gate"})
    assert "no axis" in call.err("op", name="add_joint", params={"type": "fixed", "b": "gate", "axis": RAIL})
    assert "unknown or later operation" in call.err("op", name="set_joint", params={"joint": "nope", "value": 1})
    assert "joint" in call.err("op", name="set_joint", params={"joint": "gate", "value": 1})  # an insert, not a joint


def test_the_sweep_names_the_first_overlap_the_step_and_what_it_did_not_check(call: Caller) -> None:
    gate_and_post(call)
    rail(call)

    text = call.ok("inspect", action="sweep", id="rail", range=[0, 150, 5])

    assert "sweep rail (slider, moves comp:1 [gate]): 0 mm to 150 mm every 5 mm: 31 positions checked" in text
    assert "only those, not what lies between them" in text
    assert "first overlap: at 95 mm: comp:1 [gate] x comp:2 [post] share 500 mm3" in text
    assert "OVERLAPS from 95 mm" in text and "the joint is back at 0 mm" in text
    assert "no collision" not in text.lower() and "collision-free" not in text.lower()
    assert "value 0 mm" in call.ok("op", name="set_joint", params={"joint": "rail", "value": 0})  # untouched


def test_a_sweep_without_an_overlap_reports_the_least_clearance_and_where(call: Caller) -> None:
    gate_and_post(call)
    rail(call)

    text = call.ok("inspect", action="sweep", id="rail", range=[0, 60, 10])

    assert "no overlap at any position checked" in text
    assert "least clearance 30 mm at 60 mm" in text


def test_a_sweep_can_run_backwards_and_a_single_position_is_a_check(call: Caller) -> None:
    gate_and_post(call)
    rail(call)

    assert "61 positions" not in call.ok("inspect", action="sweep", id="rail", range=[100, 0, 50])
    one = call.ok("inspect", action="sweep", id="rail", range=[0, 0, 1])
    assert "1 positions checked" in one


def test_the_sweep_is_capped_with_a_clear_message(call: Caller) -> None:
    gate_and_post(call)
    rail(call, max=1000)

    text = call.err("inspect", action="sweep", id="rail", range=[0, 400, 0.5])

    assert "801 positions" in text and "the most is 721" in text and "raise the step or shorten the range" in text
    assert "721 positions" not in call.err("inspect", action="sweep", id="rail", range=[0, 400, 0.1])
    assert "too many" in call.err("inspect", action="sweep", id="rail", range=[0, 1e12, 1e-9])


def test_the_sweep_asks_for_what_it_needs(call: Caller) -> None:
    gate_and_post(call)
    rail(call)

    assert "needs the joint" in call.err("inspect", action="sweep", range=[0, 10, 1])
    assert "not an add_joint step" in call.err("inspect", action="sweep", id="gate", range=[0, 10, 1])
    assert "range=[from, to, step]" in call.err("inspect", action="sweep", id="rail")
    assert "above 0" in call.err("inspect", action="sweep", id="rail", range=[0, 10, 0])
    assert "outside the limits" in call.err("inspect", action="sweep", id="rail", range=[0, 300, 10])


def test_a_cad_without_joints_neither_offers_the_sweep_nor_teaches_it(call: Caller, make: MakeSession) -> None:
    plain = Caller(make(fake_without("sweep_joint", "add_joint", "set_joint")))

    offered = input_schema(TOOLS_BY_NAME["inspect"], call.session)["properties"]["action"]["enum"]
    plain_offered = input_schema(TOOLS_BY_NAME["inspect"], plain.session)["properties"]["action"]["enum"]
    assert "sweep" in offered and "sweep" not in plain_offered
    plain.ok("doc", action="new", name="asm", kind="assembly")
    assert "add_joint" not in plain.ok("cad_guide", topic="assembly")
    assert "op 'add_joint' is not supported" in plain.err("op", name="add_joint", params={"type": "fixed", "b": "x"})


def test_the_assembly_guide_teaches_joints_and_the_sweep(call: Caller) -> None:
    text = call.ok("cad_guide", topic="assembly")

    for needle in ("add_joint", "set_joint", 'inspect(action="sweep"', "FIRST position checked", "never what lies"):
        assert needle in text, needle
    assert "checked every 1 degree" in text and 'not "no collision"' in text
    assert "run_native" in text  # said not to hand-write the loop
    assert "add_joint" in call.ok("cad_guide", topic="op:add_joint")
    assert "ground" in call.ok("cad_guide", topic="op:add_joint")


def test_a_joint_survives_save_and_reopen_with_its_step_id_and_value(
    call: Caller, make: MakeSession, tmp_path: Path
) -> None:
    gate_and_post(call)
    rail(call)
    call.ok("op", name="set_joint", params={"joint": "rail", "value": 40})
    call.ok("doc", action="save")

    fresh = Caller(make(FakeAdapter()))
    fresh.ok("doc", action="open", path="asm.fake")

    text = fresh.ok("op", name="set_joint", params={"joint": "rail", "value": 70})
    assert "joint rail (joint:1): slider" in text and "value 70 mm" in text
    swept = fresh.ok("inspect", action="sweep", id="rail", range=[0, 150, 10])
    assert "first overlap: at 100 mm" in swept
    assert json.loads((tmp_path / "asm.fake").read_text(encoding="utf-8"))  # the file is still plain JSON


def test_the_schema_cost_of_the_sweep_stays_small(session: Session) -> None:
    def cost(tool: ToolDef) -> int:
        return len(json.dumps(input_schema(tool, session), separators=(",", ":"))) + len(tool.description)

    assert (
        cost(TOOLS_BY_NAME["inspect"]) < 1650
    )  # 1593 with the draft action (+300, measured); 1302 with the sweep, +51 with the edge-set preview (where)
    # 6777 with develop's catalog tool, set_purpose and overwrite merged in; 7501 with develop's drawing tool,
    # bom and sheet-metal ops on top (the sum of both sides' legitimate additions); 7578 with doc import and
    # inspect holes (+77, measured)
    # 7776 with validity (+11) and import sew (+187); 8220 with feature edit/delete, reload, focus
    assert (
        sum(cost(t) for t in TOOLS if t.visible(session)) < 10200
    )  # 9394 with the convention action; +56 brief language; +~50 set_title
    # 8220 with feature edit, reload, focus; 8724 with memory v2; 9025 measured with the native drawing sheet
    # (unchanged: the drawing tool is not in this session; on its own it went 1932 -> 2184 characters);
    # +21 with memory M1.1 (related, replaces, sources and two provenance sources in the memory tool): 9046
