# ruff: noqa: E501
"""Where the views of a drawing sheet go (no FreeCAD import: plain arithmetic, unit-tested on its own).

First-angle projection (ISO): the front view at the anchor, the TOP view below it and the RIGHT view to its LEFT; the
isometric view takes the free space to the right. Third angle (ASME, the other half of the world): the TOP view above the
front one and the RIGHT view to its right. Room is kept for the overall dimensions beside the views, for the baseline
dimensions of the holes (``below_mm`` / ``above_mm`` / ``left_mm``: what the stacked rows need beyond the view) and, at the
bottom, for the title block. The largest standard scale (ISO 5455) at which everything fits is chosen unless one is asked
for.

A sheet may have ONE standard view that is not the front one (a plate seen from above): that view takes the front's place
(``has_top`` / ``has_right`` False; ``w`` and ``h`` are then what that view shows across and up the page, ``iso_dims`` the
part's own width, depth and height for the isometric view).

Coordinates are SVG page coordinates in mm (origin top left, y down); the caller converts to TechDraw's y up.
"""

import math
from collections.abc import Callable
from dataclasses import dataclass, replace

STANDARD_SCALES = (10.0, 5.0, 2.0, 1.0, 0.5, 0.2, 0.1, 0.05, 0.02, 0.01)
PAGES_MM = {
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "A0": (1189.0, 841.0),
    "Letter": (279.4, 215.9),
}  # landscape

SPACING_MM = 20.0  # between the front view and the top / right views
SPACING_MAX_MM = 2 * SPACING_MM  # ... the most the layout spreads them to when the sheet has room to spare
DIM_MARGIN_MM = 14.0  # a dimension line and its text beyond a view
ISO_GAP_MM = 12.0
AREA_LEFT_MM = 25.0  # the frame of the ISO 5457 templates starts at 20
AREA_RIGHT_MARGIN_MM = 15.0
AREA_TOP_MM = 15.0
TITLE_BLOCK_MM = 58.0  # height of the title block, counted from the frame's bottom edge (10 mm above the page's)
AREA_BOTTOM_PAD_MM = 5.0
FRAME_MARGIN_MM = 10.0  # the frame of the ISO 5457 templates is this far from the page's edge (right and bottom)
TITLE_BLOCK_TOP_MM = 58.0  # the top edge of the title block, from the page's bottom (y up): a parts list sits on it
TITLE_BLOCK_WIDTH_MM = 180.0
PARTS_ROW_MM = 6.0  # a row of the parts list; an assembly sheet keeps room for as many rows as it has parts

# Isometric view direction (towards the viewer) and the page's x axis in it, as TechDraw is told.
ISO_DIRECTION = (1.0, -1.0, 1.0)
ISO_XDIRECTION = (1.0, 1.0, 0.0)

Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class Layout:
    scale: float
    standard: bool  # False: no standard scale fits, ``scale`` is the largest that does
    fits: bool  # False: a scale was asked for and the views overflow the drawing area
    front: tuple[float, float]  # centre of the front view
    iso: tuple[float, float]  # centre of the isometric view
    spacing: float = SPACING_MM


@dataclass(frozen=True)
class Spec:
    """What the layout of one sheet depends on besides the scale: the views it has, the angle and the room the baseline
    dimensions need beyond the views."""

    w: float
    d: float
    h: float
    iso: tuple[float, float]  # the isometric view's width and height at scale 1, (0, 0) without one
    top: bool = True
    right: bool = True
    third: bool = False
    below_mm: float = 0.0
    above_mm: float = 0.0
    left_mm: float = 0.0
    right_mm: float = 0.0
    spacing: float = SPACING_MM  # between the front view and the top / right ones


def iso_extent(width: float, depth: float, height: float) -> tuple[float, float]:
    """Width and height of the isometric projection of a box, at scale 1: the box's eight corners on the view axes."""
    d = _unit(ISO_DIRECTION)
    r = _unit(ISO_XDIRECTION)
    up = (d[1] * r[2] - d[2] * r[1], d[2] * r[0] - d[0] * r[2], d[0] * r[1] - d[1] * r[0])
    xs, ys = [], []
    for x in (0.0, width):
        for y in (0.0, depth):
            for z in (0.0, height):
                xs.append(x * r[0] + y * r[1] + z * r[2])
                ys.append(x * up[0] + y * up[1] + z * up[2])
    return max(xs) - min(xs), max(ys) - min(ys)


def _unit(v: tuple[float, float, float]) -> tuple[float, float, float]:
    n = math.sqrt(sum(c * c for c in v))
    return v[0] / n, v[1] / n, v[2] / n


def drawing_area(page_w: float, page_h: float) -> tuple[float, float, float, float]:
    """(left, top, right, bottom) of the area the views may use, above the title block."""
    return (
        AREA_LEFT_MM,
        AREA_TOP_MM,
        page_w - AREA_RIGHT_MARGIN_MM,
        page_h - 10.0 - TITLE_BLOCK_MM - AREA_BOTTOM_PAD_MM,
    )


def _pads(s: Spec, scale: float) -> tuple[float, float, float, float]:
    """The room kept round the group of standard views, ``(left, right, above, below)``: for the overall dimensions and the
    baseline rows of the holes. The right view in first angle (the top view in third) and the spacing between the views
    already give the room a row needs beside them, so only what sticks out beyond is added."""
    if s.third:
        above = max(DIM_MARGIN_MM, s.above_mm) if s.top else DIM_MARGIN_MM
        below = DIM_MARGIN_MM  # the overall width of the front view goes below it, the top view is above
        left = max(DIM_MARGIN_MM, s.left_mm)
    else:
        above = max(DIM_MARGIN_MM, s.above_mm)
        below = max(0.0, s.below_mm)
        if s.right:  # the rows left of the top view lie under the right view: they only add what sticks out beyond it
            left = max(0.0, s.left_mm - (s.d * scale + s.spacing))
        else:
            left = max(DIM_MARGIN_MM, s.left_mm)
    return left, DIM_MARGIN_MM + max(0.0, s.right_mm), above, below


def _extents(scale: float, s: Spec) -> Box:
    """``(block width, block height, front x offset, front y offset)`` of the group of standard views and its room at
    ``scale``: the offsets are where the front view's centre is, counted from the block's top left corner."""
    left, right_pad, above, below = _pads(s, scale)
    if s.third:
        cols = (s.w + (s.d if s.right else 0.0)) * scale + (s.spacing if s.right else 0.0)
        rows = (s.h + (s.d if s.top else 0.0)) * scale + (s.spacing if s.top else 0.0)
        front_x = left + s.w * scale / 2
        front_y = above + ((s.d * scale + s.spacing) if s.top else 0.0) + s.h * scale / 2
    else:
        cols = (s.w + (s.d if s.right else 0.0)) * scale + (s.spacing if s.right else 0.0)
        rows = (s.h + (s.d if s.top else 0.0)) * scale + (s.spacing if s.top else 0.0)
        front_x = left + ((s.d * scale + s.spacing) if s.right else 0.0) + s.w * scale / 2
        front_y = above + s.h * scale / 2
    return left + cols + right_pad, above + rows + below, front_x, front_y


def _needed_spec(scale: float, s: Spec) -> tuple[float, float]:
    """(width, height) the views, their dimensions and the isometric view take at ``scale``."""
    block_w, block_h, _, _ = _extents(scale, s)
    gap = ISO_GAP_MM if s.iso[0] > 0 else 0.0  # no isometric view: nothing to the right of the group
    return block_w + gap + s.iso[0] * scale, max(block_h, s.iso[1] * scale + DIM_MARGIN_MM if s.iso[0] > 0 else block_h)


def _drop(needed_h: float, area_h: float | None) -> float:
    """How far the whole block goes down from the top of the drawing area so it is centred between the top margin and the
    bottom (the title block, or the parts list of an assembly): half of the spare height; none when it does not fit or no
    height is given."""
    return 0.0 if area_h is None else max(0.0, (area_h - needed_h) / 2)


def _blocks_spec(
    scale: float, s: Spec, left: float, area_w: float, top: float, area_h: float | None = None
) -> list[Box]:
    """The page rectangles (y down) of the left group (front, top, right and their dimensions) and of the isometric view
    when everything is centred across the drawing area (and down it, when ``area_h`` -- the height the block may use -- is
    given: without it the block hangs from ``top``)."""
    needed = _needed_spec(scale, s)
    start = _start(left, area_w, needed[0], s.iso)
    top += _drop(needed[1], area_h)
    block_w, block_h, _, _ = _extents(scale, s)
    iso_x = start + block_w + ISO_GAP_MM
    blocks = [(start, top, start + block_w, top + block_h)]
    if s.iso[0] > 0:
        blocks.append((iso_x, top, iso_x + s.iso[0] * scale, top + s.iso[1] * scale + DIM_MARGIN_MM))
    return blocks


def _needed(
    scale: float, w: float, d: float, h: float, iso: tuple[float, float], top: bool = True, right: bool = True
) -> tuple[float, float]:
    """(width, height) the views, their dimensions and the isometric view of a first-angle sheet take at scale."""
    return _needed_spec(scale, Spec(w, d, h, iso, top, right))


def _blocks(
    scale: float,
    w: float,
    d: float,
    h: float,
    iso: tuple[float, float],
    left: float,
    area_w: float,
    top: float,
    has_top: bool = True,
    has_right: bool = True,
    area_h: float | None = None,
) -> list[Box]:
    """The page rectangles of the views of a first-angle sheet and of the isometric one (see _blocks_spec)."""
    return _blocks_spec(scale, Spec(w, d, h, iso, has_top, has_right), left, area_w, top, area_h)


def _spread(spec: Spec, fits: Callable[[Spec], bool]) -> Spec:
    """``spec`` with the views spaced out when there is room: the spacing between the front view and the top / right ones
    grows to ``SPACING_MAX_MM`` (else half way to it) as long as everything still fits, so the sheet reads evenly and a
    dimension fits between the views. A sheet with a single standard view has no gap to widen."""
    if not (spec.top or spec.right) or spec.spacing >= SPACING_MAX_MM:
        return spec
    for spacing in (SPACING_MAX_MM, (SPACING_MAX_MM + spec.spacing) / 2):
        wider = replace(spec, spacing=spacing)
        if fits(wider):
            return wider
    return spec


def _start(left: float, area_w: float, needed_w: float, iso: tuple[float, float]) -> float:
    """Where the group starts: centred across the area; without an isometric view it stays at the left, so the room that
    is left over is one piece (an exploded view takes it)."""
    return left + (max(0.0, area_w - needed_w) / 2 if iso[0] > 0 else 0.0)


def _clear_of(blocks: list[Box], reserved: Box) -> bool:
    return not any(
        b[0] < reserved[2] and reserved[0] < b[2] and b[1] < reserved[3] and reserved[1] < b[3] for b in blocks
    )


def plan(
    w: float,
    d: float,
    h: float,
    page_w: float,
    page_h: float,
    scale: float | None = None,
    reserve: tuple[float, float] | None = None,
    isometric: bool = True,
    has_top: bool = True,
    has_right: bool = True,
    *,
    third: bool = False,
    below_mm: float = 0.0,
    above_mm: float = 0.0,
    left_mm: float = 0.0,
    right_mm: float = 0.0,
    spacing_mm: float = SPACING_MM,
    iso_dims: tuple[float, float, float] | None = None,
    _comfort_checked: bool = False,
) -> Layout:
    """The layout of a part ``w`` (x) by ``d`` (y) by ``h`` (z) mm on a page; ``scale`` is the ratio asked for
    (0.5 = 1:2), omitted: the largest standard one that fits. ``reserve`` = (width, height) keeps that much room for a
    table (the parts list of an assembly) on the title block's top edge, at the right: the views stay clear of it.
    ``has_top`` / ``has_right`` / ``isometric`` say which views the sheet has: a view that is not there takes no room,
    so the others are laid out again and the scale grows to the largest standard one that fits. ``third`` is third-angle
    projection; ``below_mm`` / ``above_mm`` / ``left_mm`` the room the stacked baseline dimensions of the holes need beyond
    the views; ``iso_dims`` the part's size for the isometric view when ``w``, ``d`` and ``h`` are those of another view."""
    if scale is None and not _comfort_checked and (right_mm or spacing_mm != SPACING_MM):
        # the room for the callouts is a comfort: a scale smaller than the sheet would have without it is not worth it
        args = (w, d, h, page_w, page_h, None, reserve, isometric, has_top, has_right)
        keep = {"third": third, "below_mm": below_mm, "above_mm": above_mm, "left_mm": left_mm, "iso_dims": iso_dims}
        roomy = plan(*args, **keep, right_mm=right_mm, spacing_mm=spacing_mm, _comfort_checked=True)
        tight = plan(*args, **keep, _comfort_checked=True)
        return roomy if roomy.scale >= tight.scale else tight
    left, top, right, bottom = drawing_area(page_w, page_h)
    area_w, area_h = right - left, bottom - top
    iso = iso_extent(*(iso_dims or (w, d, h))) if isometric else (0.0, 0.0)
    spec = Spec(w, d, h, iso, has_top, has_right, third, below_mm, above_mm, left_mm, right_mm, spacing_mm)
    held: Box | None = None
    if reserve is not None:
        held = (
            page_w - FRAME_MARGIN_MM - reserve[0] - 2.0,
            page_h - TITLE_BLOCK_TOP_MM - reserve[1] - 2.0,
            page_w - FRAME_MARGIN_MM,
            page_h - TITLE_BLOCK_TOP_MM,
        )

    # the block is centred between the top margin and the bottom of the free area (the parts list of an assembly, when
    # there is one, is the bottom: a block that only fits beside it hangs from the top as it always did)
    free_h = (bottom if held is None else min(bottom, held[1])) - top

    def fits_at(s: float, sp: Spec = spec) -> bool:
        if not _fits(_needed_spec(s, sp), area_w, area_h):
            return False
        return held is None or _clear_of(_blocks_spec(s, sp, left, area_w, top, free_h), held)

    standard = True
    if scale is None:
        chosen = next((s for s in STANDARD_SCALES if fits_at(s)), None)
        if chosen is None:
            standard = False
            # no standard scale fits: solve for the largest one that does (every term is linear in the scale, except
            # the room a left row takes beside the right view, which only shrinks the need)
            n0, n1 = _needed_spec(0.0, spec), _needed_spec(1.0, spec)
            slope_w, slope_h = max(n1[0] - n0[0], 1e-9), max(n1[1] - n0[1], 1e-9)
            chosen = max(1e-6, min((area_w - n0[0]) / slope_w, (area_h - n0[1]) / slope_h))
            for _ in range(60):  # the table still in the way: a little smaller until it is not
                if fits_at(chosen) or chosen <= 1e-6:
                    break
                chosen *= 0.95
        scale = chosen
    fits = fits_at(scale)
    if fits:  # room to spare: the views move apart (the scale stays what it was)
        spec = _spread(spec, lambda sp: fits_at(scale, sp))
    needed = _needed_spec(scale, spec)
    start = _start(left, area_w, needed[0], iso)  # everything centred across the drawing area
    top += _drop(needed[1], free_h)  # ... and down it
    block_w, _, front_x, front_y = _extents(scale, spec)
    front = (start + front_x, top + front_y)
    iso_centre = (start + block_w + ISO_GAP_MM + iso[0] * scale / 2, top + DIM_MARGIN_MM + iso[1] * scale / 2)
    return Layout(scale=scale, standard=standard, fits=fits, front=front, iso=iso_centre, spacing=spec.spacing)


AUTO_PAPERS = ("A4", "A3", "A2", "A1", "A0")
FULL_SIZE_PAPERS = 2  # a part is first tried at full size on the papers a shop prints every day: A4 and A3
HALF_SIZE = 0.5  # ... then at the largest paper that does not make it smaller than 1:2


def auto_paper(
    w: float,
    d: float,
    h: float,
    isometric: bool = True,
    has_top: bool = True,
    has_right: bool = True,
    *,
    third: bool = False,
    iso_dims: tuple[float, float, float] | None = None,
) -> tuple[str, float]:
    """The paper and the standard scale (ISO 5455) a sheet gets when nobody chose a paper, so the views fill it instead of
    floating on a sheet too big for them or shrinking on one too small: the smallest of A4 and A3 on which the automatic
    layout reaches full size or an enlargement; else the smallest paper on which it does not go below 1:2; else (a part of
    several metres) the paper with the largest scale, the smaller one on a tie. ``w`` / ``d`` / ``h`` are the sizes of the
    views as ``plan`` takes them."""
    scales = {
        name: plan(w, d, h, *PAGES_MM[name], None, None, isometric, has_top, has_right, third=third, iso_dims=iso_dims).scale
        for name in AUTO_PAPERS
    }
    for name in AUTO_PAPERS[:FULL_SIZE_PAPERS]:
        if scales[name] >= 1.0 - 1e-9:
            return name, scales[name]
    for name in AUTO_PAPERS:
        if scales[name] >= HALF_SIZE - 1e-9:
            return name, scales[name]
    best = max(AUTO_PAPERS, key=lambda name: (round(scales[name], 9), -AUTO_PAPERS.index(name)))
    return best, scales[best]


def _fits(needed: tuple[float, float], area_w: float, area_h: float) -> bool:
    return needed[0] <= area_w + 1e-9 and needed[1] <= area_h + 1e-9
