# ruff: noqa: E501
"""The quality floor behind the finished-drawing gate: the real server over stdio against the VISIBLE FreeCAD (Windows).

The 19 mm plate of the finished-drawing acceptance, drawn the way the guide says (one view, the holes called out, the
dowels with their fit and a ring, the notes). The default location puts every hole's position on the sheet by itself, so
the review has no position finding; what is left before the export is counted (the friction of a clean plate) and the
export waits for exactly that: the points are answered, the sheet is approved and the PDF and the DXF go out.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the transcript and the sheet image.
"""

import base64
import os
import sys
from pathlib import Path

import pytest

from tests.integration.conftest import pending_points, running_server
from tests.integration.test_drawing_finished_real import (
    CLEARANCE,
    DOWELS,
    TAPPED,
    THICKNESS,
    declare_references,
    make_plate,
)

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

FRICTION_CEILING = 17  # 6 notes and views findings, 2 questions, 9 memory entries (was 22 with 7 questions): what a clean one-view plate closes before its export when the agent reviews it itself


async def test_a_clean_plate_sheet_has_its_positions_and_closes_with_few_points(
    workspace: Path, tmp_path: Path
) -> None:
    folder = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    folder.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    async with running_server(workspace, visible=True) as (cad, _):
        await make_plate(cad, dowel_fit="H7")  # the callout asks for H7: the holes declare it
        await cad.ok(
            "memory", action="brief", drawing_review="propio", text="Ana: el agente revisa el plano por su cuenta"
        )
        created = await cad.ok(
            "drawing", action="create", views=["top", "iso"], title="Placa 19", author="Taller Sur"
        )  # every manufacturing sheet carries the isometric view and fills its paper (the sheet gaps decision)
        lines += ["=== create", created, ""]
        await declare_references(cad)
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
        review = await cad.ok("drawing", action="review")
        lines += ["=== review of the clean plate", review, ""]
        for position in ("hole_not_located", "hole_no_reference", "hole_partly_located", "centre_mark_missing"):
            assert position not in review, position
        assert "datum_dims ok: 7 hole(s)" in review and "review: 0 warning(s)" in review
        findings, questions, memory = await pending_points(cad)
        friction = len(findings) + len(questions) + len(memory)
        lines += [
            f"=== friction: {len(findings)} findings, {len(questions)} questions, {len(memory)} memory entries = {friction}",
            "",
        ]
        assert friction <= FRICTION_CEILING, (findings, questions, memory)

        refused = await cad.err("export", action="pdf", path="placa19.pdf")
        assert "drawing_unfinished" in refused and not (workspace / "placa19.pdf").exists()

        for entry in memory:
            await cad.ok("memory", action="get", id=entry)
        for point in [*findings, *questions, *memory]:
            await cad.ok(
                "drawing", action="answer", id=point, text="no aplica: la placa es plana y se lee en una vista"
            )
        done = await cad.ok("drawing", action="review")
        assert "export of this sheet is open" in done, done
        await cad.ok("drawing", action="approve", sheet="Drawing", by="Engineer", reason="the engineer: approve it")
        pdf = await cad.ok("export", action="pdf", path="placa19.pdf")
        dxf = await cad.ok("export", action="dxf", path="placa19.dxf")
        lines += ["=== export", pdf, dxf]
        assert (workspace / "placa19.pdf").read_bytes().startswith(b"%PDF")
        assert (workspace / "placa19.dxf").stat().st_size > 1000
        reply = await cad("drawing", action="capture")
        assert not reply.is_error, reply.text
        (folder / "f7_clean_plate.png").write_bytes(base64.b64decode(reply.images[0].data))
    (folder / "f7_clean_plate.txt").write_text("\n".join(lines), encoding="utf-8")
