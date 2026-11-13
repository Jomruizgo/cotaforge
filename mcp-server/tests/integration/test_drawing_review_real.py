# ruff: noqa: E501
"""Who reviews a drawing, end to end: the real server over stdio against the VISIBLE FreeCAD (Windows).

The test plays the agent that draws and, where a reviewer is needed, the reviewer too (the server cannot prove who records a
report; what it keeps is the order and the answers): the manufacturer's report, the engineer's choice (the wait is set to
0 minutes so the "no answer in time" case needs no waiting), the engineer's own report, ``propio`` and ``ninguno``.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the transcript.
"""

import os
import re
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import Cad, close_findings, running_server
from tests.integration.test_drawing_finished_real import CLEARANCE, DOWELS, TAPPED, THICKNESS, make_plate

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

WHY = "no aplica: la placa se hace como esta dibujada en el taller"


async def clean_plate_sheet(cad: Cad) -> None:
    await make_plate(cad)
    await cad.ok(
        "drawing", action="create", template="A3", views=["top"], scale="1:1", title="Placa 19", author="Taller Sur"
    )
    await cad.ok(
        "drawing",
        action="add",
        items=[
            {"type": "hole_dim", "id": "clear", "at": [[x, y, THICKNESS] for x, y in CLEARANCE]},
            {"type": "hole_dim", "id": "tap", "at": [[*TAPPED, THICKNESS]]},
            {
                "type": "hole_dim",
                "id": "dowels",
                "at": [[x, y, THICKNESS] for x, y in DOWELS],
                "fit": "H7",
                "dowel": True,
            },
            {"type": "note", "text": "Material: AISI 1045, lámina rectificada de 19 mm."},
            {"type": "note", "text": "Espesor 19 mm. Romper aristas vivas 0,3 mm; sin rebabas."},
        ],
    )


def ids(text: str, prefix: str) -> list[str]:
    return re.findall(rf"\b({prefix}\d+\.\d+)\b", text)


async def test_the_manufacturer_review_gates_the_export(workspace: Path, tmp_path: Path) -> None:
    lines: list[str] = []
    async with running_server(workspace, visible=True) as (cad, _):
        await clean_plate_sheet(cad)
        await cad.ok("memory", action="brief", drawing_review="fabricante", text="Ana: que la revise el fabricante")
        listing = await cad.ok("drawing", action="list")
        assert "printed on the sheet" in listing and "Material: AISI 1045" in listing  # what the manufacturer reads
        await close_findings(cad)  # the CAD's own findings hold the export in every mode but ninguno

        refused = await cad.err("export", action="pdf", path="placa19.pdf")
        lines += ["=== refused (no manufacturer report)", refused]
        assert "no fabricator report yet" in refused and 'subagent_type="fabricator"' in refused
        assert not (workspace / "placa19.pdf").exists()

        recorded = await cad.ok(
            "drawing",
            action="review_report",
            sheet="Drawing",
            by="fabricator",
            items=[
                {
                    "kind": "missing",
                    "where": "vista superior, M6",
                    "text": "No dice la clase de rosca",
                    "fix": "Añadir 6H",
                },
                {"kind": "costly", "where": "nota 1", "text": "Lámina rectificada de 19 mm encarece la placa"},
            ],
        )
        lines += ["=== report", recorded]
        remarks = ids(recorded, "fab")
        assert remarks == ["fab1.1", "fab1.2"]
        pending = await cad.err("export", action="pdf", path="placa19.pdf")
        assert (
            all(r in pending for r in remarks) and "No dice la clase de rosca" in pending and "smallest fix" in pending
        )

        await cad.ok(
            "drawing", action="answer", id="fab1.1", text="applies: la clase de rosca va en la designación M6-6H"
        )
        await cad.ok("drawing", action="answer", id="fab1.2", text=WHY)
        review = await cad.ok("drawing", action="review")
        assert "drawing review mode: fabricator" in review and "export of this sheet is open" in review
        exported = await cad.ok("export", action="pdf", path="placa19.pdf")
        lines += ["=== export", exported]
        assert "TechDraw's own export" in exported and (workspace / "placa19.pdf").read_bytes().startswith(b"%PDF")

        # a change of the sheet does NOT make the report old: it stays valid through the fixes, and the edits are shown
        await cad.ok("drawing", action="add", items=[{"type": "note", "text": "Rosca M6-6H."}])
        shown = await cad.ok("drawing", action="review")
        assert "reviewed at edit" in shown and "sheet now at edit" in shown
        await cad.ok("export", action="pdf", path="placa19.pdf", overwrite=True)  # no new review is asked
        # a new review only if the agent asks for it: the newest report supersedes the old one
        await cad.ok(
            "drawing",
            action="review_report",
            sheet="Drawing",
            by="fabricator",
            items=[{"kind": "ambiguous", "where": "nota 3", "text": "No dice la longitud de rosca útil"}],
        )
        again = await cad.err("export", action="pdf", path="placa19.pdf", overwrite=True)
        assert "fab2.1" in again and "fab1.1" not in again.split("remarks of the reviewer")[1]
        await cad.ok(
            "drawing", action="answer", id="fab2.1", text="corregido: la nota 3 dice M6-6H, longitud útil 10 mm"
        )
        await cad.ok("export", action="pdf", path="placa19.pdf", overwrite=True)
    folder = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path)
    (folder / "review_fabricator.txt").write_text("\n".join(lines), encoding="utf-8")


async def test_the_engineer_chooses_or_the_manufacturer_is_assumed(workspace: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        await clean_plate_sheet(cad)
        await cad.ok("memory", action="brief", drawing_review="ingeniero_o_fabricante:0")  # the default, no wait
        await close_findings(cad)

        assert "nobody has been asked who reviews" in await cad.err("export", action="pdf", path="a.pdf")
        asked = await cad.ok("drawing", action="request_review", sheet="Drawing")
        assert "¿Revisas tú la hoja Drawing o la revisa el agente fabricante?" in asked and "sleep 0" in asked
        # no answer from the engineer and no wait: the manufacturer is assumed
        assumed = await cad.err("export", action="pdf", path="a.pdf")
        assert "the engineer did not choose within 0 min: the manufacturer review counts" in assumed
        await cad.ok("drawing", action="review_report", sheet="Drawing", by="fabricator", items=[])
        await cad.ok("export", action="pdf", path="a.pdf")

        # a later change neither asks the question again nor restarts the wait: the manufacturer stays assumed
        await cad.ok("drawing", action="add", items=[{"type": "note", "text": "Marcar el número de plano."}])
        stays = await cad.ok("drawing", action="request_review", sheet="Drawing")
        assert "the manufacturer review counts" in stays and "¿Revisas tú" not in stays
        await cad.ok("export", action="pdf", path="a2.pdf")  # the report of the manufacturer still counts
        # the engineer can still review later: he says «yo» and his report is needed from then on
        await cad.ok("drawing", action="review_choice", sheet="Drawing", by="engineer")
        waiting = await cad.err("export", action="pdf", path="b.pdf")
        assert "the engineer chose to review the sheet himself (no time limit): no engineer report" in waiting
        recorded = await cad.ok(
            "drawing",
            action="review_report",
            sheet="Drawing",
            by="engineer",
            items=[
                {"kind": "other", "where": "rótulo", "text": "Falta el número de plano", "fix": "Llenar drawing_number"}
            ],
        )
        assert ids(recorded, "eng") == ["eng2.1"]
        assert "eng2.1" in await cad.err("export", action="pdf", path="b.pdf")
        await cad.ok("drawing", action="answer", id="eng2.1", text="applies: puse el número de plano en el rótulo")
        await cad.ok("export", action="pdf", path="b.pdf")
        assert (workspace / "b.pdf").read_bytes().startswith(b"%PDF")


async def test_propio_asks_the_agent_and_ninguno_asks_nothing(workspace: Path) -> None:
    async with running_server(workspace, visible=True) as (cad, _):
        await clean_plate_sheet(cad)
        await cad.ok("memory", action="brief", drawing_review="propio", text="Ana: lo revisa el agente")
        review = await cad.ok("drawing", action="review")
        assert "drawing review mode: own" in review and "q.callouts" in review and "q.views" not in review
        assert "drawing_unfinished" in await cad.err("export", action="pdf", path="c.pdf")
        await cad.ok(
            "memory", action="brief", drawing_review="ninguno", text="Ana: sin revisión, es un borrador de prueba"
        )
        listed = await cad.ok("drawing", action="review")
        assert "export of this sheet is open: the project asks no review" in listed
        await cad.ok("export", action="pdf", path="c.pdf")  # findings and all: nothing is asked
        assert (workspace / "c.pdf").read_bytes().startswith(b"%PDF")
