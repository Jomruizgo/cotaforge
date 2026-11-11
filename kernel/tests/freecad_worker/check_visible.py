"""Headless side of the visible-worker work: ``capture`` and ``set_view`` are refused, the live ``feature_tree``
scan lists hand-made objects, and nothing the worker itself creates is mistaken for a hand-made feature."""

import os
import tempfile

import main
from harness import build_part01, build_part03, build_part06, call, expect_error, new_document, op


def check_hello_reports_headless_and_capture_is_not_supported() -> None:
    assert main.WORKER.hello()["gui"] is False
    new_document("p")
    path = os.path.join(tempfile.gettempdir(), f"cadh_capture_{os.getpid()}.png")
    exc = expect_error("not_supported", "capture", op("cap", path=path, width=640, height=480))
    assert "headless" in exc.message and exc.recoverable is False
    assert not os.path.exists(path)


def check_set_view_is_not_supported_headless() -> None:
    new_document("p")
    for name in ("iso", "front", "top", "right", "rear", "left", "bottom", "dimetric", "trimetric"):
        exc = expect_error("not_supported", "set_view", op("view", name=name))
        assert "headless" in exc.message and exc.recoverable is False
    # headless answers not_supported before it looks at the name: no GUI, no view to orient
    expect_error("not_supported", "set_view", op("view", name="oblique"))
    expect_error("not_supported", "set_view", op("view", bodies=[{"id": "body:1"}]))  # a fit, no orientation


def check_style_and_section_are_not_supported_headless() -> None:
    build_part01()
    body = {"id": "body:1"}
    exc = expect_error("not_supported", "style_bodies", op("s", bodies=[body], transparency=50))
    assert "headless" in exc.message and exc.recoverable is False
    expect_error("not_supported", "style_bodies", op("s", bodies=[body], color="#ff0000", visible=False))
    exc = expect_error("not_supported", "section_plane", op("s", axis="z", offset_mm=3.0))
    assert "headless" in exc.message
    expect_error("not_supported", "section_plane", op("s", axis=None))  # removing a plane needs the window too


def check_set_view_without_a_document_is_no_active_document() -> None:
    expect_error("no_active_document", "set_view", op("view", name="iso"))
    expect_error("no_active_document", "style_bodies", op("s", bodies=[{"id": "body:1"}], visible=True))
    expect_error("no_active_document", "section_plane", op("s", axis="x", offset_mm=0.0))


def check_feature_tree_lists_a_hand_made_object_flagged_manual() -> None:
    build_part01()
    before = call("feature_tree", op("t"))["nodes"]
    assert [n["handle"]["id"] for n in before] == ["feature:1", "feature:2", "feature:3"]
    doc = main.WORKER.active.fc_doc
    hand = doc.addObject("Part::Box", "HandBox")  # what the engineer does in the GUI: not through the worker
    hand.Label = "By hand"
    doc.recompute()
    after = call("feature_tree", op("t"))["nodes"]
    assert after[:3] == before, "registered features keep their order and content"
    assert len(after) == 4
    node = after[3]
    assert node == {
        "handle": {"id": "feature:manual:HandBox"},
        "kind": "Part::Box",
        "name": "By hand",
        "parameters": {"manual": True, "object": "HandBox"},
        "named_dimensions": {},
        "parent": None,
    }
    # a hand-made PartDesign feature inside the worker's body is listed too; the body itself is a container
    body = doc.getObject(main.WORKER.active.bodies["body:1"].fc_body)
    hand_pad = body.newObject("PartDesign::Pad", "HandPad")
    hand_pad.Profile = doc.getObject(main.WORKER.active.sketches["sketch:1"].fc_name)
    hand_pad.Length = 1.0
    doc.recompute()
    kinds = [(n["kind"], n["parameters"].get("manual", False)) for n in call("feature_tree", op("t"))["nodes"]]
    assert kinds[3:] == [("Part::Box", True), ("PartDesign::Pad", True)]
    assert all(k != "PartDesign::Body" for k, _ in kinds)


def check_worker_made_objects_are_never_reported_as_manual() -> None:
    """Sketches, bodies, datum lines, patterns, components: every object the worker creates is registered."""
    for build in (build_part01, build_part03, build_part06):
        main.reset()
        build()
        for doc_id in list(main.WORKER.documents):
            call("set_active_document", op("d", document={"id": doc_id}))
            nodes = call("feature_tree", op("t"))["nodes"]
            manual = [n for n in nodes if n["parameters"].get("manual")]
            assert manual == [], (build.__name__, doc_id, manual)
            assert len(nodes) == len(main.WORKER.documents[doc_id].features)
