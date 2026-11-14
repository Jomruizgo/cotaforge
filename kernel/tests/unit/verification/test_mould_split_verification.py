"""What the verifier expects of ``mould_split``: the document gains the insert block's volume less the part's, whatever the
way the block was given (a size, a margin, or the proposal), and the two inserts are registered as new bodies."""

from typing import Any

import pytest

from cotaforge.contract import BodyHandle, BoundingBox, MouldSplitRequest
from cotaforge.ir import MouldSplitOperation
from cotaforge.ir.refs import BodyRef
from cotaforge.verification.observe import StateSnapshot
from cotaforge.verification.results import CheckKind
from tests.unit.verification.test_sufficiency import _concept_stub, mass

PART_VOLUME = 120000.0  # the 100 x 60 x 20 box of the stub


def _expected(request_fields: dict[str, Any], op_fields: dict[str, Any]) -> Any:
    computer, stub, _ = _concept_stub()
    block = StateSnapshot(mass=mass(PART_VOLUME), bbox=BoundingBox(min=(0, 0, 0), max=(100, 60, 20)))
    op = MouldSplitOperation(id="inserts", intent="i", source=BodyRef(from_op="block"), pull="Z", **op_fields)
    request = MouldSplitRequest(
        operation_id="inserts", source=BodyHandle(id="body:1"), pull=(0, 0, 1), **request_fields
    )
    (row,) = computer.compute(op, request, block, stub)
    return computer, row


@pytest.mark.parametrize(
    ("request_fields", "op_fields", "volume"),
    [
        ({"margin_mm": 10.0}, {"margin_mm": 10}, 120 * 80 * 40 - PART_VOLUME),
        ({"block_size": (160.0, 140.0, 90.0)}, {"block_size_mm": (160, 140, 90)}, 160 * 140 * 90 - PART_VOLUME),
        ({}, {}, 150 * 110 * 70 - PART_VOLUME),  # no block given: the part's box grown 25 mm on every side
    ],
)
def test_the_inserts_fill_the_block_less_the_part(
    request_fields: dict[str, Any], op_fields: dict[str, Any], volume: float
) -> None:
    _, row = _expected(request_fields, op_fields)
    assert row.name == "volume_delta" and row.kind == CheckKind.asserted and row.value == pytest.approx(volume)


def test_the_proposal_margin_grows_with_a_long_part() -> None:
    computer, stub, _ = _concept_stub()
    stub.bbox_by_body["body:1"] = BoundingBox(min=(0, 0, 0), max=(500, 60, 20))
    block = StateSnapshot(mass=mass(PART_VOLUME), bbox=BoundingBox(min=(0, 0, 0), max=(500, 60, 20)))
    op = MouldSplitOperation(id="inserts", intent="i", source=BodyRef(from_op="block"), pull="Z")
    request = MouldSplitRequest(operation_id="inserts", source=BodyHandle(id="body:1"), pull=(0, 0, 1))
    (row,) = computer.compute(op, request, block, stub)
    margin = 100.0  # a fifth of 500 mm
    assert row.value == pytest.approx((500 + 2 * margin) * (60 + 2 * margin) * (20 + 2 * margin) - PART_VOLUME)


def test_the_inserts_are_new_bodies_and_the_source_is_not_an_edited_body() -> None:
    computer, _ = _expected({"margin_mm": 10.0}, {"margin_mm": 10})
    assert "inserts" in computer._body_of_op and "inserts#core" in computer._body_of_op  # noqa: SLF001
    assert "inserts" not in computer._edits or computer._edits["inserts"] == []  # noqa: SLF001
