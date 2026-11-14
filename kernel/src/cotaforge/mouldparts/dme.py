"""DME mould components: the dimension tables the catalogue publishes, and nothing else.

Family: DME (DME Company), the public catalogues read on 2026-11-14:

* BASES = "DME Mold Bases & Plates" catalogue, https://www.dme.net/wp-content/uploads/PDFCatalogs/MoldBases&PlatesCatalog.pdf
  (2.0 A & B-series: p. 17 leader pins, p. 20 return pins, p. 32 how to order, p. 37 layout drawing of the 1012 base);
* COMPONENTS = "Mold Components" catalogue, https://www.dme.net/wp-content/uploads/PDFCatalogs/MoldComponents.pdf
  (inch: p. 327 guide pins GL, p. 329 shoulder bushings, p. 344 sprue bushings B series, p. 348 locating rings, p. 399
  return pins; slide action: p. 25/29 angle pins, p. 35 self-lubricating wear plates);
* PINS = "DME Metric Pins" product sheet, https://dme.net/wp-content/uploads/ProductSheets/DME-Metric-Pins.pdf
  (p. 3 DIN ejector pins, nitrided, EJP-EHN).

Inch tables stay in inches (the catalogue's own numbers) and are converted once, exactly (1 in = 25.4 mm). A number
that is not in these pages is NOT here: what a part needs and the catalogue does not say is chosen by the builder
(``build.py``) and listed in the part's note as an assumption.
"""

from dataclasses import dataclass

IN = 25.4

FAMILY = "DME"
BASES = "DME Mold Bases & Plates, https://www.dme.net/wp-content/uploads/PDFCatalogs/MoldBases&PlatesCatalog.pdf"
COMPONENTS = "DME Mold Components, https://www.dme.net/wp-content/uploads/PDFCatalogs/MoldComponents.pdf"
PINS = "DME Metric Pins, https://dme.net/wp-content/uploads/ProductSheets/DME-Metric-Pins.pdf"


def src(book: str, page: str) -> str:
    return f"{book}, p. {page}"


# --- guide pins "GL" (inch), COMPONENTS p. 327 ---------------------------------------------------------------------


@dataclass(frozen=True)
class GuidePin:
    nominal_in: float
    d_in: float  # shaft diameter
    head_d_in: float
    head_k_in: float
    first_in: float  # shortest stock length; items run in steps of 1/2 in from the first
    base_item: int  # item number of the first length ("<number>GL")
    lengths_in: tuple[float, ...]  # the lengths the catalogue lists for this diameter

    def item(self, length_in: float) -> str:
        step = round((length_in - self.first_in) / 0.5)
        return f"{self.base_item + step}GL"


def _run(first: float, last: float) -> tuple[float, ...]:
    count = round((last - first) / 0.5) + 1
    return tuple(first + 0.5 * i for i in range(count))


GUIDE_PINS: tuple[GuidePin, ...] = (
    GuidePin(0.75, 0.749, 0.990, 3 / 16, 1.75, 5000, (*_run(1.75, 7.75), 9.25)),
    GuidePin(0.875, 0.874, 1.115, 0.25, 1.75, 5099, (*_run(1.75, 8.75), 10.75, 12.25)),
    GuidePin(1.0, 0.999, 1.240, 0.25, 1.75, 5198, _run(1.75, 12.25)),
    GuidePin(1.25, 1.249, 1.490, 5 / 16, 2.75, 5300, _run(2.75, 15.75)),
)
GUIDE_PIN_SOURCE = src(COMPONENTS, "327 (Guide Pins - GL)")

# --- shoulder bushings (inch), COMPONENTS p. 329 -------------------------------------------------------------------


@dataclass(frozen=True)
class ShoulderBushing:
    nominal_in: float  # inside diameter, nominal
    id_in: float
    od_in: float
    head_d_in: float
    head_k_in: float
    first_item: int
    skip_item: int | None  # the one item number the table skips (the longest bushing jumps over it)

    LENGTHS_IN = (0.875, 1.375, 1.875, 2.375, 2.875, 3.375, 3.875, 4.375, 4.875, 5.875)

    def item(self, length_in: float) -> str:
        index = self.LENGTHS_IN.index(length_in)
        number = self.first_item + index
        if self.skip_item is not None and number >= self.skip_item:
            number += 1
        return str(number)


SHOULDER_BUSHINGS: tuple[ShoulderBushing, ...] = (
    ShoulderBushing(0.75, 0.7505, 1.1255, 1.302, 3 / 16, 5700, None),
    ShoulderBushing(0.875, 0.8755, 1.2505, 1.427, 3 / 16, 5710, 5719),
    ShoulderBushing(1.0, 1.0005, 1.3755, 1.552, 3 / 16, 5730, 5739),
    ShoulderBushing(1.25, 1.2505, 1.6255, 1.802, 3 / 16, 5750, 5759),
)
BUSHING_SOURCE = src(COMPONENTS, "329 (Steel Shoulder Bushings)")

# --- sprue bushing B series (inch), COMPONENTS p. 344 --------------------------------------------------------------

SPRUE_STEM_D_IN = 1.0
SPRUE_HEAD_D_IN = 2.0
SPRUE_TAPER_PER_FOOT_IN = 0.5  # "1/2 taper per foot (included angle)": the bore grows 1/2 in of diameter per foot
SPRUE_ORIFICES_32 = (5, 7, 9, 11)  # O in 32nds of an inch: 5/32, 7/32, 9/32, 11/32
SPRUE_RADII_IN = {1: 0.5, 3: 0.75}  # the R digit of the item number: 1 = 1/2 in, 3 = 3/4 in

# prefix: (A = length of the stem under the head, in), the head is 7/8 in thick (L = A + 7/8)
SPRUE_B_SERIES: dict[str, float] = {
    "B00": 29 / 32,
    "B01": 1 + 13 / 32,
    "B02": 1 + 29 / 32,
    "B03": 2 + 13 / 32,
    "B04": 2 + 29 / 32,
    "B05": 3 + 13 / 32,
    "B06": 3 + 29 / 32,
    "B07": 4 + 13 / 32,
    "B08": 4 + 29 / 32,
}
SPRUE_B_HEAD_IN = 7 / 8
SPRUE_SOURCE = src(COMPONENTS, "344 (Sprue Bushings, B series)")

# --- locating ring 6501 (inch), COMPONENTS p. 348 ------------------------------------------------------------------

LOCATING_RINGS = {"6501": {"od_in": 3.990, "height_in": 17 / 32, "inside_in": 7 / 32, "bore_in": 2.0}}
"""Item 6501 (standard series): D = 3.990, 17/32 high of which 7/32 sits in the 4 in counterbore of the clamp plate (and
5/16 stands proud); the 2 in dimension on the drawing is its opening."""
RING_SOURCE = src(COMPONENTS, "348 (Locating Rings, item 6501)")

# --- return pins (inch), COMPONENTS p. 399 -------------------------------------------------------------------------

RETURN_PIN_HEAD_K_IN = 0.25
RETURN_PINS: dict[str, tuple[float, float, float]] = {
    # item: (shaft diameter in, head diameter in, length in)
    **{str(7410 + i): (0.5, 0.75, v) for i, v in enumerate((3.563, 4.063, 4.563, 5.063, 5.563, 6.063, 6.563))},
    **{
        str(7510 + i): (0.625, 0.875, v)
        for i, v in enumerate((4.063, 4.563, 5.063, 5.563, 6.063, 6.563, 7.063, 7.563, 8.063))
    },
    **{
        str(7610 + i): (0.75, 1.0, v)
        for i, v in enumerate((4.938, 5.438, 5.938, 6.438, 6.938, 7.438, 7.938, 8.438, 8.938, 9.438))
    },
}
RETURN_PIN_SOURCE = src(COMPONENTS, "399 (Return Pins, inch)")

# --- DIN ejector pins, nitrided EJP-EHN (mm), PINS p. 3 ------------------------------------------------------------

EJECTOR_PINS: dict[float, tuple[float, float]] = {  # D: (head diameter H, head thickness K)
    1.2: (3, 1.5), 1.5: (3, 1.5), 2.0: (4, 2), 2.2: (4, 2), 2.5: (5, 2), 2.7: (5, 2), 3.0: (6, 3), 3.2: (6, 3),
    3.5: (7, 3), 3.7: (7, 3), 4.0: (8, 3), 4.2: (8, 3), 4.5: (8, 3), 5.0: (10, 3), 5.2: (10, 3), 5.5: (10, 3),
    6.0: (12, 5), 6.2: (12, 5), 6.5: (12, 5), 7.0: (12, 5), 8.0: (14, 5), 8.2: (14, 5), 8.5: (14, 5), 9.0: (14, 5),
    10.0: (16, 5), 10.2: (16, 5), 10.5: (16, 5), 11.0: (16, 5), 12.0: (18, 7), 12.2: (18, 7), 12.5: (18, 7),
    14.0: (22, 7), 16.0: (22, 7), 18.0: (24, 7), 20.0: (26, 8), 25.0: (32, 10), 32.0: (40, 10),
}  # fmt: skip
EJECTOR_LENGTHS_MM = (100, 125, 160, 200, 250, 315, 400, 500, 630, 800, 1000)
"""The length series of the table header. Which length is in stock for which diameter is a chart on the sheet that is
not read here: a length of the series that the chart leaves out is still ordered as a special."""
EJECTOR_SOURCE = src(PINS, "3 (DIN Ejector Pins - Nitrided, EJP-EHN)")

# --- metric angle pins APD (mm), COMPONENTS p. 29 ------------------------------------------------------------------

ANGLE_PINS: dict[int, tuple[float, float, float, tuple[int, ...]]] = {
    # D: (head diameter H, head thickness K, N, stock lengths L)
    10: (12, 3, 5, (40, 60, 80, 100, 110, 120)),
    12: (16, 6, 5, (40, 60, 80, 100, 110, 120, 140, 160)),
    14: (18, 8, 6, (40, 60, 80, 100, 110, 120, 140, 160, 180, 200, 210, 220, 230)),
    16: (20, 8, 7, (40, 60, 80, 100, 110, 120, 140, 160, 180, 200, 210, 220, 230)),
    18: (22, 8, 8, (40, 60, 80, 100, 110, 120, 140, 160, 180, 200, 210, 220, 230)),
    20: (24, 8, 8, (60, 80, 100, 110, 120, 140, 160, 180, 200, 210, 220, 230)),
}
ANGLE_PIN_SOURCE = src(COMPONENTS, "29 (Metric Angle Pins)")

# --- self-lubricating wear plates SLP (inch), COMPONENTS p. 35 -----------------------------------------------------

# thickness in -> {width in: item base}; the item is "SLP" + base + length in inches (3 -> 03, 12 -> 12)
WEAR_PLATES: dict[float, dict[float, int]] = {
    0.375: {1.0: 500, 1.5: 510, 2.0: 520, 2.5: 530, 3.0: 540, 4.0: 550},
    0.5: {1.5: 600, 2.0: 610, 2.5: 620, 3.0: 630, 4.0: 650},
    0.625: {1.5: 700, 2.0: 720, 2.5: 730, 3.0: 740, 4.0: 750},
}
WEAR_LENGTHS_IN = {0.375: (4, 5, 6, 8, 10, 12), 0.5: (3, 4, 5, 6, 8, 10, 12), 0.625: (3, 4, 5, 6, 8, 10, 12)}
WEAR_4IN_LENGTHS = (4, 5, 6, 8, 10, 12)  # the 4 in wide plates start at 4 in long
WEAR_SOURCE = src(COMPONENTS, "35 (Self-Lubricating Wear Plates)")


def wear_item(thickness_in: float, width_in: float, length_in: int) -> str:
    return f"SLP{WEAR_PLATES[thickness_in][width_in] + length_in:04d}"


# --- the 1012 mould base, A-series, BASES p. 17, 20, 32, 37 --------------------------------------------------------


@dataclass(frozen=True)
class MouldBase:
    size: str
    width_in: float  # W
    length_in: float  # L
    cp_in: float  # top clamp plate
    su_in: float  # support plate
    er_in: float  # ejector retainer plate
    eb_in: float  # ejector plate
    bcp_in: float  # bottom clamp plate
    pin_d_in: float  # leader pin (nominal)
    bush_od_in: float  # shoulder bushing outside diameter
    lp_x_in: float
    lp_x_offset_in: float  # the one offset pin
    lp_y_in: float
    rp_d_in: float
    rp_y_in: float
    rp_x_in: float
    rp_x_offset_in: float
    rail_gap_in: float  # clear width between the two rails (end view of the layout drawing)
    ejector_clearance_in: float  # each side, between the rails and the ejector plates (end view)
    locating_ring: str
    sprue_stem_d_in: float
    ring_bore_d_in: float
    ring_cbore_d_in: float
    ring_cbore_depth_in: float


BASE_1012 = MouldBase(
    size="1012",
    width_in=9.875,
    length_in=11.875,
    cp_in=0.875,
    su_in=1.875,
    er_in=0.5,
    eb_in=1.0,
    bcp_in=0.875,
    pin_d_in=0.875,
    bush_od_in=1.25,
    lp_x_in=5.062,
    lp_x_offset_in=4.875,
    lp_y_in=4.0,
    rp_d_in=0.625,
    rp_y_in=2.25,
    rp_x_in=5.188,
    rp_x_offset_in=5.063,
    rail_gap_in=7.0,
    ejector_clearance_in=0.0625,
    locating_ring="6501",
    sprue_stem_d_in=1.0,
    ring_bore_d_in=2.0,
    ring_cbore_d_in=4.0,
    ring_cbore_depth_in=0.219,
)
BASES_BY_SIZE = {"1012": BASE_1012}
# the plate thickness codes of page 32 (A and B plates) and the rail heights
PLATE_CODES_IN = {17: 1.875, 23: 2.375, 27: 2.875, 33: 3.375, 37: 3.875, 47: 4.875, 57: 5.875}
RAIL_CODES_IN = {25: 2.5, 30: 3.0, 35: 3.5, 40: 4.0, 45: 4.5}
BASE_DEFAULT_CODES = (17, 17, 35)  # the configuration example of page 32: XPA1012-17-17-35
BASE_SOURCE = src(BASES, "17, 20, 32, 37")
