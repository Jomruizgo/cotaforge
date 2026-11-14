# ruff: noqa: E501
"""The facts of the live part the finished-drawing checks need and the items do not carry: the cylindrical faces that are
neither holes nor whole pins (fillets, rounded corners, slot ends), the whole convex cylinders (pins, shafts, bosses) and
whether the part is sheet metal. Read from the B-rep of the bodies, like ``holes.list_holes``; plain dataclasses out
(``sheet_checks.PartFacts``), so the checks themselves import no FreeCAD."""

import math
import re
from typing import Any

import geometry as g
import holes
import registry as reg
import sheet_checks as sck
import sheet_flats as flats_mod
import sheet_keyway as keyway

FULL_TURN = 2.0 * math.pi - 0.02  # a cylinder that goes all the way round (the faces of one cylinder add up to this)
AXIS_TOL = 1e-3
RADIUS_TOL = 1e-4
SHEET_METAL_KINDS = ("base_flange", "edge_flange", "unfold")
THREAD_NAME = re.compile(r"thread|rosca|screw", re.I)  # an external thread is named: no operation of the harness makes one


def _key(axis: g.Vec3, centre: g.Vec3, radius: float, concave: bool) -> tuple[Any, ...]:
    direction = g.canonical_direction(axis)
    foot = g.sub(centre, g.scale(direction, g.dot(centre, direction)))
    return (
        tuple(round(c, 6) for c in direction),
        tuple(round(c, 3) for c in foot),
        round(radius, 4),
        concave,
    )


def collect_assembly(doc: reg.DocumentRec) -> sck.PartFacts:
    """The whole cylinders of the components of an assembly (their bodies, placed): the convex ones (spacers, pins, bosses) and
    the walls of the holes (``bore``: nobody lists the holes of an assembly). Faces have no id an item could name here, so a
    cylinder is called ``<component>/cylinder<n>``. Cylinders of different bodies stay apart: they are different parts."""
    facts = sck.PartFacts(assembly=True)
    for comp_id, comp in doc.components.items():
        for body_id in comp.body_ids:
            shape = reg.placed_component_body(doc, comp, body_id)
            if shape.isNull() or not shape.Solids:
                continue
            groups: dict[tuple[Any, ...], list[tuple[Any, float, float]]] = {}
            for face in shape.Faces:
                if g.surface_kind(face) != "cylinder":
                    continue
                surf = face.Surface
                u0, u1, v0, v1 = face.ParameterRange
                key = _key(g.unit(g.vec(surf.Axis)), g.vec(surf.Center), float(surf.Radius), holes._is_concave(face))  # noqa: SLF001
                groups.setdefault(key, []).append((face, u1 - u0, v1 - v0))
            for number, (key, members) in enumerate(groups.items(), start=1):
                if sum(arc for _, arc, _ in members) < FULL_TURN:
                    continue  # a fillet or a round corner, not a cylinder that goes all the way round
                face = members[0][0]
                surf = face.Surface
                axis = g.unit(g.vec(surf.Axis))
                centre = g.vec(surf.Center)
                foot = g.add(centre, g.scale(axis, g.dot(g.sub(g.vec(face.CenterOfMass), centre), axis)))
                length = max(extent for _, _, extent in members)
                facts.bosses.append(sck.Boss(f"{comp_id}/cylinder{number}", key[2], foot, axis, length, bore=key[3]))
    return facts


def _flats_of(
    groups: dict[tuple[Any, ...], list[tuple[str, Any, float, float]]],
    spans: dict[tuple[Any, ...], list[float]],
    flats: list[keyway.Flat],
    slots: list[keyway.Slot],
) -> list[flats_mod.FlatFace]:
    """The flats of the turned axes of one body: planar faces parallel to an axis that lie inside its largest round (the faces
    of a keyway are not flats of the round)."""
    axes: dict[tuple[Any, ...], dict[str, Any]] = {}
    for key, span in spans.items():
        if key[3]:
            continue  # a bore is not a turned surface
        arc = sum(a for _, _, a, _ in groups[key])
        info = axes.setdefault(
            (key[0], key[1]),
            {"axis": key[0], "point": (span[2], span[3], span[4]), "radius": 0.0, "lo": span[0], "hi": span[1], "turned": False},
        )
        info["radius"] = max(info["radius"], key[2])
        info["lo"], info["hi"] = min(info["lo"], span[0]), max(info["hi"], span[1])
        info["turned"] = info["turned"] or arc >= flats_mod.TURNED_ARC_RAD
    turned = [flats_mod.Turned(i["axis"], i["point"], i["radius"], i["lo"], i["hi"], i["turned"]) for i in axes.values()]
    slot_faces = frozenset(
        f.face_id for slot in slots for f in (slot.wall_a, slot.wall_b, slot.bottom) if f is not None and f.face_id
    )
    candidates = [flats_mod.Flat(f.face_id, f.normal, f.centre) for f in flats if f.face_id]
    return flats_mod.find_flats(turned, candidates, slot_faces)


def collect(doc: reg.DocumentRec, bodies: list[reg.BodyRec]) -> sck.PartFacts:
    """The rounds and the whole cylinders of ``bodies``; a hole (a concave cylinder all the way round) is neither."""
    facts = sck.PartFacts(sheet_metal=any(f.kind in SHEET_METAL_KINDS for f in doc.features))
    facts.thread_features = sum(
        1 for f in doc.features if f.kind != "hole" and THREAD_NAME.search(f"{f.id} {f.label or ''}")
    )
    for body in bodies:
        shape = reg.world_shape(doc, body)
        if shape.isNull() or not shape.Solids:
            continue
        faces = shape.Faces
        groups: dict[tuple[Any, ...], list[tuple[str, Any, float, float]]] = {}
        spans: dict[tuple[Any, ...], list[float]] = {}  # where along its axis each cylinder lies: lo, hi, and a point
        flats: list[keyway.Flat] = []
        for face_id, index in body.live_faces():
            if index >= len(faces):
                continue
            face = faces[index]
            kind = g.surface_kind(face)
            if kind == "plane" and len(faces) <= keyway.FACES_LIMIT:
                flats.append(keyway.Flat(g.unit(g.vec(face.Surface.Axis)), g.vec(face.CenterOfMass), face_id))
            if kind != "cylinder":
                continue
            surf = face.Surface
            axis = g.unit(g.vec(surf.Axis))
            u0, u1, v0, v1 = face.ParameterRange
            concave = holes._is_concave(face)  # noqa: SLF001 - the one definition of "the material lies outside"
            key = _key(axis, g.vec(surf.Center), float(surf.Radius), concave)
            groups.setdefault(key, []).append((face_id, face, u1 - u0, v1 - v0))
            canon = key[0]
            centre = g.vec(surf.Center)
            base = g.dot(centre, canon)
            sign = g.dot(axis, canon)
            ends = sorted((base + v0 * sign, base + v1 * sign))
            span = spans.setdefault(key, [ends[0], ends[1], *centre])
            span[0], span[1] = min(span[0], ends[0]), max(span[1], ends[1])
        cylinders = [
            keyway.Cylinder(key[0], (span[2], span[3], span[4]), key[2], key[3], span[0], span[1])
            for key, span in spans.items()
        ]
        slots = keyway.find_slots(cylinders, flats)
        facts.planes.extend(flats)
        facts.cylinders.extend(cylinders)
        facts.keyways += len(slots)
        facts.keyway_slots.extend(slots)
        facts.flats.extend(_flats_of(groups, spans, flats, slots))
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
