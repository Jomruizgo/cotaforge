# ruff: noqa: E501
"""``collision``: nothing lies on anything (no FreeCAD). The check reads the ink of every unit of the sheet in sheet coordinates:
besides a text over a text or over a line, a tolerance or datum FRAME on another frame, a line (a leader, an extension line, an
arrow) through a frame, and a frame over the outline of a view are collisions, named by the two items."""

import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_collide as sc  # noqa: E402


def frame_ink(owner: str, rect: sc.Rect) -> sc.Ink:
    x0, y0, x1, y1 = rect
    box = ((x0, y0, x1, y0), (x1, y0, x1, y1), (x1, y1, x0, y1), (x0, y1, x0, y0))
    return sc.Ink(owner, (), box, (), (rect,))


def line_ink(owner: str, *segments: sc.Segment) -> sc.Ink:
    return sc.Ink(owner, (), tuple(segments), ())


def kinds(found: list[sc.Collision]) -> list[tuple[str, str, str]]:
    return [(c.a, c.b, c.kind) for c in found]


def test_a_frame_on_another_frame_is_a_collision_naming_both_items() -> None:
    found = sc.collisions([frame_ink("g_pe1", (10.0, 10.0, 24.0, 17.0)), frame_ink("dB", (20.0, 12.0, 27.0, 19.0))])
    assert ("dB", "g_pe1", "frame_frame") in kinds(found)  # (the edges of each also cross the other: the review names the pair once)


def test_frames_side_by_side_or_touching_are_not() -> None:
    assert sc.collisions([frame_ink("a", (10.0, 10.0, 24.0, 17.0)), frame_ink("b", (24.0, 10.0, 31.0, 17.0))]) == []
    assert sc.collisions([frame_ink("a", (10.0, 10.0, 24.0, 17.0)), frame_ink("b", (30.0, 10.0, 37.0, 17.0))]) == []


def test_a_leader_through_a_frame_is_a_collision_one_ending_on_its_edge_is_not() -> None:
    frame = frame_ink("g1", (10.0, 10.0, 24.0, 17.0))
    assert kinds(sc.collisions([frame, line_ink("h1", (0.0, 13.0, 40.0, 13.0))])) == [("h1", "g1", "line_frame")]
    assert sc.collisions([frame, line_ink("h1", (0.0, 13.0, 10.0, 13.0))]) == []  # it stops at the edge
    assert sc.collisions([frame, line_ink("h1", (0.0, 20.0, 40.0, 20.0))]) == []


def test_a_frame_over_the_outline_of_a_view_is_a_collision() -> None:
    outline = line_ink("views", (0.0, 13.0, 40.0, 13.0))
    assert kinds(sc.collisions([frame_ink("g1", (10.0, 10.0, 24.0, 17.0))], [outline])) == [("g1", "views", "frame_line")]
    assert sc.collisions([frame_ink("g1", (10.0, 20.0, 24.0, 27.0))], [outline]) == []


def test_the_text_cases_still_hold() -> None:
    text = lambda owner, rect: sc.Ink(owner, ((rect, "x"),), (), ())  # noqa: E731
    assert kinds(sc.collisions([text("a", (0.0, 0.0, 10.0, 4.0)), text("b", (5.0, 1.0, 15.0, 5.0))])) == [("b", "a", "text_text")]
