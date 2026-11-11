"""The declared set (closed world) as the server applies it: what may be assigned, and what a measured part is checked
against. Everything here reads the memory on every call (nothing is cached), so a process or material the engineer just
declared counts at once."""

from typing import TYPE_CHECKING

from cotaforge.constraints import DeclaredSet, Finding, MeasuredPart, blocking
from cotaforge.memory import MemoryStore, base_memory_dir, project_memory_dir

from cotaforge_mcp.config import Settings
from cotaforge_mcp.reply import num

if TYPE_CHECKING:
    from cotaforge_mcp.session import Session

NATIVE_NOTE = (
    "native steps are in effect: the measures cover the geometry the steps and the file registered, not what a "
    "fragment made on its own"
)


def memory_store(settings: Settings) -> MemoryStore:
    """Project scope ``memory/`` in the design space, user scope under ``COTAFORGE_HOME`` (or ``~/.cotaforge``)."""
    return MemoryStore(
        project_dir=project_memory_dir(settings.workspace),
        user_dir=settings.user_memory_dir(),
        base_dir=base_memory_dir() if settings.base_memory else None,
        cad=settings.cad,
    )


def declared_set(settings: Settings) -> DeclaredSet:
    return DeclaredSet.from_store(memory_store(settings))


def material_rejection(declared: DeclaredSet, material: str) -> str | None:
    """Why ``material`` may not be assigned (``None``: it may)."""
    result = declared.validate_material(material)
    return None if result.ok else result.reason


def route_rejection(declared: DeclaredSet, route: tuple[str, ...]) -> str | None:
    result = declared.validate_route(route)
    return None if result.ok else result.reason


def measured_part(session: "Session") -> MeasuredPart:
    """The facts a check needs, read from the document (never from the plan or from what the agent said)."""
    attributes = session.design_attributes()
    extents = None
    try:
        _, bbox = session.measure()
        extents = tuple(float(x) for x in bbox.size())
    except Exception:  # noqa: BLE001 - an empty or unreadable part simply has no envelope
        extents = None
    if extents is not None and max(extents) <= 0:
        extents = None
    return MeasuredPart(
        material=attributes.material,
        route=attributes.route,
        bbox_extents_mm=extents,  # type: ignore[arg-type]
    )


def findings_for(session: "Session") -> tuple[Finding, ...]:
    """The checks of the document in its present state, with the honest downgrade for native steps."""
    from cotaforge.constraints import run_checks  # noqa: PLC0415

    findings = run_checks(declared_set(session.settings), measured_part(session))
    if not any(step.op == "native" for step in session.steps()):
        return findings
    out = []
    for finding in findings:
        if finding.check.startswith("envelope.") and finding.status == "pass":
            finding = finding.model_copy(update={"status": "unverified", "detail": f"{finding.detail}; {NATIVE_NOTE}"})
        out.append(finding)
    return tuple(out)


def render(findings: tuple[Finding, ...]) -> list[str]:
    lines = []
    for finding in findings:
        parts = [f"{finding.status.upper()}: {finding.check}"]
        if finding.measured is not None:
            parts.append(f"measured {_shown(finding.measured)}")
        if finding.limit is not None:
            parts.append(f"limit {_shown(finding.limit)}")
        line = " | ".join(parts) + f" - {finding.detail}"
        if finding.entry_id:
            line += f" [{finding.entry_id}{', ' + finding.reference if finding.reference else ''}]"
        lines.append(line)
    return lines


def _shown(value: float | str) -> str:
    return num(value) if isinstance(value, float) else str(value)


def violations(findings: tuple[Finding, ...]) -> tuple[Finding, ...]:
    return blocking(findings)


def check_lines(session: "Session") -> list[str]:
    """The report of the ``check`` tool: the declared-set findings plus the validity of the measured part."""
    declared = declared_set(session.settings)
    findings = findings_for(session)
    counts = {
        status: sum(1 for item in findings if item.status == status) for status in ("pass", "violation", "unverified")
    }
    lines = [f"check: {counts['violation']} violation, {counts['unverified']} unverified, {counts['pass']} pass"]
    if not declared.materials_declared and not declared.processes_declared:
        lines.append("nothing is declared (no process or material in the memory): nothing restricts the design")
    lines.extend(render(findings))
    valid, problems = session.validity()
    lines.append("geometry: valid" if valid else "geometry: NOT valid - " + session.problems_text(problems))
    lines.extend(session.adapter.check_notes())
    blocked = violations(findings)
    if blocked:
        lines.append(f"export: BLOCKED by {len(blocked)} violation(s); editing and doc save are still allowed")
    else:
        lines.append("export: allowed")
    return lines
