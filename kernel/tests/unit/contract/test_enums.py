"""Tests for cotaforge.contract.enums: values are lowercase member names."""

from enum import Enum

from cotaforge.contract import enums

# PlaneName/AxisName carry the exact plane/axis labels of table 3.3 (XY, YZ, XZ, X, Y, Z),
# which are already the canonical spelling, not lowercase-able words.
_EXEMPT_FROM_LOWERCASE = {enums.PlaneName, enums.AxisName}

_ALL_ENUMS = [
    enums.SurfaceType,
    enums.CurveType,
    enums.PlaneName,
    enums.AxisName,
    enums.Direction,
    enums.EndCondition,
    enums.HoleStandard,
    enums.MateAlignment,
    enums.Side,
    enums.BooleanKind,
    enums.DocumentKind,
    enums.ProvenanceRole,
    enums.ErrorCode,
]


def test_every_enum_is_str_enum() -> None:
    for enum_cls in _ALL_ENUMS:
        assert issubclass(enum_cls, str)
        assert issubclass(enum_cls, Enum)


def test_enum_values_equal_lowercase_member_names_except_plane_and_axis_names() -> None:
    for enum_cls in _ALL_ENUMS:
        if enum_cls in _EXEMPT_FROM_LOWERCASE:
            continue
        for member in enum_cls:
            assert member.value == member.name.lower()


def test_plane_and_axis_names_keep_their_literal_spelling() -> None:
    assert enums.PlaneName.XY.value == "XY"
    assert enums.PlaneName.YZ.value == "YZ"
    assert enums.PlaneName.XZ.value == "XZ"
    assert enums.AxisName.X.value == "X"
    assert enums.AxisName.Y.value == "Y"
    assert enums.AxisName.Z.value == "Z"
