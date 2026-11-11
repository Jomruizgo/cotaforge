"""Operations an adapter declares of its own (contract/extension.py).

The common vocabulary (``op`` names and their IR models) was born around one CAD. An adapter whose CAD does more
returns ``OperationSpec`` objects from ``Adapter.extra_operations``: each is one operation with its own pydantic model
of arguments, one line for ``cad_guide(topic="ops")``, a guide text and the adapter method that runs it. The server
offers it through the same ``op`` tool, so it is validated, undone, recorded in the plan, saved and resumed like a
common one.

The adapter method receives an ``ExtensionRequest`` and returns an ``OperationResult``. Every reference the model
declares (a face, an edge, a body, a sketch step, a feature step) arrives already resolved, as the JSON of the
handle it resolved to.
"""

import re
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from cotaforge.contract.enums import DocumentKind
from cotaforge.contract.requests import Request

__all__ = ["EXTENSION_PREFIX", "ExtensionRequest", "OperationSpec", "extension_op_name", "plain_op_name"]

EXTENSION_PREFIX = "ext:"
"""What an adapter's operation is called inside a plan (``ext:hole_wizard``): it can never be mistaken for a common
operation of the same name, so an adapter may replace ``hole`` with a richer one. The agent never writes it."""

_NAME = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def extension_op_name(name: str) -> str:
    """The name of the adapter's operation ``name`` inside a plan."""
    return f"{EXTENSION_PREFIX}{name}"


def plain_op_name(op: str) -> str:
    """The name the agent knows an operation by (``ext:hole`` is the adapter's ``hole``)."""
    return op[len(EXTENSION_PREFIX) :] if op.startswith(EXTENSION_PREFIX) else op


@dataclass(frozen=True)
class OperationSpec:
    """One operation an adapter adds to the vocabulary (or replaces a common one with, when the name is the same)."""

    name: str
    """What the agent writes in ``op(name=...)``: lowercase letters, digits and ``_``."""
    params: type[BaseModel]
    """The model of ``op(params=...)``. A field typed with an IR reference (``FaceRef``, ``EdgeRef``, ``EdgeSet``,
    ``BodyRef``, ``VertexRef``, ``PlaneRef``, ``AxisRef``, ``ComponentRef``) or a step id (``SketchStep``,
    ``FeatureStep``, from ``cotaforge.ir``) is resolved by the executor before the adapter is called. The model's
    docstring is its description and the validation errors are phrased from its fields."""
    purpose: str
    """One line for the ``ops`` topic of the guide."""
    guide: str
    """The text of the ``op:<name>`` topic that is not about the fields: how to use it, what to ask the engineer."""
    method: str
    """Name of the adapter method that runs it: ``method(request: ExtensionRequest) -> OperationResult``. It may return
    a ``DatumPlaneResult`` (or ``DatumAxisResult``) instead: the step then registers a plane (an axis) that ``sketch``
    and the other operations name with ``from_op``, at the frame the result reports. Declare ``makes_feature=False``."""
    example: dict[str, Any] | None = None
    """Minimal valid ``params``, shown in the guide and in the usage line of an error."""
    makes_feature: bool = True
    """False for a step that leaves no feature in the document (a sketch, a datum): the executor does not demand one."""
    kinds: tuple[DocumentKind, ...] = (DocumentKind.part,)
    """The kinds of document the operation may be used in."""
    read_only: bool = False
    """True for an operation that only reads (a list, a property, a status). The agent still calls it with ``op``, but
    it is not a step: no id, not in the plan, not saved, not undoable, and it does not mark the document changed. The
    adapter method answers with an ``OperationResult`` whose ``text`` carries what it read; it must not change the
    document."""
    undo_method: str | None = None
    """Name of an adapter method ``method(request: Empty) -> UndoResult`` that takes the operation back when it left no
    feature to revert (``makes_feature=False``, or a feature-less result): ``UndoResult.reverted`` is ``None``. Without
    it such a step is only booked as undone, the CAD is not asked (as before). Ignored for a step that did leave a
    feature (``undo_last`` reverts it)."""
    category: str = ""
    """The group the guide lists it under, in the words of the CAD ("sketches", "surfaces", "weldments"...); empty = the
    common vocabulary's group of its name, else "other"."""

    def __post_init__(self) -> None:
        if not _NAME.match(self.name):
            msg = f"operation name {self.name!r} must be lowercase letters, digits and _ (start with a letter)"
            raise ValueError(msg)
        if not self.method:
            msg = f"operation {self.name!r} names no adapter method"
            raise ValueError(msg)
        if not self.purpose.strip():
            msg = f"operation {self.name!r} has no one-line purpose"
            raise ValueError(msg)
        if self.read_only and self.undo_method:
            msg = f"operation {self.name!r} only reads: it leaves nothing to undo, so it names no undo_method"
            raise ValueError(msg)


class ExtensionRequest(Request):
    """What the adapter method of an ``OperationSpec`` receives: the operation's name and its arguments, validated, with
    every reference resolved to the JSON of its handle (``{"id": "face:3"}``; an edge set is a list of them)."""

    name: str = Field(min_length=1)
    params: dict[str, Any] = Field(default_factory=dict)
