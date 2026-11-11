"""Tests for cotaforge.ir.template: TemplateParam, PlanTemplate, parse_template and instantiate."""

import pytest

from cotaforge.ir.errors import TemplateError
from cotaforge.ir.plan import parse_plan
from cotaforge.ir.template import PlanTemplate, field_value, instantiate, parse_template

_STAMP = {"adapter": "fake", "at": "2026-01-01T00:00:00Z", "trace": "trace-1"}


def _plan_data(width: float = 10.0, height: float = 6.0, depth: float = 5.0) -> dict:
    return {
        "id": "plate",
        "kind": "part",
        "operations": [
            {
                "id": "s1",
                "op": "sketch",
                "intent": "base sketch",
                "on": {"kind": "plane", "name": "XY"},
                "entities": [{"kind": "rect", "name": "plate", "center": [0.0, 0.0], "width": width, "height": height}],
            },
            {"id": "e1", "op": "extrude", "intent": "extrude base", "sketch": "s1", "depth_mm": depth},
        ],
    }


def _validated_plan(**kwargs: float) -> dict:
    """A plan dict that already carries a ``validated`` stamp (as it would look on disk)."""
    plan = _plan_data(**kwargs)
    plan["validated"] = _STAMP
    return plan


def _template_data(**param_overrides: dict) -> dict:
    params = [
        {
            "name": "width_mm",
            "op_id": "s1",
            "field": "entities.plate.width",
            "default": 10.0,
            "minimum": 5.0,
            "maximum": 50.0,
        },
        {
            "name": "height_mm",
            "op_id": "s1",
            "field": "entities.plate.height",
            "default": 6.0,
        },
        {
            "name": "depth_mm",
            "op_id": "e1",
            "field": "depth_mm",
            "default": 5.0,
            "minimum": 1.0,
            "maximum": 20.0,
        },
    ]
    data = {
        "template_id": "plate-template",
        "title": "Plate",
        "params": params,
        "plan": _validated_plan(),
        "source_plan_id": "plate",
        "validated": _STAMP,
        "created_by": "jorge",
        "created_at": "2026-01-01T00:00:00Z",
    }
    data.update(param_overrides)
    return data


def _template() -> PlanTemplate:
    return parse_template(_template_data())


# --- parse_template: happy path and PlanTemplate's own validators ----------------


def test_parse_template_returns_plan_template() -> None:
    template = _template()
    assert template.template_id == "plate-template"
    assert len(template.params) == 3
    assert template.plan.id == "plate"


def test_source_plan_id_must_match_embedded_plan_id() -> None:
    data = _template_data()
    data["source_plan_id"] = "not-plate"
    with pytest.raises(TemplateError):
        parse_template(data)


def test_validated_must_match_plan_validated() -> None:
    data = _template_data()
    data["validated"] = {"adapter": "freecad", "at": "2026-01-01T00:00:00Z", "trace": "other-trace"}
    with pytest.raises(TemplateError):
        parse_template(data)


def test_duplicate_param_names_rejected() -> None:
    data = _template_data()
    data["params"][1]["name"] = "width_mm"  # collides with params[0]
    with pytest.raises(TemplateError):
        parse_template(data)


def test_unknown_op_id_rejected() -> None:
    data = _template_data()
    data["params"][0]["op_id"] = "does_not_exist"
    with pytest.raises(TemplateError):
        parse_template(data)


def test_nonexistent_field_rejected() -> None:
    data = _template_data()
    data["params"][0]["field"] = "entities.plate.does_not_exist"
    with pytest.raises(TemplateError):
        parse_template(data)


def test_non_numeric_field_rejected() -> None:
    data = _template_data()
    data["params"][0]["field"] = "entities.plate.name"  # a string field, not numeric
    with pytest.raises(TemplateError):
        parse_template(data)


def test_duplicate_op_id_field_pair_rejected() -> None:
    data = _template_data()
    data["params"][1]["op_id"] = "s1"
    data["params"][1]["field"] = "entities.plate.width"  # same (op_id, field) as params[0]
    with pytest.raises(TemplateError):
        parse_template(data)


def test_default_outside_bounds_rejected() -> None:
    data = _template_data()
    data["params"][0]["default"] = 100.0  # above maximum=50.0
    with pytest.raises(TemplateError):
        parse_template(data)


# --- instantiate ------------------------------------------------------------------


def test_instantiate_with_all_values_substitutes_every_param() -> None:
    template = _template()
    plan = instantiate(template, {"width_mm": 20.0, "height_mm": 8.0, "depth_mm": 9.0}, plan_id="plate_v2")
    assert plan.id == "plate_v2"
    sketch = plan.operations[0]
    entity = sketch.entities[0]
    assert entity.width == 20.0
    assert entity.height == 8.0
    assert plan.operations[1].depth_mm == 9.0


def test_instantiate_without_values_uses_every_default_and_matches_origin() -> None:
    template = _template()
    plan = instantiate(template, {}, plan_id="plate_v2")
    expected = template.plan.model_copy(update={"id": "plate_v2", "validated": None})
    assert plan == expected


def test_instantiate_result_is_never_validated() -> None:
    template = _template()
    plan = instantiate(template, {"width_mm": 12.0}, plan_id="plate_v2")
    assert plan.validated is None


def test_instantiate_value_below_minimum_raises_template_error_naming_param_and_range() -> None:
    template = _template()
    with pytest.raises(TemplateError) as exc_info:
        instantiate(template, {"width_mm": 1.0}, plan_id="plate_v2")
    message = str(exc_info.value)
    assert "width_mm" in message
    assert "5.0" in message  # the minimum


def test_instantiate_value_above_maximum_raises_template_error_naming_param_and_range() -> None:
    template = _template()
    with pytest.raises(TemplateError) as exc_info:
        instantiate(template, {"depth_mm": 999.0}, plan_id="plate_v2")
    message = str(exc_info.value)
    assert "depth_mm" in message
    assert "20.0" in message  # the maximum


def test_instantiate_unknown_param_name_raises_template_error() -> None:
    template = _template()
    with pytest.raises(TemplateError):
        instantiate(template, {"not_a_param": 1.0}, plan_id="plate_v2")


def test_instantiate_written_plan_reread_with_allow_stamp_has_no_validated() -> None:
    template = _template()
    plan = instantiate(template, {"width_mm": 15.0}, plan_id="plate_v2")
    reread = parse_plan(plan.model_dump(mode="json"), allow_stamp=True)
    assert reread.validated is None


# --- field_value (tools.impl.templates.save_template reads a TemplateParam.default this way) -------


def test_field_value_reads_a_numeric_field_by_dotted_path() -> None:
    plan = parse_plan(_plan_data(width=42.0))
    assert field_value(plan, "s1", "entities.plate.width") == 42.0
    assert field_value(plan, "e1", "depth_mm") == 5.0


def test_field_value_unknown_operation_id_raises_template_error() -> None:
    plan = parse_plan(_plan_data())
    with pytest.raises(TemplateError):
        field_value(plan, "ghost", "depth_mm")


def test_field_value_non_numeric_field_raises_template_error() -> None:
    plan = parse_plan(_plan_data())
    with pytest.raises(TemplateError):
        field_value(plan, "e1", "sketch")  # a string field (the sketch op id), not numeric
