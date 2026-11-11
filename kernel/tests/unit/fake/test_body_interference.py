"""``body_interference`` on the fake: every pair of bodies of a part, overlap, contact and clearance; what the fake
cannot measure is ``None`` with a note, never 0."""

import pytest

from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import (
    AdapterError,
    Axis,
    BodyHandle,
    BodyInterferenceRequest,
    BoxRequest,
    CylinderRequest,
    DocumentKind,
    ErrorCode,
    NewDocumentRequest,
    Transform,
)


def part() -> FakeAdapter:
    adapter = FakeAdapter()
    adapter.new_document(NewDocumentRequest(operation_id="new", kind=DocumentKind.part, name="p"))
    return adapter


def cube(adapter: FakeAdapter, x: float) -> BodyHandle:
    made = adapter.box(BoxRequest(operation_id="b", size=(10, 10, 10), transform=Transform(origin=(x, 0, 0))))
    return made.created_bodies[0]


def test_every_pair_is_measured_overlap_contact_and_clearance() -> None:
    adapter = part()
    a, b, c = cube(adapter, 0), cube(adapter, 5), cube(adapter, 15)

    pairs = {(p.a.id, p.b.id): p for p in adapter.body_interference(BodyInterferenceRequest(operation_id="i")).pairs}

    assert set(pairs) == {(a.id, b.id), (a.id, c.id), (b.id, c.id)}
    assert pairs[(a.id, b.id)].common_mm3 == pytest.approx(500) and pairs[(a.id, b.id)].distance_mm == 0
    assert pairs[(b.id, c.id)].common_mm3 == 0 and pairs[(b.id, c.id)].distance_mm == pytest.approx(0)
    assert pairs[(a.id, c.id)].common_mm3 == 0 and pairs[(a.id, c.id)].distance_mm == pytest.approx(5)


def test_a_distance_the_fake_cannot_measure_is_none_with_a_note() -> None:
    adapter = part()
    cube(adapter, 0)
    adapter.cylinder(
        CylinderRequest(
            operation_id="c", diameter_mm=4, height_mm=10, axis=Axis(origin=(50, 5, 0), direction=(0, 0, 1))
        )
    )

    (pair,) = adapter.body_interference(BodyInterferenceRequest(operation_id="i")).pairs

    assert pair.common_mm3 == 0 and pair.distance_mm is None and "boxes" in pair.note


def test_only_the_bodies_asked_for_and_an_unknown_one_is_refused() -> None:
    adapter = part()
    a, _, c = cube(adapter, 0), cube(adapter, 5), cube(adapter, 40)

    only = adapter.body_interference(BodyInterferenceRequest(operation_id="i", bodies=(a, c))).pairs

    assert [(p.a.id, p.b.id) for p in only] == [(a.id, c.id)] and only[0].distance_mm == pytest.approx(30)
    with pytest.raises(AdapterError) as info:
        adapter.body_interference(BodyInterferenceRequest(operation_id="i", bodies=(BodyHandle(id="body:99"),)))
    assert info.value.info.code is ErrorCode.invalid_reference
