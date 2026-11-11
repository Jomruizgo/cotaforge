# ruff: noqa: E501
"""Conventions of the harness: behaviour every user gets by default and the engineer can override.

Holes are the first one (``holes.py``); more will come. A convention is NOT memory: its defaults ship in the code and its
overrides are plain files (``convenciones.yaml``), never entries of the memory system.
"""

from cotaforge.conventions.classify import CLASSES, PLAIN, PRECISION, THREADED, classify, fit_grade
from cotaforge.conventions.holes import (
    DEFAULT_COLOURS,
    FILENAME,
    SCOPES,
    ConventionError,
    Conventions,
    HoleConvention,
    Resolved,
    parse_colour,
)

__all__ = [
    "CLASSES",
    "DEFAULT_COLOURS",
    "FILENAME",
    "PLAIN",
    "PRECISION",
    "SCOPES",
    "THREADED",
    "ConventionError",
    "Conventions",
    "HoleConvention",
    "Resolved",
    "classify",
    "fit_grade",
    "parse_colour",
]
