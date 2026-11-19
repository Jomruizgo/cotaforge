"""``inspect(action=draft)`` and ``inspect(action=parting)``: the draft of every face of a moulded part and its parting
line against a pull direction, measured by the CAD."""

from typing import Literal

from cotaforge.contract import (
    BLOCK_MARGIN_RULE,
    DEFAULT_MIN_DRAFT_DEG,
    DraftAnalysisRequest,
    FaceDraft,
    PartingAnalysisRequest,
    PartingAnalysisResult,
    PartingLoop,
)

from cotaforge_mcp.overflow import Overflow
from cotaforge_mcp.reply import ToolError, area_text, num
from cotaforge_mcp.schemas import InspectArgs
from cotaforge_mcp.session import Session

ORDER = {"undercut": 0, "zero": 1, "low": 2, "ok": 3}
DEFAULT_SOURCE = (
    "no minimum given: 1 degree is the usual first-pass rule of thumb of moulding design guides, not a standard "
    "and not the resin's own value; say the engineer's or the moulder's"
)


def _row(session: Session, item: FaceDraft) -> str:
    shadow = (
        f", {num(round(item.shadowed * 100))} % of the points cannot be reached along the pull" if item.shadowed else ""
    )
    how = "exact" if item.exact else f"sampled at {item.samples} points"
    return (
        f"{item.face.id} {session.body_label(item.body.id)} {item.surface} {area_text(item.area_mm2)} "
        f"draft {num(round(item.draft_deg, 2))} deg ({how}){shadow}: {item.status}"
    )


def draft(session: Session, args: InspectArgs) -> list[str]:
    """The counts first, then the faces that are not ``ok`` (undercuts, no draft, low draft), most serious first and the
    largest first inside each; a long list shows ``limit`` rows and writes the whole to a file."""
    session.need("draft_analysis", "inspect(action=draft)")
    session.require_executor()
    if args.pull is None:
        raise ToolError(
            "draft needs the pull direction: the way a mould half moves away from the part",
            code="invalid_input",
            field_name="pull",
            usage='inspect(action="draft", pull=[x, y, z], min_draft_deg?, body?, paint?)',
            example='inspect(action="draft", pull=[0, 0, 1], min_draft_deg=1.5)',
        )
    if sum(c * c for c in args.pull) < 1e-12:
        raise ToolError("pull must be a non-zero vector", code="invalid_input", field_name="pull")
    minimum = args.min_draft_deg if args.min_draft_deg is not None else DEFAULT_MIN_DRAFT_DEG
    body = session.body_handle(args.body, "body") if args.body else None
    result = session.adapter.draft_analysis(
        DraftAnalysisRequest(
            operation_id="draft", body=body, pull=tuple(args.pull), min_draft_deg=minimum, paint=args.paint
        )
    )
    counts = {status: sum(1 for f in result.faces if f.status == status) for status in ORDER}
    pull = ", ".join(num(c) for c in args.pull)
    lines = [
        f"draft against the pull ({pull}), minimum {num(minimum)} deg: {len(result.faces)} faces = "
        + ", ".join(f"{counts[s]} {s}" for s in ORDER)
    ]
    if args.min_draft_deg is None:
        lines.append(f"note: {DEFAULT_SOURCE}")
    lines.append(f"method: {result.method}")
    if not result.shadow_tested:
        lines.append("note: this CAD did not test undercuts: none is reported, which does not mean there are none")
    if result.painted:
        lines.append("painted: green ok, yellow low, red zero or undercut (until something else is shown)")
    lines.extend(f"warning: {w}" for w in result.warnings)
    problems = sorted((f for f in result.faces if f.status != "ok"), key=lambda f: (ORDER[f.status], -f.area_mm2))
    if not problems:
        lines.append("every face is ok")
        return lines
    report = Overflow(session.settings.workspace, "draft", f"draft of {session.doc_name or 'the document'}")
    shown, more = report.items("faces", [_row(session, f) for f in problems], args.limit)
    if more is not None:
        lines.append(more)
        warning = report.write()
        if warning:
            lines.append(warning)
    lines.extend(shown)
    return lines


def _loop_text(loop: PartingLoop) -> str:
    where = "planar" if loop.planar else "not planar"
    if loop.levels_mm:
        shown = ", ".join(num(round(h, 3)) for h in loop.levels_mm[:6])
        where += f", at {shown} mm along the pull" if len(loop.levels_mm) == 1 else f", steps across heights {shown} mm"
    state = "" if loop.closed else " (NOT closed)"
    return (
        f"loop {loop.number} ({loop.role}): {len(loop.edges)} edges, {num(round(loop.length_mm, 2))} mm, {where}{state}"
    )


def parting(session: Session, args: InspectArgs) -> list[str]:
    """The parting line of the part against the pull, the walls that need the engineer's decision, the faces the
    silhouette runs across and the slide each undercut region would need: what ``mould_split`` takes as its inputs."""
    session.need("parting_analysis", "inspect(action=parting)")
    session.require_executor()
    if args.pull is None:
        raise ToolError(
            "parting needs the pull direction: the way the core (the moving half) opens along; the faces that look "
            "along it are the core's, the ones that look against it the cavity's",
            code="invalid_input",
            field_name="pull",
            usage='inspect(action="parting", pull=[x, y, z], id?="core"|"cavity", min_draft_deg?, body?)',
            example='inspect(action="parting", pull=[0, 0, 1], id="cavity")',
        )
    if sum(c * c for c in args.pull) < 1e-12:
        raise ToolError("pull must be a non-zero vector", code="invalid_input", field_name="pull")
    minimum = args.min_draft_deg if args.min_draft_deg is not None else DEFAULT_MIN_DRAFT_DEG
    body = session.body_handle(args.body, "body") if args.body else None
    if args.id not in (None, "core", "cavity"):
        raise ToolError(
            f"id is the half that forms the walls with no draft: core or cavity, not {args.id!r}",
            code="invalid_input",
            field_name="id",
        )
    decision: Literal["core", "cavity"] | None = args.id  # type: ignore[assignment]
    result: PartingAnalysisResult = session.adapter.parting_analysis(
        PartingAnalysisRequest(
            operation_id="parting",
            body=body,
            pull=tuple(args.pull),
            min_draft_deg=minimum,
            zero_draft_to=decision,
        )
    )
    pull = ", ".join(num(c) for c in args.pull)
    lines = [
        f"parting against the pull ({pull}): {len(result.loops)} loop(s), {len(result.ambiguous)} group(s) of walls to "
        f"decide, {len(result.slides)} undercut region(s)" + ("" if result.complete else ", INCOMPLETE")
    ]
    lines.append(f"method: {result.method}")
    lines.extend(_loop_text(loop) for loop in result.loops)
    if result.provisional:
        lines.append(
            f"loops above are computed under the proposal zero_draft_to={result.zero_draft_proposal} (the shorter "
            "parting line; a tie goes to the cavity): the engineer decides"
        )
    for group in result.ambiguous:
        faces = ", ".join(f.id for f in group.faces[:12]) + (" ..." if len(group.faces) > 12 else "")
        lines.append(
            f"DECIDE: {len(group.faces)} wall(s) with no draft touch both halves "
            f"({', '.join(group.touches) or 'none'}): "
            f"{faces}. Which half forms them? zero_draft_to=core or cavity (inspect id=, mould_split zero_draft_to=)"
        )
    if result.straddle:
        faces = ", ".join(f.id for f in result.straddle[:12])
        lines.append(
            f"LIMIT: the silhouette runs across {faces}: FreeCAD cannot split a face along its silhouette, so "
            "no split is possible on this pull"
        )
    for slide in result.slides:
        faces = ", ".join(f.id for f in slide.faces[:8]) + (" ..." if len(slide.faces) > 8 else "")
        if slide.direction is None:
            lines.append(
                f"undercut region {slide.region}: {len(slide.faces)} face(s), {area_text(slide.area_mm2)} ({faces}): "
                "no direction frees it, so no slide can be proposed; change the part or the pull"
            )
        else:
            d = ", ".join(num(round(c, 4)) for c in slide.direction)
            lines.append(
                f"undercut region {slide.region}: {len(slide.faces)} face(s), {area_text(slide.area_mm2)} ({faces}): "
                f"needs a SLIDE along ({d}) ({slide.why}); accept it with mould_split slides=[{slide.region}] or all"
            )
    lines.extend(f"warning: {w}" for w in result.warnings)
    lines.append(f"block: {BLOCK_MARGIN_RULE}")
    return lines
