"""Typed ``Annotated`` aliases (units/typed.py).

A tool input model declaring ``span: Length`` rejects ``"200 N"`` at validation: pydantic wraps the
``DimensionMismatchError`` raised by ``expect_dimension`` into its ``ValidationError``. This is the
schema-level guarantee (quantities with units, never bare floats).
"""

from collections.abc import Callable
from typing import Annotated

from pydantic import AfterValidator

from cotaforge.units.errors import DimensionMismatchError
from cotaforge.units.quantity import (
    AREA,
    DENSITY,
    DIMENSIONLESS,
    FORCE,
    LENGTH,
    MASS,
    MASS_INERTIA,
    MONEY,
    MONEY_PER_MASS,
    SECOND_MOMENT,
    STRESS,
    TORQUE,
    VOLUME,
    VOLUME_INERTIA,
    Dimension,
    Quantity,
)

__all__ = [
    "Angle",
    "Area",
    "Density",
    "Dimensionless",
    "Force",
    "Length",
    "Mass",
    "MassInertia",
    "Money",
    "MoneyPerMass",
    "SecondMoment",
    "Stress",
    "Torque",
    "Volume",
    "VolumeInertia",
    "expect_dimension",
]


def expect_dimension(dimension: Dimension) -> Callable[[Quantity], Quantity]:
    """An ``AfterValidator`` body that accepts only quantities of ``dimension``."""

    def _check(quantity: Quantity) -> Quantity:
        if quantity.dimension != dimension:
            raise DimensionMismatchError(dimension, quantity.dimension, "field validation")
        return quantity

    return _check


Length = Annotated[Quantity, AfterValidator(expect_dimension(LENGTH))]
Area = Annotated[Quantity, AfterValidator(expect_dimension(AREA))]
Volume = Annotated[Quantity, AfterValidator(expect_dimension(VOLUME))]
SecondMoment = Annotated[Quantity, AfterValidator(expect_dimension(SECOND_MOMENT))]
VolumeInertia = Annotated[Quantity, AfterValidator(expect_dimension(VOLUME_INERTIA))]
Mass = Annotated[Quantity, AfterValidator(expect_dimension(MASS))]
Force = Annotated[Quantity, AfterValidator(expect_dimension(FORCE))]
Stress = Annotated[Quantity, AfterValidator(expect_dimension(STRESS))]
Torque = Annotated[Quantity, AfterValidator(expect_dimension(TORQUE))]
Density = Annotated[Quantity, AfterValidator(expect_dimension(DENSITY))]
MassInertia = Annotated[Quantity, AfterValidator(expect_dimension(MASS_INERTIA))]
Dimensionless = Annotated[Quantity, AfterValidator(expect_dimension(DIMENSIONLESS))]
Angle = Dimensionless
Money = Annotated[Quantity, AfterValidator(expect_dimension(MONEY))]
MoneyPerMass = Annotated[Quantity, AfterValidator(expect_dimension(MONEY_PER_MASS))]
