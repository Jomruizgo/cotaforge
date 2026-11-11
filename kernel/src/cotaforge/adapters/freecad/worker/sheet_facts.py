# ruff: noqa: E501
"""The facts of the live part the finished-drawing checks need and the items do not carry: the cylindrical faces that are
neither holes nor whole pins (fillets, rounded corners, slot ends), the whole convex cylinders (pins, shafts, bosses) and
whether the part is sheet metal. Read from the B-rep of the bodies, like ``holes.list_holes``; plain dataclasses out
(``sheet_checks.PartFacts``), so the checks themselves import no FreeCAD."""

import math
from typing import Any

import geometry as g
import holes
import registry as reg
import sheet_checks as sck

FULL_TURN = 2.0 * math.pi - 0.02  # a cylinder that goes all the way round (the faces of one cylinder add up to this)
AXIS_TOL = 1e-3
RADIUS_TOL = 1e-4
SHEET_METAL_KINDS = ("base_flange", "edge_flange", "unfold")


def _key(axis: g.Vec3, centre: g.Vec3, radius: float, concave: bool) -> tuple[Any, ...]:
    direction = g.canonical_direction(axis)
    foot = g.sub(centre, g.scale(direction, g.dot(centre, direction)))
    return (
        tuple(round(c, 6) for c in direction),
        tuple(round(c, 3) for c in foot),
        round(radius, 4),
        concave,
    )


def collect(doc: reg.DocumentRec, bodies: list[reg.BodyRec]) -> sck.PartFacts:
    """The rounds and the whole cylinders of ``bodies``; a hole (a concave cylinder all the way round) is neither."""
    facts = sck.PartFacts(sheet_metal=any(f.kind in SHEET_METAL_KINDS for f in doc.features))
    for body in bodies:
        shape = reg.world_shape(doc, body)
        if shape.isNull() or not shape.Solids:
            continue
        faces = shape.Faces
        groups: dict[tuple[Any, ...], list[tuple[str, Any, float, float]]] = {}
        for face_id, index in body.live_faces():
            if index >= len(faces):
                continue
            face = faces[index]
            if g.surface_kind(face) != "cylinder":
                continue
            surf = face.Surface
            axis = g.unit(g.vec(surf.Axis))
            u0, u1, v0, v1 = face.ParameterRange
            concave = holes._is_concave(face)  # noqa: SLF001 - the one definition of "the material lies outside"
            key = _key(axis, g.vec(surf.Center), float(surf.Radius), concave)
            groups.setdefault(key, []).append((face_id, face, u1 - u0, v1 - v0))
        for key, members in groups.items():
            concave, radius = key[3], key[2]
            if sum(arc for _, _, arc, _ in members) >= FULL_TURN:
                if concave:
                    continue  # a hole: hole_infos has it
                face_id, face, _, _ = members[0]
                surf = face.Surface
                axis = g.unit(g.vec(surf.Axis))
                centre = g.vec(surf.Center)
                middle = g.vec(face.CenterOfMass)
                foot = g.add(centre, g.scale(axis, g.dot(g.sub(middle, centre), axis)))  # on the axis, mid-height
                facts.bosses.append(sck.Boss(face_id, radius, foot, axis, max(length for _, _, _, length in members)))
                continue
            facts.rounds.extend(sck.RoundFace(face_id, radius, concave) for face_id, _, _, _ in members)
    return facts
