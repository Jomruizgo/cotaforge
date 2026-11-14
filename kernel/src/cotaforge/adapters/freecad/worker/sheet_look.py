# ruff: noqa: E501
"""What the agent is shown when it looks at a sheet (no FreeCAD import, unit-testable on its own).

A capture of a whole A3 sheet is 1400 px wide: 3.3 px per mm. A 3.5 mm text is 12 px tall and a 1 mm overlap is 3 px, so a
dimension stacked wrong or two texts that nearly touch are in the picture and still go unseen. The agent is given two more things:

* ``layout_findings``: the measured list of what lies on what in THIS capture (a crossing, an overlap, a stack in the wrong
  order, text on the outline of the part), each with the box on the sheet where to look. It is the ONE function that
  produces the list: its source today is what the items measured while the sheet was composed (``sheet_collide``,
  ``sheet_viewrules``, the leaders of ``ctx.extras``); a lint that reads the finished sheet whatever drew it can replace the
  body of the function and nothing else (the shape ``LayoutFinding`` is the contract with the capture).
* ``select_crops``: zoomed crops (about ``TARGET_PX_PER_MM`` px per mm) of the annotated area of each view, and of every
  finding the crops of the views do not already contain, capped by count and by size so the cost in tokens stays bounded.

Why 10 px per mm: a 3.5 mm text is 35 px tall and a 0.5 mm line 5 px wide, so a 1 mm overlap is 10 px. The floor is 6: a
view up to 166 mm wide fits one crop of 1000 px; below 6 px per mm a 3.5 mm text would be under 21 px and a 1 mm overlap
under 6, so the area is cut into tiles instead of shrunk (tiles cost more pixels; a crop that cannot be read costs the capture). A crop is at most ``MAX_CROP_PX`` px on its long side (about
1000 tokens as an image); at most ``MAX_CROPS`` of them go with one capture.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

Rect = tuple[float, float, float, float]

TARGET_PX_PER_MM = 10.0
MIN_PX_PER_MM = 6.0  # below this an area is cut into tiles instead of shrunk
FINDING_MIN_PX_PER_MM = 5.0  # a finding box wider than this allows is drawn smaller, never cut
MAX_CROP_PX = 1000
MAX_CROPS = 5
DENSE_MIN_ANNOTATIONS = 6  # a sheet with fewer annotation units than this is read whole: no view crops
VIEW_MIN_ANNOTATIONS = 2  # a view with fewer is not worth a crop of its own
MARGIN_MM = 40.0  # how far from a view an annotation still belongs to it
PAD_MM = 4.0
TILE_OVERLAP_MM = 8.0
POINT_BOX_MM = (8.0, 6.0)  # half width and height of the box around a finding that is a point


PRIORITY = {"overlap": 0, "stack_order": 1, "stack_gap": 2, "outside": 3, "crossing": 4}


@dataclass(frozen=True)
class LayoutFinding:
    """One thing measured on the sheet: ``kind`` (``overlap``, ``crossing``, ``stack_order``, ``stack_gap``, ``outside``),
    the sentence for the agent, the ``box`` on the sheet (page mm, y up) where it can be seen, the view it is in and the
    items it names."""

    kind: str
    text: str
    box: Rect
    view: str = ""
    items: tuple[str, ...] = ()


@dataclass(frozen=True)
class Crop:
    """A part of the sheet to draw zoomed: where (``region``), at how many px per mm, what it is (``label``, ``view``) and
    the indexes of the findings it holds."""

    label: str
    view: str
    region: Rect
    px_per_mm: float
    findings: tuple[int, ...] = ()
    annotations: int = 0

    @property
    def width_px(self) -> int:
        return max(64, round((self.region[2] - self.region[0]) * self.px_per_mm))


# --- the measured list ------------------------------------------------------------------------------------------------------


def _around(point: tuple[float, float], half: tuple[float, float] = POINT_BOX_MM) -> Rect:
    return point[0] - half[0], point[1] - half[1], point[0] + half[0], point[1] + half[1]


def _union(boxes: Sequence[Rect]) -> Rect:
    return min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes)


def _grow(box: Rect, by: float) -> Rect:
    return box[0] - by, box[1] - by, box[2] + by, box[3] + by


def layout_findings(ctx: object) -> list[LayoutFinding]:
    """What lies wrong on the sheet that ``ctx`` describes, as measured now. THIS is the function a better source replaces:
    it takes the composed context and returns findings with a box each; nothing else of the capture reads the context."""
    out: list[LayoutFinding] = []
    out += _collisions(ctx)
    out += _dimension_findings(ctx)
    out += _leader_findings(ctx)
    return sorted(_unique(out), key=lambda f: PRIORITY.get(f.kind, len(PRIORITY)))  # the worst first: a list is read from the top


def _unique(found: list[LayoutFinding]) -> list[LayoutFinding]:
    seen: set[tuple[str, str]] = set()
    out: list[LayoutFinding] = []
    for f in found:
        key = (f.kind, f.text)
        if key not in seen:
            seen.add(key)
            out.append(f)
    return out


def _collisions(ctx: object) -> list[LayoutFinding]:
    """Ink of one item on the ink of another, or a text on the outline of a view (``sheet_collide``)."""
    out: list[LayoutFinding] = []
    for c in getattr(ctx, "extras", {}).get("collisions", []):  # type: ignore[attr-defined]
        if c.kind == "outside_frame":
            kind, text = "outside", f"{c.a} lies outside the drawing area"
        else:
            kind = "crossing" if c.kind in ("line_text", "text_line", "line_frame", "frame_line") else "overlap"
            text = f"{c.a} on {c.b}: {c.what or c.kind}"
        items = tuple(k for k in dict.fromkeys((c.a, c.b)) if k not in ("views", "sheet"))
        out.append(LayoutFinding(kind, text, _around(c.at), items=items))
    return out


def _dimension_findings(ctx: object) -> list[LayoutFinding]:
    import sheet_viewrules as rules  # noqa: PLC0415 - the rules of the dimension stack (no FreeCAD import)

    extras = getattr(ctx, "extras", {})
    shapes = extras.get("dim_shapes", [])
    if len(shapes) < 2:
        return []
    views = {**ctx.views, **extras.get("sections", {})}  # type: ignore[attr-defined]
    out: list[LayoutFinding] = []
    for f in rules.dimension_crossings(shapes):
        who = (
            f"a dimension of {f.owner} crosses another of its own"
            if f.owner == f.other
            else f"the extension lines of {f.owner} cross the dimension line of {f.other}"
        )
        text = f"{f.view} view: {who}"
        out.append(LayoutFinding("crossing", text, _around(f.at), f.view, tuple(dict.fromkeys((f.owner, f.other)))))
    by_owner = {(d.owner, d.view): d for d in shapes}
    for s in rules.dimension_stacks(shapes, {n: v.box for n, v in views.items()}):
        lines = [
            line
            for line in (
                rules.dimension_line(d) for d in (by_owner.get((s.owner, s.view)), by_owner.get((s.other, s.view))) if d
            )
            if line is not None
        ]
        if not lines:
            continue
        box = _grow(_union([(min(a[0], a[2]), min(a[1], a[3]), max(a[0], a[2]), max(a[1], a[3])) for a in lines]), 5.0)
        if s.kind == "order":
            text = (
                f"{s.view} view ({s.side}): {s.owner} ({s.owner_mm:.0f} mm) stands nearer the part than {s.other} "
                f"({s.other_mm:.0f} mm) and is longer: the short one goes inside and the long one outside, or the extension "
                "lines of the short one cross the line of the long one"
            )
            out.append(LayoutFinding("stack_order", text, box, s.view, (s.owner, s.other)))
        else:
            text = (
                f"{s.view} view ({s.side}): the lines of {s.owner} and {s.other} run {s.gap_mm:.1f} mm apart: their texts "
                "nearly touch"
            )
            out.append(LayoutFinding("stack_gap", text, box, s.view, (s.owner, s.other)))
    return out


def _leader_findings(ctx: object) -> list[LayoutFinding]:
    """Two leaders that cross, a leader through the outline of the part more than once, a leader across a dimension."""
    from items_common import view_segments  # noqa: PLC0415
    from sheet_collide import ink_of, segments_cross  # noqa: PLC0415

    extras = getattr(ctx, "extras", {})
    leaders = extras.get("leaders", [])
    if not leaders:
        return []

    def parts(leader: Mapping[str, object]) -> list[tuple[float, float, float, float]]:
        tip, elbow, end = leader["tip"], leader["elbow"], leader["end"]
        return [(tip[0], tip[1], elbow[0], elbow[1]), (elbow[0], elbow[1], end[0], end[1])]  # type: ignore[index]

    def box_of(pieces: Sequence[tuple[float, float, float, float]]) -> Rect:
        xs = [v for p in pieces for v in (p[0], p[2])]
        ys = [v for p in pieces for v in (p[1], p[3])]
        return min(xs), min(ys), max(xs), max(ys)

    out: list[LayoutFinding] = []
    for i, a in enumerate(leaders):
        for b in leaders[i + 1 :]:
            if any(segments_cross(x, y) for x in parts(a) for y in parts(b)):
                ids = tuple(dict.fromkeys(str(k["item"]) for k in (a, b) if k.get("item")))
                out.append(
                    LayoutFinding(
                        "crossing", f"the leaders of {' and '.join(ids)} cross", _grow(box_of(parts(a) + parts(b)), 2.0), items=ids
                    )
                )
    views = getattr(ctx, "views", {})
    outline = view_segments(ctx, next(iter(views.values()))) if views else []  # type: ignore[arg-type]
    dimension_ink = [
        (u.key, ink_of(u.key, u.entities).lines)
        for u in extras.get("units", [])
        if u.kind == "item" and u.item_type in ("datum_dims", "overall", "dim", "baseline_dims") and u.entities
    ]
    for leader in leaders:
        item = str(leader.get("item") or "")
        if not item:
            continue
        tip_part = parts(leader)[0]
        cuts = sum(1 for s in outline if segments_cross(tip_part, s))
        if cuts > 1:
            out.append(
                LayoutFinding(
                    "crossing",
                    f"the leader of {item} goes through the outline of the part {cuts} times",
                    _grow(box_of(parts(leader)), 2.0),
                    items=(item,),
                )
            )
        crossed = [
            key
            for key, lines in dimension_ink
            if key != item and any(segments_cross(p, line) for p in parts(leader) for line in lines)
        ]
        if crossed:  # one finding per leader, naming every dimension it crosses
            out.append(
                LayoutFinding(
                    "crossing",
                    f"the leader of {item} crosses a dimension line or an extension line of {', '.join(crossed)}",
                    _grow(box_of(parts(leader)), 2.0),
                    items=(item, *crossed),
                )
            )
    return out


# --- the crops --------------------------------------------------------------------------------------------------------------


def _inside(point: tuple[float, float], box: Rect) -> bool:
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]


def _centre(box: Rect) -> tuple[float, float]:
    return (box[0] + box[2]) / 2, (box[1] + box[3]) / 2


def _overlap(a: Rect, b: Rect) -> float:
    w = min(a[2], b[2]) - max(a[0], b[0])
    h = min(a[3], b[3]) - max(a[1], b[1])
    return w * h if w > 0 and h > 0 else 0.0


def _distance(point: tuple[float, float], box: Rect) -> float:
    """How far ``point`` is from the rectangle ``box`` (0 inside it)."""
    return math.hypot(max(box[0] - point[0], 0.0, point[0] - box[2]), max(box[1] - point[1], 0.0, point[1] - box[3]))


def _clip(box: Rect, page: tuple[float, float]) -> Rect:
    return max(0.0, box[0]), max(0.0, box[1]), min(page[0], box[2]), min(page[1], box[3])


def _cut(box: Rect, limit: Rect) -> Rect:
    """The part of box inside limit."""
    return max(box[0], limit[0]), max(box[1], limit[1]), min(box[2], limit[2]), min(box[3], limit[3])


def _tiles(area: Rect) -> list[Rect]:
    """``area`` as one rectangle, or as tiles along its long side when one rectangle would need fewer than ``MIN_PX_PER_MM``
    px per mm to fit ``MAX_CROP_PX``."""
    w, h = area[2] - area[0], area[3] - area[1]
    long_side = max(w, h)
    most = MAX_CROP_PX / MIN_PX_PER_MM
    if long_side <= most:
        return [area]
    n = math.ceil(long_side / (most - TILE_OVERLAP_MM))
    step = (long_side - TILE_OVERLAP_MM) / n
    out: list[Rect] = []
    for k in range(n):
        lo = (area[0] if w >= h else area[1]) + k * step
        hi = lo + step + TILE_OVERLAP_MM
        out.append((lo, area[1], hi, area[3]) if w >= h else (area[0], lo, area[2], hi))
    return out


def _scale_for(region: Rect, floor: float) -> float:
    long_side = max(region[2] - region[0], region[3] - region[1])
    return max(floor, min(TARGET_PX_PER_MM, MAX_CROP_PX / long_side))


def _name(view: str, region: Rect, scale: float) -> str:
    x0, y0, x1, y1 = region
    return f"{view}: x {x0:.0f}..{x1:.0f}, y {y0:.0f}..{y1:.0f} mm, {scale:.0f} px/mm"


def select_crops(
    views: Sequence[tuple[str, Rect]],
    notes: Sequence[Rect],
    findings: Sequence[LayoutFinding],
    page: tuple[float, float],
    max_crops: int = MAX_CROPS,
) -> tuple[list[Crop], list[str]]:
    """The crops of one capture and the areas it leaves out (as text, with how to ask for them). ``views`` are the views
    with their boxes, ``notes`` the boxes of the annotation units (a dimension, a callout, a note: whatever an item put on
    the sheet). The annotated area of each view, tiled when it would not fit at the target scale, is a crop when the sheet
    is dense (``DENSE_MIN_ANNOTATIONS`` units or more) and the view holds at least ``VIEW_MIN_ANNOTATIONS``, or when a finding
    is in it; every finding the crops of the views do not hold gets one of its own. At most ``max_crops``: the ones with
    findings first, then the busiest."""
    dense = len(notes) >= DENSE_MIN_ANNOTATIONS
    where: dict[int, list[Rect]] = {i: [] for i in range(len(views))}
    for note in notes:  # each annotation belongs to the nearest view that is within MARGIN_MM of it
        reach = [(_distance(_centre(note), rect), i) for i, (_, rect) in enumerate(views)]
        near, best = min(reach, default=(MARGIN_MM + 1.0, -1))
        if near <= MARGIN_MM:
            where[best].append(note)
    wanted: list[Crop] = []
    for i, (name, rect) in enumerate(views):
        mine = where[i]
        held = [n for n, f in enumerate(findings) if _inside(_centre(f.box), _grow(rect, MARGIN_MM))]
        if not held and not (dense and len(mine) >= VIEW_MIN_ANNOTATIONS):
            continue
        limit = _grow(rect, MARGIN_MM)
        area = _clip(_union([rect, *[_cut(n, limit) for n in mine if _overlap(n, limit) > 0]]), page)
        for tile in _tiles(_grow(area, PAD_MM)):
            tile = _clip(tile, page)
            inside = tuple(n for n in held if _inside(_centre(findings[n].box), tile))
            count = sum(1 for n in mine if _inside(_centre(n), tile))
            if not inside and count < VIEW_MIN_ANNOTATIONS:
                continue
            scale = _scale_for(tile, MIN_PX_PER_MM)
            wanted.append(Crop(_name(name, tile, scale), name, tile, scale, inside, count))
    covered = {n for c in wanted for n in c.findings}
    for n, f in enumerate(findings):  # a finding no crop of a view holds (a note, the title block, a lone label)
        if n in covered:
            continue
        box = _clip(_grow(f.box, PAD_MM), page)
        near = [m for m in range(len(findings)) if m not in covered and _inside(_centre(findings[m].box), box)]
        scale = _scale_for(box, FINDING_MIN_PX_PER_MM)
        wanted.append(Crop(_name(f.view or "sheet", box, scale), f.view or "sheet", box, scale, tuple(near), 0))
        covered.update(near)
    wanted.sort(key=lambda c: (-len(c.findings), -c.annotations))
    chosen = wanted[:max_crops]
    left = [
        f"{c.label} (ask with region=[{c.region[0]:.0f}, {c.region[1]:.0f}, {c.region[2]:.0f}, {c.region[3]:.0f}])"
        for c in wanted[max_crops:]
    ]
    return chosen, left
