"""What an ``op`` call asks the adapter to measure: the step measures the part once when it ends, and the reply prints
that measure (the state lines and the volume change) instead of measuring the part again."""

from collections import Counter
from typing import Any

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import BoundingBox, MassProperties, MassRequest

from tests.conftest import PLATE_SKETCH, Caller, MakeSession


class Counting(FakeAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.calls: Counter[str] = Counter()

    def mass_properties(self, request: MassRequest) -> MassProperties:
        self.calls["mass_properties"] += 1
        return super().mass_properties(request)

    def bounding_box(self, request: MassRequest) -> BoundingBox:
        self.calls["bounding_box"] += 1
        return super().bounding_box(request)


def _op(call: Caller, adapter: Counting, name: str, **params: Any) -> tuple[str, Counter[str]]:
    before = Counter(adapter.calls)
    text = call.ok("op", name=name, params=params)
    return text, adapter.calls - before


def test_an_op_measures_the_part_once_when_it_ends_and_prints_that(make: MakeSession) -> None:
    adapter = Counting()
    call = Caller(make(adapter))
    call.ok("doc", action="new", name="plate")
    _, first = _op(call, adapter, "sketch", **PLATE_SKETCH)
    assert first == Counter(mass_properties=2, bounding_box=2)  # the first step has nothing measured before it
    text, extrude = _op(call, adapter, "extrude", sketch="s1", depth_mm=8)
    # before: 3 and 3 (the part before the step, the step's own, the state lines of the reply)
    assert extrude == Counter(mass_properties=1, bounding_box=1)
    assert "volume: 48000 mm3 (+48000)" in text  # the change is still against the part before the step
    assert "bbox: size (100, 60, 8) mm" in text


def test_a_hand_edit_makes_the_next_step_measure_the_part_again(make: MakeSession) -> None:
    adapter = Counting()
    session = make(adapter)
    call = Caller(session)
    call.ok("doc", action="new", name="plate")
    call.ok("op", name="sketch", params=PLATE_SKETCH)
    assert session.executor is not None
    session.executor.invalidate_measure()  # the engineer changed the part in the CAD
    _, reads = _op(call, adapter, "extrude", sketch="s1", depth_mm=8)
    assert reads == Counter(mass_properties=2, bounding_box=2)  # the part before it and the step's own, never a third
