"""Checks of a measured part against the declared set (pure, deterministic).

Each check returns ``pass``, ``violation`` or ``unverified``. Honest-limit rule: whatever cannot be computed
from the facts in ``MeasuredPart`` is ``unverified``, never approximated. A ``violation`` blocks a release or
export; an ``unverified`` never blocks and is always shown. Values flagged ``placeholder`` (not measured yet)
are pending: they produce ``unverified``, never a ``violation``.

First-cut checks: assigned material, process route and the bounding-box envelope against
``max_diameter_mm`` / ``max_length_mm`` / ``build_volume_mm``.
"""

from collections.abc import Iterable
from typing import Literal

from pydantic import BaseModel, ConfigDict

from cotaforge.constraints.declared import DeclaredProcess, DeclaredSet
from cotaforge.memory import Capability

FindingStatus = Literal["pass", "violation", "unverified"]
Extents = tuple[float, float, float]

PLACEHOLDER_DETAIL = "placeholder value, not measured"
AXIS_TOLERANCE = 0.05  # extents within 5% of each other: the part has no identifiable axis
LIMIT_TOLERANCE_MM = 1e-6  # measurement noise allowed when comparing an extent with a limit
ENVELOPE_CAPABILITIES = ("max_diameter_mm", "max_length_mm", "build_volume_mm")


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class MeasuredPart(_Frozen):
    """The adapter-independent facts a check needs; every one is optional (None / empty = not known).

    ``route`` is the list of declared process ids assigned to the part; ``bbox_extents_mm`` are the three
    side lengths of its bounding box, in any order."""

    material: str | None = None
    route: tuple[str, ...] = ()
    bbox_extents_mm: Extents | None = None


class Finding(_Frozen):
    """One check outcome. ``entry_id`` / ``reference`` identify the declared entry that supplied the limit
    and the provenance reference of its numbers."""

    check: str
    status: FindingStatus
    measured: float | str | None = None
    limit: float | str | None = None
    detail: str
    entry_id: str | None = None
    reference: str | None = None


def blocking(findings: Iterable[Finding]) -> tuple[Finding, ...]:
    """Only the violations: the findings that block a release or export."""
    return tuple(finding for finding in findings if finding.status == "violation")


def _format_numbers(numbers: Iterable[float]) -> str:
    return " x ".join(f"{number:g}" for number in numbers)


def _sorted_extents(extents: Extents) -> Extents:
    longest, middle, shortest = sorted(extents, reverse=True)
    return longest, middle, shortest


# ---- material and route -----------------------------------------------------------------------


def _material_findings(declared: DeclaredSet, part: MeasuredPart) -> list[Finding]:
    if not declared.materials_declared:
        return []
    allowed = ", ".join(item.name for item in declared.materials)
    if part.material is None or not part.material.strip():
        detail = f"no material is assigned to the part; declared: {allowed}"
        return [Finding(check="material", status="unverified", detail=detail)]
    result = declared.validate_material(part.material)
    if not result.ok:
        return [
            Finding(check="material", status="violation", measured=part.material, limit=allowed, detail=result.reason)
        ]
    matched = next((item for item in declared.materials if item.entry_id == result.matched), None)
    return [
        Finding(
            check="material",
            status="pass",
            measured=part.material,
            limit=matched.name if matched else None,
            detail=result.reason,
            entry_id=result.matched,
            reference=matched.reference if matched else None,
        )
    ]


def _route_findings(declared: DeclaredSet, route: tuple[str, ...]) -> list[Finding]:
    if not declared.processes_declared:
        return []
    allowed = ", ".join(item.entry_id for item in declared.processes)
    if not route:
        detail = f"no process route is assigned to the part; declared: {allowed}"
        return [Finding(check="route", status="unverified", detail=detail)]
    result = declared.validate_route(route)
    status: FindingStatus = "pass" if result.ok else "violation"
    return [Finding(check="route", status=status, measured=", ".join(route), limit=allowed, detail=result.reason)]


# ---- envelope ---------------------------------------------------------------------------------


def _permissiveness(name: str, capability: Capability) -> tuple[float, ...]:
    """Sort key: a larger key is a more permissive limit (for a build volume: the larger volume)."""
    if name == "build_volume_mm":
        volume = 1.0
        for number in capability.numbers:
            volume *= number
        return (volume, *sorted(capability.numbers, reverse=True))
    return capability.numbers


def _limit_value(name: str, capability: Capability) -> float | str:
    if name == "build_volume_mm":
        return _format_numbers(sorted(capability.numbers, reverse=True))
    return capability.numbers[0]


def _axis_is_ambiguous(extents: Extents) -> bool:
    longest, _, shortest = extents
    return longest <= 0 or (longest - shortest) / longest <= AXIS_TOLERANCE


def _fits(name: str, capability: Capability, extents: Extents) -> bool:
    """``extents`` sorted descending. A scalar limit compares with the length (largest extent) or the
    diameter (larger of the other two); a build volume compares sorted extents with sorted limits."""
    if name == "build_volume_mm":
        limits = sorted(capability.numbers, reverse=True)
        return all(extent <= limit + LIMIT_TOLERANCE_MM for extent, limit in zip(extents, limits, strict=True))
    value = extents[0] if name == "max_length_mm" else extents[1]
    return value <= capability.numbers[0] + LIMIT_TOLERANCE_MM


def _measured_value(name: str, extents: Extents) -> tuple[float | str, str]:
    """What is compared with the limit, and how it was derived (for the finding's detail)."""
    if name == "build_volume_mm":
        return _format_numbers(extents), "sorted bounding-box extents against the sorted build volume"
    if name == "max_length_mm":
        return extents[0], "length = largest extent, the axis assumed along the longest extent"
    return extents[1], "diameter = larger of the two shorter extents, the axis assumed along the longest extent"


def _envelope_findings(
    name: str, candidates: list[tuple[DeclaredProcess, Capability]], bbox: Extents | None
) -> list[Finding]:
    """The finding(s) of one envelope capability across the route processes that declare it:
    a violation only if EVERY such process fails it, citing the most permissive limit."""
    check = f"envelope.{name}"
    findings = [
        Finding(
            check=check,
            status="unverified",
            detail=PLACEHOLDER_DETAIL,
            entry_id=process.entry_id,
            reference=process.reference,
        )
        for process, capability in candidates
        if capability.placeholder
    ]
    real = [(process, capability) for process, capability in candidates if not capability.placeholder]
    if not real:
        return findings
    if bbox is None:
        findings.append(Finding(check=check, status="unverified", detail="no bounding box has been measured"))
        return findings

    extents = _sorted_extents(bbox)
    by_permissiveness = sorted(real, key=lambda pair: _permissiveness(name, pair[1]), reverse=True)
    fitting = [pair for pair in by_permissiveness if _fits(name, pair[1], extents)]
    # one process that accommodates the part is enough; otherwise cite the most permissive limit
    best_process, best = (fitting or by_permissiveness)[0]
    limit = _limit_value(name, best)
    reference = best_process.reference
    if name != "build_volume_mm" and _axis_is_ambiguous(extents):
        detail = (
            f"the three extents ({_format_numbers(extents)}) are within {AXIS_TOLERANCE:.0%} of each other, "
            "so the axis of the part cannot be told"
        )
        findings.append(
            Finding(
                check=check,
                status="unverified",
                limit=limit,
                detail=detail,
                entry_id=best_process.entry_id,
                reference=reference,
            )
        )
        return findings

    measured, how = _measured_value(name, extents)
    if fitting:
        status: FindingStatus = "pass"
        detail = f"{measured} fits {name} = {limit} of process '{best_process.entry_id}' ({how})"
    else:
        all_fail = "; every process of the route that declares it fails" if len(real) > 1 else ""
        exceeds = f"{measured} exceeds {name} = {limit}, the most permissive ('{best_process.entry_id}'{all_fail})"
        if len(real) < len(candidates):
            # a process with a placeholder value has not failed: "every process fails" is not established
            status = "unverified"
            detail = f"{exceeds}; not a violation because a process with a placeholder value could accommodate it"
        else:
            status = "violation"
            detail = f"{exceeds} ({how})"
    findings.append(
        Finding(
            check=check,
            status=status,
            measured=measured,
            limit=limit,
            detail=detail,
            entry_id=best_process.entry_id,
            reference=reference,
        )
    )
    return findings


def run_checks(declared: DeclaredSet, part: MeasuredPart) -> tuple[Finding, ...]:
    """All first-cut checks of ``part`` against ``declared``. Nothing declared of a kind: no findings of it."""
    route = tuple(dict.fromkeys(step.strip() for step in part.route if step.strip()))
    findings = _material_findings(declared, part) + _route_findings(declared, route)
    route_processes = [item for item in (declared.process(step) for step in route) if item is not None]
    for process in route_processes:
        if process.known_tag is None:
            findings.append(
                Finding(
                    check="process_capabilities",
                    status="unverified",
                    detail=f"no automatic checks for process {process.tag}",
                    entry_id=process.entry_id,
                    reference=process.reference,
                )
            )
    known = [process for process in route_processes if process.known_tag is not None]
    for name in ENVELOPE_CAPABILITIES:
        candidates = [
            (process, capability) for process in known for capability in process.capabilities if capability.name == name
        ]
        if candidates:
            findings.extend(_envelope_findings(name, candidates, part.bbox_extents_mm))
    return tuple(findings)
