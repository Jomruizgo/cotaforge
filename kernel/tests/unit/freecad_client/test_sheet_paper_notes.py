# ruff: noqa: E501
"""The paper and the ``sheet_underused`` check agree: a remedy the review names is one the sheet can take with its notes
block on it (the junction box body, 100 x 100 x 47.5, on A2 at 1:1 with fifteen notes was told to take 2:1, which did not fit).
Synthetic data, no FreeCAD."""

import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_context as sc  # noqa: E402
import sheet_layout as layout  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_review as review  # noqa: E402
import sheet_viewchecks as viewchecks  # noqa: E402
import sheet_viewrules as vr  # noqa: E402

BOX = (100.0, 100.0, 47.5)
NOTES_ROOM = (150.0, 190.0)  # 36 lines of the numbered block: ~150 mm wide, ~190 mm tall


def box_on_a2(lines: int = 36) -> sc.Context:
    """The box in front, top, right and isometric views at 1:1 on A2 (as ``sheet_layout`` spreads them), with a notes block of
    ``lines`` lines."""
    width, height = layout.PAGES_MM["A2"]
    left, top, right, bottom = layout.drawing_area(width, height)
    ctx = sc.Context(
        sheet=model.Sheet(width, height), views={}, holes=[], geometry=sc.Geometry(),
        area=(left, height - bottom, right, height - top), language="es",
    )  # fmt: skip
    cx, cy = (left + right) / 2, (height - bottom + height - top) / 2
    boxes = {
        "front": (cx - 50, cy + 30, cx + 50, cy + 77.5),
        "top": (cx - 50, cy - 120, cx + 50, cy - 20),
        "right": (cx - 190, cy + 30, cx - 90, cy + 77.5),
    }
    ctx.views = {n: sc.ViewInfo(n, (b[0] + b[2]) / 2, (b[1] + b[3]) / 2, 1.0, (0, 0, 1), (1, 0, 0), (0, 0, 20), b) for n, b in boxes.items()}
    ctx.extras.update(
        {
            "view_boxes": [*boxes.values(), (cx + 70, cy - 110, cx + 211, cy - 15)],
            "main_scale": 1.0,
            "extent_mm": BOX,
            "angle": "first",
        }
    )
    if lines:
        ctx.extras.update({"note_lines": lines, "note_width": 146.0})
    return ctx


def found(ctx: sc.Context) -> list[review.Finding]:
    return [f for f in viewchecks.check(review.ReviewInput([], {}, ctx, ctx.sheet)) if f.code in ("sheet_underused", "sheet_unbalanced")]


def test_the_notes_block_is_part_of_what_the_paper_holds() -> None:
    room = viewchecks._notes_room(box_on_a2())  # noqa: SLF001
    assert room is not None and 140.0 < room[0] < 160.0 and 180.0 < room[1] < 200.0
    assert viewchecks._notes_room(box_on_a2(lines=0)) is None  # noqa: SLF001


def test_fewer_views_at_2_to_1_is_not_offered_when_the_notes_would_not_fit_beside_them() -> None:
    """At 2:1 the front and top views take 240 x 270 mm of the 554 x 332 mm free area: beside 150 mm of notes they fit only if the
    notes go in the corner left over, and the box of those views (with dimensions and the isometric view) does not leave one."""
    alone = viewchecks._fewer_views(box_on_a2(lines=0))  # noqa: SLF001
    assert any(scale >= 2.0 for _, scale in alone)  # without notes the arithmetic says 2:1 with fewer views: that is what the draft was told
    with_notes = viewchecks._fewer_views(box_on_a2())  # noqa: SLF001
    assert all(scale < 2.0 for _, scale in with_notes), with_notes


def test_the_box_on_a2_with_its_notes_is_not_blocked_for_a_scale_it_cannot_take() -> None:
    """The draft was held by ``sheet_underused`` asking for 2:1; with the notes counted, nothing the sheet cannot take is asked."""
    for finding in found(box_on_a2()):
        assert "2 : 1" not in finding.message and "scale=2" not in finding.message, finding.message
        if finding.code == "sheet_underused":  # a remedy that holds the export must be one the sheet can take
            assert "3 : 1" not in finding.message


def test_a_smaller_paper_is_offered_only_when_it_holds_the_views_and_the_notes() -> None:
    area = (25.0, 15.0, 405.0, 224.0)  # A3's free area
    block = (200.0, 120.0)
    papers = {"A3": (380.0, 209.0), "A2": (554.0, 332.0)}
    page = (554.0, 332.0)
    rects = [(100.0, 100.0, 300.0, 220.0)]
    without = vr.sheet_underused(rects, (25.0, 15.0, 579.0, 347.0), 1.0, page, True, layout.STANDARD_SCALES, papers)
    assert without is not None and without.paper == "A3"  # a block of 200 x 120 alone fits A3
    with_notes = vr.sheet_underused(rects, (25.0, 15.0, 579.0, 347.0), 1.0, page, True, layout.STANDARD_SCALES, papers, notes=(190.0, 190.0))
    assert with_notes is None or with_notes.paper is None  # ... but 200 x 120 and a 190 x 190 notes block do not
    assert block[0] < area[2] - area[0]


def test_the_layout_leaves_room_for_the_notes_block() -> None:
    page_w, page_h = layout.PAGES_MM["A2"]
    free = layout.plan(*BOX, page_w, page_h, None, None, True, True, False, iso_dims=BOX, own_iso_scale=False)
    kept = layout.plan(*BOX, page_w, page_h, None, None, True, True, False, iso_dims=BOX, own_iso_scale=False, notes=NOTES_ROOM)
    assert free.scale >= 2.0 > kept.scale  # front and top at 2:1 take the sheet; beside the notes they do not fit
    assert kept.fits


def test_fits_round_notes_wants_the_room_above_or_beside() -> None:
    assert layout.fits_round_notes(300.0, 100.0, 554.0, 332.0, None)
    assert layout.fits_round_notes(300.0, 100.0, 554.0, 332.0, (150.0, 190.0))  # above it
    assert layout.fits_round_notes(380.0, 332.0, 554.0, 332.0, (150.0, 190.0))  # beside it
    assert not layout.fits_round_notes(400.0, 200.0, 554.0, 332.0, (150.0, 190.0))  # neither


# --- the notes over the views: which paper holds both ------------------------------------------------------------------------------


def box_on_a3_with_colliding_notes(scale: float = 1.0) -> review.ReviewInput:
    import sheet_collide as collide

    ctx = box_on_a2()
    ctx.sheet = model.Sheet(*layout.PAGES_MM["A3"])
    ctx.extras["main_scale"] = scale
    ctx.extras["collisions"] = [collide.Collision("n1", "views", "text_line", (0.0, 0.0))]
    return review.ReviewInput([{"id": "n1", "type": "note"}], {}, ctx, ctx.sheet)


def test_notes_over_the_views_say_which_paper_holds_both_at_the_scale_of_the_sheet() -> None:
    (finding,) = viewchecks._paper_for_notes(box_on_a3_with_colliding_notes())  # noqa: SLF001
    assert finding.code == "paper_too_small" and finding.items == ("n1",)
    assert "template=A2" in finding.message and "1 : 1" in finding.message and "se solapa" in finding.message


def test_when_no_paper_holds_them_the_finding_says_so_instead_of_naming_one() -> None:
    (finding,) = viewchecks._paper_for_notes(box_on_a3_with_colliding_notes(scale=20.0))  # noqa: SLF001
    assert "ninguna hoja hasta A0" in finding.message and "template=" not in finding.message


def test_notes_that_collide_with_nothing_of_the_views_are_not_a_matter_of_paper() -> None:
    data = box_on_a3_with_colliding_notes()
    data.ctx.extras["collisions"] = []
    assert viewchecks._paper_for_notes(data) == []  # noqa: SLF001


def test_the_finding_holds_the_export() -> None:
    import sheet_checks

    assert "paper_too_small" not in sheet_checks.NON_BLOCKING  # the notes lie on the views: a sheet a shop would send back
