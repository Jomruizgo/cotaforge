# ruff: noqa: E501
"""What a ``compose`` costs the CAD in requests. Every request of the CAD that reviews a sheet draws the whole sheet (and the
sheet of a welded part took minutes each time on the real CAD: bench of 2026-11-13), so the number of reviews a compose asks
for is the number of times the sheet is drawn."""

from cotaforge_mcp.config import Settings

from tests.unit.test_drawing_compose import composed, na_all, sheet_shaft


def test_compose_has_the_cad_review_the_sheet_once_for_all_the_answers_it_gives(settings: Settings) -> None:
    call, fake = sheet_shaft(settings)
    answers = na_all(call)
    assert len(answers) >= 3  # the plan of the shaft asks some rows: each used to be a review of its own
    before = len(fake.described)
    composed(call, {"answers": answers})
    reviews = [r for r in fake.described[before:] if r.review]
    assert len(reviews) <= 4, f"{len(reviews)} reviews for {len(answers)} answers"  # the survey, the one all the answers are checked against, the paper check and the report; it was one more per answer (6 for these 3)
    assert set(answers) <= fake.drawings["Drawing"]["answers"].keys()  # and every answer is on the sheet all the same
