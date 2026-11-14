# ruff: noqa: E501
"""The guard of the native-only rule: a drawing is made of FreeCAD's own TechDraw objects, never of a symbol the harness drew.

A ``DrawViewSymbol`` carrying an SVG the harness generated does not follow its view, cannot be edited line by line and stays
behind when its view is deleted. Only ``sheet_symbols`` (the files of FreeCAD's own symbol library) may make one. Any other
worker module that makes a ``DrawViewSymbol`` or sets the ``Symbol`` of one fails here.

``STILL_DRAWN_BY_HARNESS`` is the debt of the migration, by module: each entry is a module that still makes harness symbols
and the items it makes them for. The list only shrinks; a new entry is refused by review.
"""

import re
import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_symbols  # noqa: E402

ALLOWED = {"sheet_symbols.py"}
STILL_DRAWN_BY_HARNESS: dict[str, str] = {
    "sheet_mirror.py": (
        "the units with no native form yet: datum and GD&T symbols, finish symbols, the datum_dims table, detail and "
        "auxiliary views, sections drawn as sheet geometry, welds, mould symbols (ISO 10135), parts list and balloons, and "
        "every view of an hlr sheet"
    ),
}
MAKES_A_SYMBOL = re.compile(r"addObject\(\s*(?:SYMBOL|[\"']TechDraw::DrawViewSymbol[\"'])|\.Symbol\s*=[^=]")


def _makers() -> dict[str, list[int]]:
    found: dict[str, list[int]] = {}
    for path in sorted(WORKER_DIR.glob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        hits = [n for n, line in enumerate(lines, start=1) if MAKES_A_SYMBOL.search(line) and not line.lstrip().startswith("#")]
        if hits:
            found[path.name] = hits
    return found


def test_no_worker_module_makes_a_symbol_of_its_own_drawing() -> None:
    makers = _makers()
    unknown = {name: lines for name, lines in makers.items() if name not in ALLOWED | set(STILL_DRAWN_BY_HARNESS)}
    assert not unknown, (
        f"these modules make a DrawViewSymbol: {unknown}. A drawing is made of FreeCAD's own objects; a symbol comes "
        "only from FreeCAD's library through sheet_symbols.make"
    )


def test_the_debt_lists_only_modules_that_still_make_symbols() -> None:
    makers = _makers()
    assert set(STILL_DRAWN_BY_HARNESS) <= set(makers), (
        f"already clean, remove from STILL_DRAWN_BY_HARNESS: {sorted(set(STILL_DRAWN_BY_HARNESS) - set(makers))}"
    )


def test_the_library_module_is_the_only_one_allowed() -> None:
    assert ALLOWED == {"sheet_symbols.py"}
    assert (WORKER_DIR / "sheet_symbols.py").is_file()


def test_a_library_symbol_is_told_from_one_the_harness_drew(tmp_path: Path) -> None:
    folder = tmp_path / "Mod" / "TechDraw" / "Symbols" / "misc"
    folder.mkdir(parents=True)
    (folder / "Datum_Up.svg").write_text("<svg><g/></svg>\n", encoding="utf-8")
    root = str(tmp_path)
    sheet_symbols._cache.pop(root, None)
    assert sheet_symbols.library(root) == {"misc/Datum_Up.svg": "<svg><g/></svg>\n"}
    assert sheet_symbols.is_library_text(root, "<svg><g/></svg>")
    assert not sheet_symbols.is_library_text(root, '<svg viewBox="0 0 10 10"><path d="M 0 0 L 5 5"/></svg>')
    assert sheet_symbols.library_file(root, "misc/Datum_Up.svg").startswith("<svg>")
    try:
        sheet_symbols.library_file(root, "misc/Nope.svg")
    except KeyError as exc:
        assert "Nope.svg" in str(exc)
    else:
        raise AssertionError("a file the library lacks must be refused")
