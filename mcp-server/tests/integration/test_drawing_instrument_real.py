# ruff: noqa: E501
"""The dimension scheme follows the measuring tool of the shop: the real server over stdio against the VISIBLE FreeCAD (Windows).

The 19 mm plate (100 x 60, four dowels, two counterbores, one M6) is drawn the way an agent draws it and the sheet is compared with
the one the owner corrected by hand: per group of equal holes the spacing between its holes (76 and 44 for the dowels, 28 for the
counterbores) and ONE locator from a real flat face of the plate (12, 36 and 50 from the left edge, 8 from the edge), nothing counted
from one datum baseline (that needs a height gauge), nothing that leans on the symmetry of the plate and no extension line across
the line of another dimension; the dimensions that do not touch share a row, so the plate keeps its 1:2 on an A4 sheet. The side
view a plate does not need is flagged, and no word of the agent closes that finding: the view is taken off. A plate dimensioned
properly is EXPORTED through the gate with nothing in its way that an order of the engineer would have to close: every point the
review lists is answered by the agent with what is true of this sheet. A turned shaft and a flange have no flat face a caliper can be
set against across their round outline: the harness draws no locator from it and asks.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the images of the sheets.
"""

import base64
import os
import re
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import Cad, Reply, pending_points, pending_rows, running_server
from tests.integration.test_drawing_finished_real import CLEARANCE, DOWELS, TAPPED, THICKNESS, make_plate

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

VIEWS = ["front", "top", "right", "iso"]  # what the agent of the owner's session drew: the right view of a plate adds nothing
LOC = re.compile(r"loc datum_dims \w+: \d+ hole\(s\) in the top view; x ([\d, ]+); y ([\d, ]+) \(mm")
OWNER_X = {12, 36, 50, 28, 76}  # the locators of the dowels, the counterbores and the M6, and the spacings of the dowels and the counterbores
OWNER_Y = {8, 44}  # the locator of the dowels from the edge and their spacing (the counterbores add 30; the M6 stand on a row the dowels locate)


def shot(shots: Path, name: str, reply: Reply) -> None:
    assert reply.images, reply.text
    shots.mkdir(parents=True, exist_ok=True)
    (shots / f"{name}.png").write_bytes(base64.b64decode(reply.images[0].data))


def scheme_of(listed: str) -> tuple[list[int], list[int]]:
    found = LOC.search(listed)
    assert found, listed
    return [int(v) for v in found.group(1).split(",")], [int(v) for v in found.group(2).split(",")]


def assert_the_owners_scheme(listed: str, review: str) -> None:
    x, y = scheme_of(listed)
    assert OWNER_X <= set(x) and len(x) == 5, x  # 12, 36, 50 from the left edge, 76 and 28 between the holes: five, each once
    assert OWNER_Y <= set(y) and sorted(y) == [8, 30, 44], y  # 8 from the edge (the dowels' row, which the M6 shares), 44 between the dowels, 30 of the counterbores: the second 8 would close 8 + 44 + 8 on the overall 60
    for corner in (15, 85, 52, 64, 88):  # a baseline from one datum would say these
        assert corner not in x + y, (corner, x, y)
    for finding in ("hole_not_located", "hole_partly_located", "hole_no_reference", "hole_group_no_locator", "dimension_crossing", "dimension_stack_order"):
        assert finding not in review, (finding, review)


async def hole_items(cad: Cad) -> None:
    await cad.ok(
        "drawing",
        action="add",
        items=[
            {"type": "hole_dim", "id": "clear", "at": [[x, y, THICKNESS] for x, y in CLEARANCE]},
            {"type": "hole_dim", "id": "tap", "at": [[*TAPPED, THICKNESS]]},
            {"type": "hole_dim", "id": "dowels", "at": [[x, y, THICKNESS] for x, y in DOWELS], "fit": "H7", "dowel": True},
        ],
    )


# develop's own plate sheets hold these two too (dimension_gap between the two overall dimensions, sheet_underused:small): not this test's subject
@pytest.mark.expected_findings("dimension_gap", "sheet_underused")
async def test_placa19_in_steps_mode_has_the_scheme_of_the_caliper_and_loses_its_side_view(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await make_plate(cad, dowel_fit="H7")
        await cad.ok("drawing", action="create", views=VIEWS, title="Placa 19", author="JR")
        await cad.ok("drawing", action="add", items=[{"type": "overall"}, {"type": "view_lines"}])
        await hole_items(cad)
        listed = await cad.ok("drawing", action="list")
        review = await cad.ok("drawing", action="review")
        shot(shots, "ds2_steps_placa19_agent", await cad("drawing", action="capture"))
        assert_the_owners_scheme(listed, review)
        # the right view of a plate adds nothing: the finding holds the export and the agent's word does not close it
        assert "BLOCKS EXPORT view_adds_nothing:right" in review, review
        assert "views=[right]" in review and "view=right" not in review, review  # the argument the tool takes
        word = "applies: revisado en la hoja, el requisito está cubierto por las cotas puestas"  # what the agent of the owner's session answered
        await cad.ok("drawing", action="answer", id="view_adds_nothing:right", text=word)
        refused = await cad.err("export", action="pdf", path="placa19.pdf")
        assert "- view_adds_nothing:right" in refused, refused  # the claim does not close it: the view is still there
        own = await cad.err("drawing", action="answer", id="view_adds_nothing:right", text="no aplica: la placa es plana")
        assert "measured_not_dismissable" in own, own
        # taking the view off closes it, and the sheet is the owner's: front, top and the isometric view
        await cad.ok("drawing", action="remove_view", views=["right"])
        after = await cad.ok("drawing", action="review")
        assert "view_adds_nothing" not in after, after
        listed = await cad.ok("drawing", action="list")
        assert_the_owners_scheme(listed, after)
        shot(shots, "ds2_steps_placa19_owner", await cad("drawing", action="capture"))


@pytest.mark.expected_findings("collision", "sheet_underused")
async def test_the_plate_keeps_its_one_to_two_on_an_a4_sheet_with_the_rows_grouped(workspace: Path, tmp_path: Path) -> None:
    """With every dimension on a row of its own the rows asked for more room than the sheet has and the scale fell to 1:5."""
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await make_plate(cad, dowel_fit="H7")
        created = await cad.ok("drawing", action="create", template="A4", views=["front", "top", "iso"], title="Placa 19", author="JR")
        assert "A4 sheet at 1:2" in created, created
        await hole_items(cad)
        assert "A4 at 1:2" in await cad.ok("drawing", action="list")  # the holes' callouts and rows did not move the scale
        shot(shots, "ds2_a4_one_to_two", await cad("drawing", action="capture"))


# the notes of the plate: what the engineer decided for THIS part, as the agent writes them on the sheet
NOTES = ["Romper aristas vivas 0,3 mm; sin rebabas. Cantidad: 1. Proceso: fresado y rectificado."]
# what is true of this sheet, point by point: the agent reads the entry (memory get) and the sheet (capture) and answers each one on its own
ANSWERS = {
    "q.callouts": "applies: cada llamada dice cantidad, Ø, profundidad (↧), caja (⌴) y rosca; los 4 registros Ø6 H7 llevan el anillo de registro y se distinguen de los 2 agujeros de paso y de la M6 en la imagen del plano",
    "q.line_weights": "applies: en la captura el contorno es grueso y las cotas, ejes y ocultas finos; todo el texto mide 3,5 mm",
    "drawing-dimensions-from-functional-datums": "applies: ninguna cota es suma o resta de otras; cada posición va del borde de la placa o entre agujeros del mismo grupo, medida con calibre",
    "drawing-feature-size-and-position": "applies: los siete agujeros tienen diámetro y profundidad en su llamada y su posición en la ubicación por defecto; la placa no tiene ranuras ni bolsillos",
    "drawing-general-tolerance-declared": "applies: el rótulo declara ISO 2768-mK y ninguna cota lleva tolerancia más fina que ella salvo el H7 de los registros",
    "drawing-hatching-cut-material-only": "no aplica: la hoja no tiene cortes ni rayados",
    "drawing-hidden-lines-or-section": "no aplica: la placa se lee en la vista superior; la profundidad de la caja y de la rosca va en sus llamadas",
    "drawing-manufacturing-data-in-title-block": "applies: el material (AISI 1045) y la tolerancia general están en el rótulo; las notas dicen aristas, tratamiento, recubrimiento, marcado, cantidad y proceso",
    "drawing-one-dimension-per-feature-direction": "applies: cada rasgo tiene una sola cota por dirección; no hay cadenas cerradas ni la total repetida",
    "drawing-sufficient-views": "applies: la vista superior da todas las cotas de posición y la isométrica muestra la forma; no se repite ninguna vista",
    "notes_heat_treatment": "no aplica: la placa de AISI 1045 se entrega normalizada y no se endurece",
    "notes_coating": "no aplica: pieza interna, sin recubrimiento ni pintura",
    "notes_marking": "no aplica: el número de plano va en el rótulo y no se graba en la pieza",
    "no_view_lines": "no aplica: la hoja no muestra líneas ocultas; los agujeros se leen en la vista superior y sus profundidades van en la llamada",
    "drawing-thread-designation": "applies: la M6 lleva su designación y las profundidades de rosca y de taladro en su llamada",
    "req.functional_dimensions": "met: la separación entre los registros (76 y 44) va en la hoja directamente entre los agujeros; las demás medidas son libres (ISO 2768-mK)",
}


def answer_for(point: str) -> str:
    """The answer written for a point (a finding id is its code, then ``:`` and the place): no catch-all."""
    return ANSWERS[next(key for key in ANSWERS if point == key or point.startswith(key + ":"))]


async def assert_nothing_unaccounted(cad: Cad) -> None:
    """Every point the review still lists has an answer written for it above."""
    findings, questions, memory = await pending_points(cad)
    rows = await pending_rows(cad)
    unknown = [p for p in (*findings, *questions, *memory, *rows) if not any(p == k or p.startswith(k + ":") for k in ANSWERS)]
    assert not unknown, f"points this test has no answer for (fix the sheet or write the answer): {unknown}"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "the harness holds the export of a plate dimensioned properly with points only an order of the engineer closes: the notes of "
        "the sheet are drawn as loose symbols instead of TechDraw's own objects (native_fallback, foreign_symbol), a counterbore "
        "asks for a section although its depth is in the callout (hidden_feature_no_section) and the notes block lies on the "
        "dimension rows (collision). Remove this mark when the harness stops raising them: the test is the proof."
    ),
)
@pytest.mark.expected_findings("sheet_underused")
async def test_a_plate_dimensioned_properly_is_exported_through_the_gate_with_no_order(workspace: Path, tmp_path: Path) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await make_plate(cad, dowel_fit="H7")
        await cad.ok("doc", action="set_material", material="AISI 1045")
        await cad.ok("memory", action="brief", drawing_review="propio", text="Ana: el agente revisa el plano por su cuenta")
        await cad.ok("drawing", action="create", views=["top", "iso"], title="Placa 19", author="Taller Sur")
        await hole_items(cad)
        await cad.ok(
            "drawing",
            action="add",
            items=[*({"type": "note", "text": text} for text in NOTES), {"type": "finish", "general": True, "ra": 3.2}],
        )
        listed = await cad.ok("drawing", action="list")
        review = await cad.ok("drawing", action="review")
        assert_the_owners_scheme(listed, review)
        shot(shots, "ds2_exported_placa19", await cad("drawing", action="capture"))
        # nothing of the sheet holds the export but the points the agent answers; none of them is a measured one that needs an order
        await assert_nothing_unaccounted(cad)
        refused = await cad.err("export", action="pdf", path="placa19.pdf")
        assert "drawing_unfinished" in refused and not (workspace / "placa19.pdf").exists()
        findings, questions, memory = await pending_points(cad)
        for entry in memory:
            await cad.ok("memory", action="get", id=entry)
        for point in (*findings, *questions, *memory, *await pending_rows(cad)):
            await cad.ok("drawing", action="answer", id=point, text=answer_for(point))  # no `by`, no `reason`: no order of the engineer
        for _ in range(3):  # answering opens the next points (the rows the answers close, the entries that follow)
            await assert_nothing_unaccounted(cad)
            more = [*await pending_points(cad), await pending_rows(cad)]
            if not any(more):
                break
            for point in (p for group in more for p in group):
                await cad.ok("drawing", action="answer", id=point, text=answer_for(point))
        done = await cad.ok("drawing", action="review")
        assert "export of this sheet is open" in done, done
        exported = await cad.ok("export", action="pdf", path="placa19.pdf")
        assert "TechDraw's own export" in exported, exported
        assert (workspace / "placa19.pdf").read_bytes().startswith(b"%PDF")


async def position_sheet(cad: Cad) -> str:
    """The plate with a position tolerance on its dowels and no note on how it is measured; returns the datum face."""
    await make_plate(cad, dowel_fit="H7")
    await cad.ok("drawing", action="create", template="A3", views=["front", "top", "iso"], title="Placa 19", author="JR")
    await cad.ok("drawing", action="add", items=[{"type": "view_lines"}])
    await hole_items(cad)
    faces = await cad.ok("inspect", action="faces", op="base")
    bottom = next(line.split()[0] for line in faces.splitlines() if "n(0, 0, -1)" in line)
    await cad.ok("drawing", action="add", items=[{"type": "datum", "id": "dA", "letter": "A", "face": bottom}])
    frame = {"type": "gdt", "id": "pos", "char": "position", "value": 0.02, "diameter": True, "datums": ["A"], "on": "dowels"}
    await cad.ok("drawing", action="add", items=[frame])
    return bottom


@pytest.mark.expected_findings("sheet_underused")
async def test_a_hole_in_the_middle_of_a_plate_is_dimensioned_from_an_edge_and_a_bare_position_tolerance_asks_for_a_note(
    workspace: Path, tmp_path: Path
) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await position_sheet(cad)
        listed = await cad.ok("drawing", action="list")
        x, y = scheme_of(listed)
        # the two counterbores stand in the middle of the plate across y (y = 0): their place is counted from an edge like any other
        assert 30 in y and 36 in x, (x, y)
        review = await cad.ok("drawing", action="review")
        assert "hole_not_located" not in review and "hole_group_no_locator" not in review, review
        assert "BLOCKS EXPORT measurement_unstated:pos" in review, review  # no CMM note: nobody says how the position is measured
        claim = "applies: la posición se verifica en la máquina de medir del taller"
        await cad.ok("drawing", action="answer", id="measurement_unstated:pos", text=claim)
        refused = await cad.err("export", action="pdf", path="placa19.pdf")
        assert "- measurement_unstated:pos" in refused, refused  # the agent's word does not close it: only the note does
        own = await cad.err("drawing", action="answer", id="measurement_unstated:pos", text="no aplica: el taller tiene MMC")
        assert "measured_not_dismissable" in own, own
        await cad.ok("drawing", action="add", items=[{"type": "note", "text": "Verificar la posición"}])  # a loose note names no feature
        assert "measurement_unstated:pos" in await cad.ok("drawing", action="review")
        await cad.ok("drawing", action="add", items=[{"type": "note", "text": "Posición de los 4× Ø6 H7: verificar en MMC"}])
        assert "measurement_unstated" not in await cad.ok("drawing", action="review")
        shot(shots, "ds2_position_note", await cad("drawing", action="capture"))


TOP_CYLINDER_EDGE = {
    "kind": "edge",
    "between": [
        {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}},
        {"kind": "face", "from_op": "base", "select": {"surface": "cylinder"}},
    ],
}
BOTTOM_CYLINDER_EDGE = {
    "kind": "edge",
    "between": [
        {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, -1]}},
        {"kind": "face", "from_op": "base", "select": {"surface": "cylinder"}},
    ],
}


async def make_shaft_with_a_cross_hole(cad: Cad) -> None:
    """A Ø30 x 100 shaft with a 1,5 chamfer on both ends and a Ø6 radial hole through its axis 40 from the end (a pin hole)."""
    await cad.ok("doc", action="new", name="eje")
    await cad.ok("doc", action="set_material", material="AISI 1045")
    circle = [{"kind": "circle", "name": "od", "center": [0, 0], "diameter": 30}]
    await cad.ok("op", name="sketch", id="outline", params={"on": {"kind": "plane", "name": "XY"}, "entities": circle})
    await cad.ok("op", name="extrude", id="base", params={"sketch": "outline", "depth_mm": 100})
    chamfer = {"edges": [TOP_CYLINDER_EDGE, BOTTOM_CYLINDER_EDGE], "distance1_mm": 1.5}
    await cad.ok("op", name="chamfer", id="end_break", params=chamfer)
    wall = {"kind": "face", "from_op": "end_break", "select": {"surface": "cylinder"}}
    await cad.ok("op", name="hole", id="cross", params={"face": wall, "position": [40, 270], "standard": "simple", "diameter_mm": 6, "end_condition": "through_all"})


@pytest.mark.expected_findings("collision", "sheet_underused", "foreign_symbol")
async def test_a_radial_hole_in_a_shaft_is_located_from_its_end_and_by_the_axis_with_no_locator_from_the_silhouette(
    workspace: Path, tmp_path: Path
) -> None:
    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        await make_shaft_with_a_cross_hole(cad)
        await cad.ok("drawing", action="create", template="A3", views=["front", "right", "iso"], title="Eje")
        await cad.ok("drawing", action="add", items=[{"type": "hole_dim", "all": True}, {"type": "view_lines"}])
        listed = await cad.ok("drawing", action="list")
        shot(shots, "ds2_shaft_cross_hole", await cad("drawing", action="capture"))
        review = await cad.ok("drawing", action="review")
        # one locator, along the shaft from the nearest end face (10: the real flat face); across it the axis locates the hole,
        # so there is no 15 from the silhouette of the cylinder and no y at all
        assert re.search(r"loc datum_dims ok: 1 hole\(s\) in the front view; x 10 \(mm", listed), listed
        assert "hole_not_located" not in review and "hole_partly_located" not in review, review


@pytest.mark.expected_findings("collision", "sheet_underused", "foreign_symbol")
async def test_the_holes_of_a_flange_get_no_locator_from_its_round_outline(workspace: Path, tmp_path: Path) -> None:
    """The bolt circle and the lone pin of the flange are located from the axis (pattern_dim); the harness draws nothing from the tangent
    points of the Ø140 outline, and says so in the sheet it lists."""
    from tests.integration.test_drawing_pattern_real import flange  # noqa: PLC0415

    shots = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    async with running_server(workspace, visible=True) as (cad, _):
        lone = await flange(cad)
        await cad.ok("drawing", action="create", template="A3", views=["top", "iso"])
        await cad.ok("drawing", action="add", items=[{"type": "hole_dim", "all": True}, {"type": "view_lines"}])
        await cad.ok("drawing", action="add", items=[{"type": "pattern_dim", "at": lone}, {"type": "pattern_dim", "at": lone, "kind": "angle"}])
        listed = await cad.ok("drawing", action="list")
        shot(shots, "ds2_flange", await cad("drawing", action="capture"))
        assert "on the axis of the part: the axis locates them" in listed, listed  # the bore
        assert "Ø80 pitch circle" in listed and "pd2" in listed, listed  # the bolt circle and the lone pin: from the axis
        review = await cad.ok("drawing", action="review")
        assert "hole_not_located" not in review and "hole_group_no_locator" not in review, review
