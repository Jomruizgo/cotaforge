"""``tool_overlap``: the optional, read-only query that measures a tool against the part before the op builds it
(contract/probe.py).

A cut removes the part of its tool that lies in material, and a boss adds the part of its tool that does not: both
numbers are a boolean of the tool solid with the part as it is NOW. Computing them from the real geometry (instead of
area x depth) is what lets an op that breaks into an existing cavity, or a sketch with a loop inside a loop, be checked
against what the CAD must really do.
"""

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from cotaforge.contract.handles import BodyHandle, FaceHandle, SketchHandle
from cotaforge.contract.requests import Request


class HoleTool(BaseModel, frozen=True, allow_inf_nan=False):
    """The solid of a hole on a PLANAR face: ``outline`` is its half section in ``(t, r)`` (``t`` from the entry plane
    along the drill direction, the inward normal of ``face``; ``r`` from the axis), see ``contract.holetool``."""

    face: FaceHandle
    position: tuple[float, float]
    outline: tuple[tuple[float, float], ...] = Field(min_length=3)


class ToolOverlapRequest(Request):
    """The tool is the profile loops of ``sketch`` swept from ``lo_mm`` to ``hi_mm`` along the sketch normal (nested
    loops by the even-odd rule), or a ``hole``. ``mode`` cut: measure against the body the op would cut (the body of the
    sketch's face, else the only body); add: against the body a merged boss would join."""

    mode: Literal["cut", "add"]
    sketch: SketchHandle | None = None
    lo_mm: float = 0.0
    hi_mm: float = 0.0
    hole: HoleTool | None = None

    @model_validator(mode="after")
    def _one_tool(self) -> "ToolOverlapRequest":
        if (self.sketch is None) == (self.hole is None):
            msg = "tool_overlap needs exactly one of sketch (swept from lo_mm to hi_mm) and hole"
            raise ValueError(msg)
        if self.sketch is not None and self.hi_mm <= self.lo_mm:
            msg = "tool_overlap: hi_mm must be greater than lo_mm"
            raise ValueError(msg)
        return self


class ToolOverlapResult(BaseModel, frozen=True):
    """``tool_volume_mm3``: the volume of the tool solid. ``common_volume_mm3``: the part of it inside the body now
    (the volume a cut removes; a boss adds ``tool_volume_mm3 - common_volume_mm3``). ``body``: the body measured, or
    ``None`` when there is none to cut or join (the whole tool is then new material)."""

    tool_volume_mm3: float
    common_volume_mm3: float
    body: BodyHandle | None = None
