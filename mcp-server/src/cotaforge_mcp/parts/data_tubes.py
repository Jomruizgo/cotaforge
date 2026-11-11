"""Structural tubes: square, rectangular and round (cold-formed, welded, carbon steel).

RESEARCH (read 2026-10-24)
* The Colombian standard for these tubes is NTC 4526 (ICONTEC), "tubos de acero al carbono estructurales formados en
  frío, soldados y sin costura"; ASTM A500/A500M is the US standard of the same product family (not identical: the
  two documents were NOT compared, the equivalence is a general statement, not verified here). Colombian mills and
  distributors quote NTC 4526 (Ternium Colombia: NTC 4526:2017 in its 2021/2022 catalogs, NTC 4526:2021 in the
  October 2023 one). ASTM A500 is what a supplier of imported tube quotes.
* THE SOURCE OF EVERY ROW BELOW is the Ternium Colombia catalog "Tubería y perfiles", October 2023, whose notes say the
  theoretical linear mass (kg/m) "cumple con el peso definido en la norma NTC4526:2021" (the earlier 2022 catalog
  declares +/-10 % on the NTC 4526:2017 tables):
  https://co.ternium.com/media/52qja44b/ternium_tuberi-a-y-perfiles_octubre-2023.pdf
  Earlier edition, consulted to cross-check:
  https://co.ternium.com/media/vzflt2ae/catalogo-tuberi-a-y-perfiles-ternium-2022.pdf
  The NTC 4526 document itself is paywalled and was NOT read: the masses are the mill's published figures. Rows
  flagged ``to_order`` are "producción bajo pedido" (minimum volumes), so not stock items.
* The masses of the 2022 and 2023 catalogs differ by a few percent for the same size (40x40x2: 2.34 vs 2.45 kg/m):
  the 2023 figures are the ones shipped. They are 1 to 13 % ABOVE the mass of the nominal geometry (the thin, small
  sizes the most: 26x26x3 is +13 %; even a sharp-cornered section is lighter than the table), so the tool prints both
  numbers: buy and weigh with the published one, take A, I and W from the geometry, and expect a real tube to be a
  few percent off either.
* SECTION PROPERTIES (A, I, W, r) are NOT published by that catalog. They are computed from the nominal dimensions
  with the convention of geometry.py (Ro = 2 t, Ri = t, nominal t) and labelled "calculado". Convention reference:
  Steel Tube Institute FAQ on HSS corner radii, https://steeltubeinstitute.org/faqs/topic/hollow-structural-sections/
* Not included: ornamental tube (the mill says it is not for structural use), sizes the catalog does not list.

Row format: square ``(side, t, mass, to_order)``, rectangular ``(b_minor, h_major, t, mass, to_order)``, round
``(nps_label, d, t, mass, to_order)``; dimensions in mm, mass in kg/m. Rows were extracted by script from the
catalog text (43 square, 44 rectangular, 48 round) and counted against it.
"""

from cotaforge_mcp.parts import geometry
from cotaforge_mcp.parts.model import Part, Source

NTC_4526 = Source(
    key="ntc4526",
    label="catálogo (NTC 4526:2021, Ternium Colombia oct-2023)",
    citation=(
        "Masa teórica lineal según NTC 4526:2021, catálogo Tubería y perfiles de Ternium Colombia, octubre 2023: "
        "https://co.ternium.com/media/52qja44b/ternium_tuberi-a-y-perfiles_octubre-2023.pdf"
    ),
)
CALC_NOTE = "calculado de las dimensiones nominales (esquina exterior 2t, interior t, acero 7850 kg/m3)"
ORDER_NOTE = "producción bajo pedido en el fabricante (volumen mínimo): no es de stock"

SQUARE: tuple[tuple[float, float, float, bool], ...] = (
    (26.0, 1.5, 1.21, True),
    (26.0, 2.0, 1.57, False),
    (26.0, 2.5, 1.92, True),
    (26.0, 3.0, 2.25, True),
    (30.0, 2.0, 1.83, False),
    (30.0, 2.5, 2.23, True),
    (30.0, 3.0, 2.62, True),
    (38.1, 2.0, 2.33, True),
    (38.1, 2.5, 2.87, True),
    (38.1, 3.0, 3.39, True),
    (38.1, 3.5, 3.88, True),
    (38.1, 4.0, 4.36, True),
    (40.0, 1.5, 1.87, False),
    (40.0, 2.0, 2.45, False),
    (40.0, 2.5, 3.02, False),
    (40.0, 3.0, 3.57, True),
    (40.0, 3.5, 4.09, True),
    (40.0, 4.0, 4.6, True),
    (50.0, 1.5, 2.34, False),
    (50.0, 2.0, 3.08, False),
    (50.0, 2.5, 3.8, False),
    (50.0, 3.0, 4.51, False),
    (50.0, 3.5, 5.19, True),
    (60.0, 2.0, 3.71, False),
    (60.0, 2.5, 4.59, False),
    (60.0, 3.0, 5.45, True),
    (60.0, 3.5, 6.29, True),
    (60.0, 4.0, 7.11, True),
    (70.0, 2.0, 4.34, False),
    (70.0, 2.5, 5.37, False),
    (70.0, 3.0, 6.39, True),
    (70.0, 3.5, 7.39, True),
    (70.0, 4.0, 8.37, True),
    (90.0, 2.0, 5.59, False),
    (90.0, 2.5, 6.94, False),
    (90.0, 3.0, 8.28, True),
    (90.0, 3.5, 9.59, True),
    (90.0, 4.0, 10.88, True),
    (100.0, 2.0, 6.22, False),
    (100.0, 2.5, 7.73, False),
    (100.0, 3.0, 9.22, False),
    (100.0, 3.5, 10.69, True),
    (100.0, 4.0, 12.14, False),
)

RECTANGULAR: tuple[tuple[float, float, float, float, bool], ...] = (
    (25.4, 50.8, 1.5, 1.78, False),
    (25.4, 50.8, 2.0, 2.33, False),
    (25.4, 50.8, 2.5, 2.87, True),
    (25.4, 50.8, 3.0, 3.39, True),
    (25.4, 50.8, 3.5, 3.88, True),
    (30.0, 50.0, 1.5, 1.87, False),
    (30.0, 50.0, 2.0, 2.45, False),
    (30.0, 50.0, 2.5, 3.02, False),
    (30.0, 50.0, 3.0, 3.57, True),
    (30.0, 50.0, 3.5, 4.09, True),
    (40.0, 60.0, 1.5, 2.34, False),
    (40.0, 60.0, 2.0, 3.08, False),
    (40.0, 80.0, 1.5, 2.81, False),
    (40.0, 80.0, 2.0, 3.71, False),
    (40.0, 80.0, 2.5, 4.59, False),
    (40.0, 80.0, 3.0, 5.45, False),
    (40.0, 80.0, 3.5, 6.29, True),
    (40.0, 80.0, 4.0, 7.11, False),
    (50.0, 90.0, 2.0, 4.34, False),
    (50.0, 90.0, 2.5, 5.37, False),
    (50.0, 90.0, 3.0, 6.39, False),
    (50.0, 90.0, 3.5, 7.39, True),
    (50.0, 100.0, 1.5, 3.52, False),
    (50.0, 100.0, 2.0, 4.65, False),
    (50.0, 100.0, 2.5, 5.77, False),
    (50.0, 100.0, 3.0, 6.86, False),
    (50.0, 100.0, 3.5, 7.94, True),
    (50.0, 100.0, 4.0, 9.0, True),
    (70.0, 110.0, 2.5, 6.94, False),
    (70.0, 110.0, 3.0, 8.28, False),
    (70.0, 110.0, 3.5, 9.59, True),
    (60.0, 120.0, 2.0, 5.59, False),
    (60.0, 120.0, 2.5, 6.94, False),
    (60.0, 120.0, 3.0, 8.28, False),
    (60.0, 120.0, 3.5, 9.59, True),
    (50.0, 130.0, 2.0, 5.59, False),
    (50.0, 130.0, 2.5, 6.94, False),
    (50.0, 130.0, 3.0, 8.28, False),
    (50.0, 130.0, 3.5, 9.59, True),
    (50.0, 150.0, 2.0, 6.22, False),
    (50.0, 150.0, 2.5, 7.73, False),
    (50.0, 150.0, 3.0, 9.22, False),
    (50.0, 150.0, 3.5, 10.69, True),
    (50.0, 150.0, 4.0, 12.14, True),
)

ROUND: tuple[tuple[str, float, float, float, bool], ...] = (
    ('1"', 25.4, 2.0, 1.23, True),
    ('1"', 25.4, 2.5, 1.51, True),
    ('1-1/4"', 31.8, 2.0, 1.55, True),
    ('1-1/4"', 31.8, 2.5, 1.9, True),
    ('1.315"', 33.4, 2.0, 1.63, True),
    ('1.315"', 33.4, 2.5, 2.0, True),
    ('1.315"', 33.4, 3.0, 2.37, True),
    ('1.315"', 33.4, 3.5, 2.72, True),
    ('1-1/2"', 38.1, 2.0, 1.86, True),
    ('1-1/2"', 38.1, 2.5, 2.29, True),
    ('1-1/2"', 38.1, 3.0, 2.71, True),
    ('1-1/2"', 38.1, 3.5, 3.12, True),
    ('1-1/2"', 38.1, 4.0, 3.52, True),
    ('1.663"', 42.2, 2.0, 2.06, True),
    ('1.663"', 42.2, 2.5, 2.55, True),
    ('1.663"', 42.2, 3.0, 3.02, True),
    ('1.663"', 42.2, 3.5, 3.48, True),
    ('1.663"', 42.2, 4.0, 3.93, True),
    ('1.9"', 48.3, 2.0, 2.36, True),
    ('1.9"', 48.3, 2.5, 2.92, True),
    ('1.9"', 48.3, 3.0, 3.47, True),
    ('1.9"', 48.3, 3.5, 4.0, True),
    ('1.9"', 48.3, 4.0, 4.53, True),
    ('2"', 50.8, 2.0, 2.49, True),
    ('2"', 50.8, 2.5, 3.08, True),
    ('2"', 50.8, 3.0, 3.65, True),
    ('2"', 50.8, 3.5, 4.22, True),
    ('2"', 50.8, 4.0, 4.77, True),
    ('2.36"', 59.9, 2.0, 2.93, True),
    ('2.36"', 59.9, 2.5, 3.64, True),
    ('2.36"', 59.9, 3.0, 4.33, True),
    ('2.36"', 59.9, 3.5, 5.01, True),
    ('2.36"', 59.9, 4.0, 5.67, True),
    ('3"', 76.2, 2.0, 3.74, True),
    ('3"', 76.2, 2.5, 4.64, True),
    ('3"', 76.2, 3.0, 5.53, True),
    ('3"', 76.2, 3.5, 6.41, True),
    ('3"', 76.2, 4.0, 7.28, True),
    ('3-1/2"', 88.9, 2.0, 4.36, True),
    ('3-1/2"', 88.9, 2.5, 5.43, True),
    ('3-1/2"', 88.9, 3.0, 6.47, True),
    ('3-1/2"', 88.9, 3.5, 7.51, True),
    ('3-1/2"', 88.9, 4.0, 8.53, True),
    ('4-1/2"', 114.3, 2.0, 5.62, True),
    ('4-1/2"', 114.3, 2.5, 6.99, True),
    ('4-1/2"', 114.3, 3.0, 8.35, True),
    ('4-1/2"', 114.3, 3.5, 9.7, True),
    ('4-1/2"', 114.3, 4.0, 11.04, True),
)


def num(value: float) -> str:
    """``40.0`` -> ``40``, ``38.1`` -> ``38.1``: how a designation writes a size."""
    return f"{value:g}"


def _square(side: float, t: float, mass: float, to_order: bool) -> Part:
    props = geometry.in_cm(geometry.rect_tube(side, side, t))
    return Part(
        id=f"tube-sq-{num(side)}x{num(side)}x{num(t)}",
        kind="tube_square",
        designation=f"Tubo estructural cuadrado {num(side)}x{num(side)}x{num(t)} mm",
        dims={"side_mm": side, "t_mm": t},
        source=NTC_4526,
        mass=(mass, "kg/m"),
        mass_basis="publicado",
        props=props,
        props_note=CALC_NOTE,
        note=ORDER_NOTE if to_order else "",
        keywords="tubo tuberia cuadrado cuadrada estructural square tube hss ptr pte ntc 4526 a500",
    )


def _rectangular(b: float, h: float, t: float, mass: float, to_order: bool) -> Part:
    props = geometry.in_cm(geometry.rect_tube(b, h, t))
    return Part(
        id=f"tube-rect-{num(h)}x{num(b)}x{num(t)}",
        kind="tube_rect",
        designation=f"Tubo estructural rectangular {num(h)}x{num(b)}x{num(t)} mm (H x B x e)",
        dims={"h_mm": h, "b_mm": b, "t_mm": t},
        source=NTC_4526,
        mass=(mass, "kg/m"),
        mass_basis="publicado",
        props=props,
        props_note=CALC_NOTE + "; x = eje paralelo a B (eje fuerte)",
        note=ORDER_NOTE if to_order else "",
        keywords=(
            f"tubo tuberia rectangular estructural rectangle rectangular tube hss ptr pte rec ntc 4526 a500 "
            f"{num(b)}x{num(h)}x{num(t)}"
        ),
    )


def _round(label: str, d: float, t: float, mass: float, to_order: bool) -> Part:
    props = geometry.in_cm(geometry.round_tube(d, t))
    return Part(
        id=f"tube-rnd-{num(d)}x{num(t)}",
        kind="tube_round",
        designation=f"Tubo estructural redondo {num(d)}x{num(t)} mm (OD {label})",
        dims={"d_mm": d, "t_mm": t},
        source=NTC_4526,
        mass=(mass, "kg/m"),
        mass_basis="publicado",
        props=props,
        props_note="calculado de las dimensiones nominales (acero 7850 kg/m3)",
        note=ORDER_NOTE if to_order else "",
        keywords="tubo tuberia redondo redonda estructural round tube pipe circular ntc 4526 a500",
    )


def parts() -> list[Part]:
    return [
        *(_square(*row) for row in SQUARE),
        *(_rectangular(*row) for row in RECTANGULAR),
        *(_round(*row) for row in ROUND),
    ]
