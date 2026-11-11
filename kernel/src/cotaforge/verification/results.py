"""Expectation / CheckResult models."""

from enum import Enum
from typing import Literal

from pydantic import BaseModel

from cotaforge.contract import BoundingBox, Transform, Vec3


class CheckKind(str, Enum):  # noqa: UP042
    asserted = "asserted"
    reported = "reported"


# ``Transform`` is added to the architecture's Value union so ``component_transform`` can be
# expressed as a single expectation (see 6.2, assembly rows).
Value = bool | int | float | Vec3 | BoundingBox | Transform

Unit = Literal["mm", "mm2", "mm3", "count", "deg", "vec", "bbox", "bool", "transform"]


class Expectation(BaseModel, frozen=True):
    name: str
    kind: CheckKind
    unit: Unit
    value: Value
    note: str = ""


class CheckResult(BaseModel, frozen=True):
    name: str
    kind: CheckKind
    ok: bool | None
    expected: Value | None
    observed: Value | None
    message: str = ""
    basis: str = ""
    """What the expected value was computed from, when that is worth telling (``volume_delta`` measured against the
    real boolean of the op's tool with the previous solid)."""
