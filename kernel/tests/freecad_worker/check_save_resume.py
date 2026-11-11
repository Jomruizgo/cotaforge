"""``open_document``: reopen a saved document and re-bind the handle registry.

Plain asserts run inside ``freecadcmd`` (see ``run.py``) on both supported FreeCAD lines. Each check saves a live
document with ``save_document``, RESETS the worker (``main.reset``: the registry and every open document are gone, as
in a new worker process), reopens the file with ``open_document`` and the ``adapter_state`` of that save, and compares:
the registry dump (every face/edge/vertex id at its element index with its signature), the descriptors of every live
face, the mass, and -- the point of resuming -- that an in-place edit or a new operation after the reopen gives
bit-identical geometry to the same change made without the save/reopen. A hand edit of the file is drift; a damaged
file is a structured ``corrupt`` error that leaves the file alone.
"""

import contextlib
import hashlib
import os
import shutil
import tempfile
from collections.abc import Iterator
from typing import Any

import FreeCAD as App
import main
import persist
from check_edit_in_place import CASES, assert_same_shapes, build, edit, frame_plan, shapes_of, with_changes
from check_undo_depth import active_fc, active_rec
from harness import build_part01, build_part03, build_part04, call, expect_error, face_info, faces_of, op, select_face

STAMP = {
    "design_id": "d",
    "plan_id": "p",
    "plan_revision": 1,
    "save_seq": 3,
    "last_step_index": 4,
    "harness_version": "0.0.0",
    "plan_sha256": "a" * 64,
    "sidecar_sha256": None,
}


@contextlib.contextmanager
def workdir() -> Iterator[str]:
    path = tempfile.mkdtemp(prefix="cadh-resume-")
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def sha256(path: str) -> str:
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def save_and_reset(directory: str, name: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    """Save the active document, then reset the worker as a new process would start: returns (path, adapter_state,
    the registry dump of the live document before the reset)."""
    path = os.path.join(directory, f"{name}.FCStd")
    result = call("save_document", op("save", path=path, stamp=STAMP))
    before = persist.dump(active_rec())
    main.reset()
    assert main.WORKER.active is None and not main.WORKER.documents
    return path, result["adapter_state"], before


def reopen(path: str, state: dict[str, Any]) -> dict[str, Any]:
    return call("open_document", op("open", path=path, adapter_state=state))


def without_journals(dump: dict[str, Any]) -> dict[str, Any]:
    return {**dump, "features": [{k: v for k, v in f.items() if k != "has_journal"} for f in dump["features"]]}


def first_difference(a: Any, b: Any, where: str = "", tol: float = 0.0) -> str | None:
    """Where two JSON-like values first differ (``None``: nowhere); floats within ``tol`` (relative, at least
    absolute for values below 1) are equal."""
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(set(a) | set(b)):
            if key == "has_journal":
                continue
            found = first_difference(a.get(key), b.get(key), f"{where}.{key}", tol)
            if found:
                return found
        return None
    if isinstance(a, list | tuple) and isinstance(b, list | tuple):
        if len(a) != len(b):
            return f"{where}: {len(a)} vs {len(b)} items"
        for index, (x, y) in enumerate(zip(a, b, strict=True)):
            found = first_difference(x, y, f"{where}[{index}]", tol)
            if found:
                return found
        return None
    if isinstance(a, float) and isinstance(b, float) and abs(a - b) <= tol * max(1.0, abs(a), abs(b)):
        return None
    return None if a == b else f"{where}: {a!r} vs {b!r}"


def live_face_descriptors() -> dict[str, dict[str, Any]]:
    rec = active_rec()
    out = {}
    for body in rec.live_bodies():
        for face_id, face in body.faces.items():
            if not face.stale:
                out[face_id] = face_info({"id": face_id})
    return out


def check_every_handle_rebinds_at_its_index_after_a_reopen() -> None:
    builders = (("plate", build_part01), ("bracket", build_part03), ("flange", build_part04))
    with workdir() as directory:
        for name, builder in (*builders, ("frame", lambda: build(frame_plan(), "frame"))):
            builder()
            faces_before = live_face_descriptors()
            mass_before = call("mass_properties", op("m"))
            path, state, before = save_and_reset(directory, name)
            saved = sha256(path)
            result = reopen(path, state)
            rebind = result["rebind"]
            assert not rebind["bodies_drifted"] and not rebind["records_unmatched"], (name, rebind)
            assert not rebind["objects_invalid"] and not rebind["foreign_objects"], (name, rebind)
            assert rebind["bodies_ok"], (name, rebind)
            meta = result["stamp_meta"]  # the client turns it into a DocumentStamp
            assert meta["cotaforge.save_seq"] == "3" and meta["cotaforge.plan_id"] == "p", meta
            assert result["sha256"] == saved == sha256(path)  # reading never writes
            assert result["document"]["handle"]["id"] == before["document"]["id"]
            after = persist.dump(active_rec())
            assert all(not f["has_journal"] for f in after["features"])
            # ids, indices, signatures and links are exact; the per-body volume may differ in the last ulp after the
            # reload (seen on the frame: 37364.05842658294 vs 37364.058426582946)
            assert first_difference(after, before, tol=1e-6) is None, (name, first_difference(after, before))
            # equal up to float noise: a reloaded face can carry -2.7e-48 where the live one had 0.0 (seen on bracket)
            assert first_difference(live_face_descriptors(), faces_before, tol=1e-9) is None, (
                name,
                first_difference(live_face_descriptors(), faces_before, tol=1e-9),
            )
            mass_after = call("mass_properties", op("m"))
            # the off-diagonal entries of the inertia matrix are numerically zero: a reload moves them by ~1e-9 absolute
            assert first_difference(mass_after, mass_before, tol=1e-7) is None, (
                name,
                first_difference(mass_after, mass_before, tol=1e-7),
            )


def check_an_in_place_edit_after_a_reopen_matches_the_same_edit_without_it() -> None:
    changes = CASES["five_at_once"]
    with workdir() as directory:
        plan = frame_plan()
        modified = with_changes(plan, **changes)
        ids = build(plan, "frame")
        assert edit(ids, modified, *changes)["applied"]
        straight = shapes_of(active_rec())
        main.reset()
        ids = build(plan, "frame")
        path, state, _ = save_and_reset(directory, "frame")
        reopen(path, state)
        result = edit(ids, modified, *changes)  # the handles of the FIRST build: same ids after the reopen
        assert result["applied"] and result["rebuild_ok"] and not result["refused"], result
        assert_same_shapes(shapes_of(active_rec()), straight, "edit after reopen vs edit without")


def check_a_new_operation_on_a_saved_face_matches_the_same_operation_without_a_reopen() -> None:
    def drill() -> dict[str, Any]:
        rec = active_rec()
        base = next(f for f in rec.features if f.label == "base")
        top = select_face(faces_of({"id": base.id}), normal=(0, 0, 1))
        sk = call(
            "sketch",
            op("sk2", on=top["handle"], entities=[{"kind": "circle", "name": "c", "center": [0, 0], "diameter": 5}]),
        )
        return call("cut", op("cut2", sketch=sk["sketch"], depth_mm=3.0))

    with workdir() as directory:
        build_part01()
        reference = drill()
        straight = (call("mass_properties", op("m")), shapes_of(active_rec()))
        main.reset()
        build_part01()
        path, state, _ = save_and_reset(directory, "plate")
        reopen(path, state)
        again = drill()
        assert again["feature"] == reference["feature"] and again["created_faces"] == reference["created_faces"]
        assert call("mass_properties", op("m")) == straight[0]
        assert_same_shapes(shapes_of(active_rec()), straight[1], "new op after reopen vs without")
        undone = call("undo_last", op("u"))  # an op made after the reopen has its journal: it undoes
        assert undone["reverted"] == again["feature"]


def check_undo_below_the_reopen_point_is_a_recoverable_refusal() -> None:
    with workdir() as directory:
        build_part01()
        path, state, before = save_and_reset(directory, "plate")
        reopen(path, state)
        volume = call("mass_properties", op("m"))["volume_mm3"]
        failure = expect_error("invalid_request", "undo_last", op("u"))
        assert failure.details["reason"] == "undo_floor", failure.details  # nothing touched: no degraded session
        assert call("mass_properties", op("m"))["volume_mm3"] == volume
        assert without_journals(persist.dump(active_rec())) == without_journals(before)


def _edit_file_by_hand(path: str, change: Any) -> None:
    """The engineer opens the saved file in FreeCAD, changes it and saves (``save``: the file's own name)."""
    fc_doc = App.openDocument(path)
    try:
        change(fc_doc)
        fc_doc.recompute()
        fc_doc.save()
    finally:
        App.closeDocument(fc_doc.Name)


def check_a_dimension_changed_by_hand_is_reported_as_drift() -> None:
    with workdir() as directory:
        build(frame_plan(), "frame")
        path, state, _ = save_and_reset(directory, "frame")

        def widen(fc_doc: Any) -> None:
            (plate,) = fc_doc.getObjectsByLabel("dl_plate")
            plate.Length = plate.Length.Value + 6.0

        _edit_file_by_hand(path, widen)
        result = reopen(path, state)
        assert result["rebind"]["bodies_drifted"], result["rebind"]


def check_a_feature_added_by_hand_is_a_foreign_object() -> None:
    with workdir() as directory:
        build_part01()
        path, state, _ = save_and_reset(directory, "plate")

        def add(fc_doc: Any) -> None:
            box = fc_doc.addObject("Part::Box", "HandBox")
            box.Length = 5

        _edit_file_by_hand(path, add)
        result = reopen(path, state)
        assert "HandBox" in result["rebind"]["foreign_objects"], result["rebind"]


def check_damaged_files_are_refused_and_left_alone() -> None:
    with workdir() as directory:
        build_part01()
        path, state, _ = save_and_reset(directory, "plate")
        with open(path, "rb") as handle:
            good = handle.read()
        for name, content in (("empty", b""), ("truncated", good[: len(good) // 3]), ("garbage", b"PK\x03\x04junk")):
            bad = os.path.join(directory, f"{name}.FCStd")
            with open(bad, "wb") as handle:
                handle.write(content)
            failure = expect_error("internal", "open_document", op("o", path=bad, adapter_state=state))
            assert failure.recoverable and failure.details["reason"] == "corrupt", (name, failure.message)
            with open(bad, "rb") as handle:
                assert handle.read() == content, name
            assert main.WORKER.active is None, name
            assert not [d for d in App.listDocuments().values() if d.FileName == bad], name


def check_bad_requests_say_why() -> None:
    with workdir() as directory:
        build_part01()
        path, state, _ = save_and_reset(directory, "plate")
        missing = expect_error(
            "invalid_request", "open_document", op("o", path=os.path.join(directory, "nope.FCStd"), adapter_state=state)
        )
        assert missing.details["reason"] == "missing"
        relative = expect_error("invalid_request", "open_document", op("o", path="plate.FCStd", adapter_state=state))
        assert relative.details["reason"] == "path"
        for schema in ("freecad-registry/1", "freecad-registry/99", "fake-document/2"):
            unknown = expect_error(
                "invalid_request", "open_document", op("o", path=path, adapter_state={**state, "schema": schema})
            )
            assert unknown.details["reason"] == "adapter_state", schema
        assert main.WORKER.active is None
        reopen(path, state)
        # FreeCAD's own save writes the same file (FreeCAD reports the path with its own separators and case)
        assert os.path.normcase(os.path.normpath(active_fc().FileName)) == os.path.normcase(os.path.normpath(path))
