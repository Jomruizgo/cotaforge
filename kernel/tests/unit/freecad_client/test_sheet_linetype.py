# ruff: noqa: E501
"""A centre line is a chain line made with TechDraw's own centre-line object: the line number of a centre line or a cosmetic
edge read from and written to the saved content of its property (``sheet_linetype``), the two edges a centre line hangs from,
and what ``sheet_native`` makes of them and reads back for the review, on fake objects. No FreeCAD."""

import io
import sys
import types
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_linetype as lt  # noqa: E402
import sheet_units as su  # noqa: E402

# --- the saved content of TechDraw's lists (as 1.1.3 writes it) -------------------------------------------------------

ITEM = """    <{item}  type="TechDraw::{item}">
        <Style value="{style}"/>
        <Weight value="0.1800000000000000"/>
        <CosmeticTag value="tag-{index}"/>
        <LineNumber value="{number}"/>
    </{item}>
"""


def content(prop: str, numbers: list[int]) -> str:
    item = lt.ITEMS[prop]
    rows = "".join(ITEM.format(item=item, style=4, index=i, number=n) for i, n in enumerate(numbers))
    return f'<Content>\n<{item}List count="{len(numbers)}">\n{rows}</{item}List>\n</Content>\n'


class Line:
    """A cosmetic line or a centre line of the fake view: what TechDraw's Python object answers."""

    def __init__(self, start: tuple[float, float], end: tuple[float, float]) -> None:
        self.Start = types.SimpleNamespace(x=start[0], y=start[1])
        self.End = types.SimpleNamespace(x=end[0], y=end[1])
        self.Format: dict[str, Any] = {"style": 2, "weight": 0.5, "color": (0.0, 0.0, 0.0, 1.0), "visible": True}
        self.Tag = f"t{id(self)}"
        self.number = 2  # TechDraw's default: the dashed line
        self.Edges: list[str] = []
        self.Mode = 0
        self.Extension = 0.0
        self.HorizShift = 0.0
        self.VertShift = 0.0

    @property
    def Radius(self) -> float:  # noqa: N802 - FreeCAD's name; a line has none
        raise TypeError("not a circle")


class Arc(Line):
    """A cosmetic circle or arc of the fake view."""

    def __init__(self, centre: tuple[float, float], radius: float) -> None:
        super().__init__(centre, centre)
        self.Center = types.SimpleNamespace(x=centre[0], y=centre[1])
        self.radius = radius

    @property
    def Radius(self) -> float:  # noqa: N802
        return self.radius


class ModelEdge:
    """A straight edge of the part in the view (``getEdgeByIndex``)."""

    def __init__(self, x1: float, y1: float, x2: float, y2: float) -> None:
        self.ends = (types.SimpleNamespace(x=x1, y=y1), types.SimpleNamespace(x=x2, y=y2))
        self.Curve = type("Line", (), {})()
        self.FirstParameter, self.LastParameter = 0, 1
        self.Length = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5

    def valueAt(self, parameter: int) -> Any:  # noqa: N802 - FreeCAD's name
        return self.ends[parameter]


class View:
    """A TechDraw view with the two lists and their saved content, as far as the sheet uses them."""

    def __init__(self, model: list[ModelEdge] | None = None) -> None:
        self.model = model or []
        self.CosmeticEdges: list[Line] = []
        self.CenterLines: list[Line] = []
        self.PropertiesList: list[str] = []
        self.restored: list[str] = []
        self.X, self.Y, self.TypeId = 100.0, 100.0, "TechDraw::DrawViewPart"

    def getScale(self) -> float:  # noqa: N802
        return 2.0

    def getEdgeByIndex(self, index: int) -> Any:  # noqa: N802 - the model's edges first, then the cosmetic ones, then the centre lines
        edges = [*self.model, *[ModelEdge(e.Start.x, e.Start.y, e.End.x, e.End.y) for e in (*self.CosmeticEdges, *self.CenterLines)]]
        return edges[index]

    def makeCosmeticLine(self, a: Any, b: Any) -> str:  # noqa: N802
        self.CosmeticEdges.append(Line((a.x, a.y), (b.x, b.y)))
        return self.CosmeticEdges[-1].Tag

    def makeCosmeticCircleArc(self, centre: Any, radius: float, start: float, end: float) -> str:  # noqa: N802
        self.CosmeticEdges.append(Arc((centre.x, centre.y), radius))
        return self.CosmeticEdges[-1].Tag

    def getCosmeticEdge(self, tag: str) -> Line:  # noqa: N802
        return next(e for e in self.CosmeticEdges if e.Tag == tag)

    def makeCenterLine(self, edges: list[str], mode: int) -> str:  # noqa: N802
        a, b = (self.model[int(name[4:])] for name in edges)
        mid = lambda i: ((a.ends[i].x + b.ends[1 - i].x) / 2, (a.ends[i].y + b.ends[1 - i].y) / 2)  # noqa: E731
        line = Line(mid(0), mid(1))
        line.Edges, line.Mode = list(edges), mode
        self.CenterLines.append(line)
        return line.Tag

    def getCenterLineBySelection(self, name: str) -> Line:  # noqa: N802
        index = int(name[4:]) - len(self.model) - len(self.CosmeticEdges)
        if index < 0:
            raise ValueError("not a centre line")
        return self.CenterLines[index]

    def getCenterLine(self, tag: str) -> Line:  # noqa: N802
        return next(e for e in self.CenterLines if e.Tag == tag)

    def removeCenterLine(self, tag: str) -> None:  # noqa: N802
        self.CenterLines = [e for e in self.CenterLines if e.Tag != tag]

    def removeCosmeticEdge(self, tag: str) -> None:  # noqa: N802
        self.CosmeticEdges = [e for e in self.CosmeticEdges if e.Tag != tag]

    def dumpPropertyContent(self, prop: str) -> bytearray:  # noqa: N802
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("Persistence.xml", content(prop, [e.number for e in getattr(self, prop)]))
        return bytearray(buffer.getvalue())

    def restorePropertyContent(self, prop: str, data: bytearray) -> None:  # noqa: N802
        archive = zipfile.ZipFile(io.BytesIO(bytes(data)))
        for line, number in zip(getattr(self, prop), lt.numbers(archive.read("Persistence.xml").decode(), prop), strict=True):
            line.number = number
            line.Tag = f"again-{line.Tag}"  # TechDraw gives the objects new tags
        self.restored.append(prop)

    def addProperty(self, kind: str, name: str, group: str, doc: str) -> None:  # noqa: N802
        self.PropertiesList.append(name)
        setattr(self, name, "")

    def setEditorMode(self, name: str, mode: int) -> None:  # noqa: N802
        pass


# --- the line number in the saved content ----------------------------------------------------------------------------


def test_the_chain_line_is_number_4_of_iso_128_and_of_asme_and_3_of_ansi() -> None:
    assert lt.line_number("centre", lt.ISO) == 4 and lt.line_number("centre", lt.ASME) == 4
    assert lt.line_number("centre", lt.ANSI) == 3
    assert lt.line_number("thin", lt.ISO) == lt.CONTINUOUS and lt.line_number("parting", lt.ISO) == 8
    assert lt.line_number("centre", 9) == 4  # a standard TechDraw does not list: the ISO table


def test_the_line_numbers_are_read_in_the_order_of_the_list() -> None:
    assert lt.numbers(content("CenterLines", [2, 4, 10]), "CenterLines") == [2, 4, 10]
    assert lt.numbers(content("CosmeticEdges", []), "CosmeticEdges") == []


def test_only_the_objects_asked_for_change_their_number() -> None:
    xml = content("CosmeticEdges", [2, 2, 2])
    out = lt.renumber(xml, "CosmeticEdges", {0: 4, 2: 1})
    assert lt.numbers(out, "CosmeticEdges") == [4, 2, 1]
    assert out.replace('<LineNumber value="4"', '<LineNumber value="2"').replace('<LineNumber value="1"', '<LineNumber value="2"') == xml


def test_a_list_already_right_is_not_written_again() -> None:
    view = View()
    view.makeCosmeticLine(types.SimpleNamespace(x=0, y=0), types.SimpleNamespace(x=5, y=0))
    assert lt.apply(view, "CosmeticEdges", {0: 4}) is True
    assert [e.number for e in view.CosmeticEdges] == [4] and view.restored == ["CosmeticEdges"]
    assert lt.apply(view, "CosmeticEdges", {0: 4}) is False
    assert view.restored == ["CosmeticEdges"]


def test_a_position_the_list_does_not_have_is_an_error_not_a_guess() -> None:
    with pytest.raises(ValueError, match="fewer than asked"):
        lt.apply(View(), "CenterLines", {0: 4})


# --- the two edges a centre line hangs from ---------------------------------------------------------------------------

SHAFT = [  # a stepped shaft seen from the side: Ø20 over 0..40, Ø30 over 40..100; its end faces
    (0, 0.0, 10.0, 40.0, 10.0),
    (1, 40.0, -10.0, 0.0, -10.0),
    (2, 40.0, 15.0, 100.0, 15.0),
    (3, 100.0, -15.0, 40.0, -15.0),
    (4, 0.0, -10.0, 0.0, 10.0),
    (5, 100.0, -15.0, 100.0, 15.0),
]


def test_the_axis_of_a_cylinder_hangs_from_its_two_sides() -> None:
    pair = lt.pair_for(SHAFT, (37.5, 0.0, 102.5, 0.0), 0.03)
    assert pair is not None and {pair.a, pair.b} == {2, 3} and pair.mode == lt.MODE_HORIZONTAL
    assert pair.extension == pytest.approx(2.5) and pair.shift == pytest.approx(0.0)


def test_the_axis_of_the_whole_shaft_hangs_from_its_longest_step_and_is_shifted_along_itself() -> None:
    pair = lt.pair_for(SHAFT, (-2.5, 0.0, 102.5, 0.0), 0.03)
    assert pair is not None and {pair.a, pair.b} == {2, 3}
    assert pair.extension == pytest.approx(22.5) and pair.shift == pytest.approx(-20.0)  # 40..100 -> -2.5..102.5


def test_a_vertical_axis_takes_the_vertical_mode() -> None:
    bore = [(0, -4.0, 0.0, -4.0, 20.0), (1, 4.0, 20.0, 4.0, 0.0)]
    pair = lt.pair_for(bore, (0.0, -2.5, 0.0, 22.5), 0.03)
    assert pair is not None and pair.mode == lt.MODE_VERTICAL and pair.extension == pytest.approx(2.5)


@pytest.mark.parametrize(
    ("wanted", "why"),
    [
        ((37.5, 5.0, 102.5, 5.0), "no pair is symmetric about a line off the axis"),
        ((0.0, 0.0, 30.0, 30.0), "a line at an angle has no horizontal or vertical mode"),
        ((50.0, 0.0, 60.0, 0.0), "the sides are longer than the line: it is not their centre line"),
    ],
)
def test_no_pair_means_no_centre_line_object(wanted: tuple[float, ...], why: str) -> None:
    assert lt.pair_for(SHAFT, wanted, 0.03) is None, why


# --- what the sheet makes on a view and what the review reads --------------------------------------------------------


@pytest.fixture
def native() -> Iterator[Any]:
    """``sheet_native`` imported with FreeCAD and the window module stubbed, and taken out again so no test sees the stubs."""
    before = set(sys.modules)
    stubs = {name: types.ModuleType(name) for name in ("FreeCAD", "gui")}
    stubs["FreeCAD"].Vector = lambda x, y, z: types.SimpleNamespace(x=x, y=y, z=z)  # type: ignore[attr-defined]
    saved = {name: sys.modules.get(name) for name in stubs}
    sys.modules.update(stubs)
    try:
        import sheet_native  # noqa: PLC0415

        sheet_native.line_standard = lambda: lt.ISO
        yield sheet_native
    finally:
        for name in set(sys.modules) - before:
            sys.modules.pop(name, None)
        for name, module in saved.items():
            if module is not None:
                sys.modules[name] = module
            else:
                sys.modules.pop(name, None)


def page_of(native: Any, view: View) -> Any:
    native.view_names = lambda page: ["front"]
    native.view_object = lambda page, name: view
    native.page_position = lambda page, v: (100.0, 100.0)
    return types.SimpleNamespace(Views=[view])


def shaft_view() -> View:
    return View([ModelEdge(*row[1:]) for row in SHAFT])


def axis(x1: float, x2: float, y: float = 0.0) -> su.NativeCosmetic:
    """A centre line of the sheet in page mm: the view is at (100, 100) at scale 2."""
    return su.NativeCosmetic("ln1", "front", "line", (100 + 2 * x1, 100 + 2 * y, 100 + 2 * x2, 100 + 2 * y), "centre")


def unit(*natives: su.NativeCosmetic) -> Any:
    return types.SimpleNamespace(key="ln1", natives=natives)


def test_an_axis_is_techdraws_centre_line_between_the_sides_of_its_cylinder_in_the_chain_line(native: Any) -> None:
    view = shaft_view()
    page = page_of(native, view)
    assert native.sync_cosmetics(page, [unit(axis(38.75, 101.25))]) == {}
    (line,) = view.CenterLines
    assert view.CosmeticEdges == []  # no cosmetic line stands for the axis
    assert sorted(line.Edges) == ["Edge2", "Edge3"] and line.Mode == lt.MODE_HORIZONTAL
    assert line.Extension == pytest.approx(2.5)  # 1.25 in the view at scale 2: 2.5 mm of the page
    assert line.number == 4 and line.Format["weight"] == 0.18
    assert native.line_type_problems(page) == []  # a correct sheet has nothing to flag
    assert native.own_centre_count(view) == 1 and native.own_cosmetic_count(view) == 0
    assert native.cosmetic_problems(page, unit(axis(38.75, 101.25))) == []


def test_a_centre_line_with_no_sides_in_the_view_is_a_cosmetic_line_and_still_a_chain_line(native: Any) -> None:
    view = shaft_view()
    page = page_of(native, view)
    assert native.sync_cosmetics(page, [unit(axis(10.0, 30.0, y=40.0))]) == {}
    (line,) = view.CosmeticEdges
    assert view.CenterLines == [] and line.number == 4
    assert native.line_type_problems(page) == []


def test_a_sheet_synced_twice_keeps_its_lines_and_writes_nothing(native: Any) -> None:
    view = shaft_view()
    page = page_of(native, view)
    native.sync_cosmetics(page, [unit(axis(38.75, 101.25))])
    written = list(view.restored)
    made: list[str] = []
    native.sync_cosmetics(page, [unit(axis(38.75, 101.25))], made)
    assert made == [] and len(view.CenterLines) == 1 and view.restored == written


def test_a_unit_that_is_gone_takes_its_centre_line_with_it(native: Any) -> None:
    view = shaft_view()
    page = page_of(native, view)
    native.sync_cosmetics(page, [unit(axis(38.75, 101.25))])
    native.sync_cosmetics(page, [])
    assert view.CenterLines == [] and native.own_centre_count(view) == 0


def test_two_arcs_of_one_circle_both_get_their_line_number(native: Any) -> None:
    view = shaft_view()
    page = page_of(native, view)
    arcs = [su.NativeCosmetic("threads", "front", "arc", (120.0, 110.0, 8.0, a, b), "thin") for a, b in ((10.0, 170.0), (190.0, 350.0))]
    assert native.sync_cosmetics(page, [types.SimpleNamespace(key="threads", natives=tuple(arcs))]) == {}
    assert [e.number for e in view.CosmeticEdges] == [lt.CONTINUOUS, lt.CONTINUOUS]  # neither is left the dashed line


def test_an_arc_read_back_with_its_centre_mirrored_is_still_the_sheets_own(native: Any) -> None:
    view = shaft_view()
    page = page_of(native, view)
    arc = su.NativeCosmetic("threads", "front", "arc", (120.0, 110.0, 8.0, 10.0, 170.0), "thin")
    thread = types.SimpleNamespace(key="threads", natives=(arc,))
    native.sync_cosmetics(page, [thread])
    view.CosmeticEdges[0].Center.y *= -1  # what TechDraw answers once the list was restored (the arc is drawn where it was)
    assert native.cosmetic_problems(page, thread) == []
    made: list[str] = []
    native.sync_cosmetics(page, [thread], made)
    assert made == [] and len(view.CosmeticEdges) == 1


def test_a_centre_line_whose_edges_were_numbered_again_is_made_again_on_the_sides_of_its_cylinder(native: Any) -> None:
    view = shaft_view()
    page = page_of(native, view)
    native.sync_cosmetics(page, [unit(axis(38.75, 101.25))])
    view.model[:0] = [ModelEdge(0.0, 50.0, 5.0, 50.0), ModelEdge(0.0, 60.0, 5.0, 60.0)]  # the view shows its hidden edges: two more, first
    made: list[str] = []
    native.sync_cosmetics(page, [unit(axis(38.75, 101.25))], made)
    (line,) = view.CenterLines
    assert made == ["ln1"] and sorted(line.Edges) == ["Edge4", "Edge5"] and line.number == 4


def test_the_review_reads_a_dashed_centre_line_made_by_hand(native: Any) -> None:
    view = shaft_view()
    page = page_of(native, view)
    native.sync_cosmetics(page, [unit(axis(38.75, 101.25))])
    view.makeCenterLine(["Edge0", "Edge1"], lt.MODE_HORIZONTAL)  # the engineer's: TechDraw makes it dashed (number 2)
    assert native.line_type_problems(page) == [("front", "centre line", 2)]


def test_the_review_reads_a_cosmetic_centre_line_that_is_dashed(native: Any) -> None:
    view = shaft_view()
    page = page_of(native, view)
    tag = view.makeCosmeticLine(types.SimpleNamespace(x=0, y=30), types.SimpleNamespace(x=9, y=30))
    view.getCosmeticEdge(tag).Format = {"style": 4, "weight": 0.18}  # a sheet made before the line number was set
    thin = view.makeCosmeticLine(types.SimpleNamespace(x=0, y=35), types.SimpleNamespace(x=9, y=35))
    view.getCosmeticEdge(thin).Format = {"style": 1, "weight": 0.18}  # not a centre line: any number
    assert native.line_type_problems(page) == [("front", "cosmetic centre line", 2)]


def test_a_centre_line_techdraw_drew_elsewhere_becomes_a_cosmetic_chain_line_where_it_was_asked(native: Any) -> None:
    view = shaft_view()
    page = page_of(native, view)
    native.sync_cosmetics(page, [unit(axis(38.75, 101.25))])
    assert native.verify_centre_lines(page) != []  # the fake draws it without its extension: 40..100, not 38.75..101.25
    assert view.CenterLines == []
    (line,) = view.CosmeticEdges
    assert (line.Start.x, line.End.x, line.number) == (38.75, 101.25, 4)
    assert native.verify_centre_lines(page) == [] and native.line_type_problems(page) == []


def test_a_centre_line_drawn_where_asked_is_left_alone(native: Any) -> None:
    view = shaft_view()
    page = page_of(native, view)
    native.sync_cosmetics(page, [unit(axis(40.0, 100.0))])  # no reach: the fake draws exactly this
    assert native.verify_centre_lines(page) == [] and len(view.CenterLines) == 1


def test_the_signature_of_a_view_with_centre_lines_says_they_are_centre_line_objects() -> None:
    old = '[["line",[177.5,100.0,302.5,100.0],"centre"]]'  # what a sheet made before this stored
    assert su.cosmetic_signature([axis(38.75, 101.25)]) != old
    thin = su.NativeCosmetic("t1", "front", "arc", (1.0, 2.0, 3.0, 0.0, 90.0), "thin")
    assert su.CENTRE_LINE_MARK not in su.cosmetic_signature([thin])
