"""Mould components from a manufacturer's catalogue (family DME), as parts a CAD draws with its own features.

``expand`` turns a reference (or a size) and a placement into ``PartSpec``s; ``dme`` holds the published tables with
their sources. Pure data and arithmetic: no CAD, no I/O.
"""

from cotaforge.mouldparts import dme
from cotaforge.mouldparts.build import (
    KINDS,
    ComponentError,
    Placement,
    angle_pin_rows,
    basis,
    expand,
    wear_rows,
)

__all__ = ["KINDS", "ComponentError", "Placement", "angle_pin_rows", "basis", "dme", "expand", "wear_rows"]
