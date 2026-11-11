"""``cotaforge.units`` quantities."""

import math

import pytest
from pydantic import BaseModel, ValidationError

from cotaforge.units import (
    AREA,
    CANONICAL_UNIT,
    DIMENSIONLESS,
    FORCE,
    LENGTH,
    MASS_INERTIA,
    STRESS,
    TORQUE,
    UNIT_TABLE,
    Dimension,
    DimensionMismatchError,
    Q,
    Quantity,
    UnitsError,
    UnknownUnitError,
    canonical_unit,
)

# --- dimensions ---------------------------------------------------------------------


def test_dimension_arithmetic() -> None:
    assert FORCE / AREA == STRESS
    assert FORCE * LENGTH == TORQUE
    assert LENGTH**2 == AREA
    assert LENGTH / LENGTH == DIMENSIONLESS
    assert str(FORCE) == "L1 M1 T-2"
    assert str(DIMENSIONLESS) == "dimensionless"


def test_canonical_unit_table_hit_and_synthesised() -> None:
    assert canonical_unit(STRESS) == "Pa"
    assert canonical_unit(TORQUE) == "N*m"
    stiffness = FORCE / LENGTH
    assert stiffness not in CANONICAL_UNIT
    assert canonical_unit(stiffness) == "si:L0M1T-2C0"
    assert Q(3, canonical_unit(stiffness)).dimension == stiffness


def test_every_canonical_unit_has_factor_one() -> None:
    for dimension, unit in CANONICAL_UNIT.items():
        assert UNIT_TABLE[unit] == (dimension, 1.0)


# --- construction and parsing -------------------------------------------------------


def test_q_accepts_text_and_pairs() -> None:
    assert Q("20 mm") == Quantity(value=20.0, unit="mm")
    assert Q(20, "mm") == Q("20 mm")
    assert Q("1.5e3 N").value == 1500.0
    assert Q("-0.5 mm").value == -0.5
    assert Q("1 1").dimension == DIMENSIONLESS


@pytest.mark.parametrize("text", ["20", "20mm", "mm 20", "twenty mm", ""])
def test_q_rejects_malformed_text(text: str) -> None:
    with pytest.raises(UnitsError):
        Q(text)


def test_q_raises_unknown_unit_unwrapped() -> None:
    with pytest.raises(UnknownUnitError) as info:
        Q("1 furlong")
    assert info.value.unit == "furlong"
    with pytest.raises(UnknownUnitError):
        Q(1, "furlong")


def test_q_without_unit_for_a_number_is_an_error() -> None:
    with pytest.raises(UnitsError):
        Q(20)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_non_finite_values_are_rejected(value: float) -> None:
    with pytest.raises(UnitsError):
        Q(value, "mm")
    with pytest.raises(ValidationError):
        Quantity(value=value, unit="mm")


def test_model_validation_accepts_text_pair_and_dict() -> None:
    assert Quantity.model_validate("20 mm") == Q("20 mm")
    assert Quantity.model_validate(("20", "mm")) == Q("20 mm")
    assert Quantity.model_validate({"value": 20, "unit": "mm"}) == Q("20 mm")


def test_model_validation_rejects_bare_numbers_and_unknown_units() -> None:
    with pytest.raises(ValidationError):
        Quantity.model_validate(20)
    with pytest.raises(ValidationError, match="unknown unit"):
        Quantity(value=1.0, unit="furlong")
    with pytest.raises(ValidationError):
        Quantity.model_validate(("1", "mm", "extra"))


def test_json_round_trip_keeps_value_and_unit() -> None:
    quantity = Q("20.25 mm")
    assert quantity.model_dump_json() == '{"value":20.25,"unit":"mm"}'
    assert Quantity.model_validate_json(quantity.model_dump_json()) == quantity
    assert Quantity.model_json_schema()["required"] == ["value", "unit"]


def test_str_and_frozen() -> None:
    assert str(Q("20 mm")) == "20.0 mm"
    with pytest.raises(ValidationError):
        Q("20 mm").value = 3.0  # type: ignore[misc]


# --- conversion -------------------------------------------------------------------------


@pytest.mark.parametrize("unit", sorted(UNIT_TABLE))
def test_every_table_symbol_round_trips_through_its_canonical_unit(unit: str) -> None:
    quantity = Q(3.7, unit)
    canonical = canonical_unit(quantity.dimension)
    back = quantity.to(canonical).to(unit)
    assert back.unit == unit
    assert math.isclose(back.value, 3.7, rel_tol=1e-12)


def test_to_converts_and_si_uses_the_canonical_unit() -> None:
    assert Q("1 m").to("mm") == Q("1000 mm")
    assert Q("4 MPa").to("N/mm2").value == pytest.approx(4.0)
    assert Q("2 kN*m").si() == Q("2000 N*m")
    assert Q("90 deg").to("rad").value == pytest.approx(math.pi / 2)
    assert Q("50 %").to("1").value == pytest.approx(0.5)
    assert Q("20 mm").to("mm") is not None


def test_to_refuses_other_dimensions_and_unknown_units() -> None:
    with pytest.raises(DimensionMismatchError) as info:
        Q("20 mm").to("N")
    assert info.value.expected == LENGTH and info.value.actual == FORCE
    with pytest.raises(UnknownUnitError):
        Q("20 mm").to("inch")


# --- arithmetic ---------------------------------------------------------------------------


def test_add_sub_same_dimension_result_in_left_unit() -> None:
    assert Q("20 mm") + Q("1 m") == Q("1020 mm")
    assert (Q("20 mm") + Q("1 m")).unit == "mm"
    assert Q("1 m") - Q("20 mm") == Q("0.98 m")


def test_add_sub_mismatch_raises() -> None:
    with pytest.raises(DimensionMismatchError):
        Q("200 N") + Q("50 mm")
    with pytest.raises(DimensionMismatchError):
        Q("200 N") - Q("50 mm")
    with pytest.raises(UnitsError):
        Q("200 N") + 3  # type: ignore[operator]


def test_mul_div_quantities_land_in_canonical_units() -> None:
    stress = Q("200 N") / Q("50 mm2")
    assert stress == Quantity(value=4_000_000.0, unit="Pa")
    assert stress.to("MPa") == Q("4 MPa")
    torque = Q("100 N") * Q("250 mm")
    assert torque.unit == "N*m" and torque.value == pytest.approx(25.0)
    inertia = Q("1 mm5") * Q("7850 kg/m3")
    assert inertia.dimension == MASS_INERTIA and inertia.unit == "kg*m2"
    assert (Q("2 m") / Q("4 m")) == Q("0.5 1")


def test_scalar_multiply_and_divide_but_never_add() -> None:
    assert Q("20 mm") * 2 == Q("40 mm")
    assert 2 * Q("20 mm") == Q("40 mm")
    assert Q("20 mm") / 4 == Q("5 mm")
    assert (1 / Q("2 s")).unit == "si:L0M0T-1C0"
    with pytest.raises(UnitsError, match="requires a Quantity operand"):
        Q("20 mm") + 2.0  # type: ignore[operator]


def test_pow_and_sqrt() -> None:
    assert Q("2 m") ** 3 == Q("8 m3")
    assert (Q("20 mm") ** 2).to("mm2").value == pytest.approx(400.0)
    assert Q("4 m2").sqrt() == Q("2 m")
    assert (Q("9 mm2").sqrt()).to("mm").value == pytest.approx(3.0)
    with pytest.raises(UnitsError, match="even"):
        Q("8 m3").sqrt()
    with pytest.raises(UnitsError, match="negative"):
        Q("-4 m2").sqrt()
    with pytest.raises(UnitsError):
        Q("2 m") ** 1.5  # type: ignore[operator]


def test_overflow_in_arithmetic_raises_units_error_not_validation_error() -> None:
    with pytest.raises(UnitsError):
        Q("1e200 N") * Q("1e200 N")


def test_neg_and_abs() -> None:
    assert -Q("20 mm") == Q("-20 mm")
    assert abs(Q("-20 mm")) == Q("20 mm")


# --- comparison ------------------------------------------------------------------------------


def test_equality_and_hash_across_units() -> None:
    assert Q("1 m") == Q("1000 mm")
    assert hash(Q("1 m")) == hash(Q("1000 mm"))
    assert Q("4 MPa") == Q("4 N/mm2")
    assert len({Q("1 m"), Q("1000 mm"), Q("100 cm")}) == 1
    assert Q("1 m") != Q("2 m")


def test_equality_with_non_quantity_is_false() -> None:
    assert (Q("1 1") == 1.0) is False
    assert (Q("1 m") == "1 m") is False
    assert Q("1 m") != 1.0


def test_ordering_in_si_and_mismatch() -> None:
    assert Q("999 mm") < Q("1 m")
    assert Q("1 m") <= Q("1000 mm")
    assert Q("2 kN") > Q("1999 N")
    assert Q("2 kN") >= Q("2000 N")
    with pytest.raises(DimensionMismatchError):
        _ = Q("1 m") < Q("1 N")


def test_equality_across_dimensions_is_false_not_a_raise() -> None:
    assert (Q("1 m") == Q("1 N")) is False
    assert Q("1 m") != Q("1 N")
    assert (Q("1 m") in [Q("1 N")]) is False
    mixed = {Q("1 m"), Q("1 N")}
    assert len(mixed) == 2
    assert Q("1 m") in mixed and Q("1 N") in mixed


def test_is_close() -> None:
    assert Q("1 m").is_close(Q("1000.0001 mm"), rel=1e-6)
    assert not Q("1 m").is_close(Q("1001 mm"))
    assert Q("0 m").is_close(Q("0.0005 mm"), abs_si=1e-6)
    with pytest.raises(DimensionMismatchError):
        Q("1 m").is_close(Q("1 N"))


# --- nested in a model ------------------------------------------------------------------------


class _Holder(BaseModel):
    size: Quantity
    others: tuple[Quantity, ...] = ()


def test_quantity_inside_a_model_accepts_text_and_round_trips() -> None:
    holder = _Holder(size="20 mm", others=["1 N", ("2", "kN")])  # type: ignore[arg-type]
    assert holder.size == Q("20 mm") and holder.others == (Q("1 N"), Q("2 kN"))
    assert _Holder.model_validate_json(holder.model_dump_json()) == holder


def test_dimension_is_hashable_and_frozen() -> None:
    assert {Dimension(length=1): "m"}[LENGTH] == "m"
    with pytest.raises(ValidationError):
        LENGTH.length = 2  # type: ignore[misc]
