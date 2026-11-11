"""Steel sheet gauges (calibre).

RESEARCH (read 2026-10-24)
* A gauge number is NOT a thickness by itself: it names a table. Colombian suppliers of cold-rolled and galvanized
  sheet mostly quote the thickness in mm; when they say "calibre" the usual references are the Manufacturers'
  Standard Gauge (MSG, for uncoated carbon steel sheet) and the galvanized-steel gauge. No Colombian supplier or
  NTC gauge table could be retrieved (Ternium Colombia's published catalogs give mm only for flat steel). So: ASK THE
  SUPPLIER FOR THE THICKNESS IN MM and treat the gauge as an alias. BWG (tube/wire) and US Standard Gauge are other,
  different tables and are not shipped.
* Source of both tables: the "Standard sheet metal gauges" table of https://en.wikipedia.org/wiki/Sheet_metal , which
  cites Oberg et al., Machinery's Handbook (Manufacturers' Standard Gauge for sheet steel) and 15 U.S.C. section 206.
  Thickness in inches as published (4 decimals); mm = inches x 25.4. This is a secondary source (an encyclopedia
  reproduction): the values agree with a second source read (a Spanish-language steel catalog listing gauge 16 =
  0.0598 in = 1.52 mm and gauge 18 = 0.0478 in = 1.21 mm, Scribd https://es.scribd.com/doc/314616691/Catalogo-Acero,
  origin unknown) but the handbook itself was not read.
* Mass per square metre is thickness x 7850 kg/m3. The gauge tables are defined on 41.82 lb/ft2 per inch of
  thickness (about 8039 kg/m3), so a published gauge weight is about 2.4 % higher than the figure shipped here.

Row format: ``(gauge, thickness_in)``.
"""

from cotaforge_mcp.parts import geometry
from cotaforge_mcp.parts.model import Part, Source

MSG = Source(
    key="msg",
    label="catálogo (calibre Manufacturers' Standard Gauge, Machinery's Handbook vía Wikipedia, 2026-10-24)",
    citation=(
        "Manufacturers' Standard Gauge for sheet steel (Oberg et al., Machinery's Handbook), tabla "
        "'Standard sheet metal gauges': https://en.wikipedia.org/wiki/Sheet_metal"
    ),
)
GALVANIZED = Source(
    key="galv-gauge",
    label="catálogo (calibre de acero galvanizado, tabla 'Standard sheet metal gauges' de Wikipedia, 2026-10-24)",
    citation="Galvanized steel gauge column of https://en.wikipedia.org/wiki/Sheet_metal",
)

MSG_ROWS: tuple[tuple[int, float], ...] = (
    (7, 0.1793),
    (8, 0.1644),
    (9, 0.1495),
    (10, 0.1345),
    (11, 0.1196),
    (12, 0.1046),
    (13, 0.0897),
    (14, 0.0747),
    (15, 0.0673),
    (16, 0.0598),
    (17, 0.0538),
    (18, 0.0478),
    (19, 0.0418),
    (20, 0.0359),
    (21, 0.0329),
    (22, 0.0299),
    (23, 0.0269),
    (24, 0.0239),
    (25, 0.0209),
    (26, 0.0179),
    (27, 0.0164),
    (28, 0.0149),
)

GALVANIZED_ROWS: tuple[tuple[int, float], ...] = (
    (8, 0.1681),
    (9, 0.1532),
    (10, 0.1382),
    (11, 0.1233),
    (12, 0.1084),
    (13, 0.0934),
    (14, 0.0785),
    (15, 0.0710),
    (16, 0.0635),
    (17, 0.0575),
    (18, 0.0516),
    (19, 0.0456),
    (20, 0.0396),
    (21, 0.0366),
    (22, 0.0336),
    (23, 0.0306),
    (24, 0.0276),
    (25, 0.0247),
    (26, 0.0217),
    (27, 0.0202),
    (28, 0.0187),
)


def _gauge(prefix: str, name: str, source: Source, gauge: int, inches: float) -> Part:
    mm = round(inches * 25.4, 3)
    return Part(
        id=f"gauge-{prefix}-{gauge}",
        kind="sheet_gauge",
        designation=f"Lámina de acero calibre {gauge} ({name}): {mm:g} mm",
        dims={"gauge": gauge, "t_in": inches, "t_mm": mm},
        source=source,
        mass=(round(geometry.mass_per_metre_kg(inches * 25.4 * 1000.0), 3), "kg/m2"),
        mass_basis="calculado",
        props_note="masa = espesor x 7850 kg/m3; las tablas de calibre usan ~8039 kg/m3 (publican ~2.4 % más)",
        note="el calibre nombra una tabla: confirmar el espesor en mm con el proveedor",
        keywords=f"calibre gauge lamina lamina sheet plancha chapa cold rolled laminada frio caliente {name} {prefix}",
    )


def parts() -> list[Part]:
    msg = [_gauge("msg", "MSG, acero sin recubrir", MSG, g, t) for g, t in MSG_ROWS]
    galvanized = [_gauge("galv", "acero galvanizado", GALVANIZED, g, t) for g, t in GALVANIZED_ROWS]
    return [*msg, *galvanized]
