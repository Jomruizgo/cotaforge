# ruff: noqa: E501
"""What the dimensions of a sheet PRINT, read back from TechDraw's own objects (no FreeCAD import: the objects are only read).

A shop reads the numbers on the sheet and nothing else, so the review checks them as a reader would:

* ``dimension_tolerance_unit``: the deviations of a toleranced size must be written in mm (``6 -0,012 / -0,042``). Left to
  TechDraw's own unit schema 12 µm print as ``-12``, which a machinist reads as millimetres (ISO 129-1, ISO 286);
* ``dimension_decimal_sign``: a number printed with the other decimal sign than the sheet's (``4.01`` on a sheet that writes
  ``4,01``): TechDraw writes a measured value with the decimal sign of the operating system;
* ``dimension_value_noise``: the number TechDraw prints is not the model's (a reference on the wrong point measures ``4,01``
  where the model says ``4``);
* ``dimension_value_unclean``: the model's own value is not a clean number at the precision the sheet's general tolerance
  warrants (``4,01`` under ISO 2768-m, whose tolerance is a tenth): modelling noise, or a decision to confirm.

Each one names the item. The first three are defects of the sheet and the last one is answered by the engineer when the value is
meant.
"""

import math
import re
from dataclasses import dataclass
from typing import Any

import iso2768
import sheet_units as su
from sheet_context import Context

CODES = ("dimension_tolerance_unit", "dimension_decimal_sign", "dimension_value_noise", "dimension_value_unclean")
PRINTED_DECIMALS = 2  # the measured value TechDraw writes (``%.2w``) and the sheet's own numbers
NOISE_MM = 0.03  # a value this near a clean one (and not on it) reads as noise, further off as a decision
MAX_ROWS = 3  # findings of one code listed before the rest are counted

_PLAIN = re.compile(r"^Ø?\d+(?:[.,]\d+)?$")
_NUMBER = re.compile(r"[+-]?\d+(?:[.,]\d+)?")


@dataclass(frozen=True)
class Printed:
    """One native dimension as the page holds it."""

    item: str
    label: str
    kind: str
    arbitrary: bool
    text: str  # what it prints besides the tolerance: the arbitrary text, else TechDraw's own formatted value
    raw: float | None  # what TechDraw measured
    expected: float | None  # what the model says
    own_tolerance: bool  # the dimension carries deviations (a fit or a ``tol``)
    arbitrary_tolerances: bool
    over: float
    under: float
    over_text: str
    under_text: str


def read(page_units: list[su.Unit], held: dict[str, list[Any]]) -> list[Printed]:
    """The native dimensions of the units that TechDraw's own objects hold, with what each one prints."""
    rows: list[Printed] = []
    for unit in page_units:
        dims = [n for n in unit.natives if isinstance(n, su.NativeDim)]
        objects = [o for o in held.get(unit.key, []) if "Dimension" in str(o.TypeId)]
        if not dims or len(objects) != len(dims):
            continue
        for obj, dim in zip(objects, dims, strict=True):
            rows.append(_row(obj, dim))
    return rows


def _row(obj: Any, dim: su.NativeDim) -> Printed:
    try:
        raw: float | None = float(obj.getRawValue())
    except Exception:  # noqa: BLE001 - FreeCAD raises plain exceptions of every kind
        raw = None
    arbitrary = bool(obj.Arbitrary)
    try:
        text = str(obj.FormatSpec) if arbitrary else str(obj.getText())
    except Exception:  # noqa: BLE001
        text = ""
    return Printed(
        item=dim.item,
        label=str(obj.Label),
        kind=dim.kind,
        arbitrary=arbitrary,
        text=text,
        raw=raw,
        expected=dim.expected,
        own_tolerance=not bool(obj.EqualTolerance) or dim.tol is not None,
        arbitrary_tolerances=bool(obj.ArbitraryTolerances),
        over=_mm(obj.OverTolerance),
        under=_mm(obj.UnderTolerance),
        over_text=str(obj.FormatSpecOverTolerance),
        under_text=str(obj.FormatSpecUnderTolerance),
    )


def _mm(quantity: Any) -> float:
    """A tolerance property in mm (a FreeCAD ``Quantity`` keeps its value in the base unit, mm)."""
    return float(getattr(quantity, "Value", quantity))


def _value(text: str) -> float | None:
    """The number a deviation text says (``-0,012`` -> -0.012; ``0`` -> 0.0), ``None`` when it is not just a number."""
    clean = text.strip().replace("−", "-").replace("±", "")
    if not _NUMBER.fullmatch(clean):
        return None
    return float(clean.replace(",", "."))


def unit_problems(row: Printed) -> str | None:
    """Why the deviations of ``row`` do not print in mm (``None`` when they do, or it has none)."""
    if not row.own_tolerance or (row.over == 0.0 and row.under == 0.0):
        return None
    if not row.arbitrary_tolerances:
        return f"{row.over:+g} / {row.under:+g} mm are left to TechDraw's own unit (µm)"
    for text, want in ((row.over_text, row.over), (row.under_text, row.under)):
        got = _value(text)
        if got is None or not math.isclose(got, want, abs_tol=1e-6):
            return f"it prints {row.over_text} / {row.under_text} where the deviations are {row.over:+g} / {row.under:+g} mm"
    return None


def sign_problem(row: Printed, comma: bool) -> str | None:
    """The text of ``row`` that carries the other decimal sign than the sheet's, else ``None``."""
    wrong = re.compile(r"\d\.\d" if comma else r"\d,\d")
    for text in ((row.text,) if not row.arbitrary else ()) + (
        (row.over_text, row.under_text) if row.arbitrary_tolerances else ()
    ):
        if wrong.search(text):
            return text
    return None


def noise_problem(row: Printed) -> str | None:
    """How TechDraw's measure prints differently from the model's value, else ``None``. Only a measured value: an arbitrary
    text is the sheet's own number."""
    if row.arbitrary or row.raw is None or row.expected is None or row.kind == "angle":
        return None
    if round(abs(row.raw), PRINTED_DECIMALS) == round(row.expected, PRINTED_DECIMALS):
        return None
    return f"TechDraw prints {round(abs(row.raw), PRINTED_DECIMALS):g} and the model says {row.expected:.4f}"


def warranted_decimals(cls: str, size: float) -> int | None:
    """How many decimals the general tolerance of ISO 2768 class ``cls`` warrants on a size: those of its deviation (±0,1 is
    a tenth: one decimal)."""
    dev = iso2768.linear(cls, size)
    if dev is None or dev <= 0:
        return None
    return max(0, math.ceil(-math.log10(dev) - 1e-9))


def unclean_problem(row: Printed, cls: str | None) -> str | None:
    """The size when the model's value is a hair off a clean number at the precision ``cls`` warrants, else ``None``."""
    if cls is None or row.expected is None or row.kind not in ("distance", "diameter") or row.own_tolerance:
        return None
    if row.arbitrary and not _PLAIN.match(row.text.strip()):
        return None  # a callout (a fit, a thread): its numbers are the engineer's
    places = warranted_decimals(cls, row.expected)
    if places is None:
        return None
    shown = round(row.expected, PRINTED_DECIMALS)
    clean = round(shown, places)
    if math.isclose(shown, clean, abs_tol=1e-9) or abs(shown - clean) > NOISE_MM:
        return None
    return f"{shown:g} where ISO 2768-{cls} warrants {places} decimal(s) ({clean:g})"


def findings(rows: list[Printed], ctx: Context) -> list[dict[str, Any]]:
    """The review findings of ``rows``: ``{"code", "severity", "message", "items", "features", "key"}``."""
    parsed = iso2768.parse(str(ctx.extras.get("general_tolerances", "")))
    cls = parsed[0] if parsed else None
    out: list[dict[str, Any]] = []
    for row in rows:
        who = f"{row.item} ({row.label})"
        what = unit_problems(row)
        if what:
            text = ctx.say(
                f"{who}: las desviaciones no se escriben en mm: {what}. Un taller lee ese número como milímetros.",
                f"{who}: the deviations are not written in mm: {what}. A shop reads that number as millimetres.",
            )
            out.append(_finding("dimension_tolerance_unit", row, text))
        shown = sign_problem(row, ctx.comma)
        if shown:
            text = ctx.say(
                f"{who}: imprime '{shown}' con el signo decimal contrario al de la hoja.",
                f"{who}: it prints '{shown}' with the decimal sign the sheet does not use.",
            )
            out.append(_finding("dimension_decimal_sign", row, text))
        noise = noise_problem(row)
        if noise:
            text = ctx.say(
                f"{who}: la cota no imprime el valor del modelo: {noise} (referencia en un punto que no es el del modelo).",
                f"{who}: the dimension does not print the model's value: {noise} (a reference on a point that is not the model's).",
            )
            out.append(_finding("dimension_value_noise", row, text))
        unclean = unclean_problem(row, cls)
        if unclean:
            text = ctx.say(
                f"{who}: valor no redondo para la precisión de la hoja: {unclean}. Ruido de modelado o una decisión: corrige el "
                "modelo o responde que el valor es el pedido.",
                f"{who}: not a clean number at the precision of the sheet: {unclean}. Modelling noise or a decision: correct the "
                "model or answer that the value is the one asked for.",
            )
            out.append(_finding("dimension_value_unclean", row, text))
    return _capped(out)


def _finding(code: str, row: Printed, message: str) -> dict[str, Any]:
    return {
        "code": code,
        "severity": "warning",
        "message": message,
        "items": [row.item],
        "features": [],
        "key": row.item,
    }


def _capped(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """At most ``MAX_ROWS`` findings of one code (a sheet whose every dimension prints wrong is one defect, not forty)."""
    seen: dict[str, int] = {}
    kept: list[dict[str, Any]] = []
    for row in rows:
        seen[row["code"]] = seen.get(row["code"], 0) + 1
        if seen[row["code"]] <= MAX_ROWS:
            kept.append(row)
    return kept
