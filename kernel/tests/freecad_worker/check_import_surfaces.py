"""A file whose faces arrive loose: one surface body (never a body per face), the plain reader when the importer
that keeps the assembly structure finds nothing, optional sewing into solids, and the distance to a surface body.

The IGES files are written by the check itself: ``Import.export`` writes the faces of an assembly without any topology
above them, and FreeCAD's ``Import.insert`` reads such a file back as NOTHING (no object at all) while ``Part.read``
finds every face -- the case the fallback is for.
"""

import os
import tempfile
from typing import Any

import foreign
import FreeCAD as App
import Import
import main
import Part
from check_import_scale import FACES_PER_SOLID, SOLIDS, _write_assembly
from harness import IDENTITY, call, op

V = App.Vector


def _export_assembly_as_iges(tmp: str) -> tuple[str, list[float]]:
    step = os.path.join(tmp, "source.step")
    volumes = _write_assembly(step)
    doc = App.newDocument("again")
    Import.insert(step, doc.Name)
    flat = []
    for obj in doc.Objects:  # the exporter keeps the placement of each product, not that of its container: bake it in
        if obj.isDerivedFrom("Part::Feature") and not obj.Shape.isNull():
            shape = obj.Shape.copy()
            shape.Placement = obj.getGlobalPlacement()
            made = doc.addObject("Part::Feature", "flat")
            made.Shape = shape
            flat.append(made)
    path = os.path.join(tmp, "faces.igs")
    Import.export(flat, path)
    App.closeDocument(doc.Name)
    return path, volumes


def _import(path: str, **extra: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    heard: list[dict[str, Any]] = []
    previous = main.PROGRESS_EMITTER
    main.PROGRESS_EMITTER = heard.append
    try:
        result = call("import_geometry", op("imp", path=path, name="faces", **extra))
    finally:
        main.PROGRESS_EMITTER = previous
    return result, heard


def check_the_plain_reader_is_taken_when_the_importer_lost_faces() -> None:
    assert foreign.prefer_plain_reader(0, 720)
    assert foreign.prefer_plain_reader(300, 720)
    assert not foreign.prefer_plain_reader(720, 720)
    assert not foreign.prefer_plain_reader(700, 720)  # a few faces fewer is not "lost"
    assert not foreign.prefer_plain_reader(0, 0)  # nothing to take


def check_loose_faces_come_in_as_one_surface_body_and_the_file_says_how_it_was_read() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path, _ = _export_assembly_as_iges(tmp)
        result, heard = _import(path)
    bodies = result["bodies"]
    assert len(bodies) == 1, [b["label"] for b in bodies][:5]
    surfaces = bodies[0]
    assert surfaces["label"] == "surfaces" and surfaces["solids"] == 0 and surfaces["closed"] is False
    assert surfaces["faces"] == SOLIDS * FACES_PER_SOLID, surfaces["faces"]
    assert surfaces["volume_mm3"] == 0.0
    note = "no names, colours or assembly structure to keep"
    assert any("plain IGES reader" in n and note in n for n in result["notes"])
    assert result["sewing"] is None and "sewing" not in {m["phase"] for m in heard}
    mass = call("mass_properties", op("q"))
    assert mass["centroid_meaningful"] is False and mass["surface_only"] == [surfaces["body"]["id"]]
    assert mass["volume_mm3"] == 0.0 and mass["area_left_out"] == []
    # the registry holds a record for every face of that one body
    doc = main.WORKER.active
    assert doc is not None and len(doc.live_bodies()) == 1
    assert len(doc.live_bodies()[0].faces) == SOLIDS * FACES_PER_SOLID


def check_sewing_makes_a_solid_of_every_closed_shell() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path, volumes = _export_assembly_as_iges(tmp)
        result, heard = _import(path, sew=True, sew_tolerance_mm=0.01)
    sewing = result["sewing"]
    assert sewing is not None and sewing["faces"] == SOLIDS * FACES_PER_SOLID and sewing["tolerance_mm"] == 0.01
    assert sewing["shells"] == SOLIDS and sewing["closed_shells"] == SOLIDS and sewing["solids"] == SOLIDS
    assert sewing["free_edges"] == 0
    bodies = result["bodies"]
    assert len(bodies) == SOLIDS and all(b["solids"] == 1 and b["closed"] and b["valid"] for b in bodies)
    assert sorted(round(b["volume_mm3"], 3) for b in bodies) == sorted(round(v, 3) for v in volumes)
    assert "sewing" in {m["phase"] for m in heard}
    total = call("mass_properties", op("q"))["volume_mm3"]
    assert abs(total - sum(volumes)) < 1e-6 * sum(volumes)


def _open_box_file(tmp: str) -> str:
    """A 10 mm box missing its top: five faces that sew into one OPEN shell with four free edges."""
    box = Part.makeBox(10, 10, 10)
    path = os.path.join(tmp, "open.igs")
    Part.setStaticValue("write.iges.brep.mode", 0)
    Part.makeCompound(box.Faces[:5]).exportIges(path)
    return path


def check_what_cannot_be_closed_stays_one_surface_body_with_its_gaps_counted() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result, _ = _import(_open_box_file(tmp), sew=True)
    sewing = result["sewing"]
    assert sewing["faces"] == 5 and sewing["shells"] == 1 and sewing["closed_shells"] == 0 and sewing["solids"] == 0
    assert sewing["free_edges"] == 4, sewing
    assert [(b["label"], b["solids"], b["faces"]) for b in result["bodies"]] == [("surfaces", 0, 5)]
    assert result["bodies"][0]["shells"] == 1 and result["bodies"][0]["volume_mm3"] == 0.0


def check_the_distance_to_a_surface_body_is_measured_and_no_volume_is_pretended() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result, _ = _import(_open_box_file(tmp))
    surface = result["bodies"][0]["body"]
    box = call("box", op("tool", size=[10, 10, 10], transform={"origin": [30, 0, 0], "rotation": IDENTITY["rotation"]}))
    other = box["created_bodies"][0]
    pairs = call("body_interference", op("q"))["pairs"]
    assert len(pairs) == 1
    pair = pairs[0]
    assert {pair["a"]["id"], pair["b"]["id"]} == {surface["id"], other["id"]}
    assert abs(pair["distance_mm"] - 20.0) < 1e-6, pair
    assert pair["common_mm3"] == 0.0 and "surface body" in pair["note"]
    near = call("move_body", op("mv", body=other, transform={"origin": [-30, 0, 5], "rotation": IDENTITY["rotation"]}))
    assert near is not None
    pair = call("body_interference", op("q"))["pairs"][0]
    assert pair["distance_mm"] == 0.0  # the box now stands inside the open shell's reach


def check_a_very_big_surface_body_registers_its_faces_only_and_reopens() -> None:
    import registry as reg
    from check_save_resume import STAMP

    previous = reg.BIG_SURFACE_FACES
    reg.BIG_SURFACE_FACES = 100  # the check's file has a few hundred faces
    try:
        with tempfile.TemporaryDirectory() as tmp:
            path, _ = _export_assembly_as_iges(tmp)
            result, _ = _import(path)
            body = main.WORKER.active.live_bodies()[0]
            assert len(body.faces) == SOLIDS * FACES_PER_SOLID and not body.edges and not body.vertices
            assert any("faces have handles" in w for w in result["warnings"]), result["warnings"]
            face = next(iter(body.faces.values()))
            assert face.shape.isSame(reg.topo_shape(main.WORKER.active, body).getElement(face.sub_name))
            target = os.path.join(tmp, "surfaces.FCStd")
            saved = call("save_document", op("save", path=target, stamp=STAMP))
            main.reset()
            reopened = call("open_document", op("open", path=target, adapter_state=saved["adapter_state"]))
            assert reopened["rebind"]["bodies_drifted"] == [] and reopened["rebind"]["records_unmatched"] == []
    finally:
        reg.BIG_SURFACE_FACES = previous
