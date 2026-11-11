"""The shipped standards tables: spot values read from the cited sources, and self-consistency of every row.

A wrong number in a catalog destroys trust, so each table is held two ways:

(a) SPOT VALUES: rows hard-coded here as they were read from the source named in the comment (not copied from the data
    module), compared with what the catalog returns.
(b) SELF-CONSISTENCY: published masses against the mass of the nominal geometry at 7850 kg/m3, and areas / inertias
    recomputed from the dimensions by an INDEPENDENT method (polygon integration with Green's theorem, not the closed
    forms the product uses) within a stated tolerance.
"""

import math

import pytest

from cotaforge_mcp.parts import all_parts, family_counts, get_part, search_parts
from cotaforge_mcp.parts.geometry import DENSITY_KG_M3
from cotaforge_mcp.parts.model import Part

DENSITY_G_MM3 = DENSITY_KG_M3 * 1e-6  # g per mm3 (7.85e-3)


def part(part_id: str) -> Part:
    found = get_part(part_id)
    assert found is not None, part_id
    return found


def of_kind(*kinds: str) -> list[Part]:
    return [p for p in all_parts() if p.kind in kinds]


# --- (a) spot values read from the sources -----------------------------------------------------------------------

# Ternium Colombia, Catalogo Tuberia y perfiles, octubre 2023, theoretical mass per NTC 4526:2021 (kg/m):
# https://co.ternium.com/media/52qja44b/ternium_tuberi-a-y-perfiles_octubre-2023.pdf
TERNIUM_2023 = [
    ("tube-sq-26x26x1.5", 1.21, True),  # square: "PTE CUA. 26 x 26 mm *" 1,5 mm -> 1,21 (made to order)
    ("tube-sq-40x40x2", 2.45, False),
    ("tube-sq-50x50x1.5", 2.34, False),
    ("tube-sq-60x60x3", 5.45, True),
    ("tube-sq-100x100x3", 9.22, False),
    ("tube-sq-100x100x4", 12.14, False),
    ("tube-rect-100x50x3", 6.86, False),  # "PTE REC. 100 x 50mm" 3,0 mm
    ("tube-rect-80x40x2", 3.71, False),
    ("tube-rect-50.8x25.4x1.5", 1.78, False),
    ("tube-rect-150x50x4", 12.14, True),
    ("tube-rnd-48.3x3", 3.47, True),  # "PTE RED. 1.9" x 3 mm"
    ("tube-rnd-114.3x4", 11.04, True),
    ("tube-rnd-76.2x2.5", 4.64, True),
]

# Aceros Arequipa, hoja tecnica Angulos estructurales ASTM A36, nominal mass kg/m:
# https://acerosarequipa.com/sites/default/files/fichas/2020-09/hoja-tecnica-angulos-estructurales.pdf
AREQUIPA = [
    ("angle-2x2x5-16in", 5.83),
    ("angle-3x3x3-16in", 5.52),
    ("angle-6x6x1-2in", 29.34),
    ("angle-6x6x3-8in", 22.22),
    ("angle-20x20x2", 0.60),
    ("angle-25x25x4.5", 1.61),
    ("angle-38x38x2", 1.16),
]

# Sheet gauges: https://en.wikipedia.org/wiki/Sheet_metal "Standard sheet metal gauges" (Machinery's Handbook, MSG;
# galvanized column): (id, thickness in inches as printed, mm as printed)
GAUGES = [
    ("gauge-msg-10", 0.1345, 3.42),
    ("gauge-msg-14", 0.0747, 1.90),
    ("gauge-msg-16", 0.0598, 1.52),
    ("gauge-msg-18", 0.0478, 1.21),
    ("gauge-msg-20", 0.0359, 0.91),
    ("gauge-msg-22", 0.0299, 0.76),
    ("gauge-msg-28", 0.0149, 0.38),
    ("gauge-galv-14", 0.0785, 1.99),
    ("gauge-galv-16", 0.0635, 1.61),
    ("gauge-galv-20", 0.0396, 1.01),
]


@pytest.mark.parametrize(("part_id", "mass", "to_order"), TERNIUM_2023)
def test_tube_masses_match_the_ternium_ntc_4526_catalog(part_id: str, mass: float, to_order: bool) -> None:
    row = part(part_id)
    assert row.mass == (mass, "kg/m")
    assert row.verified and "NTC 4526:2021" in row.provenance
    assert bool(row.note) is to_order  # "producción bajo pedido" rows say so


@pytest.mark.parametrize(("part_id", "mass"), AREQUIPA)
def test_angle_masses_match_the_arequipa_a36_datasheet(part_id: str, mass: float) -> None:
    row = part(part_id)
    assert row.mass == (mass, "kg/m") and row.mass_basis == "publicado" and row.verified
    assert "ASTM A36" in row.provenance


@pytest.mark.parametrize(("part_id", "inches", "mm"), GAUGES)
def test_gauge_thicknesses_match_the_published_table(part_id: str, inches: float, mm: float) -> None:
    row = part(part_id)
    assert row.dims["t_in"] == inches
    assert row.dims["t_mm"] == pytest.approx(mm, abs=0.006)  # the table prints 2 decimals of mm
    assert row.mass is not None and row.mass[0] == pytest.approx(inches * 25.4 * DENSITY_KG_M3 / 1000, rel=1e-3)


# ISO 4017 / ISO 4014 (DIN EN ISO tables at https://fasten.it/norms/norm/din_en_iso_4017 and .../din_en_iso_4014),
# ISO 4032 (https://www.globalfastener.com/standards/detail_385.html), ISO 7089 (https://fasten.it/Norms/Norm/DIN_EN_ISO_7089).
def test_fastener_spot_values_match_the_iso_tables() -> None:
    bolt = part("bolt-iso4017-m10").dims
    assert (bolt["s_mm"], bolt["k_mm"], bolt["e_min_mm"], bolt["pitch_mm"]) == (16.0, 6.4, 17.77, 1.5)
    b12 = part("bolt-iso4014-m12").dims
    assert (b12["s_mm"], b12["k_mm"], b12["b_l<=125_mm"], b12["b_125<l<=200_mm"], b12["b_l>200_mm"]) == (
        18.0,
        7.5,
        30,
        36,
        49,
    )
    nut = part("nut-iso4032-m8")
    assert (nut.dims["s_mm"], nut.dims["m_max_mm"], nut.dims["e_min_mm"], nut.dims["dw_min_mm"]) == (
        13.0,
        6.8,
        14.38,
        11.6,
    )
    assert part("nut-iso4032-m24").dims["m_max_mm"] == 21.5 and part("nut-iso4032-m16").dims["s_mm"] == 24.0
    for size, d1, d2, h in (("m8", 8.4, 16.0, 1.6), ("m12", 13.0, 24.0, 2.5), ("m24", 25.0, 44.0, 4.0)):
        washer = part(f"washer-iso7089-{size}").dims
        assert (washer["d1_mm"], washer["d2_mm"], washer["h_mm"]) == (d1, d2, h)
    assert part("washer-iso7089-m8").mass == (1.83, "g/pieza")  # 1.83 kg per 1000


def test_non_preferred_sizes_m14_m18_m22_are_in_every_fastener_family() -> None:
    """M14 / M18 / M22: rows read from fasten.it, globalfastener.com and torqbolt (see data_fasteners)."""
    for size, bolt, nut, washer in (
        ("m14", (21.0, 8.8, 23.36, 2.0), (21.0, 12.8, 23.36, 19.6), (15.0, 28.0, 2.5)),
        ("m18", (27.0, 11.5, 30.14, 2.5), (27.0, 15.8, 29.56, 24.9), (19.0, 34.0, 3.0)),
        ("m22", (34.0, 14.0, 37.72, 2.5), (34.0, 19.4, 37.29, 31.4), (23.0, 39.0, 3.0)),
    ):
        for iso in (4017, 4014):
            d = part(f"bolt-iso{iso}-{size}").dims
            assert (d["s_mm"], d["k_mm"], d["e_min_mm"], d["pitch_mm"]) == bolt
        n = part(f"nut-iso4032-{size}").dims
        assert (n["s_mm"], n["m_max_mm"], n["e_min_mm"], n["dw_min_mm"]) == nut
        w = part(f"washer-iso7089-{size}").dims
        assert (w["d1_mm"], w["d2_mm"], w["h_mm"]) == washer
    assert part("bolt-iso4014-m14").dims["b_l<=125_mm"] == 34 and part("bolt-iso4014-m22").dims["b_l>200_mm"] == 69
    assert part("nut-iso4032-m18").mass == (43.5, "g/pieza") and part("washer-iso7089-m22").mass == (18.3, "g/pieza")


# --- (b) self-consistency ---------------------------------------------------------------------------------------


def polygon_properties(points: list[tuple[float, float]]) -> tuple[float, float, float, float, float, float]:
    """Area, centroid, and the second moments about the CENTROIDAL axes (Ix, Iy, Ixy) of a CCW polygon (Green)."""
    area = cx = cy = ix = iy = ixy = 0.0
    for (x0, y0), (x1, y1) in zip(points, [*points[1:], points[0]], strict=True):
        cross = x0 * y1 - x1 * y0
        area += cross / 2
        cx += (x0 + x1) * cross
        cy += (y0 + y1) * cross
        ix += (y0 * y0 + y0 * y1 + y1 * y1) * cross
        iy += (x0 * x0 + x0 * x1 + x1 * x1) * cross
        ixy += (x0 * y1 + 2 * x0 * y0 + 2 * x1 * y1 + x1 * y0) * cross
    cx, cy = cx / (6 * area), cy / (6 * area)
    ix, iy, ixy = ix / 12 - area * cy * cy, iy / 12 - area * cx * cx, ixy / 24 - area * cx * cy
    return area, cx, cy, ix, iy, ixy


def rounded_rectangle(b: float, h: float, r: float, per_corner: int = 3000) -> list[tuple[float, float]]:
    points: list[tuple[float, float]] = []
    for (sx, sy), start in (((1, 1), 0.0), ((-1, 1), 90.0), ((-1, -1), 180.0), ((1, -1), 270.0)):
        for i in range(per_corner + 1):
            angle = math.radians(start + 90.0 * i / per_corner)
            points.append((sx * (b / 2 - r) + r * math.cos(angle), sy * (h / 2 - r) + r * math.sin(angle)))
    return points


def tube_section_by_polygons(b: float, h: float, t: float) -> tuple[float, float, float]:
    outer = polygon_properties(rounded_rectangle(b, h, 2 * t))
    inner = polygon_properties(rounded_rectangle(b - 2 * t, h - 2 * t, t))
    return outer[0] - inner[0], outer[3] - inner[3], outer[4] - inner[4]


@pytest.mark.parametrize("row", of_kind("tube_square", "tube_rect"), ids=lambda p: p.id)
def test_rectangular_tube_properties_match_an_independent_integration(row: Part) -> None:
    b = row.dims.get("b_mm", row.dims.get("side_mm"))
    h = row.dims.get("h_mm", row.dims.get("side_mm"))
    area, ix, iy = tube_section_by_polygons(float(b), float(h), float(row.dims["t_mm"]))
    assert row.props["area_cm2"] == pytest.approx(area / 100, rel=1e-4)
    assert row.props["ix_cm4"] == pytest.approx(ix / 1e4, rel=1e-4)
    assert row.props["iy_cm4"] == pytest.approx(iy / 1e4, rel=1e-4)
    assert row.props["wx_cm3"] == pytest.approx(ix / (float(h) / 2) / 1e3, rel=1e-4)
    assert row.props["rx_cm"] == pytest.approx(math.sqrt(ix / area) / 10, rel=1e-4)


@pytest.mark.parametrize("row", of_kind("tube_round"), ids=lambda p: p.id)
def test_round_tube_properties_match_an_independent_integration(row: Part) -> None:
    d, t = float(row.dims["d_mm"]), float(row.dims["t_mm"])
    n = 20000
    outer = [(d / 2 * math.cos(2 * math.pi * i / n), d / 2 * math.sin(2 * math.pi * i / n)) for i in range(n)]
    inner = [
        ((d / 2 - t) * math.cos(2 * math.pi * i / n), (d / 2 - t) * math.sin(2 * math.pi * i / n)) for i in range(n)
    ]
    ring_area = polygon_properties(outer)[0] - polygon_properties(inner)[0]
    ring_i = polygon_properties(outer)[3] - polygon_properties(inner)[3]
    assert row.props["area_cm2"] == pytest.approx(ring_area / 100, rel=1e-4)
    assert row.props["i_cm4"] == pytest.approx(ring_i / 1e4, rel=1e-4)
    assert row.props["w_cm3"] == pytest.approx(ring_i / (d / 2) / 1e3, rel=1e-4)


@pytest.mark.parametrize("row", of_kind("angle"), ids=lambda p: p.id)
def test_angle_properties_match_an_independent_integration(row: Part) -> None:
    leg, t = float(row.dims["leg_mm"]), float(row.dims["t_mm"])
    outline = [(0.0, 0.0), (leg, 0.0), (leg, t), (t, t), (t, leg), (0.0, leg)]
    area, cx, _, ix, iy, ixy = polygon_properties(outline)
    principal_min = (ix + iy) / 2 - math.sqrt(((ix - iy) / 2) ** 2 + ixy**2)
    # the shown dimensions are rounded (76.2 x 4.762 for 3 x 3 x 3/16 in) while the properties use the exact ones
    assert row.props["area_cm2"] == pytest.approx(area / 100, rel=5e-4)
    assert row.props["c_cm"] == pytest.approx(cx / 10, rel=5e-4)
    assert row.props["ix_cm4"] == pytest.approx(ix / 1e4, rel=1e-3)
    assert row.props["imin_cm4"] == pytest.approx(principal_min / 1e4, rel=1e-3)
    assert row.props["wx_cm3"] == pytest.approx(ix / (leg - cx) / 1e3, rel=1e-3)


def geometric_mass(row: Part) -> float:
    return row.props["area_cm2"] * 100 * DENSITY_KG_M3 * 1e-6


@pytest.mark.parametrize("row", of_kind("tube_square", "tube_rect", "tube_round"), ids=lambda p: p.id)
def test_published_tube_mass_is_within_the_stated_tolerance_of_the_geometry(row: Part) -> None:
    """The mill's NTC 4526 mass sits above the mass of the nominal section (observed +1.4 % to +13.4 %): it must stay
    between the geometry and +15 %; a transcription slip (a digit, a shifted row) breaks it."""
    assert row.mass is not None
    ratio = row.mass[0] / geometric_mass(row)
    assert 1.0 <= ratio <= 1.15, (row.id, round(ratio, 3))


@pytest.mark.parametrize("row", of_kind("angle"), ids=lambda p: p.id)
def test_angle_mass_is_area_times_density(row: Part) -> None:
    assert row.mass is not None
    if row.mass_basis == "calculado":
        assert row.mass[0] == pytest.approx(geometric_mass(row), rel=1e-3)
    else:  # published nominal mass: the fillets add up to ~1 %, the datasheet rounds to 0.01 kg/m
        assert row.mass[0] == pytest.approx(geometric_mass(row), rel=0.02), row.id


def test_sheet_gauge_tables_are_ordered_and_consistent() -> None:
    for prefix in ("msg", "galv"):
        rows = sorted(
            (p for p in of_kind("sheet_gauge") if p.id.startswith(f"gauge-{prefix}-")), key=lambda p: p.dims["gauge"]
        )
        assert len(rows) >= 20
        thickness = [float(p.dims["t_in"]) for p in rows]
        assert thickness == sorted(thickness, reverse=True)  # a higher gauge number is a thinner sheet
        for p in rows:
            assert p.dims["t_mm"] == pytest.approx(float(p.dims["t_in"]) * 25.4, abs=0.0006)
            assert p.mass is not None and p.mass[1] == "kg/m2"
            assert p.mass[0] == pytest.approx(float(p.dims["t_in"]) * 25.4 * DENSITY_KG_M3 / 1000, rel=2e-4)


def test_washer_mass_is_the_volume_of_the_ring_at_7850() -> None:
    for row in of_kind("washer"):
        d1, d2, h = (float(row.dims[k]) for k in ("d1_mm", "d2_mm", "h_mm"))
        grams = math.pi / 4 * (d2 * d2 - d1 * d1) * h * DENSITY_G_MM3
        assert row.mass is not None and row.mass[0] == pytest.approx(grams, rel=0.015), row.id


def test_nut_mass_is_below_the_prism_of_its_flats_and_above_a_chamfered_floor() -> None:
    """Sanity only: the hexagonal prism minus the minor-diameter hole overestimates (the chamfers remove 8 to 25 %)."""
    for row in of_kind("nut"):
        if row.mass is None:
            continue
        s, m, pitch = float(row.dims["s_mm"]), float(row.dims["m_max_mm"]), float(row.dims["pitch_mm"])
        d = float(str(row.dims["d"])[1:])
        prism = (math.sqrt(3) / 2 * s * s - math.pi / 4 * (d - pitch) ** 2) * m * DENSITY_G_MM3
        assert 0.65 * prism <= row.mass[0] <= prism, (row.id, round(row.mass[0] / prism, 3))


def test_fastener_dimensions_grow_with_the_size_and_the_hexagon_is_consistent() -> None:
    for family in ("bolt-iso4017", "nut-iso4032", "washer-iso7089"):
        rows = [p for p in all_parts() if p.id.startswith(family)]
        sizes = [float(str(p.dims["d"])[1:]) for p in rows]
        assert sizes == sorted(sizes) and len(rows) == 13
        for key in rows[0].dims:
            if key.endswith("_mm") and not key.startswith("dw"):
                values = [float(p.dims[key]) for p in rows]
                assert values == sorted(values), (family, key)
    for row in [*of_kind("bolt"), *of_kind("nut")]:
        s, e = float(row.dims["s_mm"]), float(row.dims["e_min_mm"])
        assert 1.09 * s <= e <= s / math.cos(math.radians(30)) + 1e-9, row.id  # corners of a hexagon of flats s
    for row in of_kind("bolt"):
        if "b_l<=125_mm" in row.dims:
            d = float(str(row.dims["d"])[1:])  # ISO 4014: b = 2 d + 6, + 12, + 25
            assert (row.dims["b_l<=125_mm"], row.dims["b_125<l<=200_mm"], row.dims["b_l>200_mm"]) == (
                2 * d + 6,
                2 * d + 12,
                2 * d + 25,
            )


def test_tube_mass_grows_with_the_wall_and_the_size() -> None:
    squares = sorted(of_kind("tube_square"), key=lambda p: (p.dims["side_mm"], p.dims["t_mm"]))
    for a, b in zip(squares, squares[1:], strict=False):
        if a.dims["side_mm"] == b.dims["side_mm"]:
            assert a.mass[0] < b.mass[0], (a.id, b.id)  # type: ignore[index]
    by_size: dict[tuple[float, float, float], float] = {}
    for p in of_kind("tube_rect"):
        by_size[(p.dims["h_mm"], p.dims["b_mm"], p.dims["t_mm"])] = p.mass[0]  # type: ignore[index]
    for (h, b, t), mass in by_size.items():
        thicker = [m for (h2, b2, t2), m in by_size.items() if (h2, b2) == (h, b) and t2 > t]
        assert all(m > mass for m in thicker)


# --- the table as a whole -------------------------------------------------------------------------------------------


def test_row_counts_are_the_ones_the_sources_have() -> None:
    """43 square, 44 rectangular, 48 round rows in the Ternium catalog; the others are this catalog's own choice."""
    counts = family_counts()
    assert (counts["tube_square"], counts["tube_rect"], counts["tube_round"]) == (43, 44, 48)
    assert len(of_kind("sheet_gauge")) == 22 + 21 and len(all_parts()) == 257 + 12  # + M14 M18 M22 x 4 families


def test_ids_are_unique_and_every_table_cites_its_standard_and_source() -> None:
    ids = [p.id for p in all_parts()]
    assert len(set(ids)) == len(ids)
    for p in all_parts():
        assert p.source.label.startswith("catálogo (") and p.source.citation
        assert any(word in p.source.label for word in ("NTC", "ASTM", "ISO", "Gauge", "calibre"))
        if p.verified:
            assert "http" in p.source.citation, p.id


def test_unverified_rows_are_flagged_everywhere_they_show() -> None:
    flagged = [p for p in all_parts() if not p.verified]
    assert {p.id for p in flagged} >= {"angle-5x5x3-8in", "angle-50x50x5", "angle-3x3x1-4in"}
    assert all(p.provenance.startswith("SIN VERIFICAR") for p in flagged)
    assert all(p.verified for p in all_parts() if p.kind in ("tube_square", "tube_rect", "tube_round"))
    assert all(p.mass_basis == "calculado" for p in flagged)  # nothing unverified carries a "published" figure


def test_search_finds_rows_by_size_in_either_order_and_by_spanish_or_english_words() -> None:
    assert part("tube-sq-40x40x2") in search_parts("tubo cuadrado 40x40x2")
    assert part("tube-sq-40x40x2") in search_parts("square tube 40x40x2.0")
    assert [p.id for p in search_parts("tubo 50x100x3")] == ["tube-rect-100x50x3"]
    assert [p.id for p in search_parts("100x50x3")] == ["tube-rect-100x50x3"]
    assert part("nut-iso4032-m8") in search_parts("tuerca M8")
    assert part("washer-iso7089-m12") in search_parts("arandela m12")
    assert part("gauge-msg-16") in search_parts("calibre 16")
    assert search_parts("") == [] and search_parts("zzz") == []
