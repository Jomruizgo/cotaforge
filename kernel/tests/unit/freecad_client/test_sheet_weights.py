# ruff: noqa: E501
"""The line weights of a sheet: the table (``sheet_weights``), the layers of the composed sheet that follow it, and the check of a
page's native objects against it (``sheet_mirror.weight_readings`` on fake objects). No FreeCAD."""

import sys
import types
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_model as model  # noqa: E402
import sheet_weights as sw  # noqa: E402

# --- the table ----------------------------------------------------------------------------------------------------


def test_the_table_is_iso_128_20_group_035_with_hidden_lines_thick() -> None:
    assert sw.weight(sw.VISIBLE) == sw.weight(sw.HIDDEN) == 0.35
    assert sw.weight(sw.DIMENSION) == sw.weight(sw.CENTRE) == sw.weight(sw.THREAD) == 0.18
    assert sw.weight(sw.HATCH) == sw.weight(sw.TANGENT) == 0.07
    assert sw.weight(sw.FRAME) == 0.7  # ISO 5457: twice the thick line
    assert "ISO 128-20" in sw.SOURCE and "owner's reference drawings" in sw.SOURCE


def test_every_kind_has_a_weight_and_the_thick_one_is_at_least_twice_the_thin_one() -> None:
    kinds = (
        sw.VISIBLE, sw.HIDDEN, sw.CENTRE, sw.DIMENSION, sw.HATCH, sw.SECTION_LINE, sw.THREAD, sw.TANGENT, sw.TITLE_BLOCK, sw.TABLE_GRID, sw.FRAME,
    )  # fmt: skip
    assert set(sw.WEIGHTS) == set(kinds)
    assert sw.THICK >= 1.9 * sw.THIN  # ISO 128-20: the ratio of thick to thin is two
    assert sw.allowed_widths() == [0.07, 0.18, 0.35, 0.7]


def test_the_techdraw_line_group_is_the_035_entry() -> None:
    assert (
        sw.LINE_GROUP_NAME == "FC 0.35mm" and sw.LINE_GROUP_INDEX == 1
    )  # TechDraw's list: 0.25, 0.35, 0.50, ... in file order
    assert sw.LINE_GROUP_NAME.endswith(f"{sw.THICK:.2f}mm")


def test_the_view_properties_cover_the_widths_a_view_provider_has() -> None:
    assert set(sw.VIEW_PROPS) == {"LineWidth", "HiddenWidth", "IsoWidth", "ExtraWidth"}
    assert sw.VIEW_PROPS["HiddenWidth"] == sw.HIDDEN and sw.VIEW_PROPS["LineWidth"] == sw.VISIBLE


def test_the_composed_sheets_layers_follow_the_table() -> None:
    layers = model.LAYERS
    assert layers[model.CONTOUR].width_mm == layers[model.HIDDEN].width_mm == 0.35
    assert layers[model.DIMENSIONS].width_mm == layers[model.AXES].width_mm == layers[model.TEXT].width_mm == 0.18
    assert layers[model.HATCH].width_mm == 0.07
    assert model.THICK_MM == 0.35 and model.THIN_MM == 0.18 and model.HAIR_MM == 0.07
    for layer, style in layers.items():
        assert sw.in_table(style.width_mm), layer
    pens: dict[int, set[float]] = {}
    for style in layers.values():
        pens.setdefault(style.color, set()).add(style.width_mm)
    assert all(len(widths) == 1 for widths in pens.values()), pens  # the DXF has no lineweight: one colour, one pen


# --- the check ----------------------------------------------------------------------------------------------------


def test_a_width_is_in_the_table_for_its_kind_or_for_any_kind() -> None:
    assert sw.in_table(0.35, sw.VISIBLE) and sw.in_table(0.351, sw.HIDDEN)
    assert not sw.in_table(0.18, sw.HIDDEN)  # a hidden line drawn thin is off the table
    assert sw.in_table(0.18) and sw.in_table(0.07) and not sw.in_table(0.25)


def test_what_techdraw_gives_by_default_or_the_old_table_is_flagged() -> None:
    readings = [
        ("View_front", sw.VISIBLE, 0.7),
        ("View_front", sw.HIDDEN, 0.35),
        ("Dim", sw.DIMENSION, 0.35),
        ("Dim2", sw.DIMENSION, 0.25),
    ]
    assert sw.off_table(readings) == [
        "View_front: 0.7 mm (0.35 mm expected)",
        "Dim: 0.35 mm (0.18 mm expected)",
        "Dim2: 0.25 mm (0.18 mm expected)",
    ]


def test_a_cosmetic_line_of_unknown_kind_may_be_any_width_of_the_table() -> None:
    assert sw.off_table([("View_top", None, 0.18), ("View_top", None, 0.35)]) == []
    (line,) = sw.off_table([("View_top", None, 0.5), ("View_top", None, 0.5), ("View_top", None, 0.6)])
    assert line.startswith(
        "View_top: 0.5 mm (one of 0.07, 0.18, 0.35, 0.7 mm expected); 0.6 mm"
    )  # one line per object, each width once


def test_a_page_on_the_table_has_nothing_to_flag() -> None:
    readings = [(f"v{i}", kind, sw.weight(kind)) for i, kind in enumerate(sw.WEIGHTS)]
    assert sw.off_table(readings) == []


# --- the page's objects: what the mirror reads and sets -------------------------------------------------------------


class _Quantity(float):
    """A TechDraw width property: a number that answers ``float()``."""


class _ViewObject:
    def __init__(self, **props: float) -> None:
        for name, value in props.items():
            setattr(self, name, _Quantity(value))


class _Obj:
    def __init__(self, label: str, type_id: str, vo: _ViewObject | None, edges: list[Any] | None = None) -> None:
        self.Label = label
        self.TypeId = type_id
        self.ViewObject = vo
        self.CosmeticEdges = edges or []
        self.CenterLines: list[Any] = []
        self.PropertiesList: list[str] = []


class _Edge:
    def __init__(self, weight: float) -> None:
        self.Format = {"weight": weight, "style": 1}


class _Page:
    def __init__(self, views: list[_Obj]) -> None:
        self.Views = views
        self.PropertiesList: list[str] = []
        self.Document = types.SimpleNamespace(Objects=[])

    def addProperty(self, kind: str, name: str, group: str, doc: str) -> None:  # noqa: N802 - FreeCAD's name
        self.PropertiesList.append(name)
        setattr(self, name, "")

    def setEditorMode(self, name: str, mode: int) -> None:  # noqa: N802
        pass


@pytest.fixture
def mirror() -> Iterator[Any]:
    """``sheet_mirror`` imported with FreeCAD and the window module stubbed, and taken out again so no test sees the stubs."""
    before = set(sys.modules)
    stubs = {name: types.ModuleType(name) for name in ("FreeCAD", "gui")}
    stubs["gui"].ACTIVE = False  # type: ignore[attr-defined]
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        import sheet_mirror  # noqa: PLC0415

        yield sheet_mirror
    finally:
        for name in set(sys.modules) - before:
            sys.modules.pop(name, None)
        for name, module in saved.items():
            if module is not None:
                sys.modules[name] = module
            else:
                sys.modules.pop(name, None)


def _page(vo_view: _ViewObject, vo_dim: _ViewObject, edges: list[Any] | None = None) -> _Page:
    return _Page(
        [
            _Obj("View_front", "TechDraw::DrawViewPart", vo_view, edges),
            _Obj("Dim", "TechDraw::DrawViewDimension", vo_dim),
        ]
    )


def test_the_mirror_sets_the_table_on_the_view_providers_and_marks_the_page(mirror: Any) -> None:
    view = _ViewObject(LineWidth=0.7, HiddenWidth=0.35, IsoWidth=0.5, ExtraWidth=1.4, WeightPattern=0.1)
    dim = _ViewObject(LineWidth=0.35, Fontsize=5.0)
    page = _page(view, dim)
    assert not mirror.weights_set(page)
    mirror.apply_weights(page)
    assert (view.LineWidth, view.HiddenWidth, view.IsoWidth, view.ExtraWidth, view.WeightPattern) == (
        0.35,
        0.35,
        0.07,
        0.35,
        0.07,
    )
    assert dim.LineWidth == 0.18 and dim.Fontsize == 3.5
    assert mirror.weights_set(page)
    assert mirror.line_weights(page) == {
        "outline": 0.35,
        "thin": 0.35,
        "tangent": 0.07,
        "hatch": 0.07,
        "dimension": 0.18,
    }
    assert mirror.off_table(page) == []


def test_a_window_less_page_is_not_marked_and_only_its_cosmetic_lines_are_read(mirror: Any) -> None:
    page = _Page([_Obj("View_top", "TechDraw::DrawViewPart", None, [_Edge(0.18), _Edge(0.5)])])
    mirror.apply_weights(page)
    assert not mirror.weights_set(page)  # nothing was set: a window will do it when it opens the file
    assert mirror.line_weights(page) == {}
    assert mirror.off_table(page) == ["View_top: 0.5 mm (one of 0.07, 0.18, 0.35, 0.7 mm expected)"]


def test_the_review_reads_what_the_engineer_changed(mirror: Any) -> None:
    page = _page(
        _ViewObject(LineWidth=0.35, HiddenWidth=0.35, IsoWidth=0.07, ExtraWidth=0.35, WeightPattern=0.07),
        _ViewObject(LineWidth=0.18),
    )
    assert mirror.off_table(page) == []
    page.Views[1].ViewObject.LineWidth = _Quantity(0.5)  # a dimension thickened by hand
    page.Views[0].ViewObject.HiddenWidth = _Quantity(0.18)  # hidden lines made thin
    assert mirror.off_table(page) == ["View_front: 0.18 mm (0.35 mm expected)", "Dim: 0.5 mm (0.18 mm expected)"]
