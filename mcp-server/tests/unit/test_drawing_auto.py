# ruff: noqa: E501
"""The harness puts on the sheet what it is sure the sheet needs (rows whose whole content the model or the intent file
already gives) and asks only what needs a decision. The fake CAD stores the items an add brings and a test makes the sheet
print what they print (``DrawingFake.on_add``)."""

from typing import Any

from cotaforge_mcp import drawing_requirements as dr
from cotaforge_mcp.config import Settings
from tests.conftest import Caller, DrawingFake
from tests.unit.test_drawing_requirements import PLATE, SHAFT, finding
from cotaforge_mcp.server import make_session
from tests.conftest import plate, set_review

INTENT = """## Tratamientos y acabado
- Temple y revenido a 40 HRC.
- Sin recubrimiento.
"""


def shaft_with_callouts(fake: DrawingFake, items: list[dict[str, Any]]) -> None:
    """What a hole_dim callout of the shaft's holes does to the sheet: it prints the thread and the declared fit."""
    if any(i["type"] == "hole_dim" for i in items):
        fake.facts = {**fake.facts, "thread_designation_on_sheet": 1, "tolerance_classes_on_sheet": 1, "fit_holes_uncalled": 0}
        fake.findings = tuple(f for f in fake.findings if f.code not in dr.CALLOUT_CODES)


def bare_session(settings: Settings, fake: DrawingFake, mode: str = "own") -> Caller:
    """A session on the fake with a part and NO sheet yet: the test makes it."""
    set_review(settings, mode)
    call = Caller(make_session(Settings(adapter="fake", workspace=settings.workspace, home=settings.home, base_memory=False), fake))
    plate(call)
    return call


def shaft_session(settings: Settings, mode: str = "own") -> tuple[Caller, DrawingFake]:
    fake = DrawingFake()
    fake.facts = {**SHAFT, "keyways": 0, "turned": 0, "boss_radii_undimensioned": 0, "bosses": 0}
    fake.findings = (finding("thread_no_callout", "Agujero roscado sin llamada"),)
    fake.on_add = shaft_with_callouts
    return bare_session(settings, fake, mode), fake


def items_of(fake: DrawingFake) -> dict[str, dict[str, Any]]:
    return {i["id"]: i for i in fake.drawings["Drawing"]["items"]}


# --- what is determined is added, what needs a decision is asked ------------------------------------------------------------------


def test_the_callouts_of_a_threaded_fitted_shaft_are_added_without_asking(settings: Settings) -> None:
    call, fake = shaft_session(settings)
    text = call.ok("drawing", action="create")
    assert "added automatically" in text and "rq_callouts" in text and "meets req.thread" in text
    assert items_of(fake)["rq_callouts"]["type"] == "hole_dim" and items_of(fake)["rq_callouts"]["all"] is True
    listing = call.ok("drawing", action="requirements")
    assert "[added automatically] req.thread" in listing and "added by the harness: item rq_callouts" in listing
    assert "ask, one at a time" in listing  # what needs a decision is still a question (surface texture, treatments...)
    assert "[MISSING] req.thread" not in listing


def test_a_row_that_needs_a_decision_is_a_question_that_blocks_the_export(settings: Settings) -> None:
    call, fake = shaft_session(settings)
    call.ok("drawing", action="create")
    refused = call.err("export", action="pdf")
    assert "req.surface_texture" in refused
    assert "req.thread" not in refused  # the harness met it
    assert "rq_surface_texture" not in items_of(fake)  # no value known: no item


def test_what_the_intent_file_says_is_added_as_a_note_in_the_designers_words(settings: Settings) -> None:
    (settings.workspace / "drawings").mkdir()
    (settings.workspace / "drawings" / "plate-intent.md").write_text(INTENT, encoding="utf-8")
    fake = DrawingFake()
    fake.facts = PLATE
    fake.findings = (finding("notes_heat_treatment", "Falta"), finding("notes_coating", "Falta"), finding("notes_edges", "Falta"))
    call = bare_session(settings, fake)
    call.ok("drawing", action="create")
    text = call.ok("drawing", action="review")
    items = items_of(fake)
    assert items["rq_heat_treatment"]["text"] == "Temple y revenido a 40 HRC." and items["rq_coating"]["text"] == "Sin recubrimiento."
    assert "rq_edge_state" not in items  # the file says nothing about the edges: that one is a question
    assert "added automatically" in text and "rq_heat_treatment" in text
    assert "ask, one at a time" in text and "req.edge_state" in text


def test_nothing_is_added_twice_and_the_title_block_rows_say_create_filled_them(settings: Settings) -> None:
    call, fake = shaft_session(settings)
    call.ok("drawing", action="create")
    before = len(fake.drawings["Drawing"]["items"])
    call.ok("drawing", action="review")
    call.ok("drawing", action="requirements")
    assert len(fake.drawings["Drawing"]["items"]) == before
    text = call.ok("drawing", action="requirements")
    assert "[added automatically] req.title_block" in text and "in the title block (create fills it)" in text


def test_in_mode_none_the_harness_adds_nothing(settings: Settings) -> None:
    call, fake = shaft_session(settings, mode="none")
    call.ok("drawing", action="create")
    assert "rq_callouts" not in items_of(fake)


# --- the drafter may move them, and remove one only with a reason ------------------------------------------------------------------


def test_an_automatic_item_is_removed_only_with_a_reason_that_is_recorded_and_it_never_comes_back(settings: Settings) -> None:
    (settings.workspace / "drawings").mkdir()
    (settings.workspace / "drawings" / "plate-intent.md").write_text(INTENT, encoding="utf-8")
    fake = DrawingFake()
    fake.facts = PLATE
    fake.findings = (finding("notes_coating", "Falta"),)
    call = bare_session(settings, fake)
    call.ok("drawing", action="create")
    call.ok("drawing", action="review")
    assert "rq_coating" in items_of(fake)
    no_reason = call.err("drawing", action="remove", items=["rq_coating"])
    assert "removed only with a reason" in no_reason and "rq_coating" in items_of(fake)
    call.ok("drawing", action="remove", items=["rq_coating"], reason="el cliente pinta la pieza")
    assert "rq_coating" not in items_of(fake)
    answer = fake.drawings["Drawing"]["answers"]["req.coating"]
    assert answer.verdict == "not_applies" and "el cliente pinta la pieza" in answer.text and "rq_coating" in answer.text
    assert answer.ordered_by.startswith("the drafter")  # recorded for the engineer; an intent file's note needs only a reason
    call.ok("drawing", action="review")
    assert "rq_coating" not in items_of(fake)  # answered: it does not come back


def test_removing_what_the_model_measured_needs_the_engineers_order_too(settings: Settings) -> None:
    call, fake = shaft_session(settings)
    call.ok("drawing", action="create")
    text = call.err("drawing", action="remove", items=["rq_callouts"], reason="no hacen falta las llamadas")
    assert "measured_not_dismissable" in text and "rq_callouts" in items_of(fake)
    call.ok("drawing", action="remove", items=["rq_callouts"], reason="Ana lo ordena: las llamadas van en otra hoja", by="Ana")
    assert "rq_callouts" not in items_of(fake)
    assert fake.drawings["Drawing"]["answers"]["req.thread"].ordered_by == "Ana"


def test_a_callout_of_the_drafters_own_replaces_the_automatic_one(settings: Settings) -> None:
    call, fake = shaft_session(settings)
    call.ok("drawing", action="create")
    text = call.ok("drawing", action="add", items=[{"type": "hole_dim", "id": "h1", "all": True}])
    assert "replaced the automatic rq_callouts by yours" in text
    assert "rq_callouts" not in items_of(fake) and "h1" in items_of(fake)


# --- the planning is pure ------------------------------------------------------------------------------------------------------------


class Item:
    def __init__(self, item_id: str, kind: str) -> None:
        self.id, self.type = item_id, kind


def test_the_plan_adds_callouts_once_and_not_over_the_drafters_own() -> None:
    rows = dr.evaluate(SHAFT, (finding("thread_no_callout"),), {}, dr.Intent())
    (one,) = dr.plan_additions(rows, [])
    assert one.item == {"type": "hole_dim", "id": "rq_callouts", "all": True} and "req.thread" in one.rows
    assert dr.plan_additions(rows, [Item("h1", "hole_dim")]) == []
    assert dr.plan_additions(rows, [Item("rq_callouts", "hole_dim")]) == []


def test_every_determined_row_says_how_and_the_decision_rows_say_nothing() -> None:
    kinds = {row.id: row.auto for row in dr.ROWS}
    assert {k for k, v in kinds.items() if v} >= {"req.thread", "req.fit_class", "req.heat_treatment", "req.title_block"}
    assert not any(kinds[i] for i in ("req.bent_sheet", "req.geometric_tolerance", "req.turned_part"))
    assert kinds["req.keyway"] == "keyway" and dr.auto_item_id(next(r for r in dr.ROWS if r.id == "req.keyway")) == "rq_keyway"
    assert [r.id for r in dr.rows_of_item("rq_keyway_w")] == ["req.keyway"]
    assert dr.auto_item_id(next(r for r in dr.ROWS if r.id == "req.heat_treatment")) == "rq_heat_treatment"
    assert {r.id for r in dr.rows_of_item("rq_callouts")} == {"req.thread", "req.fit_class", "req.dimensions"}

