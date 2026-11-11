"""Complete holes (counterbore, countersink, angled point, up to a face) and ``tool_overlap``, straight on the worker.

A 19 mm plate 120 x 80 gets the holes of a real design: a socket-head clearance with its counterbore, a clearance
drilled from the bottom into a counterbore made before (it breaks into the cavity), a tapped set screw that ends in a
bore, a countersink; then a pocket with an island and a ring boss (a circle in a circle). Every removed or added
volume is compared with the closed form where there is one, and ``tool_overlap`` must announce exactly what the op
then does.
"""

import math
from typing import Any

from harness import (
    call,
    circle,
    close,
    expect_error,
    extrude,
    faces_of,
    new_document,
    op,
    rect,
    select_face,
    sketch,
    volume,
)

PLATE_Z = 19.0
PI = math.pi
TOL = 1e-3


def _plate() -> dict[str, Any]:
    new_document("plate")
    sk = sketch("base_sketch", "XY", [rect("outline", (0, 0), 120, 80)])
    base = extrude("base", sk, PLATE_Z)
    faces = faces_of(base["feature"])
    return {
        "base": base,
        "top": select_face(faces, normal=(0, 0, 1)),
        "bottom": select_face(faces, normal=(0, 0, -1)),
        "side": select_face(faces, normal=(1, 0, 0)),
    }


def _hole(
    name: str, face: dict[str, Any], position: list[float], diameter: float, end: str, **extra: Any
) -> dict[str, Any]:
    params = op(
        name,
        face=face["handle"],
        position=position,
        standard="simple",
        size=None,
        diameter_mm=diameter,
        end_condition=end,
        depth_mm=extra.pop("depth_mm", None),
    )
    params.update(extra)
    return call("hole", params)


def _outline(
    diameter: float, length: float, cut: str | None = None, cut_arg: tuple = (), point: float | None = None
) -> list:
    r = diameter / 2.0
    pts = [(0.0, 0.0)]
    if cut == "counterbore":
        big = cut_arg[0] / 2.0
        pts += [(0.0, big), (cut_arg[1], big), (cut_arg[1], r)]
    elif cut == "countersink":
        big = cut_arg[0] / 2.0
        pts += [(0.0, big), ((big - r) / math.tan(math.radians(cut_arg[1]) / 2.0), r)]
    else:
        pts.append((0.0, r))
    pts.append((length, r))
    pts.append((length + r / math.tan(math.radians(point) / 2.0), 0.0) if point else (length, 0.0))
    return [list(p) for p in pts]


def _probe(face: dict[str, Any], position: list[float], outline: list) -> dict[str, Any]:
    return call(
        "tool_overlap",
        op("probe", mode="cut", hole={"face": face["handle"], "position": position, "outline": outline}),
    )


def _removed_by(before: float) -> float:
    return before - volume()


def check_counterbored_clearance_removes_the_closed_form_volume() -> None:
    p = _plate()
    before = volume()
    # M8 socket head: clearance 9, counterbore 14 x 9 from the top
    probe = _probe(p["top"], [-50, -30], _outline(9, 400, "counterbore", (14, 9)))
    result = _hole("cbore", p["top"], [-50, -30], 9, "through_all", counterbore={"diameter_mm": 14, "depth_mm": 9})
    expected = PI * (49.0 * 9.0 + 20.25 * 10.0)
    assert close(_removed_by(before), expected, TOL), (_removed_by(before), expected)
    assert close(probe["common_volume_mm3"], expected, TOL), probe
    assert result["rebuild_ok"] is True
    cylinders = sorted(f["radius"] for f in faces_of(result["feature"], "created") if f["surface"] == "cylinder")
    assert [round(r, 6) for r in cylinders] == [4.5, 7.0], cylinders


def check_blind_counterbore_depth_includes_the_counterbore() -> None:
    p = _plate()
    before = volume()
    _hole("blind", p["top"], [0, 0], 9, "blind", depth_mm=12, counterbore={"diameter_mm": 14, "depth_mm": 9})
    assert close(_removed_by(before), PI * (49.0 * 9.0 + 20.25 * 3.0), TOL), _removed_by(before)


def check_countersink_and_angled_point() -> None:
    p = _plate()
    before = volume()
    probe = _probe(p["top"], [0, 0], _outline(5.5, 400, "countersink", (10.4, 90.0)))
    _hole("csink", p["top"], [0, 0], 5.5, "through_all", countersink={"diameter_mm": 10.4, "angle_deg": 90.0})
    r, big = 2.75, 5.2
    cone = big - r
    expected = PI * r * r * PLATE_Z + PI * cone / 3.0 * (big * big + big * r + r * r) - PI * r * r * cone
    assert close(_removed_by(before), expected, TOL), (_removed_by(before), expected)
    assert close(probe["common_volume_mm3"], expected, TOL), probe
    mid = volume()
    probe = _probe(p["top"], [30, 0], _outline(6, 10, None, (), 118.0))
    _hole("angled", p["top"], [30, 0], 6, "blind", depth_mm=10, drill_point="angled", drill_point_angle_deg=118.0)
    tip = 3.0 / math.tan(math.radians(59.0))
    expected = PI * 9.0 * 10.0 + PI * 9.0 * tip / 3.0
    assert close(_removed_by(mid), expected, TOL), (_removed_by(mid), expected)
    assert close(probe["common_volume_mm3"], expected, TOL), probe


def check_a_hole_from_below_breaks_into_the_counterbore() -> None:
    p = _plate()
    _hole("first", p["top"], [-10, 30], 14, "blind", depth_mm=9)  # the cavity: 14 x 9 from the top, floor at z = 10
    before = volume()
    # the bottom face looks down: its v axis is world -Y, so world (-10, 30) is (u, v) = (-10, -30)
    outline = _outline(9, 400)
    probe = _probe(p["bottom"], [-10, -30], outline)
    result = _hole("second", p["bottom"], [-10, -30], 9, "through_all")
    expected = PI * 20.25 * 10.0  # only the 10 mm of material under the floor; the cavity above it is air
    assert close(_removed_by(before), expected, TOL), (_removed_by(before), expected)
    assert close(probe["common_volume_mm3"], expected, TOL), probe
    assert probe["tool_volume_mm3"] > 10 * expected, probe  # the tool is far longer than the part it meets
    assert result["rebuild_ok"] is True


def check_up_to_face_ends_at_the_plane_of_that_face() -> None:
    p = _plate()
    first = _hole("first", p["top"], [-10, 30], 14, "blind", depth_mm=9)
    floor = select_face(faces_of(first["feature"], "created"), normal=(0, 0, 1))
    before = volume()
    _hole("second", p["bottom"], [-10, -30], 9, "up_to_face", up_to_face=floor["handle"])
    assert close(_removed_by(before), PI * 20.25 * 10.0, TOL), _removed_by(before)
    # a face that is not ahead of the hole, or not normal to its axis, is refused with the reason
    exc = expect_error(
        "invalid_request",
        "hole",
        op(
            "bad",
            face=p["top"]["handle"],
            position=[40, 0],
            standard="simple",
            size=None,
            diameter_mm=5,
            end_condition="up_to_face",
            depth_mm=None,
            up_to_face=p["side"]["handle"],
        ),
    )
    assert "not normal to the hole axis" in exc.message, exc.message


def check_tapped_set_screw_ends_in_a_bore() -> None:
    p = _plate()
    _hole("bore", p["top"], [30, 0], 10, "blind", depth_mm=15)
    before = volume()
    outline = _outline(5.0, 32.0)
    probe = _probe(p["side"], [0, 0], outline)
    result = call(
        "hole",
        op(
            "set_screw",
            face=p["side"]["handle"],
            position=[0, 0],
            standard="iso_tapped",
            size="M6",
            diameter_mm=5.0,
            end_condition="blind",
            depth_mm=32.0,
            thread={"size": "M6", "depth_mm": None},
        ),
    )
    assert result["rebuild_ok"] is True
    removed = _removed_by(before)
    assert close(removed, probe["common_volume_mm3"], TOL), (removed, probe)
    assert removed < PI * 6.25 * 32.0 - 100.0, removed  # the part of the screw hole inside the bore removed nothing


def check_ring_and_island_use_the_even_odd_rule() -> None:
    _plate()
    plane = {"origin": [0, 0, PLATE_Z], "u": [1, 0, 0], "v": [0, 1, 0], "n": [0, 0, 1]}
    datum = call("datum_plane", op("dp", how="offset", frame=plane))
    on = {"handle": datum["datum"], "frame": plane}
    before = volume()
    ring_sk = sketch("ring_sk", on, [circle("outer", (-20, 0), 24), circle("inner", (-20, 0), 16)])
    probe = call("tool_overlap", op("probe", mode="add", sketch=ring_sk["sketch"], lo_mm=0.0, hi_mm=5.0))
    ring = extrude("ring", ring_sk, 5)
    ring_volume = PI * (144.0 - 64.0) * 5.0
    assert close(volume() - before, ring_volume, TOL), volume() - before
    assert close(probe["tool_volume_mm3"], ring_volume, TOL), probe
    assert close(probe["common_volume_mm3"], 0.0, TOL), probe
    assert ring["rebuild_ok"] is True
    mid = volume()
    island_sk = sketch("pocket_sk", on, [rect("outer", (30, 25), 24, 20), rect("island", (30, 25), 8, 6)])
    probe = call("tool_overlap", op("probe", mode="cut", sketch=island_sk["sketch"], lo_mm=-6.0, hi_mm=0.0))
    call(
        "cut",
        op(
            "pocket",
            sketch=island_sk["sketch"],
            end_condition="blind",
            depth_mm=6,
            direction="negative",
            both_directions=False,
            depth2_mm=None,
        ),
    )
    expected = (24 * 20 - 8 * 6) * 6.0
    assert close(mid - volume(), expected, TOL), mid - volume()
    assert close(probe["common_volume_mm3"], expected, TOL), probe


def check_unsupported_combinations_are_refused() -> None:
    p = _plate()
    exc = expect_error(
        "invalid_request",
        "hole",
        op(
            "both",
            face=p["top"]["handle"],
            position=[0, 0],
            standard="simple",
            size=None,
            diameter_mm=5,
            end_condition="through_all",
            depth_mm=None,
            counterbore={"diameter_mm": 9, "depth_mm": 3},
            countersink={"diameter_mm": 9, "angle_deg": 90},
        ),
    )
    assert "counterbore or a countersink" in exc.message, exc.message


def check_a_counterbore_reads_as_two_holes_on_one_axis_for_the_drawing_callout() -> None:
    """The drawing hole_dim already writes a counterbore as two lines (Ø9 pasante / ⌴ Ø14 ↧ 9)
    from the holes list_holes finds: a wide blind hole on the axis of the through one, both declared by a hole."""
    import drawing
    import items_holes
    import main

    p = _plate()
    _hole("cbore", p["top"], [0, 0], 9, "through_all", counterbore={"diameter_mm": 14, "depth_mm": 9})
    infos = drawing.hole_infos(main.WORKER.active, None, None)
    stacks = items_holes._stacks(infos)
    assert len(stacks) == 1, [(h.diameter, h.depth, h.through) for h in infos]
    assert [round(h.diameter, 3) for h in stacks[0]] == [9.0, 14.0]
    assert [h.through for h in stacks[0]] == [True, False]
    assert abs(stacks[0][1].depth - 9.0) < 1e-6
    assert all(h.declared for h in infos)
