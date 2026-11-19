# ruff: noqa: E501
"""A page this cotaforge cannot read (no spec: made by hand or by something else; a spec of another version; a damaged spec) is never
thrown away by a ``create`` that does not say ``fresh``: the call is refused, says why, and the page is as it was. With ``fresh`` it is
rebuilt. Plain asserts run inside ``freecadcmd`` (see ``run.py``)."""

import json
from typing import Any

import drawing
import main
from check_drawing_live import build_part
from harness import call, expect_error, op


def _fc_doc() -> Any:
    assert main.WORKER.active is not None
    return main.WORKER.active.fc_doc


def _make_by_hand(name: str) -> Any:
    """A TechDraw page the way a person makes it: a page and its template, no cotaforge property."""
    fc_doc = _fc_doc()
    page = fc_doc.addObject("TechDraw::DrawPage", name)
    template = fc_doc.addObject("TechDraw::DrawSVGTemplate", f"{name}Template")
    template.Template = drawing.template_path("A3", None)
    page.Template = template
    fc_doc.recompute()
    return page


def _refused_and_untouched(sheet: str, expected: str) -> None:
    fc_doc = _fc_doc()
    page = fc_doc.getObject(sheet)
    assert page is not None
    before = (page.Name, len(page.Views), str(getattr(page, drawing.SPEC_PROP, "")))
    error = expect_error("invalid_request", "create_drawing", op("again", sheet=sheet, template="A3"))
    assert "cannot read it" in error.message and expected in error.message and "fresh=true" in error.message, error.message
    again = fc_doc.getObject(sheet)
    assert again is not None and (again.Name, len(again.Views), str(getattr(again, drawing.SPEC_PROP, ""))) == before, "the page changed"


def check_a_page_made_by_hand_is_not_thrown_away_by_a_create_without_fresh() -> None:
    build_part()
    _make_by_hand("Hand")
    _refused_and_untouched("Hand", "no cotaforge spec")
    made = call("create_drawing", op("fresh", sheet="Hand", template="A3", fresh=True))
    assert made["sheet"] == "Hand" and drawing.read_spec(drawing.find_page(_fc_doc(), "Hand")) is not None


def check_a_spec_of_another_version_or_a_damaged_one_is_not_thrown_away_either() -> None:
    build_part()
    call("create_drawing", op("d", template="A3", sheet="Drawing"))
    page = drawing.find_page(_fc_doc(), "Drawing")
    spec = drawing.read_spec(page)
    assert spec is not None
    page_spec: dict[str, Any] = {**spec, "v": 0}
    setattr(page, drawing.SPEC_PROP, json.dumps(page_spec))
    _refused_and_untouched("Drawing", "version 0")
    setattr(page, drawing.SPEC_PROP, "{not json")
    _refused_and_untouched("Drawing", "damaged")
    made = call("create_drawing", op("fresh", sheet="Drawing", template="A3", fresh=True))
    assert made["sheet"] == "Drawing" and drawing.read_spec(drawing.find_page(_fc_doc(), "Drawing")) is not None
