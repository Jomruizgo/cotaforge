"""An imported body that nothing has touched is saved as id ranges, not as a record per face, edge and vertex: the
session record stays small, the ids come back the same after a reopen (the registry is rebuilt from the shape, in the
order of the shape), a body an operation changed is saved in full, and the numbers saved with the state are trusted only
when the file is byte for byte the saved one.

The assembly is written by ``check_import_scale``: 4 groups of 10 boxes with three holes each.
"""

import json
import math
import os
import tempfile
from typing import Any

import FreeCAD as App
import main
import Part
import persist
import registry as reg
from check_import_scale import FACES_PER_SOLID, SOLIDS, _write_assembly
from check_save_resume import STAMP
from harness import IDENTITY, call, op


def _import_and_save(tmp: str) -> tuple[dict[str, Any], str, dict[str, Any]]:
    step = os.path.join(tmp, "asm.step")
    _write_assembly(step)
    result = call("import_geometry", op("imp", path=step, name="asm"))
    target = os.path.join(tmp, "asm.FCStd")
    saved = call("save_document", op("save", path=target, stamp=STAMP))
    return result, target, saved["adapter_state"]


def _reopen(target: str, state: dict[str, Any]) -> dict[str, Any]:
    main.reset()
    return call("open_document", op("open", path=target, adapter_state=state))


def _faces(feature: dict[str, str]) -> list[tuple[str, tuple[float, ...]]]:
    out = call("faces_of", op("q", feature=feature))["faces"]
    return sorted((f["handle"]["id"], tuple(round(c, 6) for c in f["center"])) for f in out)


def check_an_untouched_import_is_saved_as_id_ranges_and_stays_small() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result, _, state = _import_and_save(tmp)
    assert state["schema"] == "freecad-registry/3" and len(state["bodies"]) == SOLIDS
    for body in state["bodies"]:
        assert "pristine" in body and "faces" not in body and "edges" not in body and "vertices" not in body
        assert body["pristine"]["faces"][1] == FACES_PER_SOLID
    # the features list no ids either: they are those of the bodies' records
    assert all(f["created_faces"] == {"__bodies__": [f["created_bodies"][0]]} for f in state["features"])
    assert len(json.dumps(state)) < 1500 * SOLIDS, len(json.dumps(state))
    assert len(result["bodies"]) == SOLIDS


def check_the_ids_and_descriptors_are_the_same_after_a_reopen() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result, target, state = _import_and_save(tmp)
        features = [b["feature"] for b in result["bodies"]]
        before = [_faces(f) for f in features[:5]]
        face_ids = [i for i, _ in before[0]]
        edges_before = call("edges_of", op("q", face=({"id": face_ids[0]})))["edges"]
        edge_ids_before = sorted(e["handle"]["id"] for e in edges_before)
        mass_before = call("mass_properties", op("q"))
        reopened = _reopen(target, state)
        assert reopened["rebind"]["bodies_ok"] and len(reopened["rebind"]["bodies_ok"]) == SOLIDS
        assert reopened["rebind"]["bodies_drifted"] == [] and reopened["rebind"]["records_unmatched"] == []
        after = [_faces(f) for f in features[:5]]
        assert after == before
        edges_after = call("edges_of", op("q", face={"id": face_ids[0]}))["edges"]
        assert sorted(e["handle"]["id"] for e in edges_after) == edge_ids_before
        assert call("mass_properties", op("q")) == mass_before
        # the registry of a body is built only when something asks: the rest stay as ranges
        doc = main.WORKER.active
        assert doc is not None
        built = [b.id for b in doc.live_bodies() if not b.is_lazy]
        assert 0 < len(built) < SOLIDS, built


def check_asking_about_one_face_builds_one_body_only() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result, target, state = _import_and_save(tmp)
        _reopen(target, state)
        doc = main.WORKER.active
        assert doc is not None and all(b.is_lazy for b in doc.live_bodies())
        wanted = _faces(result["bodies"][7]["feature"])[0][0]
        call("face_info", op("q", face={"id": wanted}))
        assert [b.id for b in doc.live_bodies() if not b.is_lazy] == [result["bodies"][7]["body"]["id"]]
        # status-like queries touch no sub-shape record
        for name in ("mass_properties", "bounding_box", "is_valid", "feature_tree"):
            call(name, op("q"))
        assert len([b for b in doc.live_bodies() if not b.is_lazy]) == 1


def check_a_body_an_operation_changed_is_saved_in_full_and_the_others_stay_compact() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result, _, _ = _import_and_save(tmp)
        moved = result["bodies"][3]["body"]
        call("move_body", op("mv", body=moved, transform={"origin": [0, 0, 100], "rotation": IDENTITY["rotation"]}))
        state = persist.dump(main.WORKER.active)
    by_id = {b["id"]: b for b in state["bodies"]}
    assert "pristine" not in by_id[moved["id"]] and len(by_id[moved["id"]]["faces"]) == FACES_PER_SOLID
    assert sum(1 for b in state["bodies"] if "pristine" in b) == SOLIDS - 1
    feature = next(f for f in state["features"] if f["created_bodies"] == [moved["id"]])
    assert len(feature["created_faces"]) == FACES_PER_SOLID  # the adopting feature kept its list when the body changed


def check_undoing_the_change_makes_the_body_compact_again() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result, _, _ = _import_and_save(tmp)
        moved = result["bodies"][3]["body"]
        volume = call("mass_properties", op("q"))["volume_mm3"]
        call("move_body", op("mv", body=moved, transform={"origin": [0, 0, 100], "rotation": IDENTITY["rotation"]}))
        call("undo_last", op("u"))
        state = persist.dump(main.WORKER.active)
        assert call("mass_properties", op("q"))["volume_mm3"] == volume
    assert all("pristine" in b for b in state["bodies"])


def check_a_file_changed_behind_the_session_is_drift_and_gets_new_ids() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result, target, state = _import_and_save(tmp)
        # the file is edited elsewhere: one solid is replaced by a smaller box
        main.reset()
        fc_doc = App.openDocument(target)
        name = main_object_of(fc_doc, state, result["bodies"][2]["body"]["id"])
        fc_doc.getObject(name).Shape = Part.makeBox(5, 5, 5)
        fc_doc.saveAs(target)
        App.closeDocument(fc_doc.Name)
        reopened = _reopen(target, state)
    rebind = reopened["rebind"]
    assert rebind["bodies_drifted"] == [result["bodies"][2]["body"]["id"]], rebind
    assert len(rebind["bodies_ok"]) == SOLIDS - 1


def main_object_of(fc_doc: Any, state: dict[str, Any], body_id: str) -> str:
    return next(b["result_obj"] for b in state["bodies"] if b["id"] == body_id)


def check_saved_numbers_are_trusted_only_for_the_file_they_were_saved_with() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result, target, state = _import_and_save(tmp)
        truth = call("mass_properties", op("q"))["volume_mm3"]
        lie = json.loads(json.dumps(state))
        for body in lie["bodies"]:
            body["cache"]["volumes"][0] += 1.0  # one cubic millimetre more in every body
        _reopen(target, lie)  # same file, same sha: the numbers saved with the state are the ones used
        assert abs(call("mass_properties", op("q"))["volume_mm3"] - (truth + SOLIDS)) < 1e-6
        lie["file_sha256"] = "0" * 64  # another file: measured again
        _reopen(target, lie)
        assert abs(call("mass_properties", op("q"))["volume_mm3"] - truth) < 1e-6


def check_a_state_with_every_record_listed_is_still_read() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result, target, _ = _import_and_save(tmp)
        doc = main.WORKER.active
        assert doc is not None
        for body in doc.live_bodies():  # what the previous schema saved: the records of every body
            body._ensure()
            body.pristine = None
        full = persist.dump(doc)
        assert all("faces" in b and "pristine" not in b for b in full["bodies"])
        full["schema"] = "freecad-registry/2"
        reopened = _reopen(target, full)
        assert len(reopened["rebind"]["bodies_ok"]) == SOLIDS
        wanted = result["bodies"][1]["feature"]
        assert len(_faces(wanted)) == FACES_PER_SOLID


def check_a_garbage_area_is_left_out_and_a_document_without_solids_has_no_centroid() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result, _, _ = _import_and_save(tmp)
        doc = main.WORKER.active
        assert doc is not None
        good = call("mass_properties", op("q"))
        assert good["area_left_out"] == [] and good["centroid_meaningful"] is True
        body = doc.live_bodies()[0]
        area = reg.body_area(doc, body)
        body._area = math.nan  # what the CAD gives for an invalid shape
        mass = call("mass_properties", op("q"))
        assert mass["area_left_out"] == [body.id]
        assert abs(mass["surface_mm2"] - (good["surface_mm2"] - area)) < 1e-6
        assert mass["volume_mm3"] == good["volume_mm3"]


def check_an_area_far_above_what_the_box_allows_is_not_an_area() -> None:
    import Part

    box = Part.makeBox(10, 20, 30)
    assert reg.plausible_area(float(box.Area), box)
    assert not reg.plausible_area(1e300, box)
    assert not reg.plausible_area(float("inf"), box)
    assert not reg.plausible_area(float("nan"), box)
    assert not reg.plausible_area(-1.0, box)
    plate = Part.makeBox(100, 100, 0.001)  # a flat thing: the square of its diagonal allows a lot
    assert reg.plausible_area(float(plate.Area), plate)
    assert not reg.plausible_area(1e9, box)  # 1000 times more than the box's whole surface


def _file_with_a_garbage_face(tmp: str) -> str:
    """A STEP of a box, a small loose face and a plane a billion kilometres wide (an unbounded face)."""
    box = Part.makeBox(10, 20, 30)
    small = Part.makePlane(10, 10, App.Vector(0, 0, 50))
    huge = Part.makePlane(1e12, 1e12, App.Vector(0, 0, 60))
    path = os.path.join(tmp, "garbage.step")
    Part.makeCompound([box, small, huge]).exportStep(path)
    return path


def check_a_box_that_is_not_real_leaves_out_the_faces_that_make_it_garbage() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result = call("import_geometry", op("imp", path=_file_with_a_garbage_face(tmp), name="garbage"))
        flat = next(b for b in result["bodies"] if b["solids"] == 0)
        solid = next(b for b in result["bodies"] if b["solids"] == 1)
        assert flat["bbox_faces_left_out"] == 1 and solid["bbox_faces_left_out"] == 0
        assert flat["bbox"]["max"][2] < 100 and max(flat["bbox"]["max"]) < 1e6, flat["bbox"]
        exact = call("bounding_box", op("q"))
        assert exact["left_out"] == [flat["body"]["id"] + ": 1 face"] and "exact" not in exact
        assert max(exact["max"]) < 1e6 and exact["max"][2] == 50.0, exact
        quick = call("bounding_box", op("q", exact_box=False))
        assert max(quick["max"]) < 1e6 and quick["left_out"] == exact["left_out"]
        mass = call("mass_properties", op("q"))
        assert mass["area_left_out"] == [flat["body"]["id"]] and mass["surface_mm2"] < 1e5, mass
        assert call("mass_properties", op("q", surface=False))["surface_mm2"] is None
        info = call("bodies_of", op("q", feature=flat["feature"]))["bodies"][0]
        assert info["bbox_faces_left_out"] == 1 and max(info["bbox"]["max"]) < 1e6


def check_the_quick_box_is_flagged_and_the_exact_one_replaces_it() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result, _, _ = _import_and_save(tmp)
        assert all(b["bbox_exact"] is False for b in result["bodies"])
        doc = main.WORKER.active
        assert doc is not None and all(b._area is None for b in doc.live_bodies()), "the area is measured when asked"
        quick = call("bounding_box", op("q", exact_box=False))
        assert quick["exact"] is False
        exact = call("bounding_box", op("q"))
        assert "exact" not in exact and exact["min"] == quick["min"] and exact["max"] == quick["max"]
        assert call("mass_properties", op("q", surface=False))["surface_mm2"] is None
        assert all(b._area is None for b in doc.live_bodies())
        assert call("mass_properties", op("q"))["surface_mm2"] > 0


def check_listing_the_holes_builds_no_record() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result, target, state = _import_and_save(tmp)
        before = call("list_holes", op("q"))["holes"]
        _reopen(target, state)
        doc = main.WORKER.active
        assert doc is not None
        holes = call("list_holes", op("q"))["holes"]
        assert len(holes) == 3 * SOLIDS and all(b.is_lazy for b in doc.live_bodies()), len(holes)
        assert holes == before  # the same holes, with the same face ids, from the ranges
        one = holes[0]["faces"][0]
        assert call("face_info", op("q", face=one))["handle"] == one
