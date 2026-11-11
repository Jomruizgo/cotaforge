"""split: a body cut in two along a plane into history-free Part bodies (``common`` of the placed shape with a
half-space box), the source consumed. Driven straight on the worker.

Every expected number is derived by hand or from an invariant: the pieces add up to the source (volume to ~1e-9 mm3 for
any plane, oblique ones included), their volume-weighted centroid is the source's, each one lies on its side of the
plane, and a plane with no material on one side is an error that is not retried. Run on FreeCAD 1.0.2 and 1.1.3."""

import math
from typing import Any

import geometry as g
from harness import (
    IDENTITY,
    bbox,
    call,
    close,
    expect_error,
    extrude,
    faces_of,
    new_document,
    op,
    parallel,
    rect,
    select_face,
    sketch,
    vclose,
    volume,
)

PLATE_VOLUME = 48000.0
HOLE_VOLUME = math.pi * 9.0 * 8.0  # d 6 through the 8 mm plate


def _plane(origin: tuple[float, float, float], normal: tuple[float, float, float]) -> dict[str, Any]:
    """A right-handed frame whose normal is ``normal`` (u and v only complete it; a cut ignores them)."""
    n = g.unit(normal)
    seed = g.Z if abs(g.dot(n, g.Z)) < 0.9 else g.X
    u = g.unit(g.perpendicular_part(seed, n))
    v = g.cross(n, u)
    return {"origin": list(origin), "u": list(u), "v": list(v), "n": list(n)}


def _split(
    source: dict[str, str], plane: dict[str, Any], keep: str = "both", operation_id: str = "sp"
) -> dict[str, Any]:
    return call("split", op(operation_id, source=source, plane=plane, keep=keep))


def _mass(handle: dict[str, str]) -> dict[str, Any]:
    return call("mass_properties", op("q", body=handle))


def _box(size: tuple[float, float, float] = (100, 60, 20), transform: dict[str, Any] | None = None) -> dict[str, str]:
    result = call("box", op("b", size=list(size), transform=transform or IDENTITY))
    return result["created_bodies"][0]


def _plate_with_hole() -> dict[str, str]:
    """100 x 60 x 8 PartDesign plate with a d6 through hole at (10, 10); returns its body."""
    sk = sketch("s0", "XY", [rect("r", (50, 30), 100, 60)])
    base = extrude("base", sk, 8)
    top = select_face(faces_of(base["feature"]), normal=(0, 0, 1))
    call(
        "hole",
        op(
            "h",
            face=top["handle"],
            position=[-40.0, -20.0],
            standard="simple",
            size=None,
            diameter_mm=6,
            end_condition="through_all",
            depth_mm=None,
        ),
    )
    return base["created_bodies"][0]


# --- the pieces ------------------------------------------------------------------------------------------------------


def check_split_a_box_at_an_axis_aligned_plane_adds_up_exactly() -> None:
    new_document("sp_box")
    source = _box()
    result = _split(source, _plane((0, 0, 5), (0, 0, 1)))
    up, down = result["created_bodies"]
    assert result["rebuild_ok"] is True and up != down and source not in result["created_bodies"], result
    assert close(_mass(up)["volume_mm3"], 90000.0, 1e-6) and close(_mass(down)["volume_mm3"], 30000.0, 1e-6)
    assert vclose(_mass(up)["centroid"], (50, 30, 12.5), 1e-6) and vclose(_mass(down)["centroid"], (50, 30, 2.5), 1e-6)
    ub = call("bounding_box", op("q", body=up))
    assert vclose(ub["min"], (0, 0, 5), 1e-9) and vclose(ub["max"], (100, 60, 20), 1e-9), ub
    assert close(volume(), 120000.0, 1e-6)  # the source is gone, its pieces are the document
    assert len(result["created_faces"]) == 12 and result["modified_faces"] == [], result
    assert source in result["modified_bodies"], result
    expect_error("invalid_reference", "mass_properties", op("q", body=source))
    assert call("is_valid", op("q"))["valid"] is True


def check_split_the_normal_decides_which_piece_is_positive() -> None:
    new_document("sp_sign")
    source = _box()
    up, down = _split(source, _plane((0, 0, 5), (0, 0, -1)))["created_bodies"]
    assert close(_mass(up)["volume_mm3"], 30000.0, 1e-6)  # the positive side is now z < 5
    assert close(_mass(down)["volume_mm3"], 90000.0, 1e-6)


def check_split_the_pieces_own_faces_with_the_cut_face_on_the_plane() -> None:
    new_document("sp_faces")
    source = _box()
    result = _split(source, _plane((0, 0, 5), (0, 0, 1)))
    up = result["created_bodies"][0]
    cut_faces = [
        f
        for f in faces_of(result["feature"], "created")
        if f["surface"] == "plane" and abs(f["center"][2] - 5.0) < 1e-6 and f["normal"] is not None
    ]
    assert len(cut_faces) == 2, cut_faces
    pos_cut = next(f for f in cut_faces if parallel(f["normal"], (0, 0, -1)))  # outward of the positive piece
    assert close(pos_cut["area_mm2"], 6000.0, 1e-6)
    assert call("face_info", op("q", face=pos_cut["handle"]))["body"] == up


def check_split_keep_one_piece_creates_only_that_piece() -> None:
    new_document("sp_keep")
    source = _box()
    positive = _split(source, _plane((0, 0, 5), (0, 0, 1)), "positive")
    assert len(positive["created_bodies"]) == 1 and close(volume(), 90000.0, 1e-6), positive
    assert close(_mass(positive["created_bodies"][0])["volume_mm3"], 90000.0, 1e-6)
    call("undo_last", op("u"))
    negative = _split(source, _plane((0, 0, 5), (0, 0, 1)), "negative")
    assert len(negative["created_bodies"]) == 1 and close(volume(), 30000.0, 1e-6), negative
    assert vclose(_mass(negative["created_bodies"][0])["centroid"], (50, 30, 2.5), 1e-6)


def check_split_a_piece_can_be_split_again() -> None:
    new_document("sp_again")
    up, _ = _split(_box(), _plane((0, 0, 5), (0, 0, 1)))["created_bodies"]
    far, near = _split(up, _plane((25, 0, 0), (1, 0, 0)), operation_id="sp2")["created_bodies"]  # x > 25, x < 25
    assert close(_mass(far)["volume_mm3"], 67500.0, 1e-6) and close(_mass(near)["volume_mm3"], 22500.0, 1e-6)
    assert close(volume(), 120000.0, 1e-6)
    expect_error("invalid_reference", "split", op("sp3", source=up, plane=_plane((0, 0, 9), (0, 0, 1)), keep="both"))


# --- any plane, any pose ---------------------------------------------------------------------------------------------


def check_split_an_oblique_plane_adds_up_to_the_source() -> None:
    new_document("sp_oblique")
    source = _box()
    unit = g.unit((1.0, 1.0, 1.0))
    centre = (50.0, 30.0, 10.0)
    origin = g.add(centre, g.scale(unit, 4.0))  # off the centroid: unequal pieces
    plane = _plane(origin, (1.0, 1.0, 1.0))
    up, down = _split(source, plane)["created_bodies"]
    mu, md = _mass(up), _mass(down)
    assert close(mu["volume_mm3"] + md["volume_mm3"], 120000.0, 1e-8), (mu["volume_mm3"], md["volume_mm3"])
    assert mu["volume_mm3"] < md["volume_mm3"] and mu["volume_mm3"] > 1000.0
    total = mu["volume_mm3"] + md["volume_mm3"]
    weighted = [(mu["volume_mm3"] * mu["centroid"][i] + md["volume_mm3"] * md["centroid"][i]) / total for i in range(3)]
    assert vclose(weighted, centre, 1e-8), weighted
    assert g.dot(g.sub(mu["centroid"], origin), unit) > 0 and g.dot(g.sub(md["centroid"], origin), unit) < 0
    assert call("is_valid", op("q"))["valid"] is True


def check_split_a_moved_and_rotated_box_is_cut_where_it_stands() -> None:
    new_document("sp_moved")
    c, s = math.cos(math.radians(30)), math.sin(math.radians(30))
    transform = {"origin": [10, 20, 30], "rotation": [[c, -s, 0], [s, c, 0], [0, 0, 1]]}
    source = _box(transform=transform)
    centre = (10 + c * 50 - s * 30, 20 + s * 50 + c * 30, 40.0)
    assert vclose(_mass(source)["centroid"], centre, 1e-9)
    plane = _plane((0, 0, 35), (0, 0, 1))
    up, down = _split(source, plane)["created_bodies"]
    assert close(_mass(up)["volume_mm3"], 100 * 60 * 15, 1e-6) and close(_mass(down)["volume_mm3"], 100 * 60 * 5, 1e-6)
    assert vclose(_mass(up)["centroid"], (centre[0], centre[1], 42.5), 1e-9)
    assert close(volume(), 120000.0, 1e-6)
    # an oblique plane through the same moved box
    undone = call("undo_last", op("u"))
    assert undone["reverted"] is not None
    oblique = _plane(centre, (1.0, -2.0, 3.0))
    up, down = _split(source, oblique, operation_id="sp_o")["created_bodies"]
    mu, md = _mass(up), _mass(down)
    assert close(mu["volume_mm3"] + md["volume_mm3"], 120000.0, 1e-8)
    assert close(mu["volume_mm3"], md["volume_mm3"], 1e-6)  # through the centroid of a box: equal halves


def check_split_a_partdesign_body_keeps_its_hole_and_the_hole_keeps_its_axis_sense() -> None:
    new_document("sp_pd")
    source = _plate_with_hole()
    assert close(volume(), PLATE_VOLUME - HOLE_VOLUME, 1e-6)
    plane = _plane((50, 0, 0), (1, 0, 0))
    result = _split(source, plane)
    up, down = result["created_bodies"]  # positive = x > 50, negative = x < 50 (the hole is at x = 10)
    assert close(_mass(up)["volume_mm3"], 24000.0, 1e-6)
    assert close(_mass(down)["volume_mm3"], 24000.0 - HOLE_VOLUME, 1e-6)
    assert close(volume(), PLATE_VOLUME - HOLE_VOLUME, 1e-6)
    bores = [f for f in faces_of(result["feature"], "created") if f["surface"] == "cylinder"]
    assert len(bores) == 1 and bores[0]["radius"] is not None and close(bores[0]["radius"], 3.0, 1e-9), bores
    assert parallel(bores[0]["axis"]["direction"], (0, 0, -1)), bores[0]["axis"]  # the hole's intended sense survives
    assert call("face_info", op("q", face=bores[0]["handle"]))["body"] == down


def check_split_a_moved_partdesign_body_is_cut_where_it_stands() -> None:
    new_document("sp_pd_moved")
    source = _plate_with_hole()
    call(
        "move_body",
        op("mv", body=source, transform={"origin": [200, 50, 7], "rotation": [[0, -1, 0], [1, 0, 0], [0, 0, 1]]}),
    )
    centre = _mass(source)["centroid"]
    before = volume()
    plane = _plane((centre[0], centre[1], centre[2]), (0, 1, 0))
    up, down = _split(source, plane)["created_bodies"]
    mu, md = _mass(up), _mass(down)
    assert close(mu["volume_mm3"] + md["volume_mm3"], before, 1e-6), (mu["volume_mm3"], md["volume_mm3"], before)
    total = mu["volume_mm3"] + md["volume_mm3"]
    weighted = [(mu["volume_mm3"] * mu["centroid"][i] + md["volume_mm3"] * md["centroid"][i]) / total for i in range(3)]
    assert vclose(weighted, centre, 1e-6), (weighted, centre)
    assert mu["centroid"][1] > centre[1] > md["centroid"][1]
    box = bbox()
    assert box["min"][1] < centre[1] < box["max"][1]


def check_split_a_concave_body_can_have_a_piece_of_two_solids() -> None:
    new_document("sp_u")
    block = _box()
    notch = call(
        "box", op("n", size=[40, 62, 11], transform={"origin": [30, -1, 10], "rotation": IDENTITY["rotation"]})
    )
    call("boolean", op("cut", kind="subtract", target=block, tools=[notch["created_bodies"][0]], keep_tools=False))
    assert close(volume(), 120000.0 - 40 * 60 * 10, 1e-6)
    up, down = _split(block, _plane((0, 0, 15), (0, 0, 1)))["created_bodies"]
    assert close(_mass(up)["volume_mm3"], 2 * 30 * 60 * 5, 1e-6)  # two separate prisms above the notch floor
    assert close(_mass(down)["volume_mm3"], 96000.0 - 18000.0, 1e-6)
    assert close(volume(), 96000.0, 1e-6)
    assert call("is_valid", op("q"))["valid"] is True


def check_split_a_cylinder_piece_keeps_the_intended_axis_sense() -> None:
    new_document("sp_cyl")
    cyl = call(
        "cylinder",
        op("c", diameter_mm=20, height_mm=40, axis={"origin": [0, 0, 40], "direction": [0, 0, -1]}),
    )["created_bodies"][0]
    result = _split(cyl, _plane((0, 0, 15), (0, 0, 1)))
    up, down = result["created_bodies"]
    assert close(_mass(up)["volume_mm3"], math.pi * 100 * 25, 1e-6) and close(
        _mass(down)["volume_mm3"], math.pi * 100 * 15, 1e-6
    )
    sides = [f for f in faces_of(result["feature"], "created") if f["surface"] == "cylinder"]
    assert len(sides) == 2, sides
    for side in sides:
        assert parallel(side["axis"]["direction"], (0, 0, -1)), side["axis"]  # the cylinder was made pointing down


# --- failures ---------------------------------------------------------------------------------------------------------


def check_split_a_plane_that_misses_the_body_is_an_error_that_is_not_retried() -> None:
    new_document("sp_miss")
    source = _box()
    for z in (25.0, 20.0, 0.0, -3.0):  # beyond, tangent to the top, tangent to the bottom, below
        failure = expect_error(
            "geometry_failed", "split", op("sp", source=source, plane=_plane((0, 0, z), (0, 0, 1)), keep="both")
        )
        assert "plane does not cut the body" in failure.message, failure.message
        assert failure.recoverable is False
    assert close(volume(), 120000.0, 1e-6)
    assert close(_mass(source)["volume_mm3"], 120000.0, 1e-6)  # the failed attempts left the source alone
    assert len(_split(source, _plane((0, 0, 5), (0, 0, 1)))["created_bodies"]) == 2  # and it still splits


def check_split_rejects_an_unknown_keep_an_unknown_source_and_a_consumed_one() -> None:
    new_document("sp_bad")
    source = _box()
    plane = _plane((0, 0, 5), (0, 0, 1))
    expect_error("invalid_request", "split", op("sp", source=source, plane=plane, keep="top"))
    expect_error("invalid_reference", "split", op("sp", source={"id": "body:99"}, plane=plane, keep="both"))
    expect_error("invalid_request", "split", op("sp", source=source, plane={"origin": [0, 0, 0]}, keep="both"))
    _split(source, plane)
    expect_error("invalid_reference", "split", op("sp2", source=source, plane=plane, keep="both"))


def check_split_undo_restores_the_source_and_forgets_the_pieces() -> None:
    new_document("sp_undo")
    source = _box()
    result = _split(source, _plane((0, 0, 5), (0, 0, 1)))
    up = result["created_bodies"][0]
    face = result["created_faces"][0]
    undone = call("undo_last", op("undo"))
    assert undone["reverted"] == result["feature"], undone
    assert close(volume(), 120000.0, 1e-6)
    assert close(_mass(source)["volume_mm3"], 120000.0, 1e-6)  # the source is a live body again
    expect_error("invalid_reference", "mass_properties", op("q", body=up))
    expect_error("invalid_reference", "face_info", op("q", face=face))
    again = _split(source, _plane((0, 0, 5), (0, 0, 1)), operation_id="sp_again")
    assert len(again["created_bodies"]) == 2 and close(volume(), 120000.0, 1e-6)
    assert vclose(bbox()["max"], (100, 60, 20), 1e-9)
