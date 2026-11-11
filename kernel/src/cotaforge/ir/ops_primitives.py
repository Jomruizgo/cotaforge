"""Operation base and body-level primitives (ir/ops_primitives.py).

``OperationBase`` is shared by every operation (ops_features.py imports it). It is
defined here, next to the body-level primitives (box, cylinder, booleans, moves, copy), so that
``ops_features`` -> ``ops_primitives`` -> ``refs`` stays a one-directional import
chain with no cycle back through ``plan.py``.
"""

import math
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from cotaforge.contract import AxisName, Vec3
from cotaforge.ir.intent import IntentLink
from cotaforge.ir.refs import REF_TYPES, BodyRef, IRModel, OpId, Ref

__all__ = [
    "BooleanIntersectOperation",
    "BooleanSubtractOperation",
    "BooleanUnionOperation",
    "BoxOperation",
    "CopyBodyOperation",
    "CylinderOperation",
    "OperationBase",
    "Placement",
    "RotateOperation",
    "RotateSpec",
    "TranslateOperation",
]


def _check_positive_vec3(value: Vec3) -> Vec3:
    if any(component <= 0 for component in value):
        msg = "expected every component to be > 0"
        raise ValueError(msg)
    return value


class Placement(IRModel):
    """IR form of ``contract.geometry.Transform``."""

    origin: Vec3 = (0.0, 0.0, 0.0)
    axis: AxisName | Vec3 = AxisName.Z
    angle_deg: float = 0.0


class OperationBase(IRModel):
    id: OpId
    op: str
    intent: str = Field(min_length=1)
    links: tuple[IntentLink, ...] = ()

    def refs(self) -> tuple[Ref, ...]:
        """Collect every ``Ref``-typed value reachable from this operation's fields.

        Walks tuples/lists and nested pydantic models recursively, so a
        ``FaceRef`` nested inside an ``EdgeRef.between`` or a ``FaceRef`` used
        as ``distance1_on`` are both returned, alongside the outer ref itself.
        """
        collected: list[Ref] = []

        def walk(value: object) -> None:
            if isinstance(value, REF_TYPES):
                collected.append(value)  # type: ignore[arg-type]
                for name in type(value).model_fields:
                    walk(getattr(value, name))
            elif isinstance(value, BaseModel):
                for name in type(value).model_fields:
                    walk(getattr(value, name))
            elif isinstance(value, (tuple, list)):
                for item in value:
                    walk(item)

        for name in type(self).model_fields:
            if name in ("id", "op", "intent", "links"):
                continue
            walk(getattr(self, name))
        return tuple(collected)


class BoxOperation(OperationBase):
    op: Literal["box"] = "box"
    size: Vec3
    placement: Placement = Placement()

    @field_validator("size")
    @classmethod
    def _check_size(cls, value: Vec3) -> Vec3:
        return _check_positive_vec3(value)


class CylinderOperation(OperationBase):
    op: Literal["cylinder"] = "cylinder"
    diameter_mm: float = Field(gt=0)
    height_mm: float = Field(gt=0)
    axis: AxisName = AxisName.Z
    origin: Vec3 = (0.0, 0.0, 0.0)


def _keep_tools_is_for_subtract(data: Any) -> Any:
    if isinstance(data, dict) and "keep_tools" in data:
        msg = (
            "keep_tools is only for boolean_subtract (a coping cut that leaves its tool as a body); a union or an "
            "intersect gives one body: to combine a body and still keep it, copy_body it first"
        )
        raise ValueError(msg)
    return data


class BooleanUnionOperation(OperationBase):
    op: Literal["boolean_union"] = "boolean_union"
    target: BodyRef
    tools: tuple[BodyRef, ...] = Field(min_length=1)

    _no_keep_tools = model_validator(mode="before")(_keep_tools_is_for_subtract)


class BooleanSubtractOperation(OperationBase):
    op: Literal["boolean_subtract"] = "boolean_subtract"
    target: BodyRef
    tools: tuple[BodyRef, ...] = Field(min_length=1)
    keep_tools: bool = False
    """Fish-mouth coping: when ``True`` every ``tools`` body survives the subtraction as its own live,
    independently referenceable body (unchanged: same faces/edges, same identity), instead of being
    consumed. Only ``boolean_subtract`` supports it -- ``union``/``intersect`` already keep every input's
    volume in the fused/intersected result, so "keep the tool too" would double-count it there."""


class BooleanIntersectOperation(OperationBase):
    op: Literal["boolean_intersect"] = "boolean_intersect"
    target: BodyRef
    tools: tuple[BodyRef, ...] = Field(min_length=1)

    _no_keep_tools = model_validator(mode="before")(_keep_tools_is_for_subtract)


class TranslateOperation(OperationBase):
    op: Literal["translate"] = "translate"
    target: BodyRef
    offset: Vec3


class RotateOperation(OperationBase):
    op: Literal["rotate"] = "rotate"
    target: BodyRef
    axis: AxisName
    angle_deg: float
    about: Vec3 = (0.0, 0.0, 0.0)


class RotateSpec(IRModel):
    """The rotation part of ``copy_body``: same semantics as ``RotateOperation`` (right-hand rule about the
    ``axis`` line through ``about``)."""

    axis: AxisName
    angle_deg: float
    about: Vec3 = (0.0, 0.0, 0.0)


class CopyBodyOperation(OperationBase):
    """Duplicate a body: the copy is rotated about ``rotate.about`` first, then translated by ``offset``.

    The result is a NEW body (its own ref); the source is left untouched.

    ``scale`` (default 1) grows or shrinks the copy uniformly about the point ``scale_about`` (a shrinkage allowance
    for a mould, 1.006 for 0.6 %). The order is: scale about ``scale_about``, then rotate, then offset."""

    op: Literal["copy_body"] = "copy_body"
    source: BodyRef
    offset: Vec3 = (0.0, 0.0, 0.0)
    rotate: RotateSpec | None = None
    scale: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    scale_about: Vec3 = (0.0, 0.0, 0.0)

    @field_validator("scale_about")
    @classmethod
    def _check_scale_about_finite(cls, value: Vec3) -> Vec3:
        if not all(math.isfinite(component) for component in value):
            msg = "copy_body.scale_about must be finite numbers"
            raise ValueError(msg)
        return value
