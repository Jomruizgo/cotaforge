"""The FreeCAD adapter sends the colour of the convention in force with the hole and says whether the isometric view is
in colour, reading the convention when the thing is made. No FreeCAD: _call is replaced to see the request."""

from pathlib import Path
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter
from cotaforge.contract import (
    DrawingRequest,
    EditFeatureRequest,
    EndCondition,
    FaceHandle,
    FeatureEdit,
    FeatureHandle,
    HoleRequest,
    HoleStandard,
)
from cotaforge.conventions import FILENAME, Conventions


def _hole(**extra: Any) -> HoleRequest:
    return HoleRequest(
        operation_id="h1",
        face=FaceHandle(id="face:1"),
        position=(0.0, 0.0),
        standard=HoleStandard.simple,
        diameter_mm=6.0,
        end_condition=EndCondition.through_all,
        **extra,
    )


@pytest.fixture
def sent(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    log: list[tuple[str, Any]] = []
    monkeypatch.setattr(FreeCADAdapter, "_call", lambda self, method, request, *a, **k: log.append((method, request)))
    return log


def test_without_a_convention_object_the_defaults_of_the_product_apply(sent: list[tuple[str, Any]]) -> None:
    adapter = FreeCADAdapter()
    assert adapter.conventions is None
    adapter.hole(_hole(fit="H7"))
    request = sent[-1][1]
    assert request.colour == (255, 128, 128) and request.hole_class == "precision" and request.precision_grade == 8


def test_the_convention_in_force_when_the_hole_is_made_is_the_one_sent(
    tmp_path: Path, sent: list[tuple[str, Any]]
) -> None:
    adapter = FreeCADAdapter()
    adapter.conventions = Conventions(tmp_path / "home", tmp_path / "space")
    adapter.hole(_hole())
    assert sent[-1][1].colour == (0, 192, 0)
    adapter.conventions.set_holes("proyecto", {"colores": {"liso": [1, 2, 3]}, "grado_it_preciso": 9})
    adapter.hole(_hole(fit="H9"))
    request = sent[-1][1]
    assert request.colour == (255, 128, 128) and request.precision_grade == 9
    adapter.hole(_hole())
    assert sent[-1][1].colour == (1, 2, 3)


def test_a_threaded_hole_is_threaded_whatever_its_fit(sent: list[tuple[str, Any]]) -> None:
    from cotaforge.contract import HoleThread

    FreeCADAdapter().hole(_hole(thread=HoleThread(size="M6"), fit="H7"))
    assert sent[-1][1].hole_class == "threaded" and sent[-1][1].colour == (128, 128, 255)


def test_a_wrong_convention_file_does_not_stop_the_hole(tmp_path: Path, sent: list[tuple[str, Any]]) -> None:
    (tmp_path / "space").mkdir()
    (tmp_path / "space" / FILENAME).write_text("agujeros: [\n", encoding="utf-8")
    adapter = FreeCADAdapter()
    adapter.conventions = Conventions(tmp_path / "home", tmp_path / "space")
    adapter.hole(_hole(fit="H7"))
    assert sent[-1][1].colour == (255, 128, 128)


def test_switched_off_the_hole_goes_without_colour(tmp_path: Path, sent: list[tuple[str, Any]]) -> None:
    adapter = FreeCADAdapter()
    adapter.conventions = Conventions(tmp_path / "home", tmp_path / "space")
    adapter.conventions.set_holes("proyecto", {"activa": False, "isometrico_en_color": False})
    adapter.hole(_hole(fit="H7"))
    assert sent[-1][1].colour is None and sent[-1][1].hole_class is None and sent[-1][1].fit == "H7"
    adapter.create_drawing(DrawingRequest(operation_id="d"))
    assert sent[-1][1].iso_colour is False


def test_editing_a_hole_sends_it_with_the_class_and_colour_of_the_convention(sent: list[tuple[str, Any]]) -> None:
    edit = FeatureEdit(feature=FeatureHandle(id="feature:1"), kind="hole", request=_hole(fit="H7"))
    FreeCADAdapter().edit_feature(EditFeatureRequest(operation_id="e", edits=(edit,)))
    request = sent[-1][1].edits[0].request
    assert sent[-1][0] == "edit_feature"
    assert (request.hole_class, request.colour, request.precision_grade) == ("precision", (255, 128, 128), 8)


def test_the_drawing_carries_the_convention(sent: list[tuple[str, Any]]) -> None:
    adapter = FreeCADAdapter()
    adapter.create_drawing(DrawingRequest(operation_id="d"))
    assert sent[-1][0] == "create_drawing" and sent[-1][1].iso_colour is True
