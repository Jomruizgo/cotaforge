# ruff: noqa: E501
"""How a hole is made, from what it declares: the one place that decides (standard library only).

A thread makes a hole threaded, whatever else it has. Otherwise a hole-basis tolerance class of IT grade
``max_precision_grade`` or finer (H6, H7, H8 by default) makes it a precision hole: the one that needs a tool such as a
reamer. Anything else, no fit or a coarser one (H9 and above), is plain: an ordinary drill is enough.

This file imports nothing from the package on purpose: the FreeCAD worker is a set of flat modules that cannot import
``cotaforge``, and loads this very file by path (``worker/hole_class.py``), so the adapter and the worker never disagree.
"""

import re

PRECISION = "precision"
PLAIN = "plain"
THREADED = "threaded"
CLASSES = (PRECISION, PLAIN, THREADED)

DEFAULT_MAX_PRECISION_GRADE = 8
"""IT grade at or below which a fit makes a hole a precision hole (the engineer can change it)."""

_CLASS = re.compile(r"^\s*([A-Za-z]{1,2})(\d{1,2})\s*$")


def fit_grade(fit: str | None) -> int | None:
    """IT grade of the hole's own tolerance class: ``H7`` -> 7, ``H7/g6`` -> 7, ``g6/H7`` -> 7 (the hole is the side in
    capital letters, whichever comes first); ``None`` when there is no fit, or it is a shaft class or not a class at all."""
    if not fit:
        return None
    for part in fit.split("/"):
        found = _CLASS.match(part)
        if found is not None and found.group(1).isupper():
            return int(found.group(2))
    return None


def classify(*, threaded: bool, fit: str | None, max_precision_grade: int = DEFAULT_MAX_PRECISION_GRADE) -> str:
    """``threaded``, ``precision`` or ``plain`` (module constants)."""
    if threaded:
        return THREADED
    grade = fit_grade(fit)
    if grade is not None and grade <= max_precision_grade:
        return PRECISION
    return PLAIN
