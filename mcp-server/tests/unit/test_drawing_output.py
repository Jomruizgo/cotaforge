# ruff: noqa: E501
"""One truth for the sheet: what ``capture`` shows, what ``export pdf`` writes and what a reviewer reads. The PDF is checked
after it is written (a damaged one is an error and nothing is saved), a capture can zoom, and the objects a native script put
on the page are listed instead of silently left out. Over the drawing fake: the CAD side is ``test_visible_sheet_output.py``
and the real-server tests."""

import pytest
from cotaforge.contract import DrawingFinding, SheetView

from cotaforge_mcp.config import Settings
from tests.conftest import Caller, DrawingFake, MakeSession, plate, set_review
from tests.pdf_support import minimal_pdf


@pytest.fixture
def sheet(make: MakeSession, settings: Settings) -> tuple[Caller, DrawingFake]:
    set_review(settings, "ninguno")
    adapter = DrawingFake()
    call = Caller(make(adapter))
    plate(call)
    call.ok("drawing", action="create")
    return call, adapter


NATIVE = (
    "SecNative [DrawViewSection]: section A, cut surface SvgHatch",
    "DimNative [DrawViewDimension]: DistanceX, 12",
)


# --- the PDF is checked -----------------------------------------------------------------------------------------------


def test_a_damaged_pdf_is_an_error_and_nothing_is_saved(sheet: tuple[Caller, DrawingFake], settings: Settings) -> None:
    call, adapter = sheet
    adapter.pdf = minimal_pdf(content=b"/CSp cs scn 10 10 m 20 20 l S\n")  # a colour operator with no components
    text = call.err("export", action="pdf")
    assert "damaged" in text and "scn has 0 number(s)" in text and "colour outside 0..1" in text
    assert not (settings.workspace / "plate.pdf").exists()
    assert not list(settings.workspace.glob(".cotaforge-sheets-*"))  # the staging folder is gone too


def test_a_damaged_pdf_does_not_replace_the_good_one_that_was_there(
    sheet: tuple[Caller, DrawingFake], settings: Settings
) -> None:
    call, adapter = sheet
    call.ok("export", action="pdf")
    good = (settings.workspace / "plate.pdf").read_bytes()
    adapter.pdf = minimal_pdf(content=b"10 10 m 20 l S\n")
    assert "damaged" in call.err("export", action="pdf", overwrite=True)
    assert (settings.workspace / "plate.pdf").read_bytes() == good


def test_a_pdf_of_the_wrong_page_count_or_size_is_refused(sheet: tuple[Caller, DrawingFake]) -> None:
    call, adapter = sheet
    adapter.pdf = minimal_pdf(pages=2)
    assert "2 page(s), the sheet is 1" in call.err("export", action="pdf")
    adapter.pdf = minimal_pdf(width_mm=210.0, height_mm=297.0)
    assert "the sheet is 297 x 210 mm" in call.err("export", action="pdf")


def test_a_view_with_no_ink_in_the_pdf_is_refused(sheet: tuple[Caller, DrawingFake]) -> None:
    call, adapter = sheet
    # the sound content draws a box at 3.5..38.9 x 3.5..21.2 mm; the sheet says it has a view far from there
    adapter.output = {"views": (SheetView(name="front", rect=(100, 100, 160, 150)),)}
    text = call.err("export", action="pdf")
    assert "no ink in the PDF where the sheet has front" in text


def test_a_sound_pdf_is_reported_as_checked_with_what_the_sheet_holds(
    sheet: tuple[Caller, DrawingFake], settings: Settings
) -> None:
    call, adapter = sheet
    adapter.output = {
        "renderer": "techdraw",
        "native": NATIVE,
        "views": (SheetView(name="front", rect=(3.5, 3.5, 38.9, 21.2)),),
    }
    text = call.ok("export", action="pdf")
    assert "checked: one page, every operator sound, ink in all 1 view(s)" in text
    assert "TechDraw's own export" in text and "native TechDraw objects on the sheet (2" in text
    assert (settings.workspace / "plate.pdf").is_file()


def test_the_objects_a_pdf_does_not_draw_are_named_not_hidden(sheet: tuple[Caller, DrawingFake]) -> None:
    call, adapter = sheet
    adapter.output = {"native": NATIVE, "unrendered": tuple(f"{n} (not drawn)" for n in NATIVE)}
    text = call.ok("export", action="pdf")
    assert "NOT DRAWN here (2)" in text and "SecNative" in text and "DimNative" in text


def test_the_dxf_is_not_checked_as_a_pdf(sheet: tuple[Caller, DrawingFake], settings: Settings) -> None:
    call, adapter = sheet
    adapter.pdf = b"not a pdf"
    call.ok("export", action="dxf")
    assert (settings.workspace / "plate.dxf").is_file()


# --- the capture is the same sheet ------------------------------------------------------------------------------------


def test_capture_says_how_it_was_rendered_and_what_the_sheet_holds(sheet: tuple[Caller, DrawingFake]) -> None:
    call, adapter = sheet
    adapter.output = {
        "renderer": "techdraw",
        "native": NATIVE,
        "views": (
            SheetView(name="front", rect=(0, 0, 10, 10)),
            SheetView(name="SecNative", rect=(0, 0, 5, 5), kind="native"),
        ),
    }
    reply = call("drawing", action="capture")
    assert not reply.is_error and len(reply.images) == 1
    assert "TechDraw's own scene of the sheet window (the PDF is written from the same scene)" in reply.text
    assert "views on the sheet: front, SecNative (native)" in reply.text
    assert "native TechDraw objects on the sheet (2; no item made them, the review cannot read them)" in reply.text
    assert "NOT DRAWN" not in reply.text


def test_a_headless_capture_lists_every_native_object_it_could_not_draw(sheet: tuple[Caller, DrawingFake]) -> None:
    call, adapter = sheet
    adapter.output = {"native": NATIVE, "unrendered": ("SecNative (not drawn)", "DimNative (not drawn)")}
    reply = call("drawing", action="capture")
    assert "this CAD is headless" in reply.text
    assert "NOT DRAWN here (2): SecNative (not drawn) | DimNative (not drawn)" in reply.text


def test_capture_can_zoom_into_a_region_of_the_sheet(sheet: tuple[Caller, DrawingFake]) -> None:
    call, adapter = sheet
    reply = call("drawing", action="capture", region=[60, 120, 140, 180])
    assert not reply.is_error
    request = adapter.captured[-1]
    assert request.region == (60, 120, 140, 180)
    assert request.width == 1400  # the longest side keeps the full width
    assert "zoom into x 60..140, y 120..180 mm" in reply.text
    call("drawing", action="capture", region=[0, 0, 20, 100])
    assert adapter.captured[-1].width == 280  # a tall narrow region is not stretched into a huge image
    call("drawing", action="capture")
    assert adapter.captured[-1].region is None


def test_a_region_needs_four_numbers(sheet: tuple[Caller, DrawingFake]) -> None:
    call, adapter = sheet
    text = call.err("drawing", action="capture", region=[1, 2, 3])
    assert "four numbers" in text and adapter.captured == []


# --- the review does not read natives, and says so --------------------------------------------------------------------


def test_list_names_the_native_objects_the_review_does_not_read(make: MakeSession, settings: Settings) -> None:
    set_review(settings, "ninguno")
    call = Caller(make(_NativeSheet()))
    plate(call)
    call.ok("drawing", action="create")
    listed = call.ok("drawing", action="list")
    assert "native TechDraw objects (2; no item made them, the review does not read them" in listed
    assert "SecNative [DrawViewSection]" in listed


def test_the_review_reports_the_native_objects_it_cannot_read(make: MakeSession, settings: Settings) -> None:
    set_review(settings, "ninguno")
    adapter = _NativeSheet()
    adapter.findings = (
        DrawingFinding(
            code="native_unread",
            severity="warning",
            message="The sheet holds 2 native TechDraw object(s) this review cannot read",
            id="native_unread",
            blocking=False,
        ),
    )
    call = Caller(make(adapter))
    plate(call)
    call.ok("drawing", action="create")
    review = call.ok("drawing", action="review")
    assert "native_unread" in review and "cannot read" in review
    assert "nothing found" not in review


class _NativeSheet(DrawingFake):
    """A fake whose sheets hold native objects (a script put them on the page)."""

    def describe_drawing(self, request: object) -> object:
        described = super().describe_drawing(request)  # type: ignore[arg-type]
        return described.model_copy(
            update={"sheets": tuple(s.model_copy(update={"native": NATIVE}) for s in described.sheets)}  # type: ignore[attr-defined]
        )
