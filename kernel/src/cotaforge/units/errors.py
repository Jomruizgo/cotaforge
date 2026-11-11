"""Errors raised by ``cotaforge.units``.

All of them are ``ValueError`` subclasses so pydantic wraps them into a ``ValidationError`` when they
are raised inside a model validator (a typed alias rejecting a wrong dimension, an unknown unit in a
JSON document); the direct constructors (``Q``, arithmetic, ``to``) raise them unwrapped.
"""

__all__ = ["DimensionMismatchError", "UnitsError", "UnknownUnitError"]


class UnitsError(ValueError):
    """Base class for every error raised by ``cotaforge.units``."""


class UnknownUnitError(UnitsError):
    """A unit symbol that is neither in ``UNIT_TABLE`` nor a synthesised ``si:...`` symbol."""

    def __init__(self, unit: str) -> None:
        self.unit = unit
        super().__init__(f"unknown unit {unit!r}: not in the unit table (an unknown unit is an error, never a guess)")


class DimensionMismatchError(UnitsError):
    """Two quantities of different dimensions met in an operation that requires the same dimension."""

    def __init__(self, expected: object, actual: object, operation: str) -> None:
        self.expected = expected
        self.actual = actual
        self.operation = operation
        super().__init__(f"dimension mismatch in {operation}: expected {expected}, got {actual}")
