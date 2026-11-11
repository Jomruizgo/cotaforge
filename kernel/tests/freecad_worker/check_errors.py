"""Error paths: geometry_failed leaves the document unchanged, stale handles, invalid requests, no document."""

import main
from harness import (
    IDENTITY,
    PI,
    build_part01,
    call,
    circle,
    close,
    expect_error,
    extrude,
    face_info,
    faces_of,
    new_document,
    op,
    rect,
    select_face,
    sketch,
    volume,
)


def _fingerprint() -> tuple[float, int, list[str]]:
    doc = main.WORKER.active
    names = sorted(o.Name for o in doc.fc_doc.Objects)
    faces = sum(len(b.faces) for b in doc.live_bodies())
    return volume(), faces, names


def check_fillet_impossible_radius_is_geometry_failed_and_document_unchanged() -> None:
    p = build_part01()
    before = _fingerprint()
    top = p["top"]["handle"]
    edges = call("edges_of", op("q", face=top))["edges"]
    line_edges = [e for e in edges if e["curve"] == "line"]
    exc = expect_error("geometry_failed", "fillet", op("bad", edges=[line_edges[0]["handle"]], radius_mm=500))
    assert exc.recoverable is True and exc.operation_id == "bad"
    assert _fingerprint() == before, (_fingerprint(), before)
    assert main.WORKER.active.fc_doc.UndoCount == 3  # the aborted transaction left nothing behind
    assert len(call("feature_tree", op("q"))["nodes"]) == 3
    face_info(top)
    face_info(p["hole"]["created_faces"][0])
    # the document still works afterwards
    call("undo_last", op("u"))
    assert close(volume(), 48000 - PI * 3.3**2 * 8, 1e-6)


def check_stale_handles_are_invalid_reference() -> None:
    p = build_part01()
    call("undo_last", op("u"))  # pattern gone
    exc = expect_error("invalid_reference", "face_info", op("q", face=p["holes"]["created_faces"][0]))
    assert exc.recoverable is False and exc.details == {"handle": p["holes"]["created_faces"][0]["id"]}
    expect_error("invalid_reference", "faces_of", op("q", feature=p["holes"]["feature"], role="any"))
    call("undo_last", op("u"))  # hole gone
    expect_error("invalid_reference", "face_info", op("q", face=p["hole"]["created_faces"][0]))
    expect_error("invalid_reference", "edge_info", op("q", edge=p["hole"]["created_edges"][0]))
    expect_error(
        "invalid_reference",
        "hole",
        op(
            "h",
            face={"id": "face:999"},
            position=[0, 0],
            standard="simple",
            size=None,
            diameter_mm=1,
            end_condition="through_all",
            depth_mm=None,
        ),
    )
    expect_error(
        "invalid_reference",
        "extrude",
        op(
            "e",
            sketch={"id": "sketch:77"},
            depth_mm=1,
            direction="positive",
            both_directions=False,
            depth2_mm=None,
            merge=True,
        ),
    )
    expect_error(
        "invalid_reference",
        "linear_pattern",
        op(
            "p",
            features=[{"id": "feature:9"}],
            direction=[1, 0, 0],
            spacing_mm=1,
            count=2,
            direction2=None,
            spacing2_mm=None,
            count2=None,
        ),
    )
    expect_error("invalid_reference", "set_active_document", op("d", document={"id": "doc:9"}))
    expect_error(
        "invalid_reference", "boolean", op("b", kind="union", target={"id": "body:1"}, tools=[{"id": "body:5"}])
    )
    # the pre-undo top face handle is still valid (its record survived both undos)
    face_info(p["top"]["handle"])


def check_no_active_document_and_unknown_method() -> None:
    exc = expect_error("no_active_document", "mass_properties", op("m"))
    assert exc.recoverable is False
    expect_error("no_active_document", "sketch", op("s", on="XY", entities=[rect("r", (0, 0), 1, 1)]))
    expect_error("no_active_document", "active_document", op("a"))
    exc = expect_error("not_supported", "teleport", op("t"))
    assert exc.details == {"method": "teleport"}
    expect_error("invalid_request", "new_document", op("n", kind="drawing", name="x"))


def check_invalid_requests() -> None:
    new_document("bad")
    sk = sketch("s", "XY", [rect("r", (5, 5), 10, 10)])
    e = extrude("e", sk, 4)
    top = select_face(faces_of(e["feature"]), normal=(0, 0, 1))
    expect_error(
        "invalid_request",
        "hole",
        op(
            "h",
            face=top["handle"],
            position=[0, 0],
            standard="simple",
            size=None,
            diameter_mm=2,
            end_condition="blind",
            depth_mm=None,
        ),
    )
    expect_error(
        "invalid_request",
        "cut",
        op(
            "c",
            sketch=sk["sketch"],
            end_condition="blind",
            depth_mm=None,
            direction="negative",
            both_directions=False,
            depth2_mm=None,
        ),
    )
    front = select_face(faces_of(e["feature"]), normal=(0, -1, 0))
    edge = call("edges_between", op("q", a=top["handle"], b=front["handle"]))["edges"][0]
    expect_error(
        "invalid_request",
        "chamfer",
        op("ch", edges=[edge["handle"]], distance1_mm=1, distance2_mm=2, distance1_face=None),
    )
    bottom = select_face(faces_of(e["feature"]), normal=(0, 0, -1))
    expect_error(
        "invalid_request",
        "chamfer",
        op("ch", edges=[edge["handle"]], distance1_mm=1, distance2_mm=2, distance1_face=bottom["handle"]),
    )
    expect_error("invalid_request", "fillet", op("f", edges=[], radius_mm=1))
    fillet = call("fillet", op("f", edges=[edge["handle"]], radius_mm=1))
    expect_error(
        "invalid_request",
        "linear_pattern",
        op(
            "p",
            features=[fillet["feature"]],
            direction=[1, 0, 0],
            spacing_mm=20,
            count=2,
            direction2=None,
            spacing2_mm=None,
            count2=None,
        ),
    )
    expect_error("invalid_request", "box", op("b", size=[1, 0, 1], transform=IDENTITY))
    expect_error(
        "invalid_request",
        "cylinder",
        op("c", diameter_mm=0, height_mm=1, axis={"origin": [0, 0, 0], "direction": [0, 0, 1]}),
    )
    expect_error("invalid_request", "boolean", op("b", kind="union", target=e["created_bodies"][0], tools=[]))
    expect_error(
        "invalid_request",
        "boolean",
        op("b", kind="union", target=e["created_bodies"][0], tools=[e["created_bodies"][0]]),
    )
    # a boss that does not touch the body: PartDesign would silently keep one solid -> geometry_failed instead
    sk2 = sketch("s2", "XY", [circle("c", (50, 50), 4)])
    exc = expect_error(
        "geometry_failed",
        "extrude",
        op(
            "e2",
            sketch=sk2["sketch"],
            depth_mm=4,
            direction="positive",
            both_directions=False,
            depth2_mm=None,
            merge=True,
        ),
    )
    assert "no material" in exc.message
    expect_error(
        "geometry_failed",
        "cut",
        op(
            "c2",
            sketch=sk2["sketch"],
            end_condition="blind",
            depth_mm=2,
            direction="negative",
            both_directions=False,
            depth2_mm=None,
        ),
    )
    assert len(call("feature_tree", op("q"))["nodes"]) == 2  # extrude + fillet only
    # a hole on the cylindrical face of a second body (merge=False) used to be refused as "not planar"; it is now a
    # radial hole across the convex wall (position = [axial, angle]): through both walls of a solid R2 cylinder with a
    # D1 hole it removes the integral of the removed length over the hole disc, 3.11685 mm3 (OCC is a little low)
    boss = extrude("e3", sk2, 4, merge=False)
    assert boss["created_bodies"] == [{"id": "body:2"}]
    cyl = select_face(faces_of(boss["feature"], "created"), surface="cylinder")
    before = volume()
    assert close(before, 400 - (1 - PI / 4) * 1 * 10 + PI * 4 * 4, 1e-6), before
    call(
        "hole",
        op(
            "h",
            face=cyl["handle"],
            position=[0, 0],
            standard="simple",
            size=None,
            diameter_mm=1,
            end_condition="through_all",
            depth_mm=None,
        ),
    )
    assert close(before - volume(), 3.11685336242153, 0.02), before - volume()


def check_not_supported_paths() -> None:
    new_document("ns")
    box = call("box", op("b", size=[10, 10, 10], transform=IDENTITY))
    sk = sketch("s", "XY", [circle("c", (5, 5), 4)])
    expect_error(
        "not_supported",
        "revolve",
        op("r", sketch=sk["sketch"], axis={"origin": [0, 0, 0], "direction": [1, 0, 0]}, angle_deg=360),
    )
    # detail op on a concept body converts it (second slice of the design, exercised here for coverage)
    top = select_face(faces_of(box["feature"]), normal=(0, 0, 1))
    hole = call(
        "hole",
        op(
            "h",
            face=top["handle"],
            position=[0, 0],
            standard="simple",
            size=None,
            diameter_mm=2,
            end_condition="through_all",
            depth_mm=None,
        ),
    )
    assert close(volume(), 1000 - PI * 1 * 10, 1e-6)
    assert len(hole["created_faces"]) == 1 and len(hole["modified_faces"]) == 2
    cyl = call("cylinder", op("c", diameter_mm=2, height_mm=2, axis={"origin": [0, 0, 20], "direction": [0, 0, 1]}))
    expect_error(
        "not_supported",
        "boolean",
        op("u", kind="union", target=box["created_bodies"][0], tools=[cyl["created_bodies"][0]]),
    )


def check_boss_disjoint_with_allow_compound_true_is_geometry_failed() -> None:
    """``PartDesign::Body.AllowCompound`` defaults to ``False`` on
    1.0.2 and to ``True`` on 1.1.3 -- with it ``True``, a disjoint boss keeps *two* solids and the naive
    "volume grew -> success" check (the one ``check_invalid_requests`` exercises above) would report
    success instead of ``geometry_failed``. ``ops_common._check_volume_change`` must catch this from the
    solid count itself, not from the version-specific default, so it must fail here even on 1.0.2 with
    the flag flipped by hand -- exactly the hole a user hits by touching ``AllowCompound`` there too."""
    new_document("ac")
    sk = sketch("s", "XY", [rect("r", (5, 5), 10, 10)])
    e = extrude("e", sk, 4)
    body = main.WORKER.active.bodies[e["created_bodies"][0]["id"]]
    fc_body = main.WORKER.active.fc_doc.getObject(body.fc_body)
    fc_body.AllowCompound = True
    sk2 = sketch("s2", "XY", [circle("c", (50, 50), 4)])
    exc = expect_error(
        "geometry_failed",
        "extrude",
        op(
            "e2",
            sketch=sk2["sketch"],
            depth_mm=4,
            direction="positive",
            both_directions=False,
            depth2_mm=None,
            merge=True,
        ),
    )
    assert "no material" in exc.message
    # the disjoint boss must not have been kept as a second solid
    assert len(main.WORKER.active.fc_doc.getObject(body.fc_body).Shape.Solids) == 1
