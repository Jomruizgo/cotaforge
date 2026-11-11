"""The length unit a STEP or IGES file declares: read from the text, ``None`` when the file does not say."""

from pathlib import Path

import pytest

from cotaforge.adapters.freecad.fileunits import declared_length_unit

STEP_HEAD = (
    "ISO-10303-21;\nHEADER;\nFILE_NAME('x','2026',(''),(''),'','','');\nENDSEC;\n"
    "DATA;\n#1 = CARTESIAN_POINT('',(0.,0.,0.));\n"
)


def _step(tmp_path: Path, units: str, suffix: str = ".step") -> Path:
    path = tmp_path / f"part{suffix}"
    path.write_text(STEP_HEAD + units + "ENDSEC;\nEND-ISO-10303-21;\n", encoding="latin-1")
    return path


@pytest.mark.parametrize(
    ("units", "expected"),
    [
        ("#9 = ( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) );\n", "mm"),
        ("#9=(LENGTH_UNIT()NAMED_UNIT(*)SI_UNIT($,.METRE.));\n", "m"),
        ("#9 = ( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.CENTI.,.METRE.) );\n", "cm"),
        # FreeCAD writes the statement of an inch file over two lines
        (
            "#219 = ( CONVERSION_BASED_UNIT('INCH',#221) LENGTH_UNIT() NAMED_UNIT(\n#220) );\n"
            "#222 = ( LENGTH_UNIT() NAMED_UNIT(*) SI_UNIT(.MILLI.,.METRE.) );\n",
            "inch",
        ),
        ("#9 = ( NAMED_UNIT(*) PLANE_ANGLE_UNIT() SI_UNIT($,.RADIAN.) );\n", None),
    ],
)
def test_a_step_file_declares_its_length_unit(tmp_path: Path, units: str, expected: str | None) -> None:
    assert declared_length_unit(_step(tmp_path, units)) == expected
    assert declared_length_unit(_step(tmp_path, units, ".stp")) == expected


def _iges(tmp_path: Path, global_lines: list[str], suffix: str = ".iges") -> Path:
    rows = [f"{'':72}S{1:07d}"]
    rows += [f"{line:<72}G{i:07d}" for i, line in enumerate(global_lines, start=1)]
    rows.append(f"{'':72}T{1:07d}")
    path = tmp_path / f"part{suffix}"
    path.write_text("\n".join(rows) + "\n", encoding="latin-1")
    return path


# the global sections open cascade wrote for a millimetre and an inch file
MM = [
    ",,31HOpen CASCADE IGES processor 7.8,13HFilename.iges,",
    "16HOpen CASCADE 7.8,31HOpen CASCADE IGES processor 7.8,32,308,15,308,15,",
    ",1.,2,2HMM,1,0.01,15H20261024.131150,1E-07,30.,,,11,0,",
    "15H20261024.131150,;",
]
INCH = [
    ",,31HOpen CASCADE IGES processor 7.8,13HFilename.iges,",
    "16HOpen CASCADE 7.8,31HOpen CASCADE IGES processor 7.8,32,308,15,308,15,",
    ",1.,1,4HINCH,1,0.000394,15H20261024.131231,3.93701E-09,119.685039,,,11,",
    "0,15H20261024.131231,;",
]


def test_an_iges_file_declares_its_length_unit(tmp_path: Path) -> None:
    assert declared_length_unit(_iges(tmp_path, MM)) == "mm"
    assert declared_length_unit(_iges(tmp_path, INCH, ".igs")) == "inch"


def test_an_iges_unit_given_by_name_is_read_when_the_flag_says_so(tmp_path: Path) -> None:
    named = [line.replace(",1.,2,2HMM,", ",1.,3,2HFT,") for line in MM]
    assert declared_length_unit(_iges(tmp_path, named)) == "foot"


def test_a_file_that_does_not_say_or_cannot_be_read_has_no_unit(tmp_path: Path) -> None:
    assert declared_length_unit(tmp_path / "missing.step") is None
    empty = tmp_path / "empty.iges"
    empty.write_text("", encoding="latin-1")
    assert declared_length_unit(empty) is None
    other = tmp_path / "part.x_t"
    other.write_text("**ABCDEFGHIJKLMNOPQRSTUVWXYZ", encoding="latin-1")
    assert declared_length_unit(other) is None
