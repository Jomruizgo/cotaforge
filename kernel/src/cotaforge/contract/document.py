"""Saving the native document: the optional ``save_document`` tool.

The live CAD document is the progress of a design: the harness writes it to a native file at its own
checkpoints so a closed session, an idle timeout or a restarted server no longer loses it. The contract stays
CAD-neutral:

* ``DocumentStamp`` is the identity the adapter writes INSIDE the file (FreeCAD: ``Document.Meta`` keys
  ``cotaforge.*``): which design, plan and revision it holds and which save it was. Flat strings, so every CAD that
  keeps document metadata can carry it.
* ``SaveDocumentResult.adapter_state`` is opaque to the harness: whatever the adapter needs to re-bind its own handles
  when the document is reopened (FreeCAD: the dump of the handle registry). It always names its ``schema`` so a
  reader can tell a format it does not know. The harness stores it next to the file, never inside it.

A failed save is RECOVERABLE (``internal`` with ``recoverable=True`` and ``details.reason`` -- ``io`` or ``conflict``;
``invalid_request`` for a bad path): the live document is untouched. No new ``ErrorCode`` is introduced: a code an
older harness does not know would make its traces unreadable.
"""

from typing import Any, ClassVar

from pydantic import BaseModel, Field, field_validator

from cotaforge.contract.requests import Request
from cotaforge.contract.responses import DocumentInfo

__all__ = [
    "STAMP_META_PREFIX",
    "STAMP_SCHEMA",
    "DesignAttributes",
    "DocumentStamp",
    "OpenDocumentRequest",
    "OpenDocumentResult",
    "RebindReport",
    "SaveDocumentRequest",
    "SaveDocumentResult",
    "SetDesignAttributesRequest",
]

STAMP_META_PREFIX = "cotaforge."
STAMP_SCHEMA = 1


class DocumentStamp(BaseModel, frozen=True):
    """The identity of a saved document, written inside the native file. ``save_seq`` grows by one per save of the
    plan (across sessions); ``last_step_index`` is the number of active steps of the trace when it was saved;
    ``plan_sha256``/``sidecar_sha256`` are the hashes of the stored plan document and of the companion record kept next
    to it, as they were on disk at that save (``None``: no companion record yet)."""

    _FIELDS: ClassVar[tuple[str, ...]] = (
        "design_id",
        "plan_id",
        "plan_revision",
        "save_seq",
        "last_step_index",
        "harness_version",
        "plan_sha256",
        "sidecar_sha256",
    )
    _INTS: ClassVar[frozenset[str]] = frozenset({"plan_revision", "save_seq", "last_step_index"})

    design_id: str = Field(min_length=1)
    plan_id: str = Field(min_length=1)
    plan_revision: int = Field(ge=1)
    save_seq: int = Field(ge=0)
    last_step_index: int = Field(ge=0)
    harness_version: str = Field(min_length=1)
    plan_sha256: str = Field(min_length=1)
    sidecar_sha256: str | None = None

    def to_meta(self) -> dict[str, str]:
        """The stamp as flat ``cotaforge.<field>`` string metadata (an absent hash is an empty string)."""
        meta = {f"{STAMP_META_PREFIX}schema": str(STAMP_SCHEMA)}
        for name in self._FIELDS:
            value = getattr(self, name)
            meta[f"{STAMP_META_PREFIX}{name}"] = "" if value is None else str(value)
        return meta

    @classmethod
    def from_meta(cls, meta: dict[str, str]) -> "DocumentStamp | None":
        """The stamp read back from document metadata; ``None`` when the document carries none. ``ValueError`` when
        it was written by a newer stamp schema (never guessed) or is malformed."""
        schema = meta.get(f"{STAMP_META_PREFIX}schema")
        if schema is None:
            return None
        if int(schema) > STAMP_SCHEMA:
            msg = f"document stamp schema {schema} is newer than this harness supports ({STAMP_SCHEMA})"
            raise ValueError(msg)
        fields: dict[str, Any] = {}
        for name in cls._FIELDS:
            raw = meta.get(f"{STAMP_META_PREFIX}{name}", "")
            if name in cls._INTS:
                fields[name] = int(raw)
            else:
                fields[name] = raw if raw or name != "sidecar_sha256" else None
        return cls.model_validate(fields)


class SaveDocumentRequest(Request):
    """Write the active document to ``path`` (absolute, same operating system as the adapter) with ``stamp`` inside.
    The write is atomic: a reader never sees a half-written file. ``expected_sha256`` is the hash of the file the
    harness wrote last: when the file on disk differs and the adapter cannot attribute the change to the engineer
    saving the same document by hand, it refuses (``details.reason=conflict``) rather than overwrite a file someone
    else wrote. ``overwrite=False`` refuses an existing file outright.

    ``recovery_copy`` writes a RECOVERY COPY: the file is a snapshot of the live document, which keeps its own file,
    name, stamp and state (nothing about the engineer's file changes), the file at ``path`` is replaced without
    questions and ``stamp``, ``overwrite`` and ``expected_sha256`` are not used."""

    path: str = Field(min_length=1)
    stamp: DocumentStamp
    overwrite: bool = True
    expected_sha256: str | None = None
    recovery_copy: bool = False


class SaveDocumentResult(BaseModel, frozen=True):
    """What was written: the final path, its sha256 and size, how long it took, the document's own unique id when
    the CAD has one, the CAD version, the opaque ``adapter_state`` (always with a ``schema`` key) and warnings that
    did not stop the save (e.g. the engineer saved the document by hand since the last checkpoint)."""

    path: str
    sha256: str
    size_bytes: int = Field(ge=0)
    duration_ms: float = Field(ge=0)
    document_uid: str | None = None
    cad_version: str | None = None
    adapter_state: dict[str, Any]
    warnings: tuple[str, ...] = ()

    @field_validator("adapter_state")
    @classmethod
    def _names_its_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _with_schema(value)


def _with_schema(value: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(value.get("schema"), str) or not value["schema"]:
        msg = "adapter_state must carry a non-empty 'schema' string"
        raise ValueError(msg)
    return value


# --- open_document --------------------------------------------------------------------------------


class OpenDocumentRequest(Request):
    """Open the native file at ``path`` (absolute, same operating system as the adapter) as the active document and
    re-bind the adapter's own handles to it from ``adapter_state`` (what ``save_document`` returned for that file), so
    every handle id the harness recorded (trace, stored records, executor context) names the same entity again. Nothing
    is rebuilt or replayed. The adapter never writes the file here.

    ``adapter_state=None`` ADOPTS the file instead: any native file of the CAD, stamped or not, written by this harness
    or by anyone else. There is nothing to re-bind, so the adapter mints new handles for what the file holds (one
    feature record per solid body, listed by ``feature_tree``/``bodies_of``) and the report carries notes only.

    Errors (no new ``ErrorCode``): ``invalid_request`` for a bad or missing path or an ``adapter_state`` whose schema
    the adapter does not know (``details.reason``: ``path``, ``missing`` or ``adapter_state``); ``internal`` with
    ``recoverable=True`` and ``details.reason=corrupt`` when the CAD cannot load the file.

    An ASSEMBLY names the part documents it uses by file (relative to its own). Those must already be open in the
    adapter (opened the same way, one after the other); when they are not, the open is refused with
    ``invalid_request``, ``details.reason=parts_missing`` and ``details.parts`` the missing files joined with ``|``,
    relative to the assembly's folder, so the caller can open them and ask again. A part that was saved again since
    the assembly was is what the assembly shows (the components are linked to the part documents).

    ``hidden=True`` loads the document WITHOUT a window (what a part needs to be linked from an assembly: the link wants
    the document loaded, not shown). It is still the active document and still answers every request; its window is
    made when ``set_active_document`` asks for it. An adapter whose CAD has no such thing opens it normally."""

    path: str = Field(min_length=1)
    adapter_state: dict[str, Any] | None = None
    hidden: bool = False

    @field_validator("adapter_state")
    @classmethod
    def _names_its_schema(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        return None if value is None else _with_schema(value)


class RebindReport(BaseModel, frozen=True):
    """How the recorded handles matched the reopened document. ``bodies_ok``: every recorded face, edge and vertex was
    found at its recorded element index with a matching signature and the body's cheap measure is unchanged;
    ``bodies_drifted``: re-bound by signature only, or measured differently -- the geometry is not what was saved;
    ``records_unmatched``: recorded entity ids that no longer resolve at all; ``objects_invalid``: CAD objects in an
    error state after loading; ``foreign_objects``: model objects in the file the registry does not know (added by hand
    after the save). ``clean`` only when nothing but ``bodies_ok`` is set (``notes`` are informative)."""

    bodies_ok: tuple[str, ...] = ()
    bodies_drifted: tuple[str, ...] = ()
    records_unmatched: tuple[str, ...] = ()
    objects_invalid: tuple[str, ...] = ()
    foreign_objects: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @property
    def clean(self) -> bool:
        return not (self.bodies_drifted or self.records_unmatched or self.objects_invalid or self.foreign_objects)


class OpenDocumentResult(BaseModel, frozen=True):
    """The reopened document, the stamp found inside the file (``None``: the file carries none), the sha256 and size
    of the file as it was read, the CAD's own document id and version, the re-bind report and warnings."""

    document: DocumentInfo
    stamp: DocumentStamp | None = None
    sha256: str
    size_bytes: int = Field(ge=0)
    document_uid: str | None = None
    cad_version: str | None = None
    rebind: RebindReport
    duration_ms: float = Field(default=0.0, ge=0)
    warnings: tuple[str, ...] = ()
    borrowed: bool = False
    """The file was already open in the CAD when it was asked for (the engineer opened it by hand): the document is his,
    so closing it here only forgets it and leaves it open."""
    windowless: bool = False
    """The document was loaded without a window (``OpenDocumentRequest.hidden``)."""


# --- design attributes ----------------------------------------------------------------------------


class DesignAttributes(BaseModel, frozen=True):
    """What the design declares about the part, kept INSIDE the native document so it survives saving and reopening:
    the ``material`` assigned to it and the process ``route`` (ids of declared processes, in order). ``material`` is
    ``None`` when none is assigned; when the document disagrees with itself (two bodies with different materials) it
    lists the distinct values joined by `` / `` so no check can pass on it."""

    material: str | None = None
    route: tuple[str, ...] = ()
    roles: dict[str, str] = Field(default_factory=dict)
    """Role of bodies and components by the name the caller gave them (the step that made them), kept in the file."""


class SetDesignAttributesRequest(Request):
    """Assign the material and/or the process route of the active document. ``None`` leaves that attribute as it is;
    ``""`` (material) or ``()`` (route) clears it. The adapter stores them in the document (FreeCAD: a dynamic property
    of every live body, and the document metadata) and does not judge them: whether they are declared is the caller's
    business."""

    material: str | None = None
    route: tuple[str, ...] | None = None
    roles: dict[str, str] | None = None
    """The whole mapping name -> role (replaces the stored one; ``{}`` clears it); ``None`` leaves it as it is."""
