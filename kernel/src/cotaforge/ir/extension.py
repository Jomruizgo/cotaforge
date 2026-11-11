"""The step of an operation the adapter declared (ir/extension.py).

The closed vocabulary of ``ops_features.Operation`` has one model per common operation. An operation an adapter adds
(``contract.OperationSpec``) cannot have one, because the kernel does not know it: it is stored as an
``ExtensionOperation``, its name (``ext:<name>``) and its arguments as plain JSON. The adapter's own model validates
the arguments when the agent calls the operation and again when the executor builds the request, so the plan stays
parseable (saved, reopened, extended) by a kernel that knows nothing about the adapter.

``SketchStep`` and ``FeatureStep`` are the two argument types an adapter model uses for "a step of this plan"; the
other references (faces, edges, bodies...) are the ones of ``ir.refs``.
"""

from typing import Annotated, Any

from pydantic import BaseModel, Field, RootModel, TypeAdapter, ValidationError

from cotaforge.contract import EXTENSION_PREFIX
from cotaforge.ir.ops_primitives import OperationBase
from cotaforge.ir.refs import (
    REF_TYPES,
    AxisRef,
    BodyRef,
    ComponentRef,
    EdgeRef,
    EdgeSet,
    FaceRef,
    OpId,
    PlaneRef,
    Ref,
    VertexRef,
)

__all__ = ["ExtensionOperation", "FeatureStep", "SketchStep", "is_extension_op"]


class SketchStep(RootModel[OpId]):
    """In an adapter's model: the id of an earlier step that made a sketch. The adapter receives its handle."""

    model_config = {"frozen": True}


class FeatureStep(RootModel[OpId]):
    """In an adapter's model: the id of an earlier step that made a feature. The adapter receives its handle."""

    model_config = {"frozen": True}


_REF_UNION = Annotated[
    FaceRef | EdgeRef | EdgeSet | VertexRef | PlaneRef | AxisRef | BodyRef | ComponentRef, Field(discriminator="kind")
]
_REF_ADAPTER: TypeAdapter = TypeAdapter(_REF_UNION)
_REF_KINDS = frozenset({"face", "edge", "edges", "vertex", "plane", "axis_ref", "body", "component"})


def is_extension_op(name: object) -> bool:
    return isinstance(name, str) and name.startswith(EXTENSION_PREFIX)


class ExtensionOperation(OperationBase):
    """An operation of the adapter: ``op`` is ``ext:<name>``, ``params`` its validated arguments as JSON."""

    op: str = Field(pattern=rf"^{EXTENSION_PREFIX}[a-z][a-z0-9_]{{0,63}}$")
    params: dict[str, Any] = Field(default_factory=dict)

    def refs(self) -> tuple[Ref, ...]:
        """The references written in ``params``: every dict that has the shape of one (it names a ``kind`` of
        ``ir.refs`` and parses as it), so the plan rules and the verifier see the faces and bodies an adapter
        operation names as they see those of a common one. What is not a reference is left to the adapter's model."""
        collected: list[Ref] = []

        def walk_typed(value: object) -> None:
            if isinstance(value, REF_TYPES):
                collected.append(value)  # type: ignore[arg-type]
                for name in type(value).model_fields:
                    walk_typed(getattr(value, name))
            elif isinstance(value, BaseModel):
                for name in type(value).model_fields:
                    walk_typed(getattr(value, name))
            elif isinstance(value, tuple | list):
                for item in value:
                    walk_typed(item)

        def walk_raw(node: object) -> None:
            if isinstance(node, dict):
                if node.get("kind") in _REF_KINDS:
                    try:
                        ref = _REF_ADAPTER.validate_python(node)
                    except ValidationError:
                        ref = None
                    if ref is not None:
                        walk_typed(ref)
                        return
                for item in node.values():
                    walk_raw(item)
            elif isinstance(node, list | tuple):
                for item in node:
                    walk_raw(item)

        walk_raw(self.params)
        return tuple(collected)
