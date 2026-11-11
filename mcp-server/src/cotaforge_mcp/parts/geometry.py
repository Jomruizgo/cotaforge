"""Section properties of the catalog profiles, computed from the nominal dimensions (closed forms, mm in, mm out).

Conventions, stated once because a catalog number is only as good as its convention:

* Steel density 7850 kg/m3 (``DENSITY_KG_M3``).
* Hollow sections (square, rectangular, round) use the NOMINAL wall thickness ``t`` (no 0.93 design factor) and, for
  the square and rectangular ones, the outside corner radius ``Ro = 2 t`` and the inside radius ``Ri = t``. 2 t is the
  usual outside radius of cold-formed welded sections (EN 10219 for t <= 6 mm; the Steel Tube Institute quotes about
  2 t for ASTM A500 sections). The standards only bound the radius (ASTM A500 allows up to 3 t), so these properties
  are accurate to a few percent for a real tube, not to the fourth digit.
* Equal angles are taken with sharp corners (no root fillet, no toe radius): the real section is up to about 1 to 3 %
  heavier because of the fillets.

Axes: for a rectangular tube ``b`` runs along x and ``h`` along y, so ``Ix`` is the inertia about the axis parallel to
``b`` (the strong axis when ``h > b``). Every function returns plain floats in mm, mm2, mm3 and mm4; ``in_cm`` converts.
"""

import math

DENSITY_KG_M3 = 7850.0

OUTER_RADIUS_FACTOR = 2.0
"""Outside corner radius of square/rectangular tubes, in wall thicknesses."""
INNER_RADIUS_FACTOR = 1.0
"""Inside corner radius of square/rectangular tubes, in wall thicknesses."""


def mass_per_metre_kg(area_mm2: float) -> float:
    """kg per metre of a profile of ``area_mm2`` at ``DENSITY_KG_M3``."""
    return area_mm2 * 1e-6 * DENSITY_KG_M3


_TO_CM = {"mm2": ("cm2", 1e-2), "mm4": ("cm4", 1e-4), "mm3": ("cm3", 1e-3), "mm": ("cm", 1e-1)}


def in_cm(props: dict[str, float]) -> dict[str, float]:
    """The same properties in the units the section tables use: cm2, cm4, cm3, cm (keys renamed to match)."""
    out: dict[str, float] = {}
    for key, value in props.items():
        stem, _, unit = key.rpartition("_")
        new_unit, factor = _TO_CM[unit]
        out[f"{stem}_{new_unit}"] = value * factor
    return out


def _rounded_rect(b: float, h: float, r: float) -> tuple[float, float, float]:
    """Area, Ix (about the axis parallel to ``b``) and Iy of a ``b`` x ``h`` rectangle with corner radius ``r``."""
    area = b * h - (4 - math.pi) * r * r
    spandrel = (1 - math.pi / 4) * r * r  # the material the rounding removes from one corner
    centroid = r * (5 / 6 - math.pi / 4) / (1 - math.pi / 4)  # its distance to the two straight edges
    own_inertia = r**4 * (1 - 5 * math.pi / 16) - spandrel * centroid**2  # about its own centroidal axes
    ix = b * h**3 / 12 - 4 * (own_inertia + spandrel * (h / 2 - centroid) ** 2)
    iy = h * b**3 / 12 - 4 * (own_inertia + spandrel * (b / 2 - centroid) ** 2)
    return area, ix, iy


def rect_tube(b: float, h: float, t: float) -> dict[str, float]:
    """``b`` x ``h`` x ``t`` rectangular or square tube: A mm2, Ix, Iy mm4 (x parallel to ``b``), Wx, Wy mm3 (elastic),
    rx, ry mm."""
    outer = _rounded_rect(b, h, OUTER_RADIUS_FACTOR * t)
    inner = _rounded_rect(b - 2 * t, h - 2 * t, INNER_RADIUS_FACTOR * t)
    area, ix, iy = outer[0] - inner[0], outer[1] - inner[1], outer[2] - inner[2]
    return {
        "area_mm2": area,
        "ix_mm4": ix,
        "iy_mm4": iy,
        "wx_mm3": ix / (h / 2),
        "wy_mm3": iy / (b / 2),
        "rx_mm": math.sqrt(ix / area),
        "ry_mm": math.sqrt(iy / area),
    }


def round_tube(d: float, t: float) -> dict[str, float]:
    """``d`` outside diameter x ``t`` round tube: A mm2, I mm4, W mm3 (elastic), r mm."""
    inner = d - 2 * t
    area = math.pi / 4 * (d * d - inner * inner)
    i = math.pi / 64 * (d**4 - inner**4)
    return {"area_mm2": area, "i_mm4": i, "w_mm3": i / (d / 2), "r_mm": math.sqrt(i / area)}


def equal_angle(leg: float, t: float) -> dict[str, float]:
    """``leg`` x ``leg`` x ``t`` equal angle with sharp corners: A mm2, centroid distance ``c_mm`` from the outer back
    of a leg, I about the centroidal axes parallel to the legs (``ix_mm4``, equal to Iy), the principal minor axis
    (``imin_mm4``) and the matching W (against the farthest fibre), r values."""
    a_vert, a_horiz = leg * t, (leg - t) * t
    area = a_vert + a_horiz
    # Vertical leg: x in [0, t], y in [0, leg]. Horizontal leg: x in [t, leg], y in [0, t].
    c = (a_vert * t / 2 + a_horiz * (leg + t) / 2) / area  # = cx = cy by symmetry
    cy_vert, cy_horiz = leg / 2, t / 2
    ix = t * leg**3 / 12 + a_vert * (cy_vert - c) ** 2 + (leg - t) * t**3 / 12 + a_horiz * (cy_horiz - c) ** 2
    ixy = a_vert * (t / 2 - c) * (cy_vert - c) + a_horiz * ((leg + t) / 2 - c) * (cy_horiz - c)
    imin = ix - abs(ixy)
    farthest = leg - c
    return {
        "area_mm2": area,
        "c_mm": c,
        "ix_mm4": ix,
        "wx_mm3": ix / farthest,
        "rx_mm": math.sqrt(ix / area),
        "imin_mm4": imin,
        "rmin_mm": math.sqrt(imin / area),
    }
