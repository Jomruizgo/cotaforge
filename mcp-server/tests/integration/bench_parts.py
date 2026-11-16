# ruff: noqa: E501
"""Bench parts a real shop would make, designed through the harness's own flow (its operations, its alerts and its design
review) with made-up dimensions, and the sheet of each one drawn the way a drafter session does it (plan, compose, local fixes).

* ``die_plate``: a hardened die plate. Faces A and B are ground AFTER the hardening, so the dowel holes are pre-drilled under
  size before it and finished (D6 H7, square to A, located from the edges) after it with A on the table. If they were finished
  first, the grinding would work from the opposite face and their axis error would stay.
* ``edge_block``: a block with its holes on the edges: tapped holes in two side faces, a cross hole that meets the hole of the top
  face, and datums on edges.

The intent files are in ``tests/data/bench`` (the designer writes them next to the model, ``drawings/<part>-intent.md``).
What the harness gets wrong on these sheets is kept in ``KNOWN_DEFECTS``: the tests tolerate those blocking codes and fail on any other.
"""

import re
import shutil
from pathlib import Path

from tests.integration.conftest import Cad

DATA = Path(__file__).resolve().parent.parent / "data" / "bench"
XY = {"kind": "plane", "name": "XY"}

DESIGN_ANSWERS = {
    "die_plate": {
        "design-precision-the-assembly-guarantees": "applies: the two dowel holes are finished on the single plate after hardening and grinding (jig grind or wire cut, datum A on the table), located in coordinates from the ground faces; the part alone guarantees the pattern, it is not joined or bent before.",
        "drawing-thread-designation": "does not apply: the plate has no threaded hole (D6 H7 dowel holes and four M6 clearance holes with counterbore); the thread is in the mating part.",
    },
    "edge_block": {
        "design-precision-the-assembly-guarantees": "applies: the bore D12 H7 and the cross hole are made on the single milled block, located from datums A, B and C; nothing is joined or bent afterwards.",
        "drawing-thread-designation": "applies: the two M6 holes in the left face and the two M8 holes in the front face are tapped holes made with the hole op; each is called with its thread, its thread depth and its drill depth.",
    },
}

KNOWN_DEFECTS: dict[str, dict[str, str]] = {
    "die_plate": {
        "hole_not_located": "the fourth corner of a rectangular pattern is asked for its own dimension although the other three and the two distances fix it",
        "hidden_dense": "the front view keeps its hidden lines: they cannot be switched off for one hole group and the counterbores are not read without a section",
        "hidden_feature_no_section": "a counterbore depth is asked as a section but the section the harness makes lands on the notes and carries no dimension",
        "view_adds_nothing": "the section A-A of the counterbores has no dimension of its own to put on it",
        "collision": "the notes block, the section and the baseline stack of the top view lie on one another; the planner does not find the free corner to the right",
        "dimension_crossing": "extension lines of the centre-to-centre dimensions cross the baseline stack",
        "item_outside_frame": "dimensions that the frame holds are said to lie outside the drawing area",
        "foreign_symbol": "the chamfer callout and the section are symbols of the harness, not TechDraw objects, and hold the export",
        "leader_crosses_dimension": "the callout leaders cross the dimensions",
        "leader_crosses_part": "the callout leaders cross the outline twice",
        "layout_unplaced": "the global planner leaves several items where they were drawn",
        "dimension_side": "the side the dimension is on is not the near one",
        "datum_on_line": "a datum symbol rests on a dimension line",
        "callouts_crowded": "two callouts of the same holes stand on one another",
        "paper_too_small": "the notes block of a 120 x 80 plate is said not to fit an A3 sheet at 1:1",
        "window_differs": "the window and the sheet differ after the edits",
        "text_outside_frame": "dimension text of the baseline stack lands outside the frame of the sheet",
        "dimension_twice": "the toleranced thickness repeats the overall dimension",
        "duplicate_dimension": "the toleranced thickness repeats the overall dimension",
    },
    "edge_block": {
        "over_dimensioned": "the second hole of a pair is dimensioned from the datum and between the two centres (the in-place edit that would drop one is refused)",
        "collision": "the callouts, the chamfer callout and the notes block lie on one another",
        "fit_hole_no_tolerance": "the clearance cross hole D6 is taken for a dowel hole because of its diameter and asked for a fit",
        "leader_crosses_part": "the callout leaders cross the outline twice",
        "hidden_dense": "the front and right views are mostly hidden lines of the edge holes",
        "hidden_feature_no_section": "the depth of the edge holes is only in hidden lines",
        "foreign_symbol": "the chamfer callout is a symbol of the harness, not a TechDraw object",
        "datum_on_line": "a datum symbol rests on a dimension line",
        "layout_unplaced": "the global planner leaves several items where they were drawn",
        "leader_crosses_dimension": "the callout leaders cross the dimensions",
        "dimension_side": "the side the dimension is on is not the near one",
        "dimension_crossing": "extension lines cross a dimension line",
        "callouts_crowded": "two callouts stand on one another",
        "window_differs": "the window and the sheet differ after the edits",
    },
}
"""Per part: the codes the review of its sheet holds, with why. A code that is not here and that blocks the export fails the test."""


def intent(part: str) -> str:
    return (DATA / f"{part}-intent.md").read_text(encoding="utf-8")


def write_intent(workspace: Path, part: str) -> Path:
    target = workspace / "drawings" / f"{part}-intent.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(DATA / f"{part}-intent.md", target)
    return target


async def design_review(cad: Cad, part: str) -> None:
    """The designer's milestone: read the entries the design called for, answer them, and the review is ready."""
    await cad.ok("memory", action="get", ids=list(DESIGN_ANSWERS[part]))
    await cad.ok("memory", action="answer", answers=DESIGN_ANSWERS[part])
    ready = await cad.ok("memory", action="review", stage="detail")
    assert "ready" in ready, ready
    assert "violation" in await cad.ok("check")


async def build_die_plate(cad: Cad, workspace: Path) -> None:
    """120 x 80 x 25 plate: two D6 H7 dowel holes and four counterbored M6 screw holes; edges broken 0.5 x 45."""
    top = {"kind": "face", "from_op": "plate", "select": {"normal": [0, 0, 1]}}
    await cad.ok("memory", action="brief", sector="precision-machining")
    await cad.ok("doc", action="new", name="die_plate")
    await cad.ok("doc", action="set_material", material="Acero 1.2379 (D2) templado 60 HRC")
    await cad.ok(
        "doc",
        action="set_route",
        route=["fresado", "taladrado", "roscado", "temple", "rectificado", "rectificado de coordenadas"],
    )
    await cad.ok(
        "op",
        name="sketch",
        id="outline",
        params={
            "on": XY,
            "entities": [{"kind": "rect", "name": "plate", "center": [0, 0], "width": 120, "height": 80}],
        },
    )
    await cad.ok("op", name="extrude", id="plate", params={"sketch": "outline", "depth_mm": 25})
    dowels = {
        "face": top,
        "positions": [[-30, -12], [30, 12]],
        "standard": "simple",
        "diameter_mm": 6,
        "fit": "H7",
        "end_condition": "through_all",
    }
    await cad.ok("op", name="hole", id="dowels", params=dowels)
    screws = {
        "face": top,
        "positions": [[-48, -28], [48, -28], [-48, 28], [48, 28]],
        "standard": "iso_clearance",
        "size": "M6",
        "counterbore": {"diameter_mm": 11, "depth_mm": 6.5},
        "end_condition": "through_all",
    }
    await cad.ok("op", name="hole", id="screws", params=screws)
    edges = {"kind": "edges", "from_op": "screws", "where": {"convex": True, "exclude_holes": True}}
    await cad.ok("op", name="chamfer", id="edge_break", params={"edges": [edges], "distance1_mm": 0.5})
    await design_review(cad, "die_plate")
    await cad.ok("doc", action="save", path="parts/die_plate.FCStd")


async def build_edge_block(cad: Cad, workspace: Path) -> None:
    """80 x 60 x 40 block: D12 H7 bore in the top face, a D6 cross hole from the left face that meets it, two M6 holes in the left
    face and two M8 holes in the front face (their axes lie in the edge faces)."""
    face = {"kind": "face", "from_op": "body"}
    await cad.ok("memory", action="brief", sector="precision-machining")
    await cad.ok("doc", action="new", name="edge_block")
    await cad.ok("doc", action="set_material", material="Acero 1045")
    await cad.ok(
        "op",
        name="sketch",
        id="outline",
        params={"on": XY, "entities": [{"kind": "rect", "name": "block", "center": [0, 0], "width": 80, "height": 60}]},
    )
    await cad.ok("op", name="extrude", id="body", params={"sketch": "outline", "depth_mm": 40})
    bore = {
        "face": {**face, "select": {"normal": [0, 0, 1]}},
        "at": [0, 0, 40],
        "standard": "simple",
        "diameter_mm": 12,
        "fit": "H7",
        "end_condition": "blind",
        "depth_mm": 30,
        "drill_point": "flat",
    }
    await cad.ok("op", name="hole", id="bore", params=bore)
    left = {**face, "select": {"normal": [-1, 0, 0]}}
    await cad.ok(
        "op",
        name="hole",
        id="cross",
        params={
            "face": left,
            "at": [-40, 0, 25],
            "standard": "simple",
            "diameter_mm": 6,
            "end_condition": "through_all",
        },
    )
    for index, y in enumerate((-18, 18), start=1):
        m6 = {
            "face": {**left, "select": {"normal": [-1, 0, 0], "nearest_to": [-40, y, 12]}},
            "at": [-40, y, 12],
            "standard": "iso_tapped",
            "size": "M6",
            "end_condition": "blind",
            "depth_mm": 16,
            "thread_depth_mm": 12,
        }
        await cad.ok("op", name="hole", id=f"tapped_left_{index}", params=m6)
    front = {**face, "select": {"normal": [0, -1, 0]}}
    for index, x in enumerate((-22, 22), start=1):
        m8 = {
            "face": front,
            "at": [x, -30, 20],
            "standard": "iso_tapped",
            "size": "M8",
            "end_condition": "blind",
            "depth_mm": 20,
            "thread_depth_mm": 16,
        }
        await cad.ok("op", name="hole", id=f"tapped_front_{index}", params=m8)
    edges = {"kind": "edges", "from_op": "tapped_front_2", "where": {"convex": True, "exclude_holes": True}}
    await cad.ok("op", name="chamfer", id="edge_break", params={"edges": [edges], "distance1_mm": 0.5})
    await design_review(cad, "edge_block")
    await cad.ok("doc", action="save", path="parts/edge_block.FCStd")


async def reopen_for_drawing(cad: Cad, workspace: Path, part: str) -> None:
    """The designer saved and closed the file and wrote the intent; the drafter opens it."""
    write_intent(workspace, part)
    await cad.ok("doc", action="close")
    await cad.ok("doc", action="open", path=f"parts/{part}.FCStd")


async def draw_die_plate(cad: Cad) -> None:
    """The first proposal in one order, then the local fixes a drafter makes: a toleranced thickness instead of the overall,
    the callouts split by function, the notes block moved, a section of the counterbores."""
    d1, d2, s1, s2, s3, s4 = [-30, -12, 25], [30, 12, 25], [-48, -28, 25], [48, -28, 25], [-48, 28, 25], [48, 28, 25]
    plan = await cad.ok("drawing", action="plan")
    assert "rq_surface_texture" in plan and "rq_heat_treatment" in plan
    decisions = {
        "datums": [
            {"letter": "A", "face": "face:5"},
            {"letter": "B", "face": "face:4"},
            {"letter": "C", "face": "face:1"},
        ],
        "relations": [
            {"type": "datum_dims", "x_from": "face:4", "y_from": "face:1", "view": "top", "holes": [d1, s1]},
            {
                "type": "dim",
                "kind": "distance",
                "from": {"at": d1},
                "to": {"at": d2},
                "along": "x",
                "view": "top",
                "tol": 0.01,
            },
            {
                "type": "dim",
                "kind": "distance",
                "from": {"at": d1},
                "to": {"at": d2},
                "along": "y",
                "view": "top",
                "tol": 0.01,
            },
            {
                "type": "dim",
                "kind": "distance",
                "from": {"at": s1},
                "to": {"at": s2},
                "along": "x",
                "view": "top",
                "tol": 0.1,
            },
            {
                "type": "dim",
                "kind": "distance",
                "from": {"at": s1},
                "to": {"at": s3},
                "along": "y",
                "view": "top",
                "tol": 0.1,
            },
        ],
        "fits": [
            {"type": "hole_dim", "at": [d1, d2], "fit": "H7", "dowel": True},
            {"type": "hole_dim", "at": [s1, s2, s3, s4]},
        ],
        "gdt": [
            {"type": "gdt", "char": "parallelism", "value": 0.005, "datums": ["A"], "face": "face:6", "view": "front"},
            {"type": "gdt", "char": "perpendicularity", "value": 0.005, "datums": ["A"], "on": "dc_fits1"},
        ],
        "finish": [
            {"type": "finish", "ra": 0.4, "face": "face:5", "view": "front"},
            {"type": "finish", "ra": 0.4, "face": "face:6", "view": "front"},
            {"type": "finish", "ra": 3.2, "general": True},
        ],
        "notes": [
            {
                "type": "note",
                "text": "Sin recubrimiento (aceite). Plano grabado en la cara lateral derecha. Cantidad: 1 pieza.",
            },
            {
                "type": "note",
                "text": "Ruta: fresar con 0,5 mm por cara de sobremedida; taladrar 4× Ø6,6 y avellanado Ø11; preagujerear el registro en Ø5,7 antes del temple; templar a 60 ±2 HRC.",
            },
            {
                "type": "note",
                "text": "Después del temple: rectificar A (primero) y B; acabar el registro a Ø6 H7 con A sobre la mesa.",
            },
        ],
        "sheet": {"template": "A3", "angle": "first", "scale": "1:1"},
    }
    composed = await cad.ok("drawing", action="compose", decisions=decisions)
    assert "composed sheet" in composed, composed
    # the thickness is a toleranced functional dimension and the overall cannot carry a tolerance: the overall comes off
    await cad.ok(
        "drawing",
        action="remove",
        items=["o1"],
        reason="the thickness is a toleranced functional dimension: the overall cannot carry a tolerance, it would be given twice",
    )
    added = [
        {
            "id": "w1",
            "type": "dim",
            "kind": "distance",
            "from": {"face": "face:4"},
            "to": {"face": "face:2"},
            "view": "top",
            "side": "below",
        },
        {
            "id": "d1",
            "type": "dim",
            "kind": "distance",
            "from": {"face": "face:1"},
            "to": {"face": "face:3"},
            "view": "top",
            "side": "right",
        },
        {
            "id": "t0",
            "type": "dim",
            "kind": "distance",
            "from": {"face": "face:5"},
            "to": {"face": "face:6"},
            "view": "front",
            "stage": "fresado",
            "size_mm": 26,
            "side": "right",
        },
        {
            "id": "t1",
            "type": "dim",
            "kind": "distance",
            "from": {"face": "face:5"},
            "to": {"face": "face:6"},
            "view": "front",
            "stage": "rectificado",
            "tol": 0.02,
            "side": "right",
        },
    ]
    await cad.ok("drawing", action="add", items=added)
    await cad.ok("drawing", action="add", items=[{"id": "sec1", "type": "section", "holes": [s1, s2], "view": "top"}])
    await cad.ok("drawing", action="update", items=[{"id": "rq_view_lines", "hidden": False}])
    notes = []
    y = 141.0
    for name, lines in [
        ("rq_surface_texture", 1),
        ("rq_edge_state", 1),
        ("rq_heat_treatment", 2),
        ("dc_notes1", 2),
        ("dc_notes2", 3),
        ("dc_notes3", 2),
    ]:
        notes.append({"id": name, "at_mm": [229, round(y, 1)]})
        y -= lines * 5.5 + 0.8
    moved = await cad("drawing", action="update", items=notes)
    # a refused move (the free corner is crowded by the stage dimensions) leaves the notes where the planner put them: a known defect
    assert moved.is_error is False or "nothing was changed" in moved.text, moved.text


async def draw_edge_block(cad: Cad) -> None:
    """One proposal in one order: datums A (bottom), B (right face) and C (back face) as edges in the views, the holes of each edge face
    located from them in the view that shows the face."""
    plan = await cad.ok("drawing", action="plan")
    assert "rq_callouts" in plan
    decisions = {
        "datums": [
            {"letter": "A", "face": "face:5"},
            {"letter": "B", "face": "face:2"},
            {"letter": "C", "face": "face:3"},
        ],
        "relations": [
            {"type": "datum_dims", "x_from": "face:2", "y_from": "face:3", "view": "top", "holes": [[0, 0, 40]]},
            {
                "type": "datum_dims",
                "x_from": "face:2",
                "y_from": "face:5",
                "view": "front",
                "holes": [[-22, -30, 20], [22, -30, 20]],
            },
            {
                "type": "datum_dims",
                "x_from": "face:3",
                "y_from": "face:5",
                "view": "right",
                "holes": [[-40, -18, 12], [-40, 18, 12], [-40, 0, 25]],
            },
            {
                "type": "dim",
                "kind": "distance",
                "from": {"at": [-22, -30, 20]},
                "to": {"at": [22, -30, 20]},
                "along": "x",
                "view": "front",
                "tol": 0.1,
            },
        ],
        "finish": [{"type": "finish", "ra": 1.6, "general": True}],
        "notes": [
            {
                "type": "note",
                "text": "Sin recubrimiento (aceite). Plano grabado en la cara superior junto al borde. Cantidad: 1 pieza. Fresado y taladrado.",
            }
        ],
        "sheet": {"template": "A3", "angle": "first", "scale": "1:1"},
    }
    composed = await cad.ok("drawing", action="compose", decisions=decisions)
    assert "composed sheet" in composed, composed


BLOCKING = re.compile(r"^  BLOCKS EXPORT ([a-z_]+)", re.M)


async def blocking_codes(cad: Cad) -> set[str]:
    """The codes of the findings that hold the export of the sheet right now."""
    return set(BLOCKING.findall(await cad.ok("drawing", action="review")))
