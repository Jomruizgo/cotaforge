"""The parting line, the slides and the core / cavity split, driven straight on the worker's geometry (made-up parts of
plain ``Part`` solids, pulled along +Z).

The shell is two frustums subtracted from one another (outer 80 x 60 x 30, inner 75 x 55 x 27.5,
both wider at the top by
1.5 degrees): the outer walls look down (the cavity's), the inner ones up (the core's), so the parting line is the one
loop of the rim's outer edge, 4 lines in one plane. Every volume balance is core + cavity + slides + part = block."""

import math
from typing import Any

import FreeCAD as App
import mould_split as ms
import Part
import parting as pt

PULL = (0.0, 0.0, 1.0)


def _frustum(x0: float, y0: float, z0: float, w0: float, d0: float, z1: float, grow: float) -> Any:
    """Six planar faces: a rectangle at ``z0`` and the same grown by ``grow`` on every side at ``z1``."""
    a = [
        App.Vector(x0, y0, z0),
        App.Vector(x0 + w0, y0, z0),
        App.Vector(x0 + w0, y0 + d0, z0),
        App.Vector(x0, y0 + d0, z0),
    ]
    b = [App.Vector(x0 - grow, y0 - grow, z1), App.Vector(x0 + w0 + grow, y0 - grow, z1)]
    b += [App.Vector(x0 + w0 + grow, y0 + d0 + grow, z1), App.Vector(x0 - grow, y0 + d0 + grow, z1)]
    quads = [a, b, *[[a[i], a[(i + 1) % 4], b[(i + 1) % 4], b[i]] for i in range(4)]]
    solid = Part.Solid(Part.Shell([Part.Face(Part.makePolygon([*q, q[0]])) for q in quads]))
    solid.fix(1e-7, 1e-7, 1e-7)
    return solid


def _shell() -> Any:
    grow = math.tan(math.radians(1.5)) * 30.0
    outer = _frustum(-40, -30, 0, 80, 60, 30, grow)
    inner = _frustum(-37.5, -27.5, 2.5, 75, 55, 30.001, grow)
    return outer.cut(inner).removeSplitter()


def _halves(shape: Any, decision: str | None = None, slides: Any = None) -> tuple[dict[str, Any], dict[str, Any]]:
    block, _ = ms.block_box(shape, None, None)
    made = ms.make_halves(shape, PULL, 1.0, decision, block, slides)
    return made, ms.check_halves(shape, PULL, made)


def _expect(message: str, call: Any) -> str:
    try:
        call()
    except pt.PartingError as exc:
        assert message in str(exc), str(exc)
        return str(exc)
    raise AssertionError(f"expected a PartingError with {message!r}")


def _balanced(checks: dict[str, Any]) -> None:
    assert checks["valid"]["core"] and checks["valid"]["cavity"] and all(checks["valid"]["slides"]), checks["valid"]
    assert checks["balance_ok"] and checks["balance_gap"] < 1e-6, checks["balance_gap"]
    assert not ms._problems(checks), ms._problems(checks)  # noqa: SLF001


def check_a_pin_in_a_hole_traps_and_sliding_along_the_hole_does_not() -> None:
    plate = Part.makeBox(20, 20, 20).cut(Part.makeCylinder(3.0, 30.0, App.Vector(-5, 10, 10), App.Vector(1, 0, 0)))
    pin = Part.makeCylinder(3.0, 40.0, App.Vector(-10, 10, 10), App.Vector(1, 0, 0))
    free, shared_free = ms.leaves_along(pin, (1.0, 0.0, 0.0), plate, 20.0)
    stuck, shared_stuck = ms.leaves_along(pin, (0.0, 0.0, 1.0), plate, 20.0)
    assert free and shared_free == 0.0, (free, shared_free)
    assert not stuck and shared_stuck > 100.0, (stuck, shared_stuck)  # the pin runs into the plate's wall


def check_a_shell_with_a_planar_line_splits_in_two_and_adds_up() -> None:
    shape = _shell()
    made, checks = _halves(shape)
    (loop,) = made["analysis"]["loops"]
    assert loop["closed"] and loop["planar"] and len(loop["edges"]) == 4, loop
    assert abs(loop["levels"][0] - 30.0) < 1e-6 and abs(loop["length_mm"] - 2 * (81.57 + 61.57)) < 0.01
    assert "a plane through the line" in made["surface"] and not made["slides"]
    _balanced(checks)
    assert checks["trapped"] == []
    # the core is the half that forms the inside: it holds the point just above the floor, the cavity the one below it
    assert made["core"].isInside(App.Vector(0, 0, 10), 1e-6, False) and made["cavity"].isInside(
        App.Vector(0, 0, -10), 1e-6, False
    )


def check_a_wall_with_no_draft_is_not_guessed_and_the_decision_moves_the_line() -> None:
    box = Part.makeBox(40, 30, 20)
    _expect("have no draft and touch both halves", lambda: _halves(box))
    for decision, height in (("cavity", 20.0), ("core", 0.0)):  # the rim's edge, or the bottom's
        made, checks = _halves(box, decision)
        (loop,) = made["analysis"]["loops"]
        assert abs(loop["levels"][0] - height) < 1e-6 and len(loop["edges"]) == 4, (decision, loop)
        _balanced(checks)
    proposal = pt.analyse(box, PULL, 1.0, None)
    assert proposal["proposal"] == "cavity" and len(proposal["ambiguous"]) == 1  # a tie goes to the cavity


def check_a_rim_that_steps_gets_a_run_off_surface_and_adds_up() -> None:
    notch = Part.makeBox(10, 20, 6.0, App.Vector(35, -10, 24.0))
    shape = _shell().cut(notch).removeSplitter()
    made, checks = _halves(shape, "core")
    (loop,) = made["analysis"]["loops"]
    assert not loop["planar"] and len(loop["levels"]) == 2 and len(loop["edges"]) == 8, loop
    assert "run-off" in made["surface"]
    _balanced(checks)
    both = pt.analyse(shape, PULL, 1.0, "cavity")["loops"][0]
    assert (
        len(both["edges"]) == 12 and both["length_mm"] > loop["length_mm"]
    )  # the other decision cuts through the wall


def check_a_hole_across_the_pull_needs_a_slide_that_leaves_sideways() -> None:
    shape = _shell().cut(Part.makeCylinder(4.0, 20.0, App.Vector(30, 0, 16), App.Vector(1, 0, 0))).removeSplitter()
    analysis = pt.analyse(shape, PULL, 1.0, None)
    (slide,) = analysis["slides"]
    assert slide["direction"] is not None and abs(slide["direction"][0] - 1.0) < 1e-9, slide  # out through the wall
    _expect("need a slide: the part would be trapped", lambda: _halves(shape))
    _expect("there is no undercut region", lambda: _halves(shape, None, [2]))
    made, checks = _halves(shape, None, "all")
    _balanced(checks)
    (_, body, how) = made["slides"][0]
    assert "slide 1 along (1" in how and body.Volume > 1000.0
    assert checks["leave"]["slide 1"][0] and checks["trapped"] == []


def check_a_silhouette_across_a_face_is_declared_and_nothing_is_split() -> None:
    bar = Part.makeCylinder(
        10.0, 40.0, App.Vector(-20, 0, 0), App.Vector(1, 0, 0)
    )  # lying across the pull: its silhouette is in the face
    analysis = pt.analyse(bar, PULL, 1.0, "cavity")
    assert len(analysis["straddle"]) == 1, analysis["straddle"]
    _expect("FreeCAD cannot split a face along its silhouette", lambda: _halves(bar, "cavity"))


def check_the_block_given_by_the_engineer_must_be_bigger_than_the_part() -> None:
    box = Part.makeBox(40, 30, 20)
    block, how = ms.block_box(box, (100.0, 90.0, 60.0), None)
    assert abs(block.Volume - 100 * 90 * 60) < 1e-6 and "given by the engineer" in how
    _expect("less than 1 mm of steel on a side", lambda: ms.block_box(box, (40.5, 90.0, 60.0), None))
    margin, said = ms.block_box(box, None, 10.0)
    assert abs(margin.Volume - 60 * 50 * 40) < 1e-6 and "grown 10 mm" in said and "proposed" not in said
    proposed, said = ms.block_box(box, None, None)
    assert abs(proposed.Volume - 90 * 80 * 70) < 1e-6 and "not a standard" in said  # 25 mm: a fifth of 40 is 8


def check_the_run_off_refuses_a_loop_it_cannot_push_outward() -> None:
    wall = Part.makeBox(10, 10, 10)
    topo = pt.Topology(wall)
    steps_only = {
        "walked": [(i, True) for i, e in enumerate(topo.edges) if abs(e.Vertexes[0].Z - e.Vertexes[-1].Z) > 5][:4]
    }
    try:
        pt.runoff_sheet(topo, steps_only, PULL, 100.0)
    except pt.PartingError as exc:
        assert "only along the pull" in str(exc), str(exc)
    else:
        raise AssertionError("a loop of steps only has nothing to push outward")
