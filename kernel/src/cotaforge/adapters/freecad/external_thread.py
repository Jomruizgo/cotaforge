"""``external_thread``: a cosmetic ISO metric thread on the outside cylinder of a shaft end (an operation this adapter adds
to the common vocabulary, through ``Adapter.extra_operations``).

FreeCAD has a cosmetic thread for a hole (the ``hole`` operation) and none for a shaft: a threaded shaft end is a plain
cylinder in the model and the thread is told by the drawing. This operation is the missing half. It changes no geometry:
the cylinder stays at the nominal (major) diameter and a record is stored in the document (an object of the tree,
``Cotaforge`` properties: designation, pitch, minor diameter, length, the axis and the end the thread runs from) that the
sheet reads, the same way it reads the thread of a ``hole``: the ISO 6410 representation, the callout and the thread
requirement of the drawing.

This module is plain data and a pure function (no FreeCAD): the worker (``worker/ops_threads.py``) checks the geometry.
"""

from typing import Any

from pydantic import BaseModel, Field, field_validator

from cotaforge.contract.extension import OperationSpec
from cotaforge.contract.threads import THREADS, fine_thread_sizes, thread
from cotaforge.ir import FaceRef

__all__ = ["OP_NAME", "ExternalThread", "major_tolerance_mm", "minor_diameter_mm", "spec"]

OP_NAME = "external_thread"
MAJOR_TOLERANCE_MM = 0.05
"""The cylinder is the thread blank at its nominal diameter: it may differ from the major diameter by this much."""
BASIC_MINOR_FACTOR = 1.0825
"""ISO 68-1 basic profile: the minor diameter of an external thread is d1 = d - 1.0825 P (H = 0.866 P, 5/8 H on each side)."""


def minor_diameter_mm(major_mm: float, pitch_mm: float) -> float:
    """``d1`` of the ISO 68-1 basic profile, which the thin line of the ISO 6410 representation is drawn at."""
    return round(major_mm - BASIC_MINOR_FACTOR * pitch_mm, 3)


def major_tolerance_mm() -> float:
    return MAJOR_TOLERANCE_MM


class ExternalThread(BaseModel):
    """A cosmetic external ISO metric thread on the outside cylinder of a shaft end, like the thread of a hole but on a
    pin: no geometry is added, the thread is stored for the drawing (its representation, its callout) and for the
    requirements of the drawing. The cylinder must already be the nominal (major) diameter of the thread. ``end`` is the
    flat end face of the shaft the thread starts from; ``length_mm`` runs from it along the cylinder."""

    face: FaceRef
    """The outside cylindrical face of the shaft end (a convex cylinder, not a bore: a thread in a bore is a ``hole``)."""
    size: str
    """ISO metric designation: ``M12`` (coarse) or ``M12x1.25`` (fine), as in the thread table of ``hole``."""
    length_mm: float = Field(gt=0)
    """Thread length from the end face, along the shaft (<= the length of the cylinder)."""
    end: FaceRef
    """The planar end face of the shaft the thread starts from (normal to the cylinder axis, at one end of it)."""

    @field_validator("size")
    @classmethod
    def _known_size(cls, value: str) -> str:
        known = thread(value)
        if known is None:
            fine = ", ".join(fine_thread_sizes()[:6])
            msg = (
                f"size {value!r} is not an ISO metric thread of the table: coarse M2..M68 ({', '.join(sorted(THREADS)[:6])}"
                f", ...) or fine like {fine}, ..."
            )
            raise ValueError(msg)
        return known.designation


def spec() -> OperationSpec:
    """The operation as ``Adapter.extra_operations`` offers it."""
    return OperationSpec(
        name=OP_NAME,
        params=ExternalThread,
        purpose="cosmetic ISO metric thread on a shaft end (cylinder + end face, size, length): no geometry, for the drawing",
        guide=(
            "Use it for the threaded end of a shaft or pin, the way `hole` threads a hole: model the cylinder at the "
            "nominal diameter of the thread (a thread M12 is a Ø12 cylinder), then `external_thread` with the cylinder, "
            "its end face, `size` and `length_mm`. It adds no geometry and the part keeps its volume: the thread is "
            "stored in the document and the drawing draws the ISO 6410 representation (thin line at the minor "
            "diameter, 3/4 arc end on) and asks for its callout. It refuses a cylinder whose diameter is not the "
            "thread's major diameter, a bore, an end face that is not at an end of the cylinder, and a length longer "
            "than the cylinder. A thread undercut or a chamfer at the end are separate features."
        ),
        method="external_thread",
        example={
            "face": {"kind": "face", "from_op": "shaft", "select": {"normal": [1, 0, 0]}},
            "size": "M12",
            "length_mm": 20,
            "end": {"kind": "face", "from_op": "shaft", "select": {"normal": [0, 0, 1]}},
        },
        category="shafts",
    )


def record(size: str) -> dict[str, Any]:
    """The numbers of a size for the worker's record: major, pitch and basic minor diameter."""
    row = thread(size)
    if row is None:  # the model refuses it first
        msg = f"{size!r} is not in the thread table"
        raise ValueError(msg)
    return {
        "size": row.designation,
        "major_mm": row.major_mm,
        "pitch_mm": row.pitch_mm,
        "minor_mm": minor_diameter_mm(row.major_mm, row.pitch_mm),
    }
