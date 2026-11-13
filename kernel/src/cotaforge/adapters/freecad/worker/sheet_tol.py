"""Tolerances on a dimension as sheet text (no FreeCAD import): the ``fit`` / ``tol`` / ``show`` fields that
``hole_dim`` and ``dim`` share, what they print (``Ø8 H7 (+0,015/0)``, ``20 +0,1/-0,05``, ``Ø8,015 / 8``), the ISO
2768 general tolerance that applies to a dimension with none of its own.

A tolerance is a property of a dimension, not a drawing element of its own, so it is a field of the dimension that
shows the value (the number always comes from the model; the tolerance is the engineer's requirement). Deviations are
millimetres with the sheet's decimal sign; the fit's come out of ``iso286``.
"""

from dataclasses import dataclass
from typing import Any

import iso286
import iso2768
from sheet_context import Check, Context, ItemError, v_enum
from sheet_model import DIMENSIONS, Text, text_width

SHOWS = ("symbol", "deviations", "both", "limits")
DEV_DECIMALS = 4


def v_fit(value: Any) -> str:
    if not isinstance(value, str):
        msg = 'must be a tolerance class like "H7", "g6" or a fit like "H7/g6"'
        raise ItemError(msg)
    try:
        return "/".join(iso286.split_fit(value))
    except iso286.FitError as exc:
        raise ItemError(str(exc)) from exc


def v_tol(value: Any) -> tuple[float, float]:
    """``0.1`` -> (+0.1, -0.1); ``[0.1, -0.05]`` -> (+0.1, -0.05): the upper and lower deviation in mm."""
    numbers = value if isinstance(value, list | tuple) else None
    if numbers is None:
        pair = [value, value]
        symmetric = True
    else:
        pair = list(numbers)
        symmetric = False
    if len(pair) != 2 or any(isinstance(n, bool) or not isinstance(n, int | float) for n in pair):
        msg = "must be a number (symmetric, +-) or [upper, lower] in mm, like 0.1 or [0.1, -0.05]"
        raise ItemError(msg)
    if symmetric:
        if pair[0] <= 0:
            msg = "a symmetric tolerance must be above 0"
            raise ItemError(msg)
        return float(pair[0]), -float(pair[0])
    upper, lower = float(pair[0]), float(pair[1])
    if upper < lower:
        msg = "the upper deviation must not be below the lower one: [upper, lower]"
        raise ItemError(msg)
    if abs(upper) > 50 or abs(lower) > 50:
        msg = "deviations are in mm and below 50"
        raise ItemError(msg)
    return upper, lower


CHECKS: dict[str, Check] = {"fit": v_fit, "tol": v_tol, "show": v_enum(*SHOWS)}


def check_combination(out: dict[str, Any]) -> None:
    """The rules between the three fields (they apply to a spec the field checks have passed)."""
    if "fit" in out and "tol" in out:
        msg = "fit and tol are two ways to tolerate one dimension: give one"
        raise ItemError(msg)
    show = out.get("show")
    if show is not None and "fit" not in out and "tol" not in out:
        msg = "show needs a fit or a tol to show"
        raise ItemError(msg)
    if show in ("symbol", "both") and "fit" not in out:
        msg = f"show={show} prints the fit symbol: give fit, or use show=deviations|limits with tol"
        raise ItemError(msg)
    if "fit" in out and "/" in out["fit"] and show not in (None, "symbol"):
        msg = "a fit of two classes (H7/g6) is shown as its symbol only: the two classes have different deviations"
        raise ItemError(msg)


@dataclass(frozen=True)
class Resolved:
    symbol: str | None
    upper: float | None  # mm
    lower: float | None
    show: str
    iso: bool  # the deviations come from the ISO 286 tables


def resolve(spec: dict[str, Any], nominal: float) -> Resolved | None:
    """The tolerance a dimension spec asks for at the measured ``nominal`` size (mm), or ``None`` for none."""
    if "fit" in spec:
        parts = spec["fit"].split("/")
        if len(parts) == 2:
            return Resolved(spec["fit"], None, None, "symbol", True)
        try:
            upper, lower = iso286.deviations(parts[0], round(nominal, 3))
        except (iso286.FitError, ValueError) as exc:
            raise ItemError(str(exc)) from exc
        return Resolved(parts[0], round(upper / 1000, 6), round(lower / 1000, 6), spec.get("show", "both"), True)
    if "tol" in spec:
        upper, lower = spec["tol"]
        return Resolved(None, upper, lower, spec.get("show", "deviations"), False)
    return None


# --- numbers ----------------------------------------------------------------------------------------------------------


def _decimal(text: str, comma: bool) -> str:
    return text.replace(".", ",") if comma else text


def deviation(value: float, comma: bool, unit: str = "", signed: bool = True) -> str:
    """A deviation as drawings write it: ``+0,015``, ``-0,005``, ``0`` (a zero has no sign), at most four decimals."""
    rounded = round(value, DEV_DECIMALS)
    if rounded == 0:
        return "0"
    text = f"{abs(rounded):.{DEV_DECIMALS}f}".rstrip("0").rstrip(".")
    sign = ("+" if rounded > 0 else "-") if signed else ""
    return f"{sign}{_decimal(text, comma)}{unit}"


def _decimals(value: float) -> int:
    text = f"{round(value, DEV_DECIMALS):.{DEV_DECIMALS}f}".rstrip("0")
    return len(text.split(".")[1]) if "." in text else 0


def pair_text(res: Resolved, comma: bool, unit: str = "") -> str:
    """The deviations on one line: ``+0,015/0`` or, when symmetric, ``±0,1``."""
    assert res.upper is not None
    assert res.lower is not None
    if abs(res.upper + res.lower) < 1e-9 and res.upper > 0:
        return "±" + deviation(res.upper, comma, unit, signed=False)
    return f"{deviation(res.upper, comma, unit)}/{deviation(res.lower, comma, unit)}"


@dataclass(frozen=True)
class Rendered:
    text: str  # the dimension text as it reads, with the tolerance inline unless ``stack`` carries it
    stack: tuple[str, str] | None = None  # upper and lower deviation to draw one over the other after ``text``


def render(
    ctx: Context, nominal: float, res: Resolved, prefix: str = "", unit: str = "", stackable: bool = False
) -> Rendered:
    """The text of a tolerated dimension. ``prefix`` is ``Ø`` or ``R``; with ``stackable`` the deviations of a linear
    dimension are returned apart, to be set one over the other as ISO 129-1 draws them."""
    base = f"{prefix}{ctx.num(nominal)}{unit}"
    if res.show == "limits":
        assert res.upper is not None
        assert res.lower is not None
        high, low = nominal + res.upper, nominal + res.lower
        places = max(_decimals(high), _decimals(low))
        top = _decimal(f"{high:.{places}f}", ctx.comma)
        bottom = _decimal(f"{low:.{places}f}", ctx.comma)
        return Rendered(f"{prefix}{top}{unit} / {bottom}{unit}")
    if res.show == "symbol":
        return Rendered(f"{base} {res.symbol}")
    with_symbol = bool(res.symbol) and res.show == "both"
    head = f"{base} {res.symbol}" if with_symbol else base
    symmetric = res.upper is not None and res.lower is not None and abs(res.upper + res.lower) < 1e-9 and res.upper > 0
    if stackable and not symmetric:
        assert res.upper is not None
        assert res.lower is not None
        return Rendered(head, (deviation(res.upper, ctx.comma, unit), deviation(res.lower, ctx.comma, unit)))
    inline = pair_text(res, ctx.comma, unit)
    return Rendered(f"{head} ({inline})" if with_symbol else f"{head} {inline}")


def stack_entities(
    entities: list[Any], stack: tuple[str, str], horizontal: bool, height: float = 3.5, layer: str = DIMENSIONS
) -> None:
    """Set the deviations after the dimension text, one over the other in smaller type. ``entities`` are the ones
    ``linear_dimension`` returned: the last ``Text`` is the value, which moves back by half the width of the stack so
    the group stays centred (or stays put when it is left-anchored)."""
    index = max(i for i, e in enumerate(entities) if isinstance(e, Text))
    value: Text = entities[index]
    small = round(height * 0.7, 2)
    width = max(text_width(s, small) for s in stack)
    main = text_width(value.text, value.height)
    ux, uy = (1.0, 0.0) if horizontal else (0.0, 1.0)  # reading direction on the page
    nx, ny = (0.0, 1.0) if horizontal else (-1.0, 0.0)  # "up" for the reader
    shift = 0.0
    if value.anchor == "center":
        shift = -(width + 0.8) / 2
        start = main / 2 + 0.8 + shift
    elif value.anchor == "right":
        shift = -(width + 0.8)
        start = 0.8 + shift
    else:
        start = main + 0.8
    entities[index] = Text(
        value.x + ux * shift, value.y + uy * shift, value.text, value.height, value.layer, value.anchor, value.angle
    )
    for text, rise in ((stack[0], height * 0.55), (stack[1], -height * 0.2)):
        x = value.x + ux * start + nx * rise
        y = value.y + uy * start + ny * rise
        entities.append(Text(x, y, text, small, layer, "left", value.angle))


# --- the general tolerance a dimension without its own falls under ---------------------------------------------------


def general_note(ctx: Context, kind: str, value: float) -> str:
    """The general tolerance of one untoleranced size (see ``general_notes``)."""
    return general_notes(ctx, kind, {"": value})


def general_notes(ctx: Context, kind: str, sizes: dict[str, float]) -> str:
    """What ISO 2768 says about untoleranced dimensions of ``kind`` (``linear``, ``radius`` -- also chamfer heights --
    or angle): sizes maps a label (width, Ø12, or empty) to the size. Returns a phrase to append
    to the item's result; empty when the sheet's general tolerance field does not name ISO 2768. The first phrase of a
    sheet also says the table is transcribed."""
    parsed = iso2768.parse(str(ctx.extras.get("general_tolerances", "")))
    if parsed is None:
        return ""
    cls = parsed[0]
    label = f"ISO 2768-{cls}"
    if kind == "angle":
        low, high = (
            iso2768.angle_text(iso2768.angle_minutes(cls, 1.0)),
            iso2768.angle_text(iso2768.angle_minutes(cls, 1e9)),
        )
        phrase = f"general tolerance ({label}) depends on the shorter leg: {low} up to 10 mm, {high} over 400 mm"
    else:
        parts = []
        for name, value in sizes.items():
            dev = iso2768.radius(cls, value) if kind == "radius" else iso2768.linear(cls, value)
            shown = f"±{ctx.num(dev)}" if dev is not None else f"none for {ctx.num(value)} mm (tolerate it)"
            parts.append(f"{name} {shown}".strip())
        phrase = f"general tolerance ({label}): " + ", ".join(parts)
    if not ctx.extras.get("iso2768_said"):
        ctx.extras["iso2768_said"] = True
        phrase += f" [{iso2768.TRANSCRIPTION_NOTE['es' if ctx.comma else 'en']}]"
    return f"; {phrase}"
