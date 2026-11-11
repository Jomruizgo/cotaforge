"""Metric fasteners: hexagon head bolts (ISO 4014) and screws (ISO 4017), hexagon nuts style 1 (ISO 4032) and plain
washers, normal series (ISO 7089). Coarse thread, product grade A (M3 to M24, including M14, M18 and M22).

RESEARCH (read 2026-10-24)
* The ISO documents are paywalled and were NOT read; the figures are the dimension tables that fastener reference
  sites reproduce from them, and each family was cross-checked between two reproductions where one existed.
  Current ISO editions are ISO 4014:2022, ISO 4017:2022 (these tables follow the 2011 / 2014 editions that the
  DIN EN ISO tables cite), ISO 4032 (the 1999 edition is the one tabulated) and ISO 7089:2000. Dimensions of these
  parts are stable across the editions for the sizes shipped, but the 2022 editions were not compared.
* In Colombia ISO metric fasteners are sold next to inch (UNC) ones; hardware stores and industrial suppliers list
  "tornillo / tuerca / arandela" by metric size and grade (property class 8.8 for bolts, 8 for nuts); the property
  class is NOT part of these tables (ISO 898-1 / 898-2).
* ISO 4017 (screws, thread to the head), dimensions s, e, k:
  https://fasten.it/norms/norm/din_en_iso_4017  (DIN EN ISO 4017, 10/2014)
  ISO 4014 (bolts, partial thread), dimensions s, e, k and thread length b:
  https://fasten.it/norms/norm/din_en_iso_4014  (DIN EN ISO 4014, 06/2011). That page prints e min = 8.87 for M5,
  but 8.79 in the ISO 4017 table and in the nut tables, and e min = 1.13 x s min = 8.79: 8.79 is used.
* ISO 4032 (nut): s, m, e, dw min from https://www.globalfastener.com/standards/detail_385.html (ISO 4032-1999);
  mass per piece from https://torqbolt.com/iso-4032-hexagon-nuts-dimensions-standards-specifications (no mass
  there for M3 to M6, so those parts carry none; the page prints the mass without a unit, read as kg: it gives
  10 g for an M10 nut, as expected).
* ISO 7089 (washer): d1 (inside), d2 (outside), h (thickness) from
  https://torqbolt.com/iso-7089-flat-washers-200hv-dimensions-standards-specifications and
  https://fasten.it/Norms/Norm/DIN_EN_ISO_7089 (DIN EN ISO 7089, 11/2000; mass in g per piece for steel, 7.85 kg/dm3).
* DIN 931 / 933 / 934 / 125 are the older names still used in Colombian shops. They are NOT identical to the ISO
  parts: for M10, M12, M14 and M22 the DIN wrench size is 17 / 19 / 22 / 32 mm against 16 / 18 / 21 / 34 mm in ISO.
  Search finds these parts by
  the DIN number but the note says so.
  Product grade A, hardness classes 200 HV and 300 HV. One source called the series "grade C"; both agree on the
  dimensions, the grade should be confirmed with the supplier.
* Bolt/screw mass depends on the length and is not tabulated here: compute it from the shank volume if needed.
* Sizes shipped: M3, M4, M5, M6, M8, M10, M12, M16, M20, M24 (the preferred ones) and M14, M18, M22 (the
  non-preferred ones of the same range, added 2026-11-05 from the same pages and cross-checks as above: bolt rows from
  both fasten.it pages, which agree on s, k and e; nut s, m, e from globalfastener.com and torqbolt, which agree, dw
  min from globalfastener.com, mass from torqbolt (kg x 1000 = g per piece); washer d1, d2, h from torqbolt and
  fasten.it, which agree, mass from fasten.it only). ISO 4014 starts at M5 here because its table was read from M5
  up. NOT shipped, because not read: M1.6 to M2.5 and everything above M24 (M27, M30...).

Row formats: bolt ``(size, pitch, s, k, e_min, b_l125, b_l200, b_over200)``, nut ``(size, pitch, s, m, e_min, dw_min,
mass_g)``, washer ``(size, d1, d2, h, mass_g)``; all in mm.
"""

from cotaforge_mcp.parts.model import Part, Source

ISO_4017 = Source(
    key="iso4017",
    label="catálogo (ISO 4017, tabla DIN EN ISO 4017:2014 de fasten.it)",
    citation="DIN EN ISO 4017 (10/2014) dimensions: https://fasten.it/norms/norm/din_en_iso_4017",
)
ISO_4014 = Source(
    key="iso4014",
    label="catálogo (ISO 4014, tabla DIN EN ISO 4014:2011 de fasten.it)",
    citation="DIN EN ISO 4014 (06/2011) dimensions: https://fasten.it/norms/norm/din_en_iso_4014",
)
ISO_4032 = Source(
    key="iso4032",
    label="catálogo (ISO 4032:1999, tablas de globalfastener.com y torqbolt.com)",
    citation=(
        "ISO 4032-1999 dimensions https://www.globalfastener.com/standards/detail_385.html ; mass "
        "https://torqbolt.com/iso-4032-hexagon-nuts-dimensions-standards-specifications"
    ),
)
ISO_7089 = Source(
    key="iso7089",
    label="catálogo (ISO 7089:2000, tablas de fasten.it y torqbolt.com)",
    citation=(
        "DIN EN ISO 7089 (11/2000) https://fasten.it/Norms/Norm/DIN_EN_ISO_7089 ; "
        "https://torqbolt.com/iso-7089-flat-washers-200hv-dimensions-standards-specifications"
    ),
)

DIN_NOTE = (
    "DIN 931/933/934/125 es el nombre antiguo y no es idéntico "
    "(M10/M12/M14/M22: llave 17/19/22/32 mm en DIN, 16/18/21/34 en ISO)"
)
BOLT_NOTE = (
    "masa según la longitud: calcular del volumen; clase de resistencia (8.8, 10.9...) no incluida (ISO 898-1); "
    + DIN_NOTE
)

# (size, pitch, s, k, e_min, b for l<=125, b for 125<l<=200, b for l>200)
BOLTS: tuple[tuple[str, float, float, float, float, int | None, int | None, int | None], ...] = (
    ("M3", 0.5, 5.5, 2.0, 6.01, None, None, None),
    ("M4", 0.7, 7.0, 2.8, 7.66, None, None, None),
    ("M5", 0.8, 8.0, 3.5, 8.79, 16, 22, 35),
    ("M6", 1.0, 10.0, 4.0, 11.05, 18, 24, 37),
    ("M8", 1.25, 13.0, 5.3, 14.38, 22, 28, 41),
    ("M10", 1.5, 16.0, 6.4, 17.77, 26, 32, 45),
    ("M12", 1.75, 18.0, 7.5, 20.03, 30, 36, 49),
    ("M14", 2.0, 21.0, 8.8, 23.36, 34, 40, 53),
    ("M16", 2.0, 24.0, 10.0, 26.75, 38, 44, 57),
    ("M18", 2.5, 27.0, 11.5, 30.14, 42, 48, 61),
    ("M20", 2.5, 30.0, 12.5, 33.53, 46, 52, 65),
    ("M22", 2.5, 34.0, 14.0, 37.72, 50, 56, 69),
    ("M24", 3.0, 36.0, 15.0, 39.98, 54, 60, 73),
)

# (size, pitch, s, m, e_min, dw_min, mass in g)
NUTS: tuple[tuple[str, float, float, float, float, float | None, float | None], ...] = (
    ("M3", 0.5, 5.5, 2.4, 6.01, None, None),
    ("M4", 0.7, 7.0, 3.2, 7.66, None, None),
    ("M5", 0.8, 8.0, 4.7, 8.79, 6.9, None),
    ("M6", 1.0, 10.0, 5.2, 11.05, 8.9, None),
    ("M8", 1.25, 13.0, 6.8, 14.38, 11.6, 4.6),
    ("M10", 1.5, 16.0, 8.4, 17.77, 14.6, 10.0),
    ("M12", 1.75, 18.0, 10.8, 20.03, 16.6, 15.1),
    ("M14", 2.0, 21.0, 12.8, 23.36, 19.6, 23.5),
    ("M16", 2.0, 24.0, 14.8, 26.75, 22.5, 30.4),
    ("M18", 2.5, 27.0, 15.8, 29.56, 24.9, 43.5),
    ("M20", 2.5, 30.0, 18.0, 32.95, 27.7, 58.7),
    ("M22", 2.5, 34.0, 19.4, 37.29, 31.4, 72.0),
    ("M24", 3.0, 36.0, 21.5, 39.55, 33.3, 98.3),
)

# (size, d1, d2, h, mass in g)
WASHERS: tuple[tuple[str, float, float, float, float], ...] = (
    ("M3", 3.2, 7.0, 0.5, 0.119),
    ("M4", 4.3, 9.0, 0.8, 0.308),
    ("M5", 5.3, 10.0, 1.0, 0.443),
    ("M6", 6.4, 12.0, 1.6, 1.02),
    ("M8", 8.4, 16.0, 1.6, 1.83),
    ("M10", 10.5, 20.0, 2.0, 3.57),
    ("M12", 13.0, 24.0, 2.5, 6.27),
    ("M14", 15.0, 28.0, 2.5, 8.62),
    ("M16", 17.0, 30.0, 3.0, 11.3),
    ("M18", 19.0, 34.0, 3.0, 14.7),
    ("M20", 21.0, 37.0, 3.0, 17.2),
    ("M22", 23.0, 39.0, 3.0, 18.3),
    ("M24", 25.0, 44.0, 4.0, 32.3),
)


def _bolt(row: tuple[str, float, float, float, float, int | None, int | None, int | None], iso: int) -> Part:
    size, pitch, s, k, e_min, b1, b2, b3 = row
    dims: dict[str, float | str] = {"d": size, "pitch_mm": pitch, "s_mm": s, "k_mm": k, "e_min_mm": e_min}
    if iso == 4014:
        dims.update({"b_l<=125_mm": b1 or 0, "b_125<l<=200_mm": b2 or 0, "b_l>200_mm": b3 or 0})
        name = f"Perno hexagonal ISO 4014 {size} (rosca parcial)"
        source = ISO_4014
    else:
        name = f"Tornillo hexagonal ISO 4017 {size} (rosca completa)"
        source = ISO_4017
    return Part(
        id=f"bolt-iso{iso}-{size.lower()}",
        kind="bolt",
        designation=name,
        dims=dims,
        source=source,
        note=BOLT_NOTE,
        keywords=f"tornillo perno bolt screw hexagonal hex cabeza iso {iso} din {931 if iso == 4014 else 933} {size}",
    )


def _nut(row: tuple[str, float, float, float, float, float | None, float | None]) -> Part:
    size, pitch, s, m, e_min, dw_min, mass_g = row
    dims: dict[str, float | str] = {"d": size, "pitch_mm": pitch, "s_mm": s, "m_max_mm": m, "e_min_mm": e_min}
    if dw_min is not None:
        dims["dw_min_mm"] = dw_min
    return Part(
        id=f"nut-iso4032-{size.lower()}",
        kind="nut",
        designation=f"Tuerca hexagonal ISO 4032 {size} (estilo 1)",
        dims=dims,
        source=ISO_4032,
        mass=(mass_g, "g/pieza") if mass_g is not None else None,
        mass_basis="publicado" if mass_g is not None else "",
        note=("" if mass_g is not None else "sin masa publicada en las fuentes leídas; ") + DIN_NOTE,
        keywords=f"tuerca nut hexagonal hex iso 4032 din 934 {size}",
    )


def _washer(row: tuple[str, float, float, float, float]) -> Part:
    size, d1, d2, h, mass_g = row
    return Part(
        id=f"washer-iso7089-{size.lower()}",
        kind="washer",
        designation=f"Arandela plana ISO 7089 {size} (serie normal)",
        dims={"d": size, "d1_mm": d1, "d2_mm": d2, "h_mm": h},
        source=ISO_7089,
        mass=(mass_g, "g/pieza"),
        mass_basis="publicado",
        keywords=f"arandela rondana washer plana flat iso 7089 din 125 {size}",
    )


def parts() -> list[Part]:
    bolts = [_bolt(row, 4017) for row in BOLTS]
    bolts += [_bolt(row, 4014) for row in BOLTS if row[5] is not None]
    return [*bolts, *(_nut(r) for r in NUTS), *(_washer(r) for r in WASHERS)]
