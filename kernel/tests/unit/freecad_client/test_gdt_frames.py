# ruff: noqa: E501
"""``worker/gdt_frames.py`` and the library of ISO 1101 frames it reads (``packaging/symbols/cotaforge-gdt``).

What is checked here: the files are what the generator writes; the geometry and the cells of every file (cell height, widths,
dividers, the datum cells); every text of a cell is editable; one datum cell is not three; every characteristic of ISO 1101
has files; the function picks the file and the texts and refuses what ISO 1101 has no cell for; a library symbol is told
from another one even after FreeCAD rewrote it; the installer stages the library. The painted result in the real FreeCAD is
``tests/integration/freecad/test_visible_gdt_library.py``.
"""

import ast
import importlib.util
import re
import shutil
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import ModuleType

import pytest

KERNEL = Path(__file__).resolve().parents[3]
ROOT = KERNEL.parent
WORKER_DIR = KERNEL / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
LIBRARY = ROOT / "packaging" / "symbols" / "cotaforge-gdt"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import gdt_frames as gf  # noqa: E402

SVG = "{http://www.w3.org/2000/svg}"
ISO_1101 = {  # the 14 symbols of ISO 1101 (concentricity and coaxiality share one)
    "straightness", "flatness", "circularity", "cylindricity", "profile_line", "profile_surface", "parallelism",
    "perpendicularity", "angularity", "position", "concentricity", "symmetry", "runout", "total_runout",
}  # fmt: skip


def _generator() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "build_gdt_frames", ROOT / "packaging" / "symbols" / "build_gdt_frames.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


GEN = _generator()


def _root(relative: str) -> ET.Element:
    return ET.fromstring((LIBRARY / relative).read_text(encoding="utf-8"))


def _numbers(path_d: str) -> list[float]:
    return [float(t) for t in re.sub(r"[A-Za-z]", " ", path_d).split()]


# --- the files ----------------------------------------------------------------------------------------------------------


def test_the_files_are_what_the_generator_writes() -> None:
    on_disk = {p.relative_to(LIBRARY).as_posix(): p.read_text(encoding="utf-8") for p in LIBRARY.rglob("*.svg")}
    assert on_disk == GEN.all_files(), "run packaging/symbols/build_gdt_frames.py"
    assert not [p for p in LIBRARY.rglob("*") if p.is_file() and p.suffix != ".svg"]


def test_every_characteristic_of_iso_1101_has_files_and_the_module_and_the_generator_agree() -> None:
    assert set(GEN.DATUM_RANGE) == ISO_1101 == set(gf.DATUM_RANGE) == set(gf.CHARACTERISTICS)
    assert dict(GEN.DATUM_RANGE) == dict(gf.DATUM_RANGE)
    for name in ISO_1101:
        for datums in gf.DATUM_RANGE[name]:
            for wide in (False, True):
                assert gf.gdt_frame_file(name, datums, wide=wide) in gf.library_files()
    assert len(gf.library_files()) == 74  # 37 frames, each with a narrow and a wide tolerance cell


def test_form_tolerances_have_no_datum_cell_orientation_and_run_out_need_one_and_position_and_profile_may_have_none() -> (
    None
):
    for name in ("flatness", "straightness", "circularity", "cylindricity"):
        assert list(gf.DATUM_RANGE[name]) == [0]
    for name in (
        "parallelism",
        "perpendicularity",
        "angularity",
        "symmetry",
        "concentricity",
        "runout",
        "total_runout",
    ):
        assert list(gf.DATUM_RANGE[name]) == [1, 2, 3]
    for name in ("position", "profile_line", "profile_surface"):
        assert list(gf.DATUM_RANGE[name]) == [0, 1, 2, 3]


@pytest.mark.parametrize("relative", sorted(p.relative_to(LIBRARY).as_posix() for p in LIBRARY.rglob("*.svg")))
def test_geometry_and_cells_of_every_file(relative: str) -> None:
    root = _root(relative)
    wide = relative.endswith("-wide.svg")
    datums = int(relative.rsplit("/", 1)[1].split("-")[-3 if wide else -2].replace("datums", "").replace("datum", ""))
    cells = [7.0, 36.0 if wide else 22.0, *([10.0] * datums)]
    total = sum(cells)
    line = 0.35
    # the viewBox is the frame plus half a line on every side, one user unit a millimetre
    assert root.attrib["width"] == f"{total + line:g}mm" and root.attrib["height"] == f"{7 + line:g}mm"
    assert [float(v) for v in root.attrib["viewBox"].split()] == pytest.approx(
        [-line / 2, -line / 2, total + line, 7 + line]
    )
    frame = root.find(f"{SVG}g[@id='frame']")
    assert frame is not None and f"stroke-width:{line:g}" in frame.attrib["style"]
    outline, dividers = frame.findall(f"{SVG}path")
    assert outline.attrib["d"] == f"M 0 0 H {total:g} V 7 H 0 Z"  # a 7 mm high frame: twice the text (ISO 7083)
    edges = [sum(cells[: i + 1]) for i in range(len(cells) - 1)]
    assert dividers.attrib["d"] == " ".join(f"M {x:g} 0 V 7" for x in edges)  # one divider between neighbours
    symbol = root.find(f"{SVG}g[@id='symbol']")
    assert symbol is not None and len(list(symbol)) >= 1
    for shape in symbol.iter():  # the symbol stays inside its 7 x 7 cell
        if "d" in shape.attrib:
            assert all(-1e-9 <= number <= 7 + 1e-9 for number in _numbers(shape.attrib["d"]))
        elif shape.tag == f"{SVG}circle":
            cx, cy, r = (float(shape.attrib[k]) for k in ("cx", "cy", "r"))
            assert min(cx, cy) - r >= -1e-9 and max(cx, cy) + r <= 7 + 1e-9
    texts = root.findall(f"{SVG}text")
    assert len(texts) == 1 + datums
    for text in texts:
        assert "{http://www.w3.org/2000/svg}" not in text.attrib.get(gf.EDITABLE, "")
        assert gf.EDITABLE in text.attrib, "every text of a cell is editable"
        assert "font-size:3.5px" in text.attrib["style"]
        assert "text-anchor:middle" in text.attrib["style"]  # centred in its cell whatever it holds
    centres = [7 + cells[1] / 2] + [7 + cells[1] + 10 * i + 5 for i in range(datums)]
    assert [float(t.attrib["x"]) for t in texts] == pytest.approx(centres)
    assert [t.attrib[gf.EDITABLE] for t in texts] == ["tolerance", *(f"datum_{i + 1}" for i in range(datums))]


def test_one_datum_cell_versus_three() -> None:
    one, three = (
        gf.library_files()["position/position-1-datum.svg"],
        gf.library_files()["position/position-3-datums.svg"],
    )
    assert [n for n, _ in gf.editable_fields(one)] == ["tolerance", "datum_1"]
    assert [n for n, _ in gf.editable_fields(three)] == ["tolerance", "datum_1", "datum_2", "datum_3"]
    width_one = float(ET.fromstring(one).attrib["width"].removesuffix("mm"))
    width_three = float(ET.fromstring(three).attrib["width"].removesuffix("mm"))
    assert width_three - width_one == pytest.approx(20.0)  # two more datum cells of 10 mm
    wide = gf.library_files()["position/position-3-datums-wide.svg"]
    assert float(ET.fromstring(wide).attrib["width"].removesuffix("mm")) - width_three == pytest.approx(14.0)


def test_freecad_lists_the_editable_texts_the_way_the_file_orders_them() -> None:
    _, texts = gf.gdt_frame_symbol("position", 0.1, ("diameter", "M"), ("A", "B", "C"))
    svg, _ = gf.gdt_frame_symbol("position", 0.1, ("diameter", "M"), ("A", "B", "C"))
    assert [n for n, _ in gf.editable_fields(svg)] == ["tolerance", "datum_1", "datum_2", "datum_3"]
    assert len(texts) == len(gf.editable_fields(svg))


# --- gdt_frame_symbol -----------------------------------------------------------------------------------------------------


def test_a_position_frame_with_diameter_material_condition_and_three_datums() -> None:
    svg, texts = gf.gdt_frame_symbol("position", 0.1, ("diameter", "M"), ("A", "B", "C"))
    assert texts == ("Ø0,1 Ⓜ", "A", "B", "C")
    assert svg == gf.library_files()["position/position-3-datums.svg"]  # the library file as it is: no SVG made here


def test_the_decimal_sign_and_the_text_of_a_cell() -> None:
    assert gf.gdt_frame_symbol("flatness", 0.05, decimal=".")[1] == ("0.05",)
    assert gf.gdt_frame_symbol("flatness", 0.0125)[1] == ("0,0125",)
    assert gf.gdt_frame_symbol("flatness", "0,02 / 100")[1] == ("0,02 / 100",)


def test_datum_cells_hold_a_letter_a_common_datum_or_a_letter_with_a_condition() -> None:
    _, texts = gf.gdt_frame_symbol("angularity", 0.1, (), ("A-B", ("C", "M")))
    assert texts == ("0,1", "A-B", "C Ⓜ")
    assert gf.gdt_frame_symbol("runout", 0.03, (), ["A-B"])[1] == ("0,03", "A-B")


def test_the_file_follows_the_number_of_datums_and_the_width_of_the_text() -> None:
    assert gf.gdt_frame_symbol("position", 0.1)[0] == gf.library_files()["position/position-0-datums.svg"]
    assert gf.gdt_frame_symbol("position", 0.1, (), ("A",))[0] == gf.library_files()["position/position-1-datum.svg"]
    long_text = gf.gdt_frame_symbol("position", 0.05, ("diameter", "M", "CZ"), ("A", "B"))
    assert long_text[0] == gf.library_files()["position/position-2-datums-wide.svg"]
    assert long_text[1][0] == "Ø0,05 Ⓜ CZ"
    forced = gf.gdt_frame_symbol("flatness", 0.05, width="wide")[0]
    assert forced == gf.library_files()["flatness/flatness-0-datums-wide.svg"]


def test_coaxiality_is_concentricity() -> None:
    assert gf.gdt_frame_symbol("coaxiality", 0.05, ("diameter",), ("A",)) == gf.gdt_frame_symbol(
        "concentricity", 0.05, ("diameter",), ("A",)
    )


@pytest.mark.parametrize(
    ("args", "why"),
    [
        (("flatness", 0.05, (), ("A",)), "form tolerance"),
        (("position", 0.1, (), ("A", "B", "C", "D")), "0 to 3 datum cells"),
        (("perpendicularity", 0.1, (), ()), "1 to 3 datum cells"),
        (("position", 0.1, (), ("A", "A")), "repeated"),
        (("position", 0.1, (), ("I",)), "ISO 5459"),
        (("position", 0.1, (), ("A-B-C",)), "two letters"),
        (("position", 0.1, (), (("A", "X"),)), "must be a letter"),
        (("flatness", 0.05, ("diameter",), ()), "no diameter zone"),
        (("profile_surface", 0.1, ("M",), ()), "no M modifier"),
        (("runout", 0.1, ("P",), ("A",)), "no P modifier"),
        (("flatness", 0.05, ("CZ",), ()), "no CZ modifier"),
        (("position", 0.1, ("M", "L"), ()), "one of M, L and P"),
        (("position", 0.1, ("bogus",), ()), "unknown modifier"),
        (("nope", 0.1, (), ()), "unknown characteristic"),
        (("position", 0.0, (), ()), "out of range"),
        (("position", "  ", (), ()), "empty"),
        (("position", "Ø0,0125 Ⓜ CZ / 100 UF xx", (), ()), "too long"),
    ],
)
def test_a_frame_iso_1101_has_no_cell_for_is_refused(args: tuple, why: str) -> None:
    with pytest.raises(gf.GdtFrameError, match=why):
        gf.gdt_frame_symbol(*args)


def test_the_narrow_cell_refuses_a_long_text_when_forced() -> None:
    with pytest.raises(gf.GdtFrameError, match="narrow"):
        gf.gdt_frame_symbol("position", "Ø0,05 Ⓜ CZ xx", (), (), width="std")


# --- telling a library symbol from another --------------------------------------------------------------------------------


def _freecad_rewrite(svg: str) -> str:
    """What FreeCAD stores in ``Symbol`` after ``EditableTexts`` is set (measured in 1.1.3): the same drawing, attributes in
    another order, an ``xml:space`` on the spans and the texts replaced."""
    root = ET.fromstring(svg)
    for span in root.iter(f"{SVG}tspan"):
        span.attrib["{http://www.w3.org/XML/1998/namespace}space"] = "preserve"
    ET.indent(root, space=" ")
    for element in root.iter():
        element.attrib = dict(reversed(list(element.attrib.items())))
    return "<?xml version='1.0' encoding='UTF-8'?>\n" + ET.tostring(root, encoding="unicode")


def test_a_library_symbol_stays_one_whatever_the_engineer_types_in_it() -> None:
    svg, texts = gf.gdt_frame_symbol("position", 0.1, ("diameter", "M"), ("A", "B", "C"))
    assert gf.is_frame_symbol(svg)
    filled = gf.fill_editable_texts(svg, ("Ø0,05 Ⓜ", "A", "D", "C"))
    assert [v for _, v in gf.editable_fields(filled)] == ["Ø0,05 Ⓜ", "A", "D", "C"]
    assert gf.is_frame_symbol(filled)
    rewritten = _freecad_rewrite(filled)
    assert rewritten != filled and gf.is_frame_symbol(rewritten)
    assert [v for _, v in gf.editable_fields(rewritten)] == ["Ø0,05 Ⓜ", "A", "D", "C"]
    assert texts == ("Ø0,1 Ⓜ", "A", "B", "C")


def test_a_symbol_that_is_not_a_library_file_is_foreign() -> None:
    svg, _ = gf.gdt_frame_symbol("position", 0.1, (), ("A",))
    assert not gf.is_frame_symbol(svg.replace("M 7 0 V 7", "M 8 0 V 7"))  # another geometry
    assert not gf.is_frame_symbol(svg.replace("</svg>", '<rect width="1" height="1"/></svg>'))  # one more thing drawn
    assert not gf.is_frame_symbol(svg.replace("stroke-width:0.35", "stroke-width:0.5", 1))
    assert not gf.is_frame_symbol('<svg xmlns="http://www.w3.org/2000/svg"><g/></svg>')
    assert not gf.is_frame_symbol("not xml")
    assert not gf.is_frame_symbol("")


def test_every_file_of_the_library_is_told_from_the_others() -> None:
    signatures = {repr(gf._tree(ET.fromstring(text), False)) for text in gf.library_files().values()}  # noqa: SLF001
    assert len(signatures) == len(gf.library_files())  # no two files draw the same


def test_filling_a_symbol_needs_a_text_for_every_field() -> None:
    svg, _ = gf.gdt_frame_symbol("position", 0.1, (), ("A", "B"))
    with pytest.raises(gf.GdtFrameError, match="3 editable texts, 2 given"):
        gf.fill_editable_texts(svg, ("0,1", "A"))


# --- where the library is, and what only it may hold ---------------------------------------------------------------------


def test_the_library_is_found_in_a_checkout_and_by_the_variable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv(gf.ENV_DIR, raising=False)
    assert gf.library_dir() == LIBRARY
    monkeypatch.setenv(gf.ENV_DIR, str(tmp_path))
    assert gf.library_dir() == tmp_path
    monkeypatch.setenv(gf.ENV_DIR, str(tmp_path / "missing"))
    with pytest.raises(gf.GdtFrameError, match="not a folder"):
        gf.library_dir()


def test_the_install_layout_is_found_next_to_python(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    r"""``{app}\python\Lib\site-packages\cotaforge\adapters\freecad\worker\gdt_frames.py`` sees ``{app}\symbols``."""
    monkeypatch.delenv(gf.ENV_DIR, raising=False)
    app = tmp_path / "app"
    worker = app / "python" / "Lib" / "site-packages" / "cotaforge" / "adapters" / "freecad" / "worker"
    worker.mkdir(parents=True)
    (worker / "gdt_frames.py").write_text((WORKER_DIR / "gdt_frames.py").read_text(encoding="utf-8"), encoding="utf-8")
    shutil.copytree(LIBRARY, app / "symbols" / gf.LIBRARY_NAME)
    spec = importlib.util.spec_from_file_location("gdt_frames_installed", worker / "gdt_frames.py")
    assert spec is not None and spec.loader is not None
    installed = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(installed)
    assert installed.library_dir() == (app / "symbols" / gf.LIBRARY_NAME).resolve()
    svg, texts = installed.gdt_frame_symbol("position", 0.1, ("diameter",), ("A",))
    assert svg == gf.gdt_frame_symbol("position", 0.1, ("diameter",), ("A",))[0] and texts == ("Ø0,1", "A")


def test_the_module_only_reads_files_it_makes_no_symbol_and_writes_no_svg() -> None:
    """The guard of the native-only rule lets exactly two sources make a symbol: FreeCAD's own library folder and this one.
    This module is the reader of the second: it never builds an SVG and never makes a ``DrawViewSymbol``."""
    tree = ast.parse((WORKER_DIR / "gdt_frames.py").read_text(encoding="utf-8"))
    called = {n.func.attr for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
    assert not called & {"addObject", "write_text", "write_bytes", "SubElement", "Element", "write"}, called
    text = (WORKER_DIR / "gdt_frames.py").read_text(encoding="utf-8")
    assert ".Symbol =" not in text.replace("symbol.Symbol = svg", "")


def test_the_svg_files_of_the_packaging_folder_are_only_this_library() -> None:
    outside = [p for p in (ROOT / "packaging").rglob("*.svg") if LIBRARY not in p.parents]
    assert outside == []


def test_the_windows_build_stages_the_library_next_to_python() -> None:
    build = (ROOT / "packaging" / "windows" / "build.ps1").read_text(encoding="utf-8")
    assert r'Join-Path $Repo "packaging\symbols\cotaforge-gdt"' in build
    assert 'Join-Path $Stage "symbols"' in build
