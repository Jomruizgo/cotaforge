"""Health, framing, JSON shapes, references, recorder replay, export, [verify] items."""

import json
import math
import os
import tempfile

import FreeCAD as App
import main
import Part
import recorder
from FreeCAD import Placement, Rotation, Vector
from harness import (
    IDENTITY,
    PI,
    build_part01,
    build_part06,
    call,
    circle,
    close,
    edge_info,
    expect_error,
    extrude,
    face_info,
    faces_of,
    is_supported_freecad_version,
    new_document,
    op,
    rect,
    select_face,
    sketch,
    vclose,
    volume,
)


def check_validate_api_ok_and_canary_healthy() -> None:
    assert call("validate_api", op("v")) == {"ok": True, "problems": []}
    result = call("canary", op("c"))
    assert result["healthy"] is True
    assert result["checks"] == {"plane": True, "box": True, "normal": True, "undo": True}
    assert result["duration_ms"] > 0
    assert "__cadh_canary__" not in [d.Name for d in App.listDocuments().values()]
    hello = main.WORKER.hello()
    assert hello["hello"] == "cotaforge-freecad-worker" and is_supported_freecad_version(hello["freecad"])
    assert hello["python"].startswith("3.11") and hello["pid"] == os.getpid()


def check_reset_clears_the_document_name_to_id_mapping() -> None:
    """``reset()`` must drop ``recorder._DOC_KEYS``: FreeCAD reuses ``Document`` names (``Unnamed``,
    ``Unnamed1``, ...) across resets, so a stale entry would resolve a brand-new document to the
    previous run's ``doc_id``."""
    new_document("p")
    assert recorder._DOC_KEYS  # populated by fc.new_document -> register_document
    main.reset()
    assert recorder._DOC_KEYS == {}


def check_handle_line_framing() -> None:
    line = json.dumps({"id": 7, "method": "new_document", "params": {"operation_id": "d", "kind": "part", "name": "x"}})
    response = main.handle_line(line + "\n")
    assert response == {
        "id": 7,
        "ok": True,
        "result": {"handle": {"id": "doc:1"}, "kind": "part", "name": "x", "units": "mm"},
    }
    assert main.handle_line("   \n") is None
    bad = main.handle_line("{not json\n")
    assert bad["ok"] is False and bad["error"]["code"] == "internal" and bad["id"] is None
    err = main.handle_line(
        json.dumps({"id": 8, "method": "face_info", "params": {"operation_id": "q", "face": {"id": "face:1"}}})
    )
    assert err == {
        "id": 8,
        "ok": False,
        "error": {
            "code": "invalid_reference",
            "message": "unknown or stale handle face:1",
            "operation_id": "q",
            "details": {"handle": "face:1"},
            "recoverable": False,
        },
    }
    for line in (
        json.dumps({"id": 9, "method": "suppress_interaction", "params": {"operation_id": "s"}}),
        json.dumps({"id": 10, "method": "restore_interaction", "params": {"operation_id": "s"}}),
    ):
        assert main.handle_line(line) == {"id": int(json.loads(line)["id"]), "ok": True, "result": {}}
    assert main.handle_line(json.dumps({"id": 11, "method": "disconnect", "params": {"operation_id": "x"}})) == {
        "id": 11,
        "ok": True,
        "result": {},
    }
    assert main.WORKER.stopping is True


def check_results_are_json_and_echo_operation_id() -> None:
    p = build_part01()
    for key in ("base", "hole", "holes"):
        r = p[key]
        json.dumps(r)
        assert r["operation_id"] == {"base": "base", "hole": "hole_1", "holes": "holes"}[key]
        assert r["rebuild_ok"] is True and r["feature"]["id"].startswith("feature:")
        assert set(r) == {
            "operation_id",
            "feature",
            "rebuild_ok",
            "created_faces",
            "modified_faces",
            "created_edges",
            "created_bodies",
            "modified_bodies",
            "sketch",
            "component",
            "mate",
            "instance_count",
            "duration_ms",
            "warnings",
            *(["place"] if key == "hole" else []),  # the frame a hole on a planar face was measured from
        }
    assert p["base"]["sketch"] == p["sketch"]["sketch"] and p["hole"]["sketch"] is None
    info = face_info(p["top"]["handle"])
    json.dumps(info)
    assert set(info) == {
        "handle",
        "body",
        "component",
        "surface",
        "normal",
        "axis",
        "center",
        "radius",
        "area_mm2",
        "bbox",
        "provenance",
    }
    edge = edge_info(p["hole"]["created_edges"][0])
    json.dumps(edge)
    assert set(edge) == {
        "handle",
        "curve",
        "faces",
        "start",
        "end",
        "center",
        "axis",
        "radius",
        "length_mm",
        "bbox",
        "convex",
        "dihedral_deg",
    }
    assert edge["curve"] == "circle" and edge["start"] is None and close(edge["radius"], 3.3, 1e-9)
    # the material wedge at a hole's rim is 90 degrees: geometrically convex (the fake labels it False; the verifier
    # reads ``convex`` from the adapter, so the true value keeps its formulas right)
    assert edge["convex"] is True and close(edge["dihedral_deg"], 90.0, 1e-6)
    assert vclose(edge["center"][:2], (10, 10), 1e-9) and close(edge["length_mm"], 2 * PI * 3.3, 1e-9)
    tree = call("feature_tree", op("t"))
    json.dumps(tree)
    for node in tree["nodes"]:
        assert all(isinstance(v, bool | int | float | str) for v in node["parameters"].values()), node
    m = call("mass_properties", op("m"))
    json.dumps(m)
    assert m["inertia"] is not None and len(m["inertia"]) == 3


def check_edges_vertices_queries_on_a_box() -> None:
    new_document("q")
    sk = sketch("s", "XY", [rect("r", (5, 10), 10, 20)])
    e = extrude("e", sk, 30)
    faces = faces_of(e["feature"])
    bottom = select_face(faces, normal=(0, 0, -1))
    left = select_face(faces, normal=(-1, 0, 0))
    front = select_face(faces, normal=(0, -1, 0))
    edges = call("edges_between", op("q", a=bottom["handle"], b=left["handle"]))["edges"]
    assert len(edges) == 1 and edges[0]["curve"] == "line" and close(edges[0]["length_mm"], 20.0, 1e-9)
    assert edges[0]["convex"] is True and close(edges[0]["dihedral_deg"], 90.0, 1e-9)
    assert set(edges[0]["faces"][0].values()) | set(edges[0]["faces"][1].values()) == {
        bottom["handle"]["id"],
        left["handle"]["id"],
    }
    assert len(call("edges_of", op("q", face=bottom["handle"]))["edges"]) == 4
    vs = call("vertices_of", op("q", edge=edges[0]["handle"]))["vertices"]
    assert (
        len(vs) == 2
        and vclose(vs[0]["point"], edges[0]["start"], 1e-9)
        and vclose(vs[1]["point"], edges[0]["end"], 1e-9)
    )
    assert all(len(v["edges"]) == 3 for v in vs)
    corner = call("vertex_at", op("q", faces=[bottom["handle"], left["handle"], front["handle"]]))["vertices"]
    assert len(corner) == 1 and vclose(corner[0]["point"], (0, 0, 0), 1e-9)
    bodies = call("bodies_of", op("q", feature=e["feature"], role="created"))["bodies"]
    assert len(bodies) == 1 and bodies[0]["face_count"] == 6 and close(bodies[0]["volume_mm3"], 6000.0, 1e-9)
    assert bodies[0]["provenance"]["created_by"] == e["feature"]


def check_references_and_signatures() -> None:
    p = build_part01()
    top = p["top"]["handle"]
    ref = call("persistent_reference", op("q", entity=top))
    assert ref["adapter"] == "freecad" and ref["token"] == f"doc:1|body:1|{top['id']}"
    assert ref["signature"]["kind"] == "face" and ref["signature"]["surface"] == "plane"
    assert vclose(ref["signature"]["normal"], (0, 0, 1)) and ref["signature"]["curve"] is None
    assert call("resolve_reference", op("q", ref=ref)) == {"entity": top}
    sig = call("signature", op("q", entity=p["hole"]["created_edges"][0]))
    assert sig["kind"] == "edge" and sig["curve"] == "circle" and close(sig["radius"], 3.3, 1e-9)
    assert call("signature", op("q", entity=p["hole"]["feature"]))["kind"] == "feature"
    assert call("signature", op("q", entity={"id": "body:1"}))["kind"] == "body"
    call("undo_last", op("u"))
    call("undo_last", op("u"))
    hole_ref = {
        "adapter": "freecad",
        "token": f"doc:1|body:1|{p['hole']['created_faces'][0]['id']}",
        "signature": {"kind": "face"},
    }
    expect_error("invalid_reference", "resolve_reference", op("q", ref=hole_ref))
    expect_error(
        "invalid_reference",
        "resolve_reference",
        op("q", ref={"adapter": "fake", "token": "x", "signature": {"kind": "face"}}),
    )


def check_export_macro_replays_the_session() -> None:
    build_part01()
    script = call("export_macro", op("x", path="unused.py"))["script"]
    assert script.startswith("import FreeCAD as App") and "PartDesign::Hole" in script and "MultiTransform" in script
    namespace: dict[str, object] = {}
    exec(script, namespace)  # noqa: S102 - replaying our own recorded script
    replayed = namespace["docs"]["doc:1"]  # type: ignore[index]
    body = [o for o in replayed.Objects if o.TypeId == "PartDesign::Body"][0]
    assert close(body.Shape.Volume, 100 * 60 * 8 - 4 * PI * 3.3**2 * 8, 1e-6)
    App.closeDocument(replayed.Name)


def check_export_step_writes_file() -> None:
    build_part01()
    path = os.path.join(tempfile.gettempdir(), f"cadh_worker_{os.getpid()}.step")
    try:
        assert call("export_step", op("x", path=path)) == {}
        assert os.path.getsize(path) > 1000
        shape = Part.Shape()
        shape.read(path)
        assert close(shape.Volume, 100 * 60 * 8 - 4 * PI * 3.3**2 * 8, 1e-3)
    finally:
        if os.path.exists(path):
            os.remove(path)


def check_export_mesh_writes_a_stl() -> None:
    build_part01()
    path = os.path.join(tempfile.gettempdir(), f"cadh_worker_{os.getpid()}.stl")
    try:
        assert call("export_mesh", op("x", path=path, linear_deflection_mm=0.05)) == {}
        assert os.path.getsize(path) > 1000
        import Mesh

        mesh = Mesh.Mesh(path)
        assert mesh.CountFacets > 12, mesh.CountFacets
        exact = 100 * 60 * 8 - 4 * PI * 3.3**2 * 8
        assert abs(mesh.Volume - exact) < 0.005 * exact, (mesh.Volume, exact)  # a tessellation under-fills circles
    finally:
        if os.path.exists(path):
            os.remove(path)


def check_extrude_directions_and_cut_both_ways() -> None:
    """Direction semantics: negative pads go along -n; through-all both ways is no half hole."""
    new_document("dirs")
    sk = sketch("s", "XY", [rect("r", (0, 0), 10, 10)])
    e = extrude("e", sk, 8, direction="negative", both_directions=True, depth2_mm=3)
    box = call("bounding_box", op("b"))
    assert vclose(box["min"], (-5, -5, -8), 1e-9) and vclose(box["max"], (5, 5, 3), 1e-9), box
    faces = faces_of(e["feature"])
    mid = select_face(faces, normal=(0, 1, 0))
    csk = sketch("c", mid["handle"], [circle("h", (0, 0), 2)])
    assert vclose(csk["frame"]["origin"], (0, 5, -2.5), 1e-9)
    cut = call(
        "cut",
        op(
            "cut",
            sketch=csk["sketch"],
            end_condition="through_all",
            depth_mm=None,
            direction="negative",
            both_directions=True,
            depth2_mm=None,
        ),
    )
    assert close(volume(), 100 * 11 - PI * 1 * 10, 1e-6)
    cyl = select_face(faces_of(cut["feature"], "created"), surface="cylinder")
    # full extent along n (exact boxes carry <= 2e-7 of OCC slack)
    assert close(cyl["bbox"]["max"][1] - cyl["bbox"]["min"][1], 10.0, 1e-6)
    call("undo_last", op("u"))
    cut = call(
        "cut",
        op(
            "cut2",
            sketch=csk["sketch"],
            end_condition="blind",
            depth_mm=4,
            direction="negative",
            both_directions=False,
            depth2_mm=None,
        ),
    )
    assert close(volume(), 100 * 11 - PI * 1 * 4, 1e-6)
    assert len(cut["created_faces"]) == 2 and cut["modified_faces"] == [mid["handle"]]
    call("undo_last", op("u"))
    hole = call(
        "hole",
        op(
            "h",
            face=mid["handle"],
            position=[1, 1],
            standard="simple",
            size=None,
            diameter_mm=2,
            end_condition="blind",
            depth_mm=3,
        ),
    )
    assert close(volume(), 100 * 11 - PI * 1 * 3, 1e-6)
    assert len(hole["created_faces"]) == 2 and hole["modified_faces"] == [mid["handle"]]
    cyl = select_face(faces_of(hole["feature"], "created"), surface="cylinder")
    # face frame at n=+Y: u=+X, v=n x u=-Z, origin (0, 5, -2.5); position (1, 1) -> (1, 5, -3.5); depth 3 along -Y
    assert vclose(cyl["axis"]["direction"], (0, -1, 0)) and vclose(cyl["axis"]["origin"], (1, 3.5, -3.5), 1e-9)
    # revolve of a partial angle in a fresh document: quarter turn of a 10x4 rect (centroid y = 8) about X
    new_document("rev")
    rsk = sketch("r", "XY", [rect("q", (0, 8), 10, 4)])
    rev = call(
        "revolve", op("rev", sketch=rsk["sketch"], axis={"origin": [0, 0, 0], "direction": [1, 0, 0]}, angle_deg=90.0)
    )
    assert len(rev["created_faces"]) == 6 and rev["created_bodies"] == [{"id": "body:1"}]
    assert close(volume(), 2 * PI * 8 * 40 / 4, 1e-6)  # Pappus: 2*pi*8*40 / 4 = 502.65
    box = call("bounding_box", op("b"))
    assert vclose(box["min"], (-5, 0, 0), 1e-9) and vclose(box["max"], (5, 10, 10), 1e-9), box


def check_verify_copy_order_and_component_signatures() -> None:
    """[verify] Shape.copy() keeps the face order; component faces match the part faces by signature."""
    box = Part.makeBox(10, 20, 30)
    box.Placement = Placement(Vector(1, 2, 3), Rotation(Vector(0, 0, 1), 30))
    copy = box.copy()
    assert all((a.CenterOfMass - b.CenterOfMass).Length < 1e-9 for a, b in zip(box.Faces, copy.Faces, strict=True))
    p = build_part06()
    call("set_active_document", op("d", document=p["block_doc"]["handle"]))
    part_faces = {
        f["handle"]["id"]: f for f in faces_of({"id": "feature:1"}) + faces_of(p["bore"]["feature"], "created")
    }
    assert len(part_faces) == 7
    call("set_active_document", op("d", document=p["asm"]["handle"]))
    comp_faces = faces_of(p["ins_block"]["feature"])
    assert len(comp_faces) == 7 and len({f["handle"]["id"] for f in comp_faces}) == 7
    by_local = {f["handle"]["id"].split("/", 1)[1]: f for f in comp_faces}
    for pf in part_faces.values():
        cf = by_local[pf["handle"]["id"]]
        assert cf["surface"] == pf["surface"] and close(cf["area_mm2"], pf["area_mm2"], 1e-9)
        assert vclose(cf["center"], pf["center"], 1e-9)  # block inserted at the identity
        if pf["normal"] is not None:
            assert vclose(cf["normal"], pf["normal"], 1e-9)
        if pf["axis"] is not None:
            assert vclose(cf["axis"]["direction"], pf["axis"]["direction"], 1e-9)


def check_verify_placement_multiply_applies_argument_first() -> None:
    p1 = Placement(Vector(10, 0, 0), Rotation(Vector(0, 0, 1), 90))
    p2 = Placement(Vector(0, 5, 0), Rotation())
    assert (p1.multiply(p2).multVec(Vector(0, 0, 0)) - p1.multVec(p2.multVec(Vector(0, 0, 0)))).Length < 1e-9
    assert (p1.multiply(p2).multVec(Vector(0, 0, 0)) - Vector(5, 0, 0)).Length < 1e-9
    _ = math, IDENTITY
