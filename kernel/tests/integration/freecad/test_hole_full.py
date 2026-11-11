"""Complete holes and the verification by real geometry, against the real FreeCAD worker.

One 19 mm plate gets what a real design needs and a plan used to bring a raw script for: M8 socket-head clearances with
their counterbore (one call), a clearance drilled from the bottom into a counterbore made before it (the hole breaks
into the cavity, by ``through_all`` and by ``up_to_face``), a tapped set screw that ends in a bore, a countersunk
M5, a pocket with an island and a ring boss (a circle in a circle). Every op must succeed on its first try with the
volume the real geometry says.
"""

import math
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import ListHolesRequest
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier

pytestmark = pytest.mark.freecad

PI = math.pi
PLATE = 120.0 * 80.0 * 19.0
TOP = {"kind": "face", "from_op": "plate", "select": {"normal": [0, 0, 1]}}
BOTTOM = {"kind": "face", "from_op": "plate", "select": {"normal": [0, 0, -1]}}
SIDE = {"kind": "face", "from_op": "plate", "select": {"normal": [1, 0, 0]}}


def _m8_with_counterbore(op_id: str, x: float, y: float) -> dict[str, Any]:
    return {
        "id": op_id,
        "op": "hole",
        "intent": "M8 socket-head clearance with its counterbore",
        "face": TOP,
        "at": [x, y, 19],
        "standard": "iso_clearance",
        "size": "M8",
        "end_condition": "through_all",
        "counterbore": {"diameter_mm": 14, "depth_mm": 9},
    }


def _screw_removed_volume() -> float:
    """Volume of a D5 hole entering the plate side at x = 60 and ending inside a D10 bore standing at x = 30:
    the material it meets is the part of the disc in front of the bore wall (the wall is round, so not a plain
    cylinder), integrated along the disc."""
    r_screw, r_bore, x_side, x_bore, steps = 2.5, 5.0, 60.0, 30.0, 4000
    total = 0.0
    for i in range(steps):
        y = -r_screw + (i + 0.5) * 2 * r_screw / steps
        chord = 2 * math.sqrt(max(r_screw**2 - y * y, 0.0))
        near_wall = x_bore + math.sqrt(max(r_bore**2 - y * y, 0.0))
        total += chord * (x_side - near_wall) * (2 * r_screw / steps)
    return total


PLAN: list[dict[str, Any]] = [
    {
        "id": "plate_sketch",
        "op": "sketch",
        "intent": "plate outline",
        "on": {"kind": "plane", "name": "XY"},
        "entities": [{"kind": "rect", "name": "plate", "center": [0, 0], "width": 120, "height": 80}],
    },
    {"id": "plate", "op": "extrude", "intent": "plate", "sketch": "plate_sketch", "depth_mm": 19},
    # four M8 socket-head clearances, counterbore 14 x 9 from the top, in one call each
    _m8_with_counterbore("cb_1", -50, -30),
    _m8_with_counterbore("cb_2", -50, 30),
    _m8_with_counterbore("cb_3", -30, -30),
    _m8_with_counterbore("cb_4", -30, 30),
    # the cavity first (14 x 9 from the top, floor at z = 10) ...
    {
        "id": "cavity_a",
        "op": "hole",
        "intent": "counterbore pocket made first",
        "face": TOP,
        "at": [-10, 30, 19],
        "standard": "simple",
        "diameter_mm": 14,
        "end_condition": "blind",
        "drill_point": "flat",  # a flat bottom is asked for; FreeCAD's default is angled
        "depth_mm": 9,
    },
    # ... then the M8 clearance drilled from the bottom up: it breaks into that cavity
    {
        "id": "from_below",
        "op": "hole",
        "intent": "M8 clearance from the bottom, into the counterbore",
        "face": BOTTOM,
        "at": [-10, 30, 0],
        "standard": "iso_clearance",
        "size": "M8",
        "end_condition": "through_all",
    },
    # the same, stopped exactly at the floor of the cavity
    {
        "id": "cavity_b",
        "op": "hole",
        "intent": "counterbore pocket made first",
        "face": TOP,
        "at": [-10, -30, 19],
        "standard": "simple",
        "diameter_mm": 14,
        "end_condition": "blind",
        "drill_point": "flat",  # a flat bottom is asked for; FreeCAD's default is angled
        "depth_mm": 9,
    },
    {
        "id": "up_to_floor",
        "op": "hole",
        "intent": "M8 clearance from the bottom, up to the floor of the cavity",
        "face": BOTTOM,
        "at": [-10, -30, 0],
        "standard": "iso_clearance",
        "size": "M8",
        "end_condition": "up_to_face",
        "up_to_face": {
            "kind": "face",
            "from_op": "cavity_b",
            "select": {"normal": [0, 0, 1], "nearest_to": [-10, -30, 10]},
        },
    },
    # a pin bore, and an M6 tapped set screw from the side that ends in it
    {
        "id": "pin_bore",
        "op": "hole",
        "intent": "pin bore D10",
        "face": TOP,
        "at": [30, 0, 19],
        "standard": "simple",
        "diameter_mm": 10,
        "end_condition": "blind",
        "drill_point": "flat",  # a flat bottom is asked for; FreeCAD's default is angled
        "depth_mm": 15,
    },
    {
        "id": "set_screw",
        "op": "hole",
        "intent": "M6 set screw into the pin bore",
        "face": SIDE,
        "at": [60, 0, 9.5],
        "standard": "iso_tapped",
        "size": "M6",
        "end_condition": "blind",
        "drill_point": "flat",  # a flat bottom is asked for; FreeCAD's default is angled
        "depth_mm": 32,
        "thread_depth_mm": 20,
    },
    {
        "id": "countersunk",
        "op": "hole",
        "intent": "M5 countersunk",
        "face": TOP,
        "at": [10, -30, 19],
        "standard": "iso_clearance",
        "size": "M5",
        "end_condition": "through_all",
        "countersink": {"diameter_mm": 10.4},
    },
    {
        "id": "angled",
        "op": "hole",
        "intent": "blind hole with a drill point",
        "face": TOP,
        "at": [40, -30, 19],
        "standard": "simple",
        "diameter_mm": 6,
        "end_condition": "blind",
        "depth_mm": 10,
    },
    {
        "id": "pocket_sketch",
        "op": "sketch",
        "intent": "pocket outline with an island",
        "on": TOP,
        "entities": [
            {"kind": "rect", "name": "outer", "center": [30, 25], "width": 24, "height": 20},
            {"kind": "rect", "name": "island", "center": [30, 25], "width": 8, "height": 6},
        ],
    },
    {"id": "pocket", "op": "cut", "intent": "pocket around an island", "sketch": "pocket_sketch", "depth_mm": 6},
    {
        "id": "ring_sketch",
        "op": "sketch",
        "intent": "ring section",
        "on": TOP,
        "entities": [
            {"kind": "circle", "name": "outer", "center": [-20, 0], "diameter": 24},
            {"kind": "circle", "name": "inner", "center": [-20, 0], "diameter": 16},
        ],
    },
    {"id": "ring", "op": "extrude", "intent": "ring boss", "sketch": "ring_sketch", "depth_mm": 5},
    # a counterbored hole repeated by a pattern: the instances are verified like the seed
    _m8_with_counterbore("cb_seed", -50, 0),
    {
        "id": "cb_pattern",
        "op": "linear_pattern",
        "intent": "second counterbored hole",
        "features": ["cb_seed"],
        "direction": "Y",
        "spacing_mm": 14,
        "count": 2,
    },
]


def _failures(trace: ExecutionTrace) -> list[Any]:
    return [
        (
            s.op_id,
            s.status.value,
            s.plan_error,
            s.attempts[-1].error if s.attempts else None,
            [(c.name, c.expected, c.observed) for c in s.last_level2() if c.ok is False] if s.attempts else None,
        )
        for s in trace.steps
        if s.status is not StepStatus.success
    ]


@pytest.fixture(scope="module")
def trace(adapter: FreeCADAdapter) -> ExecutionTrace:
    plan = parse_plan({"id": "hole_full", "kind": "part", "operations": PLAN})
    executor = Executor(plan, adapter, Verifier(), ExecutorConfig())
    executor.start()
    return executor.run_to_end()


def test_every_op_succeeds_on_its_first_try(trace: ExecutionTrace) -> None:
    assert trace.final_state is ExecutorState.completed, _failures(trace)
    assert all(s.status is StepStatus.success for s in trace.steps), _failures(trace)
    assert all(len(s.attempts) == 1 for s in trace.steps if s.attempts), [
        (s.op_id, len(s.attempts)) for s in trace.steps
    ]


def test_the_part_has_exactly_the_volume_the_geometry_says(trace: ExecutionTrace) -> None:
    cb = PI * (49.0 * 9.0 + 20.25 * 10.0)
    csink = PI * 2.75**2 * 19.0 + PI * 2.45 / 3.0 * (5.2**2 + 5.2 * 2.75 + 2.75**2) - PI * 2.75**2 * 2.45
    angled = PI * 9.0 * 10.0 + PI * 9.0 * (3.0 / math.tan(math.radians(59.0))) / 3.0
    removed = (
        6 * cb  # four socket-head holes, and the seed of the pattern with its instance
        + 2 * (PI * 49.0 * 9.0 + PI * 20.25 * 10.0)  # cavity + the clearance that breaks into it, twice
        + PI * 25.0 * 15.0  # pin bore
        + _screw_removed_volume()
        + csink
        + angled
        + (24 * 20 - 8 * 6) * 6.0  # pocket around its island
    )
    added = PI * (144.0 - 64.0) * 5.0  # the ring: outer circle minus inner circle
    assert trace.final_mass is not None
    assert trace.final_mass.volume_mm3 == pytest.approx(PLATE - removed + added, abs=1.0)


def _rows(trace: ExecutionTrace, op_id: str) -> dict[str, Any]:
    step = trace.step(op_id)
    assert step is not None
    return {c.name: c for c in step.last_level2()}


@pytest.mark.parametrize("op_id", ["from_below", "up_to_floor"])
def test_a_hole_that_breaks_into_a_cavity_is_judged_by_the_boolean(trace: ExecutionTrace, op_id: str) -> None:
    rows = _rows(trace, op_id)
    volume = rows["volume_delta"]
    assert volume.kind is CheckKind.asserted and volume.ok
    assert volume.observed == pytest.approx(-PI * 20.25 * 10.0, abs=1e-2)  # the 10 mm of material under the floor
    if op_id == "from_below":
        # through everything: it crosses one solid block of 10 mm from the bottom and opens into the cavity, so it
        # makes the one wall of a through hole, and the extent is that block's thickness
        assert rows["new_faces_count"].kind is CheckKind.asserted and rows["new_faces_count"].ok
        assert rows["cut_extent_along_n"].kind is CheckKind.asserted and rows["cut_extent_along_n"].ok
        assert rows["cut_extent_along_n"].expected == pytest.approx(10.0, abs=1e-3)
    else:
        # the faces it created are not those of a hole in solid material: reported, not asserted
        assert rows["new_faces_count"].kind is CheckKind.reported


def test_a_ring_is_an_annulus_and_a_pocket_leaves_its_island(trace: ExecutionTrace) -> None:
    for op_id, delta in (("ring", PI * 80.0 * 5.0), ("pocket", -(24 * 20 - 8 * 6) * 6.0)):
        volume = _rows(trace, op_id)["volume_delta"]
        assert volume.kind is CheckKind.asserted and volume.ok, op_id
        assert volume.expected == pytest.approx(delta, abs=1e-2), op_id


def test_list_holes_reports_the_counterbore_the_countersink_and_the_tapped_hole(
    trace: ExecutionTrace, adapter: FreeCADAdapter
) -> None:
    assert trace.final_state is ExecutorState.completed
    holes = adapter.list_holes(ListHolesRequest(operation_id="holes")).holes
    diameters = sorted(round(h.diameter_mm, 3) for h in holes)
    assert diameters.count(14.0) == 8  # six counterbores and the two cavities
    assert diameters.count(9.0) == 8  # the M8 clearance of each of them
    assert 5.0 in diameters  # the set screw (the M6 tap drill)
    assert 5.5 in diameters  # the M5 clearance under the countersink
    assert 10.0 in diameters  # the pin bore
    assert 6.0 in diameters  # the angled blind hole
    # a counterbore is a blind wider hole on the axis of the through one: the drawing callout reads it as two lines
    boxes = [h for h in holes if round(h.diameter_mm, 3) == 14.0]
    assert all(h.through is False and h.depth_mm == pytest.approx(9.0, abs=1e-6) for h in boxes), boxes
    assert all(h.countersink_diameter_mm is None for h in boxes)
    # a countersink is listed with the hole it widens
    sink = next(h for h in holes if round(h.diameter_mm, 3) == 5.5)
    assert sink.through is True
    assert sink.countersink_diameter_mm == pytest.approx(
        10.4, abs=1e-6
    ) and sink.countersink_angle_deg == pytest.approx(90.0)
    # the drill point of the angled hole is not a countersink
    angled = next(h for h in holes if round(h.diameter_mm, 3) == 6.0)
    assert angled.countersink_diameter_mm is None and angled.through is False
