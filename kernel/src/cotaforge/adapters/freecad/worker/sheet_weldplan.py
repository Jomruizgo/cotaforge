# ruff: noqa: E501
"""What a weldment still asks of its designer, on plain facts (no FreeCAD import): the bodies of the cut list with the section
the harness MEASURED of each (the proposal for its designation) and the groups of joints with the weld it proposes. The server
(``drawing(action=plan)``) turns it into the questions of the order; nothing here is applied."""

from typing import Any

import sheet_joints
from sheet_weldment import PROFILE_RATIO, SOLID_MIN_FILL, BodyFact


def section_of(fact: BodyFact) -> dict[str, Any]:
    """The section measured of one body, from the sides of its smallest box (longest first), its volume and its surface:
    ``tube`` (it fills little of its box: the wall is measured), ``bar`` (solid and long: width x thickness) or ``plate``
    (solid and not long: its thickness). Round sections are read as their box: the designer corrects the designation."""
    long, side, thin = fact.dims
    box = (long * side * thin) or 1.0
    if fact.volume / box < SOLID_MIN_FILL and thin > 0:
        wall = sheet_joints.wall_thickness(fact.volume, fact.area, fact.dims)
        return {"kind": "tube", "section_mm": [round(side, 1), round(thin, 1)], "wall_mm": wall}
    if side > 0 and long / side >= PROFILE_RATIO:
        return {"kind": "bar", "section_mm": [round(side, 1), round(thin, 1)], "wall_mm": 0.0}
    return {"kind": "plate", "section_mm": [round(thin, 1)], "wall_mm": 0.0}


def plan_of(
    groups: list[Any], joints: list[Any], named: frozenset[str], welded: set[str]
) -> dict[str, Any]:
    """``groups`` are the positions of the cut list (``sheet_context.PartGroup``), ``joints`` the measured joints,
    ``named`` the positions whose designation the sheet carries and ``welded`` the keys of the joints a weld item covers."""
    bodies = [
        {
            "position": g.position,
            "instances": list(g.instances),
            "reference": g.reference,
            "named": str(g.position) in named,
            **section_of(g.measured),
        }
        for g in groups
        if g.measured is not None
    ]
    out_joints = []
    for group in sheet_joints.groups(joints):
        first = group[0]
        weld = sheet_joints.proposal(first)
        out_joints.append(
            {
                "kind": first.kind,
                "pairs": [list(j.bodies) for j in group],
                "length_mm": round(first.length, 1),
                "thin_mm": round(first.thin, 1),
                "weld_kind": weld.kind,
                "size_mm": weld.size_mm,
                "size_kind": weld.size_kind,
                "all_around": any(len(line) > 2 and line[0] == line[-1] for line in first.seams),  # noqa: PLR2004
                "welded": all(j.key in welded for j in group),
            }
        )
    return {"bodies": bodies, "joints": out_joints}
