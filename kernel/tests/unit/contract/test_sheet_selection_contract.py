# ruff: noqa: E501
"""The contract of selection on drawing sheets: what ``get_selection`` returns for a sheet (picks, the items that refer to
a selected face) and what ``show_entities`` takes (items, views, where)."""

import pytest
from pydantic import ValidationError

from cotaforge.contract import (
    EdgeHandle,
    FaceHandle,
    PersistentRef,
    SelectionEntry,
    SelectionResult,
    SheetHole,
    SheetPick,
    SheetRef,
    ShowEntitiesRequest,
    ShowEntitiesResult,
)
from cotaforge.contract.responses import GeometricSignature


def test_show_entities_takes_items_and_views_alone() -> None:
    request = ShowEntitiesRequest(operation_id="s", items=("d3", "view:top"))
    assert request.entities == () and request.where == "auto" and request.sheet is None and not request.clear
    both = ShowEntitiesRequest(
        operation_id="s",
        entities=(FaceHandle(id="face:1"), EdgeHandle(id="edge:2")),
        items=("h1",),
        where="both",
        sheet="Hoja2",
    )
    assert both.items == ("h1",) and both.where == "both" and both.sheet == "Hoja2"
    sent = ShowEntitiesRequest.model_validate_json(
        both.model_dump_json()
    )  # the worker reads ids: a face and an edge are alike
    assert [e.id for e in sent.entities] == ["face:1", "edge:2"] and sent.items == both.items and sent.where == "both"


def test_show_entities_still_needs_something_to_show_or_clear() -> None:
    with pytest.raises(ValidationError, match="items are given or clear=True"):
        ShowEntitiesRequest(operation_id="s")
    assert ShowEntitiesRequest(operation_id="s", clear=True).clear
    with pytest.raises(ValidationError):
        ShowEntitiesRequest(operation_id="s", items=("d3",), where="everywhere")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        ShowEntitiesRequest(operation_id="s", items=("d3",), sheet="not a sheet name")


def test_a_selection_result_carries_the_picks_of_the_sheet_and_the_items_that_refer_to_an_entity() -> None:
    hole = SheetHole(diameter_mm=6.0, through=True, origin=(15, 12, 0), direction=(0, 0, 1), faces=("face:7",))
    pick = SheetPick(sheet="Drawing", kind="item", label="d3 dim", item="d3", item_type="dim", text="70", holes=(hole,))
    ref = PersistentRef(adapter="fake", token="t", signature=GeometricSignature(kind="face"))
    entry = SelectionEntry(
        ref=ref,
        kind="face",
        label="Face7",
        on_sheet=(SheetRef(sheet="Drawing", item="d3", item_type="dim", text="70"),),
    )
    result = SelectionResult(entries=(entry,), sheet=(pick,))
    assert SelectionResult.model_validate_json(result.model_dump_json()) == result
    assert result.entries[0].on_sheet[0].item == "d3" and result.sheet[0].holes[0].diameter_mm == 6.0
    assert SelectionResult().sheet == () and SelectionEntry(ref=ref, kind="face", label="x").on_sheet == ()


def test_a_pick_is_one_of_the_kinds_of_a_sheet() -> None:
    for kind in ("item", "view", "edge", "vertex", "other"):
        assert SheetPick(sheet="Drawing", kind=kind, label="x").kind == kind  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        SheetPick(sheet="Drawing", kind="face", label="x")  # type: ignore[arg-type]


def test_what_was_shown_and_where_is_in_the_result() -> None:
    assert ShowEntitiesResult(shown="highlight").detail == ""
    assert ShowEntitiesResult(shown="highlight", detail="3D: face:5; sheet Drawing: d3").detail.startswith("3D")
