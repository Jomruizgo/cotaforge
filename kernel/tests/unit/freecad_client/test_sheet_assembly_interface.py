# ruff: noqa: E501
"""The holes an assembly is fastened through on its sheet: their diameter, centre distance and position from the edge of the
plate, and the gate that let them go unlocated; the parts nothing joins; the balloon that points at a sliver; the direction of
the isometric view; the hatching of touching parts. No FreeCAD."""

import sys
from pathlib import Path
from typing import Any

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_context as sc  # noqa: E402
import sheet_hatch  # noqa: E402
import sheet_interface  # noqa: E402
import sheet_isoview  # noqa: E402
import sheet_review as review  # noqa: E402

from .test_sheet_assembly import AssemblyGeometry, codes, context, reviewed  # noqa: E402

PLATE = ((0.0, 0.0, 0.0), (100.0, 60.0, 12.0))  # in the top view of the test context: page x = 100 + x, page y = 70 + y
HOLES = [
    sc.AssemblyHole("comp:1", (20.0, 30.0, 6.0), (0.0, 0.0, 1.0), 9.0),
    sc.AssemblyHole("comp:1", (80.0, 30.0, 6.0), (0.0, 0.0, 1.0), 9.0),
    sc.AssemblyHole("comp:2", (20.0, 30.0, 20.0), (0.0, 0.0, 1.0), 9.0),
    sc.AssemblyHole("comp:2", (80.0, 30.0, 20.0), (0.0, 0.0, 1.0), 6.8),
]


class HoleGeometry(AssemblyGeometry):
    def instance_box(self, comp_id: str) -> tuple[sc.Vec3, sc.Vec3]:
        return PLATE if comp_id == "comp:1" else super().instance_box(comp_id)

    def assembly_holes(self) -> list[sc.AssemblyHole]:
        return list(HOLES)


def with_holes() -> sc.Context:
    ctx = context()
    ctx.geometry = HoleGeometry()
    ctx.holes = []  # an assembly has no holes of its own in the context: the parts have them
    return ctx


def numbers(ctx: sc.Context) -> list[str]:
    return [t for t in ctx.sheet.texts() if t.replace(",", "").isdigit()]


def test_the_holes_on_one_axis_are_one_stack_and_a_hole_elsewhere_is_another() -> None:
    stacks = sheet_interface.stacks_of(HOLES)
    assert [len(s.holes) for s in stacks] == [2, 2]
    assert len(sheet_interface.stacks_of([*HOLES, sc.AssemblyHole("comp:1", (50.0, 10.0, 6.0), (0.0, 0.0, 1.0), 5.0)])) == 3


def test_hole_positions_of_an_assembly_draws_the_diameter_the_centre_distance_and_the_distance_from_the_plate_edge() -> None:
    ctx = with_holes()
    findings = reviewed(ctx, {"type": "hole_positions"})
    texts = ctx.sheet.texts()
    assert "60" in numbers(ctx) and "20" in numbers(ctx), texts  # the first hole is 20 from the left edge and 30 from the bottom (60: the centre distance)
    assert "30" in numbers(ctx), texts
    assert any("2× Ø9" in t for t in texts) and any("Ø6,8" in t for t in texts), texts  # the diameters, with the positions of the parts
    assert "hole_not_located" not in codes(findings), [f.message for f in findings if f.code == "hole_not_located"]


def test_a_through_hole_the_sheet_does_not_dimension_is_not_located_and_blocks_the_export() -> None:
    """Fails with the sheet of before: the holes were in the model and nowhere on the sheet, and no check looked at them."""
    ctx = with_holes()
    findings = reviewed(ctx)  # no overall item: nothing positions the holes
    flagged = [f for f in findings if f.code == "hole_not_located"]
    assert len(flagged) == 2 and all("sin posición" in f.message for f in flagged), codes(findings)
    assert {f.key for f in flagged} == {"@20,30,6", "@80,30,6"} or len({f.key for f in flagged}) == 2


def test_a_hole_is_located_only_by_what_was_drawn_not_by_an_item_that_drew_nothing() -> None:
    ctx = with_holes()
    del ctx.views["top"]  # hole_positions has no view that sees the holes as circles: it draws no position for them
    findings = reviewed(ctx, {"type": "hole_positions"})
    assert codes(findings).count("hole_not_located") == 2


def test_an_assembly_without_holes_gets_no_hole_finding() -> None:
    ctx = context()
    ctx.holes = []
    assert "hole_not_located" not in codes(reviewed(ctx, {"type": "hole_positions"}))


# --- parts nothing joins ------------------------------------------------------------------------------------------------------


def plain_assembly(ctx: sc.Context, *names: str) -> None:
    ctx.assembly = sc.Assembly([sc.PartGroup(i, f"k{i}", name, "", "S355", "", [f"comp:{i}"]) for i, name in enumerate(names, 1)])


def test_two_plates_and_two_spacers_with_nothing_joining_them_are_a_question_to_the_designer() -> None:
    ctx = context()
    plain_assembly(ctx, "Placa base", "Separador", "Placa superior")
    found = [f for f in reviewed(ctx) if f.code == "assembly_not_joined"]
    assert len(found) == 1 and "cómo se unen" in found[0].message


def test_a_fastener_a_weld_a_fit_or_a_note_of_the_engineer_joins_them() -> None:
    ctx = context()
    plain_assembly(ctx, "Placa base", "Tornillo M8x30", "Placa superior")
    assert "assembly_not_joined" not in codes(reviewed(ctx))
    ctx = context()
    plain_assembly(ctx, "Placa base", "Separador")
    assert "assembly_not_joined" not in codes(reviewed(ctx, {"type": "note", "text": "Unión soldada, cordón de 4 mm", "id": "n1"}))
    ctx = context()
    plain_assembly(ctx, "Eje", "Casquillo")
    assert "assembly_not_joined" in codes(reviewed(ctx, {"type": "note", "text": "Material S355", "id": "n1"}))


def test_a_single_part_is_not_an_assembly_that_needs_joining() -> None:
    ctx = context()
    plain_assembly(ctx, "Placa")
    assert "assembly_not_joined" not in codes(reviewed(ctx))


# --- the balloon that points at a sliver -----------------------------------------------------------------------------------------


def balloon_findings(ctx: sc.Context, shares: dict[int, float]) -> list[review.Finding]:
    ctx.extras["views_extra"] = {"isometric": object()}
    ctx.extras["balloons"] = {"drawn": set(shares), "hidden": {}, "view": {n: "isometric" for n in shares}, "share": shares}
    ctx.extras["parts_list"] = {"rect": (0, 0, 1, 1), "positions": sorted(shares)}
    spec = [{"id": "pl", "type": "parts_list"}, {"id": "bal", "type": "balloons"}]
    data = review.ReviewInput(spec, {i["id"]: sc.ItemResult(i["id"], i["type"], "ok", "") for i in spec}, ctx, ctx.sheet)
    return review._assembly_lists(data)


def test_a_balloon_whose_part_is_less_visible_than_the_threshold_is_flagged() -> None:
    assert sheet_isoview.VISIBLE_MIN == 0.2
    ctx = context()
    found = [f for f in balloon_findings(ctx, {1: 0.9, 2: 0.12, 3: 0.2}) if f.code == "balloon_part_hidden"]
    assert len(found) == 1 and "2 (" in found[0].message and "20 %" in found[0].message
    assert "balloon_part_hidden" not in [f.code for f in balloon_findings(context(), {1: 0.9, 2: 0.2, 3: 0.5})]


def test_the_isometric_direction_is_the_one_that_shows_the_worst_seen_part_best() -> None:
    shares = [{"p1": 0.9, "p2": 0.1}, {"p1": 0.5, "p2": 0.4}, {"p1": 0.6, "p2": 0.3}, {"p1": 0.9, "p2": 0.1}]
    assert sheet_isoview.choose(shares) == 1
    assert sheet_isoview.choose([{"p": 0.5}, {"p": 0.5}]) == 0, "a tie keeps the usual direction"
    assert sheet_isoview.CANDIDATES[0] == sheet_isoview.DEFAULT
    assert not sheet_isoview.quarter_turned(sheet_isoview.DEFAULT[0]) and sheet_isoview.quarter_turned(sheet_isoview.CANDIDATES[2][0])


def test_every_candidate_isometric_frame_is_a_right_handed_view_from_above() -> None:
    for direction, xdir in sheet_isoview.CANDIDATES:
        assert direction[2] > 0 and sum(a * b for a, b in zip(direction, xdir, strict=True)) == 0
        up = sc.cross(direction, xdir)
        assert up[2] > 0, (direction, xdir)


# --- the hatching of the section: touching parts never share angle and spacing -------------------------------------------------


def test_two_plates_and_two_spacers_touching_in_the_cut_get_different_hatching_where_they_meet() -> None:
    bodies = ["body 1", "body 2", "body 3", "body 4"]  # base plate, spacer, spacer, top plate
    touching = [("body 1", "body 2"), ("body 1", "body 3"), ("body 2", "body 4"), ("body 3", "body 4")]
    styles = sheet_hatch.plan(bodies, touching)
    for a, b in touching:
        assert styles[a] != styles[b], (a, b, styles)
    findings = sheet_hatch.hatch_findings([sheet_hatch.HatchFact(b, "", "", *styles[b]) for b in bodies], touching)
    assert findings == []

