"""Density of a material and the mass it gives: never a guess.

A density comes from, in this order, (1) the ``density_kg_m3`` property of the declared material entry (the shop's or
supplier's number, whose provenance the memory already requires) or (2) the small table below, every value with the
source it is taken from. Anything else is "not available" with the reason; the caller never invents one.
"""

from dataclasses import dataclass

from cotaforge.constraints.declared import DeclaredSet
from cotaforge.memory import normalize_key


@dataclass(frozen=True)
class Density:
    kg_m3: float
    source: str
    unverified: bool = False  # a table value whose source could not be checked against the document itself


@dataclass(frozen=True)
class DensityLookup:
    """``density`` is None when none is known; ``reason`` then says why (and what to declare)."""

    density: Density | None
    reason: str = ""


@dataclass(frozen=True)
class _TableRow:
    aliases: tuple[str, ...]
    kg_m3: float
    source: str
    unverified: bool = False


# Every value with its source. ``unverified`` marks the ones whose source differs between references or was not checked
# against the document: the reply says so, and a declared density (memory) always wins.
_TABLE: tuple[_TableRow, ...] = (
    _TableRow(
        (
            "acero estructural",
            "acero al carbono",
            "structural steel",
            "carbon steel",
            "acero a36",
            "a36",
            "astm a36",
            "s235",
            "s235jr",
            "s275",
            "s275jr",
            "s355",
            "s355jr",
        ),
        7850.0,
        "EN 1993-1-1:2005 3.2.6 (unit mass of steel)",
    ),
    _TableRow(
        ("acero inoxidable 304", "inox 304", "stainless 304", "stainless steel 304", "aisi 304", "304", "1.4301"),
        7900.0,
        "EN 10088-1 (1.4301 / AISI 304; North American references give 8000)",
        unverified=True,
    ),
    _TableRow(
        ("acero inoxidable 316", "inox 316", "stainless 316", "stainless steel 316", "aisi 316", "316", "1.4401"),
        8000.0,
        "EN 10088-1 (1.4401 / AISI 316)",
        unverified=True,
    ),
    _TableRow(
        ("aluminio 6061", "aluminium 6061", "aluminum 6061", "6061", "6061-t6", "en aw-6061"),
        2700.0,
        "EN 1999-1-1:2007 3.2.2 (unit mass of aluminium alloys); ASM 2.70 g/cm3",
    ),
)

_BY_KEY = {normalize_key(alias): row for row in _TABLE for alias in row.aliases}


def lookup_density(material: str | None, declared: DeclaredSet) -> DensityLookup:
    """The density of ``material`` (the name or designation the design carries), or why there is none."""
    name = (material or "").strip()
    if not name:
        return DensityLookup(None, "no material is assigned: doc(action=set_material)")
    if " / " in name:
        return DensityLookup(None, f"the document carries several materials ({name}): one material per part")
    found = declared.find_material(name)
    if found is not None and found.density_kg_m3 is not None:
        where = f"memory entry {found.entry_id}" + (f" ({found.reference})" if found.reference else "")
        return DensityLookup(Density(found.density_kg_m3, where))
    row = _BY_KEY.get(normalize_key(name))
    if row is not None:
        return DensityLookup(Density(row.kg_m3, f"shipped table: {row.source}", row.unverified))
    return DensityLookup(
        None,
        f"no density known for '{name}': save its material entry with a density_kg_m3 property and a provenance",
    )


def mass_kg(volume_mm3: float, density: Density) -> float:
    """Mass of ``volume_mm3`` of a material of this density (1 mm3 = 1e-9 m3)."""
    return volume_mm3 * 1e-9 * density.kg_m3


def describe_density(density: Density) -> str:
    unverified = "; UNVERIFIED value, confirm it" if density.unverified else ""
    return f"density {density.kg_m3:g} kg/m3, {density.source}{unverified}"
