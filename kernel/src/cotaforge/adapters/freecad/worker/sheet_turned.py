# ruff: noqa: E501
"""A turned part goes on the sheet the way it is machined: its axis horizontal, so the thread end or the chuck end can be
read the shop's way (no FreeCAD import, unit-tested on its own).

TechDraw's front view looks along -y with z up, so a shaft modelled along z would be drawn standing on its end. The harness
turns the anchor view of such a part instead: the view's x direction is the axis and the part's own side stays towards the
viewer (the front view turned a quarter of a turn, not another face). The views the projection group derives from it (the
end view, which looks along the axis) follow, and in first angle the end view stands beside the main one.

The end of the part with a thread goes to the right (the working end, away from the chuck at the left); a part with no
thread keeps the positive end of its axis on the right.
"""

Vec3 = tuple[float, float, float]

# axis index -> (direction towards the viewer, x direction) of the turned front view, for the positive end on the right.
# ``up = direction x xdir`` (the page's up in the model): x axis: z up; y axis: -x up; z axis: -x up.
_FRAMES: dict[int, tuple[Vec3, Vec3]] = {
    0: ((0.0, -1.0, 0.0), (1.0, 0.0, 0.0)),
    1: ((0.0, 0.0, 1.0), (0.0, 1.0, 0.0)),
    2: ((0.0, -1.0, 0.0), (0.0, 0.0, 1.0)),
}


def axis_index(axis: Vec3) -> int:
    """The coordinate axis (0, 1, 2) a unit ``axis`` lies along."""
    return max(range(3), key=lambda i: abs(axis[i]))


def frame(axis: Vec3, flip: bool = False) -> tuple[Vec3, Vec3]:
    """``(direction, xdirection)`` of the front view of a part turned about ``axis``: the axis runs along the page's x. With
    ``flip`` its negative end is on the right (the view is seen from the other side, so nothing is mirrored)."""
    direction, xdirection = _FRAMES[axis_index(axis)]
    if flip:
        direction = (-direction[0], -direction[1], -direction[2])
        xdirection = (-xdirection[0], -xdirection[1], -xdirection[2])
    return direction, xdirection


def iso_frame(axis: Vec3, flip: bool = False) -> tuple[Vec3, Vec3]:
    """``(direction, xdirection)`` of the isometric view of a turned part: the standard one (looking from the front, the right
    and above) seen in the frame of the turned main view, so the axis runs along the page the way it does in the main view
    instead of standing up."""
    d, r = frame(axis, flip)
    u = (d[1] * r[2] - d[2] * r[1], d[2] * r[0] - d[0] * r[2], d[0] * r[1] - d[1] * r[0])
    direction = (r[0] + d[0] + u[0], r[1] + d[1] + u[1], r[2] + d[2] + u[2])
    xdirection = (r[0] - d[0], r[1] - d[1], r[2] - d[2])
    return direction, xdirection


def thread_end_flip(axis: Vec3, part_range: tuple[float, float], threads: list[float]) -> bool:
    """Whether the view is flipped so the end with a thread is on the right. ``part_range`` is the part's extent along the
    axis (low, high, model coordinates), ``threads`` the coordinates along the axis of the middle of each external thread.
    Threads at both ends, or none: not flipped."""
    if not threads:
        return False
    middle = (part_range[0] + part_range[1]) / 2
    high = sum(1 for t in threads if t > middle)
    low = sum(1 for t in threads if t < middle)
    return low > 0 and high == 0


def layout_sizes(length: float, diameter: float) -> tuple[float, float, float]:
    """The ``(w, d, h)`` the layout arranges: the length across the sheet, the diameter up and (the end view's width) the
    diameter again."""
    return length, diameter, diameter


def views_for(keyway: bool) -> list[str]:
    """The views a solid turned part is drawn in when nobody says: the main view alone, which carries the diameters, the
    lengths, the thread and the chamfers; the end view only when it shows something the main view does not (a keyway seen
    end-on). A part with a bore is the section's: see ``drawing._views_for_part``."""
    return ["front", "right"] if keyway else ["front"]
