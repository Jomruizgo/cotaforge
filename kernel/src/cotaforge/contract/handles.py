"""Opaque adapter-assigned handles (contract/handles.py)."""

from pydantic import BaseModel


class Handle(BaseModel, frozen=True):
    id: str


class DocumentHandle(Handle):
    pass


class FeatureHandle(Handle):
    pass


class SketchHandle(Handle):
    pass


class BodyHandle(Handle):
    pass


class FaceHandle(Handle):
    pass


class EdgeHandle(Handle):
    pass


class VertexHandle(Handle):
    pass


class ComponentHandle(Handle):
    pass


class MateHandle(Handle):
    pass


class JointHandle(Handle):
    """A joint between two components (id prefix ``joint:``)."""


class DatumPlaneHandle(Handle):
    """A datum plane made by ``datum_plane`` (id prefix ``datum_plane:``). Not an ``AnyHandle`` member: it
    names a pose the executor computed, not a piece of the model's topology."""


class DatumAxisHandle(Handle):
    """A datum axis made by ``datum_axis`` (id prefix ``datum_axis:``)."""


AnyHandle = FaceHandle | EdgeHandle | VertexHandle | BodyHandle | FeatureHandle | ComponentHandle
