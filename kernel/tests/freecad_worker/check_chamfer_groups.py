"""Coverage for the FlipDirection-grouping branch of ``ops_features.chamfer``: "edges needing different flips -> two
Chamfer features in the same transaction (second Base = first)". That branch (the ``groups`` dict growing past one key,
and the second feature's ``Base`` being re-found in the first feature's ``Shape`` via ``ops_features._find_edge_name``)
had no check.

Investigation before writing this check (both probed live against freecadcmd, not assumed): for a
100x60x8 plate, ``ancestorsOfType(edge, Part.Face)[0]`` is the adjacent SIDE face for every one of the
four top edges -- never the top face itself. This held both for a ``PartDesign::Pad`` extrude (this
module's ``_plate()``) and for a ``Part::Box`` body converted via ``ops_common.ensure_partdesign``: OCC
lists a swept/box solid's side faces before its cap faces, so the *first* ancestor of any top-face
boundary edge is structurally always the side face, regardless of which edge is picked.

Consequence: ``ops_features.chamfer``'s ``distance1_face`` must be adjacent to *every* edge passed to one
call (``chamfer()``'s adjacency check), and the top face is the only face bordering more than one top
edge -- a side face borders exactly one. So the only way to batch two top edges into a single
``chamfer()`` call is ``distance1_face=top``, and with that face ``flip_for()`` returns the same boolean
for all four edges (verified above). ``groups`` therefore never grows past one key for any pair of top
edges on this geometry, and the chained-feature branch (``if chamfers: ... _find_edge_name(...)``) is
never reached -- it is genuinely unreachable here, not merely untested by omission.

This check instead drives TWO SEPARATE ``chamfer()`` calls through ``main.dispatch`` on two top edges that
do not share a vertex (front, y=0, and back, y=60): one with ``distance1_face`` = the top face (a raw,
pre-post-check ``flip_for`` of ``True``) and one with ``distance1_face`` = the edge's own adjacent side
face (a raw ``flip_for`` of ``False``) -- covering both boolean outcomes of ``flip_for`` and the
``build()``/``post_check()`` logic under both ``distance1_face`` orientations, which is as close as this
geometry gets to the grouping branch. It does NOT cover ``_find_edge_name`` or the second-feature
chaining -- there is no pair of top edges on a box/plate that reaches it, per the investigation above.

Numbers: with ``distance1_mm=2`` (d1), ``distance2_mm=3`` (d2), the existing ``check_part03_bracket_cut_fillet_chamfer``
(``tests/freecad_worker/check_parts.py``) establishes the verified invariant "the created face's extent along
n(distance1_face) must equal distance2_mm" (post_check enforces this, self-correcting FlipDirection once if needed) --
so the extent along each call's own distance1_face normal is 3, not 2, and the extent along the other adjacent face is
2, not 3."""

import main
from harness import call, close, extrude, face_info, faces_of, mass, new_document, op, rect, select_face, sketch

D1 = 2.0
D2 = 3.0


def _fingerprint() -> tuple[float, int, list[str]]:
    doc = main.WORKER.active
    names = sorted(o.Name for o in doc.fc_doc.Objects)
    faces = sum(len(b.faces) for b in doc.live_bodies())
    return mass()["volume_mm3"], faces, names


def _plate() -> dict:
    """100x60x8 plate (X x Y x Z), PartDesign Pad, no other features."""
    new_document("plate")
    sk = sketch("base_sketch", "XY", [rect("outline", (50, 30), 100, 60)])
    base = extrude("base", sk, 8)
    faces = faces_of(base["feature"])
    return {
        "base": base,
        "top": select_face(faces, normal=(0, 0, 1)),
        "front": select_face(faces, normal=(0, -1, 0)),  # y = 0
        "back": select_face(faces, normal=(0, 1, 0)),  # y = 60
    }


def _top_edge_at(top_handle: dict, y: float) -> dict:
    edges = call("edges_of", op("q", face=top_handle))["edges"]
    (candidate,) = [
        e for e in edges if abs(e["length_mm"] - 100.0) < 1e-6 and abs((e["start"][1] + e["end"][1]) / 2.0 - y) < 1e-6
    ]
    return candidate


def check_chamfer_flip_direction_groups_on_a_plate() -> None:
    p = _plate()
    before = _fingerprint()
    v0 = mass()["volume_mm3"]

    front_edge = _top_edge_at(p["top"]["handle"], 0.0)
    back_edge = _top_edge_at(p["top"]["handle"], 60.0)

    # Call 1: distance1_face = top face -> flip_for() = True for every top edge (see module docstring).
    c1 = call(
        "chamfer",
        op("c1", edges=[front_edge["handle"]], distance1_mm=D1, distance2_mm=D2, distance1_face=p["top"]["handle"]),
    )
    assert len(c1["created_faces"]) == 1, c1
    v1 = mass()["volume_mm3"]
    assert close(v1 - v0, -(D1 * D2 / 2.0) * front_edge["length_mm"], 1e-6), (v1 - v0, front_edge["length_mm"])
    f1 = face_info(c1["created_faces"][0])
    assert f1["surface"] == "plane", f1
    assert close(f1["bbox"]["max"][2] - f1["bbox"]["min"][2], D2, 1e-6), f1  # n(distance1_face) = +Z -> d2
    assert close(f1["bbox"]["max"][1] - f1["bbox"]["min"][1], D1, 1e-6), f1  # the other adjacent face -> d1

    # Call 2 (separate transaction -- re-fetch the back face, unaffected by call 1's front-corner cuts):
    # distance1_face = the back face itself, adjacent to the back edge -> flip_for() = False (raw), the
    # opposite of call 1's raw result.
    c2 = call(
        "chamfer",
        op("c2", edges=[back_edge["handle"]], distance1_mm=D1, distance2_mm=D2, distance1_face=p["back"]["handle"]),
    )
    assert len(c2["created_faces"]) == 1, c2
    v2 = mass()["volume_mm3"]
    assert close(v2 - v1, -(D1 * D2 / 2.0) * back_edge["length_mm"], 1e-6), (v2 - v1, back_edge["length_mm"])
    f2 = face_info(c2["created_faces"][0])
    assert f2["surface"] == "plane", f2
    assert close(f2["bbox"]["max"][1] - f2["bbox"]["min"][1], D2, 1e-6), f2  # n(distance1_face) = +Y -> d2
    assert close(f2["bbox"]["max"][2] - f2["bbox"]["min"][2], D1, 1e-6), f2  # the other adjacent face -> d1

    assert len(c1["created_faces"]) + len(c2["created_faces"]) == 2

    total_expected = -(D1 * D2 / 2.0) * (front_edge["length_mm"] + back_edge["length_mm"])
    assert close(v2 - v0, total_expected, 1e-6), (v2 - v0, total_expected)

    result = call("undo_last", op("u"))
    assert result["reverted"] == {"id": c2["feature"]["id"]}, result
    assert close(mass()["volume_mm3"], v1, 1e-6)
    result = call("undo_last", op("u"))
    assert result["reverted"] == {"id": c1["feature"]["id"]}, result
    assert _fingerprint() == before, (_fingerprint(), before)
