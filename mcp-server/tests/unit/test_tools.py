"""Every tool and action on the fake adapter: happy paths and the shape of the errors."""

import re
from pathlib import Path

import pytest

from tests.conftest import PLATE_SKETCH, TOP, Caller, plate


def volume_of(text: str) -> float:
    match = re.search(r"volume: (-?[\d.]+) mm3", text)
    assert match, text
    return float(match.group(1))


# --- doc -------------------------------------------------------------------------------------------------------------


def test_doc_new_status_and_a_second_document_does_not_replace_the_first(call: Caller) -> None:
    assert "document: none" in call.ok("doc", action="status")
    created = call.ok("doc", action="new", name="plate")
    assert "document: plate (part)" in created and "volume: 0 mm3" in created
    status = call.ok("doc", action="status")
    assert "steps: 0" in status and "file: not saved yet" in status and "documents:" not in status
    second = call.ok("doc", action="new", name="other")
    assert "document: other (part)" in second and "other open documents: plate (part)" in second
    assert "already open" in call.err("doc", action="new", name="plate")


def test_doc_save_close_open_keeps_the_step_ids(call: Caller, tmp_path: Path) -> None:
    plate(call)
    assert "UNSAVED" in call.ok("doc", action="status")
    saved = call.ok("doc", action="save", path="plate.fake")
    assert str(tmp_path / "plate.fake") in saved
    assert (tmp_path / "plate.cotaforge.json").is_file()
    assert "UNSAVED" not in call.ok("doc", action="status")
    assert "closed: plate" in call.ok("doc", action="close")
    assert "document: none" in call.ok("doc", action="status")

    opened = call.ok("doc", action="open", path="plate.fake")
    assert "session restored: step ids and handles kept" in opened and "drift" not in opened
    assert "s1: sketch" in opened and "s2: extrude" in opened
    assert volume_of(opened) == pytest.approx(48000)
    assert "[s2]" in call.ok("inspect", action="tree")


def test_close_refuses_unsaved_changes_unless_discarded(call: Caller) -> None:
    plate(call)
    refused = call.err("doc", action="close")
    assert "unsaved" in refused and "discard=true" in refused
    assert "closed: plate" in call.ok("doc", action="close", discard=True)


def test_open_errors_are_actionable(call: Caller, tmp_path: Path) -> None:
    assert "path" in call.err("doc", action="open")
    assert "does not exist" in call.err("doc", action="open", path="nope.FCStd")
    (tmp_path / "x.fake").write_text("junk", encoding="utf-8")
    text = call.err("doc", action="open", path="x.fake")
    assert "could not be opened" in text and "Traceback" not in text
    assert "document: none" in call.ok("doc", action="status")


def test_new_requires_a_name(call: Caller) -> None:
    text = call.err("doc", action="new")
    assert "field: name" in text and "usage:" in text and "example:" in text


# --- op --------------------------------------------------------------------------------------------------------------


def test_op_returns_measured_facts_and_assigns_ids(call: Caller) -> None:
    call.ok("doc", action="new", name="plate")
    sketch = call.ok("op", name="sketch", params=PLATE_SKETCH, intent="outline")
    assert sketch.startswith("ok: s1 sketch") and "area 6000 mm2" in sketch
    extrude = call.ok("op", name="extrude", params={"sketch": "s1", "depth_mm": 8})
    assert extrude.startswith("ok: s2 extrude")
    assert volume_of(extrude) == pytest.approx(48000) and "(+48000)" in extrude
    assert "size (100, 60, 8) mm" in extrude and "valid: yes" in extrude and "created: 6 faces" in extrude
    named = call.ok(
        "op",
        name="hole",
        id="drill",
        params={
            "face": TOP,
            "position": [0, 0],
            "standard": "simple",
            "diameter_mm": 6,
            "end_condition": "through_all",
        },
    )
    assert named.startswith("ok: drill hole")
    assert volume_of(named) == pytest.approx(48000 - 3.14159265358979 * 9 * 8, abs=1e-3)
    assert call.ok("op", name="box", params={"size": [1, 1, 1]}).startswith("ok: s3 box")  # auto ids skip nothing used


def test_intent_is_free_text_the_server_records(call: Caller) -> None:
    call.ok("doc", action="new", name="plate")
    call.ok("op", name="sketch", params=PLATE_SKETCH, intent="the plate outline, 100 x 60")
    call.ok("op", name="box", params={"size": [1, 1, 1]})
    intents = [op.intent for op in call.session.executor.plan.operations]  # type: ignore[union-attr]
    assert intents == ["the plate outline, 100 x 60", "box s2"]


def test_op_results_stay_short(call: Caller) -> None:
    plate(call)
    assert len(call.ok("op", name="box", params={"size": [5, 5, 5]})) < 1500


def test_invalid_params_report_field_shape_and_example(call: Caller) -> None:
    call.ok("doc", action="new", name="p")
    text = call.err("op", name="extrude", params={"sketch": "s1", "depth_mm": -3})
    assert "field: params.depth_mm" in text
    assert 'usage: op(name="extrude"' in text and "depth_mm: number" in text
    assert 'example: op(name="extrude"' in text
    assert "Traceback" not in text
    missing = call.err("op", name="extrude", params={})
    assert "params.sketch" in missing and "params.depth_mm" in missing
    extra = call.err("op", name="extrude", params={"sketch": "s1", "depth_mm": 3, "bogus": 1})
    assert "params.bogus" in extra


def test_unknown_op_lists_close_names(call: Caller) -> None:
    call.ok("doc", action="new", name="p")
    text = call.err("op", name="extrud", params={})
    assert "close names:" in text and "extrude" in text
    spanish = call.err("op", name="extruir corte", params={})
    assert "cut" in spanish


def test_bad_ids_are_rejected(call: Caller) -> None:
    plate(call)
    assert "not a valid step id" in call.err("op", name="box", id="Bad-Id", params={"size": [1, 1, 1]})
    assert "already used" in call.err("op", name="box", id="s1", params={"size": [1, 1, 1]})


def test_the_kernel_intent_links_are_not_a_parameter(call: Caller) -> None:
    call.ok("doc", action="new", name="p")
    text = call.err("op", name="box", params={"size": [1, 1, 1], "links": [{"kind": "brief", "ref": "x"}]})
    assert "params.links" in text and "intent" in text


def test_a_failing_step_changes_nothing_and_the_session_continues(call: Caller) -> None:
    plate(call)
    before = call.ok("doc", action="status")
    bad_face = {"kind": "face", "from_op": "s2", "select": {"normal": [0, 1, 1]}}
    failed = call.err(
        "op",
        name="hole",
        params={
            "face": bad_face,
            "position": [0, 0],
            "standard": "simple",
            "diameter_mm": 6,
            "end_condition": "through_all",
        },
    )
    assert "was not applied" in failed and "state: unchanged" in failed
    assert call.ok("doc", action="status").split("steps:")[1] == before.split("steps:")[1]
    again = call.ok(
        "op",
        name="hole",
        id="s3",
        params={
            "face": TOP,
            "position": [0, 0],
            "standard": "simple",
            "diameter_mm": 6,
            "end_condition": "through_all",
        },
    )
    assert again.startswith("ok: s3 hole")


def test_forward_reference_is_a_plan_error(call: Caller) -> None:
    call.ok("doc", action="new", name="p")
    text = call.err("op", name="extrude", params={"sketch": "nope", "depth_mm": 3})
    assert "unknown or later operation" in text


def test_op_without_a_document(call: Caller) -> None:
    text = call.err("op", name="box", params={"size": [1, 1, 1]})
    assert "no document is open" in text and "doc(action" in text


# --- inspect ---------------------------------------------------------------------------------------------------------


def test_inspect_everything_on_a_plate(call: Caller) -> None:
    plate(call)
    tree = call.ok("inspect", action="tree")
    assert "extrude" in tree and "[s2]" in tree
    faces = call.ok("inspect", action="faces")
    assert faces.startswith("faces: 6")
    top = call.ok("inspect", action="faces", normal=[0, 0, 1])
    assert "faces: 1" in top and "A=6000 mm2" in top and "by=s2" in top
    face_id = re.search(r"(face:\d+)", top).group(1)  # type: ignore[union-attr]
    detail = call.ok("inspect", action="face", id=face_id)
    assert face_id in detail and "bbox:" in detail
    edges = call.ok("inspect", action="edges", face=face_id)
    assert edges.startswith("edges: 4") and "L=100" in edges
    edge_id = re.search(r"(edge:\d+)", edges).group(1)  # type: ignore[union-attr]
    assert edge_id in call.ok("inspect", action="edge", id=edge_id)
    props = call.ok("inspect", action="properties")
    assert volume_of(props) == pytest.approx(48000) and "centroid: (50, 30, 4)" in props
    measure = call.ok("inspect", action="measure")
    assert "size (100, 60, 8) mm" in measure and "body:" in measure
    other = re.findall(r"face:\d+", faces)
    between = call.ok("inspect", action="edges", between=[other[0], other[1]])
    assert between.startswith("edges:")


def test_inspect_sketch_status_and_filters(call: Caller) -> None:
    plate(call)
    status = call.ok("inspect", action="sketch_status", sketch="s1")
    assert "sketch s1:" in status
    assert call.ok("inspect", action="faces", op="s2", surface="plane", limit=2).count("face:") == 2
    assert "no step or adopted feature" in call.err("inspect", action="faces", op="s9")


def test_inspect_errors_are_actionable(call: Caller) -> None:
    plate(call)
    assert "usage:" in call.err("inspect", action="edges")
    assert "field: id" in call.err("inspect", action="face")
    bad = call.err("inspect", action="nope")
    assert "field: action -> must be one of: tree, faces" in bad
    assert "field: limit" in call.err("inspect", action="faces", limit=0)


def test_inspect_without_document(call: Caller) -> None:
    assert "no document is open" in call.err("inspect", action="tree")


# --- view / export ---------------------------------------------------------------------------------------------------


def test_view_show_and_selection(call: Caller) -> None:
    plate(call)
    assert call.ok("view", action="selection") == "selection: empty"
    shown = call.ok("view", action="show", ids=["face:6"], note="top")
    assert shown.startswith("shown: highlight")
    assert "ids" in call.err("view", action="show")
    assert "not a face or edge id" in call.err("view", action="show", ids=["body:1"])


def test_view_capture_is_not_supported_without_a_window(call: Caller) -> None:
    plate(call)
    text = call.err("view", action="capture")
    assert "not available" in text


def test_export_is_hidden_when_the_cad_cannot_export(call: Caller) -> None:
    assert "unknown tool" in call.err("export", action="step")


# --- undo ------------------------------------------------------------------------------------------------------------


def test_undo_last_and_to_a_step(call: Caller) -> None:
    plate(call)
    call.ok("op", name="box", params={"size": [10, 10, 10]})
    last = call.ok("undo")
    assert last.startswith("undone: s3 box") and "steps now: 2, last s2 extrude" in last
    assert volume_of(last) == pytest.approx(48000)
    call.ok("op", name="box", params={"size": [10, 10, 10]})
    back = call.ok("undo", to="s1")
    assert "s4 box" in back and "s2 extrude" in back and "steps now: 1, last s1 sketch" in back
    assert volume_of(back) == pytest.approx(0)
    everything = call.ok("undo", to="start")
    assert "s1 sketch" in everything and "steps now: 0" in everything
    assert "nothing to undo" in call.err("undo")


def test_undo_errors(call: Caller) -> None:
    plate(call)
    text = call.err("undo", to="zzz")
    assert "steps: s1, s2" in text and "field: to" in text
    assert "already the last step" in call.err("undo", to="s2")


def test_redo_with_the_same_id_after_an_undo(call: Caller) -> None:
    plate(call)
    box = {"size": [10, 10, 10]}
    call.ok("op", name="box", id="b", params=box)
    call.ok("undo")
    again = call.ok("op", name="box", id="b", params={"size": [20, 20, 20]})
    assert volume_of(again) == pytest.approx(48000 + 8000)
    assert call.ok("undo").startswith("undone: b box")


# --- guide / unknown tool --------------------------------------------------------------------------------------------


def test_guide_topics_and_errors(call: Caller) -> None:
    assert call.ok("cad_guide").startswith("CAD: fake")
    assert "extrude(sketch, depth_mm)" in call.ok("cad_guide", topic="ops")
    assert "extruir corte" in call.ok("cad_guide", topic="terms")
    assert "exactly ONE" in call.ok("cad_guide", topic="traps")
    assert "not available with fake" in call.ok("cad_guide", topic="native")
    bad = call.err("cad_guide", topic="wat")
    assert "topics:" in bad and "example:" in bad
    unknown = call.err("cad_guide", topic="op:extrud")
    assert "close names: extrude" in unknown


def test_unknown_tool_and_unexpected_arguments(call: Caller) -> None:
    assert "unknown tool" in call.err("nope")
    text = call.err("doc", action="status", bogus=1)
    assert "field: bogus" in text and "usage:" in text
