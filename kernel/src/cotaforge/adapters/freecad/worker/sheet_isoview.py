# ruff: noqa: E501
"""Which way the isometric view of an assembly looks (no FreeCAD import).

The four isometric directions from above are tried (the same view turned by quarter turns about the vertical) and the one
that shows the most of each ballooned part is kept. ``VISIBLE_MIN`` is the share of a part's outline that must be its own
surface for a balloon on that view to point at something a reader can see: below it the review asks for another view.
"""

from collections.abc import Sequence

Vec3 = tuple[float, float, float]

DEFAULT: tuple[Vec3, Vec3] = ((1.0, -1.0, 1.0), (1.0, 1.0, 0.0))  # ``sheet_layout.ISO_DIRECTION`` / ``ISO_XDIRECTION``
CANDIDATES: tuple[tuple[Vec3, Vec3], ...] = (
    DEFAULT,
    ((-1.0, 1.0, 1.0), (-1.0, -1.0, 0.0)),
    ((1.0, 1.0, 1.0), (-1.0, 1.0, 0.0)),
    ((-1.0, -1.0, 1.0), (1.0, -1.0, 0.0)),
)
VISIBLE_MIN = 0.2  # a part with less than a fifth of its outline showing is a sliver, not a part one can balloon
MAX_PARTS_TRIED = 12  # a vendor assembly of hundreds of bodies keeps the default: the rays would cost minutes


def quarter_turned(direction: Vec3) -> bool:
    """Whether this direction is a quarter turn from the default one (the width and the depth of the part swap on the page)."""
    return direction[0] * direction[1] > 0


def choose(shares: Sequence[dict[str, float]]) -> int:
    """The index of the candidate whose worst-seen part is seen best (ties: the better average, then the first)."""
    best, best_key = 0, (-1.0, -1.0)
    for index, found in enumerate(shares):
        values = list(found.values()) or [0.0]
        key = (min(values) - 1e-9 * index, sum(values) / len(values))
        if key > best_key:
            best, best_key = index, key
    return best
