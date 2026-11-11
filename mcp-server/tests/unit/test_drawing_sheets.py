"""The drawing tool beyond the first sheet: items (add / remove / update / list), several sheets, the title block fields
and the one-sheet-one-file export, over a fake CAD that keeps the sheets (``DrawingFake``)."""

import json
from pathlib import Path

import pytest
from cotaforge.contract import drawing_items

from cotaforge_mcp.config import Settings
from cotaforge_mcp.guide import guide
from cotaforge_mcp.schemas import TOOLS_BY_NAME, input_schema
from tests.conftest import Caller, DrawingFake, MakeSession, plate, set_review


@pytest.fixture
def sheet(make: MakeSession, settings: Settings) -> tuple[Caller, DrawingFake]:
    set_review(settings, "ninguno")  # these tests are about the sheet, not about who reviews it
    adapter = DrawingFake()
    call = Caller(make(adapter))
    plate(call)
    return call, adapter


# --- create: the title block and the sheet name ----------------------------------------------------------------------


def test_create_passes_the_new_title_block_fields(sheet: tuple[Caller, DrawingFake]) -> None:
    call, adapter = sheet
    call.ok(
        "drawing",
        action="create",
        drawing_number="PL-001",
        revision="B",
        general_tolerances="ISO 2768-fH",
        company="Taller Sur",
        language="en",
        sheet="Detalle",
    )
    request = adapter.created[-1]
    assert (request.drawing_number, request.revision, request.general_tolerances) == ("PL-001", "B", "ISO 2768-fH")
    assert (request.company, request.language, request.sheet) == ("Taller Sur", "en", "Detalle")


def test_the_defaults_are_a_spanish_title_block_and_iso_2768_mk(sheet: tuple[Caller, DrawingFake]) -> None:
    call, adapter = sheet
    call.ok("drawing", action="create")
    request = adapter.created[-1]
    assert (request.language, request.general_tolerances, request.sheet, request.body) == (
        "es",
        "ISO 2768-mK",
        None,
        None,
    )


def test_create_passes_the_paper_the_projection_angle_and_the_title_block_fields(
    sheet: tuple[Caller, DrawingFake],
) -> None:
    call, adapter = sheet
    call.ok(
        "drawing",
        action="create",
        template="A2",
        angle="third",
        views=["top"],
        title_block={"legal_owner_2": "Taller Sur", "fixed_note_1": "Sin tratamiento"},
    )
    request = adapter.created[-1]
    assert (request.template, request.angle, request.views) == ("A2", "third", ("top",))
    assert request.title_block == {"legal_owner_2": "Taller Sur", "fixed_note_1": "Sin tratamiento"}
    call.ok("drawing", action="create", template="Letter", sheet="Carta")
    assert adapter.created[-1].template == "Letter" and adapter.created[-1].angle == "first"
    assert "template" in call.err("drawing", action="create", template="B5")
    assert "angle" in call.err("drawing", action="create", angle="second")


def test_the_title_block_is_set_with_update_and_with_nothing_else(sheet: tuple[Caller, DrawingFake]) -> None:
    call, adapter = sheet
    call.ok("drawing", action="create")
    call.ok("drawing", action="update", title_block={"document_type": "Plano de taller"})
    assert adapter.annotated[-1].title_block == {"document_type": "Plano de taller"}
    call.ok("drawing", action="add", items=[{"type": "note", "text": "x"}], title_block={"title": "ignored"})
    assert adapter.annotated[-1].title_block == {}  # only update takes it


def test_the_quality_topic_says_what_every_sheet_carries_and_fits_what_cad_guide_returns(make: MakeSession) -> None:
    session = make(DrawingFake())
    text = guide(session, "drawing:quality")
    assert 200 < len(text) < 6000
    for word in ("auto=true", "dowel=true", "single_view_enough", "angle=first|third", "title_block", "NOTAS"):
        assert word in text, word
    assert "drawing:quality" in guide(session, "drawing")
    assert "A2|A1|A0|Letter" in guide(session, "drawing")


def test_a_bad_sheet_name_or_language_is_refused_before_the_cad(sheet: tuple[Caller, DrawingFake]) -> None:
    call, adapter = sheet
    assert "sheet" in call.err("drawing", action="create", sheet="2 bad name")
    assert "language" in call.err("drawing", action="create", language="fr")
    assert adapter.created == []


def test_a_create_says_which_settings_it_did_not_get_so_a_rebuild_can_keep_the_sheets_own(
    sheet: tuple[Caller, DrawingFake],
) -> None:
    call, adapter = sheet
    call.ok("drawing", action="create", template="A3", scale="1:2")
    request = adapter.created[-1]
    assert request.fresh is False and request.unset == ("angle", "language", "projection", "body")
    call.ok("drawing", action="create", fresh=True, projection="techdraw")
    assert adapter.created[-1].fresh is True and "projection" not in adapter.created[-1].unset


def test_a_rebuild_reply_says_what_it_kept_what_no_longer_resolves_and_what_a_fresh_one_threw_away(
    sheet: tuple[Caller, DrawingFake],
) -> None:
    call, adapter = sheet
    adapter.rebuilt = {
        "kept": ("items o1, hd (placed again on the new views)", "2 answer(s) to the review"),
        "unresolved": ("dz (dim): the sheet has no 'right' view",),
    }
    text = call.ok("drawing", action="create")
    assert "rebuilt: kept items o1, hd (placed again on the new views); 2 answer(s) to the review" in text
    assert "no longer resolve (fix or remove them): dz (dim): the sheet has no 'right' view" in text
    adapter.rebuilt = {"discarded": ("items hd", "the chosen reviewer (fabricante)")}
    text = call.ok("drawing", action="create", fresh=True)
    assert "fresh: threw away items hd; the chosen reviewer (fabricante)" in text and "rebuilt:" not in text


def test_the_reply_names_the_sheet_the_language_and_the_next_steps(sheet: tuple[Caller, DrawingFake]) -> None:
    call, _ = sheet
    text = call.ok("drawing", action="create", template="A3", sheet="Planta")
    assert "drawing Planta: A3 sheet at 1:2, es" in text and "o1 overall ok" in text
    assert 'drawing(action="add"' in text and 'cad_guide(topic="drawing")' in text


# --- items -----------------------------------------------------------------------------------------------------------


def test_add_passes_the_items_and_the_reply_says_what_each_one_did(sheet: tuple[Caller, DrawingFake]) -> None:
    call, adapter = sheet
    call.ok("drawing", action="create")
    call.session.dirty = False
    text = call.ok(
        "drawing",
        action="add",
        items=[{"type": "hole_dim", "all": True}, {"type": "note", "text": "Sin rebabas"}],
    )
    request = adapter.annotated[-1]
    assert request.action == "add" and [i["type"] for i in request.items] == ["hole_dim", "note"]
    assert "h2 hole_dim ok" in text and "n3 note ok" in text
    assert call.session.dirty is True  # the items are in the document


def test_items_are_checked_before_the_cad_hears_of_them(sheet: tuple[Caller, DrawingFake]) -> None:
    call, adapter = sheet
    call.ok("drawing", action="create")
    for items, needle in (
        ([{"type": "weld", "colour": "red"}], "unknown field(s) colour"),
        ([{"type": "blob"}], "type must be one of"),
        ([{"type": "hole_dim", "all": True, "colour": "red"}], "unknown field(s) colour"),
        ([{"type": "dim"}], "kind is required"),
        (["hole_dim"], "valid dictionary"),
        ([{"type": "note", "text": "a", "id": "9"}], "id '9'"),
    ):
        text = call.err("drawing", action="add", items=items)
        assert needle in text and "items" in text, (items, text)
    assert "needs items" in call.err("drawing", action="add")
    assert adapter.annotated == []


def test_an_item_the_cad_refused_is_shown_as_refused_with_its_reason(sheet: tuple[Caller, DrawingFake]) -> None:
    call, adapter = sheet
    call.ok("drawing", action="create")
    adapter.refuse = {"dim"}
    text = call.ok("drawing", action="add", items=[{"type": "dim", "kind": "radius", "face": "face:3", "id": "r9"}])
    assert "r9 dim refused: not stored" in text


def test_remove_takes_items_off_by_id_and_never_the_sheet(sheet: tuple[Caller, DrawingFake]) -> None:
    call, adapter = sheet
    call.ok("drawing", action="create")
    call.ok("drawing", action="add", items=[{"type": "note", "text": "uno", "id": "n1"}])
    text = call.ok("drawing", action="remove", items=["n1"])
    assert adapter.annotated[-1].ids == ("n1",) and "n1 note" not in text
    call.ok("drawing", action="add", items=[{"type": "note", "text": "dos", "id": "n2"}])
    call.ok("drawing", action="remove", items=[{"id": "n2", "type": "note"}])  # the item object, as list prints it
    assert adapter.annotated[-1].ids == ("n2",)
    assert "no item zz" in call.err("drawing", action="remove", items=["zz"])
    count = len(adapter.annotated)
    assert "remove_sheet" in call.err("drawing", action="remove")  # no ids: refused, the sheet stays
    assert "remove_sheet" in call.err("drawing", action="remove", items=[])
    assert len(adapter.annotated) == count and "Drawing" in adapter.drawings


def test_remove_sheet_is_the_one_way_to_take_a_sheet_away(sheet: tuple[Caller, DrawingFake]) -> None:
    call, adapter = sheet
    call.ok("drawing", action="create")
    gone = call.ok("drawing", action="remove_sheet")
    assert "sheet removed" in gone and "none" in gone and adapter.annotated[-1].action == "remove_sheet"


def test_update_lays_the_sheet_out_again(sheet: tuple[Caller, DrawingFake]) -> None:
    call, adapter = sheet
    call.ok("drawing", action="create")
    text = call.ok("drawing", action="update")
    assert adapter.annotated[-1].action == "update" and "drawing Drawing" in text


def test_list_shows_every_item_with_what_was_stored(sheet: tuple[Caller, DrawingFake]) -> None:
    call, _ = sheet
    call.ok("drawing", action="create", sheet="Planta")
    call.ok("drawing", action="add", sheet="Planta", items=[{"type": "hole_dim", "at": [10, 10, 0], "id": "h1"}])
    text = call.ok("drawing", action="list")
    assert "sheet Planta: A4 at 1:2, es" in text and "title=plate" in text
    row = next(line for line in text.splitlines() if "h1 hole_dim" in line)
    assert json.loads(row.split("  ", 2)[-1]) == {"at": [10, 10, 0]}


def test_every_action_needs_a_sheet_to_work_on(sheet: tuple[Caller, DrawingFake]) -> None:
    call, _ = sheet
    for action in ("update", "remove_sheet"):
        assert "create it first" in call.err("drawing", action=action)
    assert "create it first" in call.err("drawing", action="add", items=[{"type": "overall"}])
    assert "no sheets" in call.ok("drawing", action="list")


def test_capture_asks_for_the_sheet(sheet: tuple[Caller, DrawingFake]) -> None:
    call, adapter = sheet
    call.ok("drawing", action="create", sheet="Planta")
    call("drawing", action="capture", sheet="Planta")
    assert adapter.captured[-1].sheet == "Planta"


# --- export: one drawing, one sheet, one file ------------------------------------------------------------------------


def test_an_export_writes_one_sheet_to_one_file_and_with_several_sheets_names_the_one(
    sheet: tuple[Caller, DrawingFake], settings: Settings
) -> None:
    call, adapter = sheet
    call.ok("drawing", action="create")
    call.ok("export", action="pdf")
    assert adapter.exported[-1].sheet == "Drawing" and (settings.workspace / "plate.pdf").is_file()
    call.ok("drawing", action="create", sheet="Detalle")
    text = call.err("export", action="pdf", path="both.pdf")
    assert (
        "2 sheets (Drawing, Detalle)" in text and "one file" in text and not (settings.workspace / "both.pdf").exists()
    )
    call.ok("export", action="pdf", path="detail.pdf", sheet="Detalle")
    assert adapter.exported[-1].sheet == "Detalle" and (settings.workspace / "detail.pdf").is_file()


def test_a_dxf_is_one_file_for_the_one_sheet_asked_for(sheet: tuple[Caller, DrawingFake], settings: Settings) -> None:
    call, adapter = sheet
    call.ok("drawing", action="create")
    call.ok("drawing", action="create", sheet="Detalle")
    assert "one sheet" in call.err("export", action="dxf", path="out/plate.dxf")
    text = call.ok("export", action="dxf", path="out/detail.dxf", sheet="Detalle")
    assert "exported dxf" in text and "detail.dxf" in text and "files" not in text
    assert [p.name for p in (settings.workspace / "out").iterdir()] == ["detail.dxf"]  # one file, no scratch left
    assert adapter.exported[-1].sheet == "Detalle"


def test_a_sheet_export_is_never_replaced_unasked(sheet: tuple[Caller, DrawingFake], settings: Settings) -> None:
    call, _ = sheet
    call.ok("drawing", action="create")
    (settings.workspace / "plate.dxf").write_text("mine", encoding="utf-8")
    assert "plate.dxf already exists" in call.err("export", action="dxf")
    assert (settings.workspace / "plate.dxf").read_text(encoding="utf-8") == "mine"
    call.ok("export", action="dxf", overwrite=True)
    assert (settings.workspace / "plate.dxf").read_bytes().startswith(b"0")


def test_review_asks_the_cad_for_findings_and_lists_warnings_first(sheet: tuple[Caller, DrawingFake]) -> None:
    from cotaforge.contract import DrawingFinding  # noqa: PLC0415

    call, fake = sheet
    call.ok("drawing", action="create")
    fake.findings = (
        DrawingFinding(
            code="hole_no_callout", severity="warning", message="Agujero sin llamada", features=("feature:4",)
        ),
        DrawingFinding(code="no_view_lines", severity="info", message="falta view_lines"),
    )
    text = call.ok("drawing", action="review")
    assert fake.described[-1].review is True
    assert "review: 1 warning(s), 1 to confirm" in text
    assert "warning hole_no_callout [feature:4]: Agujero sin llamada" in text and "info no_view_lines" in text
    fake.findings = ()
    assert "nothing found" in call.ok("drawing", action="review")
    call.ok("drawing", action="list")
    assert fake.described[-1].review is False


# --- schema and guide -------------------------------------------------------------------------------------------------


def test_the_drawing_schema_names_the_new_arguments_and_stays_small(make: MakeSession) -> None:
    session = make(DrawingFake())
    schema = input_schema(TOOLS_BY_NAME["drawing"], session)
    properties = schema["properties"]
    for name in ("sheet", "items", "language", "drawing_number", "revision", "general_tolerances", "company", "body"):
        assert name in properties, name
    assert len(json.dumps(schema)) < 2000  # 1800 + the capture region
    assert "sheet" in input_schema(TOOLS_BY_NAME["export"], session)["properties"]


DRAWING_TOPICS = (
    "drawing",
    "drawing:items",
    "drawing:marks",
    "drawing:views",
    "drawing:assembly",
    "drawing:output",
    "drawing:quality",
    "drawing:done",
)


def _whole_drawing_guide(make: MakeSession) -> str:
    session = make(DrawingFake())
    return "\n".join(guide(session, topic) for topic in DRAWING_TOPICS)


def test_every_topic_of_the_drawing_guide_fits_what_cad_guide_returns(make: MakeSession) -> None:
    """Real use: the one drawing topic was 19k characters and ``cad_guide`` cut its reply at 6000, so everything after
    the sheet rules never reached the agent. Each topic is read whole."""
    session = make(DrawingFake())
    for topic in DRAWING_TOPICS:
        assert 200 < len(guide(session, topic)) < 6000, (topic, len(guide(session, topic)))
    assert "drawing:items" in guide(session, "drawing") and "TECHDRAW DRAWING" in guide(session, "drawing")


def test_the_guide_has_the_item_reference_and_says_the_sheet_is_built_live_in_the_window(make: MakeSession) -> None:
    text = _whole_drawing_guide(make)
    for item_type in drawing_items.ITEM_FIELDS:
        assert item_type in text, item_type  # every built item type is in the reference
    assert "BUILT LIVE IN THE FREECAD WINDOW" in text and "SMALL STEPS" in text and "SYMBOL object" in text
    assert "NOT into" not in text and "NO views" not in text  # the old statements: the window does show the items
    assert "remove_sheet" in text and "never the sheet" in text
    assert "rosca no declarada" in text and "offset_mm" in text and "at_mm" in text


def test_the_guide_has_no_item_reference_for_a_cad_that_only_makes_the_sheet(make: MakeSession) -> None:
    from cotaforge.contract import Capabilities  # noqa: PLC0415

    class SheetOnly(DrawingFake):
        def capabilities(self) -> Capabilities:
            return Capabilities(tools=super().capabilities().tools - {"annotate_drawing", "describe_drawing"})

    text = guide(make(SheetOnly()), "drawing")
    assert "hole_dim" not in text and "build the sheet again" in text
    schema = input_schema(TOOLS_BY_NAME["drawing"], make(SheetOnly()))
    assert schema["properties"]["action"]["enum"] == ["create", "capture"]


def test_every_field_of_the_contract_table_is_named_in_the_guide(make: MakeSession) -> None:
    text = _whole_drawing_guide(make)
    for item_type, fields in drawing_items.ITEM_FIELDS.items():
        for field in fields:
            assert field in text, (item_type, field)


def test_settings_workspace_stays_clean_of_scratch_folders(
    sheet: tuple[Caller, DrawingFake], settings: Settings
) -> None:
    call, _ = sheet
    call.ok("drawing", action="create")
    call.ok("export", action="pdf")
    leftovers = [p for p in Path(settings.workspace).iterdir() if p.name.startswith(".cotaforge-sheets-")]
    assert leftovers == []


def test_an_add_answers_briefly_with_the_items_of_the_call_and_what_the_window_did(
    sheet: tuple[Caller, DrawingFake],
) -> None:
    call, _ = sheet
    call.ok("drawing", action="create")
    call.ok("drawing", action="add", items=[{"type": "note", "id": "n1", "text": "uno"}])
    text = call.ok("drawing", action="add", items=[{"type": "note", "id": "n2", "text": "dos"}])
    assert "n2 note ok" in text and "window: 1 new" in text
    assert "n1 note" not in text and "views:" not in text and "dimensions:" not in text  # the rest is not repeated
    gone = call.ok("drawing", action="remove", items=["n2"])
    assert "removed: n2" in gone
