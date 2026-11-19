"""``parting_analysis`` and ``mould_split``: the parting line of a shrunk part, and the inserts cut along
its parting surface (with the slide bodies its undercuts need).

How the halves are made, in order (the result carries the same words as ``method``):

1. the INSERT BLOCK is a box with the engineer's size, or the part's box grown by a margin on every side;
2. the NEGATIVE is the block minus the part;
3. each accepted SLIDE is a body that fills the undercut (the volume the part has there, closed by flat faces on the
   rims of the undercut faces) and runs on, along its own direction, to the block's outside; it is cut out of the
   negative;
4. the PARTING SURFACE (``parting.build_sheet``) slices what is left in two; the piece that touches the core's faces of
   the part is the CORE, the other the CAVITY.

Checks, all on the real shapes: each half is one valid solid; core + cavity + slides + part add up to the block within
``VOLUME_TOL``; the part leaves each half along its own sense of the pull (the half moved a little, and further, along
it meets no volume of the part) and each slide leaves along its direction (the same test).
"""

from typing import Any

import draft_math as dm
import fc
import geometry as g
import mould as md
import mould_math as mm
import ops_common as oc
import ops_primitives as prim
import Part
import parting as pt
import registry as reg
from BOPTools import SplitAPI
from protocol import geometry_failed, invalid_request, operation_id_of, unrecoverable
from recorder import literal

VOLUME_TOL = 1e-4  # 0.01 %: core + cavity + slides + part against the block
LEAVE_NEAR_MM = 0.05  # a half moved by this, then by LEAVE_FAR_SHARE of the block's size, must not meet the part
LEAVE_FAR_SHARE = 0.5
LEAVE_EPS = 1e-6  # relative to the smaller solid: a moved body that overlaps the obstacles by more is trapped
VOTE_OFFSET = 0.1  # mm off a face of the part, into the negative, where a piece is asked whether it holds the point
MAX_VOTES = 400


def block_box(shape: Any, size: g.Vec3 | None, margin: float | None) -> tuple[Any, str]:
    """The insert block (an axis-aligned box) and how it was chosen in words."""
    box = g.exact_box(shape)
    extent = max(box.XLength, box.YLength, box.ZLength)
    if size is not None:
        centre = box.Center
        lo = (centre.x - size[0] / 2, centre.y - size[1] / 2, centre.z - size[2] / 2)
        spans = ((box.XMin, box.XMax), (box.YMin, box.YMax), (box.ZMin, box.ZMax))
        for axis, (part_low, part_high) in enumerate(spans):
            low = lo[axis]
            high = low + size[axis]
            if low > part_low - 1.0 or high < part_high + 1.0:
                msg = (
                    f"the block is {size[axis]:g} mm along {'XYZ'[axis]} and "
                    f"the part is {part_high - part_low:.4g} mm: "
                    "less than 1 mm of steel on a side (the block must be bigger than the part on every side)"
                )
                raise pt.PartingError(msg)
        how = f"block {size[0]:g} x {size[1]:g} x {size[2]:g} mm given by the engineer, centred on the part"
    else:
        margin = mm.proposed_margin(extent) if margin is None else margin
        lo = (box.XMin - margin, box.YMin - margin, box.ZMin - margin)
        size = (box.XLength + 2 * margin, box.YLength + 2 * margin, box.ZLength + 2 * margin)
        how = (
            f"block {size[0]:.4g} x {size[1]:.4g} x {size[2]:.4g} mm: the part's box grown {margin:g} mm on every side"
            + (
                " (proposed: the larger of 25 mm and a fifth of the part's longest side; not a standard)"
                if margin == mm.proposed_margin(extent)
                else " (given by the engineer)"
            )
        )
    return Part.makeBox(size[0], size[1], size[2], g.fcvec(lo)), how


def _boundary_loops(topo: pt.Topology, region: list[int]) -> list[dict[str, Any]]:
    inside = set(region)
    edges = [e for e, owners in topo.edge_faces.items() if len(owners) == 2 and sum(o in inside for o in owners) == 1]
    ends = {str(e): (str(topo.edge_ends[e][0]), str(topo.edge_ends[e][1])) for e in edges}
    pull = (0.0, 0.0, 1.0)  # only for the descriptor's across-the-pull outline; a slide loop is read for its plane
    loops = []
    for loop in mm.chain_loops(ends):
        walked = [(int(e), bool(f)) for e, f in loop["edges"]]  # type: ignore[attr-defined]
        loops.append(pt.describe_loop(topo, walked, pull, bool(loop["closed"])))
    return loops


def slide_body(topo: pt.Topology, slide: dict[str, Any], block: Any, part: Any, reach: float) -> tuple[Any, str]:
    """The slide of one undercut region: the void the part has there (the region's faces closed by flat caps on their
    rims) joined to a prism that carries the outer cap on, along the slide's direction, past the block. Returns the body
    and the method in words. Raises ``PartingError`` when the region's rims are not one or two closed flat loops."""
    d = g.unit(slide["direction"])
    loops = _boundary_loops(topo, slide["faces"])
    if not 1 <= len(loops) <= 2 or not all(lp["closed"] and lp["planar"] for lp in loops):
        msg = (
            f"slide {slide['region']}: the undercut's rims are {len(loops)} loops, "
            f"{'not all ' if loops else ''}closed and flat: "
            "the void is closed with flat caps only on one or two flat rims"
        )
        raise pt.PartingError(msg)
    caps = [pt.shut_off(topo, lp) for lp in loops]
    outer = max(range(len(loops)), key=lambda i: g.dot(g.vec(caps[i].CenterOfMass), d))
    shell = Part.Shell([topo.faces[i] for i in slide["faces"]] + caps)
    plug = Part.Solid(shell)
    plug.fix(1e-7, 1e-7, 1e-7)  # the caps and the part's faces are not oriented alike: fix the solid's orientation
    if not plug.isValid() or plug.Volume <= 1e-9:
        msg = f"slide {slide['region']}: the undercut's void does not close into a valid solid"
        raise pt.PartingError(msg)
    prism = caps[outer].extrude(g.fcvec(g.scale(d, reach)))
    body = plug.fuse(prism).common(block).cut(part).removeSplitter()
    solids = body.Solids
    if not solids:
        msg = f"slide {slide['region']}: nothing is left of the slide inside the block"
        raise pt.PartingError(msg)
    pieces = (
        f"slide {slide['region']} along ({', '.join(f'{round(c, 6) + 0.0:.3g}' for c in d)}): "
        f"the undercut's void ({len(slide['faces'])} faces, "
        f"{len(caps)} flat cap{'s' if len(caps) > 1 else ''}) carried on to the block"
    )
    return (solids[0] if len(solids) == 1 else Part.Compound(solids)), pieces


def _vote(pieces: list[Any], analysis: dict[str, Any]) -> list[dict[str, int]]:
    """For each piece, how many points just off core faces and off cavity faces of the part it holds."""
    votes = [{mm.CORE: 0, mm.CAVITY: 0} for _ in pieces]
    sides = analysis["sides"]
    points: list[tuple[g.Vec3, str]] = []
    for row in analysis["rows"]:
        side = sides[row["index"]]
        if side not in (mm.CORE, mm.CAVITY):
            continue
        samples = row["samples_pts"]
        stride = max(1, len(samples) // 6)
        for p, n in samples[::stride]:
            points.append(((p[0] + n[0] * VOTE_OFFSET, p[1] + n[1] * VOTE_OFFSET, p[2] + n[2] * VOTE_OFFSET), side))
    stride = max(1, len(points) // MAX_VOTES)
    for point, side in points[::stride]:
        vector = g.fcvec(point)
        for index, piece in enumerate(pieces):
            if piece.isInside(vector, 1e-7, False):
                votes[index][side] += 1
                break
    return votes


def _one_solid(shape: Any) -> Any:
    solids = shape.Solids
    return solids[0] if len(solids) == 1 else shape


def leaves_along(body: Any, direction: g.Vec3, obstacles: Any, block_extent: float) -> tuple[bool, float]:
    """Whether ``body`` can move along ``direction`` without meeting ``obstacles``: it is moved a little, then further,
    and the volume it then shares with the obstacles must be nil. Returns the verdict and the largest shared volume."""
    worst = 0.0
    for distance in (LEAVE_NEAR_MM, LEAVE_FAR_SHARE * block_extent):
        moved = body.copy()
        moved.translate(g.fcvec(g.scale(direction, distance)))
        shared = moved.common(obstacles)
        worst = max(worst, float(shared.Volume) if not shared.isNull() else 0.0)
    smaller = min(float(body.Volume), float(obstacles.Volume))  # the tolerance follows the smaller of the two solids
    return worst <= LEAVE_EPS * max(smaller, 1.0), worst


def make_halves(
    shape: Any,
    pull: g.Vec3,
    minimum: float,
    decision: str | None,
    block: Any,
    accepted: list[int] | str | None,
) -> dict[str, Any]:
    """Cut the part's negative in a core and a cavity (and the accepted slides). ``accepted``: the regions of the slides
    to make (None: none). Returns the shapes, the analysis, the checks and the method; raises ``PartingError`` with a
    message that says what the engineer must decide or what FreeCAD cannot do."""
    analysis = pt.analyse(shape, pull, minimum, decision)
    topo: pt.Topology = analysis["topology"]
    if analysis["ambiguous"]:
        faces = ", ".join(f"face:{i}" for a in analysis["ambiguous"] for i in a["faces"])
        msg = (
            f"{faces} have no draft and touch both halves: say zero_draft_to=core or cavity "
            "(the engineer decides which half forms them)"
        )
        raise pt.PartingError(msg)
    if analysis["straddle"]:
        faces = ", ".join(f"face:{i}" for i in analysis["straddle"])
        msg = (
            f"the silhouette of the part runs across {faces}: FreeCAD cannot split a face along its silhouette, so the "
            "parting line cannot be taken from edges; those faces need a split by hand or a different pull"
        )
        raise pt.PartingError(msg)
    wanted = [s["region"] for s in analysis["slides"]] if accepted == "all" else list(accepted or [])
    stray = [n for n in wanted if n not in [s["region"] for s in analysis["slides"]]]
    if stray:
        msg = f"there is no undercut region {stray}: the part has {len(analysis['slides'])}"
        raise pt.PartingError(msg)
    unmet = [s["region"] for s in analysis["slides"] if s["region"] not in wanted]
    if unmet:
        msg = (
            f"undercut region(s) {', '.join(map(str, unmet))} need a slide: the part would be trapped. "
            "Accept the proposed slide(s) (slides=all) or change the part"
        )
        raise pt.PartingError(msg)
    nodir = [s["region"] for s in analysis["slides"] if s["direction"] is None]
    if nodir:
        msg = f"no direction frees undercut region(s) {', '.join(map(str, nodir))}: no slide can be proposed for them"
        raise pt.PartingError(msg)
    loops = analysis["loops"]
    if not loops:
        msg = "the part has no parting line along the pull: every face looks the same way (nothing to split)"
        raise pt.PartingError(msg)
    if any(lp["branched"] for lp in loops):
        msg = "two parting loops touch at a vertex: the parting surface of a touching pair is not built"
        raise pt.PartingError(msg)
    box = block.BoundBox
    reach = 2.0 * box.DiagonalLength
    negative = block.cut(shape)
    slides: list[tuple[dict[str, Any], Any, str]] = []
    for slide in analysis["slides"]:
        body, how = slide_body(topo, slide, block, shape, reach)
        slides.append((slide, body, how))
        negative = negative.cut(body)
    sheet, surface_how, limits = pt.build_sheet(topo, loops, pull, reach, g.vec(box.Center))
    if limits or sheet is None:
        raise pt.PartingError("; ".join(limits) or "no parting surface could be built")
    cut = SplitAPI.slice(negative, [sheet], "Split", 1e-6)
    pieces = list(cut.Solids)
    votes = _vote(pieces, analysis)
    core_idx = [i for i, v in enumerate(votes) if v[mm.CORE] > 0 and v[mm.CAVITY] == 0]
    cavity_idx = [i for i, v in enumerate(votes) if v[mm.CAVITY] > 0 and v[mm.CORE] == 0]
    mixed = [i for i, v in enumerate(votes) if v[mm.CORE] > 0 and v[mm.CAVITY] > 0]
    if mixed or len(core_idx) != 1 or len(cavity_idx) != 1:
        msg = (
            f"the parting surface cut the negative into {len(pieces)} pieces ({len(core_idx)} on the core's faces, "
            f"{len(cavity_idx)} on the cavity's, {len(mixed)} on both): it must give exactly one of each"
        )
        raise pt.PartingError(msg)
    core, cavity = _one_solid(pieces[core_idx[0]]), _one_solid(pieces[cavity_idx[0]])
    strays = [pieces[i] for i in range(len(pieces)) if i not in (core_idx[0], cavity_idx[0])]
    return {
        "analysis": analysis,
        "core": core,
        "cavity": cavity,
        "slides": slides,
        "strays": strays,
        "block": block,
        "surface": surface_how,
        "sheet": sheet,
        "reach": reach,
    }


def check_halves(shape: Any, pull: g.Vec3, made: dict[str, Any]) -> dict[str, Any]:
    """Validity, volume balance and leave-ability of what ``make_halves`` returned."""
    block, core, cavity = made["block"], made["core"], made["cavity"]
    slide_shapes = [s for _, s, _ in made["slides"]]
    volumes = {
        "block": float(block.Volume),
        "part": float(shape.Volume),
        "core": float(core.Volume),
        "cavity": float(cavity.Volume),
        "slides": [float(s.Volume) for s in slide_shapes],
        "strays": [float(s.Volume) for s in made["strays"]],
    }
    gap = mm.volume_balance(
        volumes["block"], [volumes["part"], volumes["core"], volumes["cavity"], *volumes["slides"], *volumes["strays"]]
    )
    extent = max(block.BoundBox.XLength, block.BoundBox.YLength, block.BoundBox.ZLength)
    leave: dict[str, tuple[bool, float]] = {
        "core": leaves_along(core, pull, shape, extent),
        "cavity": leaves_along(cavity, g.scale(pull, -1.0), shape, extent),
    }
    for slide, body, _ in made["slides"]:
        others = Part.Compound([shape, core, cavity])
        leave[f"slide {slide['region']}"] = leaves_along(body, g.unit(slide["direction"]), others, extent)
    return {
        "valid": {
            "core": bool(core.isValid()) and core.ShapeType == "Solid",
            "cavity": bool(cavity.isValid()) and cavity.ShapeType == "Solid",
            "slides": [bool(s.isValid()) for s in slide_shapes],
        },
        "volumes": volumes,
        "balance_gap": gap,
        "balance_ok": gap <= VOLUME_TOL,
        "leave": leave,
        "trapped": trapped_faces(made["analysis"], pull),
    }


def trapped_faces(analysis: dict[str, Any], pull: g.Vec3) -> list[int]:
    """The draft analysis' own shadow test, run for the halves as they were really cut: every face of the part the CORE
    forms must be reachable along ``+pull`` and every face the CAVITY forms along ``-pull`` (a ray from the face, off it
    along its normal, meets nothing of the part). Slide faces are tested with the slide's own direction when it was
    proposed; the tiny faces the analysis already took as sampling artefacts are left out."""
    caster = dm.ShadowCaster(analysis["vertices"], analysis["triangles"], pull)
    skipped = {i for group in analysis["slivers"] for i in group}
    trapped: list[int] = []
    for row in analysis["rows"]:
        index = row["index"]
        side = analysis["sides"][index]
        if side not in (mm.CORE, mm.CAVITY) or index in skipped:
            continue
        sense = 1 if side == mm.CORE else -1
        offset = md.SHADOW_OFFSET
        for p, n in row["samples_pts"]:
            if caster.blocked((p[0] + n[0] * offset, p[1] + n[1] * offset, p[2] + n[2] * offset), sense):
                trapped.append(index)
                break
    return trapped


# --- the worker methods -----------------------------------------------------------------------------------------

METHOD = (
    "faces graded as in draft_analysis; a face looking along the pull is the core's,"
    "one looking against it the cavity's; "
    "an undercut face belongs to a slide; walls with no draft take the side of all their neighbours or the engineer's "
    "decision; the parting line is the edge loops between core and cavity faces; the parting surface is a plane when a "
    "loop lies in one, else a run-off surface (edges pushed outward across the pull, corners mitred), with a flat "
    "shut-off for every inner loop"
)


def _single_solid(doc: reg.DocumentRec, body: reg.BodyRec, op: str, what: str) -> Any:
    shape = reg.world_shape(doc, body)
    solids = [] if shape.isNull() else list(shape.Solids)
    if len(solids) != 1:
        raise geometry_failed(f"{what} needs a body that is one solid; {body.id} has {len(solids)}", op)
    return solids[0]


def _ids(body: reg.BodyRec) -> tuple[dict[int, str], dict[int, str]]:
    faces = {index: face_id for face_id, index in body.live_faces()}
    edges = {rec.index: rec.id for rec in body.edges.values() if not rec.stale}
    return faces, edges


def parting_analysis(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = operation_id_of(params)
    try:
        pull = dm.unit(g.vec(params["pull"]))
    except (KeyError, ValueError, ZeroDivisionError, TypeError) as exc:
        raise invalid_request(f"parting_analysis: invalid pull direction ({exc})", op) from exc
    wanted = params.get("body")
    bodies = [reg.resolve_body(doc, reg.handle_id(wanted, op, "body"), op)] if wanted is not None else doc.live_bodies()
    if len(bodies) != 1:
        raise invalid_request(f"parting_analysis reads one body; the document has {len(bodies)}: name it with body", op)
    body = bodies[0]
    shape = _single_solid(doc, body, op, "parting_analysis")
    try:
        analysis = pt.analyse(shape, pull, float(params.get("min_draft_deg", 1.0)), params.get("zero_draft_to"))
    except pt.PartingError as exc:
        raise geometry_failed(f"parting_analysis: {exc}", op) from exc
    face_ids, edge_ids = _ids(body)

    def faces(indices: list[int]) -> list[dict[str, str]]:
        return [{"id": face_ids.get(i, f"face:{i}")} for i in indices]

    loops = [
        {
            "number": lp["number"],
            "role": lp["role"],
            "closed": lp["closed"],
            "length_mm": lp["length_mm"],
            "planar": lp["planar"],
            "levels_mm": [round(h, 6) for h in lp["levels"]][:12],
            "edges": [{"id": edge_ids.get(i, f"edge:{i}")} for i in lp["edges"]],
        }
        for lp in analysis["loops"]
    ]
    warnings: list[str] = []
    if analysis["slivers"]:
        warnings.append(
            f"{len(analysis['slivers'])} tiny face(s) the shadow test blocks at a few points only (under "
            f"{pt.SLIVER_AREA_MM2:g} mm2, no direction frees them) are taken as ordinary faces, not as undercuts: "
            + "; ".join(", ".join(face_ids.get(i, f"face:{i}") for i in group) for group in analysis["slivers"])
        )
    if any(lp["branched"] for lp in analysis["loops"]):
        warnings.append("two parting loops touch at a vertex: a split does not build that case")
    complete = not analysis["straddle"] and all(lp["closed"] for lp in analysis["loops"])
    if analysis["straddle"]:
        warnings.append(
            "the silhouette of the part runs across the face(s) listed in straddle:"
            "FreeCAD cannot split a face along its "
            "silhouette, so their part of the line is missing and no split can be made on this pull"
        )
    return {
        "loops": loops,
        "ambiguous": [{"faces": faces(a["faces"]), "touches": a["touches"]} for a in analysis["ambiguous"]],
        "zero_draft_proposal": analysis["proposal"],
        "provisional": analysis["proposal"] is not None,
        "straddle": faces(analysis["straddle"]),
        "slides": [
            {
                "region": s["region"],
                "faces": faces(s["faces"]),
                "area_mm2": s["area_mm2"],
                "direction": list(s["direction"]) if s["direction"] is not None else None,
                "why": s["why"],
                "accepted": False,
            }
            for s in analysis["slides"]
        ],
        "complete": complete,
        "method": METHOD,
        "warnings": warnings,
    }


def _loop_line(lp: dict[str, Any]) -> str:
    levels = lp["levels"]
    if not lp["planar"]:
        where = f"steps across {len(levels)} heights ({levels[0]:.4g} to {levels[-1]:.4g} mm along the pull)"
    elif len(levels) == 1:
        where = f"planar, at {levels[0]:.4g} mm along the pull"
    else:
        where = "planar, inclined to the pull"
    return f"{len(lp['edges'])} edges, {lp['length_mm']:.4g} mm, {where}"


def _loops_line(loops: list[dict[str, Any]]) -> str:
    """``parting line: outer loop ...; inner loops: 4 x (...)``: identical inner loops are told once."""
    text = "parting line: outer loop " + _loop_line(loops[0])
    inner: dict[str, int] = {}
    for lp in loops[1:]:
        key = _loop_line(lp)
        inner[key] = inner.get(key, 0) + 1
    if inner:
        text += "; inner loops: " + "; ".join(f"{count} x ({key})" for key, count in inner.items())
    return text


def mould_split(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """The ``mould_split`` op: two new ``Part::Feature`` bodies, cavity and core (and one per accepted slide), from the
    negative of the source cut along its parting surface (``make_halves``).
    Nothing is made unless every check passes."""
    op = operation_id_of(params)
    source = reg.resolve_body(doc, reg.handle_id(params["source"], op, "source"), op)
    try:
        pull = dm.unit(g.vec(params["pull"]))
    except (KeyError, ValueError, ZeroDivisionError, TypeError) as exc:
        raise invalid_request(f"mould_split: invalid pull direction ({exc})", op) from exc
    shape = _single_solid(doc, source, op, "mould_split")
    size = params.get("block_size")
    margin = params.get("margin_mm")
    slides = params.get("slides", "none")
    accepted: list[int] | str = slides if slides == "all" else ([] if slides == "none" else [int(n) for n in slides])
    holder: dict[str, Any] = {}

    def build(ctx: oc.OpContext) -> None:
        try:
            block, block_how = block_box(shape, tuple(size) if size is not None else None, margin)
            minimum = float(params.get("min_draft_deg", 1.0))
            made = make_halves(shape, pull, minimum, params.get("zero_draft_to"), block, accepted)
            checks = check_halves(shape, pull, made)
        except pt.PartingError as exc:
            raise unrecoverable(geometry_failed(f"mould_split: {exc}", op)) from exc
        problems = _problems(checks)
        if problems:
            raise unrecoverable(geometry_failed("mould_split: " + "; ".join(problems), op))
        source_obj = doc.fc_doc.getObject(source.fc_body if source.kind == "partdesign" else source.result_obj)
        pieces = [("cavity", made["cavity"], "MouldCavity"), ("core", made["core"], "MouldCore")]
        pieces += [(f"slide_{s['region']}", body, f"MouldSlide{s['region']}") for s, body, _ in made["slides"]]
        for role, piece, name in pieces:
            obj = fc.add_object(doc.fc_doc, "Part::Feature", name, label=ctx.label(role))
            fc.set_prop(obj, "Shape", piece, source=f"{literal(source_obj)}.Shape  # the {role} of the mould split")
            prim._new_part_body(ctx, obj)  # noqa: SLF001
        holder["report"] = _report(made, checks, block_how, pull)

    result = oc.run_op(doc, "mould_split", params, build)
    # printed line by line by the reply: the parting line, the method and the checks
    result["text"] = "\n".join(holder.get("report", []))
    return result


def _problems(checks: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for name in ("core", "cavity"):
        if not checks["valid"][name]:
            out.append(f"the {name} is not one valid solid")
    if not all(checks["valid"]["slides"]):
        out.append("a slide is not a valid solid")
    if not checks["balance_ok"]:
        out.append(
            f"core + cavity + slides + part differ from the block by {checks['balance_gap']:.3g} of its volume "
            f"(limit {VOLUME_TOL:g}): the split lost or invented material"
        )
    for name, (ok, shared) in checks["leave"].items():
        if not ok:
            out.append(
                f"the {name} traps the part: moved along its opening direction it shares {shared:.4g} mm3 with it"
            )
    if checks["trapped"]:
        faces = ", ".join(f"face:{i}" for i in checks["trapped"][:10])
        out.append(f"the draft analysis finds face(s) a half cannot release along the pull: {faces}")
    return out


def _report(made: dict[str, Any], checks: dict[str, Any], block_how: str, pull: g.Vec3) -> list[str]:
    analysis = made["analysis"]
    lines = [
        f"pull ({pull[0]:.4g}, {pull[1]:.4g}, {pull[2]:.4g}): the core opens along it, the cavity the other way",
        block_how,
    ]
    lines.append(_loops_line(analysis["loops"]))
    lines.append(f"parting surface: {made['surface']}")
    lines += [how for _, _, how in made["slides"]]
    v = checks["volumes"]
    lines.append(
        f"volumes (mm3): block {v['block']:.6g} = core {v['core']:.6g} + cavity {v['cavity']:.6g} + "
        f"part {v['part']:.6g}"
        + "".join(f" + slide {x:.6g}" for x in v["slides"])
        + f"; gap {checks['balance_gap']:.2e} (limit {VOLUME_TOL:g})"
    )
    leave = ", ".join(
        f"{k} {'ok' if ok else 'TRAPS'} (shared {shared:.3g} mm3)" for k, (ok, shared) in checks["leave"].items()
    )
    lines.append("leave: " + leave + f"; faces a ray along the pull meets the part from: {len(checks['trapped'])}")
    if analysis["slivers"]:
        lines.append(
            f"{len(analysis['slivers'])} tiny face(s) taken as ordinary faces, not undercuts (see parting analysis)"
        )
    return lines
