"""Memory entry model: a Markdown file with YAML frontmatter.

An entry is frozen and rejects unknown fields (an unknown field is a load error, as in the rest of the
kernel). Only ``process`` and ``material`` entries carry structured data, because they are the only ones
checked by code; the other types are free text. Every entry states when it applies and when it does not.
Numbers are facts with provenance: an entry that carries any numeric value must carry ``provenance``.
"""

import datetime as dt
import math
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator, model_validator

from cotaforge.memory.capabilities import PROCESS_CAPABILITIES, CapabilityShape, CapabilitySpec, known_process_tag
from cotaforge.memory.legacy_ids import LEGACY_IDS

ID_PATTERN = r"^[a-z0-9][a-z0-9-]{0,63}$"
EntryId = Annotated[str, StringConstraints(pattern=ID_PATTERN)]
NonEmptyText = Annotated[str, StringConstraints(min_length=1)]

FRONTMATTER_DELIMITER = "---"


class EntryError(ValueError):
    """An entry file cannot be parsed or fails validation (the message is meant to be shown to the user)."""


class EntryType(StrEnum):
    practice = "practice"
    principle = "principle"
    process = "process"
    material = "material"
    correction = "correction"
    cad_trap = "cad_trap"
    case = "case"  # a real design: its files, what was measured and what it teaches


class EntryStatus(StrEnum):
    active = "active"
    inactive = "inactive"  # exists, but neither restricts nor is suggested
    proposed = "proposed"  # saved by the agent, not yet approved by the engineer: pending, never a rule


class ProvenanceSource(StrEnum):
    shop_measurement = "shop_measurement"
    supplier_datasheet = "supplier_datasheet"
    standard = "standard"
    user_statement = "user_statement"
    correction = "correction"
    reference_book = "reference_book"  # a textbook or handbook
    manufacturer_guide = "manufacturer_guide"  # a design guide published by a manufacturer or supplier


class Scope(StrEnum):
    project = "project"
    user = "user"
    base = "base"  # shipped with the product: read-only; the engineer overrides or deactivates it by id


class Stage(StrEnum):
    concept = "concept"
    layout = "layout"
    detail = "detail"


# The closed vocabulary of ``when.ops``: the operations of the server, plus actions that are not plan operations.
OPS_VOCABULARY: frozenset[str] = frozenset(
    {
        *("add_joint", "base_flange", "boolean_intersect", "boolean_subtract", "boolean_union", "box", "chamfer"),
        *("check_interference", "circular_pattern", "copy_body", "cut", "cylinder", "draft", "edge_flange"),
        *("extrude", "fillet", "fix_component", "hole", "insert_component", "linear_pattern", "mate_coincident"),
        *("mate_concentric", "mate_distance", "mirror", "mirror_body", "place_component", "ref_axis", "ref_plane"),
        *("revolve", "rotate", "set_joint", "shell", "shrink_body", "sketch", "split", "sweep", "translate", "unfold"),
        # not operations of the plan
        *("delete_feature", "edit_feature", "native", "drawing_create", "drawing_dimension", "drawing_export"),
        *("mates", "joints"),
    }
)
MATE_OPS = frozenset({"mate_coincident", "mate_concentric", "mate_distance"})
JOINT_OPS = frozenset({"add_joint", "set_joint"})
ROLE_PATTERN = r"^[a-z][a-z0-9_-]{0,31}$"
RoleName = Annotated[str, StringConstraints(pattern=ROLE_PATTERN)]
CadKey = Annotated[str, StringConstraints(pattern=ROLE_PATTERN)]
"""The key of a CAD software (``Adapter.cad_key``): lowercase letters, digits, ``_`` and ``-``."""


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class Provenance(_Frozen):
    """Where a number comes from: never an estimate of the model."""

    source: ProvenanceSource
    reference: NonEmptyText
    by: NonEmptyText
    date: dt.date


class Capability(_Frozen):
    """One named capability of a process. Exactly one of ``value`` / ``value_set`` / ``value_names``.

    ``placeholder`` marks a value that is not measured yet: it is reported as pending and never blocks.
    """

    name: NonEmptyText
    value: int | float | None = None
    value_set: tuple[int | float, ...] | None = None
    value_names: tuple[NonEmptyText, ...] | None = None
    unit: NonEmptyText | None = None
    placeholder: bool = False

    @field_validator("value", mode="before")
    @classmethod
    def _value_is_a_number(cls, value: object) -> object:
        if isinstance(value, bool):
            raise ValueError("value must be a number, not a boolean")
        return value

    @field_validator("value_set", mode="before")
    @classmethod
    def _value_set_has_numbers(cls, value: object) -> object:
        if isinstance(value, list | tuple) and any(isinstance(item, bool) for item in value):
            raise ValueError("value_set must contain numbers, not booleans")
        return value

    @model_validator(mode="after")
    def _single_shape(self) -> "Capability":
        given = [
            label
            for label, field in (
                ("value", self.value),
                ("value_set", self.value_set),
                ("value_names", self.value_names),
            )
            if field is not None
        ]
        if len(given) != 1:
            raise ValueError(
                f"capability '{self.name}' needs exactly one of value / value_set / value_names (got {given or 'none'})"
            )
        numbers = self.numbers
        if not all(math.isfinite(number) for number in numbers):
            raise ValueError(f"capability '{self.name}' has a non-finite number")
        if self.value_set is not None and not self.value_set:
            raise ValueError(f"capability '{self.name}' has an empty value_set")
        if self.value_names is not None and not self.value_names:
            raise ValueError(f"capability '{self.name}' has an empty value_names")
        return self

    @property
    def shape(self) -> CapabilityShape:
        if self.value_set is not None:
            return CapabilityShape.number_set
        if self.value_names is not None:
            return CapabilityShape.names
        return CapabilityShape.scalar

    @property
    def numbers(self) -> tuple[float, ...]:
        """Every number the capability carries (empty for a ``value_names`` capability)."""
        if self.value is not None:
            return (float(self.value),)
        if self.value_set is not None:
            return tuple(float(number) for number in self.value_set)
        return ()

    def check_against(self, spec: CapabilitySpec) -> None:
        """Raise ``ValueError`` when this capability does not match the spec of a known process tag."""
        if self.shape is not spec.shape:
            raise ValueError(f"capability '{self.name}' must be a {spec.shape.value}, got a {self.shape.value}")
        if spec.unit is not None and self.unit is not None and self.unit != spec.unit:
            raise ValueError(f"capability '{self.name}' is in '{spec.unit}', got unit '{self.unit}'")
        if spec.set_size is not None and len(self.numbers) != spec.set_size:
            raise ValueError(f"capability '{self.name}' needs {spec.set_size} numbers, got {len(self.numbers)}")
        if self.placeholder:
            return  # a placeholder is not a measurement: its number is not range-checked
        for number in self.numbers:
            if spec.minimum is not None and number <= spec.minimum:
                raise ValueError(f"capability '{self.name}' must be greater than {spec.minimum:g}, got {number:g}")
            if spec.maximum is not None and number > spec.maximum:
                raise ValueError(f"capability '{self.name}' must be at most {spec.maximum:g}, got {number:g}")


class ProcessBlock(_Frozen):
    """The structured part of a ``process`` entry.

    A known ``tag`` (see ``PROCESS_CAPABILITIES``) restricts the capability names and shapes; a free tag
    accepts any capability and gets no automatic checks.
    """

    tag: NonEmptyText
    capabilities: tuple[Capability, ...] = ()

    @model_validator(mode="after")
    def _capabilities_match_the_tag(self) -> "ProcessBlock":
        names = [capability.name for capability in self.capabilities]
        duplicated = sorted({name for name in names if names.count(name) > 1})
        if duplicated:
            raise ValueError(f"duplicate capabilities: {duplicated}")
        known = known_process_tag(self.tag)
        if known is None:
            return self
        specs = PROCESS_CAPABILITIES[known]
        for capability in self.capabilities:
            spec = specs.get(capability.name)
            if spec is None:
                raise ValueError(
                    f"'{capability.name}' is not a capability of the known process '{known}' (allowed: {sorted(specs)})"
                )
            capability.check_against(spec)
        return self

    @property
    def known_tag(self) -> str | None:
        return known_process_tag(self.tag)


PropertyScalar = str | int | float | bool
PropertyValue = PropertyScalar | list[PropertyScalar]


class MaterialBlock(_Frozen):
    """The structured part of a ``material`` entry; only ``name`` is required."""

    name: NonEmptyText
    designations: tuple[NonEmptyText, ...] = ()
    properties: dict[NonEmptyText, PropertyValue] = Field(default_factory=dict)

    @property
    def numbers(self) -> tuple[float, ...]:
        found: list[float] = []
        for value in self.properties.values():
            for item in value if isinstance(value, list) else [value]:
                if isinstance(item, int | float) and not isinstance(item, bool):
                    found.append(float(item))
        return tuple(found)


class Example(_Frozen):
    """How the entry looks in one sector. ``validated`` is true only when the engineer confirmed it."""

    sector: NonEmptyText
    text: NonEmptyText
    validated: bool = False


class When(_Frozen):
    """The triggers of an entry. Within a field any value matches; all given fields must match: ``roles`` and ``ops``
    against what the op touched (``stages`` against the current stage)."""

    roles: tuple[RoleName, ...] = ()
    stages: tuple[Stage, ...] = ()
    ops: tuple[NonEmptyText, ...] = ()

    @field_validator("ops")
    @classmethod
    def _closed_vocabulary(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = sorted(set(value) - OPS_VOCABULARY)
        if unknown:
            raise ValueError(f"unknown ops {unknown}; the vocabulary is {sorted(OPS_VOCABULARY)}")
        return value

    @property
    def is_empty(self) -> bool:
        return not (self.roles or self.stages or self.ops)


class CaseBlock(_Frozen):
    """The structured part of a ``case`` entry: a real design. ``facts`` are lines such as 'mass 4.2 kg (weighed)'."""

    paths: tuple[NonEmptyText, ...] = ()
    facts: tuple[NonEmptyText, ...] = ()
    teaches: NonEmptyText


class MemoryEntry(_Frozen):
    id: EntryId
    type: EntryType
    title: NonEmptyText
    applies_when: NonEmptyText
    not_when: NonEmptyText
    tags: tuple[NonEmptyText, ...] = ()
    status: EntryStatus = EntryStatus.active
    provenance: Provenance | None = None
    process: ProcessBlock | None = None
    material: MaterialBlock | None = None
    recognize: NonEmptyText | None = None
    """The situation as seen in the model (not a category): what the agent can look at to know the entry applies."""
    examples: tuple[Example, ...] = ()
    typical_error: NonEmptyText | None = None
    cases: tuple[EntryId, ...] = ()
    related: tuple[EntryId, ...] = ()
    """Ids of the entries this one is linked with (a principle and its practices); the link reads both ways."""
    replaces: EntryId | None = None
    """The id of a base entry this one replaces: the base entry is then neither pushed nor required."""
    sources: tuple[NonEmptyText, ...] = ()
    """Where a principle or a practice comes from (a standard, a book, a manufacturer guide): free text."""
    when: When | None = None
    sector: NonEmptyText | None = None
    case: CaseBlock | None = None
    cad: CadKey | None = None
    """The CAD software the entry holds for (``Adapter.cad_key``); absent = every one. An entry of another software is
    neither listed nor pushed to this one."""
    body: str = ""

    @field_validator("body")
    @classmethod
    def _strip_body(cls, value: str) -> str:
        return value.strip()

    @field_validator("related", "cases", mode="before")
    @classmethod
    def _old_ids_in_lists(cls, value: Any) -> Any:
        """A link to a base entry by the Spanish id it had before 0.10 still reaches that entry."""
        if isinstance(value, list | tuple):
            return tuple(LEGACY_IDS.get(item, item) if isinstance(item, str) else item for item in value)
        return value

    @field_validator("replaces", mode="before")
    @classmethod
    def _old_id_replaced(cls, value: Any) -> Any:
        return LEGACY_IDS.get(value, value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def _blocks_match_the_type(self) -> "MemoryEntry":
        if self.type is EntryType.process and self.process is None:
            raise ValueError("a process entry needs a 'process' block")
        if self.type is not EntryType.process and self.process is not None:
            raise ValueError(f"the 'process' block is only for type 'process' (got '{self.type.value}')")
        if self.type is EntryType.material and self.material is None:
            raise ValueError("a material entry needs a 'material' block")
        if self.type is not EntryType.material and self.material is not None:
            raise ValueError(f"the 'material' block is only for type 'material' (got '{self.type.value}')")
        if self.type is EntryType.case and self.case is None:
            raise ValueError("a case entry needs a 'case' block")
        if self.type is not EntryType.case and self.case is not None:
            raise ValueError(f"the 'case' block is only for type 'case' (got '{self.type.value}')")
        if self.sources and self.type not in (EntryType.principle, EntryType.practice):
            raise ValueError(f"'sources' is only for principles and practices, not '{self.type.value}': use provenance")
        if self.id in self.related:
            raise ValueError("'related' cannot list the entry itself")
        if self.replaces == self.id:
            raise ValueError("'replaces' cannot be the entry itself (the same id already overrides a base entry)")
        return self

    @model_validator(mode="after")
    def _numbers_need_provenance(self) -> "MemoryEntry":
        if self.has_numeric_values and self.provenance is None:
            raise ValueError("the entry carries numeric values: 'provenance' is required (a number is a fact)")
        return self

    @property
    def has_numeric_values(self) -> bool:
        """True when the structured part carries any number (placeholders count: they are still numbers)."""
        if self.process is not None and any(capability.numbers for capability in self.process.capabilities):
            return True
        return self.material is not None and bool(self.material.numbers)

    @property
    def is_active(self) -> bool:
        return self.status is EntryStatus.active

    @property
    def is_proposed(self) -> bool:
        return self.status is EntryStatus.proposed


class StoredEntry(BaseModel):
    """An entry as the store sees it: which scope it came from and which file."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    entry: MemoryEntry
    scope: Scope
    path: Path


def _describe(error: ValidationError) -> str:
    parts: list[str] = []
    for item in error.errors():
        where = ".".join(str(part) for part in item["loc"])
        message = str(item["msg"]).removeprefix("Value error, ")
        parts.append(f"{where}: {message}" if where else message)
    return "; ".join(parts)


def _split_frontmatter(text: str) -> tuple[str, str]:
    lines = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    if not lines or lines[0].strip() != FRONTMATTER_DELIMITER:
        raise EntryError("the file must start with a '---' frontmatter line")
    for index in range(1, len(lines)):
        if lines[index].strip() == FRONTMATTER_DELIMITER:
            return "\n".join(lines[1:index]), "\n".join(lines[index + 1 :])
    raise EntryError("the frontmatter is not closed with a '---' line")


def parse_entry(text: str, *, expected_id: str | None = None) -> MemoryEntry:
    """Parse and validate an entry file. ``expected_id`` is the file stem: the ``id`` must equal it.

    Raises ``EntryError`` for anything wrong (malformed YAML, unknown field, rule violations).
    """
    raw_frontmatter, body = _split_frontmatter(text)
    try:
        data: Any = yaml.safe_load(raw_frontmatter)
    except yaml.YAMLError as error:
        raise EntryError(f"invalid YAML in the frontmatter: {error}") from error
    if not isinstance(data, dict):
        raise EntryError("the frontmatter must be a YAML mapping")
    if "body" in data:
        raise EntryError("body: the text goes after the frontmatter, not inside it")
    try:
        entry = MemoryEntry.model_validate({**data, "body": body})
    except ValidationError as error:
        raise EntryError(_describe(error)) from error
    if expected_id is not None and entry.id != expected_id:
        raise EntryError(f"id '{entry.id}' must equal the file name '{expected_id}'")
    return entry


def render_entry(entry: MemoryEntry) -> str:
    """The file text of an entry (frontmatter + body); ``parse_entry(render_entry(e)) == e``."""
    data = entry.model_dump(mode="json", exclude_none=True, exclude={"body"})
    for key in ("examples", "cases", "related", "sources"):  # the v2 fields stay out of the file while empty
        if not data.get(key):
            data.pop(key, None)
    if "when" in data:
        data["when"] = {key: value for key, value in data["when"].items() if value}
        if not data["when"]:
            del data["when"]
    frontmatter = yaml.safe_dump(data, sort_keys=False, allow_unicode=True, default_flow_style=False)
    text = f"{FRONTMATTER_DELIMITER}\n{frontmatter}{FRONTMATTER_DELIMITER}\n"
    if entry.body:
        text += f"{entry.body}\n"
    return text
