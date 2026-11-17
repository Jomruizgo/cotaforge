"""The drawing rule catalog (``catalog.yaml``): every rule a drawing follows, written once, with where it applies, the
step of the work sequence it belongs to and the review checks that hold it.

Step 1 of the migration in the workshop's drawing architecture: the catalog only describes what the code does today.
Tests keep the two in step (``tests/unit/drawing/test_catalog.py``); later steps derive the guide, the requirement rows
and the per-step plan from it.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

CATALOG_PATH = Path(__file__).with_name("catalog.yaml")

Weight = Literal["blocks", "warns", "gate", "debt"]
Origin = Literal["standard", "owner", "harness"]
Source = Literal["prompt", "guide", "requirements", "checks", "memory_base", "owner"]


class Applies(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sheet_types: tuple[str, ...] = ()
    processes: tuple[str, ...] = ()
    features: tuple[str, ...] = ()


class Rule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    step: str
    text: str
    origin: Origin
    weight: Weight
    applies: Applies = Field(default_factory=Applies)
    standard: str = ""
    checks: tuple[str, ...] = ()
    requirement: str = ""
    sources: tuple[Source, ...] = ()
    gate: str = ""
    """A rule no review finding stands for but a tool refuses to break (a call that needs the engineer's order): what
    the tool does, in a few words. Such a rule weighs ``gate``."""

    @model_validator(mode="after")
    def _debt_has_no_check(self) -> Rule:
        if (self.weight == "gate") != bool(self.gate):
            raise ValueError(f"{self.id}: weight 'gate' exactly when the rule names its gate")
        if self.gate and self.checks:
            raise ValueError(f"{self.id}: a gate rule has no review check")
        if self.weight != "gate" and (self.weight == "debt") != (not self.checks):
            raise ValueError(f"{self.id}: weight 'debt' exactly when the rule has no check")
        return self


class Catalog(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int
    steps: tuple[str, ...]
    sheet_types: tuple[str, ...]
    processes: tuple[str, ...]
    features: tuple[str, ...]
    awaiting_merge: dict[str, int] = Field(default_factory=dict)
    rules: tuple[Rule, ...]

    @model_validator(mode="after")
    def _consistent(self) -> Catalog:
        ids = [r.id for r in self.rules]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"duplicate rule ids: {dup}")
        for r in self.rules:
            if r.step not in self.steps:
                raise ValueError(f"{r.id}: unknown step {r.step!r}")
            for value, known, what in (
                (r.applies.sheet_types, self.sheet_types, "sheet type"),
                (r.applies.processes, self.processes, "process"),
                (r.applies.features, self.features, "feature"),
            ):
                unknown = sorted(set(value) - set(known))
                if unknown:
                    raise ValueError(f"{r.id}: unknown {what} {unknown}")
        return self

    def debt(self) -> tuple[Rule, ...]:
        """The rules no check holds yet."""
        return tuple(r for r in self.rules if r.weight == "debt")

    def codes(self) -> set[str]:
        """Every review finding code some rule names."""
        return {c for r in self.rules for c in r.checks}


@cache
def load(path: Path = CATALOG_PATH) -> Catalog:
    return Catalog.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))
