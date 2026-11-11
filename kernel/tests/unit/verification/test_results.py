"""Tests for cotaforge.verification.results: models and JSON round trips."""

from cotaforge.contract import BoundingBox, Transform
from cotaforge.verification.results import CheckKind, CheckResult, Expectation


def test_expectation_json_round_trip_for_every_value_type() -> None:
    bbox = BoundingBox(min=(0.0, 0.0, 0.0), max=(1.0, 2.0, 3.0))
    transform = Transform.from_axis_angle((0.0, 0.0, 1.0), 90.0, (5.0, 0.0, 0.0))
    samples: list[Expectation] = [
        Expectation(name="volume_delta", kind=CheckKind.asserted, unit="mm3", value=48000.0),
        Expectation(name="instance_count", kind=CheckKind.asserted, unit="count", value=4),
        Expectation(name="flag", kind=CheckKind.reported, unit="bool", value=True, note="why"),
        Expectation(name="hole_axis_direction", kind=CheckKind.asserted, unit="vec", value=(0.0, 0.0, -1.0)),
        Expectation(name="bbox_after", kind=CheckKind.asserted, unit="bbox", value=bbox),
        Expectation(name="component_transform", kind=CheckKind.asserted, unit="transform", value=transform),
    ]
    for sample in samples:
        restored = Expectation.model_validate(sample.model_dump(mode="json"))
        assert restored == sample, sample.name
        assert type(restored.value) is type(sample.value), sample.name


def test_check_result_reported_has_ok_none_and_round_trips() -> None:
    result = CheckResult(name="volume_delta", kind=CheckKind.reported, ok=None, expected=-1.5, observed=None)
    restored = CheckResult.model_validate(result.model_dump(mode="json"))
    assert restored == result
    assert restored.ok is None
    assert restored.message == ""
