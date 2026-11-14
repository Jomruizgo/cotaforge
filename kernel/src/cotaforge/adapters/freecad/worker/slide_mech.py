"""``slide_mechanism``: the carrier, gibs, angle pin, locking heel, ball detent and wear plate of a mould slide.

The slide is a body of an earlier ``mould_split`` step (the insert that forms the undercut). Every part of the
mechanism is a NEW native body (``mould_parts.build_part``), placed in the frame of the slide: ``d`` outward along the
slide's own direction, ``e`` across it, ``q`` depth into the half the slide rides in (see ``slide_math``). Nothing is
cut in the plates or the inserts.

What is checked on the real shapes before the step is kept:

* the slide leaves the part along ``d`` (and the cavity and the core are not in its way), and by how much: the
  undercut DEPTH; the stroke is that plus the clearance and is shown to clear the part;
* the heel is steeper than the pin, measured on the finished bodies (the face of the heel and the axis of the pin);
* no two parts share volume in the CLOSED position (the ball detent excepted: spring-loaded, it rides 1.5 mm into the
  flat side of the carrier) and none in the OPEN one, got by moving the slide group by the stroke along ``d`` and the pin
  group by the opening travel the angle pin needs, away from the host half.
"""

import itertools
import math
from typing import Any

import geometry as g
import mould_parts as mp
import ops_common as oc
import Part
import registry as reg
import slide_math as sm
from FreeCAD import Placement, Vector
from protocol import geometry_failed, invalid_request, operation_id_of, unrecoverable

CLEAR_TOL_MM3 = 1e-3
"""Shared volume under which two solids are taken as touching: coincident faces give a few 1e-9, a real overlap is
orders of magnitude above."""
ANGLE_TOL_DEG = 0.01
SEARCH_LIMIT_FACTOR = 4.0


def _unit(v: tuple[float, float, float]) -> tuple[float, float, float]:
    return g.unit(v)


def _frame_of(d: g.Vec3, p: g.Vec3) -> Placement:
    """Placement whose x is ``d``, z is the pull and y = z x x (what every part of the mechanism uses as its frame)."""
    return mp.frame_placement({"origin": (0.0, 0.0, 0.0), "x": d, "z": p})


def _shape_in(shape: Any, place: Placement) -> Any:
    local = shape.copy()
    local.transformShape(place.inverse().toMatrix(), True)
    return local


def _box(shape: Any) -> Any:
    return g.exact_box(shape)


def _moved(shape: Any, offset: g.Vec3) -> Any:
    out = shape.copy()
    out.translate(g.fcvec(offset))
    return out


class BooleanFailedError(ValueError):
    """OCC could not intersect two shapes: nothing can then be said about them, so the step stops (never 'probably clear')."""


def _shared(a: Any, b: Any) -> float:
    try:
        return float(a.common(b).Volume)
    except Exception as exc:  # noqa: BLE001
        msg = f"the kernel could not intersect two shapes ({type(exc).__name__}: {exc})"
        raise BooleanFailedError(msg) from exc


def _body_by_role(doc: reg.DocumentRec, feature: Any, suffix: str) -> reg.BodyRec | None:
    for body_id in feature.created_bodies:
        body = doc.bodies.get(body_id)
        if body is None or body.deleted:
            continue
        obj = doc.fc_doc.getObject(body.result_obj)
        if obj is not None and str(obj.Label).endswith(suffix):
            return body
    return None


def _perpendicular_candidates(pull: g.Vec3) -> list[g.Vec3]:
    a = (1.0, 0.0, 0.0) if abs(pull[0]) < 0.9 else (0.0, 1.0, 0.0)
    a = _unit(g.sub(a, g.scale(pull, g.dot(a, pull))))
    b = g.cross(pull, a)
    return [a, g.scale(a, -1.0), b, g.scale(b, -1.0)]


def _leaves(slide: Any, direction: g.Vec3, others: list[Any], reach: float) -> bool:
    moved = _moved(slide, g.scale(direction, reach))
    try:
        return all(_shared(moved, o) <= CLEAR_TOL_MM3 for o in others)
    except BooleanFailedError:
        return False


def _extent_along(shape: Any, direction: g.Vec3) -> float:
    """How long the shape is along ``direction``: the slide has left everything when it has moved this far."""
    place = mp.frame_placement(
        {"origin": (0.0, 0.0, 0.0), "x": direction, "z": _perpendicular_candidates(direction)[0]}
    )
    box = _box(_shape_in(shape, place))
    return float(box.XLength)


class Release:
    """What the slide must clear: the part leaves along the pull (it stays on the core, which opens along it), so a slide
    is free when the part, moved along the pull as far as it can go, meets it nowhere.

    The movement is sampled: the part (cropped once to the neighbourhood of the slide, so a part of hundreds of faces is
    cut a single time) is moved in steps of ``step`` along the pull, and a step is never longer than the slide is thick
    along the pull, so no overlap can fall between two samples (an overlap lasts, along the pull, at least as long as the
    slide is thick there)."""

    def __init__(self, slide: Any, part: Any, direction: g.Vec3, pull: g.Vec3, travel: float) -> None:
        self.slide, self.direction, self.pull = slide, direction, pull
        thick = _extent_along(slide, pull)
        self.step = max(0.25, min(2.0, 0.4 * thick))
        reach = _extent_along(part, pull) + thick + self.step
        self.count = int(math.ceil(reach / self.step))
        box = slide.optimalBoundingBox(False, False)
        grow = [1.0 + reach * abs(pull[i]) + travel * abs(direction[i]) for i in range(3)]
        crop = Part.makeBox(
            box.XLength + 2 * grow[0], box.YLength + 2 * grow[1], box.ZLength + 2 * grow[2],
            Vector(box.XMin - grow[0], box.YMin - grow[1], box.ZMin - grow[2]),
        )  # fmt: skip
        self.local = part.common(crop)
        self.copies: dict[int, Any] = {}

    def _part_at(self, k: int) -> Any:
        if k not in self.copies:
            self.copies[k] = _moved(self.local, g.scale(self.pull, k * self.step))  # the part going along +pull
        return self.copies[k]

    def shared(self, t: float) -> float:
        """Largest volume the slide, moved ``t`` along its direction, shares with the part at any of its positions."""
        moved = _moved(self.slide, g.scale(self.direction, t))
        worst = 0.0
        for k in range(self.count + 1):
            worst = max(worst, _shared(moved, self._part_at(k)))
            if worst > CLEAR_TOL_MM3:
                break
        return worst

    def depth(self, limit: float) -> float:
        """The least travel along the direction after which the part can leave (0 when it can already)."""
        if self.shared(0.0) <= CLEAR_TOL_MM3:
            return 0.0
        hi = limit
        if self.shared(hi) > CLEAR_TOL_MM3:
            msg = f"the part cannot leave past the slide even when it is pulled {limit:.0f} mm along ({self.direction[0]:.3g}, {self.direction[1]:.3g}, {self.direction[2]:.3g})"
            raise ValueError(msg)
        lo = 0.0
        for _ in range(10):
            mid = (lo + hi) / 2.0
            if self.shared(mid) > CLEAR_TOL_MM3:
                lo = mid
            else:
                hi = mid
        return hi


# --- the specs of the parts ---------------------------------------------------------------------------------------


def _plane(
    origin: tuple[float, float, float], normal: tuple[float, float, float], x: tuple[float, float, float]
) -> dict[str, Any]:
    return {"origin": origin, "normal": normal, "x": x}


def _poly_feature(
    name: str, plane: dict[str, Any], pts3: list[tuple[float, float, float]], depth: float
) -> dict[str, Any]:
    n = _unit(plane["normal"])
    x = _unit(plane["x"])
    y = g.cross(n, x)
    o = plane["origin"]
    uv = [(g.dot(g.sub(p, o), x), g.dot(g.sub(p, o), y)) for p in pts3]
    return {
        "kind": "pad",
        "name": name,
        "plane": plane,
        "outline": {"shape": "poly", "points": [list(p) for p in uv]},
        "depth": depth,
    }


def _pocket_circle(
    name: str, plane: dict[str, Any], centre3: tuple[float, float, float], d: float, depth: float
) -> dict[str, Any]:
    n = _unit(plane["normal"])
    x = _unit(plane["x"])
    y = g.cross(n, x)
    o = plane["origin"]
    rel = g.sub(centre3, o)
    return {
        "kind": "pocket",
        "name": name,
        "plane": plane,
        "circles": [{"shape": "circle", "cx": g.dot(rel, x), "cy": g.dot(rel, y), "d": d}],
        "depth": depth,
    }


def _pocket_rect(
    name: str, plane: dict[str, Any], centre3: tuple[float, float, float], w: float, h: float, depth: float
) -> dict[str, Any]:
    n = _unit(plane["normal"])
    x = _unit(plane["x"])
    y = g.cross(n, x)
    rel = g.sub(centre3, plane["origin"])
    return {
        "kind": "pocket",
        "name": name,
        "plane": plane,
        "rects": [{"shape": "rect", "cx": g.dot(rel, x), "cy": g.dot(rel, y), "w": w, "h": h}],
        "depth": depth,
    }


def _headed_pin(d: float, head_d: float, head_k: float, length: float) -> tuple[dict[str, Any], float]:
    profile = [(0.0, -head_k), (head_d / 2, -head_k), (head_d / 2, 0.0), (d / 2, 0.0), (d / 2, length), (0.0, length)]
    volume = math.pi * ((head_d / 2) ** 2 * head_k + (d / 2) ** 2 * length)
    return {"kind": "revolve", "name": "Pin", "profile": [list(p) for p in profile]}, volume


class Mechanism:
    """The numbers and the part specs of one mechanism, in the frame of the slide."""

    def __init__(
        self,
        n: dict[str, float],
        s: int,
        stroke: float,
        frame: dict[str, Any],
        pin_row: dict[str, Any],
        wear_row: dict[str, Any],
        catalog_family: str,
        sources: dict[str, str],
        pin_cut: float,
    ) -> None:
        self.n, self.s, self.stroke, self.frame = n, s, stroke, frame
        self.pin_row, self.wear_row, self.family, self.sources, self.pin_cut = (
            pin_row,
            wear_row,
            catalog_family,
            sources,
            pin_cut,
        )

    def z(self, q: float) -> float:
        return self.s * q

    def specs(self) -> list[dict[str, Any]]:
        n, s, frame = self.n, self.s, self.frame
        w, hc, gp = n["width"], n["carrier_height"], n["g_p"]
        tt, tb = n["top_thickness"], n["back_thickness"]
        alpha = n["alpha"]
        specs: list[dict[str, Any]] = []

        # carrier: a prism in the (d, z') plane across the width, a slanted bore for the pin, a groove on each side
        carrier_pts = [(0.0, 0.0, self.z(gp)), (tt, 0.0, self.z(gp)), (tb, 0.0, self.z(hc)), (0.0, 0.0, self.z(hc))]
        self.carrier_outline = carrier_pts
        top_plane = _plane((0.0, w / 2, 0.0), (0.0, -1.0, 0.0), (1.0, 0.0, 0.0))
        t_hat = (math.sin(alpha), 0.0, s * math.cos(alpha))
        bore_origin = (n["d_top"], 0.0, self.z(gp))
        # the bore starts on a plane square to its axis, which is tilted against the top face of the carrier: start it
        # back along the axis far enough that its low edge is above the face, or a wedge of material stays at the mouth
        lead = (n["r_hole"] * math.sin(alpha) + 0.5) / math.cos(alpha)
        bore_start = g.sub(bore_origin, g.scale(t_hat, lead))
        bore_plane = _plane(bore_start, g.scale(t_hat, -1.0), (0.0, 1.0, 0.0))
        groove_depth = sm.GROOVE_DEPTH_MM
        features = [
            _poly_feature("Body", top_plane, [(p[0], p[1], p[2]) for p in carrier_pts], w),
            _pocket_circle(
                "PinBore",
                bore_plane,
                bore_start,
                self.pin_row["d"] + sm.PIN_HOLE_CLEARANCE_MM,
                lead + (hc - gp) / math.cos(alpha) + 2.0,
            ),
        ]
        for sign, name in ((1.0, "GrooveRight"), (-1.0, "GrooveLeft")):
            plane = _plane((0.0, sign * w / 2, 0.0), (0.0, sign, 0.0), (1.0, 0.0, 0.0))
            centre = (tb / 2, sign * w / 2, self.z(n["q_groove"]))
            features.append(_pocket_rect(name, plane, centre, tb + 2.0, sm.GROOVE_HEIGHT_MM, groove_depth))
        ball_c = (self.dimple_d, w / 2 + sm.DIMPLE_DEPTH_MM, self.z(n["ball_q"]))
        features.append(
            {
                "kind": "sphere",
                "name": "Dimple",
                "center": list(ball_c),
                "radius": sm.BALL_D_MM / 2 + 0.05,
                "subtract": True,
            }
        )
        specs.append({"role": "carrier", "frame": frame, "features": features, "catalog": None, "volume_mm3": None})

        # gibs: a T section (stem outside the carrier, lip in its groove) along d, the right one with the ball bore
        for sign, role in ((1.0, "gib_right"), (-1.0, "gib_left")):
            es0 = w / 2 + sm.GIB_GAP_MM
            es1 = es0 + sm.GIB_STEM_MM
            el0 = w / 2 - groove_depth + sm.GROOVE_FIT_MM
            ql0 = n["q_groove"] - sm.GROOVE_HEIGHT_MM / 2 + sm.GROOVE_FIT_MM
            ql1 = n["q_groove"] + sm.GROOVE_HEIGHT_MM / 2 - sm.GROOVE_FIT_MM
            t_section = [(es0, gp), (es1, gp), (es1, hc), (es0, hc), (es0, ql1), (el0, ql1), (el0, ql0), (es0, ql0)]
            pts = [(0.0, sign * e, self.z(q)) for e, q in t_section]
            plane = _plane((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, sign, 0.0))
            # the plane's v axis is (normal x x); for the left gib x = -y' keeps the profile mirrored, not reversed
            gib = [_poly_feature("Gib", plane, pts, n["gib_length"])]
            if sign > 0:
                bore_plane2 = _plane((0.0, es1, 0.0), (0.0, 1.0, 0.0), (1.0, 0.0, 0.0))
                gib.append(
                    _pocket_circle(
                        "BallBore",
                        bore_plane2,
                        (self.ball_d, es1, self.z(n["ball_q"])),
                        sm.BALL_BORE_D_MM,
                        sm.GIB_STEM_MM,
                    )
                )
            specs.append({"role": role, "frame": frame, "features": gib, "catalog": None, "volume_mm3": None})

        # angle pin: cut to length from a stock DME pin, head in the pin half
        row = self.pin_row
        feature, volume = _headed_pin(row["d"], row["head_d"], row["head_k"], self.pin_cut)
        seat = n["seat"]
        head_at = (
            n["d_top"] - gp * math.tan(alpha) - seat * math.sin(alpha),
            0.0,
            -seat * s * math.cos(alpha),
        )
        pin_frame = {
            "origin": _tuple(self.frame_point(head_at)),
            "x": _tuple(self.frame_dir((0.0, 1.0, 0.0))),
            "z": _tuple(self.frame_dir(t_hat)),
        }
        specs.append(
            {
                "role": "angle_pin",
                "frame": pin_frame,
                "features": [feature],
                "volume_mm3": volume,
                "catalog": {
                    "family": self.family,
                    "reference": row["item"],
                    "size": f"metric angle pin D {row['d']:g} x L {row['length']:g} mm, cut to {self.pin_cut:.2f} mm, leaning {math.degrees(alpha):.2f} deg",
                    "source": self.sources.get("angle_pin", ""),
                    "note": "stock pin cut to the travel the slide needs (note 2 of the catalogue page); head in the pin half",
                },
            }
        )

        # locking heel: a wedge behind the carrier whose face is the carrier's back face, steeper than the pin
        mount = sm.HEEL_MOUNT_MM
        d_in_hc = sm.face_d(n, hc)
        d_out = d_in_hc + sm.HEEL_THICKNESS_MM
        heel_pts = [
            (sm.face_d(n, -mount), 0.0, self.z(-mount)),
            (d_out, 0.0, self.z(-mount)),
            (d_out, 0.0, self.z(hc)),
            (d_in_hc, 0.0, self.z(hc)),
        ]
        heel_w = w - 2.0 * (groove_depth + 1.0)
        heel_plane = _plane((0.0, heel_w / 2, 0.0), (0.0, -1.0, 0.0), (1.0, 0.0, 0.0))
        specs.append(
            {
                "role": "locking_heel",
                "frame": frame,
                "features": [_poly_feature("Wedge", heel_plane, heel_pts, heel_w)],
                "catalog": None,
                "volume_mm3": None,
            }
        )

        # wear plate under the carrier, from the catalogue
        wr = self.wear_row
        wear_plane = _plane((0.0, 0.0, self.z(hc)), (0.0, 0.0, float(s)), (1.0, 0.0, 0.0))
        wear = {
            "kind": "pad",
            "name": "Plate",
            "plane": wear_plane,
            "outline": {"shape": "rect", "cx": wr["length"] / 2, "cy": 0.0, "w": wr["length"], "h": wr["width"]},
            "depth": wr["thickness"],
        }
        specs.append(
            {
                "role": "wear_plate",
                "frame": frame,
                "features": [wear],
                "volume_mm3": wr["length"] * wr["width"] * wr["thickness"],
                "catalog": {
                    "family": self.family,
                    "reference": wr["item"],
                    "size": f"self-lubricating wear plate {wr['thickness'] / 25.4:g} x {wr['width'] / 25.4:g} x {wr['length'] / 25.4:g} in",
                    "source": self.sources.get("wear_plate", ""),
                    "note": "under the carrier; the pocket for it in the host plate is not cut",
                },
            }
        )

        # ball detent: a steel ball and its spring in the right gib, holding the open position
        ball = {
            "kind": "sphere",
            "name": "Ball",
            "center": list(ball_c_at(self)),
            "radius": sm.BALL_D_MM / 2,
            "subtract": False,
        }
        specs.append(
            {
                "role": "detent_ball",
                "frame": frame,
                "features": [ball],
                "catalog": None,
                "volume_mm3": 4.0 / 3.0 * math.pi * (sm.BALL_D_MM / 2) ** 3,
            }
        )
        spring_start = w / 2 + sm.DIMPLE_DEPTH_MM + sm.BALL_D_MM / 2
        spring = {
            "kind": "helix",
            "name": "Spring",
            "origin": [self.ball_d, spring_start, self.z(n["ball_q"])],
            "axis": [0.0, 1.0, 0.0],
            "coil_d": 4.8,
            "wire_d": 0.8,
            "pitch": 2.2,
            "height": 4.0,
        }
        specs.append(
            {"role": "detent_spring", "frame": frame, "features": [spring], "catalog": None, "volume_mm3": None}
        )
        return specs

    # the pin frame is given in the slide frame: take it to the document
    def frame_point(self, v: g.Vec3) -> g.Vec3:
        place = mp.frame_placement(self.frame)
        return g.vec(place.multVec(g.fcvec(v)))

    def frame_dir(self, v: g.Vec3) -> g.Vec3:
        place = mp.frame_placement(self.frame)
        return g.vec(place.Rotation.multVec(g.fcvec(v)))

    dimple_d = 0.0
    ball_d = 0.0
    carrier_outline: list[tuple[float, float, float]] = []


def _tuple(v: Any) -> tuple[float, float, float]:
    return (float(v[0]), float(v[1]), float(v[2]))


def ball_c_at(m: Mechanism) -> tuple[float, float, float]:
    return (m.ball_d, m.n["width"] / 2 + sm.DIMPLE_DEPTH_MM, m.z(m.n["ball_q"]))


# --- the worker method --------------------------------------------------------------------------------------------


def _angle_of_face(face: Any, pull: g.Vec3) -> float:
    n = g.vec(face.normalAt(*face.Surface.parameter(face.CenterOfMass)))
    return math.degrees(math.asin(min(1.0, abs(g.dot(g.unit(n), pull)))))


def slide_mechanism(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = operation_id_of(params)
    split = doc.feature(reg.handle_id(params["split"], op, "split"), op)
    region = int(params["slide"])
    slide_rec = _body_by_role(doc, split, f"_slide_{region}")
    cavity_rec = _body_by_role(doc, split, "_cavity")
    core_rec = _body_by_role(doc, split, "_core")
    if slide_rec is None or cavity_rec is None or core_rec is None:
        raise invalid_request(
            f"step {split.id!r} made no slide {region}: it is not a mould_split with that slide accepted (slides=all or [n])",
            op,
        )
    part_rec = reg.resolve_body(doc, reg.handle_id(params["part"], op, "part"), op)
    pull = g.unit(g.vec(params["pull"]))
    alpha_deg = float(params["angle_deg"])
    try:
        beta_deg = sm.check_angles(alpha_deg, float(params["wedge_extra_deg"]))
    except sm.SlideError as exc:
        raise invalid_request(f"slide_mechanism: {exc}", op) from exc

    slide, part = reg.world_shape(doc, slide_rec), reg.world_shape(doc, part_rec)
    cavity, core = reg.world_shape(doc, cavity_rec), reg.world_shape(doc, core_rec)
    if any(s.isNull() or not s.Solids for s in (slide, part, cavity, core)):
        raise geometry_failed("slide_mechanism: the slide, the part and both halves must be solids", op)
    reach = SEARCH_LIMIT_FACTOR * max(_box(cavity).DiagonalLength, _box(part).DiagonalLength)

    # the direction the slide leaves along: the candidates that clear the part and both halves, the outward one
    slide_centre = g.vec(_box(slide).Center)
    outward = g.sub(slide_centre, g.vec(_box(part).Center))
    free = [d for d in _perpendicular_candidates(pull) if _leaves(slide, d, [part, cavity, core], reach)]
    if not free:
        raise unrecoverable(
            geometry_failed(
                "slide_mechanism: the slide leaves the part and the halves along none of the directions across the pull",
                op,
            )
        )
    direction = max(free, key=lambda d: g.dot(d, outward))
    slide_length = _extent_along(slide, direction)
    try:
        release = Release(slide, part, direction, pull, slide_length)
        depth = release.depth(slide_length)
    except ValueError as exc:
        raise unrecoverable(geometry_failed(f"slide_mechanism: {exc}", op)) from exc
    stroke = (
        float(params["stroke_mm"]) if params.get("stroke_mm") is not None else depth + float(params["clearance_mm"])
    )
    if stroke < depth - 1e-6 or release.shared(stroke) > CLEAR_TOL_MM3:
        raise unrecoverable(
            geometry_failed(
                f"slide_mechanism: a stroke of {stroke:.3f} mm does not clear the undercut: the slide needs {depth:.3f} mm "
                f"along ({direction[0]:.3g}, {direction[1]:.3g}, {direction[2]:.3g}) before it is free of the part",
                op,
            )
        )

    # the frame: x = outward direction, z = pull, origin on the slide's back face, at the parting plane
    place0 = _frame_of(direction, pull)
    slide_l, cav_l, core_l = (_box(_shape_in(s, place0)) for s in (slide, cavity, core))
    z_cavity, z_core = cav_l.ZMax, core_l.ZMin
    host = params.get("host") or (
        "cavity" if (slide_l.ZMin + slide_l.ZMax) / 2.0 < (z_cavity + z_core) / 2.0 else "core"
    )
    s = -1 if host == "cavity" else 1
    z_part = z_cavity if host == "cavity" else z_core
    e_centre = (slide_l.YMin + slide_l.YMax) / 2.0
    origin = place0.multVec(Vector(slide_l.XMax, e_centre, z_part))
    frame = {"origin": (origin.x, origin.y, origin.z), "x": tuple(direction), "z": tuple(pull)}
    if host == "cavity":
        qa, qb = z_part - slide_l.ZMax, z_part - slide_l.ZMin
    else:
        qa, qb = slide_l.ZMin - z_part, slide_l.ZMax - z_part
    insert = {
        "d0": slide_l.XMin - slide_l.XMax,
        "e0": slide_l.YMin - e_centre,
        "e1": slide_l.YMax - e_centre,
        "qa": qa,
        "qb": qb,
    }
    try:
        n = sm.size(insert, stroke, alpha_deg, beta_deg, float(params["pin_diameter_mm"]))
        pin_options = [dict(o) for o in params.get("pin_options") or ()]
        cut = sm.pin_length(n)
        pin_row = sm.pick_pin(pin_options, cut)
        wear_options = [dict(o) for o in params.get("wear_options") or ()]
        wear_row = sm.pick_wear(wear_options, n["width"], n["back_thickness"] + stroke)
    except sm.SlideError as exc:
        raise invalid_request(f"slide_mechanism: {exc}", op) from exc
    mech = Mechanism(
        n,
        s,
        stroke,
        frame,
        pin_row,
        wear_row,
        str(params.get("catalog_family", "DME")),
        dict(params.get("sources") or {}),
        cut,
    )
    mech.dimple_d = sm.face_d(n, n["ball_q"]) / 2.0
    mech.ball_d = mech.dimple_d + stroke
    specs = mech.specs()
    report: list[str] = []
    expected: list[tuple[Any, float, str]] = []
    built: dict[str, Any] = {}

    def build(ctx: oc.OpContext) -> None:
        for number, spec in enumerate(specs):
            label = ctx.label() if number == 0 else ctx.label(spec["role"])
            built[spec["role"]] = mp.build_part(ctx, spec, label, expected)

        def post_check() -> None:
            mp.check_volumes(expected, op)
            report.extend(
                _verify(doc, built, slide_rec, part_rec, mech, direction, pull, depth, host, alpha_deg, beta_deg, op)
            )

        ctx.post_check = post_check

    result = oc.run_op(doc, "slide_mechanism", params, build)
    result["text"] = "\n".join(["bodies: " + ", ".join(f"{role}={rec.id}" for role, rec in built.items()), *report])
    return result


def _engagement(pin: Any, mech: "Mechanism", opening: g.Vec3) -> tuple[float, float]:
    """How long the angle pin is inside the carrier (the volume of the pin inside the carrier's outline over its
    cross-section), closed and with the pin half moved by ``opening``."""
    place = mp.frame_placement(mech.frame)
    pts = [place.multVec(Vector(*p)) for p in mech.carrier_outline]
    face = Part.Face(Part.makePolygon([*pts, pts[0]]))
    envelope = face.extrude(place.Rotation.multVec(Vector(0.0, -mech.n["width"], 0.0)) + Vector(0, 0, 0))
    envelope.translate(place.Rotation.multVec(Vector(0.0, mech.n["width"] / 2.0, 0.0)))
    area = math.pi * (mech.pin_row["d"] / 2.0) ** 2
    closed = _shared(pin, envelope) / area
    return closed, _shared(_moved(pin, opening), envelope) / area


def _where(a: Any, b: Any, mech: "Mechanism") -> str:
    """Where two overlapping parts overlap, in the slide frame (d outward, e across, depth q): what to look at."""
    try:
        local = a.common(b)
        local.transformShape(mp.frame_placement(mech.frame).inverse().toMatrix(), True)
        box = local.optimalBoundingBox(False, False)
        return (
            f" (d {box.XMin:.1f}..{box.XMax:.1f}, e {box.YMin:.1f}..{box.YMax:.1f}, "
            f"depth {sorted((mech.s * box.ZMin, mech.s * box.ZMax))[0]:.1f}..{sorted((mech.s * box.ZMin, mech.s * box.ZMax))[1]:.1f} mm)"
        )
    except Exception:  # noqa: BLE001 - a diagnostic only
        return ""


def _verify(
    doc: reg.DocumentRec,
    built: dict[str, reg.BodyRec],
    slide_rec: reg.BodyRec,
    part_rec: reg.BodyRec,
    mech: Mechanism,
    direction: g.Vec3,
    pull: g.Vec3,
    depth: float,
    host: str,
    alpha_deg: float,
    beta_deg: float,
    op: str,
) -> list[str]:
    for rec in built.values():
        rec.invalidate()
    shapes = {role: reg.world_shape(doc, rec) for role, rec in built.items()}
    shapes["slide"] = reg.world_shape(doc, slide_rec)
    shapes["part"] = reg.world_shape(doc, part_rec)
    lines: list[str] = []

    # the angles, measured on the finished bodies
    pin_axis = None
    for face in shapes["angle_pin"].Faces:
        if g.surface_kind(face) == "cylinder" and abs(face.Surface.Radius - mech.pin_row["d"] / 2) < 1e-6:
            pin_axis = g.vec(face.Surface.Axis)
            break
    heel_face = None
    for face in shapes["locking_heel"].Faces:
        if g.surface_kind(face) == "plane":
            normal = g.vec(face.normalAt(*face.Surface.parameter(face.CenterOfMass)))
            if g.dot(normal, direction) < -0.1 and abs(g.dot(normal, pull)) > 0.05:
                heel_face = face
                break
    if pin_axis is None or heel_face is None:
        raise unrecoverable(
            geometry_failed("slide_mechanism: cannot find the pin axis or the heel face to measure the angles", op)
        )
    alpha_m = math.degrees(math.acos(min(1.0, abs(g.dot(g.unit(pin_axis), pull)))))
    beta_m = _angle_of_face(heel_face, pull)
    if abs(alpha_m - alpha_deg) > ANGLE_TOL_DEG or abs(beta_m - beta_deg) > ANGLE_TOL_DEG or not beta_m > alpha_m:
        raise unrecoverable(
            geometry_failed(
                f"slide_mechanism: the heel measures {beta_m:.3f} deg and the pin {alpha_m:.3f} deg (asked {beta_deg:g} / {alpha_deg:g}): "
                "the heel must be steeper than the pin",
                op,
            )
        )
    lines.append(
        f"angles measured on the bodies: angle pin {alpha_m:.3f} deg, locking heel {beta_m:.3f} deg from the pull (heel steeper by {beta_m - alpha_m:.3f})"
    )
    lines.append(
        f"slide {host} half, direction ({direction[0]:.3g}, {direction[1]:.3g}, {direction[2]:.3g}): undercut depth {depth:.3f} mm, "
        f"stroke {mech.stroke:.3f} mm (clears the part), opening travel {mech.n['opening']:.3f} mm"
    )

    # interference, closed and open
    s = mech.s
    away = g.scale(pull, -float(s))  # from the host half to the other one
    pin_group = ("angle_pin", "locking_heel")
    slide_group = ("slide", "carrier")
    open_pose = {
        **{r: g.scale(direction, mech.stroke) for r in slide_group},
        **{r: g.scale(away, mech.n["opening"]) for r in pin_group},
    }
    roles = list(shapes)
    problems: list[str] = []
    counts = {}
    for state, offsets in (("closed", {}), ("open", open_pose)):
        placed = {r: (_moved(shapes[r], offsets[r]) if r in offsets else shapes[r]) for r in roles}
        pairs = 0
        for a, b in itertools.combinations(roles, 2):
            if state == "closed" and {a, b} == {"detent_ball", "carrier"}:
                continue  # spring-loaded: it rides into the flat side of the carrier by the depth of the dimple
            pairs += 1
            shared = _shared(placed[a], placed[b])
            if shared > CLEAR_TOL_MM3:
                problems.append(
                    f"{a} and {b} share {shared:.4g} mm3 in the {state} position{_where(placed[a], placed[b], mech)}"
                )
        counts[state] = pairs
    if problems:
        raise unrecoverable(geometry_failed("slide_mechanism: " + "; ".join(problems[:6]), op))
    lines.append(
        f"no interference: {counts['closed']} pairs checked closed, {counts['open']} open (slide moved {mech.stroke:.3f} mm along d, "
        f"pin half moved {mech.n['opening']:.3f} mm away along the pull); the detent ball rides {sm.DIMPLE_DEPTH_MM:g} mm into the flat side when closed"
    )
    closed_in, open_in = _engagement(shapes["angle_pin"], mech, g.scale(away, mech.n["opening"]))
    wanted = sm.PIN_ENGAGEMENT_DIAMETERS * mech.pin_row["d"] * 0.95
    if open_in < wanted:
        raise unrecoverable(
            geometry_failed(
                f"slide_mechanism: the angle pin is only {open_in:.1f} mm inside the carrier when open "
                f"({closed_in:.1f} mm closed); it must stay at least {wanted:.1f} mm in its bore",
                op,
            )
        )
    lines.append(f"angle pin inside the carrier: {closed_in:.1f} mm closed, {open_in:.1f} mm open (at least {wanted:.1f})")
    pin_row = mech.pin_row
    lines.append(
        f"angle pin {pin_row['item']} (stock {pin_row['length']:g} mm, cut to {mech.pin_cut:.2f}); "
        f"pin bore {pin_row['d'] + sm.PIN_HOLE_CLEARANCE_MM:g} mm; wear plate {mech.wear_row['item']}"
    )
    lines.append("not cut: the pockets of the plates and the inserts for the carrier, gibs, heel and wear plate")
    return lines


__all__ = ["Mechanism", "slide_mechanism"]
