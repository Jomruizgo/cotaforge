"""A measured design alert is not waved away (``measured_alerts``): the pocket corners and the bearing seat close only
when the model is fixed or the engineer ordered it kept (his name and his words); the export of a drawing waits for
them too. An entry no detector measures keeps its plain answers. Fake CAD."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from cotaforge.adapters.fake import FakeAdapter

from cotaforge_mcp import bearing_seats, design_memory, measured_alerts, pocket_corners, turned_ends
from cotaforge_mcp.config import Settings
from cotaforge_mcp.server import make_session
from tests.conftest import Caller, DrawingFake, declare
from tests.unit.test_drawing_finished import drawing_session
from tests.unit.test_drawing_requirements import PLATE
from tests.unit.test_memory_v2 import PRINCIPLE
from tests.unit.test_pocket_corners import SLOT, _block, _pocket, _rect

WORDS = "Ana: deja las esquinas como están, el taller las hace por electroerosión"
ORDERED = f"does not apply: {WORDS}"
SHEET_ORDER = {"by": "Ana", "reason": WORDS}
PLAIN = "does not apply: the shop makes it by EDM"
Fix = Callable[[Caller], None]


@pytest.fixture
def base_call(tmp_path: Path) -> Caller:
    settings = Settings(adapter="fake", workspace=tmp_path, home=tmp_path / "home", base_memory=True)
    return Caller(make_session(settings, FakeAdapter()))


def _cylinder(call: Caller, step: str, diameter: float, z: float, height: float, **extra: Any) -> None:
    call.ok(
        "op",
        name="cylinder",
        id=step,
        params={"diameter_mm": diameter, "height_mm": height, "origin": [0, 0, z], **extra},
    )


def pocket_open(call: Caller) -> tuple[str, Fix]:
    """A 12 mm deep pocket with sharp corners; the way out of it is a slot with round ends instead of the rectangle."""
    _block(call)
    _pocket(call, "pocket", _rect(50, 30, 20, 20), 12)
    return pocket_corners.ENTRY_ID, lambda c: c.ok("op", edit="pocket_sk", params={"entities": [SLOT]})


def bearing_open(call: Caller) -> tuple[str, Fix]:
    """A bearing envelope on a plain shaft much longer than itself; the way out of it is a shorter shaft."""
    call.ok("doc", action="new", name="drive")
    call.ok("memory", action="brief", sector="plastics", processes=["3D printing"])  # no bare-end alert here
    _cylinder(call, "shaft", 25, 0, 120)
    _cylinder(call, "env", 52, 20, 15, role="comercial")
    _cylinder(call, "env_bore", 25, 20, 15)
    call.ok("op", name="boolean_subtract", id="env_ring", params={"target": "env", "tools": ["env_bore"]})
    return bearing_seats.ENTRY_ID, lambda c: c.ok("op", edit="shaft", params={"height_mm": 28})


def turned_open(call: Caller) -> tuple[str, Fix]:
    """A plain shaft with two bare ends. The fake cannot chamfer a circular edge (the real CAD can: see the integration
    test), so the way out here is a shaft cut down to a disc, which is no longer a shaft."""
    call.ok("doc", action="new", name="shaft")
    _cylinder(call, "shaft", 25, 0, 120)
    return turned_ends.ENTRY_ID, lambda c: c.ok("op", edit="shaft", params={"height_mm": 20})


CASES = [
    pytest.param(pocket_open, id="pocket-corners"),
    pytest.param(bearing_open, id="bearing-seat"),
    pytest.param(turned_open, id="turned-ends"),
]


def read(call: Caller, entry: str) -> None:
    call.ok("memory", action="get", id=entry)


# ---- the rule, for every measured alert ------


@pytest.mark.parametrize("scenario", CASES)
def test_the_review_holds_the_open_violation_with_what_was_measured(base_call: Caller, scenario: Any) -> None:
    entry, _ = scenario(base_call)
    refused = base_call.err("memory", action="review", stage="detail")
    assert "pending_answers" in refused and f"- {entry} — MEASURED on the model, still fires" in refused
    assert "by=<his name>" in refused and "<his words, quoted>" in refused


@pytest.mark.parametrize("scenario", CASES)
def test_fixed_closes_it(base_call: Caller, scenario: Any) -> None:
    entry, fix = scenario(base_call)
    assert measured_alerts.count_open(base_call.session) >= 1
    fix(base_call)  # the harness measures again: nothing fires
    assert measured_alerts.live(base_call.session) == {}
    ready = base_call.ok("memory", action="review", stage="detail")
    assert "ready - no entry is called for here" in ready
    # an answer about what was fixed is an ordinary one now
    read(base_call, entry)
    assert base_call.ok("memory", action="answer", id=entry, text="fixed: corners now have a radius").startswith(
        "answered:"
    )


@pytest.mark.parametrize("scenario", CASES)
def test_an_order_with_his_words_closes_it_and_the_review_shows_who_and_what(base_call: Caller, scenario: Any) -> None:
    entry, _ = scenario(base_call)
    read(base_call, entry)
    done = base_call.ok("memory", action="answer", id=entry, text=ORDERED, by="Ana")
    assert f"answered: {entry}" in done and "ordered by Ana" in done
    ready = base_call.ok("memory", action="review", stage="detail")
    assert "ready - 1 entry answered" in ready and f"- {entry}: ORDERED KEPT, measured:" in ready
    assert "[ordered by Ana]" in ready and "Ana: deja las esquinas como están" in ready
    assert measured_alerts.open_now(base_call.session) == {}


@pytest.mark.parametrize("scenario", CASES)
def test_a_bare_does_not_apply_is_refused_while_it_fires_and_so_is_any_answer_of_the_agent(
    base_call: Caller, scenario: Any
) -> None:
    entry, _ = scenario(base_call)
    read(base_call, entry)
    for text in (PLAIN, "applies: it is dealt with in the drawing", "fixed: I changed it"):
        refused = base_call.err("memory", action="answer", id=entry, text=text)
        assert "measured_not_dismissable" in refused and "was MEASURED on the model and still fires" in refused, text
    assert entry not in base_call.session.mem.answers
    assert "MEASURED on the model" in base_call.err("memory", action="review", stage="detail")


@pytest.mark.parametrize("scenario", CASES)
def test_an_order_needs_his_name_and_words_that_are_not_a_yes(base_call: Caller, scenario: Any) -> None:
    entry, _ = scenario(base_call)
    read(base_call, entry)
    only_words = base_call.err("memory", action="answer", id=entry, text=ORDERED)  # no name: it is the agent's
    assert "measured_not_dismissable" in only_words
    only_name = base_call.err("memory", action="answer", id=entry, text="does not apply: ok", by="Ana")
    assert "answer is not enough" in only_name
    assert entry not in base_call.session.mem.answers


@pytest.mark.parametrize("scenario", CASES)
def test_the_order_is_kept_with_the_document_state(base_call: Caller, scenario: Any) -> None:
    entry, _ = scenario(base_call)
    read(base_call, entry)
    base_call.ok("memory", action="answer", id=entry, text=ORDERED, by="Ana")
    state = base_call.session.mem.to_json()["answers"][entry]
    assert state["ordered_by"] == "Ana" and state["order"] == WORDS and state["measured"].startswith("memory: ")
    assert measured_alerts.covered(state, measured_alerts.live(base_call.session)[entry])


# ---- the order covers what was measured when he gave it ------


def test_a_new_violation_after_the_order_is_open_again(base_call: Caller) -> None:
    call = base_call
    entry, _ = pocket_open(call)
    read(call, entry)
    call.ok("memory", action="answer", id=entry, text=ORDERED, by="Ana")
    assert "ready" in call.ok("memory", action="review", stage="detail")
    call.ok(
        "op",
        name="sketch",
        id="second_sk",
        params={
            "on": {"kind": "face", "from_op": "s2", "select": {"normal": [0, 0, 1]}},
            "entities": [_rect(15, 15, 8, 8)],
        },
    )
    call.ok("op", name="cut", id="second", params={"sketch": "second_sk", "depth_mm": 10})
    refused = call.err("memory", action="review", stage="detail")
    assert "second (10 mm deep)" in refused and "ORDERED KEPT" not in refused.split("answered:")[0]


def test_a_slot_that_a_tool_can_make_is_not_a_violation(base_call: Caller) -> None:
    _block(base_call)
    _pocket(base_call, "slot", SLOT, 12)
    assert measured_alerts.live(base_call.session) == {}
    assert "ready" in base_call.ok("memory", action="review", stage="detail")


# ---- an entry nobody measures keeps its plain answers ------


def test_an_unmeasured_entry_can_still_be_answered_does_not_apply(base_call: Caller, tmp_path: Path) -> None:
    call = base_call
    declare(call.session.settings, {**PRINCIPLE, "when": {"ops": ["cut"], "stages": ["detail"]}})
    entry, _ = pocket_open(call)
    assert not measured_alerts.is_measured(PRINCIPLE["id"])
    read(call, PRINCIPLE["id"])
    done = call.ok(
        "memory", action="answer", id=PRINCIPLE["id"], text="does not apply: the part is machined whole after joining"
    )
    assert "answered: precision-del-ensamble (does not apply)" in done
    refused = call.err("memory", action="review", stage="detail")  # the measured one is still open
    assert entry in refused and "DOES NOT APPLY" in refused


def test_the_alerts_are_the_ones_the_harness_measures() -> None:
    assert {d.entry_id for d in measured_alerts.detectors()} == {
        pocket_corners.ENTRY_ID,
        bearing_seats.ENTRY_ID,
        turned_ends.ENTRY_ID,
    }


# ---- the export of a drawing ------


def _drawing(settings: Settings) -> tuple[Caller, DrawingFake]:
    fake = DrawingFake()
    fake.facts = PLATE
    call, _ = drawing_session(settings, base=True, adapter=fake, mode="own")
    return call, fake


def _the_plate_pocket(call: Caller) -> None:
    top = {"kind": "face", "from_op": "s2", "select": {"normal": [0, 0, 1]}}
    call.ok("op", name="sketch", id="pocket_sk", params={"on": top, "entities": [_rect(50, 30, 20, 20)]})
    call.ok("op", name="cut", id="pocket", params={"sketch": "pocket_sk", "depth_mm": 6})


def _answer_the_rest(call: Caller) -> None:
    """Everything the export waits for but the measured alerts: the memory entries of the drawing and two rows."""
    for entry in design_memory.drawing_entries(call.session):
        read(call, entry.id)
        call.ok("drawing", action="answer", id=entry.id, text="applies: this sheet follows it as drawn")
    call.ok("drawing", action="answer", id="req.surface_texture", text="met: Ra 3,2 general en el rótulo")
    call.ok("drawing", action="answer", id="req.quantity_process", text="met: nota 3, cantidad 10 piezas, fresado")
    call.ok("drawing", action="answer", id="req.functional_dimensions", text="no aplica: ninguna medida es funcional, todas libres")


def test_a_drawing_of_a_part_with_an_open_violation_is_not_exported(settings: Settings) -> None:
    call, fake = _drawing(settings)
    _the_plate_pocket(call)
    _answer_the_rest(call)
    refused = call.err("export", action="pdf")
    assert "drawing_unfinished" in refused and "[MISSING] req.measured_alerts" in refused
    assert (
        "Measured design alerts resolved" in refused and "measured now: memory: " + pocket_corners.ENTRY_ID in refused
    )
    assert fake.exported == []


def test_the_row_is_closed_by_fixing_the_model(settings: Settings) -> None:
    call, fake = _drawing(settings)
    _the_plate_pocket(call)
    _answer_the_rest(call)
    assert "req.measured_alerts" in call.err("export", action="pdf")
    call.ok("op", edit="pocket_sk", params={"entities": [SLOT]})
    assert "exported pdf" in call.ok("export", action="pdf")
    assert fake.exported


def test_the_row_is_closed_by_his_order_given_in_memory(settings: Settings) -> None:
    call, fake = _drawing(settings)
    _the_plate_pocket(call)
    _answer_the_rest(call)
    read(call, pocket_corners.ENTRY_ID)
    call.ok("memory", action="answer", id=pocket_corners.ENTRY_ID, text=ORDERED, by="Ana")
    assert "exported pdf" in call.ok("export", action="pdf")


def test_no_answer_on_the_sheet_closes_the_row_not_even_with_his_words(settings: Settings) -> None:
    call, fake = _drawing(settings)
    _the_plate_pocket(call)
    _answer_the_rest(call)
    for extra in ({}, SHEET_ORDER):
        text = call.err("drawing", action="answer", id="req.measured_alerts", text="met: it is fine here", **extra)
        assert "measured_not_dismissable" in text and "memory(action=answer" in text
    assert "req.measured_alerts" in call.err("export", action="pdf")
    assert fake.exported == []


def test_a_part_without_violations_has_no_such_row(settings: Settings) -> None:
    call, _ = _drawing(settings)
    assert "req.measured_alerts" not in call.ok("drawing", action="requirements")
