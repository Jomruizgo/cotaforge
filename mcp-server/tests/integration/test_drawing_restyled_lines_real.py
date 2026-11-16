# ruff: noqa: E501
"""A centre line the engineer restyles stays as he styled it, on the REAL FreeCAD (Windows, VISIBLE): the bench shaft, composed, with
two of the sheet's own centre lines given another line number through FreeCAD's own property content (the path the line-style
combo of TechDraw's centre-line dialog writes: the saved content of ``CenterLines``). One is ISO 05, the other a number that is
no chain line at all. An item added later, the review and the export leave both as he set them, the review does not name them and
nothing blocks the export; the lines the engineer did not touch stay the chain line.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the PDF and the images of the sheet.
"""

import base64
import json
import os
import sys
from pathlib import Path

import pytest

from tests import pdf_widths
from tests.integration.conftest import Cad, finish_drawing, running_server
from tests.integration.test_drawing_hand_lines_real import READ
from tests.integration.test_drawing_line_weights_real import SCENARIOS, unexplained_widths

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

CHAIN = 4
ISO_05 = 5
NOT_CHAIN = 8  # the long-dash short-dash line of ISO 128: no centre line

# the engineer restyles two centre lines: the content of the property is what TechDraw's own dialog writes
RESTYLE = """
import FreeCAD as App, io, json, re, zipfile
doc = App.ActiveDocument
wanted = [__WANTED__]
views = [v for v in doc.Objects if hasattr(v, "CenterLines") and len(list(v.CenterLines)) >= 1]
spots = [(v, i) for v in views for i in range(len(list(v.CenterLines)))]
done = []
for number, (view, index) in zip(wanted, spots):
    z = zipfile.ZipFile(io.BytesIO(bytes(view.dumpPropertyContent("CenterLines"))))
    name = z.namelist()[0]
    xml = z.read(name).decode()
    blocks = list(re.finditer(r"<CenterLine\\s.*?</CenterLine>", xml, flags=re.S))
    text = re.sub(r'<LineNumber value="\\d+"', '<LineNumber value="%d"' % number, blocks[index].group(0), count=1)
    xml = xml[: blocks[index].start()] + text + xml[blocks[index].end():]
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(name, xml)
    view.restorePropertyContent("CenterLines", bytearray(out.getvalue()))
    done.append([view.Name, index, number])
doc.recompute()
print("RESTYLE " + json.dumps(done))
"""


async def lines(cad: Cad) -> dict[str, dict[str, list]]:
    reply = await cad("run_native", code=READ, intent="read the line number of every centre line of the views")
    assert not reply.is_error, reply.text
    row = next(part for part in reply.text.splitlines() if "LINES " in part)
    return json.loads(row.split("LINES ", 1)[1])


def centre_numbers(snapshot: dict[str, dict[str, list]]) -> dict[str, list[int]]:
    return {name: view["centre"] for name, view in sorted(snapshot.items()) if view["centre"]}


@pytest.mark.expected_findings("dimension_gap", "view_adds_nothing", "native_unread")
async def test_a_centre_line_the_engineer_restyles_keeps_his_style_through_annotate_review_and_export(workspace: Path, tmp_path: Path) -> None:
    out = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    out.mkdir(parents=True, exist_ok=True)
    async with running_server(workspace, visible=True) as (cad, _):
        await SCENARIOS["shaft"](cad, workspace)
        made = centre_numbers(await lines(cad))
        print("MADE", json.dumps(made))
        assert made and all(n == CHAIN for numbers in made.values() for n in numbers), made
        assert sum(len(numbers) for numbers in made.values()) >= 2, made

        reply = await cad("run_native", code=RESTYLE.replace("__WANTED__", f"{ISO_05}, {NOT_CHAIN}"), intent="the engineer gives two centre lines of the sheet another line style")
        assert not reply.is_error, reply.text
        done = json.loads(next(p for p in reply.text.splitlines() if "RESTYLE " in p).split("RESTYLE ", 1)[1])
        print("RESTYLE", done)
        styled = centre_numbers(await lines(cad))
        print("STYLED", json.dumps(styled))
        assert sorted(n for numbers in styled.values() for n in numbers if n != CHAIN) == [ISO_05, NOT_CHAIN], styled
        capture = await cad("drawing", action="capture")
        if capture.images:
            (out / "cl5_shaft_0_restyled.png").write_bytes(base64.b64decode(capture.images[0].data))

        # an item added later: the sheet makes no centre line again and sets no number on the ones that are there
        await cad.ok("drawing", action="add", items=[{"type": "note", "id": "cl5_note", "text": "Sin rebabas"}])
        after_add = centre_numbers(await lines(cad))
        print("AFTER_ADD", json.dumps(after_add))
        assert after_add == styled, (after_add, styled)

        review = await cad.ok("drawing", action="review")
        assert "centre_line_not_chain" not in review, review  # the non-chain number is his choice: never judged
        assert centre_numbers(await lines(cad)) == styled
        capture = await cad("drawing", action="capture")
        if capture.images:
            (out / "cl5_shaft_1_after_add_review.png").write_bytes(base64.b64decode(capture.images[0].data))

        await finish_drawing(cad)
        pdf = workspace / "shaft_restyled.pdf"
        await cad.ok("export", action="pdf", path=str(pdf))  # nothing the engineer styled holds it
        after_export = centre_numbers(await lines(cad))
        print("AFTER_EXPORT", json.dumps(after_export))
        assert after_export == styled, (after_export, styled)
        kept = out / "cl5_shaft_restyled.pdf"
        kept.write_bytes(pdf.read_bytes())
    dashed = pdf_widths.dashed_strokes(kept)
    print("DASH PATTERNS", sorted({(width, pattern) for width, pattern, _ in dashed}))
    print(pdf_widths.table(kept))
    assert not unexplained_widths(pdf_widths.by_width(kept)), "the widths are the table's"
