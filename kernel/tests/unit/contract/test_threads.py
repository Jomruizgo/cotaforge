"""The thread table the IR/executor share."""

import pytest

from cotaforge.contract.threads import (
    FINE_THREADS,
    THREADS,
    clearance_diameter_mm,
    fine_thread_sizes,
    tap_drill_by_rule,
    tap_drill_mm,
    thread,
    thread_sizes,
)

# What FreeCAD (both 1.0.2 and 1.1.3) uses for a cosmetic tapped hole: measured, not read from a table.
FREECAD_TAP_DRILLS = {
    "M3": 2.5,
    "M4": 3.3,
    "M5": 4.2,
    "M6": 5.0,
    "M8": 6.8,
    "M10": 8.5,
    "M12": 10.2,
    "M16": 14.0,
    "M20": 17.5,
}
ISO273_MEDIUM = {"M6": 6.6, "M8": 9.0, "M10": 11.0, "M12": 13.5, "M16": 17.5, "M20": 22.0, "M24": 26.0}
RULE_EXCEPTIONS = {"M8": 6.8, "M12": 10.2}  # the rule gives 6.75 and 10.25: the standard drill beside them


def test_table_is_ordered_by_major_diameter_and_covers_the_usual_sizes() -> None:
    sizes = thread_sizes()
    majors = [THREADS[s].major_mm for s in sizes]
    assert majors == sorted(majors)
    assert {"M2", "M3", "M6", "M8", "M10", "M12", "M16", "M20", "M24", "M36", "M45", "M68"} <= set(sizes)
    assert thread("M7") is None and tap_drill_mm("M7") is None and clearance_diameter_mm("M7") is None


@pytest.mark.parametrize(("size", "drill"), sorted(FREECAD_TAP_DRILLS.items()))
def test_tap_drills_match_what_freecad_drills(size: str, drill: float) -> None:
    assert tap_drill_mm(size) == drill


@pytest.mark.parametrize(("size", "clearance"), sorted(ISO273_MEDIUM.items()))
def test_clearance_is_the_iso_273_medium_series(size: str, clearance: float) -> None:
    assert clearance_diameter_mm(size) == clearance


def test_every_row_is_physically_consistent() -> None:
    for spec in (*THREADS.values(), *FINE_THREADS.values()):
        assert spec.pitch_mm < spec.major_mm / 2 + 1e-9
        assert spec.major_mm - 1.3 * spec.pitch_mm <= spec.tap_drill_mm <= spec.major_mm - spec.pitch_mm + 0.1
        if spec.clearance_medium_mm is not None:
            assert spec.major_mm < spec.clearance_medium_mm < spec.major_mm * 1.25


def test_every_tap_drill_is_the_rule_major_minus_pitch_but_the_two_standard_drills() -> None:
    for spec in (*THREADS.values(), *FINE_THREADS.values()):
        expected = RULE_EXCEPTIONS.get(spec.designation, tap_drill_by_rule(spec.major_mm, spec.pitch_mm))
        assert spec.tap_drill_mm == expected, spec.designation
    assert tap_drill_by_rule(45, 1.5) == 43.5


def test_coarse_goes_up_to_m68_and_the_clearance_is_known_only_up_to_m36() -> None:
    assert thread("M68") is not None and thread("M68").pitch_mm == 6.0  # type: ignore[union-attr]
    assert thread("M45").tap_drill_mm == 40.5  # type: ignore[union-attr]
    assert clearance_diameter_mm("M36") == 39.0
    assert clearance_diameter_mm("M45") is None and clearance_diameter_mm("M45x1.5") is None


def test_a_fine_thread_is_found_by_its_designation_in_any_spelling() -> None:
    spec = thread("M45x1.5")
    assert spec is not None and spec.fine and spec.major_mm == 45 and spec.pitch_mm == 1.5
    assert spec.tap_drill_mm == 43.5 and spec.designation == "M45x1.5"
    for spelling in ("M45x1,5", " M45x1.5 ", "M45×1.5", "M45X1,5"):
        assert thread(spelling) == spec, spelling
    assert tap_drill_mm("M8x1") == 7.0 and thread("M8x1.0") == thread("M8x1")


def test_a_coarse_thread_spelled_with_its_pitch_is_the_coarse_row() -> None:
    assert thread("M12x1.75") is thread("M12") and not thread("M12").fine  # type: ignore[union-attr]
    assert thread("M45x4.5") is thread("M45")


@pytest.mark.parametrize("size", ["M45x1.7", "M45x", "M45x1.5x2", "M7x1", "45x1.5", "", "M", "Tr20x4", "G1/2", "M1000"])
def test_what_is_not_in_the_table_is_none(size: str) -> None:
    assert thread(size) is None


def test_fine_sizes_are_by_ascending_diameter_then_pitch_and_never_repeat() -> None:
    sizes = fine_thread_sizes()
    assert len(sizes) == len(set(sizes)) == len(FINE_THREADS)
    keys = [(FINE_THREADS[s].major_mm, FINE_THREADS[s].pitch_mm) for s in sizes]
    assert keys == sorted(keys)
    assert {"M8x1", "M12x1.5", "M20x1.5", "M42x1.5", "M45x1.5", "M100x6"} <= set(sizes)


def test_a_fine_pitch_is_finer_than_the_coarse_one_of_the_same_diameter() -> None:
    for spec in FINE_THREADS.values():
        coarse = THREADS.get(f"M{spec.major_mm:g}")
        if coarse is not None:
            assert spec.pitch_mm < coarse.pitch_mm, spec.designation
