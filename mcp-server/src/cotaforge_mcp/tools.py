"""Tool dispatch: validate the arguments, run the handler, turn every failure into a short actionable error."""

import sys
import threading
import traceback
from typing import Any

from cotaforge import paths
from cotaforge.contract import AdapterError
from pydantic import ValidationError

from cotaforge_mcp import (
    bearing_seats,
    channel_tools,
    design_memory,
    feature_tools,
    guide,
    inspect_tools,
    memory_tools,
    parts_tools,
    pocket_corners,
    rules,
    turned_ends,
    view_export,
)
from cotaforge_mcp.overflow import REPORTS_DIRNAME, Overflow
from cotaforge_mcp.progress import Reporter
from cotaforge_mcp.reply import Reply, ToolError, fail, first_validation_issues, ok
from cotaforge_mcp.schemas import (
    TOOLS_BY_NAME,
    CatalogArgs,
    ChannelArgs,
    CheckArgs,
    DocArgs,
    DrawingArgs,
    ExportArgs,
    GuideArgs,
    InspectArgs,
    MemoryArgs,
    NativeArgs,
    OpArgs,
    ToolDef,
    UndoArgs,
    ViewArgs,
    visible_tools,
)
from cotaforge_mcp.session import Session


def _invalid(tool: ToolDef, exc: ValidationError, session: Session) -> ToolError:
    issues = first_validation_issues(exc)
    lines = []
    for path, message in issues:
        if path == "action" and tool.actions is not None:
            message = f"must be one of: {', '.join(tool.actions(session))}"
        lines.append(f"field: {path} -> {message}")
    return ToolError("\n".join(lines), code="invalid_input", usage=tool.usage, example=tool.example)


IMPORT_RESULT_CHARS = 4500
STATUS_RESULT_CHARS = 2500


def _fitted(session: Session, name: str, lines: list[str], limit: int) -> Reply:
    """The result as it is; when it exceeds ``limit``, its first lines AND its last ones (a pointer to the full report,
    the warnings and the notes come at either end, never only in the middle), what was left out in between counted, and
    the whole text in a file of ``reports/`` -- the one a tool already named when it wrote its own."""
    text = "\n".join(lines)
    if len(text) <= limit:
        return Reply(text)
    head: list[str] = []
    used = 0
    for line in lines:
        if used + len(line) + 1 > limit * 2 // 3:
            break
        head.append(line)
        used += len(line) + 1
    tail: list[str] = []
    used = 0
    for line in reversed(lines[len(head) :]):
        if used + len(line) + 1 > limit // 5:
            break
        tail.insert(0, line)
        used += len(line) + 1
    left_out = len(lines) - len(head) - len(tail)
    if any(
        name in line for line in lines for name in (REPORTS_DIRNAME, paths.LEGACY_NAMES[REPORTS_DIRNAME])
    ):  # the tool wrote its own report and named it in the result
        where = "the report file named in this result"
        warning = None
    else:
        report = Overflow(session.settings.workspace, name, f"{name} result")
        report.keep("result", text)
        where = str(report.path)
        warning = report.write()
    marker = f"[{left_out} more lines omitted here; full text in {where}]"
    return Reply("\n".join([*head, marker, *tail]) + (f"\n{warning}" if warning else ""))


def _doc(session: Session, args: DocArgs, progress: Reporter | None = None) -> Reply:
    action = args.action
    if action == "new":
        if not args.name:
            raise ToolError(
                "new needs a name",
                code="invalid_input",
                field_name="name",
                usage='doc(action="new", name=..., kind="part|assembly")',
                example='doc(action="new", name="bracket")',
            )
        return ok("\n".join(session.new_document(args.name, args.kind)))
    if action == "open":
        if not args.path:
            raise ToolError(
                "open needs a path",
                code="invalid_input",
                field_name="path",
                usage=f'doc(action="open", path="part{session.adapter.document_suffix}")',
                example=f'doc(action="open", path="bracket{session.adapter.document_suffix}")',
            )
        return ok("\n".join(session.open_document(args.path)), limit=2500)
    if action == "import":
        lines = session.import_document(args.path, args.name, args.overwrite, progress, args.sew, args.sew_tolerance_mm)
        return _fitted(session, "import", lines, IMPORT_RESULT_CHARS)
    if action == "save":
        return ok("\n".join(session.save_document(args.path, args.overwrite, args.force)))
    if action == "reload":
        return ok("\n".join(session.reload_document(args.force)), limit=2500)
    if action == "close":
        return ok("\n".join(session.close_document(args.discard)))
    if action == "switch":
        return ok("\n".join(session.switch_document(args.name)))
    if action == "set_material":
        return ok("\n".join(session.set_material(args.material)))
    if action == "set_route":
        return ok("\n".join(session.set_route(args.route)))
    if action == "set_title":
        return ok("\n".join(session.set_title(args.name)))
    if action == "set_purpose":
        return ok("\n".join(session.set_purpose(args.purpose)))
    if action == "convention":
        return ok("\n".join(session.convention(args.values)))
    return _fitted(session, "status", session.status(), STATUS_RESULT_CHARS)


def _drawing_action(args: DrawingArgs) -> str | None:
    if args.action == "create":
        return "drawing_create"
    if args.action in ("add", "update") and any("dim" in str(item) for item in args.items or ()):
        return "drawing_dimension"
    return None


def _remembering(session: Session, reply: Reply, op: str | None) -> Reply:
    """A drawing or export that worked: the entries its trigger calls for are pushed after its result."""
    if op is None or reply.is_error:
        return reply
    lines = design_memory.after_action(session, op)
    if lines:
        reply.text += "\n" + "\n".join(lines)
    return reply


def _run(
    session: Session,
    tool: ToolDef,
    args: Any,
    progress: Reporter | None = None,
    cancelled: threading.Event | None = None,
) -> Reply:
    if isinstance(args, DocArgs):
        return _doc(session, args, progress)
    if isinstance(args, OpArgs):
        if args.delete is not None:
            if args.name is not None or args.edit is not None or args.id is not None:
                raise ToolError(
                    "delete is alone: do not pass name, edit or id", code="invalid_input", field_name="delete"
                )
            lines = feature_tools.delete_feature(session, args.delete, args.params, args.intent)
            lines += design_memory.after_action(session, "delete_feature", {"step": args.delete})
            lines += bearing_seats.after_action(session, "delete_feature")
            lines += pocket_corners.after_action(session, "delete_feature")
            lines += turned_ends.after_action(session, "delete_feature")
            return ok("\n".join(lines), limit=2500)
        if (args.name is None) == (args.edit is None):
            both = ", not both" if args.name else ""
            raise ToolError(
                f"give name (a new step) or edit (an earlier step to change){both}",
                code="invalid_input",
                field_name="name",
                usage=tool.usage,
                example=tool.example,
            )
        if args.edit is not None:
            if args.id is not None:
                raise ToolError("edit keeps the step id: do not pass id", code="invalid_input", field_name="id")
            if set(args.params) == {"role"}:  # the role of the body or component a step made: not an edit of the step
                with session.lock:
                    role = design_memory.checked_role(session.settings, str(args.params["role"]))
                    return ok("\n".join(design_memory.set_role(session, args.edit, role)))
            if feature_tools.targets_feature(session, args.edit):
                lines = feature_tools.edit_feature(session, args.edit, args.params, args.intent)
                moves_holes = False
            else:
                moves_holes = session.only_moves_holes(args.edit, args.params)  # asked before the edit changes the plan
                lines = session.edit_step(args.edit, args.params, args.intent)
            lines += design_memory.after_action(session, "edit_feature", {"step": args.edit})
            lines += bearing_seats.after_action(session, "edit_feature")
            if not moves_holes:  # a hole moved is no new sharp end nor pocket corner: the faces are not read again
                lines += pocket_corners.after_action(session, "edit_feature")
                lines += turned_ends.after_action(session, "edit_feature")
            return ok("\n".join(lines), limit=2500)
        with session.listening(progress):
            return ok("\n".join(session.run_op(args.name, args.params, args.intent, args.id)))
    if isinstance(args, UndoArgs):
        return ok("\n".join(session.undo(args.to)))
    if isinstance(args, CheckArgs):
        with session.lock:
            session.require_executor()
            return ok("\n".join(rules.check_lines(session)), limit=3000)
    if isinstance(args, MemoryArgs):
        return memory_tools.run(session, args)
    if isinstance(args, ChannelArgs):
        return channel_tools.run(session.settings.workspace, args, cancelled)
    if isinstance(args, CatalogArgs):
        return parts_tools.run(session.settings, args)
    if isinstance(args, NativeArgs):
        lines = session.run_native(args.code, args.intent, args.id, args.timeout_s)
        return ok("\n".join(lines), limit=5000)  # every long line was already shortened, with a pointer to the rest
    if isinstance(args, InspectArgs):
        with session.listening(progress):
            lines, limit = inspect_tools.run(session, args)
        return _fitted(session, f"inspect-{args.action}", lines, limit)
    if isinstance(args, ViewArgs):
        return view_export.view(session, args)
    if isinstance(args, DrawingArgs):
        return _remembering(session, view_export.drawing(session, args), _drawing_action(args))
    if isinstance(args, ExportArgs):
        return _remembering(
            session, view_export.export(session, args), "drawing_export" if args.action in ("pdf", "dxf") else None
        )
    if isinstance(args, GuideArgs):
        return ok(guide.guide(session, args.topic), limit=guide.GUIDE_REPLY_LIMIT)  # already paged to fit
    raise AssertionError(tool.name)  # pragma: no cover - every ToolDef has a branch above


def _saying_what_was_lost(session: Session, failure: ToolError) -> ToolError:
    """A call the CAD process ended in the middle of says FreeCAD is closed, what went with it and how to resume."""
    if failure.code == "session_lost" and "hint: the CAD software stopped" in failure.message:
        failure.message = f"{failure.message}\n{session.lost_note()}"
    return failure


def refuse_early(session: Session, name: str, arguments: dict[str, Any] | None) -> Reply | None:
    """The error of a call that cannot run whatever the CAD turns out to be (an unknown tool, arguments that do not fit
    the tool's model), or ``None``. It needs no CAD, so the server answers it without waiting for the CAD to start."""
    tool = TOOLS_BY_NAME.get(name)
    if tool is None or not tool.visible(session):
        return fail(
            ToolError(f"unknown tool {name!r}; tools: {', '.join(visible_tools(session))}", code="unknown_tool")
        )
    try:
        tool.model.model_validate(arguments or {})
    except ValidationError as exc:
        return fail(_invalid(tool, exc, session))
    return None


LIVE_GUIDE_TOPICS = ("profile", "ops", "terms")


def _guide_topic_is_live(arguments: dict[str, Any] | None) -> bool:
    """The guide topics that say what this CAD can do right now (a worker finds out whether an add-on is installed)."""
    topic = str((arguments or {}).get("topic") or "")
    return topic in LIVE_GUIDE_TOPICS or topic.startswith("op:")


def call_tool(
    session: Session,
    name: str,
    arguments: dict[str, Any] | None,
    progress: Reporter | None = None,
    cancelled: threading.Event | None = None,
) -> Reply:
    """Run one tool. Whatever the engineer changed by hand since the last call heads the reply, once, whatever the
    tool and whether it worked (``hand_edits``). ``cancelled`` is set by the server when the client gave the call up:
    the call still runs to its end (the CAD cannot interrupt what it is doing), but nobody is left to read its reply, so
    what a reply would carry (the hand edits) stays for the next one."""
    session.mem.measured = None  # what a call measured on the model is good for that call only
    reply = _call_tool(session, name, arguments, progress, cancelled)
    session.mem.measured = None
    if cancelled is not None and cancelled.is_set():
        return reply
    heads = [line for line in (session.hand_edits.take(session), *_adapter_notices(session)) if line is not None]
    if heads:
        reply.text = "\n".join([*heads, reply.text])
    return reply


def _adapter_notices(session: Session) -> list[str]:
    """What the adapter did by itself since the last reply (``Adapter.notices``: a dialog it closed, say): each line
    heads one reply, once, because the adapter hands them over and forgets them. They are not in the instructions the
    server sends at start. An adapter that fails to say them never fails the call."""
    try:
        return [f"notice: {line}" for line in session.adapter.notices()]
    except Exception:  # noqa: BLE001 - a notice is courtesy: the reply it would head matters more
        return []


def _call_tool(
    session: Session,
    name: str,
    arguments: dict[str, Any] | None,
    progress: Reporter | None,
    cancelled: threading.Event | None = None,
) -> Reply:
    tool = TOOLS_BY_NAME.get(name)
    if tool is None or not tool.visible(session):
        return fail(
            ToolError(f"unknown tool {name!r}; tools: {', '.join(visible_tools(session))}", code="unknown_tool")
        )
    try:
        if tool.needs_cad:
            session.wait_for_cad(progress)  # a no-op unless the CAD is still starting (or did not start)
        elif name == "cad_guide" and _guide_topic_is_live(arguments) and session.connecting:
            # what the CAD can do (an add-on it lacks) is only known once it is up; if it never is, the guide says
            # what the adapter declares
            try:
                session.wait_for_cad(progress)
            except ToolError:
                pass
        session.recover_if_lost(name)  # a crashed CAD is relaunched and reported before anything else runs
        args = tool.model.model_validate(arguments or {})
        action = getattr(args, "action", None)
        if tool.actions is not None and action is not None and action not in tool.actions(session):
            raise ToolError(
                f"{name}(action={action!r}) is not available with this CAD; "
                f"actions: {', '.join(tool.actions(session))}",
                code="not_supported",
                field_name="action",
            )
        if not tool.needs_cad:
            return _run(session, tool, args, progress, cancelled)
        with session.holding(name, cancelled):  # one call at a time in the CAD; waiting for another is bounded
            return _run(session, tool, args, progress)
    except ValidationError as exc:
        return fail(_invalid(tool, exc, session))
    except ToolError as failure:
        return fail(_saying_what_was_lost(session, failure))
    except AdapterError as exc:
        return fail(_saying_what_was_lost(session, Session.adapter_failure(exc, name)))
    except Exception as exc:  # noqa: BLE001 - never leak a stack trace to the agent; keep it in the server log
        sys.stderr.write(traceback.format_exc())
        return fail(ToolError(f"{type(exc).__name__}: {exc}", code="internal_error"))
