"""The drawing item table of the contract (``contract/drawing_items.py``): what an agent may ask for, checked without a
CAD, and the promise that it is the same table the FreeCAD worker draws from."""

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from cotaforge.contract import AnnotateDrawingRequest, DescribeDrawingResult, DrawingItemInfo, DrawingSheetInfo
from cotaforge.contract import drawing_items as table

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"


def _worker_registry() -> object:
    if str(WORKER_DIR) not in sys.path:
        sys.path.append(str(WORKER_DIR))
    import sheet_items  # noqa: PLC0415 - the worker's own module, pure Python

    return sheet_items


def test_the_contract_table_is_the_one_the_worker_has() -> None:
    worker = _worker_registry()
    # a type the worker does not draw is in its UNSUPPORTED table, refused in its own words
    assert set(table.ITEM_FIELDS) == set(worker.REGISTRY) | set(worker.UNSUPPORTED)  # type: ignore[attr-defined]
    assert table.PLANNED_ITEMS == worker.PLANNED  # type: ignore[attr-defined]
    for name, fields in table.ITEM_FIELDS.items():
        if name in worker.REGISTRY:  # type: ignore[attr-defined]
            assert fields == worker.REGISTRY[name].fields, name  # type: ignore[attr-defined]


def test_a_planned_type_is_never_also_built() -> None:
    assert not set(table.PLANNED_ITEMS) & set(table.ITEM_FIELDS)


def test_good_items_have_no_problems() -> None:
    items = [
        {"type": "weld", "between": ["face:1", "face:2"], "kind": "fillet", "size_mm": 4, "sides": "both"},
        {"type": "hole_dim", "all": True},
        {"type": "dim", "kind": "distance", "from": {"face": "face:1"}, "to": {"face": "face:2"}, "id": "d1"},
        {"type": "datum_dims", "mode": "table"},
        {"type": "note", "text": "Sin rebabas"},
        {"type": "revision", "index": "A", "description": "Emisión"},
        {"type": "overall"},
    ]
    assert table.problems(items) == []


@pytest.mark.parametrize(
    ("item", "text"),
    [
        ({"type": "weld", "colour": "red"}, "unknown field(s) colour"),
        ({"type": "gdt", "char": "flatness"}, "value is required"),
        ({"type": "blob"}, "type must be one of"),
        ({}, "type must be one of"),
        ("hole_dim", "is not an object"),
        ({"type": "hole_dim", "all": True, "colour": "red"}, "unknown field(s) colour"),
        ({"type": "dim"}, "kind is required"),
        ({"type": "note"}, "text is required"),
        ({"type": "revision", "index": "A"}, "description is required"),
        ({"type": "hole_dim", "all": True, "id": "9x"}, "id '9x'"),
    ],
)
def test_bad_items_say_what_is_wrong(item: object, text: str) -> None:
    found = table.problems([item])
    assert found and text in found[0]


def test_a_repeated_id_and_too_many_items_are_problems() -> None:
    same = [{"type": "overall", "id": "a"}, {"type": "overall", "id": "a"}]
    assert "repeated" in table.problems(same)[0]
    many = [{"type": "overall"}] * (table.MAX_ITEMS_PER_CALL + 1)
    assert "at most" in table.problems(many)[0]


def test_the_annotate_request_checks_its_items_and_its_action() -> None:
    ok = AnnotateDrawingRequest(operation_id="a", action="add", items=({"type": "hole_dim", "all": True},))
    assert ok.items[0]["type"] == "hole_dim"
    with pytest.raises(ValidationError, match="needs items"):
        AnnotateDrawingRequest(operation_id="a", action="add")
    with pytest.raises(ValidationError, match="unknown field"):
        AnnotateDrawingRequest(operation_id="a", action="add", items=({"type": "weld", "colour": "red"},))
    with pytest.raises(ValidationError, match="takes no items"):
        AnnotateDrawingRequest(operation_id="a", action="update", items=({"type": "overall"},))
    with pytest.raises(ValidationError, match="takes no ids"):
        AnnotateDrawingRequest(operation_id="a", action="add", items=({"type": "overall"},), ids=("o1",))
    assert AnnotateDrawingRequest(operation_id="a", action="remove", ids=("h1",)).ids == ("h1",)
    with pytest.raises(ValidationError, match="remove needs the ids"):
        AnnotateDrawingRequest(operation_id="a", action="remove")  # no ids: it never means the whole sheet
    assert AnnotateDrawingRequest(operation_id="a", action="remove_sheet").ids == ()
    with pytest.raises(ValidationError, match="takes no ids"):
        AnnotateDrawingRequest(operation_id="a", action="remove_sheet", ids=("h1",))


def test_the_describe_result_carries_the_stored_spec_of_each_item() -> None:
    result = DescribeDrawingResult(
        sheets=(
            DrawingSheetInfo(
                sheet="Drawing",
                template="A3",
                language="es",
                page_mm=(420.0, 297.0),
                scale=1.0,
                items=(DrawingItemInfo(id="h1", type="hole_dim", status="ok", spec={"type": "hole_dim", "all": True}),),
            ),
        )
    )
    assert result.sheets[0].items[0].spec["all"] is True


def test_the_p4_item_types_are_in_the_table_with_their_required_fields() -> None:
    for kind in ("view_lines", "section", "detail", "aux_view"):
        assert kind in table.ITEM_FIELDS and kind not in table.PLANNED_ITEMS
    assert table.problems([{"type": "detail", "at": [0, 0, 0]}]) == ["item 1 (detail): radius_mm is required"]
    assert table.problems([{"type": "aux_view"}]) == ["item 1 (aux_view): face is required"]
    assert table.problems([{"type": "section", "holes": "all"}, {"type": "view_lines"}]) == []


def test_the_describe_request_asks_for_a_review_and_the_result_carries_findings() -> None:
    from cotaforge.contract import DescribeDrawingRequest, DrawingFinding  # noqa: PLC0415

    assert DescribeDrawingRequest(operation_id="r").review is False
    assert DescribeDrawingRequest(operation_id="r", review=True).review is True
    finding = DrawingFinding(code="hole_no_callout", severity="warning", message="m", features=("feature:4",))
    sheet = DrawingSheetInfo(
        sheet="Drawing", template="A3", language="es", page_mm=(420.0, 297.0), scale=1.0, findings=(finding,)
    )
    assert sheet.findings[0].features == ("feature:4",) and DrawingSheetInfo.model_fields["findings"].default == ()
    with pytest.raises(ValidationError):
        DrawingFinding(code="x", severity="fatal", message="m")
