"""Each part in its own document and axes, placed in an assembly: several documents open at once, ``switch``,
instances of a saved part with a placement, interference with distance, and an assembly that reopens with its parts."""

import json
from pathlib import Path

import pytest
from cotaforge.adapters.fake import FakeAdapter

from cotaforge_mcp import checkpoint
from cotaforge_mcp.schemas import TOOLS, TOOLS_BY_NAME, ToolDef, input_schema
from cotaforge_mcp.session import Session
from tests.conftest import Caller, MakeSession
from tests.unit.test_tools import volume_of


def cube_part(call: Caller, name: str, side: float = 10.0, *, save: bool = True) -> None:
    call.ok("doc", action="new", name=name)
    call.ok("op", name="box", id=f"{name}_box", params={"size": [side, side, side]})
    if save:
        call.ok("doc", action="save", path=f"{name}.fake")


def insert(call: Caller, instance: str, part: str, origin: tuple[float, float, float] = (0, 0, 0)) -> str:
    return call.ok(
        "op", name="insert_component", id=instance, params={"part": part, "placement": {"origin": list(origin)}}
    )


def assembly_of_two_cubes(call: Caller) -> None:
    cube_part(call, "cube")
    call.ok("doc", action="new", name="asm", kind="assembly")
    call.ok("doc", action="save", path="asm.fake")
    insert(call, "a", "cube")
    insert(call, "b", "cube", (30, 0, 0))


# --- several documents open at once -------------------------------------------------------------------------------


def test_a_second_document_parks_the_first_and_switch_brings_it_back(call: Caller) -> None:
    cube_part(call, "cube", 10)
    created = call.ok("doc", action="new", name="asm", kind="assembly")
    assert "document: asm (assembly)" in created and "other open documents: cube (part)" in created

    status = call.ok("doc", action="status")
    assert "documents: 2 open" in status and "asm (assembly) [active]" in status and "cube (part): cube.fake" in status

    back = call.ok("doc", action="switch", name="cube")
    assert back.startswith("active: cube (part)") and "other open documents: asm (assembly)" in back
    assert volume_of(back) == pytest.approx(1000)  # its own steps, untouched
    assert "cube_box" in call.ok("inspect", action="tree") or "box" in call.ok("inspect", action="tree")
    assert call.ok("undo").startswith("undone: cube_box box")  # the history of a parked document is kept


def test_switch_names_what_is_open_when_the_name_is_wrong(call: Caller) -> None:
    cube_part(call, "cube")
    text = call.err("doc", action="switch", name="nope")
    assert "no open document named 'nope'" in text and "open: cube" in text
    assert "switch needs the name" in call.err("doc", action="switch")
    assert "already the active document" in call.ok("doc", action="switch", name="cube")


def test_two_documents_cannot_share_a_name_or_a_file(call: Caller) -> None:
    cube_part(call, "cube")
    assert "already open" in call.err("doc", action="new", name="cube")
    assert "already open as 'cube'" in call.err("doc", action="open", path="cube.fake")
    call.ok("doc", action="new", name="other")
    assert 'doc(action="switch", name="cube")' in call.err("doc", action="open", path="cube.fake")


def test_a_failed_open_leaves_the_active_document_active(call: Caller, tmp_path: Path) -> None:
    cube_part(call, "cube")
    (tmp_path / "junk.fake").write_text("junk", encoding="utf-8")
    assert "could not be opened" in call.err("doc", action="open", path="junk.fake")
    assert "document: cube (part)" in call.ok("doc", action="status")
    assert volume_of(call.ok("inspect", action="measure")) == pytest.approx(1000)


# --- instances ----------------------------------------------------------------------------------------------------


def test_insert_component_places_instances_of_a_saved_part_and_saves_the_assembly(call: Caller, tmp_path: Path) -> None:
    cube_part(call, "cube")
    call.ok("doc", action="new", name="asm", kind="assembly")

    first = insert(call, "a", "cube")
    assert first.startswith("saved the assembly first") and str(tmp_path / "asm.fakecad.json") in first
    assert (tmp_path / "asm.fakecad.json").is_file()
    assert "ok: a insert_component" in first and volume_of(first) == pytest.approx(1000)
    second = insert(call, "b", "cube", (30, 0, 0))  # the same part again: a second instance
    assert "saved the assembly first" not in second and volume_of(second) == pytest.approx(2000)
    assert "UNSAVED" in call.ok("doc", action="status")


def test_the_insert_counts_the_faces_and_edges_of_the_part_and_lists_none(call: Caller) -> None:
    cube_part(call, "cube")
    call.ok("doc", action="new", name="asm", kind="assembly")

    text = insert(call, "a", "cube")

    assert "created: 6 faces, 12 edges, 1 bodies" in text, text
    assert "component comp:1 [a]: the faces and edges are the part's own, counted here and not listed" in text
    assert "comp:1/face:12" in text  # the form of their ids, as an example
    assert text.count("comp:1/face:") == 1, text  # no face of the part is named one by one


def test_place_component_sets_the_pose_and_does_not_add_to_it(call: Caller) -> None:
    assembly_of_two_cubes(call)

    moved = call.ok(
        "op", name="place_component", params={"component": "b", "placement": {"origin": [0, 20, 0]}}, id="move_b"
    )
    again = call.ok(
        "op", name="place_component", params={"component": "b", "placement": {"origin": [0, 20, 0]}}, id="move_b2"
    )

    assert moved.startswith("ok: move_b place_component") and again.startswith("ok: move_b2 place_component")
    assert "bbox: size (10, 30, 10) mm, min (0, 0, 0), max (10, 30, 10)" in again  # b sits at y 20..30, not 40..50
    assert call.ok("undo").startswith("undone: move_b2 place_component")


def test_insert_component_refuses_what_it_cannot_link_to(call: Caller) -> None:
    cube_part(call, "saved")
    cube_part(call, "loose", save=False)
    call.ok("doc", action="new", name="asm", kind="assembly")

    unknown = call.err("op", name="insert_component", params={"part": "pin", "placement": {}})
    assert "'pin' is not an open part document" in unknown and "open parts: saved, loose" in unknown
    assert "was not applied" not in unknown  # refused before it reached the executor

    unsaved = call.err("op", name="insert_component", params={"part": "loose", "placement": {}})
    assert "never been saved" in unsaved and 'doc(action="switch", name="loose")' in unsaved
    assert "document: asm (assembly)" in call.ok("doc", action="status")


def test_insert_component_in_a_part_says_to_make_an_assembly(call: Caller) -> None:
    cube_part(call, "cube")
    call.ok("doc", action="new", name="other")
    text = call.err("op", name="insert_component", params={"part": "cube", "placement": {}})
    assert "works in an assembly" in text and 'kind="assembly"' in text


def test_a_part_with_unsaved_changes_is_flagged_on_insert_and_on_save(call: Caller) -> None:
    cube_part(call, "cube")
    call.ok("op", name="box", id="more", params={"size": [2, 2, 2]})  # the part is dirty again
    call.ok("doc", action="new", name="asm", kind="assembly")
    inserted = insert(call, "a", "cube")
    assert "warning: part 'cube' has unsaved changes" in inserted
    assert "warning: part 'cube' has unsaved changes" in call.ok("doc", action="save")


# --- interference between components ------------------------------------------------------------------------------


def test_interference_between_components_uses_the_format_of_the_bodies_of_a_part(call: Caller) -> None:
    assembly_of_two_cubes(call)
    call.ok("op", name="place_component", params={"component": "b", "placement": {"origin": [5, 0, 0]}}, id="c1")
    overlap = call.ok("inspect", action="interference")
    assert overlap.startswith("interference: 2 components, 1 pair: 1 overlap (500 mm3)")
    assert "comp:1 [a] x comp:2 [b]: OVERLAP 500 mm3" in overlap

    call.ok("op", name="place_component", params={"component": "b", "placement": {"origin": [10, 0, 0]}}, id="c2")
    assert "touching (no common volume)" in call.ok("inspect", action="interference")

    call.ok("op", name="place_component", params={"component": "b", "placement": {"origin": [25, 0, 0]}}, id="c3")
    clear = call.ok("inspect", action="interference")
    assert clear.startswith("interference: 2 components, 1 pair: 0 overlap, 1 clear")
    assert "comp:1 [a] x comp:2 [b]: clear, min distance 15 mm" in clear


def test_interference_in_an_assembly_with_one_component_is_not_checked(call: Caller) -> None:
    cube_part(call, "cube")
    call.ok("doc", action="new", name="asm", kind="assembly")
    insert(call, "a", "cube")
    assert "not checked: the assembly has fewer than two components" in call.ok("inspect", action="interference")


# --- a part changed after it was inserted -------------------------------------------------------------------------


def test_a_part_changed_and_saved_updates_the_assembly_when_it_is_switched_to(call: Caller) -> None:
    assembly_of_two_cubes(call)
    call.ok("doc", action="save")
    call.ok("doc", action="switch", name="cube")
    call.ok("op", name="box", id="boss", params={"size": [5, 5, 5], "placement": {"origin": [20, 0, 0]}})
    saved = call.ok("doc", action="save")
    assert "used by the open assembly asm" in saved

    back = call.ok("doc", action="switch", name="asm")
    assert "components updated from their part documents: volume was 2000 mm3, now 2250 mm3" in back
    assert "UNSAVED" in call.ok("doc", action="status")
    assert call.ok("doc", action="switch", name="cube").startswith("active: cube")
    again = call.ok("doc", action="switch", name="asm")
    assert "up to date with their part documents" in again


# --- closing ------------------------------------------------------------------------------------------------------


def test_a_part_used_by_an_open_assembly_cannot_be_closed(call: Caller) -> None:
    assembly_of_two_cubes(call)
    call.ok("doc", action="switch", name="cube")
    refused = call.err("doc", action="close")
    assert "part of the open assembly asm" in refused and 'doc(action="switch", name="asm")' in refused
    call.ok("doc", action="switch", name="asm")
    call.ok("doc", action="save")
    closed = call.ok("doc", action="close")
    assert closed.startswith("closed: asm") and "other open documents: cube (part)" in closed
    call.ok("doc", action="switch", name="cube")
    assert call.ok("doc", action="close").startswith("closed: cube")  # nothing links to it any more


# --- save and resume ----------------------------------------------------------------------------------------------


def test_the_sidecar_of_an_assembly_records_its_parts_by_relative_path(call: Caller, tmp_path: Path) -> None:
    assembly_of_two_cubes(call)
    call.ok("doc", action="save")
    record = checkpoint.read(checkpoint.sidecar_path(tmp_path / "asm.fake"))
    assert record is not None and record.parts == {"cube": "cube.fake"}
    payload = json.loads((tmp_path / "asm.fake").read_text(encoding="utf-8"))
    assert payload["parts"] and list(payload["parts"].values()) == ["cube.fake"]  # relative, never absolute


def test_reopening_the_assembly_opens_its_parts_and_keeps_poses_and_step_ids(
    call: Caller, make: MakeSession, tmp_path: Path
) -> None:
    assembly_of_two_cubes(call)
    call.ok("op", name="place_component", params={"component": "b", "placement": {"origin": [0, 40, 0]}}, id="lift")
    call.ok("doc", action="save")

    fresh = Caller(make(FakeAdapter()))  # a new server: nothing is open
    opened = fresh.ok("doc", action="open", path="asm.fake")

    assert opened.startswith("document: asm (assembly) from asm.fake")
    assert "session restored: step ids and handles kept" in opened and "other open documents: cube (part)" in opened
    assert volume_of(opened) == pytest.approx(2000)
    assert "documents: 2 open" in fresh.ok("doc", action="status")
    # the steps keep working against the reopened parts: a new instance of the same part, placed by its step
    third = insert(fresh, "c", "cube", (0, 0, 50))
    assert volume_of(third) == pytest.approx(3000)
    moved = fresh.ok("op", name="place_component", params={"component": "b", "placement": {"origin": [0, 80, 0]}})
    assert "ok: s" in moved and "size (10, 90, 60) mm" in moved
    assert fresh.ok("doc", action="switch", name="cube").startswith("active: cube")  # the part is open and editable
    assert "cube_box" in fresh.ok("inspect", action="tree")


def test_a_part_that_changed_on_disk_shows_in_the_assembly_that_reopens(
    call: Caller, make: MakeSession, tmp_path: Path
) -> None:
    assembly_of_two_cubes(call)
    call.ok("doc", action="save")
    call.ok("doc", action="switch", name="cube")
    call.ok("op", name="box", id="boss", params={"size": [5, 5, 5], "placement": {"origin": [20, 0, 0]}})
    call.ok("doc", action="save")

    fresh = Caller(make(FakeAdapter()))
    opened = fresh.ok("doc", action="open", path="asm.fake")

    assert volume_of(opened) == pytest.approx(2250)  # the assembly is what its parts are now


def test_a_missing_part_file_is_named_and_the_assembly_stays_closed(
    call: Caller, make: MakeSession, tmp_path: Path
) -> None:
    assembly_of_two_cubes(call)
    call.ok("doc", action="save")
    (tmp_path / "cube.fake").unlink()

    fresh = Caller(make(FakeAdapter()))
    text = fresh.err("doc", action="open", path="asm.fake")

    assert "cube.fake" in text and "not at" in text and str(tmp_path / "cube.fake") in text
    assert "document: none" in fresh.ok("doc", action="status")


# --- what the agent is told ---------------------------------------------------------------------------------------


def test_the_assembly_guide_walks_the_workflow(call: Caller) -> None:
    text = call.ok("cad_guide", topic="assembly")
    for needle in ("OWN axes", 'kind="assembly"', "insert_component", "place_component", "interference", "switch"):
        assert needle in text, needle
    assert "assembly" in call.err("cad_guide", topic="nope")  # the topic is listed where the agent looks for help


def test_the_schema_of_the_doc_tool_stays_small(session: Session) -> None:
    def cost(tool: ToolDef) -> int:
        return len(json.dumps(input_schema(tool, session), separators=(",", ":"))) + len(tool.description)

    # 1095 before documents could be switched; 1364 with set_purpose and overwrite from develop
    assert (
        cost(TOOLS_BY_NAME["doc"]) < 2000
    )  # 1964 with the convention action (values, +204); 1625 with the sew options of import; 1782 with reload and force
    # 6777 with develop's catalog tool merged in; 7501 with develop's drawing tool, bom and sheet metal as well;
    # 7578 with doc import and inspect holes (+77, measured)
    # 7776 with validity (+11) and import sew (+187); 8220 with feature edit, reload, focus; 8724 with memory v2;
    # 9025 with view on=, sheet= and sheet items (measured); 9178 with the library in the catalog tool (the file
    # action, its entry fields and a clause of the description: +223, measured), so the ceiling is 9200
    assert (
        sum(cost(t) for t in TOOLS if t.visible(session)) < 10200
    )  # 9394 with the convention action; +56 brief language
    # 8220 with feature edit, reload, focus; 8724 with memory v2; 9025 measured with the native drawing sheet
    # (unchanged: the drawing tool is not in this session; on its own it went 1932 -> 2184 characters);
    # +21 with memory M1.1 (related, replaces, sources and two provenance sources in the memory tool): 9046
