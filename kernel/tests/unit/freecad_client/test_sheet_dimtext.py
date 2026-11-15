# ruff: noqa: E501
"""What the dimensions of a sheet print (``sheet_dimtext.py``): the deviations of a fit in mm with the sheet's decimal sign, a
measured value that is the model's, and the review that names the dimension that prints otherwise. No FreeCAD: the objects
are plain stand-ins with TechDraw's property names."""

import sys
from pathlib import Path
from typing import Any

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_dimtext as dimtext  # noqa: E402
import sheet_units as units  # noqa: E402

from .test_sheet_tolerances import _distance, draw, make_context  # noqa: E402


def _row(**over: Any) -> dimtext.Printed:
    base: dict[str, Any] = {
        "item": "d1",
        "label": "d1 d1",
        "kind": "distance",
        "arbitrary": False,
        "text": "6,00 mm",
        "raw": 6.0,
        "expected": 6.0,
        "own_tolerance": False,
        "arbitrary_tolerances": False,
        "over": 0.0,
        "under": 0.0,
        "over_text": "+0.00",
        "under_text": "+0.00",
    }
    return dimtext.Printed(**{**base, **over})


def _native(ctx: Any) -> units.NativeDim:
    (dim,) = [n for n in ctx.extras["natives"]["d1"] if isinstance(n, units.NativeDim)]
    return dim


# --- the item gives TechDraw the deviations in mm ----------------------------------------------------------------------


def test_a_fit_on_a_linear_dimension_asks_for_the_deviations_in_mm_with_the_sheets_decimal_sign() -> None:
    ctx = make_context()
    (r,) = draw(ctx, _distance(fit="h9"))
    assert r.status == "ok", r.detail
    dim = _native(ctx)
    assert dim.tol == (0.0, -0.052)
    assert dim.tol_text == ("0", "-0,052")  # not TechDraw's own: 52 µm print as -52
    assert dim.suffix == "h9"  # the class stays next to the measured value


def test_an_english_sheet_writes_the_deviations_with_a_point() -> None:
    ctx = make_context(language="en")
    draw(ctx, _distance(fit="h9"))
    assert _native(ctx).tol_text == ("0", "-0.052")


def test_a_tolerance_without_a_class_has_no_suffix_and_a_symmetric_one_no_stack() -> None:
    ctx = make_context()
    draw(ctx, _distance(tol=[0.1, -0.05]))
    dim = _native(ctx)
    assert dim.tol_text == ("+0,1", "-0,05") and dim.suffix == ""
    ctx = make_context()
    draw(ctx, _distance(tol=0.1))
    assert _native(ctx).tol_text is None  # printed inline by the sheet as ``±0,1``: no TechDraw deviation


def test_the_deviation_texts_are_part_of_what_the_sheet_asks_for() -> None:
    def signature(**over: Any) -> str:
        unit = units.Unit("d1", "d1 dim", units.KIND_ITEM, (), "dim", None, (_dim(**over),))
        return units.signature(unit)

    assert signature(tol=(0.0, -0.052), tol_text=("0", "-0,052")) != signature(tol=(0.0, -0.052))
    assert signature(tol=(0.0, -0.052), tol_text=("0", "-0,052"), suffix="h9") != signature(
        tol=(0.0, -0.052), tol_text=("0", "-0,052")
    )


def _dim(**over: Any) -> units.NativeDim:
    base: dict[str, Any] = {
        "item": "d1",
        "name": "d1",
        "kind": "distance",
        "view": "front",
        "refs": (units.Ref2("point", (0.0, 0.0)), units.Ref2("point", (6.0, 0.0))),
        "label": (3.0, 10.0),
        "expected": 6.0,
        "orient": "h",
    }
    return units.NativeDim(**{**base, **over})


# --- the review reads what the page prints -------------------------------------------------------------------------------


def test_deviations_left_to_techdraws_unit_are_a_finding() -> None:
    bad = _row(own_tolerance=True, over=-0.012, under=-0.042, arbitrary_tolerances=False)
    assert "µm" in str(dimtext.unit_problems(bad))
    good = _row(
        own_tolerance=True,
        over=-0.012,
        under=-0.042,
        arbitrary_tolerances=True,
        over_text="-0,012",
        under_text="-0,042",
    )
    assert dimtext.unit_problems(good) is None
    micro = _row(
        own_tolerance=True, over=-0.012, under=-0.042, arbitrary_tolerances=True, over_text="-12", under_text="-42"
    )
    assert "prints -12 / -42" in str(dimtext.unit_problems(micro))
    assert dimtext.unit_problems(_row()) is None  # a dimension with no deviation


def test_a_zero_deviation_is_a_plain_zero() -> None:
    row = _row(
        own_tolerance=True, over=0.0, under=-0.052, arbitrary_tolerances=True, over_text="0", under_text="-0,052"
    )
    assert dimtext.unit_problems(row) is None


def test_the_other_decimal_sign_is_a_finding() -> None:
    assert dimtext.sign_problem(_row(text="4.01 mm"), comma=True) == "4.01 mm"
    assert dimtext.sign_problem(_row(text="4,01 mm"), comma=True) is None
    assert dimtext.sign_problem(_row(text="4,01 mm"), comma=False) == "4,01 mm"
    assert (
        dimtext.sign_problem(_row(arbitrary=True, text="Ø25 k6 (+0.015/+0.002)"), comma=True) is None
    )  # the sheet's own text
    bad = _row(arbitrary_tolerances=True, over_text="-0.012", under_text="-0,042")
    assert dimtext.sign_problem(bad, comma=True) == "-0.012"


def test_a_measured_value_that_prints_otherwise_than_the_model_is_a_finding() -> None:
    assert dimtext.noise_problem(_row(raw=4.0105, expected=4.0)) == "TechDraw prints 4.01 and the model says 4.0000"
    assert dimtext.noise_problem(_row(raw=4.0000001, expected=4.0)) is None
    assert dimtext.noise_problem(_row(raw=18.5004, expected=18.5)) is None  # both print 18,5
    assert dimtext.noise_problem(_row(raw=-4.0, expected=4.0)) is None  # TechDraw may measure the other way round
    assert dimtext.noise_problem(_row(arbitrary=True, raw=4.0105, expected=4.0)) is None  # the text is the sheet's own
    assert dimtext.noise_problem(_row(kind="angle", raw=45.2, expected=45.0)) is None


def test_a_value_a_hair_off_a_clean_number_at_the_precision_of_the_class_is_a_finding() -> None:
    assert dimtext.warranted_decimals("m", 4.0) == 1  # ±0,1: a tenth
    assert dimtext.warranted_decimals("f", 4.0) == 2  # ±0,05
    assert dimtext.warranted_decimals("m", 200.0) == 1  # ±0,3
    assert "4.01 where ISO 2768-m warrants 1 decimal(s) (4)" == dimtext.unclean_problem(_row(expected=4.01), "m")
    assert dimtext.unclean_problem(_row(expected=4.01), "f") is None  # a hundredth is warranted
    assert dimtext.unclean_problem(_row(expected=4.0), "m") is None
    assert dimtext.unclean_problem(_row(expected=12.7), "m") is None  # one decimal: clean
    assert dimtext.unclean_problem(_row(expected=12.25), "m") is None  # a quarter: a decision, not noise
    assert dimtext.unclean_problem(_row(expected=4.01), None) is None  # no general tolerance named
    assert dimtext.unclean_problem(_row(expected=4.01, own_tolerance=True), "m") is None  # its own tolerance
    assert dimtext.unclean_problem(_row(expected=4.01, kind="radius"), "m") is None
    assert dimtext.unclean_problem(_row(expected=4.01, arbitrary=True, text="Ø25 k6 (+0,015/+0,002)"), "m") is None
    assert dimtext.unclean_problem(_row(expected=4.01, arbitrary=True, text="Ø4,01"), "m") is not None


def test_the_findings_name_the_item_and_are_capped() -> None:
    ctx = make_context(general="ISO 2768-mK")
    rows = [_row(item=f"d{i}", raw=4.0105, expected=4.0) for i in range(5)]
    found = dimtext.findings(rows, ctx)
    assert [f["code"] for f in found] == ["dimension_value_noise"] * dimtext.MAX_ROWS
    assert found[0]["items"] == ["d0"] and "d0" in found[0]["message"] and found[0]["severity"] == "warning"
    unclean = dimtext.findings([_row(raw=4.01, expected=4.01)], ctx)
    assert [f["code"] for f in unclean] == ["dimension_value_unclean"]
    assert dimtext.findings([_row()], ctx) == []


def test_these_findings_hold_the_export() -> None:
    import sheet_checks  # noqa: PLC0415

    assert not set(dimtext.CODES) & sheet_checks.NON_BLOCKING
