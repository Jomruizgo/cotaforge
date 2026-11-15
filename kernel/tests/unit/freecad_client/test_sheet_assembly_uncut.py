# ruff: noqa: E501
"""In a section of an assembly the shafts, bolts, keys and pins stay whole (no FreeCAD): what a part is (its designation, the standard
it names, its shape), when the plane runs along it, which bodies share a hatch, what TechDraw is asked for (one section per hatch over
a hidden base, a view of the whole parts, all bound to the first one) and the question asked when a part is held whole by its shape."""

import dataclasses
import sys
from pathlib import Path
from typing import Any

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import items_views as views  # noqa: E402
import sheet_context as sc  # noqa: E402
import sheet_items as items  # noqa: E402
import sheet_model as model  # noqa: E402
import sheet_solid as solid  # noqa: E402
import sheet_units as units  # noqa: E402

from .test_sheet_shop_defects import found  # noqa: E402
from .test_sheet_views import ViewsGeometry, views_context  # noqa: E402

# --- what a part is -----------------------------------------------------------------------------------------------------------------------


def test_the_head_noun_of_a_designation_decides_when_a_solid_word_and_a_cut_one_meet() -> None:
    kinds = {
        "Eje de transmisión": ("shaft", "name"),
        "Eje de la carcasa": ("shaft", "name"),
        "drive shaft": ("shaft", "name"),
        "Tornillo M6x25": ("screw", "name"),
        "cover screw": ("screw", "name"),
        "Chaveta 6x6x17": ("key", "name"),
        "Pasadores": ("pin", "name"),
    }
    assert {name: solid.classify(name) for name in kinds} == kinds
    for name in ("Carcasa del eje", "Tapa de eje", "Tapa del tornillo", "shaft cover", "bearing housing", "Cubo", "Rodamiento 6204", "Casquillo separador"):
        assert solid.classify(name) is None and solid.named_cut(name), name


def test_the_designation_comes_before_the_file_name_and_a_standard_comes_after_the_words() -> None:
    assert solid.classify(["Carcasa", "shaft"]) is None  # the designation says housing: the file name does not change it
    assert solid.classify(["", "shaft"]) == ("shaft", "name")
    assert solid.classify("ISO 4762 M6x20") == ("screw", "standard")
    assert solid.classify("DIN 6885 8x7x30") == ("key", "standard")
    assert solid.classify("Pieza ISO 2338 6m6x20") == ("pin", "standard")
    assert solid.classify("DIN 471 Ø20") is None  # a circlip is not in the list


def test_a_long_solid_of_revolution_or_a_small_block_is_a_solid_part_by_its_shape_alone_and_the_basis_says_so() -> None:
    assert solid.classify("Pieza 1", round_solid=True) == ("shaft", "shape")
    assert solid.classify("Pieza 1") is None
    assert solid.classify("Pieza 3", block=(30.0, 8.0, 7.0), span=150.0) == ("key", "shape")
    assert solid.classify("Pieza 3", block=(120.0, 60.0, 10.0), span=150.0) is None  # a plate
    assert solid.classify("Pieza 3", block=(100.0, 40.0, 40.0), span=120.0) is None  # a bar as long as the assembly is its body
    assert solid.classify("Pieza 3", block=(20.0, 20.0, 20.0), span=150.0) is None  # a cube is no key
    assert solid.classify("Carcasa", round_solid=True) is None  # the words win over the shape
    assert solid.key_like((17.0, 6.0, 6.0), 156.0)


def test_a_plane_cuts_a_part_lengthwise_only_when_it_runs_along_its_axis() -> None:
    assert solid.lengthwise((0.0, 0.0, 1.0), (0.0, 1.0, 0.0))  # a plane through the axis of a vertical shaft
    assert solid.lengthwise((0.0, 0.0, 1.0), (1.0, 0.0, 0.0))
    assert not solid.lengthwise((0.0, 0.0, 1.0), (0.0, 0.0, 1.0))  # square to it: a cross section, the circle is cut and hatched
    assert not solid.lengthwise((0.0, 0.0, 1.0), (0.0, 0.5, 0.866))
    assert solid.lengthwise(None, (0.0, 0.0, 1.0))  # an axis nobody knows: the plane is taken to run along


def test_touching_bodies_never_share_a_hatch_class() -> None:
    styles = {"body 1": (0.0, 1.0), "body 3": (90.0, 1.0), "body 4": (90.0, 1.0), "body 5": (0.0, 2.0), "body 6": (0.0, 1.0)}
    assert solid.hatch_classes(styles) == [["body 1", "body 6"], ["body 3", "body 4"], ["body 5"]]
    assert solid.hatch_classes({"body 1": (180.0, 1.0), "body 2": (0.0, 1.0)}) == [["body 1", "body 2"]]  # a turn of 180 is the same hatch


# --- what TechDraw is asked for ------------------------------------------------------------------------------------------------------------


class Bench(ViewsGeometry):
    """Four components (shaft, housing, bearing, key) the way the section sees them: ``subset_projection`` gives a box by component."""

    names = ["Component", "Component001", "Component002", "Component003"]

    def __init__(self) -> None:
        super().__init__()
        self.subsets: list[tuple[tuple[int, ...], bool]] = []

    def source_names(self) -> list[str] | None:
        return list(self.names)

    def subset_projection(self, indexes: Any, centre: Any, xdir: Any, direction: Any, cut: Any = None) -> sc.Projection:
        self.subsets.append((tuple(indexes), cut is not None))
        low = float(min(indexes)) * 10.0
        return sc.Projection([model.Line(low, 0.0, low + 10.0, 0.0), model.Line(low + 10.0, 0.0, low + 10.0, 20.0)], [], [])


def view_info() -> sc.ViewInfo:
    return sc.ViewInfo("A-A", 100.0, 50.0, 0.5, (0, 1, 0), (1, 0, 0), (0.0, 0.0, 0.0), (100.0, 50.0, 120.0, 70.0), derived=True)


def bench_parts(whole: frozenset[int], styles: dict[str, tuple[float, float]]) -> tuple[units.NativePart, ...]:
    ctx = views_context()
    ctx.geometry = Bench()
    return views._native_parts(ctx, view_info(), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0), whole, styles)


def test_the_section_is_a_cut_piece_for_each_hatch_and_a_view_of_the_whole_parts() -> None:
    styles = {"body 2": (0.0, 1.0), "body 3": (90.0, 1.0)}  # the housing and the bearing; the shaft (0) and the key (3) stay whole
    parts = bench_parts(frozenset({0, 3}), styles)
    assert [(p.role, p.sources, p.turn, p.spacing) for p in parts] == [
        ("cut", ("Component001",), 0.0, 1.0),
        ("cut", ("Component002",), 90.0, 1.0),
        ("whole", ("Component", "Component003"), 0.0, 1.0),
    ]
    assert parts[0].box == (100.0 + 0.5 * 10.0, 50.0, 100.0 + 0.5 * 20.0, 50.0 + 0.5 * 20.0)  # the box of what the sheet draws of it, on the page
    assert [p.objects for p in parts] == [2, 2, 1]  # a cut piece is its hidden base and its section


def test_a_body_with_no_cut_face_goes_with_the_first_hatch_and_a_section_of_one_hatch_with_nothing_whole_is_one_object() -> None:
    parts = bench_parts(frozenset({0}), {"body 2": (0.0, 1.0), "body 3": (90.0, 1.0)})
    assert parts[0].sources == ("Component001", "Component003")  # the key is behind the plane: lines, no hatch
    assert bench_parts(frozenset(), {"body 2": (0.0, 1.0), "body 3": (0.0, 1.0)}) == ()


def test_a_geometry_that_cannot_name_its_sources_leaves_the_section_as_one_object() -> None:
    ctx = views_context()
    assert views._native_parts(ctx, view_info(), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0), frozenset({0}), {"body 1": (0.0, 1.0)}) == ()


def native_view(parts: tuple[units.NativePart, ...] = ()) -> units.NativeView:
    return units.NativeView("secA", "A-A", "section", "top", (0.0, 0.0, 10.0, 10.0), 0.5, (0, 1, 0), (1, 0, 0), "A", at=(0.0, 0.0, 0.0), parts=parts)


def test_the_parts_are_in_the_signature_and_in_the_count_of_objects() -> None:
    plain = native_view()
    split = native_view(bench_parts(frozenset({0, 3}), {"body 2": (0.0, 1.0), "body 3": (90.0, 1.0)}))
    turned = native_view(tuple(dataclasses.replace(p, turn=45.0) if p.role == "cut" else p for p in split.parts))
    make = lambda nv: units.Unit("secA", "secA section", units.KIND_ITEM, (), "section", None, (nv,))  # noqa: E731
    assert units.signature(make(plain)) != units.signature(make(split)) != units.signature(make(turned))
    assert (plain.objects, split.objects) == (1, 5)
    assert units.object_count(make(plain)) == 1 and units.object_count(make(split)) == 5
    assert units.view_object_count(make(split)) == 5 and units.view_object_count(units.Unit("n", "n", units.KIND_ITEM, ())) == 0
    assert '"cut"' not in units.signature(make(plain))  # a view without parts keeps the signature it always had: the sheets made before are left alone


# --- the item and its question --------------------------------------------------------------------------------------------------------------


def test_a_section_takes_the_components_it_leaves_whole_and_nothing_else() -> None:
    spec = {"type": "section", "id": "s", "at": [0, 0, 0], "normal": [0, 1, 0], "view": "top"}
    assert items.normalise({**spec, "whole": ["Eje", "key_1"]}, set(), 1)["whole"] == ["Eje", "key_1"]
    assert items.normalise({**spec, "whole": []}, set(), 1)["whole"] == []  # none: every body is cut
    for bad in ("Eje", [1], [""], {"a": 1}):
        with pytest.raises(sc.ItemError, match="component ids or designations"):
            items.normalise({**spec, "whole": bad}, set(), 1)


class Guessing(ViewsGeometry):
    def solid_facts(self) -> dict[int, Any]:
        return {
            0: solid.SolidFact("comp:1", "Pieza 1", "shaft", "shape", True, (0.0, 0.0, 1.0)),
            1: solid.SolidFact("comp:2", "Eje", "shaft", "name", True, (0.0, 0.0, 1.0)),
        }


def test_a_part_held_whole_by_its_shape_alone_is_a_blocking_question_and_one_named_is_not() -> None:
    ctx = views_context()
    ctx.geometry = Guessing()
    note = views._whole_note(ctx, "A-A", frozenset({0, 1}))
    assert "Pieza 1" in note and "Eje" in note and "ISO 128-34" in note
    assert ctx.extras["whole_by_shape"] == {"A-A": [("Pieza 1", "shaft")]}  # only the one nothing but its shape says
    out = found(ctx)
    assert [f.code for f in out] == ["assembly_whole_guess"] and out[0].key == "A-A"
    assert "Pieza 1 (shaft)" in out[0].message and "Eje" not in out[0].message and "whole=[...]" in out[0].message
    from sheet_checks import NON_BLOCKING  # noqa: PLC0415

    assert "assembly_whole_guess" not in NON_BLOCKING  # it is asked before the sheet is exported
    ctx.extras.pop("whole_by_shape")
    assert found(ctx) == []
    assert views._whole_note(ctx, "B-B", frozenset()) == ""
