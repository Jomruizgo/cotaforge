"""What a customer's file is, and what ``doc(action=import)`` tells the engineer about it."""

from pathlib import Path

from cotaforge.contract import DEFAULT_SEW_TOLERANCE_MM, ImportedBody, ImportGeometryResult

from cotaforge_mcp.overflow import Overflow
from cotaforge_mcp.reply import ToolError, num, vec

IMPORT_SUFFIXES = (".step", ".stp", ".iges", ".igs")
PARASOLID_SUFFIXES = (".x_t", ".x_b", ".xmt_txt", ".xmt_bin")
FORMAT_NAMES = {"step": "STEP", "iges": "IGES", "parasolid": "Parasolid"}
MAX_BODIES_LISTED = 6

NO_HISTORY = (
    "the bodies are references: they carry no feature history, so op(edit) cannot change them. Change what is there "
    "by adding material or cuts on top (sketch on one of their faces + extrude/cut, hole, fillet, chamfer) or, for a "
    "defeaturing, with run_native; insert the part in an assembly with insert_component (part = this document's name)"
)


def readable(extra: tuple[str, ...] = ()) -> tuple[str, ...]:
    """Every suffix import reads: STEP and IGES, then what the CAD adds (``Adapter.import_suffixes``)."""
    return (*IMPORT_SUFFIXES, *(s for s in extra if s not in IMPORT_SUFFIXES))


def import_usage(extra: tuple[str, ...] = ()) -> str:
    """The import call with the suffixes this CAD reads."""
    return f'doc(action="import", path="<file>{"|".join(readable(extra))}", name="<document name>")'


def check_suffix(source: Path, extra: tuple[str, ...] = (), cad: str = "FreeCAD") -> None:
    """Refuse a file the CAD cannot read, saying what to ask the supplier for. ``extra`` are the suffixes this CAD
    reads beyond STEP and IGES (``Adapter.import_suffixes``); ``cad`` is how the text names it."""
    suffix = source.suffix.lower()
    if suffix in IMPORT_SUFFIXES or suffix in extra:
        return
    if suffix in PARASOLID_SUFFIXES:
        reads = [s for s in extra if s in PARASOLID_SUFFIXES]
        if reads:  # this CAD reads Parasolid, but not this variant of it
            why = f"{cad} reads {', '.join(reads)} but not {suffix}"
        elif cad == "FreeCAD":
            why = "FreeCAD has no Parasolid reader (OpenCASCADE has no free one)"
        else:
            why = f"{cad} has no Parasolid reader"
        raise ToolError(
            f"{source.name} is a Parasolid file ({suffix}): {why}, so it cannot be imported. Ask the supplier for a "
            "STEP file (AP214 or AP242) or convert it: a free "
            "online CAD account that imports Parasolid exports STEP, and so does any commercial CAD software seat",
            code="unsupported_format",
            field_name="path",
            usage=import_usage(extra),
        )
    raise ToolError(
        f"import reads {', '.join(readable(extra))} files, not {suffix or 'a file without suffix'}",
        code="unsupported_format",
        field_name="path",
        usage=import_usage(extra),
    )


def _body_row(label: str, body: ImportedBody) -> str:
    kind = f"{body.solids} solid{'' if body.solids == 1 else 's'}" if body.solids else "NO solid (surfaces)"
    if body.valid is None:
        state = "validity not checked"
    else:
        state = "valid" if body.valid else "NOT valid"
    state += ", closed" if body.closed else ", NOT closed"
    left_out = body.bbox_faces_left_out
    volume = f"volume {num(body.volume_mm3)} mm3" if body.solids else "volume none (surface)"
    box = f"size {vec(body.bbox.size())}, min {vec(body.bbox.min)}"
    if left_out:
        box += f" (without {left_out} face{'' if left_out == 1 else 's'} of no real size)"
    return (
        f"  {label}: {kind}, {body.shells} shell{'' if body.shells == 1 else 's'}, {body.faces} faces, "
        f"{volume}, {box}, {state}"
    )


def surface_only_wording(count: int, solids_exist: bool) -> str:
    """What follows a total volume that some bodies cannot add to: they hold no solid."""
    if not solids_exist:
        return "no solid in the document: surfaces have no volume"
    return f"counts solids only; {count} surface-only {'body adds' if count == 1 else 'bodies add'} none"


def _names(labels: list[str], shown: int = 3) -> str:
    if len(labels) <= shown:
        return ", ".join(labels)
    return f"{', '.join(labels[:shown])} and {len(labels) - shown} more"


def _sewing_lines(result: ImportGeometryResult) -> list[str]:
    """What sewing did, or -- for a file that stayed surfaces -- that it is available."""
    sewing = result.sewing
    if sewing is not None:
        return [
            f"sewing: {sewing.faces} loose faces joined at {num(sewing.tolerance_mm)} mm in {num(sewing.seconds)} s: "
            f"{sewing.shells} shells, {sewing.closed_shells} closed -> {sewing.solids} solids (the volume counts "
            f"these only); {sewing.free_edges} free edges (gaps) remain in what is still surface"
        ]
    surfaces = sum(1 for body in result.bodies if body.solids == 0)
    if not surfaces:
        return []
    sew_hint = (
        f"doc(action=import, ..., sew=true, sew_tolerance_mm={DEFAULT_SEW_TOLERANCE_MM:g}) joins the faces into shells "
        "and makes a solid of each closed one"
    )
    if surfaces == len(result.bodies):  # nothing in the file has volume
        return [
            f"note: the file holds surfaces, not solids (their volume is not meaningful). {sew_hint}; a surface body "
            "still serves as a reference (inspect interference measures its distance)"
        ]
    return [
        f"note: {surfaces} of {len(result.bodies)} bodies hold surfaces only, no solid: the total volume counts the "
        f"solids only. {sew_hint}"
    ]


def _warning_lines(result: ImportGeometryResult, labels: dict[str, str], report: Overflow) -> list[str]:
    """What the engineer must know before trusting the numbers, one line per kind of problem (never one per body: a
    large assembly would bury the totals). The names of all the bodies of each kind are kept for the report file."""
    lines: list[str] = []

    def label(body: ImportedBody) -> str:
        return labels.get(body.body.id, body.body.id)

    flat = [b for b in result.bodies if b.solids == 0]
    open_shells = [b for b in result.bodies if b.solids and not b.closed]
    invalid = [b for b in result.bodies if b.valid is False]
    unchecked = [b for b in result.bodies if b.valid is None]
    if flat:
        names = [label(b) for b in flat]
        report.text("bodies without a solid", "\n".join(names), 10**9)
        one = len(names) == 1
        if len(flat) == len(result.bodies):
            tail = "its volume is not meaningful, and neither is the total volume below"
            if not one:
                tail = "their volume is not meaningful, and neither is the total volume below"
        else:
            tail = f"{'it adds' if one else 'they add'} no volume (the total volume below counts the solids only)"
        lines.append(
            f"warning: {_names(names)} {'has' if one else 'have'} surfaces or open shells only, no solid: {tail}"
        )
    if open_shells:
        names = [label(b) for b in open_shells]
        report.text("bodies with a shell that is not closed", "\n".join(names), 10**9)
        one = len(names) == 1
        lines.append(
            f"warning: {_names(names)} {'has' if one else 'have'} a shell that is not closed: "
            f"{'its' if one else 'their'} volume cannot be trusted"
        )
    if invalid:
        names = [label(b) for b in invalid]
        report.text("bodies that fail the geometry check", "\n".join(names), 10**9)
        one = len(names) == 1
        lines.append(
            f"warning: {_names(names)} {'fails' if one else 'fail'} the CAD's geometry check (not valid): "
            "measures may be wrong"
        )
    garbage = [b for b in result.bodies if b.bbox_faces_left_out]
    if garbage:
        names = [f"{label(b)} ({b.bbox_faces_left_out})" for b in garbage]
        report.text("bodies whose box leaves faces out", "\n".join(names), 10**9)
        lines.append(
            f"warning: the box of {_names(names)} leaves out faces that have no real size (an unbounded face of an "
            "invalid shape): the sizes and the bbox below do not include them"
        )
    if unchecked:
        names = [label(b) for b in unchecked]
        report.text("bodies whose validity was not checked", "\n".join(names), 10**9)
        lines.append(
            f"warning: validity of {_names(names)} was not checked (thousands of loose faces: the check takes longer "
            "than the reading); inspect validity checks it"
        )
    lines.extend(f"warning: {w}" for w in result.warnings)
    return lines


def report_lines(
    source: Path, result: ImportGeometryResult, labels: dict[str, str], report: Overflow
) -> tuple[list[str], list[str]]:
    """The measured facts of an import: ``(head, rows)``. ``head`` is what must never be cut (what was read, the totals,
    the notes and the warnings); ``rows`` are the first bodies, and the line that says where the rest is
    (``report``'s file: written by the caller when anything was cut). ``labels`` maps a body id to the name the agent
    builds on."""
    file_format = FORMAT_NAMES.get(result.source_format, result.source_format or "?")
    lines = [
        f"imported: {source.name} ({file_format}): {len(result.bodies)} bod{'y' if len(result.bodies) == 1 else 'ies'}"
    ]
    if result.source_units is None:
        lines.append("units: the file does not declare its length unit (read as the CAD reads it; geometry is in mm)")
    elif result.source_units == "mm":
        lines.append("units: the file declares mm")
    else:
        lines.append(f"units: the file declares {result.source_units}; converted to mm")
    lines.append("coordinates: kept exactly as in the file (no recentring)")
    solids = sum(b.solids for b in result.bodies)
    shells = sum(b.shells for b in result.bodies)
    faces = sum(b.faces for b in result.bodies)
    lines.append(f"content: {solids} solids, {shells} shells, {faces} faces")
    lines.extend(f"note: {note}" for note in result.notes)
    if any(not b.bbox_exact for b in result.bodies):
        lines.append(
            "note: sizes and corners are quick boxes (they may enclose a little more than the shape); "
            "inspect measure is exact"
        )
    lines.extend(_sewing_lines(result))
    lines.extend(_warning_lines(result, labels, report))
    rows = [_body_row(labels.get(body.body.id, body.body.id), body) for body in result.bodies]
    shown, more = report.items("bodies", rows, MAX_BODIES_LISTED)
    return lines, [*shown, *([f"  ... {more}"] if more else [])]
