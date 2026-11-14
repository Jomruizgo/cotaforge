"""The sizes of a slide mechanism, as plain numbers (no FreeCAD): what ``slide_mech.py`` draws and checks.

Frame of every number: ``d`` outward along the slide's own direction, ``e`` across it, ``q`` DEPTH into the half the slide
rides in, measured from the parting plane (so ``q > 0`` inside the host half). A slide that rides in the cavity (which
lies on the ``-pull`` side) has ``z' = -q``; one that rides in the core has ``z' = +q`` (``sign`` below is that factor).
The angle pin sits in the OTHER half, leans ``alpha`` from the pull and points down and outward; the locking heel is
fixed to the same half and leans ``beta = alpha + extra``.

The kinematics, with the slide held in its guides: when the pin half moves ``O`` away along the pull, the pin (inclined
``alpha``) moves the slide ``S = O * tan(alpha)`` outward, so the opening travel the stroke needs is ``O = S / tan(alpha)``
and the pin slides ``S / sin(alpha)`` along its bore. The heel, ``beta > alpha``, leaves the carrier at once:
its face recedes ``O * tan(beta)`` from the carrier, which only moves ``O * tan(alpha)``.
"""

import math
from typing import Any

GAP_TO_PARTING_MM = 0.5
"""The carrier stops this far below the parting plane (clamped to the insert's own depth)."""
GROOVE_DEPTH_MM = 4.0
GROOVE_HEIGHT_MM = 8.0
GROOVE_FIT_MM = 0.2
GIB_STEM_MM = 10.0
GIB_GAP_MM = 0.1
BALL_D_MM = 6.0
MIN_CARRIER_HEIGHT_MM = 25.0
"""Tall enough for the ball detent to sit clear of the groove: (height / 2 - groove height / 2) must hold the ball, its bore and a margin."""
BALL_BORE_D_MM = 6.2
DIMPLE_DEPTH_MM = 1.5
HEEL_THICKNESS_MM = 8.0
HEEL_MOUNT_MM = 25.0
CARRIER_MARGIN_MM = 20.0
CARRIER_MIN_WIDTH_MM = 40.0
PIN_HOLE_CLEARANCE_MM = 0.2
PIN_ENGAGEMENT_DIAMETERS = 1.5
PIN_SEAT_DIAMETERS = 2.0
PIN_TIP_SHORT_MM = 1.0
"""Added to the share of the pin radius its slanted end reaches (r * sin(alpha)): the tip stays inside the carrier."""
HEEL_FACE_CLEARANCE_MM = 0.0
"""The heel face and the carrier's back face are the same plane at the closed position: they touch (no gap)."""


class SlideError(ValueError):
    """The mechanism cannot be sized from these numbers: the message says what to change."""


def travel_for_stroke(stroke: float, alpha_deg: float) -> float:
    """How far the pin half must open for the slide to move ``stroke``: ``O = S / tan(alpha)``."""
    return stroke / math.tan(math.radians(alpha_deg))


def pin_axis_travel(stroke: float, alpha_deg: float) -> float:
    return stroke / math.sin(math.radians(alpha_deg))


def check_angles(alpha_deg: float, extra_deg: float) -> float:
    """The heel angle; refuses a heel that is not steeper than the pin."""
    beta = alpha_deg + extra_deg
    if not beta > alpha_deg + 1e-9:
        msg = f"the locking heel ({beta:g} deg) must be steeper than the angle pin ({alpha_deg:g} deg)"
        raise SlideError(msg)
    if beta >= 45.0:
        msg = f"the heel angle {beta:g} deg is not below 45 degrees"
        raise SlideError(msg)
    return beta


def size(
    insert: dict[str, float],
    stroke: float,
    alpha_deg: float,
    beta_deg: float,
    pin_d: float,
) -> dict[str, float]:
    """The numbers of the carrier, heel, gibs, bore and pin for an insert box ``{d0, e0, e1, qa, qb}`` (``d0 < 0``: the
    insert's back face is at ``d = 0``; ``qa`` / ``qb`` its nearest and farthest depth)."""
    if insert["qa"] < -1e-6:
        msg = (
            "the slide crosses the parting plane (it lies in both mould halves): a slide mechanism needs one host half"
        )
        raise SlideError(msg)
    alpha, beta = math.radians(alpha_deg), math.radians(beta_deg)
    r_hole = pin_d / 2.0 + PIN_HOLE_CLEARANCE_MM / 2.0
    wall = max(3.0, 0.3 * pin_d)
    g_p = min(GAP_TO_PARTING_MM, max(insert["qa"], 0.0))
    engage = PIN_ENGAGEMENT_DIAMETERS * pin_d
    axis_len = pin_axis_travel(stroke, alpha_deg) + engage
    tip_short = PIN_TIP_SHORT_MM + pin_d / 2.0 * math.sin(alpha)
    carrier_height = max(g_p + axis_len * math.cos(alpha) + tip_short, insert["qb"] + 3.0, g_p + MIN_CARRIER_HEIGHT_MM)
    carrier_height = math.ceil(carrier_height * 2.0) / 2.0
    width = max(insert["e1"] - insert["e0"] + CARRIER_MARGIN_MM, CARRIER_MIN_WIDTH_MM)
    top_thickness = 2.0 * wall + 2.0 * r_hole / math.cos(alpha)
    height = carrier_height - g_p
    back_thickness = top_thickness + height * math.tan(beta)
    q_groove = (g_p + carrier_height) / 2.0
    ball_q = g_p + (q_groove - GROOVE_HEIGHT_MM / 2.0 - g_p) / 2.0  # the middle of the free stretch above the groove
    return {
        "alpha": alpha,
        "beta": beta,
        "r_hole": r_hole,
        "wall": wall,
        "g_p": g_p,
        "carrier_height": carrier_height,
        "width": width,
        "top_thickness": top_thickness,
        "back_thickness": back_thickness,
        "d_top": wall + r_hole / math.cos(alpha),
        "q_groove": q_groove,
        "ball_q": ball_q,
        "opening": travel_for_stroke(stroke, alpha_deg),
        "axis_travel": pin_axis_travel(stroke, alpha_deg),
        "engage": engage,
        "seat": PIN_SEAT_DIAMETERS * pin_d,
        "tip_short": tip_short,
        "gib_length": back_thickness + stroke + 10.0,
    }


def face_d(n: dict[str, float], q: float) -> float:
    """The ``d`` of the carrier's back face (and of the heel's inner face) at depth ``q``: inclined ``beta``."""
    return n["top_thickness"] + (q - n["g_p"]) * math.tan(n["beta"])


def hole_d(n: dict[str, float], q: float) -> float:
    """The ``d`` of the axis of the pin bore at depth ``q``: inclined ``alpha``."""
    return n["d_top"] + (q - n["g_p"]) * math.tan(n["alpha"])


def pin_length(n: dict[str, float]) -> float:
    """Length under the head the angle pin is cut to: from its seat in the pin half to ``PIN_TIP_SHORT_MM`` above the
    bottom of the carrier."""
    return n["seat"] + (n["carrier_height"] - n["tip_short"]) / math.cos(n["alpha"])


def pick_pin(options: list[dict[str, Any]], needed: float) -> dict[str, Any]:
    """The shortest stock length of the options that is at least ``needed`` (the pin is cut to ``needed``)."""
    fit = sorted((o for o in options if o["length"] >= needed - 1e-9), key=lambda o: o["length"])
    if not fit:
        longest = max((o["length"] for o in options), default=0.0)
        diameter = options[0]["d"] if options else 0
        msg = (
            f"the angle pin must be {needed:.1f} mm long and the longest stock DME angle pin of {diameter:g} mm is "
            f"{longest:g} mm: use a larger angle_deg, a shorter stroke or a thicker pin_diameter_mm"
        )
        raise SlideError(msg)
    return fit[0]


def pick_wear(options: list[dict[str, Any]], width: float, length: float) -> dict[str, Any]:
    """The thinnest, then narrowest, then shortest plate that is at least ``width`` x ``length``."""
    fit = sorted(
        (o for o in options if o["width"] >= width - 1e-9 and o["length"] >= length - 1e-9),
        key=lambda o: (o["thickness"], o["width"], o["length"]),
    )
    if not fit:
        msg = (
            f"no stock DME wear plate covers {width:.1f} x {length:.1f} mm (the largest is 4 x 12 in): "
            "split the slide or give it a shorter stroke"
        )
        raise SlideError(msg)
    return fit[0]
