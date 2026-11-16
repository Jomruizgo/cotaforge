# ruff: noqa: E501
"""The line weights of a sheet on the REAL FreeCAD (Windows): the bench plate, the shaft with its removed section and the welded
frame, each exported to PDF from the visible CAD (TechDraw's own export of the window) and from the headless one (our own
writer), and the strokes read back from the PDF operators. Whatever the engineer's TechDraw preferences are, the widths on
the paper are the harness's table (``sheet_weights``), not the defaults of whoever opens the file.

Set ``COTAFORGE_TEST_SHOTS`` to a folder to keep the PDFs and the images of the sheets.
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
from tests.integration.test_drawing_pattern_real import bench_plate
from tests.integration.test_drawing_shaft_sheet_real import DIAMETERS, shaft
from tests.integration.test_drawing_weldment_real import NOTE, WELD, build_frame

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="the visible FreeCAD window is exercised on Windows")

WORKER = Path(__file__).resolve().parents[3] / "kernel" / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER) not in sys.path:
    sys.path.append(str(WORKER))

TOLERANCE_MM = 0.011  # a PDF writes the width as a real number; rounded to two decimals it can be a hundredth off
INK_SHARE = 0.002  # a width that carries less than this share of the drawn line is a glyph or a symbol's own stroke, not a line of the sheet


@pytest.fixture(autouse=True)
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("COTAFORGE_HOME", str(tmp_path / "home"))


async def plate_sheet(cad: Cad, workspace: Path) -> None:
    await bench_plate(cad)
    await cad.ok("drawing", action="create", template="A3", views=["top", "iso"])
    await cad.ok(
        "drawing", action="add", items=[{"type": "hole_dim", "all": True}, {"type": "view_lines"}, {"type": "overall"}]
    )


async def shaft_sheet(cad: Cad, workspace: Path) -> None:
    await in_one_order(cad)
    await shaft(cad, workspace)
    decisions = {
        "sheet": {"title": "Eje de transmision", "template": "A3", "language": "es"},
        "relations": [
            {"type": "baseline_dims", "from": "face:6", "view": "front", "side": "below"},
            DIAMETERS[0],
            DIAMETERS[2],
        ],
        "fits": [DIAMETERS[1], DIAMETERS[3], DIAMETERS[4]],
        "notes": ["Cantidad 10. Proceso: torneado, rectificado de los asientos, fresado del chavetero y roscado."],
    }
    await compose_the_rest(cad, decisions, keep=("req.surface_texture",))


async def frame_sheet(cad: Cad, workspace: Path) -> None:
    await build_frame(cad)
    await cad.ok("drawing", action="create", template="A3", angle="first", title="Bastidor")
    rows = [{"component": "body:1", "name": "Tubo 40×40×3"}, {"component": "body:5", "name": "Chapa 6"}]
    await cad.ok(
        "drawing",
        action="add",
        items=[
            {"type": "parts_list", "id": "cut", "rows": rows},
            {"type": "balloons", "id": "bal", "view": "isometric"},
            NOTE,
            WELD,
        ],
    )


SCENARIOS = {"plate": plate_sheet, "shaft": shaft_sheet, "frame": frame_sheet}


def table_widths() -> set[float]:
    import sheet_weights  # noqa: PLC0415 - the worker's own module, no FreeCAD in it

    return set(sheet_weights.allowed_widths())


async def export_and_measure(
    which: str, visible: bool, workspace: Path, tmp_path: Path
) -> tuple[dict[float, float], Path]:
    out = Path(os.environ.get("COTAFORGE_TEST_SHOTS") or tmp_path / "shots")
    out.mkdir(parents=True, exist_ok=True)
    async with running_server(workspace, visible=visible) as (cad, _):
        await SCENARIOS[which](cad, workspace)
        capture = await cad("drawing", action="capture")
        if capture.images:
            (out / f"lw1_{which}_{'visible' if visible else 'headless'}.png").write_bytes(
                base64.b64decode(capture.images[0].data)
            )
        await finish_drawing(cad)
        pdf = workspace / f"{which}.pdf"
        await cad.ok("export", action="pdf", path=str(pdf))
    kept = out / f"lw1_{which}_{'visible' if visible else 'headless'}.pdf"
    kept.write_bytes(pdf.read_bytes())
    print(pdf_widths.table(kept))
    return pdf_widths.by_width(kept), kept


TECHDRAW_OWN = {
    0.03: "a stroke TechDraw draws at a tenth of the view's LineWidth (measured with the section's removed hatch)",
    0.09: "the cell borders of a spreadsheet table (the native parts list): hard-coded in DrawViewSpreadsheet",
    0.10: "the hatch of a section view: 1.1.3 draws it at 0.10 whatever ViewObject.WeightPattern says (0.07, 0.2, 0.3, 0.5 tried)",
    0.24: "the all-around circle of a weld symbol",
}
"""The widths TechDraw 1.1.3 draws itself and the harness cannot set (measured). They are a small share of the ink; every
other width of the PDF must be the table's."""
OLD_TABLE = (0.5, 0.25, 0.12)  # the widths the harness used before the table: none may be left


def off_table(widths: dict[float, float]) -> dict[float, float]:
    """The widths of a PDF (width -> mm of line) that are not the table's and carry a real share of the ink."""
    total = sum(widths.values()) or 1.0
    allowed = table_widths()  # the frame and the outer line of the title block are the table's FRAME weight, 0.7
    return {
        w: mm
        for w, mm in widths.items()
        if mm / total >= INK_SHARE and not any(abs(w - a) <= TOLERANCE_MM for a in allowed)
    }


def unexplained_widths(widths: dict[float, float]) -> dict[float, float]:
    """The widths that are neither the table's nor one TechDraw draws itself and the harness cannot set (``TECHDRAW_OWN``)."""
    return {w: mm for w, mm in off_table(widths).items() if w not in TECHDRAW_OWN}


@pytest.mark.parametrize("visible", [True, False], ids=["visible", "headless"])
@pytest.mark.parametrize("which", list(SCENARIOS))
async def test_the_pdf_carries_the_harness_line_weights(
    which: str, visible: bool, workspace: Path, tmp_path: Path
) -> None:
    widths, kept = await export_and_measure(which, visible, workspace, tmp_path)
    total = sum(widths.values()) or 1.0
    off = off_table(widths)
    unexplained = unexplained_widths(widths)
    assert not unexplained, f"widths outside the table in {kept.name}: {unexplained}"
    assert sum(off.values()) / total < 0.15, (off, "TechDraw's own widths are a small part of the sheet")
    assert not any(w in widths for w in OLD_TABLE), (widths, "a width of the old table is left")
    thick = sum(mm for w, mm in widths.items() if abs(w - 0.35) <= TOLERANCE_MM)
    assert thick / total > 0.2, widths  # the outline, the hidden lines and the title block are at the thick weight
    thin = sum(mm for w, mm in widths.items() if abs(w - 0.18) <= TOLERANCE_MM)
    assert thin > 0, widths  # dimensions, centre lines and leaders are at the thin weight


def gui_widths(fcstd: Path) -> dict[str, set[float]]:
    """The width properties the file keeps for its view providers (``GuiDocument.xml``), by property name; empty when the file has no GUI part."""
    with zipfile.ZipFile(fcstd) as archive:
        if "GuiDocument.xml" not in archive.namelist():
            return {}
        xml = archive.read("GuiDocument.xml").decode("utf-8")
    found: dict[str, set[float]] = {}
    for name in ("LineWidth", "HiddenWidth", "IsoWidth"):
        for block in re.finditer(rf'<Property name="{name}"[^>]*>\s*<Float value="([0-9.eE+-]+)"', xml):
            found.setdefault(name, set()).add(round(float(block.group(1)), 3))
    return found


async def test_the_widths_persist_where_the_cad_can_keep_them(workspace: Path) -> None:
    """The view providers' widths are saved by a CAD with a window only (``GuiDocument.xml``): a file the headless CAD saves carries
    none, and the visible CAD sets them when it opens it, so the next save keeps the table."""
    async with running_server(workspace, visible=False) as (cad, _):
        await plate_sheet(cad, workspace)
        await cad.ok("doc", action="save", path="headless.FCStd")
    assert gui_widths(workspace / "headless.FCStd") == {}
    async with running_server(workspace, visible=True) as (cad, _):
        await cad.ok("doc", action="open", path="headless.FCStd")
        await cad.ok("doc", action="save", path="opened.FCStd")
    opened = gui_widths(workspace / "opened.FCStd")
    print("GUI WIDTHS after the visible open", opened)
    assert (
        opened.get("LineWidth", set()) >= {0.35, 0.18}
        and opened.get("HiddenWidth") == {0.35}
        and opened.get("IsoWidth") == {0.07}
    ), opened
