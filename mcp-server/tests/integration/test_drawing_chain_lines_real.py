# ruff: noqa: E501
"""A centre line is a chain line on the REAL FreeCAD (Windows): the bench plate (hole pattern with its pitch circle), the
shaft with its section and a flanged ring with four holes on a pitch circle, each composed, exported to PDF from the visible
CAD (TechDraw's own export of the window) and from the headless one (our own writer). The dash pattern of every thin
dashed stroke is read back from the PDF operators: the centre lines are the long-dash dotted line of ISO 128 (line 04), and
no thin line is the two-length dashed one, which reads as a hidden edge. In the saved file of the visible CAD the axes are
TechDraw's own centre-line objects and every centre line carries TechDraw's line number of the chain line.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the PDFs and the images (the sheet, and a centre line zoomed in).
"""

import base64
import os
import re
import sys
import zipfile
from pathlib import Path

import pytest

from tests import pdf_widths
from tests.integration.conftest import Cad, finish_drawing, running_server
from tests.integration.test_drawing_compose_real import compose_the_rest, in_one_order
from tests.integration.test_drawing_line_weights_real import plate_sheet, shaft_sheet, unexplained_widths
from tests.integration.test_drawing_turned_section_real import make_ring

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

THIN_MM = 0.18
TOLERANCE_MM = 0.011
ISO_04_MM = (4.32, 0.54, 0.18, 0.54)  # long dash 24, gap 3, dot 1, gap 3 pen widths of 0.18 (ISO 128-20)
CHAIN_NUMBER = "4"
ZOOM_MM = 14.0


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COTAFORGE_HOME", str(tmp_path / "home"))


async def cover_sheet(cad: Cad, workspace: Path) -> None:
    await in_one_order(cad)
    await make_ring(cad, holes=4)
    decisions = {"sheet": {"title": "Tapa con brida", "symmetry": True}, "finish": [{"general": True, "ra": 3.2}]}
    await compose_the_rest(cad, decisions, keep=("req.surface_texture",))


SCENARIOS = {"plate": plate_sheet, "shaft": shaft_sheet, "cover": cover_sheet}


def centre_lines_of(fcstd: Path) -> tuple[list[str], list[str]]:
    """The line numbers saved in ``fcstd``: of TechDraw's centre-line objects, and of the cosmetic edges drawn as centre lines."""
    xml = zipfile.ZipFile(fcstd).read("Document.xml").decode("utf-8")
    objects = [m.group(1) for b in re.findall(r"<CenterLine\s.*?</CenterLine>", xml, flags=re.S) if (m := re.search(r'<LineNumber value="(\d+)"', b))]
    cosmetic = [
        m.group(1)
        for b in re.findall(r"<CosmeticEdge\s.*?</CosmeticEdge>", xml, flags=re.S)
        if '<Style value="4"/>' in b and (m := re.search(r'<LineNumber value="(\d+)"', b))
    ]
    return objects, cosmetic


# the sheets of these scenarios are not finished drawings and hold two defects that are not about their centre lines (the
# stack of the shaft's dimensions, the end view of the ring): they stay listed here so the gap is seen
@pytest.mark.expected_findings("dimension_gap", "view_adds_nothing", "native_unread")  # native_unread: the thread dimension of the shaft in the headless CAD, on develop too
@pytest.mark.parametrize("visible", [True, False], ids=["visible", "headless"])
@pytest.mark.parametrize("which", list(SCENARIOS))
async def test_the_centre_lines_are_long_dash_dotted_in_the_pdf_and_in_the_file(
    which: str, visible: bool, workspace: Path, tmp_path: Path
) -> None:
    out = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    out.mkdir(parents=True, exist_ok=True)
    name = f"cl2_{which}_{'visible' if visible else 'headless'}"
    async with running_server(workspace, visible=visible) as (cad, _):
        await SCENARIOS[which](cad, workspace)
        review = await cad.ok("drawing", action="review")
        assert "centre_line_not_chain" not in review, review  # a correct sheet has nothing to flag
        capture = await cad("drawing", action="capture")
        if capture.images:
            (out / f"{name}.png").write_bytes(base64.b64decode(capture.images[0].data))
        await finish_drawing(cad)
        pdf = workspace / f"{which}.pdf"
        await cad.ok("export", action="pdf", path=str(pdf))
        kept = out / f"{name}.pdf"
        kept.write_bytes(pdf.read_bytes())
        dashed = pdf_widths.dashed_strokes(kept)
        thin = [(pattern, box) for width, pattern, box in dashed if abs(width - THIN_MM) <= TOLERANCE_MM]
        chain = [(pattern, box) for pattern, box in thin if pdf_widths.is_chain(pattern)]
        print("DASH PATTERNS", name, sorted({(width, pattern) for width, pattern, _ in dashed}), "chain strokes:", len(chain))
        if chain:  # one centre line zoomed in, for a person to look at
            box = max(chain, key=lambda row: (row[1][2] - row[1][0]) + (row[1][3] - row[1][1]))[1]
            cx, cy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
            zoom = await cad("drawing", action="capture", region=[cx - 2 * ZOOM_MM, cy - ZOOM_MM, cx + 2 * ZOOM_MM, cy + ZOOM_MM])
            if zoom.images:
                (out / f"{name}_axis.png").write_bytes(base64.b64decode(zoom.images[0].data))
        fcstd = workspace / f"{which}.FCStd"
        await cad.ok("doc", action="save", path=str(fcstd))
    assert chain, (name, "no thin long-dash dotted stroke in the PDF")
    widths = pdf_widths.by_width(kept)
    print("WIDTHS", name, widths)
    assert not unexplained_widths(widths), (name, widths, "the line standard changes the dash patterns, never the widths of the table")
    not_chain = sorted({pattern for pattern, _ in thin if not pdf_widths.is_chain(pattern)})
    assert not not_chain, (name, "thin dashed strokes that are not the chain line (they read as hidden edges)", not_chain)
    if visible:
        iso = [pattern for pattern, _ in chain if all(abs(a - b) <= 0.02 for a, b in zip(pattern, ISO_04_MM, strict=True))]
        assert len(iso) >= len(chain) - 2, {p for p, _ in chain}  # every one but the two of the projection symbol of the title block
        objects, cosmetic = centre_lines_of(fcstd)
        print("CENTRE LINES", name, "objects", objects, "cosmetic", cosmetic)
        if which != "plate":  # the plate is drawn from above only: its holes are circles, with centre marks and no axis from the side
            assert objects, (name, "no axis is TechDraw's own centre-line object")
        assert set(objects) | set(cosmetic) == {CHAIN_NUMBER}, (objects, cosmetic)
