"""Tests for cotaforge.ir.loader: load_plan from YAML/JSON text, paths, or dict."""

import json
from pathlib import Path

import pytest

from cotaforge.ir.errors import PlanError
from cotaforge.ir.loader import load_plan
from cotaforge.ir.plan import Plan

_PLAN_DICT = {
    "id": "plate",
    "kind": "part",
    "operations": [
        {
            "id": "s1",
            "op": "sketch",
            "intent": "base sketch",
            "on": {"kind": "plane", "name": "XY"},
            "entities": [{"kind": "rect", "name": "r1", "center": [0.0, 0.0], "width": 10.0, "height": 10.0}],
        },
        {"id": "e1", "op": "extrude", "intent": "extrude base", "sketch": "s1", "depth_mm": 5.0},
    ],
}

_YAML_TEXT = """
id: plate
kind: part
operations:
  - id: s1
    op: sketch
    intent: base sketch
    on:
      kind: plane
      name: XY
    entities:
      - kind: rect
        name: r1
        center: [0.0, 0.0]
        width: 10.0
        height: 10.0
  - id: e1
    op: extrude
    intent: extrude base
    sketch: s1
    depth_mm: 5.0
"""


def test_load_plan_from_dict() -> None:
    plan = load_plan(_PLAN_DICT)
    assert isinstance(plan, Plan)
    assert plan.id == "plate"


def test_load_plan_from_yaml_text() -> None:
    plan = load_plan(_YAML_TEXT)
    assert isinstance(plan, Plan)
    assert len(plan.operations) == 2


def test_load_plan_from_json_text() -> None:
    plan = load_plan(json.dumps(_PLAN_DICT))
    assert isinstance(plan, Plan)


def test_load_plan_from_yaml_path(tmp_path: Path) -> None:
    path = tmp_path / "plate.yaml"
    path.write_text(_YAML_TEXT, encoding="utf-8")
    plan = load_plan(path)
    assert isinstance(plan, Plan)
    assert plan.id == "plate"


def test_load_plan_from_json_path(tmp_path: Path) -> None:
    path = tmp_path / "plate.json"
    path.write_text(json.dumps(_PLAN_DICT), encoding="utf-8")
    plan = load_plan(path)
    assert isinstance(plan, Plan)


def test_load_plan_from_path_as_str(tmp_path: Path) -> None:
    path = tmp_path / "plate.yaml"
    path.write_text(_YAML_TEXT, encoding="utf-8")
    plan = load_plan(str(path))
    assert isinstance(plan, Plan)


def test_load_plan_still_raises_plan_error_on_invalid_content() -> None:
    with pytest.raises(PlanError):
        load_plan({"id": "plate", "kind": "part"})  # missing "operations"


def test_load_plan_yaml_on_key_is_not_swallowed_as_boolean() -> None:
    # Regression: PyYAML 1.1 resolves bare `on`/`off`/`yes`/`no` scalars as
    # booleans, which would turn the `sketch.on` key into `True`.
    plan = load_plan(_YAML_TEXT)
    sketch = plan.operations[0]
    assert sketch.on.name == "XY"


def test_load_plan_refuses_a_declared_validated_stamp() -> None:
    # load_plan is the user/agent-input door: parse_plan(..., allow_stamp=False) by default,
    # so a document that declares its own "validated" stamp is rejected, not trusted.
    data = dict(_PLAN_DICT)
    data["validated"] = {"adapter": "fake", "at": "2026-01-01T00:00:00Z", "trace": "trace-1"}
    with pytest.raises(PlanError) as exc_info:
        load_plan(data)
    assert exc_info.value.rule == "V9"


def test_load_plan_yaml_true_false_are_still_booleans() -> None:
    text = _YAML_TEXT.replace("depth_mm: 5.0", "depth_mm: 5.0\n    merge: true\n    both_directions: false")
    plan = load_plan(text)
    extrude = plan.operations[1]
    assert extrude.merge is True
    assert extrude.both_directions is False
