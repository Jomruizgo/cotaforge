"""Inserting an imported part into an assembly describes nothing per face or edge: the component keeps the id ranges of
the part's untouched bodies and makes the records of one body when something asks, the step lists no face or edge (it
counts them), and an edge that is not shared by exactly two faces -- a free edge of an open shell, a non-manifold edge
-- never stops an insert: it has a record and an id, and what needs two faces refuses it where it is used.

The part is written by the check itself from synthetic solids: a cone (an apex), a sphere (poles), a cylinder (a seam),
a box with its top missing (four free edges). Two cubes that touch along one edge (an edge of four faces) go in through
a file the engineer opened: a STEP round trip splits them apart, a FreeCAD file keeps them.
"""

import math
import os
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import FreeCAD as App
import Import
import main
import Part
import persist
import registry as reg
from check_save_resume import STAMP
from harness import IDENTITY, call, expect_error, linkable, new_document, op, select_face

V = App.Vector
FACES = (2 + 1 + 3) + 5  # cone, sphere, cylinder; box without its top
FREE_EDGES = 4  # the rim of the box without its top


def _cubes_touching_along_an_edge() -> Any:
    """Two solids that meet along one edge, fused into a compound that shares it: the edge belongs to four faces, a
    non-manifold edge, which a FreeCAD file keeps and a STEP round trip splits in two."""
    return Part.makeBox(10, 10, 10, V(0, 200, 0)).fuse(Part.makeBox(10, 10, 10, V(10, 210, 0)))


def _odd_shapes() -> list[Any]:
    cone = Part.makeCone(10, 0, 20)
    sphere = Part.makeSphere(8, V(40, 0, 0))
    cylinder = Part.makeCylinder(5, 20, V(80, 0, 0))
    box = Part.makeBox(30, 20, 10, V(0, 60, 0))
    top = max(box.Faces, key=lambda f: f.CenterOfMass.z)
    open_shell = Part.Shell([f for f in box.Faces if not f.isSame(top)])
    return [cone, sphere, cylinder, open_shell]


def _write_step(path: str) -> None:
    doc = App.newDocument("maker")
    objects = []
    for i, shape in enumerate(_odd_shapes()):
        obj = doc.addObject("Part::Feature", f"odd{i}")
        obj.Shape = shape
        objects.append(obj)
    doc.recompute()
    Import.export(objects, path)
    App.closeDocument(doc.Name)


def _import_part(tmp: str) -> dict[str, Any]:
    step = os.path.join(tmp, "odd.step")
    _write_step(step)
    result = call("import_geometry", op("imp", path=step, name="odd"))
    assert len(result["bodies"]) == 4, result["bodies"]
    return {"handle": {"id": main.WORKER.active.id}}


@contextmanager
def _inserted() -> Iterator[tuple[dict[str, Any], reg.ComponentRec, dict[str, Any]]]:
    """The part imported and saved, the assembly saved, the part inserted: ``(the step's result, the component, the part
    document)``."""
    with tempfile.TemporaryDirectory() as tmp:
        part = linkable(_import_part(tmp))
        linkable(new_document("asm", "assembly"))
        step = call("insert_component", op("ins", part=part["handle"], transform=IDENTITY))
        doc = main.WORKER.active
        assert doc is not None
        yield step, doc.components["comp:1"], part


def _with_faces(comp: reg.ComponentRec, count: int) -> list[reg.EdgeRec]:
    return [e for e in comp.edges.values() if len(e.faces) == count]


def check_an_imported_part_with_odd_edges_inserts_and_the_step_lists_no_face_or_edge() -> None:
    with _inserted() as (step, comp, _part):
        assert step["component"] == {"id": "comp:1"} and step["rebuild_ok"]
        assert step["created_faces"] == [] and step["created_edges"] == []
        assert step["face_count"] == FACES and step["edge_count"] == comp.base_shape.countElement("Edge")
        assert len(step["created_bodies"]) == 4
        assert not comp._faces and not comp._edges and not comp._vertices, "nothing was described"
        assert len(comp._pending) == 4 and comp.foreign
        listed = call("faces_of", op("q", feature=step["feature"]))["faces"]
        assert len(listed) == FACES and all(f["handle"]["id"].startswith("comp:1/face:") for f in listed)


def check_a_free_edge_and_a_non_manifold_edge_keep_a_record_and_are_refused_where_two_faces_are_needed() -> None:
    with _inserted() as (step, comp, _part):
        comp.build_all()
        free = _with_faces(comp, 1)
        assert len(free) == FREE_EDGES and not _with_faces(comp, 3), len(free)
        assert not any(e.shape.Degenerated for e in comp.edges.values()), "a degenerated edge is not an edge"
        _refused(free[0], "only one face uses it")
        # an ordinary edge of the same component is described as always
        cylinder = select_face(call("faces_of", op("q", feature=step["feature"]))["faces"], surface="cylinder")
        edges = call("edges_of", op("q", face=cylinder["handle"]))["edges"]
        assert len(edges) == 2 and all(len(e["faces"]) == 2 for e in edges), edges


def _refused(rec: reg.EdgeRec, words: str) -> None:
    """The edge is refused where two faces are needed, and can still be referred to by its signature."""
    failure = expect_error("invalid_request", "edge_info", op("q", edge={"id": f"comp:1/{rec.id}"}))
    assert "is not shared by exactly two faces" in failure.message and words in failure.message, failure.message
    sig = call("signature", op("q", entity={"id": f"comp:1/{rec.id}"}))
    assert sig["kind"] == "edge" and sig["curve"] == "line" and sig["length_mm"] > 0, sig


def check_a_non_manifold_edge_of_a_part_from_a_file_does_not_stop_the_insert() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "cubes.FCStd")
        maker = App.newDocument("cubesmaker")
        maker.addObject("Part::Feature", "cubes").Shape = _cubes_touching_along_an_edge()
        maker.recompute()
        maker.saveAs(path)
        App.closeDocument(maker.Name)
        call("open_document", op("o", path=path))
        part = {"handle": {"id": main.WORKER.active.id}}
        linkable(new_document("asm", "assembly"))
        step = call("insert_component", op("ins", part=part["handle"], transform=IDENTITY))
        comp = main.WORKER.active.components["comp:1"]  # type: ignore[union-attr]
        assert step["created_faces"] == [] and step["created_edges"] == [] and step["face_count"] == 12
        assert not comp.foreign, "a file the engineer opened is not a foreign import: its records are copied"
        shared = _with_faces(comp, 4)
        assert len(shared) == 1 and not _with_faces(comp, 1), (len(shared), len(_with_faces(comp, 1)))
        _refused(shared[0], "4 faces share it")


def check_asking_about_a_face_makes_the_records_of_its_body_only() -> None:
    with _inserted() as (_step, comp, _part):
        assert len(comp._pending) == 4
        first = comp._slices["body:1"].pristine.face_start
        info = call("face_info", op("q", face={"id": f"comp:1/face:{first}"}))
        assert info["component"] == {"id": "comp:1"} and info["body"] == {"id": "comp:1/body:1"}, info
        assert set(comp._pending) == {f"body:{n}" for n in range(2, 5)}
        for name in ("mass_properties", "bounding_box", "is_valid"):
            call(name, op("q"))
        call("component_bounding_box", op("q", component={"id": "comp:1"}))
        assert len(comp._pending) == 3, "measures describe no face"


def check_a_snapshot_of_the_assembly_keeps_no_record_of_a_body_made_lazily() -> None:
    with _inserted() as (step, comp, _part):
        call("faces_of", op("q", feature=step["feature"]))
        assert len(comp._faces) == FACES and not comp._pending
        doc = main.WORKER.active
        assert doc is not None
        copy = doc.snapshot().components["comp:1"]
        assert not copy._faces and not copy._edges and len(copy._pending) == 4
        copy.build_all()
        assert sorted(copy._faces) == sorted(comp._faces), "the same ids come back"


def check_the_measures_and_the_validity_of_an_untouched_part_are_the_parts_own() -> None:
    with _inserted() as (_step, comp, part):
        doc = main.WORKER.documents[part["handle"]["id"]]
        volume = sum(sum(reg.body_volumes(doc, b)) for b in doc.live_bodies())
        mass = call("mass_properties", op("q", component={"id": "comp:1"}))
        assert abs(mass["volume_mm3"] - volume) < 1e-6, (mass["volume_mm3"], volume)
        assert comp._volumes is not None and comp._centres is not None  # taken from the part, not measured again
        valid = call("is_valid", op("q"))
        assert valid["problems"] == [], valid
        assert "valid" in call("is_valid", op("q", deep=True))  # the BOP check of every solid runs when asked


def check_undoing_the_insert_removes_the_component_and_it_can_be_done_again() -> None:
    with _inserted() as (step, _comp, part):
        undone = call("undo_last", op("u"))
        assert undone["reverted"] == step["feature"]
        doc = main.WORKER.active
        assert doc is not None and not doc.components
        again = call("insert_component", op("ins2", part=part["handle"], transform=IDENTITY))
        assert again["face_count"] == FACES and not doc.components[again["component"]["id"]]._faces


def check_a_reopened_assembly_gets_the_component_back_without_describing_it() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        part = _import_part(tmp)
        part_state = call("save_document", op("sp", path=os.path.join(tmp, "odd.FCStd"), stamp=STAMP))
        new_document("asm", "assembly")
        saved = call("save_document", op("sa0", path=os.path.join(tmp, "asm.FCStd"), stamp=STAMP))
        call("insert_component", op("ins", part=part["handle"], transform={"origin": [5, 0, 0]}))
        saved = call("save_document", op("sa", path=os.path.join(tmp, "asm.FCStd"), stamp=STAMP))
        state = saved["adapter_state"]
        assert len(state["components"]) == 1 and "faces" not in state["components"][0], "no record per face is saved"
        main.reset()
        call("open_document", op("op", path=os.path.join(tmp, "odd.FCStd"), adapter_state=part_state["adapter_state"]))
        call("open_document", op("oa", path=os.path.join(tmp, "asm.FCStd"), adapter_state=state))
        doc = main.WORKER.active
        assert doc is not None
        comp = doc.components["comp:1"]
        assert len(comp._pending) == 4 and not comp._faces and comp.foreign
        assert len(persist.dump(doc)["components"]) == 1
        box = call("component_bounding_box", op("q", component={"id": "comp:1"}))
        assert abs(box["min"][0] - (-5.0)) < 1e-6 and len(comp._pending) == 4


# --- the box and the validity of a component made of B-spline bodies ----------------------------------------------


def _write_nurbs_step(path: str, count: int) -> None:
    """``count`` boxes with a hole, every surface converted to a B-spline: what a vendor file holds, and what makes
    the exact box a pass over curved surfaces."""
    doc = App.newDocument("nurbsmaker")
    objects = []
    for i in range(count):
        plate = Part.makeBox(30, 20, 8, V(i * 40, 0, 0)).cut(Part.makeCylinder(3, 20, V(i * 40 + 15, 10, -5)))
        obj = doc.addObject("Part::Feature", f"plate{i}")
        obj.Shape = plate.toNurbs()
        objects.append(obj)
    doc.recompute()
    Import.export(objects, path)
    App.closeDocument(doc.Name)


@contextmanager
def _inserted_nurbs(count: int, transform: dict[str, Any] | None = None) -> Iterator[tuple[reg.ComponentRec, Any]]:
    with tempfile.TemporaryDirectory() as tmp:
        step = os.path.join(tmp, "plates.step")
        _write_nurbs_step(step, count)
        call("import_geometry", op("imp", path=step, name="plates"))
        part = linkable({"handle": {"id": main.WORKER.active.id}})
        linkable(new_document("asm", "assembly"))
        inserted = call("insert_component", op("ins", part=part["handle"], transform=transform or IDENTITY))
        assert inserted["component"] == {"id": "comp:1"}
        doc = main.WORKER.active
        assert doc is not None
        yield doc.components["comp:1"], doc


def _same_box(a: dict[str, Any], b: dict[str, Any], tol: float = 1e-6) -> bool:
    return all(abs(x - y) <= tol for key in ("min", "max") for x, y in zip(a[key], b[key], strict=True))


def check_the_insert_reads_the_quick_box_and_the_exact_one_is_measured_once_for_any_pose_that_keeps_the_axes() -> None:
    move = {"origin": [5, 7, 3], "rotation": IDENTITY["rotation"]}
    with _inserted_nurbs(6, move) as (comp, _doc):
        assert comp.foreign and not comp._body_boxes[True], "inserting measured no exact box"
        quick = call("component_bounding_box", op("q", component={"id": "comp:1"}))
        assert quick["exact"] is False
        assert call("bounding_box", op("q", exact_box=False))["exact"] is False
        exact = call("bounding_box", op("q"))
        assert "exact" not in exact and len(comp._body_boxes[True]) == 6
        placed = reg.placed_component_shape(main.WORKER.active, comp)  # type: ignore[arg-type]
        direct = placed.optimalBoundingBox(False, False)
        assert _same_box(
            exact, {"min": [direct.XMin, direct.YMin, direct.ZMin], "max": [direct.XMax, direct.YMax, direct.ZMax]}
        )
        assert all(q <= e + 1e-6 for q, e in zip(quick["min"], exact["min"], strict=True))  # the quick box encloses it
        assert all(q >= e - 1e-6 for q, e in zip(quick["max"], exact["max"], strict=True))
        # a turn of 90 degrees about z and a turn of 30: the first moves the measured boxes, the second measures again
        for degrees in (90, 30):
            c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
            pose = {"origin": [0, 0, 0], "rotation": [[c, -s, 0], [s, c, 0], [0, 0, 1]]}
            call("place_component", op(f"turn{degrees}", component={"id": "comp:1"}, transform=pose))
            before = len(comp._body_boxes[True])
            got = call("bounding_box", op("q"))
            placed = reg.placed_component_shape(main.WORKER.active, comp)  # type: ignore[arg-type]
            direct = placed.optimalBoundingBox(False, False)
            want = {"min": [direct.XMin, direct.YMin, direct.ZMin], "max": [direct.XMax, direct.YMax, direct.ZMax]}
            assert _same_box(got, want, 1e-6), (degrees, got, want)
            assert len(comp._body_boxes[True]) == before == 6
        bodies = call("bodies_of", op("q", feature={"id": "feature:1"}))["bodies"]
        assert len(bodies) == 6 and all(b["bbox"]["max"][0] > b["bbox"]["min"][0] for b in bodies)


def check_a_component_that_is_not_valid_names_the_bodies_that_fail() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        step = os.path.join(tmp, "plates.step")
        _write_nurbs_step(step, 8)
        call("import_geometry", op("imp", path=step, name="plates"))
        part_doc = main.WORKER.active
        assert part_doc is not None
        part = linkable({"handle": {"id": part_doc.id}})
        for n in range(2, 9):  # the check the import made says 7 of the 8 bodies fail
            part_doc.bodies[f"body:{n}"]._valid = False
        linkable(new_document("asm", "assembly"))
        call("insert_component", op("ins", part=part["handle"], transform=IDENTITY))
        valid = call("is_valid", op("q"))
        assert valid["valid"] is False
        assert valid["problems"] == [
            "comp:1: shape is not valid (comp:1/body:2, comp:1/body:3, comp:1/body:4, comp:1/body:5, comp:1/body:6 "
            "(+2 more))"
        ], valid


def check_a_body_that_fails_the_cheap_check_at_insert_time_is_named_too() -> None:
    with _inserted_nurbs(3) as (comp, doc):
        box = Part.makeBox(10, 10, 10)
        faces = list(box.Faces)
        faces[0] = faces[0].copy()
        faces[0].translate(V(0.5, 0, 0))
        broken = Part.Solid(Part.Shell(faces))  # a face off its neighbours: the CAD says it is not valid
        assert not broken.isValid()
        comp.body_solids["body:2"] = broken
        comp.invalidate_measures()
        valid = call("is_valid", op("q"))
        assert valid["problems"] == ["comp:1: shape is not valid (comp:1/body:2)"], valid
        assert doc.components["comp:1"] is comp
