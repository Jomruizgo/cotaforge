"""Density of a material: the declared number first, then the cited table, otherwise none (never a guess)."""

import pytest

from cotaforge.constraints import DeclaredSet, describe_density, lookup_density, mass_kg
from tests.unit.memory.builders import make_material

NO_DECLARATIONS = DeclaredSet([])
SHOP_PROVENANCE = {"source": "shop_measurement", "reference": "Pesaje lote 7", "by": "Ana", "date": "2026-10-11"}


def test_a_common_material_gets_its_table_density_with_the_source() -> None:
    found = lookup_density("Acero estructural", NO_DECLARATIONS)
    assert found.density is not None and found.density.kg_m3 == 7850
    assert "EN 1993-1-1" in found.density.source and not found.density.unverified
    assert "shipped table" in describe_density(found.density)


@pytest.mark.parametrize("name", ["A36", "s355jr", "ACERO  AL CARBONO", "Structural Steel"])
def test_designations_and_spellings_of_structural_steel_match(name: str) -> None:
    found = lookup_density(name, NO_DECLARATIONS)
    assert found.density is not None and found.density.kg_m3 == 7850


def test_an_unchecked_table_value_is_flagged_in_the_description() -> None:
    found = lookup_density("Inox 304", NO_DECLARATIONS)
    assert found.density is not None and found.density.unverified
    assert "UNVERIFIED" in describe_density(found.density)
    assert lookup_density("6061-T6", NO_DECLARATIONS).density is not None


def test_a_declared_density_wins_over_the_table_and_names_its_provenance() -> None:
    entry = make_material(
        "acero-taller",
        "Acero estructural",
        ["A36"],
        provenance=SHOP_PROVENANCE,
        material={"name": "Acero estructural", "designations": ["A36"], "properties": {"density_kg_m3": 7840.0}},
    )
    found = lookup_density("A36", DeclaredSet([entry]))
    assert found.density is not None and found.density.kg_m3 == 7840
    assert "acero-taller" in found.density.source and "Pesaje lote 7" in found.density.source


def test_an_unknown_material_has_no_density_and_says_what_to_declare() -> None:
    found = lookup_density("Unobtainium", NO_DECLARATIONS)
    assert found.density is None and "density_kg_m3" in found.reason


def test_no_material_or_several_materials_have_no_density() -> None:
    assert lookup_density(None, NO_DECLARATIONS).density is None
    assert "set_material" in lookup_density("", NO_DECLARATIONS).reason
    assert "several materials" in lookup_density("A36 / 6061", NO_DECLARATIONS).reason


def test_a_declared_property_that_is_not_a_positive_number_is_ignored() -> None:
    for bad in (0, -5, "heavy", True):
        entry = make_material(
            "raro",
            "Raro",
            [],
            provenance=SHOP_PROVENANCE,
            material={"name": "Raro", "designations": [], "properties": {"density_kg_m3": bad}},
        )
        assert lookup_density("Raro", DeclaredSet([entry])).density is None


def test_mass_is_volume_in_cubic_metres_times_density() -> None:
    found = lookup_density("A36", NO_DECLARATIONS)
    assert found.density is not None
    assert mass_kg(100 * 60 * 8, found.density) == pytest.approx(0.3768)
