"""Bench parts 01-06 driven through the worker dispatch: provenance counts, hand-computed numbers, undo."""

import math
from typing import Any

import main
from harness import (
    IDENTITY,
    PI,
    bbox,
    build_part01,
    build_part02,
    build_part03,
    build_part04,
    build_part05,
    build_part06,
    call,
    close,
    extrude,
    face_info,
    faces_of,
    linkable,
    mass,
    new_document,
    op,
    parallel,
    rect,
    select_face,
    sketch,
    vclose,
    volume,
)


def _undo_restores(before_volume: float, before_faces: int, feature_id: str, handles: list[dict[str, str]]) -> None:
    """undo_last reverts the last op: volume, face count and pre-op handles still resolve."""
    result = call("undo_last", op("undo"))
    assert result["reverted"] == {"id": feature_id}, result
    assert close(volume(), before_volume, 1e-6), (volume(), before_volume)
    assert _face_count() == before_faces, (_face_count(), before_faces)
    for h in handles:
        face_info(h)


def _face_count() -> int:
    total = 0
    for node in call("feature_tree", op("q"))["nodes"]:
        for body in call("bodies_of", op("q", feature=node["handle"], role="any"))["bodies"]:
            total = max(total, body["face_count"])
    return total


# --- part 01 --------------------------------------------------------------------------------------------------


def check_part01_plate_numbers() -> None:
    p = build_part01()
    assert p["sketch"]["profile_areas_mm2"] == [6000.0]
    assert len(p["base"]["created_faces"]) == 6 and len(p["base"]["created_edges"]) == 12
    assert p["base"]["created_bodies"] == [{"id": "body:1"}]
    assert close(p["top"]["area_mm2"], 6000.0) and vclose(p["top"]["center"], (50, 30, 8))
    hole = p["hole"]
    assert len(hole["created_faces"]) == 1 and len(hole["modified_faces"]) == 2, hole
    cyl = face_info(hole["created_faces"][0])
    assert cyl["surface"] == "cylinder" and close(cyl["radius"], 3.3, 1e-9)
    assert vclose(cyl["axis"]["direction"], (0, 0, -1)), cyl["axis"]
    assert vclose(cyl["axis"]["origin"][:2], (10, 10), 1e-6) and vclose(cyl["center"], (10, 10, 4))
    assert cyl["provenance"] == {"created_by": hole["feature"], "modified_by": []}
    modified = sorted(tuple(face_info(f)["normal"]) for f in hole["modified_faces"])
    assert modified == [(0.0, 0.0, -1.0), (0.0, 0.0, 1.0)], modified  # entry and exit faces
    assert face_info(p["top"]["handle"])["provenance"]["modified_by"] == [hole["feature"], p["holes"]["feature"]]
    assert close(face_info(p["top"]["handle"])["area_mm2"], 6000 - 4 * PI * 3.3**2)
    assert len(hole["created_edges"]) == 2  # two circles, no seam
    holes = p["holes"]
    assert holes["instance_count"] == 4 and len(holes["created_faces"]) == 3, holes
    for f in holes["created_faces"]:
        info = face_info(f)
        assert info["surface"] == "cylinder" and close(info["radius"], 3.3, 1e-9)
        assert parallel(info["axis"]["direction"], (0, 0, -1))
    m = mass()
    assert close(m["volume_mm3"], 46905.2178, 1e-3), m
    assert vclose(m["centroid"], (50, 30, 4), 1e-6)
    box = bbox()
    assert vclose(box["min"], (0, 0, 0), 1e-9) and vclose(box["max"], (100, 60, 8), 1e-9), box
    tree = call("feature_tree", op("q"))["nodes"]
    assert [n["kind"] for n in tree] == ["extrude", "hole", "linear_pattern"]
    assert tree[0]["named_dimensions"] == {"plate_width": 100.0}
    assert call("is_valid", op("q")) == {"valid": True, "problems": []}


def check_part01_undo_each_op() -> None:
    p = build_part01()
    v_hole = PI * 3.3**2 * 8
    _undo_restores(48000 - v_hole, 7, p["holes"]["feature"]["id"], [p["top"]["handle"], p["hole"]["created_faces"][0]])
    _undo_restores(48000.0, 6, p["hole"]["feature"]["id"], [p["top"]["handle"]])
    _undo_restores(0.0, 0, p["base"]["feature"]["id"], [])
    assert call("undo_last", op("undo")) == {"reverted": None}
    assert call("feature_tree", op("q")) == {"nodes": []}


# --- part 02 --------------------------------------------------------------------------------------------------


def check_part02_shaft_pappus_and_radii() -> None:
    p = build_part02()
    assert close(p["sketch"]["profile_areas_mm2"][0], 40 * 6 + 20 * 4, 1e-9)  # 240 + 80 = 320
    body = p["body"]
    assert len(body["created_faces"]) == 5, body
    faces = faces_of(body["feature"], "created")
    radii = sorted(f["radius"] for f in faces if f["surface"] == "cylinder")
    assert len(radii) == 2 and close(radii[0], 6.0, 1e-9) and close(radii[1], 10.0, 1e-9), radii
    for f in faces:
        if f["surface"] == "cylinder":
            assert parallel(f["axis"]["direction"], (1, 0, 0), either_sense=True)
            assert vclose(f["axis"]["origin"][1:], (0, 0), 1e-9)
    planes = [f for f in faces if f["surface"] == "plane"]
    assert sorted(round(f["center"][0], 6) for f in planes) == [0.0, 20.0, 40.0]
    m = mass()
    assert close(m["volume_mm3"], 2720 * PI, 1e-6), m
    assert vclose(m["centroid"], (41600 / 2720, 0, 0), 1e-6)
    b = bbox()
    assert vclose(b["min"], (0, -10, -10), 1e-6) and vclose(b["max"], (40, 10, 10), 1e-6)
    _undo_restores(0.0, 0, body["feature"]["id"], [])


# --- part 03 --------------------------------------------------------------------------------------------------


def check_part03_bracket_cut_fillet_chamfer() -> None:
    p = build_part03()
    assert len(p["base"]["created_faces"]) == 8
    assert vclose(p["psk"]["frame"]["u"], (0, 1, 0)) and vclose(p["psk"]["frame"]["v"], (0, 0, 1))
    pocket = p["pocket"]
    assert len(pocket["created_faces"]) == 5 and pocket["modified_faces"] == [p["end_face"]["handle"]], pocket
    inner = p["inner"]
    assert inner["curve"] == "line" and inner["convex"] is False and close(inner["dihedral_deg"], 90.0, 1e-6)
    assert close(inner["length_mm"], 20.0, 1e-9)
    fillet = p["fillet"]
    assert len(fillet["created_faces"]) == 1 and fillet["warnings"] == [], fillet
    cyl = face_info(fillet["created_faces"][0])
    assert cyl["surface"] == "cylinder" and close(cyl["radius"], 5.0, 1e-9)
    assert parallel(cyl["axis"]["direction"], (0, 0, 1), either_sense=True)
    assert vclose(cyl["axis"]["origin"][:2], (15, 15), 1e-6)
    v_after_fillet = 17700 + (1 - PI / 4) * 25 * 20
    outer = p["outer"]
    assert (
        outer["convex"] is True and close(outer["dihedral_deg"], 90.0, 1e-6) and close(outer["length_mm"], 60.0, 1e-9)
    )
    chamfer = p["chamfer"]
    assert len(chamfer["created_faces"]) == 1, chamfer
    face = face_info(chamfer["created_faces"][0])
    assert face["surface"] == "plane"
    n = (0.0, -2.0 / math.sqrt(13), 3.0 / math.sqrt(13))  # (d1*n_a + d2*n_b) normalised, n_a=+Z, n_b=-Y
    assert vclose(face["normal"], n, 1e-9), face["normal"]
    assert close(face["bbox"]["max"][2] - face["bbox"]["min"][2], 2.0, 1e-9)  # d2 along n(distance1_face)
    assert close(face["bbox"]["max"][1] - face["bbox"]["min"][1], 3.0, 1e-9)  # d1 along the front face
    assert close(face["area_mm2"], 60 * math.sqrt(13), 1e-6)
    m = mass()
    assert close(m["volume_mm3"], 17627.3009, 1e-3), m
    assert vclose(m["centroid"], (20.9075, 11.8857, 9.9047), 1e-3), m
    box = bbox()
    assert vclose(box["min"], (0, 0, 0), 1e-9) and vclose(box["max"], (60, 40, 20), 1e-9), box
    handles = [p["top"]["handle"], p["front"]["handle"], fillet["created_faces"][0]]
    _undo_restores(v_after_fillet, 14, chamfer["feature"]["id"], handles)
    assert (
        call("edges_between", op("q", a=p["top"]["handle"], b=p["front"]["handle"]))["edges"][0]["handle"]
        == outer["handle"]
    )
    _undo_restores(17700.0, 13, fillet["feature"]["id"], [p["end_face"]["handle"]])
    assert (
        call("edges_between", op("q", a=inner["faces"][0], b=inner["faces"][1]))["edges"][0]["handle"]
        == inner["handle"]
    )
    _undo_restores(18000.0, 8, pocket["feature"]["id"], [p["end_face"]["handle"]])


def check_part03_chamfer_equal_distance_and_flipped_face() -> None:
    """distance1_face = the front face: the d1 extent must now lie along Y (flip handled by the worker)."""
    from harness import extrude, line, new_document, sketch

    new_document("bracket")
    sk = sketch(
        "l",
        "XY",
        [
            line("a", (0, 0), (60, 0)),
            line("b", (60, 0), (60, 40)),
            line("c", (60, 40), (0, 40)),
            line("d", (0, 40), (0, 0)),
        ],
    )
    base = extrude("base", sk, 20)
    faces = faces_of(base["feature"])
    top = select_face(faces, normal=(0, 0, 1))
    front = select_face(faces, normal=(0, -1, 0))
    edge = call("edges_between", op("q", a=top["handle"], b=front["handle"]))["edges"][0]
    ch = call(
        "chamfer", op("c", edges=[edge["handle"]], distance1_mm=3, distance2_mm=2, distance1_face=front["handle"])
    )
    face = face_info(ch["created_faces"][0])
    assert close(face["bbox"]["max"][1] - face["bbox"]["min"][1], 2.0, 1e-9), face["bbox"]  # d2 along n(front)
    assert close(face["bbox"]["max"][2] - face["bbox"]["min"][2], 3.0, 1e-9), face["bbox"]
    assert close(volume(), 48000 - 3 * 2 / 2 * 60, 1e-6)
    call("undo_last", op("u"))
    ch = call("chamfer", op("c2", edges=[edge["handle"]], distance1_mm=2, distance2_mm=None, distance1_face=None))
    face = face_info(ch["created_faces"][0])
    assert vclose(face["normal"], (0, -1 / math.sqrt(2), 1 / math.sqrt(2)), 1e-9)
    assert close(volume(), 48000 - 2 * 2 / 2 * 60, 1e-6)


# --- part 04 --------------------------------------------------------------------------------------------------


def check_part04_flange_polar_pattern() -> None:
    p = build_part04()
    disc = p["disc"]
    assert len(disc["created_faces"]) == 3 and len(disc["created_edges"]) == 2
    bore = p["bore"]
    assert len(bore["created_faces"]) == 1 and len(bore["modified_faces"]) == 2
    assert close(p["bore_face"]["radius"], 15.0, 1e-9) and vclose(p["bore_face"]["axis"]["direction"], (0, 0, -1))
    assert vclose(p["bore_face"]["axis"]["origin"], (0, 0, 5), 1e-9)
    bolt = face_info(p["bolt"]["created_faces"][0])
    assert close(bolt["radius"], 4.5, 1e-9) and vclose(bolt["axis"]["origin"][:2], (45, 0), 1e-9)
    bolts = p["bolts"]
    assert bolts["instance_count"] == 6 and len(bolts["created_faces"]) == 5, bolts
    angles = sorted(
        round(math.degrees(math.atan2(face_info(f)["center"][1], face_info(f)["center"][0])) % 360, 6)
        for f in bolts["created_faces"]
    )
    assert angles == [60.0, 120.0, 180.0, 240.0, 300.0], angles
    m = mass()
    assert close(m["volume_mm3"], 102211.7170, 1e-3), m
    assert vclose(m["centroid"], (0, 0, 5), 1e-6)
    b = bbox()
    assert vclose(b["min"], (-60, -60, 0), 1e-6) and vclose(b["max"], (60, 60, 10), 1e-6)
    v_before = PI * 60**2 * 10 - PI * 15**2 * 10 - PI * 4.5**2 * 10
    _undo_restores(v_before, 5, bolts["feature"]["id"], [p["bore_face"]["handle"], p["bolt"]["created_faces"][0]])


def check_part04_polar_pattern_offset_mode() -> None:
    """[verify] PolarPattern Mode='offset': equal_spacing=False places instances every angle_deg."""
    p = build_part04()
    call("undo_last", op("u"))
    bolts = call(
        "circular_pattern",
        op(
            "bolts2",
            features=[p["bolt"]["feature"]],
            axis=p["bore_face"]["axis"],
            count=4,
            angle_deg=30.0,
            equal_spacing=False,
        ),
    )
    assert bolts["instance_count"] == 4 and len(bolts["created_faces"]) == 3
    angles = sorted(
        round(math.degrees(math.atan2(face_info(f)["center"][1], face_info(f)["center"][0])) % 360, 6)
        for f in bolts["created_faces"]
    )
    assert angles == [270.0, 300.0, 330.0], angles  # right-hand rule about the bore axis (0, 0, -1)
    call("undo_last", op("u"))
    bolts = call(
        "circular_pattern",
        op(
            "bolts3",
            features=[p["bolt"]["feature"]],
            axis=p["bore_face"]["axis"],
            count=4,
            angle_deg=180.0,
            equal_spacing=True,
        ),
    )
    angles = sorted(
        round(math.degrees(math.atan2(face_info(f)["center"][1], face_info(f)["center"][0])) % 360, 6)
        for f in bolts["created_faces"]
    )
    assert angles == [180.0, 240.0, 300.0], angles  # 180/(count-1) steps about -Z, like the fake


# --- part 05 --------------------------------------------------------------------------------------------------


def check_part05_concept_block() -> None:
    p = build_part05()
    body = p["body"]
    assert len(body["created_faces"]) == 6 and body["created_bodies"] == [{"id": "body:1"}]
    tool = p["tool"]
    assert len(tool["created_faces"]) == 3 and tool["created_bodies"] == [{"id": "body:2"}]
    tool_cyl = select_face(p["tool_faces"], surface="cylinder")  # read before the subtract
    assert vclose(tool_cyl["axis"]["direction"], (0, 0, 1)) and vclose(tool_cyl["center"], (50, 30, 10), 1e-9)
    pocket = p["pocket"]
    assert len(pocket["created_faces"]) == 1 and len(pocket["modified_faces"]) == 2, pocket
    assert pocket["modified_bodies"] == [{"id": "body:2"}, {"id": "body:1"}], pocket
    bore = face_info(pocket["created_faces"][0])
    assert bore["surface"] == "cylinder" and close(bore["radius"], 15.0, 1e-9)
    from harness import expect_error

    expect_error("invalid_reference", "face_info", op("q", face=tool_cyl["handle"]))  # tool faces are gone
    place = p["place"]
    assert len(place["modified_faces"]) == 7 and place["modified_bodies"] == [{"id": "body:1"}], place
    m = mass()
    assert close(m["volume_mm3"], 105862.8331, 1e-3), m
    assert vclose(m["centroid"], (60, 30, 10), 1e-6)
    box = bbox()
    assert vclose(box["min"], (10, 0, 0), 1e-9) and vclose(box["max"], (110, 60, 20), 1e-9), box
    moved = face_info(pocket["created_faces"][0])
    assert vclose(moved["center"], (60, 30, 10), 1e-9)
    _undo_restores(120000 - PI * 15**2 * 20, 7, place["feature"]["id"], [pocket["created_faces"][0]])
    box = bbox()
    assert vclose(box["min"], (0, 0, 0), 1e-9) and vclose(box["max"], (100, 60, 20), 1e-9), box
    _undo_restores(120000 + PI * 15**2 * 20, 6, pocket["feature"]["id"], [tool_cyl["handle"]])
    _undo_restores(120000.0, 6, tool["feature"]["id"], [body["created_faces"][0]])


def check_part05_move_body_rotation_composes_after_existing_placement() -> None:
    """[verify] Placement.multiply: the new transform is applied AFTER the body's existing placement."""
    from harness import new_document

    new_document("mv")
    body = call("box", op("b", size=[10, 20, 30], transform=IDENTITY))
    call(
        "move_body",
        op("t", body=body["created_bodies"][0], transform={"origin": [100, 0, 0], "rotation": IDENTITY["rotation"]}),
    )
    rot90z = {"origin": [0, 0, 0], "rotation": [[0, -1, 0], [1, 0, 0], [0, 0, 1]]}
    call("move_body", op("r", body=body["created_bodies"][0], transform=rot90z))
    b = bbox()  # box at x in [100,110], y in [0,20] rotated 90 about world Z -> x in [-20,0], y in [100,110]
    assert vclose(b["min"], (-20, 100, 0), 1e-9) and vclose(b["max"], (0, 110, 30), 1e-9), b


# --- part 06 --------------------------------------------------------------------------------------------------

V06 = (40 * 40 * 20 - PI * 3.25**2 * 20) + PI * 3**2 * 40  # 32467.3119


def check_part06_assembly() -> None:
    p = build_part06()
    ins_block = p["ins_block"]
    assert ins_block["component"] == {"id": "comp:1"} and ins_block["face_count"] == 7
    assert ins_block["created_faces"] == [] and ins_block["created_edges"] == []  # counted, not listed
    assert faces_of(ins_block["feature"])[0]["handle"]["id"].startswith("comp:1/face:")
    assert ins_block["created_bodies"] == [{"id": "comp:1/body:1"}]
    ins_pin = p["ins_pin"]
    assert ins_pin["component"] == {"id": "comp:2"} and ins_pin["face_count"] == 3
    t = call("component_transform", op("q", component=ins_pin["component"]))
    assert vclose(t["origin"], (20, 20, -20)) and t["rotation"] == IDENTITY["rotation"], t
    a, b = p["a"], p["b"]  # descriptors read before the mates (pin still at x = 100)
    assert a["component"] == {"id": "comp:1"} and a["body"] == {"id": "comp:1/body:1"}
    assert vclose(a["axis"]["direction"], (0, 0, -1)) and vclose(b["axis"]["direction"], (0, 0, 1))
    assert close(a["radius"], 3.25, 1e-9) and close(b["radius"], 3.0, 1e-9)
    assert vclose(a["axis"]["origin"], (20, 20, 10), 1e-9) and vclose(b["axis"]["origin"], (100, 0, 20), 1e-9)
    assert p["axis_mate"]["mate"] == {"id": "mate:1"} and p["axis_mate"]["component"] == {"id": "comp:2"}
    assert close(p["a2"]["area_mm2"], 1600 - PI * 3.25**2, 1e-6)
    assert vclose(p["b2"]["center"], (20, 20, 40), 1e-9)  # pin top after the concentric mate
    assert p["seat_mate"]["mate"] == {"id": "mate:2"}
    pin_box = call("component_bounding_box", op("q", component=ins_pin["component"]))
    assert vclose(pin_box["min"], (17, 17, -20), 1e-9) and vclose(pin_box["max"], (23, 23, 20), 1e-9), pin_box
    clearance = p["clearance"]
    assert clearance["total_volume_mm3"] == 0.0 and len(clearance["pairs"]) == 1, clearance
    assert clearance["pairs"][0]["a"] == {"id": "comp:1"} and clearance["pairs"][0]["b"] == {"id": "comp:2"}
    m = mass()
    assert close(m["volume_mm3"], 32467.3119, 1e-3), m
    assert vclose(m["centroid"], (20, 20, 9.6517), 1e-3), m
    box = bbox()
    assert vclose(box["min"], (0, 0, -20), 1e-9) and vclose(box["max"], (40, 40, 20), 1e-9)
    # component-scoped queries and faces_of a component feature
    pin_mass = call("mass_properties", op("q", component=ins_pin["component"]))
    assert close(pin_mass["volume_mm3"], PI * 9 * 40, 1e-6)
    top_pin = select_face(faces_of(ins_pin["feature"]), normal=(0, 0, 1))
    assert vclose(top_pin["center"], (20, 20, 20), 1e-9)
    assert faces_of(ins_pin["feature"], "modified") == []
    # undo the seat mate: the pin goes back up
    _undo_restores(V06, 7, p["seat_mate"]["feature"]["id"], [a["handle"], b["handle"]])
    pin_box = call("component_bounding_box", op("q", component=ins_pin["component"]))
    assert vclose(pin_box["min"], (17, 17, 0), 1e-9), pin_box
    _undo_restores(V06, 7, p["axis_mate"]["feature"]["id"], [b["handle"]])
    pin_box = call("component_bounding_box", op("q", component=ins_pin["component"]))
    assert vclose(pin_box["min"], (97, -3, 0), 1e-9) and vclose(pin_box["max"], (103, 3, 40), 1e-9), pin_box
    t = call("component_transform", op("q", component=ins_pin["component"]))
    assert vclose(t["origin"], (100, 0, 0)) and t["rotation"] == IDENTITY["rotation"]
    # part documents are untouched
    call("set_active_document", op("q", document=p["pin_doc"]["handle"]))
    assert close(volume(), PI * 9 * 40, 1e-6)


def check_part06_mate_errors_and_flip() -> None:
    p = build_part06()
    from harness import expect_error

    call("undo_last", op("u"))  # seat mate
    call("undo_last", op("u"))  # axis mate
    # wrong alignment -> mate_failed; fixed b -> mate_failed; non-component faces -> mate_failed
    expect_error(
        "mate_failed",
        "mate_concentric",
        op("m", a=p["a"]["handle"], b=p["b"]["handle"], alignment="aligned", flip=False),
    )
    expect_error(
        "mate_failed",
        "mate_coincident",
        op("m", a=p["b2"]["handle"], b=p["a2"]["handle"], alignment="aligned", flip=False),
    )
    expect_error(
        "mate_failed",
        "mate_coincident",
        op("m", a=p["a2"]["handle"], b=p["b2"]["handle"], alignment="anti_aligned", flip=False),
    )
    # coincident with flip: the pin ends up on the other side of the block top plane (z in [20, 60])
    call("mate_concentric", op("m1", a=p["a"]["handle"], b=p["b"]["handle"], alignment="anti_aligned", flip=False))
    call("mate_coincident", op("m2", a=p["a2"]["handle"], b=p["b2"]["handle"], alignment="aligned", flip=True))
    box = call("component_bounding_box", op("q", component=p["ins_pin"]["component"]))
    assert vclose(box["min"], (17, 17, 20), 1e-9) and vclose(box["max"], (23, 23, 60), 1e-9), box
    call("undo_last", op("u"))
    # distance mate: pin top plane 5 mm above the block top (side positive along +Z)
    call(
        "mate_distance",
        op(
            "m3",
            a=p["a2"]["handle"],
            b=p["b2"]["handle"],
            distance_mm=5,
            alignment="aligned",
            side="positive",
            flip=False,
        ),
    )
    box = call("component_bounding_box", op("q", component=p["ins_pin"]["component"]))
    assert vclose(box["max"], (23, 23, 25), 1e-9), box
    inter = call("check_interference", op("i", components=None))
    assert inter["total_volume_mm3"] == 0.0
    call("undo_last", op("u"))
    call("mate_coincident", op("m4", a=p["a2"]["handle"], b=p["b2"]["handle"], alignment="aligned", flip=False))
    # push the pin sideways by mating it concentric to nothing... instead verify a real interference: move pin down
    call("undo_last", op("u"))
    call(
        "mate_distance",
        op(
            "m5",
            a=p["a2"]["handle"],
            b=p["b2"]["handle"],
            distance_mm=0,
            alignment="aligned",
            side="positive",
            flip=False,
        ),
    )
    assert call("check_interference", op("i", components=None))["total_volume_mm3"] == 0.0


def check_part06_component_queries_and_undo_to_empty() -> None:
    p = build_part06()
    top = p["a2"]["handle"]
    edges = call("edges_of", op("q", face=top))["edges"]
    assert len(edges) == 5 and sorted(e["curve"] for e in edges) == ["circle", "line", "line", "line", "line"]
    circle_edge = [e for e in edges if e["curve"] == "circle"][0]
    assert vclose(circle_edge["center"], (20, 20, 20), 1e-9) and close(circle_edge["radius"], 3.25, 1e-9)
    assert circle_edge["handle"]["id"].startswith("comp:1/edge:")
    line_edge = [e for e in edges if e["curve"] == "line"][0]
    vs = call("vertices_of", op("q", edge=line_edge["handle"]))["vertices"]
    assert len(vs) == 2 and vs[0]["handle"]["id"].startswith("comp:1/vertex:")
    block_mass = call("mass_properties", op("q", body={"id": "comp:1/body:1"}))
    assert close(block_mass["volume_mm3"], 40 * 40 * 20 - PI * 3.25**2 * 20, 1e-6)
    sig = call("signature", op("q", entity={"id": "comp:2"}))
    assert sig["kind"] == "comp" and vclose(sig["center"], (20, 20, -20), 1e-9)
    for expected in ("feature:5", "feature:4", "feature:3", "feature:2", "feature:1"):
        undone = call("undo_last", op("u"))
        assert undone == {"reverted": {"id": expected}}, undone
    assert call("mass_properties", op("q"))["volume_mm3"] == 0.0
    assert call("feature_tree", op("q")) == {"nodes": []}
    assert main.WORKER.active.components == {}
    from harness import expect_error

    expect_error("invalid_reference", "component_transform", op("q", component={"id": "comp:1"}))
    expect_error("invalid_reference", "face_info", op("q", face=top))
    assert call("undo_last", op("u")) == {"reverted": None}


# --- mate_concentric, general case (rotation for non-parallel axes) --------------------------------


def _block_with_bore() -> dict[str, Any]:
    """Block 40x40x20 with a vertical bore diameter 6.5 through its centre, drilled from the top face -- the
    same block as part 06's, standalone (its bore axis: origin (20, 20, 10), direction (0, 0, -1), radius 3.25)."""
    block_doc = new_document("block")
    sk = sketch("block_sketch", "XY", [rect("outline", (20, 20), 40, 40)])
    block_body = extrude("block_body", sk, 20)
    top = select_face(faces_of(block_body["feature"]), normal=(0, 0, 1))
    call(
        "hole",
        op(
            "bore",
            face=top["handle"],
            position=[0, 0],
            standard="simple",
            size=None,
            diameter_mm=6.5,
            end_condition="through_all",
            depth_mm=None,
        ),
    )
    return block_doc


def _mate_pin(
    name: str, direction: tuple[float, float, float], *, alignment: str = "aligned"
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """A block with a bore (``_block_with_bore``) and a 6mm-diameter, 10mm-tall pin modelled with its own axis
    along ``direction`` -- concentric-mated into the bore. Returns (bore face, pin face after the mate,
    pin component bbox)."""
    block_doc = linkable(_block_with_bore())
    pin_doc = new_document(name, "part")
    call(
        "cylinder",
        op(f"{name}_cyl", diameter_mm=6.0, height_mm=10.0, axis={"origin": [0, 0, 0], "direction": list(direction)}),
    )
    linkable(pin_doc)
    linkable(new_document("asm", "assembly"))
    ins_block = call("insert_component", op("ins_block", part=block_doc["handle"], transform=IDENTITY))
    call("fix_component", op("fix", component=ins_block["component"]))
    ins_pin = call("insert_component", op(f"ins_{name}", part=pin_doc["handle"], transform=IDENTITY))
    a = select_face(faces_of(ins_block["feature"]), surface="cylinder")
    b = select_face(faces_of(ins_pin["feature"]), surface="cylinder")
    call("mate_concentric", op("mate", a=a["handle"], b=b["handle"], alignment=alignment, flip=False))
    b2 = face_info(b["handle"])
    box = call("component_bounding_box", op("q", component=ins_pin["component"]))
    return a, b2, box


def _assert_axes_collinear(a: dict[str, Any], b2: dict[str, Any]) -> None:
    """The pin's axis, after the mate, is parallel to the bore's (dot ~= +-1) and the two lines coincide
    (the perpendicular distance from the pin's axis point to the bore's axis line is ~= 0)."""
    da, db = a["axis"]["direction"], b2["axis"]["direction"]
    dot = sum(x * y for x, y in zip(da, db, strict=True))
    assert close(abs(dot), 1.0, 1e-9), dot
    offset = [p - q for p, q in zip(b2["axis"]["origin"], a["axis"]["origin"], strict=True)]
    along = sum(x * y for x, y in zip(offset, da, strict=True))
    lateral = [o - along * d for o, d in zip(offset, da, strict=True)]
    distance = math.sqrt(sum(c * c for c in lateral))
    assert close(distance, 0.0, 1e-9), distance


def check_part06_mate_concentric_perpendicular_axes() -> None:
    """Pin modelled along X, block bore along Z: 90 degree case.

    Hand derivation: the pin is a cylinder radius 3, local axis x in [0, 10] (FreeCAD reports the cylindrical
    face's axis point at the local midpoint (5, 0, 0)). Rotating direction (1, 0, 0) onto (0, 0, -1) is a 90
    degree turn about axis (0, 1, 0) (cross((1,0,0),(0,0,-1)) = (0,1,0)); at 90 degrees that rotation matrix is
    [[0,0,1],[0,1,0],[-1,0,0]], so a point at pivot-relative (dx,dy,dz) [dx in [-5,5] axial, dy,dz in [-3,3]
    radial] lands at pivot-relative (dz, dy, -dx). The translation is the perpendicular part of
    (bore.origin - pivot) = ((20,20,10)-(5,0,0)) = (15,20,10) w.r.t. (0,0,-1): drop the -1*10 axial component,
    leaving (15,20,0), so the pivot moves to (5,0,0)+(15,20,0) = (20,20,0). Final bbox: x,y in
    pivot_xy +- radius 3 = [17,23]; z in pivot_z +- 5 = [-5,5].
    """
    a, b2, box = _mate_pin("perp", (1.0, 0.0, 0.0))
    _assert_axes_collinear(a, b2)
    assert vclose(b2["axis"]["direction"], (0, 0, -1), 1e-9)
    assert vclose(b2["axis"]["origin"], (20, 20, 0), 1e-9), b2["axis"]
    assert vclose(box["min"], (17, 17, -5), 1e-9) and vclose(box["max"], (23, 23, 5), 1e-9), box


def check_part06_mate_concentric_anti_parallel_axes() -> None:
    """Pin modelled 176.8 degrees from the bore's axis -- close enough to antiparallel that the rotation
    construction's degenerate-cross-product branch (a stable perpendicular pick, not the general Rodrigues
    formula) has to carry the mate: direction (0.05, 0, 1), so
    dot((0,0,-1), unit(0.05,0,1)) = -1/sqrt(1.0025) ~= -0.99875, comfortably outside the exactly-parallel
    tolerance (1 - 1e-6) that still short-circuits to a pure translation."""
    a, b2, box = _mate_pin("antipar", (0.05, 0.0, 1.0))
    _assert_axes_collinear(a, b2)
    assert vclose(b2["axis"]["direction"], (0, 0, -1), 1e-9)
    assert vclose(b2["axis"]["origin"], (20, 20, 4.993761694389223), 1e-6), b2["axis"]
    assert vclose(box["min"], (17, 17, -0.006238306), 1e-6) and vclose(box["max"], (23, 23, 9.993761694), 1e-6), box


def check_part06_mate_concentric_arbitrary_skew_axes() -> None:
    """Pin modelled along (1, 1, 1) -- an arbitrary skew direction (~125 degrees from the bore's -Z), neither
    close to parallel nor to antiparallel, exercising the plain two-vector ``Rotation`` construction."""
    a, b2, box = _mate_pin("skew", (1.0, 1.0, 1.0))
    _assert_axes_collinear(a, b2)
    assert vclose(b2["axis"]["direction"], (0, 0, -1), 1e-9)
    assert vclose(b2["axis"]["origin"], (20, 20, 2.886751345948128), 1e-6), b2["axis"]
    assert vclose(box["min"], (17, 17, -2.113248654), 1e-6) and vclose(box["max"], (23, 23, 7.886751346), 1e-6), box
