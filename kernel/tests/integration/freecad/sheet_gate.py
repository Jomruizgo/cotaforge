"""No test sheet without a review.

A real test that builds a sheet passes through ``every_sheet_is_reviewed`` (``conftest``): when the test ends, the review of the
sheet runs and the test FAILS on any blocking finding of the drawing rules it did not list on purpose. Until 2026-11-15 the
tests of the native sheets never ran the review, and sheets with a view that carried nothing, the profile drawn twice, dimension
lines crossed and two thirds of the paper empty were accepted by every one of them.

A test that needs a defect on purpose says so, listing the codes::

    pytestmark = pytest.mark.expected_findings("view_adds_nothing", "dimension_crossing")  # the sheet is a bench of defects

or, from the test, ``allow(adapter, "view_adds_nothing")``. ``assert_sheet_clean`` is the same check to call in the middle of a
test (``strict=True``: every blocking finding, the content ones too: a note missing, a hole with no callout).
"""

from __future__ import annotations

from typing import Any

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import DescribeDrawingRequest

RULES = frozenset(
    {
        # a view with nothing on it or drawn twice, lines across lines, a sheet left empty or crowded: what a reader sees at once
        "view_adds_nothing",
        "view_duplicate",
        "dimension_crossing",
        "dimension_stack_order",
        "dimension_gap",
        "sheet_underused",
        "collision",
        "views_overlap",
        "text_outside_frame",
        "paper_too_small",
        # what the review could not read: never taken as checked
        "native_unread",
        "native_orphaned",
    }
)
"""The blocking codes the check always fails on (the geometry of the drawing). ``strict`` fails on every blocking code."""

_ALLOWED = "_sheet_gate_allowed"


def allow(adapter: FreeCADAdapter, *codes: str) -> None:
    """This test builds a defect on purpose: these codes may stand on its sheets."""
    setattr(adapter, _ALLOWED, {*getattr(adapter, _ALLOWED, set()), *codes})


def blocking(adapter: FreeCADAdapter, sheet: str | None = None) -> list[tuple[str, str, tuple[str, ...], str]]:
    """``(sheet, code, items, message)`` of every blocking finding of the review of the sheets (every sheet, or ``sheet``)."""
    request = DescribeDrawingRequest(operation_id="gate", review=True, sheet=sheet)
    rows: list[tuple[str, str, tuple[str, ...], str]] = []
    for described in adapter.describe_drawing(request).sheets:
        rows += [
            (described.sheet, f.code, tuple(f.items), f.message)
            for f in described.findings
            if f.blocking and f.answer is None
        ]
    return rows


def assert_sheet_clean(
    adapter: FreeCADAdapter, sheet: str | None = None, expected: tuple[str, ...] = (), strict: bool = False
) -> None:
    """Fail when the review of the sheet holds a blocking finding that is not in ``expected`` (or allowed by the test). By default
    the drawing rules (``RULES``); ``strict``: every blocking code."""
    allowed = {*expected, *getattr(adapter, _ALLOWED, set())}
    bad = [
        (name, code, items, message)
        for name, code, items, message in blocking(adapter, sheet)
        if code not in allowed and (strict or code in RULES)
    ]
    assert not bad, "the review of the sheet holds blocking findings:\n" + "\n".join(
        f"  {name}: {code} {list(items)}: {message[:240]}" for name, code, items, message in bad
    )


def check_built(built: list[Any], expected: tuple[str, ...]) -> None:
    """The check at the end of a test: every adapter that built a sheet in it, reviewed; a document that is gone (the test closed
    it) or an adapter that has stopped says nothing to review."""
    for adapter in built:
        try:
            blocking(adapter)
        except Exception as exc:  # noqa: BLE001 - nothing to review is not a defect of the sheet
            if "no drawing sheet" in str(exc) or "no document" in str(exc).lower() or "not running" in str(exc).lower():
                continue
            raise
        assert_sheet_clean(adapter, expected=expected)
