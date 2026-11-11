"""The convention of holes: classification, defaults, layers (user file < project file), wrong files, writing."""

from pathlib import Path

import pytest

from cotaforge.contract import HoleRequest
from cotaforge.contract.enums import EndCondition, HoleStandard
from cotaforge.contract.handles import FaceHandle
from cotaforge.conventions import (
    DEFAULT_COLOURS,
    FILENAME,
    PLAIN,
    PRECISION,
    THREADED,
    ConventionError,
    Conventions,
    HoleConvention,
    classify,
    fit_grade,
    parse_colour,
)
from cotaforge.conventions.requests import colour_hole, hole_convention, iso_colour_drawing


@pytest.mark.parametrize(
    ("fit", "grade"),
    [
        ("H7", 7),
        ("H6", 6),
        ("H8", 8),
        ("H11", 11),
        ("H7/g6", 7),
        ("JS7", 7),
        (" H7 ", 7),
        ("g6", None),
        (None, None),
        ("", None),
        ("H", None),
        ("7", None),
        ("g6/H7", 7),  # shaft first: the hole is the side in capitals
        ("h7/H8", 8),
        ("g6/h7", None),
    ],
)
def test_fit_grade_reads_the_hole_class_only(fit: str | None, grade: int | None) -> None:
    assert fit_grade(fit) == grade


@pytest.mark.parametrize(
    ("threaded", "fit", "expected"),
    [
        (False, "H6", PRECISION),
        (False, "H7", PRECISION),
        (False, "H8", PRECISION),
        (False, "H9", PLAIN),  # IT9 and coarser are plain
        (False, "H11", PLAIN),
        (False, None, PLAIN),
        (False, "g6", PLAIN),  # a shaft class says nothing about a hole
        (True, None, THREADED),
        (True, "H7", THREADED),  # a thread makes it threaded, whatever else it has
    ],
)
def test_classification(threaded: bool, fit: str | None, expected: str) -> None:
    assert classify(threaded=threaded, fit=fit) == expected


def test_the_grade_limit_is_part_of_the_convention() -> None:
    assert classify(threaded=False, fit="H9", max_precision_grade=9) == PRECISION
    assert classify(threaded=False, fit="H7", max_precision_grade=6) == PLAIN


def test_the_defaults_are_the_owners_without_any_file(tmp_path: Path) -> None:
    resolved = Conventions(tmp_path / "home", tmp_path / "space").resolve()
    assert resolved.problems == () and resolved.sources == ()
    holes = resolved.holes
    assert holes.active and holes.iso_colour and holes.max_precision_grade == 8
    assert holes.colours == {PRECISION: (255, 128, 128), PLAIN: (0, 192, 0), THREADED: (128, 128, 255)}
    assert holes.colours == DEFAULT_COLOURS
    assert holes.colour_for(threaded=False, fit="H7") == (PRECISION, (255, 128, 128))
    assert holes.colour_for(threaded=False, fit=None) == (PLAIN, (0, 192, 0))
    assert holes.colour_for(threaded=True, fit=None) == (THREADED, (128, 128, 255))


def test_a_convention_without_workspace_or_home_still_has_the_defaults() -> None:
    assert Conventions().resolve().holes == HoleConvention()
    assert hole_convention(None) == HoleConvention()


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def test_user_file_overrides_defaults_and_project_file_wins(tmp_path: Path) -> None:
    home, space = tmp_path / "home", tmp_path / "space"
    _write(home / FILENAME, "agujeros:\n  colores:\n    liso: [10, 20, 30]\n    preciso: '#ff0000'\n")
    _write(space / FILENAME, "agujeros:\n  colores:\n    liso: [1, 2, 3]\n  grado_it_preciso: 7\n")
    resolved = Conventions(home, space).resolve()
    assert resolved.problems == ()
    assert resolved.holes.colours[PLAIN] == (1, 2, 3)  # the project wins
    assert resolved.holes.colours[PRECISION] == (255, 0, 0)  # the user's, untouched by the project
    assert resolved.holes.colours[THREADED] == (128, 128, 255)  # the default
    assert resolved.holes.max_precision_grade == 7
    assert resolved.sources == (str(home / FILENAME), str(space / FILENAME))


def test_it_can_be_switched_off(tmp_path: Path) -> None:
    _write(tmp_path / "space" / FILENAME, "agujeros:\n  activa: false\n  isometrico_en_color: false\n")
    holes = Conventions(tmp_path / "home", tmp_path / "space").holes()
    assert holes.active is False and holes.iso_colour is False
    assert holes.colour_for(threaded=False, fit="H7") is None


@pytest.mark.parametrize(
    "text",
    ["agujeros: [1, 2\n", "just a string\n", "- a\n- b\n", "agujeros: 5\n", "\x00\x01"],
)
def test_a_wrong_file_never_stops_the_work(tmp_path: Path, text: str) -> None:
    _write(tmp_path / "space" / FILENAME, text)
    resolved = Conventions(tmp_path / "home", tmp_path / "space").resolve()
    assert resolved.holes == HoleConvention()  # the default applies
    assert resolved.problems  # and the agent is told


def test_one_wrong_value_loses_only_itself(tmp_path: Path) -> None:
    _write(
        tmp_path / "space" / FILENAME,
        "agujeros:\n  colores:\n    liso: [300, 0, 0]\n    preciso: [1, 2, 3]\n    otro: [1, 1, 1]\n"
        "  grado_it_preciso: 'x'\n  activa: quizas\n  raro: 1\n",
    )
    resolved = Conventions(tmp_path / "home", tmp_path / "space").resolve()
    assert resolved.holes.colours[PLAIN] == (0, 192, 0)
    assert resolved.holes.colours[PRECISION] == (1, 2, 3)
    assert resolved.holes.max_precision_grade == 8 and resolved.holes.active
    assert len(resolved.problems) == 5


def test_a_wrong_user_file_does_not_hide_a_good_project_file(tmp_path: Path) -> None:
    _write(tmp_path / "home" / FILENAME, "agujeros: [\n")
    _write(tmp_path / "space" / FILENAME, "agujeros:\n  colores:\n    liso: [9, 9, 9]\n")
    resolved = Conventions(tmp_path / "home", tmp_path / "space").resolve()
    assert resolved.holes.colours[PLAIN] == (9, 9, 9)
    assert len(resolved.problems) == 1 and "home" in resolved.problems[0]


@pytest.mark.parametrize(
    ("value", "rgb"),
    [([1, 2, 3], (1, 2, 3)), ("#80ff00", (128, 255, 0)), ("0000ff", (0, 0, 255)), ((9, 8, 7), (9, 8, 7))],
)
def test_parse_colour(value: object, rgb: tuple[int, int, int]) -> None:
    assert parse_colour(value) == rgb


@pytest.mark.parametrize(
    "value", [[1, 2], [1, 2, 256], [1, 2, -1], [1.5, 2, 3], "red", "#12", None, True, [True, 1, 1]]
)
def test_parse_colour_refuses(value: object) -> None:
    with pytest.raises(ConventionError):
        parse_colour(value)


def test_an_unquoted_hex_colour_in_yaml_is_an_empty_value_and_the_error_says_to_quote_it() -> None:
    """``colour: #ff8080`` is a comment in YAML: the value arrives as None."""
    with pytest.raises(ConventionError, match="in quotes"):
        parse_colour(None)


def test_set_holes_writes_a_readable_file_and_merges(tmp_path: Path) -> None:
    conv = Conventions(tmp_path / "home", tmp_path / "space")
    path = conv.set_holes("proyecto", {"colores": {"liso": "#0000ff"}})
    assert path == tmp_path / "space" / FILENAME
    conv.set_holes("proyecto", {"colores": {"preciso": [9, 9, 9]}, "grado_it_preciso": 7})
    text = path.read_text(encoding="utf-8")
    assert "agujeros:" in text and "liso" in text and "preciso" in text
    holes = conv.holes()
    assert holes.colours[PLAIN] == (0, 0, 255) and holes.colours[PRECISION] == (9, 9, 9)
    assert holes.max_precision_grade == 7
    # the user scope is separate
    user = conv.set_holes("usuario", {"activa": False})
    assert user == tmp_path / "home" / FILENAME
    assert conv.holes().active is False


def test_set_holes_keeps_other_sections_and_reset_removes_ours(tmp_path: Path) -> None:
    conv = Conventions(tmp_path / "home", tmp_path / "space")
    _write(tmp_path / "space" / FILENAME, "otra_cosa:\n  x: 1\n")
    conv.set_holes("proyecto", {"activa": False})
    assert "otra_cosa" in (tmp_path / "space" / FILENAME).read_text(encoding="utf-8")
    conv.set_holes("proyecto", None, reset=True)
    text = (tmp_path / "space" / FILENAME).read_text(encoding="utf-8")
    assert "otra_cosa" in text and "agujeros" not in text
    assert conv.holes().active is True


@pytest.mark.parametrize(
    "changes",
    [{"colores": {"liso": [1, 2]}}, {"grado_it_preciso": 99}, {"activa": "si"}, {"nada": 1}, {"colores": 3}],
)
def test_set_holes_refuses_a_wrong_change_and_writes_nothing(tmp_path: Path, changes: dict) -> None:
    conv = Conventions(tmp_path / "home", tmp_path / "space")
    with pytest.raises(ConventionError):
        conv.set_holes("proyecto", changes)
    assert not (tmp_path / "space" / FILENAME).exists()


def test_set_holes_refuses_to_overwrite_a_file_it_cannot_read(tmp_path: Path) -> None:
    _write(tmp_path / "space" / FILENAME, "agujeros: [\n")
    with pytest.raises(ConventionError, match="cannot be read"):
        Conventions(tmp_path / "home", tmp_path / "space").set_holes("proyecto", {"activa": False})
    assert (tmp_path / "space" / FILENAME).read_text(encoding="utf-8") == "agujeros: [\n"


def test_unknown_scope_is_refused(tmp_path: Path) -> None:
    with pytest.raises(ConventionError, match="scope"):
        Conventions(tmp_path, tmp_path).set_holes("nube", {"activa": False})


def test_describe_uses_the_files_words() -> None:
    assert HoleConvention().describe() == {
        "activa": True,
        "colores": {"preciso": [255, 128, 128], "liso": [0, 192, 0], "roscado": [128, 128, 255]},
        "grado_it_preciso": 8,
        "isometrico_en_color": True,
    }


def _hole(**extra: object) -> HoleRequest:
    return HoleRequest(
        operation_id="h1",
        face=FaceHandle(id="face:1"),
        position=(0.0, 0.0),
        standard=HoleStandard.simple,
        diameter_mm=6.0,
        end_condition=EndCondition.through_all,
        **extra,
    )


def test_the_request_is_coloured_by_what_it_declares() -> None:
    conv = HoleConvention()
    assert colour_hole(_hole(fit="H7"), conv).colour == (255, 128, 128)
    assert colour_hole(_hole(fit="H7"), conv).hole_class == PRECISION
    assert colour_hole(_hole(), conv).hole_class == PLAIN
    assert colour_hole(_hole(fit="H9"), conv).colour == (0, 192, 0)
    off = HoleConvention(active=False)
    assert colour_hole(_hole(fit="H7"), off).colour is None
    assert colour_hole(_hole(fit="H7"), off).hole_class is None
    # the fit stays the engineer's
    assert colour_hole(_hole(fit="H7"), conv).fit == "H7"


def test_an_old_request_without_fit_still_loads() -> None:
    old = _hole().model_dump(mode="json")
    for key in ("fit", "colour", "hole_class"):
        old.pop(key)
    assert HoleRequest.model_validate(old).fit is None


def test_iso_colour_follows_the_convention() -> None:
    from cotaforge.contract import DrawingRequest

    request = DrawingRequest(operation_id="d1")
    assert iso_colour_drawing(request, HoleConvention()).iso_colour is True
    assert iso_colour_drawing(request, HoleConvention(iso_colour=False)).iso_colour is False
    assert request.iso_colour is False  # an old request does not ask for it
