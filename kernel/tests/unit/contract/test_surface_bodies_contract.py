"""A body can be a surface (contract): the default is still a solid, and a solid serialises as it always did."""

import hashlib
from typing import Any

import pytest

from cotaforge.contract import (
    BodyHandle,
    BodyInfo,
    BoundingBox,
    Empty,
    FeatureBody,
    FeatureHandle,
    FeatureRequest,
    Provenance,
)
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.ir import parse_plan
from cotaforge.verification.verifier import Verifier
from tests.unit.executor.test_with_fake import _fake

# SHA-256 of the executed plan's JSON, its final mass and the bodies of every feature, measured BEFORE BodyInfo and
# FeatureBody grew ``kind`` and ``area_mm2`` (defaults left out, as a solid has always been written).
SOLID_FINGERPRINT = "759246638f4311fd1e0269d8f753f6cc40bfe409addd569f43c79d3b7c5e86f2"


def _executed_fingerprint() -> str:
    plan = parse_plan(
        {
            "id": "bracket",
            "kind": "part",
            "operations": [
                {"id": "block", "op": "box", "intent": "block", "size": [40, 30, 10]},
                {"id": "boss", "op": "cylinder", "intent": "boss", "diameter_mm": 10, "height_mm": 8},
            ],
        }
    )
    adapter = _fake()
    ex = Executor(plan, adapter, Verifier(), ExecutorConfig(), {})
    ex.start()
    trace = ex.run_to_end()
    assert trace.final_mass is not None
    # the CAD-measured mass is not part of the shape the fingerprint pins
    mass = trace.final_mass.model_dump_json(exclude={"mass_kg", "mass_source"})
    parts = [plan.model_dump_json(), mass]
    for node in adapter.feature_tree(Empty(operation_id="tree")).nodes:
        request = FeatureRequest(operation_id="bodies", feature=node.handle)
        parts.extend(b.model_dump_json(exclude_defaults=True) for b in adapter.bodies_of(request).bodies)
    return hashlib.sha256("\n".join(parts).encode()).hexdigest()


def test_a_solid_serialises_as_before_the_surface_fields_existed() -> None:
    assert _executed_fingerprint() == SOLID_FINGERPRINT


def _info(**extra: Any) -> BodyInfo:
    return BodyInfo(
        handle=BodyHandle(id="body:1"),
        volume_mm3=extra.pop("volume_mm3", 100.0),
        bbox=BoundingBox(min=(0, 0, 0), max=(1, 1, 1)),
        face_count=6,
        provenance=Provenance(created_by=FeatureHandle(id="f:1")),
        **extra,
    )


def test_a_body_is_a_solid_unless_the_adapter_says_otherwise() -> None:
    info = _info()
    assert info.kind == "solid"
    assert info.area_mm2 is None
    assert FeatureBody(name="Body", label="Body").kind == "solid"


def test_a_surface_body_has_no_volume_and_states_its_area() -> None:
    info = _info(kind="surface", volume_mm3=None, area_mm2=250.0)
    assert info.volume_mm3 is None and info.area_mm2 == 250.0
    again = BodyInfo.model_validate_json(info.model_dump_json())
    assert again == info


def test_a_body_kind_other_than_solid_or_surface_is_refused() -> None:
    with pytest.raises(ValueError):
        _info(kind="wire")
