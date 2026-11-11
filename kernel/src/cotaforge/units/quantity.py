"""Quantities with units (units/quantity.py).

In-house ``Quantity`` (decision 3.1: no ``pint``, no numpy): a frozen pydantic model ``{value, unit}``
over a *closed* unit table. An unknown unit is an error, never a guess. Arithmetic checks dimensions
at runtime; the result of a product or quotient is expressed in the canonical SI unit of the new
dimension (renderers, not models, convert to display units). Values are never rounded here.
"""

import math
import re
from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator, model_validator

from cotaforge.units.errors import DimensionMismatchError, UnitsError, UnknownUnitError

__all__ = [
    "AREA",
    "CANONICAL_UNIT",
    "DENSITY",
    "DIMENSIONLESS",
    "FORCE",
    "LENGTH",
    "MASS",
    "MASS_INERTIA",
    "MONEY",
    "MONEY_PER_MASS",
    "Q",
    "SECOND_MOMENT",
    "STRESS",
    "TIME",
    "TORQUE",
    "UNIT_TABLE",
    "VOLUME",
    "VOLUME_INERTIA",
    "Dimension",
    "Quantity",
    "canonical_unit",
]


class Dimension(BaseModel):
    """Exponents of the base dimensions. Angle is dimensionless (SI); temperature is not modelled in v1."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    length: int = 0
    mass: int = 0
    time: int = 0
    currency: int = 0

    def __mul__(self, other: "Dimension") -> "Dimension":
        return Dimension(
            length=self.length + other.length,
            mass=self.mass + other.mass,
            time=self.time + other.time,
            currency=self.currency + other.currency,
        )

    def __truediv__(self, other: "Dimension") -> "Dimension":
        return Dimension(
            length=self.length - other.length,
            mass=self.mass - other.mass,
            time=self.time - other.time,
            currency=self.currency - other.currency,
        )

    def __pow__(self, n: int) -> "Dimension":
        return Dimension(length=self.length * n, mass=self.mass * n, time=self.time * n, currency=self.currency * n)

    def is_dimensionless(self) -> bool:
        return self == DIMENSIONLESS

    def __str__(self) -> str:
        parts = [
            f"{symbol}{exponent}"
            for symbol, exponent in (("L", self.length), ("M", self.mass), ("T", self.time), ("C", self.currency))
            if exponent != 0
        ]
        return " ".join(parts) if parts else "dimensionless"


DIMENSIONLESS = Dimension()
LENGTH = Dimension(length=1)
AREA = Dimension(length=2)
VOLUME = Dimension(length=3)
SECOND_MOMENT = Dimension(length=4)
VOLUME_INERTIA = Dimension(length=5)
MASS = Dimension(mass=1)
TIME = Dimension(time=1)
FORCE = Dimension(length=1, mass=1, time=-2)
STRESS = Dimension(length=-1, mass=1, time=-2)
TORQUE = Dimension(length=2, mass=1, time=-2)
DENSITY = Dimension(length=-3, mass=1)
MASS_INERTIA = Dimension(length=2, mass=1)
MONEY = Dimension(currency=1)
MONEY_PER_MASS = Dimension(currency=1, mass=-1)

# symbol -> (dimension, factor to the canonical SI unit of that dimension)
UNIT_TABLE: Mapping[str, tuple[Dimension, float]] = {
    # length
    "m": (LENGTH, 1.0),
    "mm": (LENGTH, 1e-3),
    "cm": (LENGTH, 1e-2),
    "um": (LENGTH, 1e-6),
    # area
    "m2": (AREA, 1.0),
    "mm2": (AREA, 1e-6),
    "cm2": (AREA, 1e-4),
    # volume
    "m3": (VOLUME, 1.0),
    "mm3": (VOLUME, 1e-9),
    "cm3": (VOLUME, 1e-6),
    # second moment of area (L^4)
    "m4": (SECOND_MOMENT, 1.0),
    "mm4": (SECOND_MOMENT, 1e-12),
    # volume inertia (L^5, ``MassProperties.inertia`` at density 1)
    "m5": (VOLUME_INERTIA, 1.0),
    "mm5": (VOLUME_INERTIA, 1e-15),
    # mass
    "kg": (MASS, 1.0),
    "g": (MASS, 1e-3),
    "t": (MASS, 1e3),
    # time
    "s": (TIME, 1.0),
    # force
    "N": (FORCE, 1.0),
    "kN": (FORCE, 1e3),
    # stress
    "Pa": (STRESS, 1.0),
    "kPa": (STRESS, 1e3),
    "MPa": (STRESS, 1e6),
    "GPa": (STRESS, 1e9),
    "N/mm2": (STRESS, 1e6),
    # torque
    "N*m": (TORQUE, 1.0),
    "N*mm": (TORQUE, 1e-3),
    "kN*m": (TORQUE, 1e3),
    # density
    "kg/m3": (DENSITY, 1.0),
    "g/cm3": (DENSITY, 1e3),
    # mass moment of inertia
    "kg*m2": (MASS_INERTIA, 1.0),
    # dimensionless (angle is dimensionless in SI)
    "1": (DIMENSIONLESS, 1.0),
    "%": (DIMENSIONLESS, 1e-2),
    "rad": (DIMENSIONLESS, 1.0),
    "deg": (DIMENSIONLESS, math.pi / 180.0),
    # currency
    "EUR": (MONEY, 1.0),
    "EUR/kg": (MONEY_PER_MASS, 1.0),
}

CANONICAL_UNIT: Mapping[Dimension, str] = {
    DIMENSIONLESS: "1",
    LENGTH: "m",
    AREA: "m2",
    VOLUME: "m3",
    SECOND_MOMENT: "m4",
    VOLUME_INERTIA: "m5",
    MASS: "kg",
    TIME: "s",
    FORCE: "N",
    STRESS: "Pa",
    TORQUE: "N*m",
    DENSITY: "kg/m3",
    MASS_INERTIA: "kg*m2",
    MONEY: "EUR",
    MONEY_PER_MASS: "EUR/kg",
}

_SYNTHESISED = re.compile(r"^si:L(-?\d+)M(-?\d+)T(-?\d+)C(-?\d+)$")
_QUANTITY_TEXT = re.compile(r"^\s*(\S+)\s+(\S+)\s*$")


def canonical_unit(dimension: Dimension) -> str:
    """The SI unit symbol for ``dimension``: a table hit, else a synthesised ``si:L{a}M{b}T{c}C{d}``
    symbol with factor 1 (still convertible, so a stiffness N/m or a rate 1/s never becomes an error)."""
    hit = CANONICAL_UNIT.get(dimension)
    if hit is not None:
        return hit
    return f"si:L{dimension.length}M{dimension.mass}T{dimension.time}C{dimension.currency}"


def _lookup(unit: str) -> tuple[Dimension, float]:
    """``(dimension, factor)`` of a unit symbol; ``UnknownUnitError`` for anything outside the table."""
    hit = UNIT_TABLE.get(unit)
    if hit is not None:
        return hit
    match = _SYNTHESISED.match(unit)
    if match is None:
        raise UnknownUnitError(unit)
    length, mass, time, currency = (int(group) for group in match.groups())
    return Dimension(length=length, mass=mass, time=time, currency=currency), 1.0


def _check_finite(value: float) -> float:
    if not math.isfinite(value):
        msg = f"quantity value must be finite, got {value!r}"
        raise UnitsError(msg)
    return value


def _parse_text(text: str) -> tuple[float, str]:
    match = _QUANTITY_TEXT.match(text)
    if match is None:
        msg = f"quantity text must be '<value> <unit>' (for example '20 mm'), got {text!r}"
        raise UnitsError(msg)
    raw_value, unit = match.groups()
    try:
        value = float(raw_value)
    except ValueError as exc:
        msg = f"quantity text must start with a number, got {text!r}"
        raise UnitsError(msg) from exc
    return value, unit


class Quantity(BaseModel):
    """A finite ``value`` in a ``unit`` of the closed table. JSON form ``{"value": 20.0, "unit": "mm"}``;
    the before-validator also accepts ``"20 mm"`` strings (YAML files) and ``(value, unit)``
    pairs. Bare numbers are refused: a quantity always carries its unit."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float
    unit: str

    @model_validator(mode="before")
    @classmethod
    def _coerce(cls, data: Any) -> Any:
        if isinstance(data, str):
            value, unit = _parse_text(data)
            return {"value": value, "unit": unit}
        if isinstance(data, tuple | list):
            if len(data) != 2:
                msg = f"quantity pair must be (value, unit), got {data!r}"
                raise UnitsError(msg)
            return {"value": data[0], "unit": data[1]}
        if isinstance(data, bool | int | float):
            msg = f"bare number {data!r} is not a quantity: a unit is required"
            raise UnitsError(msg)
        return data

    @field_validator("value")
    @classmethod
    def _check_value(cls, value: float) -> float:
        return _check_finite(value)

    @field_validator("unit")
    @classmethod
    def _check_unit(cls, unit: str) -> str:
        _lookup(unit)
        return unit

    # --- dimension and conversion -------------------------------------------------------------

    @property
    def dimension(self) -> Dimension:
        return _lookup(self.unit)[0]

    @property
    def _factor(self) -> float:
        return _lookup(self.unit)[1]

    def _si_value(self) -> float:
        return self.value * self._factor

    def to(self, unit: str) -> "Quantity":
        """The same quantity expressed in ``unit``; ``DimensionMismatchError`` when the dimensions differ."""
        dimension, factor = _lookup(unit)
        if dimension != self.dimension:
            raise DimensionMismatchError(self.dimension, dimension, f"to({unit!r})")
        if unit == self.unit:
            return self
        return _make(self.value * self._factor / factor, unit)

    def si(self) -> "Quantity":
        """The quantity in the canonical SI unit of its dimension."""
        return self.to(canonical_unit(self.dimension))

    # --- arithmetic -----------------------------------------------------------------------------

    def _same_dimension(self, other: object, operation: str) -> "Quantity":
        if not isinstance(other, Quantity):
            msg = f"{operation} requires a Quantity operand, got {type(other).__name__}"
            raise UnitsError(msg)
        if other.dimension != self.dimension:
            raise DimensionMismatchError(self.dimension, other.dimension, operation)
        return other

    def __add__(self, other: "Quantity") -> "Quantity":
        other = self._same_dimension(other, "addition")
        return _make(self.value + other.to(self.unit).value, self.unit)

    def __sub__(self, other: "Quantity") -> "Quantity":
        other = self._same_dimension(other, "subtraction")
        return _make(self.value - other.to(self.unit).value, self.unit)

    def __neg__(self) -> "Quantity":
        return _make(-self.value, self.unit)

    def __abs__(self) -> "Quantity":
        return _make(abs(self.value), self.unit)

    def __mul__(self, other: "Quantity | float") -> "Quantity":
        if isinstance(other, Quantity):
            dimension = self.dimension * other.dimension
            # values first, powers of ten last: keeps 200 N / 50 mm2 at exactly 4e6 Pa
            return _make(self.value * other.value * self._factor * other._factor, canonical_unit(dimension))
        if isinstance(other, bool) or not isinstance(other, int | float):
            return NotImplemented
        return _make(self.value * other, self.unit)

    def __rmul__(self, other: float) -> "Quantity":
        return self.__mul__(other)

    def __truediv__(self, other: "Quantity | float") -> "Quantity":
        if isinstance(other, Quantity):
            dimension = self.dimension / other.dimension
            return _make(self.value / other.value * self._factor / other._factor, canonical_unit(dimension))
        if isinstance(other, bool) or not isinstance(other, int | float):
            return NotImplemented
        return _make(self.value / other, self.unit)

    def __rtruediv__(self, other: float) -> "Quantity":
        if isinstance(other, bool) or not isinstance(other, int | float):
            return NotImplemented
        dimension = DIMENSIONLESS / self.dimension
        return _make(other / self._si_value(), canonical_unit(dimension))

    def __pow__(self, n: int) -> "Quantity":
        if isinstance(n, bool) or not isinstance(n, int):
            msg = f"exponent must be an integer, got {n!r}"
            raise UnitsError(msg)
        dimension = self.dimension**n
        return _make(self._si_value() ** n, canonical_unit(dimension))

    def sqrt(self) -> "Quantity":
        """Square root; every dimension exponent must be even and the value non-negative."""
        dimension = self.dimension
        if any(exponent % 2 for exponent in (dimension.length, dimension.mass, dimension.time, dimension.currency)):
            msg = f"sqrt requires even dimension exponents, got {dimension}"
            raise UnitsError(msg)
        si_value = self._si_value()
        if si_value < 0:
            msg = f"sqrt of a negative quantity: {self}"
            raise UnitsError(msg)
        half = Dimension(
            length=dimension.length // 2,
            mass=dimension.mass // 2,
            time=dimension.time // 2,
            currency=dimension.currency // 2,
        )
        return _make(math.sqrt(si_value), canonical_unit(half))

    # --- comparison (in SI) ---------------------------------------------------------------------

    def __eq__(self, other: object) -> bool:
        """``NotImplemented`` for a non-``Quantity`` operand (Python then tries the reflected operation
        and falls back to identity, so ``q == 3`` is ``False`` unless the other type knows better);
        ``False`` for a different dimension (never raises); ``True``/``False`` by SI value otherwise.
        Ordering (``__lt__`` etc.) and ``is_close`` keep raising on a dimension mismatch — ordering
        across dimensions is meaningless, but equality is simply "not equal"."""
        if not isinstance(other, Quantity):
            return NotImplemented
        if other.dimension != self.dimension:
            return False
        return self._si_value() == other._si_value()

    def __hash__(self) -> int:
        return hash((self._si_value(), self.dimension))

    def __lt__(self, other: "Quantity") -> bool:
        return self._si_value() < self._same_dimension(other, "comparison")._si_value()

    def __le__(self, other: "Quantity") -> bool:
        return self._si_value() <= self._same_dimension(other, "comparison")._si_value()

    def __gt__(self, other: "Quantity") -> bool:
        return self._si_value() > self._same_dimension(other, "comparison")._si_value()

    def __ge__(self, other: "Quantity") -> bool:
        return self._si_value() >= self._same_dimension(other, "comparison")._si_value()

    def is_close(self, other: "Quantity", *, rel: float = 1e-6, abs_si: float = 0.0) -> bool:
        """``math.isclose`` on the SI values (``abs_si`` is an absolute tolerance in the canonical unit)."""
        other = self._same_dimension(other, "is_close")
        return math.isclose(self._si_value(), other._si_value(), rel_tol=rel, abs_tol=abs_si)

    def __str__(self) -> str:
        return f"{self.value} {self.unit}"


def _make(value: float, unit: str) -> Quantity:
    """Build a ``Quantity`` from an arithmetic result, raising a bare ``UnitsError`` (not a wrapped
    ``ValidationError``) when the result overflowed to inf/nan."""
    _check_finite(value)
    return Quantity(value=value, unit=unit)


def Q(text: str | float, unit: str | None = None) -> Quantity:  # noqa: N802 - documented short constructor
    """``Q("20 mm")`` or ``Q(20, "mm")``; raises ``UnitsError`` subclasses directly (never wrapped)."""
    if unit is None:
        if not isinstance(text, str):
            msg = f"Q({text!r}) needs a unit: pass '<value> <unit>' text or (value, unit)"
            raise UnitsError(msg)
        value, unit = _parse_text(text)
    else:
        try:
            value = float(text)
        except (TypeError, ValueError) as exc:
            msg = f"quantity value must be a number, got {text!r}"
            raise UnitsError(msg) from exc
    _check_finite(value)
    _lookup(unit)
    return Quantity(value=value, unit=unit)
