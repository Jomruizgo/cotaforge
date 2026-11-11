# ruff: noqa: E501
"""A finished drawing end to end: the real server over stdio against the VISIBLE FreeCAD (Windows).

A 19 mm plate with four D6 dowel holes (H7), two counterbored D9 holes and an M6. First a deliberately incomplete
drawing (dowels with no callout and no tolerance, no material, no notes; the default location already gives every hole
its position): the review names the holes, the callout and fit findings, the notes and the questions of judgement; the
export of the PDF is refused and lists them; an
answer to a memory question without a prior ``memory get`` is refused. Then some findings are fixed and the rest answered
(some with "no aplica" and a reason); the document is saved, closed and reopened and the answers are still there; the
PDF is written by TechDraw's own export; the engineer approves and "Aprobó" is filled.

The project reviews its drawings itself here (``drawing_review=propio``); who else may review (the manufacturer agent,
the engineer, nobody) is ``test_drawing_review_real.py``.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the transcript, the sheet images and the PDFs.
"""

import base64
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import Cad, pending_points, running_server

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

XY = {"kind": "plane", "name": "XY"}
TOP = {"kind": "face", "from_op": "base", "select": {"normal": [0, 0, 1]}}
THICKNESS = 19
DOWELS = [(-38, 22), (38, 22), (-38, -22), (38, -22)]
CLEARANCE = [(-14, 0), (14, 0)]
TAPPED = (0, 22)


async def make_plate(cad: Cad, dowel_fit: str | None = None) -> None:
    """The 19 mm plate; ``dowel_fit`` is declared on the four dowel holes (a callout that asks for a fit the hole does not
    declare is a finding of its own)."""
    await cad.ok("doc", action="new", name="placa19")
    rect = [{"kind": "rect", "name": "plate", "center": [0, 0], "width": 100, "height": 60}]
    await cad.ok("op", name="sketch", id="outline", params={"on": XY, "entities": rect})
    await cad.ok("op", name="extrude", id="base", params={"sketch": "outline", "depth_mm": THICKNESS})
    face = {"face": TOP, "standard": "simple", "end_condition": "through_all"}
    for number, (x, y) in enumerate(DOWELS, start=1):
        fit = {"fit": dowel_fit} if dowel_fit else {}
        params = {**face, "at": [x, y, THICKNESS], "diameter_mm": 6, **fit}
        await cad.ok("op", name="hole", id=f"dowel{number}", params=params)
    for number, (x, y) in enumerate(CLEARANCE, start=1):
        counterbore = {"diameter_mm": 14, "depth_mm": 9}
        params = {**face, "at": [x, y, THICKNESS], "diameter_mm": 9, "counterbore": counterbore}
        await cad.ok("op", name="hole", id=f"clear{number}", params=params)
    tapped = {
        "face": TOP,
        "at": [*TAPPED, THICKNESS],
        "standard": "iso_tapped",
        "size": "M6",
        "end_condition": "blind",
        "depth_mm": 12,
        "thread_depth_mm": 10,
    }
    await cad.ok("op", name="hole", id="tapped", params=tapped)


async def declare_references(cad: Cad) -> None:
    """What the engineer says of this plate: it is symmetric across x (the sheet counts x from that plane by itself) but not
    across y, so the positions along y are counted from the front face. Without a reference the default location asks."""
    faces = await cad.ok("inspect", action="faces", op="base")
    front = next(line.split()[0] for line in faces.splitlines() if "n(0, -1, 0)" in line)
    await cad.ok("drawing", action="add", items=[{"type": "datum", "id": "dC", "letter": "C", "face": front}])


class Transcript:
    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.lines: list[str] = []
        folder.mkdir(parents=True, exist_ok=True)

    def say(self, title: str, text: str) -> None:
        self.lines += [f"=== {title}", text, ""]
        (self.folder / "f6_transcript.txt").write_text("\n".join(self.lines), encoding="utf-8")

    async def shot(self, cad: Cad, name: str) -> None:
        reply = await cad("drawing", action="capture")
        assert not reply.is_error, reply.text
        (self.folder / name).write_bytes(base64.b64decode(reply.images[0].data))


async def test_a_plate_drawing_is_refused_until_it_is_finished_then_written_and_approved(
    workspace: Path, tmp_path: Path
) -> None:
    out = Transcript(Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots"))
    async with running_server(workspace, visible=True) as (cad, _):
        await make_plate(cad)
        await cad.ok(
            "memory", action="brief", drawing_review="propio", text="Ana: el agente revisa el plano por su cuenta"
        )

        # 1. a deliberately incomplete drawing: no material, no notes, the dowels not called out
        created = await cad.ok("drawing", action="create", template="A3", scale="1:1", title="Placa 19", author="JR")
        out.say("create", created)
        await declare_references(cad)
        added = await cad.ok(
            "drawing",
            action="add",
            items=[
                {"type": "view_lines"},
                {"type": "hole_dim", "id": "clear", "at": [[x, y, THICKNESS] for x, y in CLEARANCE]},
                {"type": "hole_dim", "id": "tap", "at": [[*TAPPED, THICKNESS]]},
            ],
        )
        out.say("add (the dowels are left out on purpose)", added)
        await out.shot(cad, "01_incompleto.png")

        # 2. the review lists what is missing, naming the holes
        review = await cad.ok("drawing", action="review")
        out.say("review of the incomplete drawing", review)
        assert "datum_dims ok: 7 hole(s)" in review and "hole_not_located" not in review  # loc locates them all
        for x, y in DOWELS:  # a through hole opens on the face it was drilled from or on the opposite one
            assert re.search(rf"hole_no_callout:@{x},{y},\d+ .*Ø6 \[{x}, {y}, \d+\]", review), (x, y)
        assert review.count("fit_hole_no_tolerance:@") == 4  # the four dowel-size holes ask for their class
        for code in (
            "notes_material",
            "notes_edges",
            "notes_heat_treatment",
            "notes_coating",
            "notes_roughness",
            "notes_marking",
        ):
            assert code in review, code
        for question in ("q.callouts", "q.line_weights"):  # the rest are memory principles, asked once
            assert question in review, question
        for gone in ("q.views", "q.sections", "q.dimensions", "q.tolerances", "q.fab_notes"):
            assert gone not in review, gone
        assert "drawing-manufacturing-data-in-title-block" in review and "[NOT read: memory get first]" in review
        assert "export of this sheet is REFUSED" in review

        # 3. the export is refused and lists exactly what is missing; nothing is written
        refused = await cad.err("export", action="pdf", path="placa19.pdf")
        out.say("export pdf (refused)", refused)
        assert "drawing_unfinished" in refused and "hole_no_callout:@-38,22," in refused
        assert (
            "notes_material:material" in refused
            and "q.callouts" in refused
            and "drawing-manufacturing-data-in-title-block" in refused
        )
        assert "no draft PDF" in refused and not (workspace / "placa19.pdf").exists()

        # 4. a memory question cannot be answered without reading the entry first
        unread = await cad.err(
            "drawing",
            action="answer",
            id="drawing-manufacturing-data-in-title-block",
            text="applies: las notas dicen material y aristas",
        )
        out.say("answer a memory entry without memory get (refused)", unread)
        assert "not_read" in unread

        # 5. fix what can be fixed ...
        fixed = await cad.ok(
            "drawing",
            action="add",
            items=[
                {"type": "hole_dim", "id": "dowels", "at": [[x, y, THICKNESS] for x, y in DOWELS], "fit": "H7"},
                {"type": "note", "text": "Material: AISI 1045, lámina rectificada de 19 mm."},
                {"type": "note", "text": "Romper aristas vivas 0,3 mm; sin rebabas."},
            ],
        )
        out.say("add (the fixes)", fixed)
        after_fixes = await cad.ok("drawing", action="review")
        out.say("review after the fixes", after_fixes)
        for gone in ("hole_no_callout", "fit_hole_no_tolerance", "notes_material", "notes_edges"):
            assert gone not in after_fixes, gone
        assert "notes_heat_treatment" in after_fixes  # still to be answered
        await out.shot(cad, "02_corregido.png")

        # ... and answer the rest: some "no aplica" with their reason, the memory ones after reading them
        findings, questions, memory = await pending_points(cad)
        reasons = {
            "notes_heat_treatment": "no aplica: el acero 1045 se entrega normalizado y la pieza no se endurece",
            "notes_coating": "no aplica: pieza interna sin recubrimiento ni pintura",
            "notes_marking": "no aplica: el número de plano va en el rótulo y no se graba",
            "notes_roughness": "no aplica: el rectificado de la lámina da la rugosidad de las caras",
        }
        for point in findings:
            reason = next((r for code, r in reasons.items() if point.startswith(code)), None)
            text = reason or "applies: revisado en la hoja, el requisito está cubierto por las cotas puestas"
            answered = await cad.ok("drawing", action="answer", id=point, text=text)
            assert f"answered: {point}" in answered
        for point in questions:
            await cad.ok(
                "drawing", action="answer", id=point, text="applies: lo verifiqué en la hoja con la imagen del plano"
            )
        for entry in memory:
            await cad.ok("memory", action="get", id=entry)
            answered = await cad.ok(
                "drawing",
                action="answer",
                id=entry,
                text="no aplica: una placa sin soldadura ni asientos de rodamiento",
            )
            assert f"answered: {entry}" in answered
        done = await cad.ok("drawing", action="review")
        out.say("review after the answers", done)
        assert "export of this sheet is open" in done and "answered (the engineer reads these)" in done
        assert "DOES NOT APPLY: el acero 1045 se entrega normalizado" in done

        # 6. save, close, reopen: the answers are still there
        await cad.ok("doc", action="save", path="placa19.FCStd")
        await cad.ok("doc", action="close")
        await cad.ok("doc", action="open", path="placa19.FCStd")
        reopened = await cad.ok("drawing", action="review")
        out.say("review after save, close and reopen", reopened)
        assert "export of this sheet is open" in reopened
        assert "DOES NOT APPLY: el acero 1045 se entrega normalizado" in reopened
        assert "applies: lo verifiqué" not in reopened  # shown without the prefix the agent typed

        # 7. the PDF now goes out, and it is TechDraw's own
        exported = await cad.ok("export", action="pdf", path="placa19.pdf")
        out.say("export pdf", exported)
        assert "TechDraw's own export" in exported, exported
        pdf = workspace / "placa19.pdf"
        assert pdf.read_bytes().startswith(b"%PDF") and pdf.stat().st_size > 10000
        shutil.copy2(pdf, out.folder / "placa19.pdf")

        # 8. the engineer approves; "Aprobó" is filled and a change of the sheet empties it
        approved = await cad.ok(
            "drawing", action="approve", sheet="Drawing", by="Engineer", reason="the engineer: approve it"
        )
        out.say("approve", approved)
        listed = await cad.ok("drawing", action="list")
        assert "approval_person=Engineer" in listed
        final = await cad.ok("drawing", action="review")
        assert "approved by: Engineer" in final
        await cad.ok("export", action="pdf", path="placa19_aprobado.pdf")
        shutil.copy2(workspace / "placa19_aprobado.pdf", out.folder / "placa19_aprobado.pdf")
        await out.shot(cad, "03_aprobado.png")
        await cad.ok("drawing", action="add", items=[{"type": "note", "text": "Marcar el número de plano."}])
        emptied = await cad.ok("drawing", action="review")
        out.say("a change after the approval", emptied)
        assert 'not approved ("Aprobó" is empty)' in emptied
