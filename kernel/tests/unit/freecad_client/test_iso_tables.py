"""The ISO 286 and ISO 2768 tables (``worker/iso286.py``, ``worker/iso2768.py``): the canonical values every engineer
knows, the internal consistency of the transcription (the DELTA column against the grades, the hole deviations against
the shaft ones) and the errors for a class or a size the tables do not cover."""

import sys
from pathlib import Path

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import iso286  # noqa: E402
import iso2768  # noqa: E402

# (designation, nominal size mm, upper um, lower um): the values every fits table prints
CANONICAL = [
    ("H7", 3, 10, 0),
    ("H7", 3.001, 12, 0),
    ("H7", 6, 12, 0),
    ("H7", 6.001, 15, 0),
    ("H7", 8, 15, 0),
    ("H7", 10, 15, 0),
    ("H7", 12, 18, 0),
    ("H7", 20, 21, 0),
    ("H7", 25, 21, 0),
    ("H7", 50, 25, 0),
    ("H7", 100, 35, 0),
    ("H7", 500, 63, 0),
    ("H6", 25, 13, 0),
    ("H8", 25, 33, 0),
    ("H9", 40, 62, 0),
    ("H11", 25, 130, 0),
    ("g6", 8, -5, -14),
    ("g6", 20, -7, -20),
    ("g6", 50, -9, -25),
    ("g6", 100, -12, -34),
    ("h6", 20, 0, -13),
    ("h7", 20, 0, -21),
    ("h9", 20, 0, -52),
    ("h11", 20, 0, -130),
    ("f7", 8, -13, -28),
    ("f7", 20, -20, -41),
    ("e8", 20, -40, -73),
    ("e9", 20, -40, -92),
    ("k6", 20, 15, 2),
    ("m6", 20, 21, 8),
    ("n6", 20, 28, 15),
    ("p6", 20, 35, 22),
    ("js6", 20, 6.5, -6.5),
    ("js7", 20, 10.5, -10.5),
    ("E9", 20, 92, 40),
    ("F8", 20, 53, 20),
    ("G7", 20, 28, 7),
    ("J7", 20, 12, -9),
    ("JS7", 20, 10.5, -10.5),
    ("K7", 20, 6, -15),
    ("M7", 20, 0, -21),
    ("N7", 20, -7, -28),
    ("P7", 20, -14, -35),
    ("N9", 20, 0, -52),
    ("N9", 3, -4, -29),
]


@pytest.mark.parametrize(("designation", "size", "upper", "lower"), CANONICAL)
def test_canonical_limit_deviations(designation: str, size: float, upper: float, lower: float) -> None:
    assert iso286.deviations(designation, size) == (upper, lower)


def test_delta_is_the_difference_of_two_neighbouring_grades() -> None:
    """The DELTA column is IT(n) - IT(n-1) for every size range above 3 mm: a typo in either table shows."""
    for grade, column in iso286.DELTA.items():
        for index, delta in enumerate(column):
            expected = 0 if index == 0 else iso286.IT[grade][index] - iso286.IT[grade - 1][index]
            assert delta == expected, (grade, iso286.RANGES[index])


def test_the_standard_tolerances_grow_with_the_grade_and_with_the_size() -> None:
    for grade in range(5, 12):
        row = iso286.IT[grade]
        assert list(row) == sorted(row)
        assert all(a < b for a, b in zip(iso286.IT[grade - 1], row, strict=True))


def test_a_hole_and_a_shaft_of_the_same_letter_mirror_each_other() -> None:
    for letter in "efg":
        for grade in (6, 7, 8):
            es, ei = iso286.deviations(f"{letter}{grade}", 40)
            upper, lower = iso286.deviations(f"{letter.upper()}{grade}", 40)
            assert (lower, upper) == (-es, -ei)  # E/F/G: the hole sits where the shaft does, flipped about the line


def test_the_zone_width_is_the_standard_tolerance_for_every_letter_and_grade() -> None:
    for letters in [*iso286.SHAFT_LETTERS, *[h for h in iso286.HOLE_LETTERS if h != "J"]]:
        for grade in iso286.GRADES:
            for size in (2, 5, 9, 15, 25, 45, 70, 100, 150, 200, 300, 350, 450):
                upper, lower = iso286.deviations(f"{letters}{grade}", size)
                assert upper - lower == iso286.tolerance_um(grade, size), (letters, grade, size)


def test_fits_are_checked() -> None:
    assert iso286.split_fit("H7/g6") == ["H7", "g6"]
    for text, why in (
        ("x7", "not in the tables"),
        ("H4", "IT5 to IT11"),
        ("H12", "IT5 to IT11"),
        ("7H", "not a tolerance class"),
        ("H7/g6/k5", "not a class"),
        ("Js7", "do not mix"),
    ):
        with pytest.raises(iso286.FitError, match=why):
            iso286.split_fit(text)
    with pytest.raises(iso286.FitError, match="J9"):
        iso286.deviations("J9", 20)
    with pytest.raises(iso286.FitError, match="up to 500"):
        iso286.deviations("H7", 500.5)
    with pytest.raises(iso286.FitError, match="above 0"):
        iso286.deviations("H7", 0)


def test_the_tables_say_where_they_come_from_and_carry_the_note() -> None:
    assert iso286.EDITION.startswith("ISO 286-1")
    assert iso286.SHOW_TRANSCRIPTION_NOTE is True
    assert iso286.TRANSCRIPTION_NOTE["es"] == "Tablas ISO 286 transcritas; verificar contra la norma"


# --- ISO 2768 ---------------------------------------------------------------------------------------------------------


def test_linear_general_tolerances() -> None:
    assert iso2768.linear("m", 5) == 0.1
    assert iso2768.linear("m", 20) == 0.2
    assert iso2768.linear("m", 100) == 0.3
    assert iso2768.linear("m", 400) == 0.5
    assert iso2768.linear("f", 20) == 0.1
    assert iso2768.linear("c", 100) == 0.8
    assert iso2768.linear("v", 20) == 1.0
    assert iso2768.linear("m", 0.4) is None  # below 0.5 mm the standard gives none
    assert iso2768.linear("v", 2) is None
    assert iso2768.linear("f", 3000) is None
    assert iso2768.linear("m", 5000) is None


def test_radius_angle_and_geometric_general_tolerances() -> None:
    assert iso2768.radius("m", 2) == 0.2 and iso2768.radius("m", 5) == 0.5 and iso2768.radius("c", 10) == 2.0
    assert iso2768.angle_minutes("m", 8) == 60 and iso2768.angle_minutes("m", 30) == 30
    assert iso2768.angle_minutes("c", 100) == 30 and iso2768.angle_minutes("v", 1000) == 20
    assert [iso2768.angle_text(m) for m in (60, 30, 5, 90)] == ["±1°", "±0°30'", "±0°5'", "±1°30'"]
    assert iso2768.flatness("K", 50) == 0.2 and iso2768.flatness("H", 10) == 0.02 and iso2768.flatness("L", 500) == 1.2
    assert iso2768.perpendicularity("H", 150) == 0.3 and iso2768.perpendicularity("L", 50) == 0.6
    assert iso2768.symmetry("K", 500) == 0.8 and iso2768.symmetry("H", 50) == 0.5
    assert iso2768.runout("K") == 0.2


def test_the_designation_of_the_title_block_is_read() -> None:
    assert iso2768.parse("ISO 2768-mK") == ("m", "K")
    assert iso2768.parse("ISO 2768 - f") == ("f", None)
    assert iso2768.parse("2768-cL") == ("c", "L")
    assert iso2768.parse("±0,1 general") is None
    assert iso2768.parse("") is None
