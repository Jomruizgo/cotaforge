"""Feature listing, deletion and property editing on the CAD's own feature tree (contract/features.py).

These tools work on the features as the CAD holds them, whoever made them: the ones the plan built, the ones
``run_native`` made, and the ones of a file that was opened (whose history the harness adopted, without the IR that
made them). ``list_features`` reads them; ``remove_feature`` and ``set_feature_properties`` change them the CAD's
own way (a body's Tip moves back, dependants are recomputed) as ONE undoable step, measured and rolled back when the
result is not valid. The CAD's own property names are the vocabulary (``Radius``, ``Size``, ``Length``, ...).
"""

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from cotaforge.contract.descriptors import BodyKind
from cotaforge.contract.geometry import Vec3
from cotaforge.contract.handles import BodyHandle, FeatureHandle
from cotaforge.contract.requests import Request

MAX_LISTED_FEATURES = 400
"""Features one ``list_features`` answer carries; ``truncated`` says when there were more."""

__all__ = [
    "RemoveFeatureRequest",
    "FeatureEntry",
    "FeatureList",
    "FeatureBody",
    "FocusRequest",
    "FocusResult",
    "ListFeaturesRequest",
    "RefEntry",
    "ReferenceVisibilityRequest",
    "ReferenceVisibilityResult",
    "SetFeaturePropertiesRequest",
]


class ListFeaturesRequest(Request):
    """The features of the active document, body by body. ``feature`` (the CAD's name or label) asks for one in
    detail: all its editable properties and, for a dress-up, the geometry of the edges or faces it works on."""

    model_config = ConfigDict(extra="forbid")

    feature: str | None = None
    body: BodyHandle | None = None


class RefEntry(BaseModel, frozen=True):
    """An edge or face a feature is built on, by the CAD's name for it inside the feature's base shape."""

    name: str
    kind: str
    size: float
    """Length (mm) of an edge, area (mm2) of a face."""
    center: Vec3


class FeatureEntry(BaseModel, frozen=True):
    model_config = ConfigDict(extra="forbid")

    name: str
    label: str
    type_id: str
    body_name: str = ""
    body: BodyHandle | None = None
    is_tip: bool = False
    suppressed: bool = False
    visible: bool = True
    base: str | None = None
    """The feature this one is built on (the previous solid of its body)."""
    properties: dict[str, float | str | bool] = Field(default_factory=dict)
    ref_count: int | None = None
    """Edges or faces a dress-up works on (``None`` for a feature that works on none)."""
    ref_names: tuple[str, ...] = ()
    refs: tuple[RefEntry, ...] = ()
    """Geometry of the refs: only in the detail of one feature."""
    used_by: tuple[str, ...] = ()
    """Other objects that need this one (what deleting it would break)."""
    feature: FeatureHandle | None = None
    """The harness record that made it (a plan op or a run_native step); ``None`` for what the file already had."""
    feature_kind: str | None = None
    editable_properties: tuple[str, ...] = ()
    """Detail only: the properties ``set_feature_properties`` accepts, with their type."""


class FeatureBody(BaseModel, frozen=True):
    name: str
    label: str
    body: BodyHandle | None = None
    tip: str | None = None
    visible: bool = True
    kind: BodyKind = "solid"
    area_mm2: float | None = None
    """Area of a surface body."""


class FeatureList(BaseModel, frozen=True):
    bodies: tuple[FeatureBody, ...] = ()
    features: tuple[FeatureEntry, ...] = ()
    truncated: int = 0


class RemoveFeatureRequest(Request):
    """Delete one feature the CAD's way (a body's Tip moves back to the previous solid when it was the Tip, the
    features built on it are recomputed). ``feature`` is the CAD's name or label; ``step_feature`` the harness record
    of a step instead. Other objects that need it are named and the delete refused, unless ``cascade``: then they go
    too. A result that is not valid is rolled back."""

    model_config = ConfigDict(extra="forbid")

    feature: str | None = None
    step_feature: FeatureHandle | None = None
    cascade: bool = False
    intent: str = ""

    @model_validator(mode="after")
    def _one_target(self) -> "RemoveFeatureRequest":
        if (self.feature is None) == (self.step_feature is None):
            msg = "remove_feature needs exactly one of feature and step_feature"
            raise ValueError(msg)
        return self


class SetFeaturePropertiesRequest(Request):
    """Set properties of an existing feature by the CAD's property names. Values are validated against the property
    (type, range, choices); a dress-up's ``edges`` is a list of the CAD's edge names of its base shape or of edge ids.
    The model is recomputed and checked; a result that is not valid is rolled back."""

    model_config = ConfigDict(extra="forbid")

    feature: str = Field(min_length=1)
    properties: dict[str, Any] = Field(min_length=1)
    intent: str = ""


class FocusRequest(Request):
    """Show each body's Tip and hide its sketches and intermediate features (display only). No ``bodies``: all."""

    model_config = ConfigDict(extra="forbid")

    bodies: tuple[BodyHandle, ...] = ()


class FocusResult(BaseModel, frozen=True):
    shown: tuple[str, ...] = ()
    hidden: tuple[str, ...] = ()


class ReferenceVisibilityRequest(Request):
    """Show or hide reference geometry (a datum plane, axis or point, or a sketch) by the name it has in the CAD's
    tree. Display only: nothing is removed, suppressed or changed, and a hidden reference can still be selected and
    used by name."""

    model_config = ConfigDict(extra="forbid")

    names: tuple[str, ...] = Field(min_length=1)
    visible: bool


class ReferenceVisibilityResult(BaseModel, frozen=True):
    changed: tuple[str, ...] = ()
    """The references whose display changed."""
    already: tuple[str, ...] = ()
    """The ones that were already as asked."""
    unknown: tuple[str, ...] = ()
    """Names that are not a reference of the active document."""
