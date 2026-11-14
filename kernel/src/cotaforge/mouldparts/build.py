"""From a catalogue reference to the parts the CAD draws (``PartSpec``): nominal geometry only, in millimetres.

Each part is a few native features: a revolved profile for a pin, a bush, a ring; a padded rectangle with pocketed holes
for a plate. The volume of every part is worked out here from its numbers (``volume_mm3``) and the CAD checks the body
it drew against it. What the catalogue does not publish is chosen here and said in the part's ``note``.
"""

import math
import re
from collections.abc import Sequence
from dataclasses import dataclass

from cotaforge.contract.mould_parts import (
    AnglePinOption,
    CatalogInfo,
    Circle2D,
    PadFeature,
    PartFrame,
    PartSpec,
    PlaneSpec,
    PocketFeature,
    Rect2D,
    RevolveFeature,
    WearPlateOption,
)
from cotaforge.mouldparts import dme
from cotaforge.mouldparts.dme import IN

KINDS = (
    "mould_base",
    "guide_pin",
    "guide_bush",
    "locating_ring",
    "sprue_bush",
    "ejector_pin",
    "return_pin",
    "angle_pin",
    "wear_plate",
)

Vec = tuple[float, float, float]


class ComponentError(ValueError):
    """The reference or the size is not in the catalogue: the message says what is (the agent reads it)."""


@dataclass(frozen=True)
class Placement:
    """Where a component sits: origin, the direction of its z axis and a hint for its x axis."""

    origin: Vec = (0.0, 0.0, 0.0)
    z: Vec = (0.0, 0.0, 1.0)
    x: Vec | None = None


def _unit(v: Vec) -> Vec:
    n = math.sqrt(sum(c * c for c in v))
    if n < 1e-12:
        msg = "a direction must not be the zero vector"
        raise ComponentError(msg)
    return (v[0] / n, v[1] / n, v[2] / n)


def _dot(a: Vec, b: Vec) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Vec, b: Vec) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def basis(place: Placement) -> tuple[Vec, Vec, Vec]:
    """(ex, ey, ez) of a placement: ez the axis, ex the hint made perpendicular to it (world X, or Y when that is parallel)."""
    ez = _unit(place.z)
    hint = place.x if place.x is not None else ((1.0, 0.0, 0.0) if abs(ez[0]) < 0.9 else (0.0, 1.0, 0.0))
    hx = _dot(hint, ez)
    ex = _unit((hint[0] - hx * ez[0], hint[1] - hx * ez[1], hint[2] - hx * ez[2]))
    return ex, _cross(ez, ex), ez


def _to_world(place: Placement, local: PartFrame) -> PartFrame:
    ex, ey, ez = basis(place)

    def point(v: Vec) -> Vec:
        return tuple(place.origin[i] + v[0] * ex[i] + v[1] * ey[i] + v[2] * ez[i] for i in range(3))  # type: ignore[return-value]

    def direction(v: Vec) -> Vec:
        return tuple(v[0] * ex[i] + v[1] * ey[i] + v[2] * ez[i] for i in range(3))  # type: ignore[return-value]

    return PartFrame(origin=point(local.origin), z=direction(local.z), x=direction(local.x))


def _mm(value_in: float) -> float:
    return value_in * IN


def _num(value: float) -> str:
    return f"{value:.4f}".rstrip("0").rstrip(".")


def _inch_text(value_in: float) -> str:
    return f"{_num(value_in)} in"


def _turned(name: str, points: Sequence[tuple[float, float]]) -> RevolveFeature:
    return RevolveFeature(name=name, profile=tuple((round(r, 6), round(z, 6)) for r, z in points))


def _headed_pin(
    d: float, head_d: float, head_k: float, length: float, name: str = "Pin"
) -> tuple[RevolveFeature, float]:
    """A pin on its z axis: head z in [-K, 0], shaft z in [0, L]."""
    profile = [(0.0, -head_k), (head_d / 2, -head_k), (head_d / 2, 0.0), (d / 2, 0.0), (d / 2, length), (0.0, length)]
    volume = math.pi * ((head_d / 2) ** 2 * head_k + (d / 2) ** 2 * length)
    return _turned(name, profile), volume


def _frame(
    place: Placement, origin: Vec = (0.0, 0.0, 0.0), z: Vec = (0.0, 0.0, 1.0), x: Vec = (1.0, 0.0, 0.0)
) -> PartFrame:
    return _to_world(place, PartFrame(origin=origin, z=z, x=x))


def _list(values: Sequence[float], fmt: str = "{:g}") -> str:
    return ", ".join(fmt.format(v) for v in values)


# --- single parts -------------------------------------------------------------------------------------------------


def guide_pin(ref: str, place: Placement, role: str = "guide_pin") -> PartSpec:
    for pin in dme.GUIDE_PINS:
        for length in pin.lengths_in:
            if pin.item(length) == ref:
                feature, volume = _headed_pin(_mm(pin.d_in), _mm(pin.head_d_in), _mm(pin.head_k_in), _mm(length))
                return PartSpec(
                    role=role,
                    frame=_frame(place),
                    features=(feature,),
                    volume_mm3=volume,
                    catalog=CatalogInfo(
                        family=dme.FAMILY,
                        reference=ref,
                        size=f"guide pin GL, dia {_inch_text(pin.nominal_in)} x {_inch_text(length)}",
                        source=dme.GUIDE_PIN_SOURCE,
                        note="head underside at the frame origin, shaft along +z; no tip chamfer (not dimensioned)",
                    ),
                )
    options = ", ".join(
        f"{p.item(p.lengths_in[0])}-{p.item(p.lengths_in[-1])} (dia {_inch_text(p.nominal_in)})" for p in dme.GUIDE_PINS
    )
    msg = (
        f"{ref!r} is not a DME guide pin GL (COMPONENTS p. 327). Items: {options}; the item number rises by 1 for every 1/2 in "
        "of length, from 1 3/4 in (2 3/4 in for 1 1/4 in dia)"
    )
    raise ComponentError(msg)


def _bushing_of(ref: str) -> tuple[dme.ShoulderBushing, float]:
    for bush in dme.SHOULDER_BUSHINGS:
        for length in bush.LENGTHS_IN:
            if bush.item(length) == ref:
                return bush, length
    options = "; ".join(
        f"ID {_inch_text(b.nominal_in)}: {b.item(b.LENGTHS_IN[0])}-{b.item(b.LENGTHS_IN[-1])}"
        for b in dme.SHOULDER_BUSHINGS
    )
    msg = f"{ref!r} is not a DME steel shoulder bushing (COMPONENTS p. 329). Items: {options}; lengths {_list(dme.ShoulderBushing.LENGTHS_IN)} in"
    raise ComponentError(msg)


def guide_bush(ref: str, place: Placement, role: str = "guide_bush") -> PartSpec:
    bush, length = _bushing_of(ref)
    ri, ro, rh, k = _mm(bush.id_in) / 2, _mm(bush.od_in) / 2, _mm(bush.head_d_in) / 2, _mm(bush.head_k_in)
    length_mm = _mm(length)
    profile = [(ri, -k), (rh, -k), (rh, 0.0), (ro, 0.0), (ro, length_mm), (ri, length_mm)]
    volume = math.pi * ((rh**2 - ri**2) * k + (ro**2 - ri**2) * length_mm)
    return PartSpec(
        role=role,
        frame=_frame(place),
        features=(_turned("Bush", profile),),
        volume_mm3=volume,
        catalog=CatalogInfo(
            family=dme.FAMILY,
            reference=ref,
            size=f"steel shoulder bushing, ID {_inch_text(bush.nominal_in)} x OD {_inch_text(bush.od_in)} x {_inch_text(length)}",
            source=dme.BUSHING_SOURCE,
            note="head top at z = -K, head underside at the frame origin, body along +z; head thickness 3/16 in as drawn",
        ),
    )


def locating_ring(ref: str, place: Placement, role: str = "locating_ring") -> PartSpec:
    row = dme.LOCATING_RINGS.get(ref)
    if row is None:
        msg = f"{ref!r} is not a locating ring this build knows; encoded: {', '.join(dme.LOCATING_RINGS)} (COMPONENTS p. 348)"
        raise ComponentError(msg)
    ro, ri, h = _mm(row["od_in"]) / 2, _mm(row["bore_in"]) / 2, _mm(row["height_in"])
    return PartSpec(
        role=role,
        frame=_frame(place),
        features=(_turned("Ring", [(ri, 0.0), (ro, 0.0), (ro, h), (ri, h)]),),
        volume_mm3=math.pi * (ro**2 - ri**2) * h,
        catalog=CatalogInfo(
            family=dme.FAMILY,
            reference=ref,
            size=f"locating ring dia {_num(row['od_in'])} in x {_inch_text(row['height_in'])}, opening {_inch_text(row['bore_in'])}",
            source=dme.RING_SOURCE,
            note="standard series; the two 5/16-18 flat head screws are not modelled; frame origin on the seat face of the ring",
        ),
    )


def _sprue_parse(ref: str) -> tuple[str, float, int, int]:
    found = re.fullmatch(r"(B\d\d)(\d+?)([13])", ref)
    if found is None or found.group(1) not in dme.SPRUE_B_SERIES or int(found.group(2)) not in dme.SPRUE_ORIFICES_32:
        prefixes = ", ".join(f"{k} (stem {_inch_text(v)})" for k, v in dme.SPRUE_B_SERIES.items())
        msg = (
            f"{ref!r} is not a DME B-series sprue bushing (COMPONENTS p. 344): item = prefix + orifice numerator "
            f"({', '.join(map(str, dme.SPRUE_ORIFICES_32))} = n/32 in) + radius digit (1 = 1/2 in, 3 = 3/4 in), "
            f"e.g. B0271. Prefixes: {prefixes}"
        )
        raise ComponentError(msg)
    prefix, orifice, radius = found.group(1), int(found.group(2)), int(found.group(3))
    return prefix, dme.SPRUE_B_SERIES[prefix], orifice, radius


def sprue_bush(ref: str, place: Placement, role: str = "sprue_bush", trim_stem_in: float | None = None) -> PartSpec:
    prefix, stem_in, orifice, radius = _sprue_parse(ref)
    stem = _mm(stem_in if trim_stem_in is None else trim_stem_in)
    head = _mm(dme.SPRUE_B_HEAD_IN)
    rs, rh = _mm(dme.SPRUE_STEM_D_IN) / 2, _mm(dme.SPRUE_HEAD_D_IN) / 2
    total = stem + head
    r_top = _mm(orifice / 32.0) / 2
    r_bottom = r_top + total * (dme.SPRUE_TAPER_PER_FOOT_IN / 12.0) / 2
    profile = [(r_bottom, 0.0), (rs, 0.0), (rs, stem), (rh, stem), (rh, total), (r_top, total)]
    shell = math.pi * (rs**2 * stem + rh**2 * head)
    bore = math.pi * total / 3.0 * (r_top**2 + r_top * r_bottom + r_bottom**2)
    ground = (
        ""
        if trim_stem_in is None
        else f"; stem ground to {_inch_text(trim_stem_in)} (stock {_inch_text(stem_in)}) to end flush"
    )
    return PartSpec(
        role=role,
        frame=_frame(place),
        features=(_turned("Sprue", profile),),
        volume_mm3=shell - bore,
        catalog=CatalogInfo(
            family=dme.FAMILY,
            reference=ref,
            size=f"sprue bushing B series {prefix}, orifice {orifice}/32 in, seat radius {dme.SPRUE_RADII_IN[radius]:g} in, stem {_inch_text(stem_in)} x dia 1 in, head dia 2 in",
            source=dme.SPRUE_SOURCE,
            note=(
                "stem end at the frame origin, head along +z; bore 1/2 in per foot (included) from the orifice at the head"
                f"{ground}; the spherical seat radius is carried in the reference, not modelled"
            ),
        ),
    )


def ejector_pin(ref: str | None, size: str | None, place: Placement, role: str = "ejector_pin") -> PartSpec:
    if ref is None:
        found = re.fullmatch(r"([\d.]+)x(\d+)", (size or "").replace(" ", "").lower())
        if found is None:
            msg = "ejector_pin needs ref (EJP-EHN-030-0160) or size 'DxL' in mm (3x160)"
            raise ComponentError(msg)
        d, length = float(found.group(1)), int(found.group(2))
    else:
        found = re.fullmatch(r"EJP-EHN-(\d{3})-(\d{4})", ref)
        if found is None:
            msg = f"{ref!r} is not an item EJP-EHN-<D x 10, 3 digits>-<L, 4 digits> (PINS p. 3), e.g. EJP-EHN-030-0160"
            raise ComponentError(msg)
        d, length = int(found.group(1)) / 10.0, int(found.group(2))
    if d not in dme.EJECTOR_PINS:
        msg = f"no DME DIN ejector pin of diameter {d:g} mm; diameters: {_list(sorted(dme.EJECTOR_PINS))}"
        raise ComponentError(msg)
    if length not in dme.EJECTOR_LENGTHS_MM:
        msg = f"length {length} mm is not in the series {_list(dme.EJECTOR_LENGTHS_MM)}"
        raise ComponentError(msg)
    head_d, head_k = dme.EJECTOR_PINS[d]
    feature, volume = _headed_pin(d, head_d, head_k, float(length))
    item = f"EJP-EHN-{round(d * 10):03d}-{length:04d}"
    return PartSpec(
        role=role,
        frame=_frame(place),
        features=(feature,),
        volume_mm3=volume,
        catalog=CatalogInfo(
            family=dme.FAMILY,
            reference=item,
            size=f"DIN ejector pin nitrided, D {d:g} x L {length} mm, head {head_d:g} x {head_k:g}",
            source=dme.EJECTOR_SOURCE,
            note="L taken as the length under the head; head underside at the frame origin; the stock-length chart is not read",
        ),
    )


def return_pin(ref: str, place: Placement, role: str = "return_pin", trim_length_in: float | None = None) -> PartSpec:
    row = dme.RETURN_PINS.get(ref)
    if row is None:
        msg = f"{ref!r} is not a DME inch return pin (COMPONENTS p. 399): items {_list([int(k) for k in dme.RETURN_PINS], '{:d}')}"
        raise ComponentError(msg)
    d, head_d, length = row
    used = length if trim_length_in is None else trim_length_in
    feature, volume = _headed_pin(_mm(d), _mm(head_d), _mm(dme.RETURN_PIN_HEAD_K_IN), _mm(used))
    ground = (
        ""
        if trim_length_in is None
        else f"; ground to {_inch_text(used)} (stock {_inch_text(length)}) so its end is flush"
    )
    return PartSpec(
        role=role,
        frame=_frame(place),
        features=(feature,),
        volume_mm3=volume,
        catalog=CatalogInfo(
            family=dme.FAMILY,
            reference=ref,
            size=f"return pin dia {_inch_text(d)} x {_inch_text(length)}, head dia {_inch_text(head_d)} x 1/4 in",
            source=dme.RETURN_PIN_SOURCE,
            note=f"head underside at the frame origin, shaft along +z{ground}",
        ),
    )


def angle_pin_rows(d_mm: int) -> tuple[AnglePinOption, ...]:
    if d_mm not in dme.ANGLE_PINS:
        msg = f"no DME metric angle pin of diameter {d_mm} mm; diameters: {_list(sorted(dme.ANGLE_PINS))}"
        raise ComponentError(msg)
    head_d, head_k, n, lengths = dme.ANGLE_PINS[d_mm]
    return tuple(
        AnglePinOption(d=d_mm, head_d=head_d, head_k=head_k, n=n, length=length, item=f"APD{d_mm}{length}")
        for length in lengths
    )


def angle_pin(ref: str, place: Placement, role: str = "angle_pin", cut_length: float | None = None) -> PartSpec:
    found = re.fullmatch(r"APD(\d\d)(\d{2,3})", ref)
    row = None
    if found is not None:
        row = (
            next((r for r in angle_pin_rows(int(found.group(1))) if r.item == ref), None)
            if int(found.group(1)) in dme.ANGLE_PINS
            else None
        )
    if row is None:
        msg = f"{ref!r} is not a DME metric angle pin APD<D><L> (COMPONENTS p. 29); diameters {_list(sorted(dme.ANGLE_PINS), '{:d}')}"
        raise ComponentError(msg)
    used = row.length if cut_length is None else cut_length
    feature, volume = _headed_pin(row.d, row.head_d, row.head_k, used)
    cut = (
        ""
        if cut_length is None
        else f"; cut to {used:.2f} mm from stock {row.length:g} mm (note 2 of the page: cut to the travel needed)"
    )
    return PartSpec(
        role=role,
        frame=_frame(place),
        features=(feature,),
        volume_mm3=volume,
        catalog=CatalogInfo(
            family=dme.FAMILY,
            reference=ref,
            size=f"metric angle pin D {row.d:g} x L {row.length:g} mm, head {row.head_d:g} x {row.head_k:g}",
            source=dme.ANGLE_PIN_SOURCE,
            note=f"head underside at the frame origin, shaft along +z{cut}; N = {row.n:g} (as published, not modelled)",
        ),
    )


def wear_rows() -> tuple[WearPlateOption, ...]:
    rows = []
    for t, widths in dme.WEAR_PLATES.items():
        for w in widths:
            lengths = dme.WEAR_4IN_LENGTHS if w == 4.0 else dme.WEAR_LENGTHS_IN[t]
            rows.extend(
                WearPlateOption(item=dme.wear_item(t, w, ln), thickness=t * IN, width=w * IN, length=ln * IN)
                for ln in lengths
            )
    return tuple(rows)


def wear_plate(ref: str, place: Placement, role: str = "wear_plate") -> PartSpec:
    row = next((r for r in wear_rows() if r.item == ref), None)
    if row is None:
        msg = f"{ref!r} is not a DME self-lubricating wear plate SLP<nnnn> (COMPONENTS p. 35); thickness 3/8, 1/2, 5/8 in, e.g. SLP0604 = 1/2 x 1.5 x 4 in"
        raise ComponentError(msg)
    return PartSpec(
        role=role,
        frame=_frame(place),
        features=(
            PadFeature(name="Plate", outline=Rect2D(cx=0.0, cy=0.0, w=row.width, h=row.length), depth=row.thickness),
        ),
        volume_mm3=row.width * row.length * row.thickness,
        catalog=CatalogInfo(
            family=dme.FAMILY,
            reference=ref,
            size=f"self-lubricating wear plate {row.thickness / IN:g} x {row.width / IN:g} x {row.length / IN:g} in",
            source=dme.WEAR_SOURCE,
            note="width along x, length along y, thickness along +z from the frame origin (its bottom face); fixing screws not modelled",
        ),
    )


# --- the mould base kit -------------------------------------------------------------------------------------------

_BASE_REF = re.compile(r"XPA(\d{4})-(\d+)-(\d+)-(\d+)")


def parse_base(ref: str | None, size: str | None) -> tuple[dme.MouldBase, float, float, float, str]:
    """(base, A plate in, B plate in, rail height in, configuration code)."""
    if ref is not None:
        found = _BASE_REF.fullmatch(ref.strip().upper())
        if found is None:
            msg = "a mould base ref is the configuration code of page 32: XPA<size>-<A plate code>-<B plate code>-<rail code>, e.g. XPA1012-17-17-35"
            raise ComponentError(msg)
        base_size, codes = found.group(1), tuple(int(found.group(i)) for i in (2, 3, 4))
    else:
        base_size, codes = (size or "").strip(), dme.BASE_DEFAULT_CODES
    base = dme.BASES_BY_SIZE.get(base_size)
    if base is None:
        msg = f"mould base size {base_size!r} is not encoded; encoded: {', '.join(dme.BASES_BY_SIZE)} (A-series, 2.0). The other sizes are on pages 34-76 of the same catalogue"
        raise ComponentError(msg)
    a_code, b_code, rail_code = codes
    if a_code not in dme.PLATE_CODES_IN or b_code not in dme.PLATE_CODES_IN or rail_code not in dme.RAIL_CODES_IN:
        msg = f"plate codes {_list(sorted(dme.PLATE_CODES_IN), '{:d}')} (thickness 1.875..5.875 in), rail codes {_list(sorted(dme.RAIL_CODES_IN), '{:d}')} (2.5..4.5 in)"
        raise ComponentError(msg)
    code = f"XPA{base.size}-{a_code}-{b_code}-{rail_code}"
    return base, dme.PLATE_CODES_IN[a_code], dme.PLATE_CODES_IN[b_code], dme.RAIL_CODES_IN[rail_code], code


def _largest_at_most(values: Sequence[float], limit: float) -> float:
    fit = [v for v in values if v <= limit + 1e-9]
    if not fit:
        msg = f"no stock length up to {limit:.3f} in"
        raise ComponentError(msg)
    return max(fit)


def _smallest_at_least(values: Sequence[float], limit: float) -> float:
    fit = [v for v in values if v >= limit - 1e-9]
    if not fit:
        msg = f"no stock length from {limit:.3f} in"
        raise ComponentError(msg)
    return min(fit)


def _plate(
    role: str,
    code: str,
    size: str,
    place: Placement,
    w: float,
    ln: float,
    z0: float,
    thickness: float,
    pockets: Sequence[PocketFeature] = (),
    cut_volume: float = 0.0,
    cx: float = 0.0,
    cy: float = 0.0,
    note: str = "",
) -> PartSpec:
    """A plate whose lower face is at ``z0`` (mm, base axes) and whose size is ``w`` (x) by ``ln`` (y)... see callers."""
    pad = PadFeature(
        name="Plate", plane=PlaneSpec(origin=(0.0, 0.0, z0)), outline=Rect2D(cx=cx, cy=cy, w=w, h=ln), depth=thickness
    )
    return PartSpec(
        role=role,
        frame=_frame(place),
        features=(pad, *pockets),
        volume_mm3=w * ln * thickness - cut_volume,
        catalog=CatalogInfo(
            family=f"{dme.FAMILY} 2.0 A-series mould base",
            reference=code,
            size=size,
            source=dme.BASE_SOURCE,
            note=note,
        ),
    )


def _holes(z_face: float, face_normal: float, circles: Sequence[Circle2D], depth: float, name: str) -> PocketFeature:
    """Holes that start on the face at ``z_face`` and go ``depth`` into the plate; the face looks along ``face_normal`` (+1 or -1)."""
    return PocketFeature(
        name=name,
        plane=PlaneSpec(origin=(0.0, 0.0, z_face), normal=(0.0, 0.0, face_normal)),
        circles=tuple(circles),
        depth=depth,
    )


def _area(circles: Sequence[Circle2D]) -> float:
    return sum(math.pi * (c.d / 2) ** 2 for c in circles)


def mould_base(ref: str | None, size: str | None, place: Placement) -> tuple[PartSpec, ...]:
    """The kit of a 2-plate base: nine plates, four leader pins and bushes, four return pins, locating ring and sprue
    bushing. Base axes: origin at the centre of the parting face, +z towards the B half (the core, the ejector set, the
    bottom clamp plate), x along L, y along W. The A half (cavity side) is at z < 0."""
    base, a_in, b_in, rail_in, code = parse_base(ref, size)
    w, ln = _mm(base.width_in), _mm(base.length_in)
    a, b = _mm(a_in), _mm(b_in)
    cp, su, er, eb, bcp, rail = (_mm(v) for v in (base.cp_in, base.su_in, base.er_in, base.eb_in, base.bcp_in, rail_in))
    z_su0 = b  # support plate lower face
    z_rail0 = b + su
    z_bcp0 = z_rail0 + rail
    z_eb0 = z_bcp0 - eb
    z_er0 = z_eb0 - er
    size_text = f"{base.size} {_num(base.width_in)} x {_num(base.length_in)} in"
    # leader pins: 3 on the standard positions, the offset one at (-x, +y); the return pins likewise
    sx, sy = base.lp_x_in, base.lp_y_in
    pins_in = [(sx, sy), (sx, -sy), (-sx, -sy), (-base.lp_x_offset_in, sy)]
    pins = [(_mm(x), _mm(y)) for x, y in pins_in]
    ry = base.rp_y_in
    rets = [
        (_mm(x), _mm(y))
        for x, y in [(base.rp_x_in, ry), (base.rp_x_in, -ry), (-base.rp_x_in, -ry), (-base.rp_x_offset_in, ry)]
    ]
    pin_d, bush_od = _mm(base.pin_d_in), _mm(base.bush_od_in)
    rp_d = _mm(base.rp_d_in)

    # the catalogue items that go in this base, chosen from the thicknesses
    pin_row = next(p for p in dme.GUIDE_PINS if p.nominal_in == base.pin_d_in)
    head_k = pin_row.head_k_in
    pin_len_in = _largest_at_most(pin_row.lengths_in, (b_in - head_k) + (a_in - 0.375))
    bush_row = next(p for p in dme.SHOULDER_BUSHINGS if p.nominal_in == base.pin_d_in)
    bush_len_in = _largest_at_most(bush_row.LENGTHS_IN, a_in - bush_row.head_k_in)
    er_top_in = (z_er0 + er) / IN
    rp_need_in = er_top_in - dme.RETURN_PIN_HEAD_K_IN
    rp_items = sorted((v[2], k) for k, v in dme.RETURN_PINS.items() if v[0] == base.rp_d_in)
    rp_len_in, rp_item = next((ln_, k) for ln_, k in rp_items if ln_ >= rp_need_in - 1e-9)
    sprue_prefix = next(k for k, v in dme.SPRUE_B_SERIES.items() if v >= a_in - 1e-9)
    sprue_ref = f"{sprue_prefix}71"  # orifice 7/32 in, seat radius 1/2 in: the catalogue's own example, B0271

    pin_circles = [Circle2D(cx=x, cy=y, d=pin_d) for x, y in pins]
    bush_circles = [Circle2D(cx=x, cy=y, d=bush_od) for x, y in pins]
    bush_head = [Circle2D(cx=x, cy=y, d=_mm(bush_row.head_d_in)) for x, y in pins]
    pin_head = [Circle2D(cx=x, cy=y, d=_mm(pin_row.head_d_in)) for x, y in pins]
    ret_circles = [Circle2D(cx=x, cy=y, d=rp_d) for x, y in rets]
    ret_head = [Circle2D(cx=x, cy=y, d=_mm(dme.RETURN_PINS[rp_item][1])) for x, y in rets]
    sprue_hole = Circle2D(cx=0.0, cy=0.0, d=_mm(base.sprue_stem_d_in))
    head_hole = Circle2D(cx=0.0, cy=0.0, d=_mm(dme.SPRUE_HEAD_D_IN))
    cbore = Circle2D(cx=0.0, cy=0.0, d=_mm(base.ring_cbore_d_in))
    k_bush, k_pin, k_ret = _mm(bush_row.head_k_in), _mm(pin_row.head_k_in), _mm(dme.RETURN_PIN_HEAD_K_IN)

    parts: list[PartSpec] = []
    # top clamp plate: the 2 in opening for the sprue bushing head and the 4 in counterbore for the locating ring
    cbore_depth = _mm(base.ring_cbore_depth_in)
    z_tcp0 = -a - cp
    parts.append(
        _plate(
            "top_clamp_plate",
            code,
            f"top clamp plate, {size_text} x {_inch_text(base.cp_in)}",
            place,
            ln,
            w,
            z_tcp0,
            cp,
            pockets=(
                _holes(z_tcp0, -1.0, [cbore], cbore_depth, "RingSeat"),
                _holes(z_tcp0 + cp, 1.0, [head_hole], cp, "SprueHeadHole"),
            ),
            cut_volume=math.pi * (cbore.d / 2) ** 2 * cbore_depth
            + math.pi * (head_hole.d / 2) ** 2 * (cp - cbore_depth),
            note="the 2 in opening is also the seat of the sprue bushing head; assembly screws and clamp slots are not modelled",
        )
    )
    parts.append(
        _plate(
            "a_plate",
            code,
            f"A plate (cavity side), {size_text} x {_inch_text(a_in)}",
            place,
            ln,
            w,
            -a,
            a,
            pockets=(
                _holes(0.0, 1.0, bush_circles, a, "BushBores"),
                _holes(0.0, 1.0, bush_head, k_bush, "BushHeadSeats"),
                _holes(0.0, 1.0, [sprue_hole], a, "SprueBore"),
            ),
            cut_volume=_area(bush_circles) * (a - k_bush) + _area(bush_head) * k_bush + _area([sprue_hole]) * a,
            note="no cavity pocket: the cavity insert comes from mould_split; the bush head seats are cut to the bushing head diameter",
        )
    )
    parts.append(
        _plate(
            "b_plate",
            code,
            f"B plate (core side), {size_text} x {_inch_text(b_in)}",
            place,
            ln,
            w,
            0.0,
            b,
            pockets=(
                _holes(b, 1.0, pin_circles, b, "PinBores"),
                _holes(b, 1.0, pin_head, k_pin, "PinHeadSeats"),
                _holes(b, 1.0, ret_circles, b, "ReturnPinBores"),
            ),
            cut_volume=_area(pin_circles) * (b - k_pin) + _area(pin_head) * k_pin + _area(ret_circles) * b,
            note="no core pocket: the core insert comes from mould_split; ejector pin holes are not cut (their places depend on the part)",
        )
    )
    parts.append(
        _plate(
            "support_plate",
            code,
            f"support plate, {size_text} x {_inch_text(base.su_in)}",
            place,
            ln,
            w,
            z_su0,
            su,
            pockets=(_holes(z_su0 + su, 1.0, ret_circles, su, "ReturnPinBores"),),
            cut_volume=_area(ret_circles) * su,
            note="only the return pin bores are cut; the pin heads of the leader pins are held by this plate",
        )
    )
    rail_w = (base.width_in - base.rail_gap_in) / 2.0
    rail_mm = _mm(rail_w)
    for name, sign in (("rail_left", 1.0), ("rail_right", -1.0)):
        parts.append(
            _plate(
                name,
                code,
                f"rail {_num(rail_w)} x {_num(base.length_in)} x {_inch_text(rail_in)}",
                place,
                ln,
                rail_mm,
                z_rail0,
                rail,
                cx=0.0,
                cy=sign * (w / 2 - rail_mm / 2),
                note="x along the base length; the rail width is (W - the 7 in gap) / 2 as on the layout end view",
            )
        )
    # PartSpec for rails: Rect2D(w=length along x, h=rail width along y)
    ej_w = _mm(base.rail_gap_in - 2 * base.ejector_clearance_in)
    ej_len = ln - _mm(0.5)
    parts.append(
        _plate(
            "ejector_retainer_plate",
            code,
            f"ejector retainer plate, {_num(ej_w / IN)} x {_num(ej_len / IN)} x {_inch_text(base.er_in)}",
            place,
            ej_len,
            ej_w,
            z_er0,
            er,
            pockets=(
                _holes(z_er0 + er, 1.0, ret_head, k_ret, "ReturnPinHeadSeats"),
                _holes(z_er0 + er, 1.0, ret_circles, er, "ReturnPinBores"),
            ),
            cut_volume=_area(ret_head) * k_ret + _area(ret_circles) * (er - k_ret),
            note=(
                "ASSUMED: length = L - 1/2 in (the layout page gives the 6.875 in width only); home position against the bottom clamp plate"
            ),
        )
    )
    parts.append(
        _plate(
            "ejector_plate",
            code,
            f"ejector plate, {_num(ej_w / IN)} x {_num(ej_len / IN)} x {_inch_text(base.eb_in)}",
            place,
            ej_len,
            ej_w,
            z_eb0,
            eb,
            note="ASSUMED: length = L - 1/2 in; home position against the bottom clamp plate",
        )
    )
    parts.append(
        _plate(
            "bottom_clamp_plate",
            code,
            f"bottom clamp plate, {size_text} x {_inch_text(base.bcp_in)}",
            place,
            ln,
            w,
            z_bcp0,
            bcp,
            note="the opening for the press ejector and the clamp slots are not modelled",
        )
    )
    # the standard parts that go in it
    for i, (x, y) in enumerate(pins, start=1):
        parts.append(
            guide_pin(
                pin_row.item(pin_len_in),
                Placement(
                    origin=_to_world(place, PartFrame(origin=(x, y, b - k_pin), z=(0, 0, -1))).origin,
                    z=_to_world(place, PartFrame(z=(0, 0, -1))).z,
                    x=basis(place)[0],
                ),
                role=f"guide_pin_{i}",
            )
        )
    for i, (x, y) in enumerate(pins, start=1):
        k_in = bush_row.head_k_in
        parts.append(
            guide_bush(
                bush_row.item(bush_len_in),
                Placement(
                    origin=_to_world(place, PartFrame(origin=(x, y, -_mm(k_in)), z=(0, 0, -1))).origin,
                    z=_to_world(place, PartFrame(z=(0, 0, -1))).z,
                    x=basis(place)[0],
                ),
                role=f"guide_bush_{i}",
            )
        )
    for i, (x, y) in enumerate(rets, start=1):
        parts.append(
            return_pin(
                rp_item,
                Placement(
                    origin=_to_world(place, PartFrame(origin=(x, y, z_er0 + er - k_ret), z=(0, 0, -1))).origin,
                    z=_to_world(place, PartFrame(z=(0, 0, -1))).z,
                    x=basis(place)[0],
                ),
                role=f"return_pin_{i}",
                trim_length_in=rp_need_in,
            )
        )
    zdir = _to_world(place, PartFrame(z=(0, 0, -1))).z
    parts.append(
        locating_ring(
            base.locating_ring,
            Placement(
                origin=_to_world(place, PartFrame(origin=(0, 0, z_tcp0 + cbore_depth), z=(0, 0, -1))).origin,
                z=zdir,
                x=basis(place)[0],
            ),
        )
    )
    parts.append(
        sprue_bush(
            sprue_ref,
            Placement(origin=place.origin, z=zdir, x=basis(place)[0]),
            trim_stem_in=a_in,
        )
    )
    return tuple(parts)


def expand(kind: str, ref: str | None, size: str | None, at: Sequence[Placement]) -> tuple[PartSpec, ...]:
    """The parts of one ``mould_component`` step."""
    if kind not in KINDS:
        msg = f"unknown mould component kind {kind!r}; kinds: {', '.join(KINDS)}"
        raise ComponentError(msg)
    places = tuple(at) or (Placement(),)
    if kind == "mould_base":
        return mould_base(ref, size, places[0])
    makers = {
        "guide_pin": lambda p, r: guide_pin(_need(ref, kind), p, r),
        "guide_bush": lambda p, r: guide_bush(_need(ref, kind), p, r),
        "locating_ring": lambda p, r: locating_ring(_need(ref, kind), p, r),
        "sprue_bush": lambda p, r: sprue_bush(_need(ref, kind), p, r),
        "ejector_pin": lambda p, r: ejector_pin(ref, size, p, r),
        "return_pin": lambda p, r: return_pin(_need(ref, kind), p, r),
        "angle_pin": lambda p, r: angle_pin(_need(ref, kind), p, r),
        "wear_plate": lambda p, r: wear_plate(_need(ref, kind), p, r),
    }
    make = makers[kind]
    if len(places) == 1:
        return (make(places[0], kind),)
    return tuple(make(p, f"{kind}_{i}") for i, p in enumerate(places, start=1))


def _need(ref: str | None, kind: str) -> str:
    if not ref:
        msg = f"{kind} needs ref, the catalogue item number (see the guide for the tables)"
        raise ComponentError(msg)
    return ref
