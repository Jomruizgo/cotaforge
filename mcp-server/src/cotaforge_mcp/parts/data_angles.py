"""Hot-rolled equal-leg angles (ASTM A36 steel, ASTM A6/A6M dimensional tolerances).

RESEARCH (read 2026-10-24)
* Structural angles in Colombia are hot-rolled equal-leg angles of ASTM A36 steel in inch sizes (ASTM A6) and in mm
  sizes. A Colombian mill datasheet with the mass table could not be retrieved (searches for Diaco / Ferrasa
  datasheets returned nothing readable), so the only mass table read is the datasheet of Aceros Arequipa (Peru),
  "Ángulos estructurales, calidad ASTM A36", ASTM A36/A36M mechanical properties and ASTM A6/A6M tolerances, nominal
  mass in kg/m, which states "El peso métrico no deberá variar más de +3.0%/-2.5% del peso nominal":
  https://acerosarequipa.com/sites/default/files/fichas/2020-09/hoja-tecnica-angulos-estructurales.pdf
  Rows from that datasheet are VERIFIED (mass published). The A36 / A6 sizes are the same product family sold in
  Colombia, but that is a general statement: a Colombian supplier may stock other sizes or other masses.
* Rows marked ``verified=False`` are common commercial sizes that were NOT found in any table read: their dimensions
  are given as usually listed and their mass is computed (sharp corners, 7850 kg/m3), so the real mass is up to about
  1 to 3 % higher. Treat them as to-be-confirmed with the supplier.
* The datasheet row 5 x 5 x 3/8 in lists 18.90 kg/m; geometry gives 18.28 kg/m for that section and the same
  datasheet's other rows agree with geometry within 1 %, so the 18.90 is taken as a likely misprint and the row is
  NOT shipped as verified: it carries the calculated mass and ``verified=False``.
* Section properties are computed with sharp corners (geometry.py), labelled "calculado".

Row format: ``(label, leg_mm, t_mm, published_mass_kg_m or None, verified)``.
"""

from cotaforge_mcp.parts import geometry
from cotaforge_mcp.parts.data_tubes import num
from cotaforge_mcp.parts.model import Part, Source

A36_AREQUIPA = Source(
    key="a36-arequipa",
    label="catálogo (ASTM A36, tolerancias ASTM A6/A6M; hoja técnica Aceros Arequipa)",
    citation=(
        "Aceros Arequipa, hoja técnica Ángulos estructurales ASTM A36 (QCQA01-F103/02/ABR 20), masa nominal en kg/m: "
        "https://acerosarequipa.com/sites/default/files/fichas/2020-09/hoja-tecnica-angulos-estructurales.pdf"
    ),
)
A36_COMMON = Source(
    key="a36-common",
    label="catálogo (ASTM A36 / A6M, tamaño comercial usual; masa calculada)",
    citation="Tamaño comercial usual, no encontrado en ninguna tabla leída; masa calculada de la geometría nominal.",
)

IN = 25.4

# (label, leg_mm, t_mm, published mass, verified)
ANGLES: tuple[tuple[str, float, float, float | None, bool], ...] = (
    # Aceros Arequipa, sistema inglés
    ('2x2x5/16"', 2 * IN, 5 / 16 * IN, 5.83, True),
    ('2-1/2x2-1/2x5/16"', 2.5 * IN, 5 / 16 * IN, 7.44, True),
    ('3x3x3/16"', 3 * IN, 3 / 16 * IN, 5.52, True),
    ('5x5x3/8"', 5 * IN, 3 / 8 * IN, None, False),
    ('5x5x1/2"', 5 * IN, 1 / 2 * IN, 24.15, True),
    ('6x6x3/8"', 6 * IN, 3 / 8 * IN, 22.22, True),
    ('6x6x1/2"', 6 * IN, 1 / 2 * IN, 29.34, True),
    # Aceros Arequipa, sistema métrico
    ("20x20x2", 20.0, 2.0, 0.60, True),
    ("20x20x2.5", 20.0, 2.5, 0.74, True),
    ("20x20x3", 20.0, 3.0, 0.87, True),
    ("25x25x2", 25.0, 2.0, 0.75, True),
    ("25x25x2.5", 25.0, 2.5, 0.93, True),
    ("25x25x3", 25.0, 3.0, 1.11, True),
    ("25x25x4.5", 25.0, 4.5, 1.61, True),
    ("30x30x2", 30.0, 2.0, 0.91, True),
    ("30x30x2.5", 30.0, 2.5, 1.13, True),
    ("30x30x3", 30.0, 3.0, 1.34, True),
    ("30x30x4.5", 30.0, 4.5, 1.96, True),
    ("38x38x2", 38.0, 2.0, 1.16, True),
    # Common sizes not found in a table that was read (mass calculated)
    ('1x1x1/8"', 1 * IN, 1 / 8 * IN, None, False),
    ('1-1/2x1-1/2x1/8"', 1.5 * IN, 1 / 8 * IN, None, False),
    ('1-1/2x1-1/2x3/16"', 1.5 * IN, 3 / 16 * IN, None, False),
    ('2x2x1/8"', 2 * IN, 1 / 8 * IN, None, False),
    ('2x2x3/16"', 2 * IN, 3 / 16 * IN, None, False),
    ('2x2x1/4"', 2 * IN, 1 / 4 * IN, None, False),
    ('3x3x1/4"', 3 * IN, 1 / 4 * IN, None, False),
    ('3x3x3/8"', 3 * IN, 3 / 8 * IN, None, False),
    ('4x4x1/4"', 4 * IN, 1 / 4 * IN, None, False),
    ('4x4x3/8"', 4 * IN, 3 / 8 * IN, None, False),
    ('4x4x1/2"', 4 * IN, 1 / 2 * IN, None, False),
    ("40x40x3", 40.0, 3.0, None, False),
    ("40x40x4", 40.0, 4.0, None, False),
    ("40x40x5", 40.0, 5.0, None, False),
    ("50x50x3", 50.0, 3.0, None, False),
    ("50x50x4", 50.0, 4.0, None, False),
    ("50x50x5", 50.0, 5.0, None, False),
    ("50x50x6", 50.0, 6.0, None, False),
    ("75x75x6", 75.0, 6.0, None, False),
    ("75x75x8", 75.0, 8.0, None, False),
    ("100x100x8", 100.0, 8.0, None, False),
    ("100x100x10", 100.0, 10.0, None, False),
)


def _slug(label: str) -> str:
    return label.replace('"', "in").replace("/", "-")


def _angle(label: str, leg: float, t: float, published: float | None, verified: bool) -> Part:
    raw = geometry.equal_angle(leg, t)
    calculated = geometry.mass_per_metre_kg(raw["area_mm2"])
    mass, basis = (published, "publicado") if published is not None else (calculated, "calculado")
    inch = label.endswith('"')
    note = "" if verified else "tamaño no confirmado en una tabla leída: pedir confirmación al proveedor"
    if label == '5x5x3/8"':
        note = "la hoja técnica de Aceros Arequipa da 18.90 kg/m, incoherente con la geometría (18.28): no se usa"
    return Part(
        id=f"angle-{_slug(label)}",
        kind="angle",
        designation=f"Ángulo de lados iguales L {label.replace('x', ' x ')}{'' if inch else ' mm'}",
        dims={"leg_mm": round(leg, 2), "t_mm": round(t, 3)},
        source=A36_AREQUIPA if published is not None else A36_COMMON,
        mass=(round(mass, 3) if basis == "calculado" else mass, "kg/m"),
        mass_basis=basis,
        props=geometry.in_cm(raw),
        props_note="calculado de las dimensiones nominales (esquinas vivas, 7850 kg/m3); la masa real es 1 a 3 % mayor",
        verified=verified,
        note=note,
        keywords=(
            "angulo angular perfil l lados iguales equal angle astm a36 a6 "
            f"{num(round(leg, 2))}x{num(round(leg, 2))}x{num(round(t, 3))}"
        ),
    )


def parts() -> list[Part]:
    return [_angle(*row) for row in ANGLES]
