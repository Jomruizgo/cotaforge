"""``drawing`` add / update tell, in their reply, the blocking findings the step left (a view with nothing on it, a crossing, a
sheet left empty), worded with the remedy."""

from cotaforge.contract import DrawingResult

from cotaforge_mcp import view_export


def test_a_step_that_left_blocking_findings_says_so_and_names_each_with_its_remedy() -> None:
    result = DrawingResult(
        sheet="Drawing",
        step_findings=(
            "view_adds_nothing:right: The right view carries no dimension: dimension it or drop it (drawing action=remove_view, view=right)",
            "dimension_crossing:hubxo1: In the front view the extension lines of hub cross the dimension line of o1 (side=above)",
        ),
    )
    lines = view_export._step_lines(result)  # noqa: SLF001
    assert lines[0].startswith("this step left 2 blocking finding(s)") and "the export waits" in lines[0]
    assert lines[1].startswith("  BLOCKING view_adds_nothing:right: ") and "remove_view" in lines[1]
    assert lines[2].startswith("  BLOCKING dimension_crossing:hubxo1: ") and "side=above" in lines[2]


def test_a_step_that_left_nothing_adds_no_line() -> None:
    assert view_export._step_lines(DrawingResult(sheet="Drawing")) == []  # noqa: SLF001
