# ruff: noqa: E501
"""How the harness colours faces on FreeCAD 1.1, without FreeCAD: the object the view paints, the opacity of a colour, what is
put back after a highlight, and the sources that must never go back to the old way. What the engineer SEES is checked in
``tests/integration/freecad/test_face_colours_rendered.py``, from the rendered picture."""

import ast
import importlib
import re
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import highlight  # noqa: E402
import hole_paint  # noqa: E402

FIELDS = ("AmbientColor", "DiffuseColor", "EmissiveColor", "Shininess", "SpecularColor", "Transparency")


class FakeMaterial:
    """Enough of ``App.Material``: the six attributes, DiffuseColor a 4-tuple and Transparency 0 (opaque) .. 1."""

    def __init__(self) -> None:
        self.AmbientColor = (0.2, 0.2, 0.2, 1.0)  # noqa: N815
        self.DiffuseColor = (0.8, 0.8, 0.8, 1.0)  # noqa: N815
        self.EmissiveColor = (0.0, 0.0, 0.0, 1.0)  # noqa: N815
        self.Shininess = 0.2  # noqa: N815
        self.SpecularColor = (0.5, 0.5, 0.5, 1.0)  # noqa: N815
        self.Transparency = 0.0  # noqa: N815


@pytest.fixture
def appearance(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setitem(sys.modules, "FreeCAD", types.SimpleNamespace(Material=FakeMaterial))
    sys.modules.pop("appearance", None)
    module = importlib.import_module("appearance")
    yield module
    sys.modules.pop("appearance", None)


def material(rgb: tuple[float, float, float], transparency: float = 0.0, shininess: float = 0.2) -> FakeMaterial:
    made = FakeMaterial()
    made.DiffuseColor = (*rgb, 1.0)
    made.Transparency = transparency
    made.Shininess = shininess
    return made


# --- the object the view paints -------------------------------------------------------------------------------------------


def test_a_partdesign_body_is_painted_through_its_tip(appearance: Any) -> None:
    tip = SimpleNamespace(ViewObject=object(), Name="Pad")
    body = SimpleNamespace(TypeId="PartDesign::Body", Tip=tip, ViewObject=object())
    assert appearance.paint_object(body) is tip


def test_every_other_object_paints_itself(appearance: Any) -> None:
    box = SimpleNamespace(TypeId="Part::Box", ViewObject=object())
    assert appearance.paint_object(box) is box
    no_tip = SimpleNamespace(TypeId="PartDesign::Body", Tip=None, ViewObject=object())
    assert appearance.paint_object(no_tip) is no_tip
    hidden_tip = SimpleNamespace(TypeId="PartDesign::Body", Tip=SimpleNamespace(ViewObject=None), ViewObject=object())
    assert appearance.paint_object(hidden_tip) is hidden_tip


# --- the opacity of a colour ----------------------------------------------------------------------------------------------


def test_the_fourth_value_of_a_colour_is_its_opacity(appearance: Any) -> None:
    assert appearance.rgba(material((1.0, 0.0, 0.0))) == (1.0, 0.0, 0.0, 1.0)
    assert appearance.rgba(material((1.0, 0.0, 0.0), transparency=1.0))[3] == 0.0  # invisible
    painted = appearance.recolored(material((0.5, 0.5, 0.5), shininess=0.9), (1.0, 0.55, 0.0, 1.0))
    assert painted.DiffuseColor == (1.0, 0.55, 0.0, 1.0)
    assert painted.Transparency == 0.0  # opaque: the old 4th value (0.0) made this 1.0, a face that is not there
    assert painted.Shininess == 0.9  # the rest of the material is the object's own


def test_the_colours_of_the_harness_are_opaque() -> None:
    assert hole_paint.to_rgba((255, 128, 128))[3] == 1.0
    source = (WORKER_DIR / "gui.py").read_text(encoding="utf-8")
    found = re.search(r"^HIGHLIGHT_COLOR: highlight\.RGBA = (\(.*\))$", source, re.MULTILINE)
    assert found is not None
    colour = ast.literal_eval(found.group(1))
    assert len(colour) == 4 and colour[3] == 1.0


def test_no_source_writes_the_diffuse_colour_of_a_view() -> None:
    """``DiffuseColor`` is FreeCAD 1.1's compatibility view of the face colours and the alpha it takes is the opacity: the
    harness writes ``ShapeAppearance`` (``appearance``) and never goes back to it."""
    for name in ("gui.py", "hole_colours.py", "queries.py"):
        source = (WORKER_DIR / name).read_text(encoding="utf-8")
        assert not re.search(r"\.DiffuseColor\s*=", source), name


# --- materials of a list of colours ---------------------------------------------------------------------------------------


def test_the_materials_of_a_list_keep_the_ones_that_did_not_change(appearance: Any) -> None:
    grey, red = material((0.5, 0.5, 0.5), shininess=0.7), material((1.0, 0.0, 0.0), shininess=0.1)
    current = [grey, red]
    colours = [appearance.rgba(grey), (0.0, 1.0, 0.0, 1.0)]
    made = appearance.from_rgba(colours, current)
    assert made[0] is not grey and appearance.rgba(made[0]) == appearance.rgba(grey) and made[0].Shininess == 0.7
    assert appearance.rgba(made[1]) == (0.0, 1.0, 0.0, 1.0) and made[1].Shininess == 0.1  # recoloured from ITS material


def test_a_uniform_list_grows_to_a_material_per_face(appearance: Any) -> None:
    grey = material((0.5, 0.5, 0.5), shininess=0.7)
    colours = [appearance.rgba(grey)] * 3
    colours[1] = (1.0, 0.55, 0.0, 1.0)
    made = appearance.from_rgba(colours, [grey])
    assert [appearance.rgba(m) for m in made] == colours
    assert all(m.Shininess == 0.7 for m in made)


def test_what_is_put_back_is_what_was_there(appearance: Any) -> None:
    original = [material((0.5, 0.5, 0.5)), material((0.0, 0.7, 0.0)), material((0.5, 0.5, 0.5))]
    back = appearance.restore(original, 3, 0)
    assert [appearance.rgba(m) for m in back] == [appearance.rgba(m) for m in original]
    assert all(
        a is not b for a, b in zip(back, original, strict=True)
    )  # copies, so writing them does not touch the record
    assert len(appearance.restore([original[0]], 9, 0)) == 1  # a uniform colour fits any solid


def test_a_list_that_no_longer_fits_the_solid_falls_back_to_the_colour_of_the_object(appearance: Any) -> None:
    original = [material((0.0, 0.7, 0.0)), material((0.5, 0.5, 0.5)), material((0.5, 0.5, 0.5))]
    base = highlight.base_index([appearance.rgba(m) for m in original])
    assert base == 1  # the grey most faces show, not face 0 that is a hole
    back = appearance.restore(original, 7, base)  # a feature made the solid 7 faces meanwhile
    assert len(back) == 1 and appearance.rgba(back[0]) == (0.5, 0.5, 0.5, 1.0)


# --- the colour of the object itself --------------------------------------------------------------------------------------

GREY = (0.5, 0.5, 0.5, 1.0)
PINK = hole_paint.to_rgba((255, 128, 128))


def test_the_base_colour_is_the_one_most_faces_show_that_is_not_ours() -> None:
    fallback = (0.1, 0.1, 0.1, 1.0)
    assert hole_paint.base_of([PINK, GREY, GREY, PINK], [(255, 128, 128)], fallback) == GREY
    assert hole_paint.base_of([GREY], [(255, 128, 128)], fallback) == GREY


def test_the_base_colour_is_not_the_first_face_when_that_one_is_a_hole() -> None:
    """FreeCAD's ShapeColor follows face 0 of a per-face list: it cannot be the base once face 0 is painted."""
    assert hole_paint.base_of([PINK, GREY, GREY, GREY], [(255, 128, 128)], (1.0, 0.0, 0.0, 1.0)) == GREY


def test_with_nothing_but_ours_the_base_is_the_shape_colour() -> None:
    fallback = (0.1, 0.1, 0.1, 1.0)
    assert hole_paint.base_of([PINK, PINK], [(255, 128, 128)], fallback) == fallback
    assert hole_paint.base_of([], [], fallback) == fallback


def test_a_tie_for_the_base_goes_to_the_first() -> None:
    assert highlight.base_index([GREY, PINK, GREY, PINK]) == 0
    assert highlight.base_index([]) == 0


# --- one object, faces and edges --------------------------------------------------------------------------------------------


def test_the_faces_and_the_edges_of_one_object_are_remembered_separately() -> None:
    state = highlight.HighlightState()
    state.remember("doc", "Pad", "ShapeAppearance", ["faces"])
    state.remember("doc", "Pad", "LineColorArray", ["edges"])
    assert sorted(state.entries_for("doc")) == [
        ("Pad", "LineColorArray", ("edges",)),
        ("Pad", "ShapeAppearance", ("faces",)),
    ]
    state.discard("doc", "Pad", "ShapeAppearance")
    assert state.entries_for("doc") == [
        ("Pad", "LineColorArray", ("edges",))
    ]  # the other one still waits to be put back
    state.discard("doc", "Pad")
    assert not state
