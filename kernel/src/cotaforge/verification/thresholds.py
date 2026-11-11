"""Numeric tolerances shared by the resolver and the Level 2 checks."""

import math
from typing import Literal

from pydantic import BaseModel

from cotaforge.contract import BoundingBox, Vec3

Unit = Literal["mm", "mm2", "mm3", "count", "deg"]


class Thresholds(BaseModel, frozen=True):
    abs_mm: float = 1e-3
    abs_mm2: float = 1e-3
    abs_mm3: float = 1e-3
    rel: float = 1e-4
    angle_deg: float = 0.5
    position_mm: float = 1e-2

    def close(self, expected: float, observed: float, unit: Unit) -> bool:
        """``|e - o| <= abs(unit) + rel * |e|``; ``count`` is exact; ``deg`` uses ``angle_deg``."""
        if unit == "count":
            return expected == observed
        if unit == "deg":
            return abs(expected - observed) <= self.angle_deg
        absolute = {"mm": self.abs_mm, "mm2": self.abs_mm2, "mm3": self.abs_mm3}[unit]
        return abs(expected - observed) <= absolute + self.rel * abs(expected)

    def close_position(self, expected: Vec3, observed: Vec3) -> bool:
        return math.dist(expected, observed) <= self.position_mm

    def parallel(self, expected: Vec3, observed: Vec3, *, either_sense: bool = False) -> bool:
        """Angle between the two (non-zero) vectors is within ``angle_deg`` (or 180 - angle when either sense)."""
        angle = _angle_deg(expected, observed)
        if angle <= self.angle_deg:
            return True
        return either_sense and abs(180.0 - angle) <= self.angle_deg

    def close_bbox(self, expected: BoundingBox, observed: BoundingBox) -> bool:
        return self.close_position(expected.min, observed.min) and self.close_position(expected.max, observed.max)

    def bbox_within(self, inner: BoundingBox, outer: BoundingBox) -> bool:
        """``inner`` lies inside ``outer`` allowing ``position_mm`` of slack per component."""
        tol = self.position_mm
        return all(inner.min[i] >= outer.min[i] - tol and inner.max[i] <= outer.max[i] + tol for i in range(3))


def _angle_deg(a: Vec3, b: Vec3) -> float:
    la = math.sqrt(sum(x * x for x in a))
    lb = math.sqrt(sum(x * x for x in b))
    if la == 0.0 or lb == 0.0:
        return 180.0
    cos = sum(x * y for x, y in zip(a, b, strict=True)) / (la * lb)
    return math.degrees(math.acos(max(-1.0, min(1.0, cos))))


DEFAULT_THRESHOLDS = Thresholds()
