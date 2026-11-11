"""Typed ``Annotated`` aliases."""

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from cotaforge.units import (
    DIMENSIONLESS,
    Angle,
    Area,
    Density,
    Dimensionless,
    Force,
    Length,
    Mass,
    MassInertia,
    Money,
    MoneyPerMass,
    Q,
    SecondMoment,
    Stress,
    Torque,
    Volume,
    expect_dimension,
)


class _Inputs(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    span: Length
    load: Force


def test_typed_alias_accepts_the_right_dimension_in_any_unit() -> None:
    inputs = _Inputs(span="200 mm", load="1.5 kN")  # type: ignore[arg-type]
    assert inputs.span == Q("0.2 m") and inputs.load == Q("1500 N")


def test_typed_alias_rejects_a_wrong_dimension_with_a_wrapped_mismatch() -> None:
    with pytest.raises(ValidationError) as info:
        _Inputs(span="200 N", load="1 kN")  # type: ignore[arg-type]
    error = info.value.errors()[0]
    assert error["loc"] == ("span",)
    assert "dimension mismatch" in error["msg"]


def test_typed_alias_rejects_a_bare_float() -> None:
    with pytest.raises(ValidationError):
        _Inputs(span=200.0, load="1 kN")  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("alias", "good", "bad"),
    [
        (Length, "1 mm", "1 N"),
        (Area, "1 mm2", "1 mm"),
        (Volume, "1 mm3", "1 mm2"),
        (SecondMoment, "1 mm4", "1 mm3"),
        (Mass, "1 kg", "1 N"),
        (Force, "1 kN", "1 kg"),
        (Stress, "1 MPa", "1 N"),
        (Torque, "1 N*mm", "1 N"),
        (Density, "1 g/cm3", "1 kg"),
        (MassInertia, "1 kg*m2", "1 mm4"),
        (Dimensionless, "0.9 1", "1 mm"),
        (Angle, "30 deg", "1 mm"),
        (Money, "10 EUR", "1 kg"),
        (MoneyPerMass, "6.8 EUR/kg", "10 EUR"),
    ],
)
def test_every_alias_accepts_its_dimension_and_rejects_another(alias: object, good: str, bad: str) -> None:
    class _One(BaseModel):
        value: alias  # type: ignore[valid-type]

    assert _One(value=good).value == Q(good)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        _One(value=bad)  # type: ignore[arg-type]


def test_expect_dimension_is_reusable_directly() -> None:
    check = expect_dimension(DIMENSIONLESS)
    assert check(Q("1 1")) == Q("1 1")
    with pytest.raises(ValueError, match="dimension mismatch"):
        check(Q("1 mm"))
