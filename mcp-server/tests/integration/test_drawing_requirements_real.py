# ruff: noqa: E501
"""What the standards ask a sheet to carry, end to end on the real headless FreeCAD through the stdio server: two synthetic
parts (a plate; a shaft with a tapped hole, a fitted hole and a keyway) list the rows that apply to THEM, the export is
refused while a row is open, a measured row cannot be dismissed on the agent's word, and the export passes once every row is
met or answered. The paper is chosen from the part, a bolt pattern dimensioned centre to centre is located, and a plane of
symmetry is a reference only when the intent declares it.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the transcript.
"""

import base64
import os
import re
from pathlib import Path

from tests.integration.conftest import Cad, finish_drawing, pending_points, pending_rows, running_server

XY = {"kind": "plane", "name": "XY"}
TOP = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}


class Transcript:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.lines: list[str] = []
        folder.mkdir(parents=True, exist_ok=True)

    def say(self, title: str, text: str) -> None:
        self.lines += [f"=== {title}", text, ""]
        (self.folder / "d2_transcript.txt").write_text("\n".join(self.lines), encoding="utf-8")


def transcript(tmp_path: Path) -> Transcript:
    return Transcript(Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots"))


async def make_plate(cad: Cad, name: str = "placa") -> None:
    """100 x 60 x 8, four clearance holes in a 2 x 2 pattern (70 x 40 apart, centred)."""
    await cad.ok("doc", action="new", name=name)
    await cad.ok("doc", action="set_material", material="AISI 1045")
    rect = [{"kind": "rect", "name": "plate", "center": [0, 0], "width": 100, "height": 60}]
    await cad.ok("op", name="sketch", id="outline", params={"on": XY, "entities": rect})
    await cad.ok("op", name="extrude", id="base", params={"sketch": "outline", "depth_mm": 8})
    for number, (x, y) in enumerate([(-35, -20), (35, -20), (-35, 20), (35, 20)], start=1):
        params = {"face": TOP, "at": [x, y, 8], "standard": "simple", "diameter_mm": 6.6, "end_condition": "through_all"}
        await cad.ok("op", name="hole", id=f"h{number}", params=params)


async def make_shaft(cad: Cad) -> None:
    """A Ø30 x 80 shaft: an M8 tapped hole and a Ø6 H7 hole in its end, and a keyway on its side."""
    await cad.ok("doc", action="new", name="eje")
    await cad.ok("doc", action="set_material", material="AISI 1045")
    await cad.ok("op", name="cylinder", id="base", params={"diameter_mm": 30, "height_mm": 80, "axis": "Z"})
    tapped = {"face": TOP, "at": [0, 0, 80], "standard": "iso_tapped", "size": "M8", "end_condition": "blind", "depth_mm": 16}
    await cad.ok("op", name="hole", id="tapped", params=tapped)
    fitted = {"face": TOP, "at": [9, 0, 80], "standard": "simple", "diameter_mm": 6, "fit": "H7", "end_condition": "blind", "depth_mm": 10}
    await cad.ok("op", name="hole", id="dowel", params=fitted)
    # a keyway 6 wide, 5 deep and 30 long (z = 20 to 50) on the side of the shaft, from a plane tangent to it
    plane = {"define": {"how": "offset", "base": {"kind": "plane", "name": "XZ"}, "distance_mm": 15}}
    await cad.ok("op", name="ref_plane", id="keyplane", params=plane)
    slot = [{"kind": "rect", "name": "k", "center": [0, -35], "width": 6, "height": 30}]  # the XZ sketch runs z the other way
    await cad.ok("op", name="sketch", id="key_sk", params={"on": {"kind": "plane", "from_op": "keyplane"}, "entities": slot})
    await cad.ok("op", name="cut", id="slot1", params={"sketch": "key_sk", "depth_mm": 5}, intent="slot on the side of the shaft")


SHAFT_INTENT = """# Intención: eje

## Tratamientos y acabado
- Temple y revenido a 40 HRC.
- Sin recubrimiento.
- Romper aristas vivas 0,5 mm; sin rebabas.
- Rugosidad general Ra 3,2.

## Proceso
- Cantidad: 10 pzas; proceso: torneado y fresado.
"""
PLATE_INTENT = """# Intención: placa

## Tratamientos y acabado
- Romper aristas vivas 0,3 mm; sin rebabas.
- Sin tratamiento térmico.
"""


def write_intent(workspace: Path, part: str, text: str) -> None:
    (workspace / "drawings").mkdir(exist_ok=True)
    (workspace / "drawings" / f"{part}-intent.md").write_text(text, encoding="utf-8")


async def snapshot(cad: Cad, out: Transcript, name: str) -> None:
    """The sheet as the engineer sees it, kept as a PNG beside the transcript (and checked to be one)."""
    reply = await cad("drawing", action="capture")
    assert not reply.is_error and reply.images, reply.text
    data = base64.b64decode(reply.images[0].data)
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    (out.folder / name).write_bytes(data)


def rows_of(text: str) -> dict[str, str]:
    """``{row id: state}`` of the rows a review or a create lists."""
    return {row: state for state, row in re.findall(r"^  \[([^\]]+)\] (req\.\w+) ", text, re.M)}


async def face_with_normal(cad: Cad, normal: str) -> str:
    faces = await cad.ok("inspect", action="faces", op="base")
    return next(line.split()[0] for line in faces.splitlines() if f"n({normal})" in line)


async def test_a_plate_lists_its_rows_and_the_symmetry_is_a_reference_only_when_declared(workspace: Path, tmp_path: Path) -> None:
    out = transcript(tmp_path)
    async with running_server(workspace) as (cad, _):
        await cad.ok("memory", action="brief", drawing_review="propio", text="la prueba: el agente revisa el plano por su cuenta")
        write_intent(workspace, "placa", PLATE_INTENT)
        await make_plate(cad)
        created = await cad.ok("drawing", action="create", views=["top"], title="Placa")
        out.say("create (no paper stated, no symmetry declared)", created)
        assert "paper chosen from the part" in created and "this sheet must carry" in created
        assert "added automatically" in created and "rq_callouts" in created and "rq_edge_state" in created and "rq_heat_treatment" in created
        listing = rows_of(created)
        assert listing["req.edge_state"] == "added automatically" and listing["req.heat_treatment"] == "added automatically"
        assert listing["req.dimensions"] == "MISSING" and listing["req.surface_texture"] == "MISSING"  # no Ra anywhere: a question
        assert listing["req.title_block"] in ("met", "added automatically") and listing["req.general_tolerance"] in ("met", "added automatically")
        assert listing["req.centre_lines"] in ("met", "added automatically")
        assert "req.thread" not in listing and "req.fit_class" not in listing  # a plate with clearance holes has neither
        assert "no reference to count the position" in created  # the plate is symmetric by shape; nobody said so: no centre line, asked

        await snapshot(cad, out, "d2_plate_automatic.png")
        stated = await cad.ok("drawing", action="update", symmetry=True)  # the design intent says it is symmetric
        out.say("update symmetry=true", stated)
        review = await cad.ok("drawing", action="review")
        out.say("review after declaring the symmetry", review)
        assert "hole_not_located" not in review  # the plane of symmetry is now a reference

        refused = await cad.err("export", action="pdf", path="placa.pdf")
        out.say("export refused", refused)
        assert "drawing_unfinished" in refused and "requirements the sheet must carry" in refused and "req.surface_texture" in refused

        await cad.ok(
            "drawing",
            action="add",
            items=[
                {"type": "note", "text": "Material: AISI 1045, lámina de 8 mm. Cantidad: 10 pzas, corte láser.", "at_mm": [30, 60]},
                {"type": "note", "text": "Sin recubrimiento. Marcado: número de plano grabado.", "at_mm": [30, 50]},
                {"type": "note", "text": "Rugosidad general Ra 3,2.", "at_mm": [30, 45]},
            ],
        )
        after = await cad.ok("drawing", action="requirements")
        out.say("requirements after the notes", after)
        assert not [row for row, state in rows_of(after).items() if state not in ("met", "added automatically") and row != "req.functional_dimensions"]
        assert rows_of(after)["req.functional_dimensions"] == "NEEDS AN ANSWER"  # the intent declares no functional dimension: asked
        await finish_drawing(cad)
        exported = await cad.ok("export", action="pdf", path="placa.pdf")
        out.say("export", exported)
        assert (workspace / "placa.pdf").read_bytes().startswith(b"%PDF")


async def test_a_bolt_pattern_dimensioned_centre_to_centre_is_located(workspace: Path, tmp_path: Path) -> None:
    out = transcript(tmp_path)
    async with running_server(workspace) as (cad, _):
        await cad.ok("memory", action="brief", drawing_review="propio", text="la prueba: el agente revisa el plano por su cuenta")
        await make_plate(cad)
        await cad.ok("drawing", action="create", views=["top"], title="Placa")
        left, front = await face_with_normal(cad, "-1, 0, 0"), await face_with_normal(cad, "0, -1, 0")
        at = {"a": [-35, -20, 8], "b": [35, -20, 8], "c": [-35, 20, 8], "d": [35, 20, 8]}

        def distance(start: dict | list, end: dict | list) -> dict:
            return {"type": "dim", "kind": "distance", "from": start if isinstance(start, dict) else {"at": start}, "to": {"at": end}}

        await cad.ok(
            "drawing",
            action="add",
            items=[
                {"type": "hole_dim", "all": True},
                distance({"face": left}, at["a"]),
                distance({"face": front}, at["a"]),
                distance(at["a"], at["b"]),  # centre to centre along the row
                distance(at["a"], at["c"]),  # and along the column
                distance(at["b"], at["d"]),
            ],
        )
        review = await cad.ok("drawing", action="review")
        out.say("review of the pattern dimensioned centre to centre", review)
        assert "hole_partly_located" not in review and "hole_not_located" not in review and "hole_no_reference" not in review


async def test_a_threaded_fitted_shaft_activates_measured_rows_that_only_the_engineer_can_dismiss(workspace: Path, tmp_path: Path) -> None:
    out = transcript(tmp_path)
    async with running_server(workspace) as (cad, _):
        await cad.ok("memory", action="brief", drawing_review="propio", text="la prueba: el agente revisa el plano por su cuenta")
        write_intent(workspace, "eje", SHAFT_INTENT)
        await make_shaft(cad)
        created = await cad.ok("drawing", action="create", title="Eje", views=["front", "right"])
        out.say("create (shaft)", created)
        listing = rows_of(created)
        assert "added automatically" in created and "rq_callouts" in created
        assert listing["req.thread"] == "added automatically" and listing["req.fit_class"] == "added automatically"
        assert listing["req.heat_treatment"] == "added automatically" and listing["req.surface_texture"] == "added automatically"
        assert listing["req.keyway"] == "MISSING" and listing["req.marking"] == "MISSING"  # no value known: the fit class, the marking
        assert "rq_keyway" in created and "the width, the depth (d - t1 / d + t2), the length and the position of the keyway" in created
        await snapshot(cad, out, "d2_shaft_automatic.png")
        text = await cad.ok("drawing", action="list")
        assert "M8" in text and "H7" in text  # the callout prints the thread and the declared fit; nobody asked
        for dim in ("rq_keyway_w", "rq_keyway_t", "rq_keyway_l", "rq_keyway_p"):
            assert dim in text, dim  # width, depth, length and position of the keyway, measured on the model
        assert "fit class of the width" in created  # and the one thing nobody knows is a question
        assert "[measured]" in created.split("] req.keyway")[1].splitlines()[0]  # the slot is found by its walls, not by its name
        assert "[measured]" in created.split("] req.thread")[1].splitlines()[0]
        refused = await cad.err("export", action="pdf", path="eje.pdf")
        assert "req.keyway" in refused and "req.marking" in refused and "req.thread" not in refused  # the harness met the thread

        no_reason = await cad.err("drawing", action="remove", items=["rq_callouts"])
        assert "removed only with a reason" in no_reason
        measured = await cad.err("drawing", action="remove", items=["rq_callouts"], reason="no hacen falta las llamadas")
        out.say("a measured automatic item removed on the agent's word", measured)
        assert "measured_not_dismissable" in measured and "threaded holes" in measured
        no = await cad.err("drawing", action="answer", id="req.thread", text="no aplica: el eje no lleva rosca de verdad")
        assert "measured_not_dismissable" in no
        await cad.ok("drawing", action="remove", items=["rq_callouts"], reason="Ana: las llamadas van en una hoja aparte", by="Ana")
        review = await cad.ok("drawing", action="review")
        out.say("review after the engineer's order (shaft)", review)
        assert "answered: DOES NOT APPLY" in review and "(ordered by Ana)" in review
        assert "rq_callouts" not in await cad.ok("drawing", action="list")  # answered: it is not added again
        await cad.ok("drawing", action="add", items=[{"type": "hole_dim", "all": True}])  # the drafter puts them back as his own

        assert "measured_not_dismissable" in await cad.err("drawing", action="answer", id="req.keyway", text="no aplica: el chavetero lo ejecuta el montador")
        await cad.ok(
            "drawing", action="answer", id="req.keyway", text="no aplica: el chavetero lo ejecuta el montador", by="Ana", reason="Ana: el chavetero lo hace el montador"
        )
        pending, _, _ = await pending_points(cad)
        out.say("what is still open", f"findings {pending}; rows {await pending_rows(cad)}")
        entry = "machining-inner-radius-and-cavity-depth"  # the keyway has sharp corners: a measured alert, open for the export
        await cad.ok("memory", action="get", id=entry)
        await cad.ok("memory", action="answer", id=entry, by="Ana", text="does not apply: Ana: el chavetero se fresa con esquinas vivas")
        await finish_drawing(cad)
        exported = await cad.ok("export", action="pdf", path="eje.pdf")
        out.say("export (shaft)", exported)
        assert (workspace / "eje.pdf").read_bytes().startswith(b"%PDF")
