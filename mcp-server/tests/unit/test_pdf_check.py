# ruff: noqa: E501
"""``pdf_check``: a drawing PDF that a viewer would not draw to the end is told from a sound one. The files are written by
hand (``tests/pdf_support.py``), the bad ones copy what Qt's PDF writer printed for a native object with an invalid colour
(``/CSp cs scn`` with no components: the file the engineer's viewer stopped on)."""

from pathlib import Path

import pytest

from cotaforge_mcp import pdf_check
from tests.pdf_support import SOUND_CONTENT, minimal_pdf


def write(tmp_path: Path, content: bytes = SOUND_CONTENT, **kwargs: object) -> Path:
    path = tmp_path / "sheet.pdf"
    path.write_bytes(minimal_pdf(content=content, **kwargs))  # type: ignore[arg-type]
    return path


def problems(tmp_path: Path, content: bytes, **kwargs: object) -> str:
    report = pdf_check.check_pdf(write(tmp_path, content), pages=1, **kwargs)  # type: ignore[arg-type]
    return report.summary()


def test_a_sound_one_page_pdf_of_the_sheets_size_passes(tmp_path: Path) -> None:
    report = pdf_check.check_pdf(write(tmp_path), pages=1, page_mm=(297.0, 210.0))
    assert report.ok, report.summary()
    assert report.pages == 1 and report.page_mm == pytest.approx((297.0, 210.0), abs=0.5)
    assert report.operators > 5


def test_the_colour_operators_are_read_against_their_colour_space(tmp_path: Path) -> None:
    content = (
        b"/CSp cs /CSp CS 0.1 0.2 0.3 scn 0 0 0 SCN 1 w 10 10 m 20 20 l S\n"  # a named RGB space: three numbers
        b"0.5 g 0.5 G 0 0 0 1 k 1 0 0 rg 10 10 m 50 10 l S\n"  # the device spaces set their own
    )
    assert problems(tmp_path, content) == ""


@pytest.mark.parametrize(
    ("content", "expect"),
    [
        (b"/CSp cs scn 10 10 m 20 20 l S\n", "scn has 0 number(s), the colour space /CSp gives it 3"),
        (b"/CSp CS 0 0 SCN 10 10 m 20 20 l S\n", "SCN has 2 number(s)"),
        (b"/CSp cs 0 0 0 0 scn 10 10 m 20 20 l S\n", "scn has 4 number(s)"),
        (b"/CSp CS nan 0 0 SCN 10 10 m 20 20 l S\n", "unknown operator or word 'nan'"),
        (b"/CSp CS 1e-05 0 0 SCN 10 10 m 20 20 l S\n", "unknown operator or word '1e-05'"),
        (b"10 10 m 20 l S\n", "l has 1 operand(s)"),
        (b"q 10 10 m 20 20 l S\n", "1 q operator(s) never closed"),
        (b"Q 10 10 m 20 20 l S\n", "Q without a matching q"),
        (b"10 10 m 20 20 l S 5\n", "operands left at the end"),
        (b"10 10 m /Foo 20 l S\n", "l operand"),
        (b"BT ET ET 10 10 m 20 20 l S\n", "ET without BT"),
    ],
)
def test_a_content_stream_with_a_malformed_operator_fails_and_says_where(
    tmp_path: Path, content: bytes, expect: str
) -> None:
    found = problems(tmp_path, content)
    assert expect in found, found
    assert "near:" in found or "never closed" in found


def test_the_pattern_colour_space_takes_a_name_not_numbers(tmp_path: Path) -> None:
    spaces = "/CSp /DeviceRGB /PCSp [/Pattern]"
    good = b"/PCSp cs /P1 scn 10 10 m 20 20 l S\n"
    bad = b"/PCSp cs 1 0 0 scn 10 10 m 20 20 l S\n"
    assert pdf_check.check_pdf(write(tmp_path, good, colour_spaces=spaces), pages=1).ok
    assert "pattern" in pdf_check.check_pdf(write(tmp_path, bad, colour_spaces=spaces), pages=1).summary()


def test_text_and_arrays_and_strings_are_operands_not_noise(tmp_path: Path) -> None:
    content = (
        b"BT /F1 10 Tf 1 0 0 1 20 20 Tm (a (nested) \\) text) Tj [(A) -20 (B)] TJ <0041> Tj ET\n"
        b"[3 2] 0 d 10 10 m 20 20 l S % a comment with scn in it\n"
    )
    assert problems(tmp_path, content) == ""


def test_the_page_count_and_the_page_size_must_be_the_sheets(tmp_path: Path) -> None:
    two = tmp_path / "two.pdf"
    two.write_bytes(minimal_pdf(pages=2))
    assert "2 page(s), the sheet is 1" in pdf_check.check_pdf(two, pages=1).summary()
    wrong = write(tmp_path, width_mm=210.0, height_mm=297.0)
    assert "the sheet is 297 x 210 mm" in pdf_check.check_pdf(wrong, pages=1, page_mm=(297.0, 210.0)).summary()


def test_a_file_that_is_not_a_pdf_or_is_cut_short_fails(tmp_path: Path) -> None:
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"%PDF-1.4 stub\n")
    assert "not a readable PDF" in pdf_check.check_pdf(junk, pages=1).summary()
    cut = tmp_path / "cut.pdf"
    cut.write_bytes(minimal_pdf()[:-120])
    assert not pdf_check.check_pdf(cut, pages=1).ok
    assert not pdf_check.check_pdf(tmp_path / "missing.pdf", pages=1).ok


def test_every_view_of_the_sheet_must_have_ink_over_its_extent(tmp_path: Path) -> None:
    # 297 x 210 mm page; the strokes below are two boxes, at (10..40, 10..30) mm and (100..140, 50..90) mm, in points
    mm = 72.0 / 25.4

    def box(x0: float, y0: float, x1: float, y1: float) -> bytes:
        a, b, c, d = (v * mm for v in (x0, y0, x1, y1))
        return f"{a:.2f} {b:.2f} m {c:.2f} {b:.2f} l {c:.2f} {d:.2f} l {a:.2f} {d:.2f} l h S\n".encode()

    path = write(tmp_path, b"1 w " + box(10, 10, 40, 30) + box(100, 50, 140, 90))
    views = [pdf_check.ViewBox("front", (10, 10, 40, 30)), pdf_check.ViewBox("top", (100, 50, 140, 90))]
    report = pdf_check.check_pdf(path, pages=1, views=views)
    assert report.ok and report.views_with_ink == ["front", "top"]
    missing = [*views, pdf_check.ViewBox("right", (200, 100, 260, 160))]
    report = pdf_check.check_pdf(path, pages=1, views=missing)
    assert not report.ok and report.views_without_ink == ["right"]
    assert "no ink in the PDF where the sheet has right" in report.summary()


def test_ink_that_only_touches_a_corner_of_a_view_is_not_the_view(tmp_path: Path) -> None:
    mm = 72.0 / 25.4
    path = write(tmp_path, f"10 10 m {12 * mm:.2f} {12 * mm:.2f} l S\n".encode())
    report = pdf_check.check_pdf(path, pages=1, views=[pdf_check.ViewBox("front", (10, 10, 60, 60))])
    assert report.views_without_ink == ["front"]


def test_the_page_transform_is_followed_so_ink_is_found_where_it_lands(tmp_path: Path) -> None:
    # Qt writes everything under a scale and a flip: a box in 1/10 mm units drawn under a cm
    content = b"q 0.28346 0 0 -0.28346 0 595.28 cm 100 100 m 400 100 l 400 300 l 100 300 l h S Q\n"
    path = write(tmp_path, content)
    # in mm: x 10..40 and, counted from the top, y 10..30, that is 180..200 from the bottom of a 210 mm page
    report = pdf_check.check_pdf(path, pages=1, views=[pdf_check.ViewBox("front", (10, 180, 40, 200))])
    assert report.ok, report.summary()


def test_require_sound_raises_with_every_problem_found(tmp_path: Path) -> None:
    path = write(tmp_path, b"/CSp cs scn 10 10 m 20 20 l S\n")
    with pytest.raises(pdf_check.PdfCheckError) as raised:
        pdf_check.require_sound(path, pages=1)
    assert "scn has 0 number(s)" in str(raised.value) and not raised.value.report.ok
