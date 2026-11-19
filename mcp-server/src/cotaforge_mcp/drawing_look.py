# ruff: noqa: E501
"""The agent looks at the sheet before it leaves: what a ``drawing(action=capture)`` returns, and the gate that export holds.

An agent that does not look at what it drew is wrong. The bench shows the agents DO capture (9 to 43 times per sheet) and
still send out a 260 dimension stacked outside a 340, texts that nearly touch and leaders across the part: a whole A3 sheet in
one image is 3.3 px per mm, and a 3.5 mm text is 12 px tall. So a capture is not one picture:

* the whole sheet (as before);
* when the sheet is dense, or something was measured on it, zoomed crops of the annotated area of each view and of every
  finding, about 10 px per mm (``cotaforge.adapters.freecad.worker.sheet_look`` chooses them), each labelled with its view and region;
* a plain-text list of what was MEASURED: crossings, overlaps, a stack in the wrong order. The agent reads it instead of trusting
  its eyes alone. Its source is one function of the worker (``sheet_look.layout_findings``): a better lint replaces that function
  and nothing here.

The gate: ``export`` of a sheet is refused unless a whole-sheet capture of it was taken after the last change to it. The change
counter is the sheet's own (``edits``: an item added or removed, a view added or removed, the title block, the layout; not an
answer, an approval, a review or a capture) and the steps of the model (a change of the part redraws the views). It holds in every
review mode and in both flows (item by item and the whole sheet in one order), because it reads the sheet and not the way it was made.
"""

from dataclasses import dataclass
from typing import Any

from cotaforge.contract import CaptureDrawingResult, DrawingSheetInfo

from cotaforge_mcp.reply import Image, ToolError
from cotaforge_mcp.session import Session

MAX_FINDINGS_SHOWN = 15
CAPTURE_USAGE = 'drawing(action="capture", sheet="<sheet>")'


@dataclass
class Look:
    """What the agent last saw of one sheet: the sheet's change counter and the model's steps at that capture, and whether
    the crops of that state were already returned."""

    edits: int
    model: tuple[str, ...]
    findings: int
    crops_given: bool = False


def _model(session: Session) -> tuple[str, ...]:
    """The steps of the model: a change of the part redraws the views of the sheet, so the sheet that was looked at is gone."""
    try:
        return tuple(str(s.id) for s in session.steps())
    except Exception:  # noqa: BLE001 - a session that cannot say its steps gates on the sheet's own counter only
        return ()


def _key(session: Session, sheet: str) -> tuple[str, str]:
    return session.doc_name, sheet


def seen(session: Session, sheet: str, edits: int) -> Look | None:
    """The look at ``sheet`` as it is now (same change counter, same model), or ``None``."""
    look = session.sheet_looks.get(_key(session, sheet))
    return look if look is not None and look.edits == edits and look.model == _model(session) else None


def crops_given_at(session: Session, sheet: str | None) -> int | None:
    """The change counter at which this session was last given the crops of sheet (of the only sheet it has looked at, when
    none is named), while the model is as it was then; None: send them."""
    looks = [
        look
        for (doc, name), look in session.sheet_looks.items()
        if doc == session.doc_name and (sheet is None or name == sheet)
    ]
    if len(looks) != 1:
        return None
    return looks[0].edits if looks[0].crops_given and looks[0].model == _model(session) else None


def record(session: Session, result: CaptureDrawingResult, crops_given: bool) -> None:
    """A whole-sheet capture was taken: from now until the sheet changes, it has been looked at."""
    sheet = result.sheet
    if not sheet:
        return
    previous = seen(session, sheet, result.edits)
    session.sheet_looks[_key(session, sheet)] = Look(
        result.edits, _model(session), len(result.findings), crops_given or (previous is not None and previous.crops_given)
    )


def crops_due(session: Session, result: CaptureDrawingResult, forced: bool | None) -> bool:
    """Whether the crops of this capture go to the agent: always when it asks (``crops=true``), never when it declines, and
    otherwise unless the sheet is exactly as it was when it last got them (the same crops again are tokens for nothing)."""
    if forced is not None:
        return forced
    previous = seen(session, result.sheet, result.edits) if result.sheet else None
    return not (previous is not None and previous.crops_given)


def require(session: Session, info: DrawingSheetInfo, action: str) -> None:
    """Refuse the export of ``info`` unless the agent has captured the whole sheet since its last change."""
    if "capture_drawing" not in session.capabilities.tools:
        return  # this CAD cannot capture a sheet: nothing to look at
    if seen(session, info.sheet, info.edits) is not None:
        return
    last = session.sheet_looks.get(_key(session, info.sheet))
    if last is None:
        why = "this session has not captured it"
    elif last.edits != info.edits:
        why = f"the last capture was at edit {last.edits} and the sheet is now at edit {info.edits}"
    else:
        why = "the part changed after the last capture (the views were redrawn)"
    raise ToolError(
        f"export {action} refused: nobody has looked at sheet {info.sheet} since its last change ({why}). The agent that drew "
        "it looks at it once, after its last change, before it leaves: capture it, read every image and what the capture "
        "measured, fix what is wrong, and capture again if you changed it. A capture of a region does not count: the whole sheet.",
        code="sheet_not_looked_at",
        field_name="sheet",
        usage=f'drawing(action="capture", sheet="{info.sheet}")',
        example=f'drawing(action="capture", sheet="{info.sheet}") then export(action="{action}", sheet="{info.sheet}")',
    )


# --- the reply of a capture ---------------------------------------------------------------------------------------------


def _box(box: tuple[float, float, float, float]) -> str:
    return f"x {box[0]:.0f}..{box[2]:.0f}, y {box[1]:.0f}..{box[3]:.0f} mm"


def _inside(box: tuple[float, float, float, float], region: tuple[float, float, float, float]) -> bool:
    return box[0] < region[2] and box[2] > region[0] and box[1] < region[3] and box[3] > region[1]


def findings_lines(
    result: CaptureDrawingResult, region: tuple[float, float, float, float] | None, with_crops: bool = True
) -> list[str]:
    """The measured list, in plain text: what, where on the sheet (so a region= can look at it) and in which crop."""
    shown = [(n, f) for n, f in enumerate(result.findings) if region is None or _inside(f.box, region)]
    if not shown:
        if region is not None:
            return [f"measured in this region: nothing ({len(result.findings)} on the whole sheet)"]
        return [
            "measured on this sheet: no overlap, crossing or stack out of order found among what the items drew "
            "(native TechDraw objects and what only the eye sees are not measured: look at the images)"
        ]
    in_crop: dict[int, int] = {}
    for position, crop in enumerate(result.zoomed if with_crops else (), start=2):  # image 1 is the whole sheet
        for n in crop.findings:
            in_crop.setdefault(n, position)
    lines = [f"measured on this sheet, {len(shown)} to fix or to answer (read each one in the images):"]
    for n, f in shown[:MAX_FINDINGS_SHOWN]:
        where = f"{_box(f.box)}" + (f"; image {in_crop[n]}" if n in in_crop else "")
        lines.append(f"  {n + 1}. {f.kind}: {f.text} [{where}]")
    if len(shown) > MAX_FINDINGS_SHOWN:
        lines.append(f"  ... and {len(shown) - MAX_FINDINGS_SHOWN} more (the worst are above): fix these and capture again")
    return lines


def images_lines(result: CaptureDrawingResult, with_crops: bool) -> list[str]:
    if not with_crops or not result.zoomed:
        return []
    lines = ["images: 1 = the whole sheet"]
    for position, crop in enumerate(result.zoomed, start=2):
        lines.append(f"  {position} = zoom, {crop.label}")
    if result.left_out:
        lines.append(f"not drawn (the cap on crops): {' | '.join(result.left_out)}")
    return lines


def attach(
    session: Session, result: CaptureDrawingResult, region: tuple[float, float, float, float] | None, forced: bool | None
) -> tuple[bool, list[str]]:
    """What a capture adds to the agent's picture: whether its crops go with it and the lines about the measured list and
    the images. A whole-sheet capture is recorded as the agent's look at the sheet."""
    whole = region is None
    before = seen(session, result.sheet, result.edits) if whole and result.sheet else None
    give = whole and result.zoomed != () and crops_due(session, result, forced)
    if whole:
        record(session, result, give)
    lines = findings_lines(result, region, give)
    lines += images_lines(result, give)
    if whole and not give and before is not None and before.crops_given:
        lines.append("zoomed crops of this sheet as it is were returned by the capture before: ask a region= to zoom into any part of it")
    return give, lines


def crop_images(result: CaptureDrawingResult, read: Any) -> list[Image]:
    """The crops as images, in the order the text numbers them (``read`` gives the bytes of a path)."""
    return [Image(read(crop.path)) for crop in result.zoomed]
