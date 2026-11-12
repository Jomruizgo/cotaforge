# ruff: noqa: E501
"""``view`` (show / capture / selection / orient / style / section), ``drawing`` and ``export`` (step / mesh / macro /
pdf / dxf)."""

import contextlib
import json
import math
import shutil
import tempfile
from pathlib import Path
from typing import Any

from cotaforge.contract import (
    AdapterError,
    AnnotateDrawingRequest,
    BodyHandle,
    CaptureDrawingRequest,
    CaptureRequest,
    DescribeDrawingRequest,
    DrawingRequest,
    EdgeHandle,
    Empty,
    ExportDrawingRequest,
    ExportDxfRequest,
    ExportMeshRequest,
    ExportRequest,
    FaceHandle,
    FaceInfo,
    FeatureHandle,
    FeatureRequest,
    ProvenanceRole,
    ReferenceVisibilityRequest,
    ResolveRequest,
    SectionRequest,
    ShowEntitiesRequest,
    StyleRequest,
    ViewRequest,
    WindowRequest,
)
from cotaforge.ir import FaceRef
from pydantic import ValidationError

from cotaforge_mcp import design_memory, drawing_gate, feature_tools, pdf_check, rules
from cotaforge_mcp.reply import Image, Reply, ToolError, first_validation_issues, ok
from cotaforge_mcp.schemas import DrawingArgs, ExportArgs, ViewArgs
from cotaforge_mcp.session import Session

SHEET_IMAGE_WIDTH_PX = 1400  # wide enough to read the title block of an A3 sheet
REVIEW_REPLY_CHARS = 14000  # a review lists findings, questions and memory entries: it is read whole
MAX_STEP_FACES = 24  # faces a step may stand for in view(show)
HEADLESS_HINT = "start the server with COTAFORGE_VISIBLE=1 to get a CAD window"


def _unavailable(session: Session, what: str) -> ToolError:
    return ToolError(
        f"{what} is not available in this session ({session.adapter.name}"
        f"{', headless' if not getattr(session.adapter, 'visible', True) else ''}); {HEADLESS_HINT}",
        code="not_supported",
    )


def _on_sheet(entry: Any) -> str:
    """What the sheets say about a selected face or edge: the items that refer to it, by id, type and printed text."""
    if not entry.on_sheet:
        return ""
    refs = "; ".join(
        f"{r.item} {r.item_type}{f' {r.text}' if r.text else ''}" + (f" ({r.sheet})" if r.sheet != "Drawing" else "")
        for r in entry.on_sheet[:10]
    )
    return f"\n  on the sheet: {refs}"


def _sheet_line(pick: Any) -> str:
    """One thing selected on a sheet, in the words the agent works with: what it is, what it says, what it measures and
    the model ids it corresponds to."""
    head = {
        "item": f"item {pick.item} {pick.item_type}",
        "view": f"{pick.view} view",
        "edge": f"line of the {pick.view} view ({pick.label.rpartition(' ')[2]})",
        "vertex": f"point of the {pick.view} view ({pick.label.rpartition(' ')[2]})",
    }.get(pick.kind, pick.label)
    parts = [f"  {head}"]
    if pick.text:
        parts.append(f'prints "{pick.text}"')
    if pick.detail:
        parts.append(pick.detail)
    if pick.edges:
        parts.append(f"model {', '.join(pick.edges)}")
    if pick.faces:
        parts.append(f"faces {', '.join(pick.faces)}")
    for hole in pick.holes:
        through = {True: " through", False: " blind"}.get(hole.through, "")
        parts.append(
            f"hole D{hole.diameter_mm:g}{through} axis from {_vec(hole.origin)} along {_vec(hole.direction)} "
            f"(wall {', '.join(hole.faces)})"
        )
    where = f" [sheet {pick.sheet}]" if pick.sheet and pick.sheet != "Drawing" else ""
    return "; ".join(parts) + where


def _faces_made_by(session: Session, step: str) -> list[str]:
    """The ids of the faces the step ``step`` made (a hole's wall and bottom, a boss's sides): what shows a feature of the
    model on the sheet."""
    from cotaforge_mcp import inspect_tools  # noqa: PLC0415 - inspect_tools imports this package's session

    found: dict[str, None] = {}
    for _label, feature in inspect_tools._features(session, step):  # noqa: SLF001
        result = session.adapter.faces_of(
            FeatureRequest(operation_id="show", feature=feature, role=ProvenanceRole.created)
        )
        found.update(dict.fromkeys(face.handle.id for face in result.faces))
    if not found:
        raise ToolError(
            f"step {step!r} made no face to show",
            code="invalid_input",
            field_name="ids",
            usage='view(action="show", ids=["<step>"], on="sheet") or name faces: inspect(action="faces", op="<step>")',
        )
    if len(found) > MAX_STEP_FACES:
        raise ToolError(
            f'step {step!r} made {len(found)} faces: name the ones to show (inspect(action="faces", op="{step}"))',
            code="invalid_input",
            field_name="ids",
        )
    return list(found)


def _vec(v: Any) -> str:
    return "[" + ", ".join(f"{c + 0.0:g}" for c in v) + "]"


def view(session: Session, args: ViewArgs) -> Reply:
    with session.lock:
        session.require_executor()
        action = args.action
        if action == "selection":
            if "get_selection" not in session.capabilities.tools:
                raise _unavailable(session, "view selection")
            result = session.adapter.get_selection(Empty(operation_id="selection"))
            if not result.entries and not result.unresolved and not result.sheet:
                return ok("selection: empty")
            # the id of the live entity, the one inspect, view show and an op's from_selection take (the adapter's
            # persistent token is its own business)
            usable: list[str] = []
            gone: list[str] = []
            for entry in result.entries[:20]:
                try:
                    entity = session.adapter.resolve_reference(ResolveRequest(operation_id="selection", ref=entry.ref))
                except AdapterError:
                    gone.append(f"not usable: {entry.label} (no longer in the part)")
                    continue
                usable.append(f"{entry.kind} {entry.label} id={entity.entity.id}{_on_sheet(entry)}")
            lines = [f"selection: {len(usable)} usable", *usable, *gone]
            lines.extend(f"not usable: {u.label} ({u.reason})" for u in result.unresolved[:10])
            if usable:
                lines.append(
                    'use the id as is: inspect(action="face", id=...), view(action="show", ids=[...]), an op '
                    '{"kind":"face","from_selection":"<id>"} (edges alike)'
                )
            if result.sheet:
                lines.append(f"on the sheet: {len(result.sheet)}")
                lines.extend(_sheet_line(pick) for pick in result.sheet[:20])
                lines.append(
                    'the face and edge ids above are the ones every tool takes; view(action="show", ids=[...], '
                    'on="3d") shows them in 3D, view(action="show", ids=["<item>"]) an item on the sheet'
                )
            return ok("\n".join(lines))
        if action == "show":
            session.need("show_entities", "view show")
            entities: list[FaceHandle | EdgeHandle] = []
            items: list[str] = []
            for handle in args.ids:
                kind = handle.partition(":")[0]
                if kind in ("view", "item"):  # a view of the sheet (view:top) or an item named as such (item:d3)
                    items.append(handle)
                    continue
                if args.on in ("sheet", "both") and handle in session.known_ids():  # a step: the faces it made
                    entities.extend(FaceHandle(id=face) for face in _faces_made_by(session, handle))
                    continue
                if ":" not in handle and handle not in session.known_ids():
                    items.append(handle)  # an item id of a drawing sheet (d3); the CAD says if there is none
                    continue
                if kind not in ("face", "edge"):
                    step = session.step_naming(handle)
                    named = (
                        f"; {handle!r} names a {'body' if handle not in session.known_ids() else 'step'}: list its "
                        f'faces with inspect(action="faces", op="{step}") and show their ids'
                        if step is not None
                        else ""
                    )
                    raise ToolError(
                        f"{handle!r} is not a face or edge id{named}",
                        code="invalid_input",
                        field_name="ids",
                        usage='view(action="show", ids=["face:3", "edge:7"], note="...") or clear=true',
                    )
                entities.append(FaceHandle(id=handle) if kind == "face" else EdgeHandle(id=handle))
            if not entities and not items and not args.clear:
                raise ToolError(
                    "show needs ids (faces/edges, sheet items, views) or clear=true",
                    code="invalid_input",
                    field_name="ids",
                    usage='view(action="show", ids=["face:3", "d3", "view:top"]) | view(action="show", clear=true)',
                    example='view(action="show", ids=["face:1"], note="mounting face")',
                )
            result = session.adapter.show_entities(
                ShowEntitiesRequest(
                    operation_id="show",
                    entities=tuple(entities),
                    note=args.note,
                    clear=args.clear,
                    items=tuple(items),
                    sheet=args.sheet,
                    where=args.on or "auto",
                )
            )
            text = f"shown: {result.shown}"
            if result.reason:
                text += f" ({result.reason})"
            if result.detail:
                text += f"\n{result.detail}"
            if result.preview_path:
                text += f"\npreview: {result.preview_path}"
            return ok(text)
        if action == "set_view":
            session.need("set_view", "view set_view")
            bodies = tuple(session.body_handle(text, "ids") for text in args.ids)
            session.adapter.set_view(ViewRequest(operation_id="set_view", name=args.name, bodies=bodies))
            fitted = f"fit: {', '.join(args.ids)}" if args.ids else "fit: whole model"
            return ok(f"view: {args.name or 'orientation kept'}; {fitted}")
        if action == "style":
            session.need("style_bodies", "view style")
            if args.color == "roles":  # colour every body by the role of the step that made it
                return ok("\n".join(design_memory.paint(session) or ["colours by role: no body has a role"]))
            if not args.ids:
                raise ToolError(
                    "style needs ids: the bodies (or the steps that made them)",
                    code="invalid_input",
                    field_name="ids",
                    usage='view(action="style", ids=["s3"], transparency?, color?, visible?)',
                    example='view(action="style", ids=["s3"], transparency=60, color="blue")',
                )
            bodies = tuple(session.body_handle(text, "ids") for text in args.ids)
            request = StyleRequest(
                operation_id="style",
                bodies=bodies,
                transparency=args.transparency,
                color=args.color,
                visible=args.visible,
            )
            session.adapter.style_bodies(request)
            changes = [
                f"transparency {args.transparency}%" if args.transparency is not None else None,
                f"colour {request.color}" if request.color is not None else None,
                ("shown" if args.visible else "hidden") if args.visible is not None else None,
            ]
            return ok(f"style: {', '.join(args.ids)}: {', '.join(c for c in changes if c)} (display only)")
        if action == "focus":
            return ok(feature_tools.focus(session, args.ids))
        if action == "references":
            return ok(_references(session, args))
        if action == "section":
            session.need("section_plane", "view section")
            session.adapter.section_plane(
                SectionRequest(operation_id="section", axis=args.axis, offset_mm=args.offset_mm)
            )
            if args.axis is None:
                return ok("section: removed")
            return ok(
                f"section: plane {args.axis} = {args.offset_mm:g} mm; what lies beyond it on the +{args.axis} side is "
                "cut away (display only; view(action=section) with no axis removes it)"
            )
        if action == "window":
            return _window(session, args)
        # capture
        if "capture" not in session.capabilities.tools:
            raise _unavailable(session, "view capture")
        folder = Path(tempfile.mkdtemp(prefix="cotaforge-capture-"))
        try:
            path = folder / "capture.png"
            session.adapter.capture(
                CaptureRequest(operation_id="capture", path=str(path), width=args.width, height=args.height)
            )
            data = path.read_bytes()
        finally:
            with contextlib.suppress(OSError):
                shutil.rmtree(folder)
        return Reply(f"capture: {args.width}x{args.height} png, {len(data)} bytes", images=[Image(data)])


def _references(session: Session, args: ViewArgs) -> str:
    """Show or hide reference geometry by name: planes, axes, points and sketches hide by themselves once a feature
    used them; this is how the agent looks at one again (and hides it after). Display only."""
    session.need("set_reference_visibility", "view references")
    if not args.ids or args.visible is None:
        raise ToolError(
            "references needs ids (the names of the planes, axes, points or sketches in the CAD's tree) and visible",
            code="invalid_input",
            field_name="ids",
            usage='view(action="references", ids=["Plane1"], visible=true|false)',
            example='view(action="references", ids=["Plane1"], visible=true)',
        )
    try:
        result = session.adapter.set_reference_visibility(
            ReferenceVisibilityRequest(operation_id="references", names=tuple(args.ids), visible=args.visible)
        )
    except AdapterError as exc:
        raise session.adapter_failure(exc, "references") from exc
    word = "shown" if args.visible else "hidden"
    parts = [f"references {word}: {', '.join(result.changed) or 'none changed'}"]
    if result.already:
        parts.append(f"already {word}: {', '.join(result.already)}")
    if result.unknown:
        parts.append(f"not a reference of this document: {', '.join(result.unknown)}")
    return "; ".join(parts) + " (display only; the references stay in the model and can be used by name)"


def _window(session: Session, args: ViewArgs) -> Reply:
    """The CAD's own window (where it is, moving it, a capture of it): display only, outside the plan and the undo."""
    session.need("window", "view window")
    area = None
    if args.area is not None:
        if (
            len(args.area) != 4
            or not all(0.0 <= v <= 1.0 for v in args.area)
            or not (args.area[0] < args.area[2] and args.area[1] < args.area[3])
        ):
            raise ToolError(
                "area is [left, top, right, bottom] as fractions of the monitor, left < right and top < bottom",
                code="invalid_input",
                field_name="area",
                usage='view(action="window", command="place", monitor=1, area=[0, 0, 0.5, 1])',
            )
        area = (args.area[0], args.area[1], args.area[2], args.area[3])
    command = args.command or "info"
    folder = Path(tempfile.mkdtemp(prefix="cotaforge-window-"))
    try:
        path = str(folder / "window.png") if command == "capture" else None
        result = session.adapter.window(
            WindowRequest(operation_id="window", command=command, monitor=args.monitor, area=area, path=path)
        )
        images = []
        if result.image_path:
            images.append(Image(Path(result.image_path).read_bytes()))
    finally:
        with contextlib.suppress(OSError):
            shutil.rmtree(folder)
    return Reply(result.text, images=images) if images else ok(result.text)


def _parse_scale(text: str | None) -> float | None:
    """``1:2`` -> 0.5, ``2:1`` -> 2, ``0.5`` -> 0.5."""
    if text is None or not text.strip():
        return None
    value = text.strip().replace(" ", "")
    try:
        left, sep, right = value.partition(":")
        ratio = float(left) / float(right) if sep else float(left)
    except (ValueError, ZeroDivisionError):
        ratio = 0.0
    if not math.isfinite(ratio) or ratio <= 0:
        raise ToolError(
            f"{text!r} is not a scale",
            code="invalid_input",
            field_name="scale",
            usage='scale="1:2" (half size), "2:1" (twice) or "0.5"; omit it for the largest that fits',
            example='drawing(action="create", scale="1:2")',
        )
    return ratio


def _ratio(scale: float) -> str:
    return f"1:{round(1 / scale, 2):g}" if scale < 1 else f"{scale:g}:1"


def _item_line(item: Any) -> str:
    return (
        f"  {item.id} {item.type} {item.status}: {item.detail}"
        if item.detail
        else f"  {item.id} {item.type} {item.status}"
    )


REBUILD_SETTINGS = ("template", "angle", "language", "scale", "projection", "body")
# the settings of create a rebuild leaves as the sheet has them when the caller does not state them


def _rebuild_lines(result: Any) -> list[str]:
    """What a rebuild of an existing sheet kept, what no longer resolves in it and (``fresh``) what it threw away."""
    lines = []
    if result.kept:
        lines.append(f"rebuilt: kept {'; '.join(result.kept)}")
    if result.unresolved:
        lines.append("no longer resolve (fix or remove them): " + "; ".join(result.unresolved))
    if result.discarded:
        lines.append(f"fresh: threw away {'; '.join(result.discarded)}")
    return lines


HLR_NOTICE = (
    "projection: hlr (our own hidden-line projection, not TechDraw's view objects): the views in the FreeCAD window "
    "are drawings of that projection (symbols), the same as the PDF; TechDraw's own views are projection=techdraw"
)


def _sheet_summary(result: Any, brief: bool = False) -> list[str]:
    """The lines every drawing reply opens with: the sheet, what is on it and what each item did. ``brief`` (an add or a
    remove): the sheet in one line, the items this call touched in full, the others only when they are not ok -- the
    engineer is watching the sheet window and the agent adds a little at a time, so the answer stays short."""
    head = f"drawing {result.sheet}: {result.template} sheet at {_ratio(result.scale)}, {result.language}" + (
        ", third angle" if getattr(result, "angle", "first") == "third" else ""
    )
    if brief:
        shown = [i for i in result.items if i.id in result.changed or i.status != "ok"]
        lines = [f"{head}; {len(result.items)} items" + (f"; window: {result.window}" if result.window else "")]
        lines += [_item_line(i) for i in shown]
        if result.changed and not any(i.id in result.changed for i in result.items):
            lines.append(f"removed: {', '.join(result.changed)}")
        if len(result.sheets) > 1:
            lines.append(f"sheets: {', '.join(result.sheets)}")
        return lines
    lines = [head + f"; views: {', '.join(result.views)}", f"dimensions: {', '.join(result.dimensions)}"]
    if result.projection == "hlr":
        lines.append(HLR_NOTICE)
    if len(result.sheets) > 1:
        lines.append(f"sheets: {', '.join(result.sheets)}")
    if result.items:
        lines += ["items:", *[_item_line(i) for i in result.items]]
    return lines


def _item_id(value: Any) -> str:
    """An item id to remove: the id text, or the item object itself (``{"id": "h2", ...}``) as ``list`` prints it."""
    if isinstance(value, dict) and "id" in value:
        return str(value["id"])
    return str(value)


def _problems(exc: ValidationError) -> str:
    return "; ".join(str(e["msg"]).removeprefix("Value error, ") for e in exc.errors()[:3])


def _sheet_as_read(sheet: Any) -> list[str]:
    """One sheet as a reader holding only the printed drawing has it: paper, scale, title block, the views, the texts it
    prints and the native objects on it. No item, id, model reference or body: the shop does not have them, and a
    reviewer who reads them fills in what the sheet leaves out."""
    fields = ", ".join(f"{k}={v}" for k, v in sheet.fields.items())
    lines = [
        f"sheet {sheet.sheet}: {sheet.template} at {_ratio(sheet.scale)}, {sheet.language}"
        + (", third angle" if sheet.angle == "third" else "")
        + (f"; {fields}" if fields else "")
    ]
    if sheet.views:
        lines.append(f"  views: {', '.join(sheet.views)}")
    lines.append("  printed on the sheet: " + (" | ".join(sheet.printed) if sheet.printed else "nothing"))
    if sheet.native:
        lines.append(f"  native objects on the sheet ({len(sheet.native)}): " + " | ".join(sheet.native))
    return lines


def _requirements_block(session: Session, sheet: str) -> list[str]:
    """The requirements of the sheet a create just made (what the standards ask it to carry, with the questions), for the
    reply of ``create``. Never fails the create: a CAD that cannot describe the sheet, or any error, says nothing."""
    if "describe_drawing" not in session.capabilities.tools:
        return []
    try:
        info = drawing_gate.sheet_info(session, sheet, review=True)
        if info is None or not info.facts or drawing_gate.mode_of(session) == "none":
            return []
        return drawing_gate.requirements_lines(session, info, session.language)
    except Exception:  # noqa: BLE001 - the list is help, not part of the create
        return []


def drawing(session: Session, args: DrawingArgs) -> Reply:
    with session.lock:
        session.require_executor()
        action = args.action
        if action == "create":
            session.need("create_drawing", "drawing create")
            request = DrawingRequest(
                operation_id="drawing",
                template=args.template,
                title=args.title or session.doc_name or None,
                author=args.author,
                date=args.date,
                material=args.material or session.design_attributes().material,
                scale=_parse_scale(args.scale),
                angle=args.angle,
                title_block=args.title_block or {},
                language=args.language or session.language,
                drawing_number=args.drawing_number,
                revision=args.revision,
                general_tolerances=args.general_tolerances or "ISO 2768-mK",
                company=args.company,
                sheet=args.sheet,
                body=BodyHandle(id=args.body) if args.body else None,
                isometric=args.isometric,
                views=tuple(args.views) if args.views else None,
                projection=args.projection,
                fresh=args.fresh,
                symmetry=args.symmetry,
                unset=tuple(n for n in REBUILD_SETTINGS if n not in args.model_fields_set),
            )
            result = session.adapter.create_drawing(request)
            session.dirty = True
            summary = _sheet_summary(result)
            lines = [
                summary[0],
                *_rebuild_lines(result),  # before the items: a long list of them is clipped at the end
                *summary[1:],
                f"ready: every view finished computing ({result.waited_s:g} s)",
                *[f"warning: {w}" for w in result.warnings],
                *_requirements_block(session, result.sheet),
                'next: ask what the requirements above lack BEFORE drawing it, then drawing(action="add", items=[...]) a '
                "few at a time (one feature or group per call: the engineer "
                'watches the sheet window and each item shows as it is added; cad_guide(topic="drawing")); '
                'export(action="pdf" or "dxf") to write it',
            ]
            return ok("\n".join(lines), limit=REVIEW_REPLY_CHARS)
        if action in ("add", "remove", "remove_sheet", "update", "add_view", "remove_view"):
            session.need("annotate_drawing", f"drawing {action}")
            items = list(args.items or [])
            usage = (
                'drawing(action="add", items=[{"type":"hole_dim","all":true}]) | drawing(action="remove", items=["h2"]) '
                '| drawing(action="remove_sheet", sheet="Drawing")'
            )
            if action == "remove" and not items:
                raise ToolError(
                    "remove needs the ids of the items to take off; the sheet itself goes only with "
                    'action="remove_sheet"',
                    code="invalid_input",
                    field_name="items",
                    usage=usage,
                    example='drawing(action="remove", items=["h2"])',
                )
            try:
                request = AnnotateDrawingRequest(
                    operation_id="drawing",
                    action=action,
                    sheet=args.sheet,
                    items=tuple(items) if action == "add" else (),
                    ids=tuple(_item_id(i) for i in items) if action == "remove" else (),
                    view=args.views[0] if args.views else None,
                    title_block=args.title_block or {} if action == "update" else {},
                    symmetry=args.symmetry if action == "update" else None,
                )
            except ValidationError as exc:
                raise ToolError(
                    _problems(exc),
                    code="invalid_input",
                    field_name="items",
                    usage=usage,
                    example=(
                        'drawing(action="add", items=[{"type":"dim","kind":"distance",'
                        '"from":{"face":"face:3"},"to":{"face":"face:5"}}])'
                    ),
                ) from exc
            result = session.adapter.annotate_drawing(request)
            session.dirty = True
            if result.removed:
                return ok(f"drawing {result.sheet}: sheet removed; sheets left: {', '.join(result.sheets) or 'none'}")
            lines = [
                *_sheet_summary(result, brief=action != "update"),
                *[f"warning: {w}" for w in result.warnings],
                "next: the engineer sees it in the sheet window; fix a placement with offset_mm / side, or remove + add; "
                "add the next few items",
            ]
            return ok("\n".join(lines))
        if action == "requirements":
            session.need("describe_drawing", "drawing requirements")
            return ok(drawing_gate.requirements_report(session, args.sheet), limit=REVIEW_REPLY_CHARS)
        if action in ("list", "read", "review"):
            session.need("describe_drawing", f"drawing {action}")
            described = session.adapter.describe_drawing(
                DescribeDrawingRequest(operation_id="drawing", sheet=args.sheet, review=action == "review")
            )
            if not described.sheets:
                return ok('drawing: no sheets; drawing(action="create") makes one')
            lines = []
            if action == "read":
                for sheet in described.sheets:
                    lines += _sheet_as_read(sheet)
                return ok("\n".join(lines), limit=REVIEW_REPLY_CHARS)
            for sheet in described.sheets:
                fields = ", ".join(f"{k}={v}" for k, v in sheet.fields.items())
                lines.append(
                    f"sheet {sheet.sheet}: {sheet.template} at {_ratio(sheet.scale)}, {sheet.language}"
                    + (", third angle" if sheet.angle == "third" else "")
                    + (f", body {sheet.body}" if sheet.body else "")
                    + (", projection hlr (views drawn by our own projection)" if sheet.projection == "hlr" else "")
                    + (f"; {fields}" if fields else "")
                )
                for item in sheet.items:
                    spec = {k: v for k, v in item.spec.items() if k not in ("id", "type")}
                    lines.append(f"{_item_line(item)}  {json.dumps(spec, ensure_ascii=False, separators=(',', ':'))}")
                if action == "list" and sheet.printed:
                    lines.append(
                        "  printed on the sheet (what a reader without the model has): " + " | ".join(sheet.printed)
                    )
                if action == "list" and sheet.native:
                    lines.append(
                        f"  native TechDraw objects ({len(sheet.native)}; no item made them, the review does not read "
                        "them; the capture shows them): " + " | ".join(sheet.native)
                    )
                if action == "review":
                    lines += drawing_gate.review_lines(session, sheet)
            return ok("\n".join(lines), limit=REVIEW_REPLY_CHARS)
        if action in ("answer", "approve", "request_review", "review_choice", "review_report"):
            session.need("annotate_drawing", f"drawing {action}")
            if action == "answer":
                return ok(drawing_gate.answer(session, args.sheet, args.id, args.text, args.by, args.reason))
            if action == "request_review":
                return ok(drawing_gate.request_review(session, args.sheet), limit=REVIEW_REPLY_CHARS)
            if action == "review_choice":
                return ok(drawing_gate.review_choice(session, args.sheet, args.by))
            if action == "review_report":
                return ok(
                    drawing_gate.review_report(session, args.sheet, args.by, args.items), limit=REVIEW_REPLY_CHARS
                )
            return ok(drawing_gate.approve(session, args.sheet, args.by, args.reason))
        session.need("capture_drawing", "drawing capture")
        region = _region(args.region)
        width = _capture_width(region)
        folder = Path(tempfile.mkdtemp(prefix="cotaforge-sheet-"))
        try:
            path = folder / "sheet.png"
            result = session.adapter.capture_drawing(
                CaptureDrawingRequest(
                    operation_id="drawing_capture", path=str(path), width=width, sheet=args.sheet, region=region
                )
            )
            data = path.read_bytes()
        finally:
            with contextlib.suppress(OSError):
                shutil.rmtree(folder)
        return Reply("\n".join(_capture_lines(result, len(data))), images=[Image(data)])


def _region(value: list[float] | None) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    if len(value) != 4:
        raise ToolError(
            "region needs four numbers: x0, y0, x1, y1, in mm on the sheet, from its lower left corner",
            code="invalid_input",
            field_name="region",
            usage='drawing(action="capture", sheet=, region=[x0, y0, x1, y1])',
            example='drawing(action="capture", region=[60, 120, 140, 180])',
        )
    x0, y0, x1, y1 = value
    return x0, y0, x1, y1


def _capture_width(region: tuple[float, float, float, float] | None) -> int:
    """The whole sheet is SHEET_IMAGE_WIDTH_PX wide; a zoom keeps its longest side at that many pixels (a narrow tall
    region is not stretched into a huge image)."""
    if region is None:
        return SHEET_IMAGE_WIDTH_PX
    w, h = region[2] - region[0], region[3] - region[1]
    if w <= 0 or h <= 0:
        return SHEET_IMAGE_WIDTH_PX
    return max(64, min(SHEET_IMAGE_WIDTH_PX, round(SHEET_IMAGE_WIDTH_PX * w / max(w, h))))


def _output_lines(result: Any) -> list[str]:
    """What a sheet file holds besides the drawing itself, the same words for the image and the PDF: the views, the objects a
    native script put on the page, and what the file does NOT draw."""
    lines: list[str] = []
    if result.views:
        lines.append(
            "views on the sheet: "
            + ", ".join(f"{v.name}" + (" (native)" if v.kind == "native" else "") for v in result.views)
        )
    if result.native:
        lines.append(
            f"native TechDraw objects on the sheet ({len(result.native)}; no item made them, the review cannot read them): "
            + " | ".join(result.native)
        )
    if result.unrendered:
        lines.append(
            f"NOT DRAWN here ({len(result.unrendered)}): "
            + " | ".join(result.unrendered)
            + "; they are on the TechDraw page (the visible CAD paints them), this file does not draw them"
        )
    lines += [f"warning: {w}" for w in result.warnings]
    return lines


def _capture_lines(result: Any, size: int) -> list[str]:
    how = (
        "TechDraw's own scene of the sheet window (the PDF is written from the same scene)"
        if result.renderer == "techdraw"
        else "CotaForge's own rendering of the sheet (this CAD is headless: TechDraw cannot paint here)"
    )
    pixels = f"{result.size_px[0]}x{result.size_px[1]} px, " if result.size_px[0] else ""
    zoom = ""
    if result.region is not None:
        x0, y0, x1, y1 = result.region
        zoom = f"; zoom into x {x0:g}..{x1:g}, y {y0:g}..{y1:g} mm of the sheet"
    lines = [f"drawing: sheet image, {pixels}png, {size} bytes; rendered by {how}{zoom}"]
    return lines + _output_lines(result)


def _dxf_face(session: Session, face: str | dict[str, Any] | None) -> FaceHandle:
    """The face a DXF is made from, by a handle id ('face:3') or by the face reference ops take
    ({"kind":"face","from_op":"s2","select":{...}}): exactly one face, or an error listing the candidates."""
    usage = (
        'export(action="dxf", face="face:3") | '
        'export(action="dxf", face={"kind":"face","from_op":"s2","select":{"normal":[0,0,1]}}) | '
        'export(action="dxf", flat="<unfold step id>")'
    )
    if face is None:
        raise ToolError(
            "dxf needs the planar face whose outline is cut (or flat=<unfold step id> for a sheet's flat pattern)",
            code="invalid_input",
            field_name="face",
            usage=usage,
            example='export(action="dxf", face="face:1", path="plate.dxf")',
        )
    if isinstance(face, str):
        if face.partition(":")[0] != "face":
            raise ToolError(f"{face!r} is not a face id", code="invalid_input", field_name="face", usage=usage)
        return FaceHandle(id=face)
    try:
        ref = FaceRef.model_validate(face)
    except ValidationError as exc:
        issues = "; ".join(f"{path} -> {message}" for path, message in first_validation_issues(exc))
        raise ToolError(
            f"not a face reference: {issues}", code="invalid_input", field_name="face", usage=usage
        ) from exc
    (found,) = session.require_executor().resolve_display_refs([ref], op_id="export")
    if isinstance(found, FaceInfo):
        return found.handle
    raise ToolError(str(found), code="unresolved_reference", field_name="face", usage=usage)


def _dxf_flat(session: Session, step: str) -> FeatureHandle:
    """The feature of the unfold step the flat pattern belongs to."""
    usage = 'export(action="dxf", flat="<unfold step id>")'
    found = next((s for s in session.steps() if s.id == step), None)
    if found is None or found.feature is None:
        raise ToolError(f"{step!r} is not a step in effect", code="unknown_step", field_name="flat", usage=usage)
    if found.op != "unfold":
        raise ToolError(
            f"step {step} is a {found.op}, not an unfold: only an unfold has a flat pattern",
            code="invalid_input",
            field_name="flat",
            usage=usage,
        )
    return found.feature


def _export_dxf(session: Session, args: ExportArgs, target: Path) -> None:
    """Write the DXF next to its final name and move it into place, so a failed export never leaves half a file
    and an existing one is only replaced when ``overwrite`` says so (``export`` already refused otherwise)."""
    if args.face is not None and args.flat is not None:
        raise ToolError(
            "dxf takes the planar face (face) or the unfold step whose flat pattern is cut (flat), not both",
            code="invalid_input",
            field_name="flat",
            usage='export(action="dxf", face="face:3") | export(action="dxf", flat="<unfold step id>")',
        )
    if args.flat is not None:
        source = {"flat_pattern": _dxf_flat(session, args.flat)}
    else:
        source = {"face": _dxf_face(session, args.face)}
    partial = target.with_name(f".{target.stem}.partial{target.suffix}")
    try:
        session.adapter.export_dxf(ExportDxfRequest(operation_id="export", path=str(partial), **source))
        if partial.is_file():
            partial.replace(target)
    finally:
        with contextlib.suppress(OSError):
            partial.unlink()


def _export_sheet(session: Session, args: ExportArgs, can_write_sheet: bool, target: Path, sheet: str | None) -> Any:
    """Write ONE drawing sheet as pdf or dxf at ``target``: one drawing, one sheet, one file. The adapter writes it next to
    ``target`` in a scratch folder and it is moved into place (an existing file is only replaced with ``overwrite``). A
    bare ``dxf`` with no sheet to write is ambiguous, so the error names both ways to get a DXF."""
    no_sheet = ToolError(
        "dxf needs the planar face whose outline is cut (face=) or a sheet-metal flat pattern (flat=); with neither it "
        "writes the drawing sheet, and this document has none",
        code="invalid_input",
        field_name="face",
        usage='export(action="dxf", face="face:3") | export(action="dxf", flat="<unfold step id>") | '
        'drawing(action="create") then export(action="dxf")',
        example='export(action="dxf", face="face:1", path="plate.dxf")',
    )
    if not can_write_sheet:
        raise no_sheet
    scratch = Path(tempfile.mkdtemp(prefix=".cotaforge-sheets-", dir=target.parent))
    try:
        staged = scratch / target.name
        try:
            result = session.adapter.export_drawing(
                ExportDrawingRequest(operation_id="export", path=str(staged), format=args.action, sheet=sheet)
            )
        except AdapterError as exc:
            if args.action == "dxf" and "no drawing sheet" in exc.info.message:
                raise no_sheet from exc
            raise
        if args.action == "pdf" and staged.is_file():
            _require_sound_pdf(staged, target, result)
        if staged.is_file():
            staged.replace(target)
        return result
    finally:
        with contextlib.suppress(OSError):
            shutil.rmtree(scratch)


def _require_sound_pdf(staged: Path, target: Path, result: Any) -> None:
    """A PDF that is not sound is an error, not a success, and nothing is moved to ``target``: it must open as a one-page PDF
    of the sheet's size, every operator of its content must have the operands the PDF reference gives it, and every view
    of the sheet must have ink where it is (``pdf_check``)."""
    views = [pdf_check.ViewBox(v.name, v.rect) for v in getattr(result, "views", ())]
    report = pdf_check.check_pdf(staged, pages=1, page_mm=getattr(result, "page_mm", None), views=views)
    if report.ok:
        return
    raise ToolError(
        f"the PDF written is damaged and was NOT saved ({target.name} is as it was): {report.summary()}. A viewer would "
        "stop drawing at the first bad operator. The usual cause is a TechDraw object a native script made with a "
        "colour outside 0..1 (0..255 numbers, negatives); look for the objects with drawing(action=list), correct "
        "their colours and export again",
        code="pdf_invalid",
        usage='export(action="pdf", sheet=, overwrite=true)',
    )


def _renderer_note(action: str, result: Any) -> str:
    """Which writer made a drawing file: the PDF of the visible CAD is TechDraw's own export of the sheet window; the
    headless CAD (TechDraw cannot paint there) and every DXF are CotaForge's own writers of the same sheet."""
    renderer = getattr(result, "renderer", "compositor")
    if action == "pdf":
        how = (
            "TechDraw's own export of the sheet window (what the engineer sees and edits)"
            if renderer == "techdraw"
            else "CotaForge's own writer of the sheet (this CAD is headless, TechDraw cannot paint here)"
        )
    else:
        how = "CotaForge's own R12 writer of the sheet (TechDraw's DXF has no title block, symbols or notes)"
    views = len(getattr(result, "views", ()))
    checked = (
        "; checked: one page, every operator sound" + (f", ink in all {views} view(s) of the sheet" if views else "")
        if action == "pdf"
        else ""
    )
    extra = "".join(f"\n{line}" for line in _output_lines(result))
    return f"; written by {how}{checked}{extra}"


def export(session: Session, args: ExportArgs) -> Reply:
    with session.lock:
        session.require_executor()
        extension = {
            "step": ".step",
            "parasolid": ".x_t",
            "mesh": ".stl",
            "macro": ".py",
            "pdf": ".pdf",
            "dxf": ".dxf",
        }[args.action]
        outline = args.action == "dxf" and (args.face is not None or args.flat is not None)
        if args.action == "dxf" and not outline:
            tools = session.capabilities.tools
            if "export_drawing" not in tools and "export_dxf" not in tools:
                session.need("export_dxf", "export dxf")
            needed = "export_drawing" if "export_drawing" in tools else None
        else:
            needed = {
                "step": "export_step",
                "parasolid": "export_parasolid",
                "mesh": "export_mesh",
                "macro": "export_macro",
                "pdf": "export_drawing",
                "dxf": "export_dxf",
            }[args.action]
        if needed is not None:
            session.need(needed, f"export {args.action}")
        blocked = rules.violations(rules.findings_for(session))
        if blocked:
            raise ToolError(
                "export is blocked: the part as measured breaks what is declared\n"
                + "\n".join(rules.render(blocked))
                + "\nediting and doc(action=save) are still allowed; fix the part, or change the declaration",
                code="export_blocked",
                usage="check() shows every finding",
            )
        sheet = args.sheet
        if needed == "export_drawing" and not outline:
            # a drawing sheet (pdf, or the dxf of the sheet): only a finished one is written, ONE sheet to ONE file. The
            # DXF of a face cut or of a sheet-metal flat pattern is not a drawing and does not pass here.
            sheet = drawing_gate.require_finished(session, args.sheet, args.action)
        target = session.settings.resolve(args.path or f"{session.doc_name or 'part'}{extension}")
        if target.exists() and not args.overwrite:
            raise ToolError(
                f"{target.name} already exists; exporting would overwrite it",
                code="file_exists",
                field_name="path",
                usage=f'export(action="{args.action}", path="<another name>") or the same path with overwrite=true',
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        request: ExportRequest | ExportMeshRequest
        wrote = ""
        if outline:
            _export_dxf(session, args, target)
        elif args.action in ("pdf", "dxf"):
            done = _export_sheet(session, args, needed is not None, target, sheet)
            wrote = _renderer_note(args.action, done)
        elif args.action == "step":
            session.adapter.export_step(ExportRequest(operation_id="export", path=str(target)))
        elif args.action == "parasolid":
            session.adapter.export_parasolid(ExportRequest(operation_id="export", path=str(target)))
        elif args.action == "mesh":
            request = ExportMeshRequest(
                operation_id="export", path=str(target), linear_deflection_mm=args.deflection_mm
            )
            session.adapter.export_mesh(request)
        else:
            session.adapter.export_macro(ExportRequest(operation_id="export", path=str(target)))
        if not target.is_file():
            raise ToolError(f"the CAD reported success but {target} was not written", code="export_failed")
        return ok(f"exported {args.action}: {target} ({target.stat().st_size} bytes){wrote}")
