"""The CAD's own features: list them, delete one, change a property of one, show each body's Tip.

``inspect(action=tree)`` lists every feature as the CAD holds it, whoever made it; ``op(edit=<CAD feature name>,
params={Radius: 4})`` sets its properties and ``op(delete=<step id or CAD name>)`` removes it, the CAD's own way. Both
run in the CAD as ONE undoable step (the step the session records, like a ``run_native`` one), are measured, and are
rolled back when the model would not be valid. A feature a plan op made keeps being edited through its step, so the IR
stays the truth about it.
"""

from typing import Any

from cotaforge.contract import (
    AdapterError,
    Empty,
    FeatureEntry,
    FeatureList,
    FocusRequest,
    ListFeaturesRequest,
    MassProperties,
    RemoveFeatureRequest,
    RunNativeResult,
    SetFeaturePropertiesRequest,
)
from cotaforge.executor import ExecutorStateError

from cotaforge_mcp.overflow import Overflow
from cotaforge_mcp.reply import ToolError, area_text, num
from cotaforge_mcp.session import (
    Session,
    _native_failure_lines,
    _native_output_lines,
    _report_warning,
)

MAX_TREE_FEATURES = 60
MAX_REFS_SHOWN = 12
SHORT_PREFIXES = ("PartDesign::", "Sketcher::", "Part::")


def _short_type(type_id: str) -> str:
    for prefix in SHORT_PREFIXES:
        if type_id.startswith(prefix):
            return type_id[len(prefix) :]
    return type_id


def _value(value: float | str | bool) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, str):
        return value
    return num(value)


def _listing(session: Session, feature: str | None = None) -> FeatureList:
    try:
        return session.adapter.list_features(
            ListFeaturesRequest(operation_id=session._op_id("features"), feature=feature)
        )
    except AdapterError as exc:
        if exc.info.code.value == "invalid_request":
            raise _invalid(session, exc.info.message, "edit") from exc
        raise session.adapter_failure(exc, "features") from exc


def _line(session: Session, entry: FeatureEntry, detail: bool = False) -> str:
    step = session.step_of_feature(entry.feature.id) if entry.feature is not None else None
    parts = [entry.name]
    if entry.label != entry.name:
        parts.append(f"'{entry.label}'")
    if not entry.name.startswith(_short_type(entry.type_id)) or detail:
        parts.append(_short_type(entry.type_id))
    shown = [f"{k}={_value(v)}" for k, v in list(entry.properties.items())[:5]]
    if entry.ref_count is not None:
        shown.append(f"{entry.ref_count} edges/faces")
    if shown:
        parts.append("(" + ", ".join(shown) + ")")
    flags = []
    if entry.is_tip:
        flags.append("tip")
    if entry.suppressed:
        flags.append("suppressed")
    if not entry.visible:
        flags.append("hidden")
    if step is not None:
        flags.append(f"step {step}")
    if flags:
        parts.append("[" + ", ".join(flags) + "]")
    return " ".join(parts)


def targets_feature(session: Session, name: str) -> bool:
    """Is ``op(edit=name)`` about a feature of the CAD (a name that is not a step or an adopted body)?"""
    return session.supports("set_feature_properties") and name not in session.known_ids()


def tree_lines(session: Session, feature: str | None = None) -> list[str]:
    """The features block of ``inspect(action=tree)``: body by body in the order of its chain; ``feature`` (a CAD name)
    asks for one in detail."""
    listing = _listing(session, feature)
    if feature is not None:
        entry = listing.features[0]
        lines = [_line(session, entry, True)]
        if entry.used_by:
            lines.append("needed by: " + ", ".join(entry.used_by))
        if entry.base:
            lines.append(f"built on: {entry.base}")
        if entry.editable_properties:
            lines.append("properties (op edit): " + ", ".join(entry.editable_properties))
        if entry.refs:
            kind = entry.refs[0].kind
            unit = "mm" if kind == "edge" else "mm2"
            rows = [f"{r.name} {num(r.size)} {unit} at {tuple(round(c, 2) for c in r.center)}" for r in entry.refs]
            shown = rows[:MAX_REFS_SHOWN]
            more = f" ... {len(rows) - len(shown)} more" if len(rows) > len(shown) else ""
            lines.append(f"{kind}s it works on: " + "; ".join(shown) + more)
        return lines
    if not listing.features:
        return []
    lines = [
        f"features: {len(listing.features)} (the CAD's own names; without a [step] tag they were made outside the "
        'steps: op(edit="<name>", params={...}) changes them, op(delete="<name>") removes them)'
    ]
    tips = {b.name: b for b in listing.bodies}
    current = object()
    for entry in listing.features[:MAX_TREE_FEATURES]:
        if entry.body_name != current:
            current = entry.body_name
            body = tips.get(entry.body_name)
            if body is not None:
                lines.append(
                    f"body {body.name}"
                    + (f" '{body.label}'" if body.label != body.name else "")
                    + (f" ({body.body.id})" if body.body is not None else "")
                    + (f", surface, {area_text(body.area_mm2)}" if body.kind == "surface" else "")
                    + f": tip {body.tip or 'none'}"
                )
        lines.append("  " + _line(session, entry))
    if len(listing.features) > MAX_TREE_FEATURES or listing.truncated:
        lines.append(f"  ... {len(listing.features) - MAX_TREE_FEATURES + listing.truncated} more")
    return lines


def _tip_lines(session: Session) -> list[str]:
    try:
        listing = _listing(session)
    except ToolError:
        return []
    return [f"tip: {b.name} -> {b.tip}" for b in listing.bodies if b.tip]


def _changes(old: FeatureEntry, new: FeatureEntry) -> list[str]:
    out = []
    for key in dict.fromkeys([*old.properties, *new.properties]):
        before, after = old.properties.get(key), new.properties.get(key)
        if before != after and not (
            isinstance(before, float) and isinstance(after, float) and abs(before - after) < 1e-9
        ):
            was = _value(before) if before is not None else "-"
            now = _value(after) if after is not None else "-"
            out.append(f"{key} {was} -> {now}")
    if old.ref_names != new.ref_names:
        out.append(
            f"edges/faces {len(old.ref_names)} -> {len(new.ref_names)}: {', '.join(new.ref_names[:MAX_REFS_SHOWN])}"
        )
    if old.suppressed != new.suppressed:
        out.append("suppressed" if new.suppressed else "unsuppressed")
    return out


def _finish(
    session: Session, step_id: str, kind: str, result: RunNativeResult, before: MassProperties, head: str, wording: str
) -> list[str]:
    """Record the step the adapter ran and say what it did, or explain why it was not applied."""
    report = Overflow(session.settings.workspace, f"{kind}-{step_id}", f"{kind} {step_id}: {wording}")
    if not result.ok:
        lines = [f"{head} was not applied", *_native_failure_lines(result, report)]
        lines.append(
            "state: unchanged"
            if result.rolled_back
            else "WARNING: the rollback could not be verified; inspect the document"
        )
        lines.extend(session.state_lines())
        lines.extend(_report_warning(report))
        raise ToolError("\n".join(lines), code="edit_failed")
    executor = session.require_executor()
    lines = [f"ok: {step_id} {head}"]
    if result.feature is None:
        lines.append("changes: none (the document already had these values)")
        lines.extend(_native_output_lines(result, report))
        return lines
    after, _ = session.measure()
    try:
        executor.record_external_step(
            step_id, kind, feature=result.feature, mass_before=before, mass_after=after, note=wording
        )
    except ExecutorStateError as exc:
        session.adapter.undo_last(Empty(operation_id=step_id))
        raise ToolError(str(exc), code="session_state") from exc
    session.dirty = True
    return lines


def delete_feature(session: Session, target: str, params: dict[str, Any], intent: str | None) -> list[str]:
    unknown = sorted(set(params) - {"cascade"})
    if unknown:
        raise ToolError(
            f'delete takes only params={{"cascade": true}} (also delete what is built on it); got {unknown}',
            code="invalid_input",
            field_name="params",
        )
    cascade = bool(params.get("cascade", False))
    session.need("remove_feature", "op(delete=...)")
    with session.lock:
        executor = session.require_executor()
        steps = {s.id: s for s in session.steps()}
        plan_ops = {op.id for op in executor.plan.operations}
        wording = (intent or "").strip()
        if target in session.aliases:
            raise ToolError(
                f"{target} is a whole body adopted from the opened file, not a feature: delete one of its features by "
                'the CAD name (inspect(action="tree") lists them)',
                code="not_editable",
                field_name="delete",
            )
        step_id = session.next_id()
        if target in steps:
            step = steps[target]
            if target not in plan_ops or step.feature is None:
                raise ToolError(
                    f"{target} is a {step.op} step, not a plan op: undo(to=...) removes steps",
                    code="not_editable",
                    field_name="delete",
                )
            request = RemoveFeatureRequest(
                operation_id=step_id, step_feature=step.feature, cascade=cascade, intent=wording
            )
        else:
            request = RemoveFeatureRequest(operation_id=step_id, feature=target, cascade=cascade, intent=wording)
        before, _ = session.measure()
        try:
            result = session.adapter.remove_feature(request)
        except AdapterError as exc:
            raise _refused(session, exc, target, "delete") from exc
        head = f"deleted {target}"
        lines = _finish(session, step_id, "delete_feature", result, before, head, wording)
        removed = ", ".join(o.name for o in result.removed)
        if removed:
            lines.append(f"removed: {removed}")
        lines.extend(_tip_lines(session))
        lines.extend(session.state_lines(before))
        lines.append("view: tips shown, sketches and intermediate features hidden")
        return lines


def _refused(session: Session, exc: AdapterError, target: str, what: str) -> ToolError:
    if exc.info.code.value == "invalid_request":
        return _invalid(session, exc.info.message, "delete" if what == "delete" else "edit")
    return session.adapter_failure(exc, f"{what} {target}")


def _invalid(session: Session, message: str, field_name: str) -> ToolError:
    unknown = "no feature called" in message
    steps = ", ".join(s.id for s in session.steps())
    return ToolError(
        message + (f"; steps: {steps or 'none'}" if unknown else ""),
        code="unknown_step" if unknown else "not_editable",
        field_name=field_name,
        usage='inspect(action="tree") lists the steps and the CAD features',
    )


def edit_feature(session: Session, name: str, changes: dict[str, Any], intent: str | None) -> list[str]:
    """``op(edit=<CAD feature>)``: set properties of a feature the plan did not make (or any feature of a file)."""
    session.need("set_feature_properties", "op(edit=<CAD feature name>)")
    with session.lock:
        executor = session.require_executor()
        if not changes:
            raise ToolError(
                "edit needs params: the properties to set, by the CAD's names",
                code="invalid_input",
                field_name="params",
                usage='op(edit="<feature>", params={"Radius": 4}) | params={"edges": ["Edge12", ...]}',
            )
        wording = (intent or "").strip()
        old = _listing(session, name).features[0]
        owner = session.step_of_feature(old.feature.id) if old.feature is not None else None
        if owner is not None and owner in {op.id for op in executor.plan.operations}:
            raise ToolError(
                f"{old.name} was made by step {owner}: edit that step so the plan stays true: "
                f'op(edit="{owner}", params={{...}}) (the fields of the op: cad_guide(topic="ops"))',
                code="not_editable",
                field_name="edit",
            )
        step_id = session.next_id()
        before, _ = session.measure()
        try:
            result = session.adapter.set_feature_properties(
                SetFeaturePropertiesRequest(operation_id=step_id, feature=old.name, properties=changes, intent=wording)
            )
        except AdapterError as exc:
            raise _refused(session, exc, name, "edit") from exc
        lines = _finish(session, step_id, "set_properties", result, before, f"edited {old.name}", wording)
        if result.feature is None:
            return lines
        new = _listing(session, old.name).features[0]
        diffs = _changes(old, new)
        lines[0] += ": " + ("; ".join(diffs) if diffs else "no visible property changed")
        lines.extend(_tip_lines(session))
        lines.extend(session.state_lines(before))
        return lines


def focus(session: Session, ids: list[str]) -> str:
    session.need("focus_model", "view focus")
    bodies = tuple(session.body_handle(text, "ids") for text in ids)
    try:
        result = session.adapter.focus_model(FocusRequest(operation_id="focus", bodies=bodies))
    except AdapterError as exc:
        raise session.adapter_failure(exc, "focus") from exc
    shown = ", ".join(result.shown) or "nothing new"
    hidden = ", ".join(result.hidden) or "nothing new"
    return f"focus: shown {shown}; hidden {hidden} (display only)"
