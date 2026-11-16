# ruff: noqa: E501
"""An engineer's order is recorded only when he confirmed that very call in Claude Code's permission dialog
(``engineer_orders``): which calls carry an order, what the hook answers (ask, pass, deny when nobody is there), the
ticket the server checks, and that the work goes on when he is away. The engineer of these tests is ``Engineer``
(conftest): the real hook runs, and he answers its dialog the way a person does."""

import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
import types
from pathlib import Path
from typing import Any

import pytest

from cotaforge_mcp import cli, design_memory, engineer_orders
from cotaforge_mcp.config import Settings
from cotaforge_mcp.drawing_window import opening_prompt
from cotaforge_mcp.reply import ToolError
from cotaforge_mcp.server import call_id_of, make_session
from cotaforge_mcp.tools import call_tool
from tests.conftest import REJECTED, Caller, DrawingFake, Engineer

ENTRY = {
    "id": "ajuste-ejes",
    "type": "practice",
    "title": "Ajuste de ejes",
    "applies_when": "Ejes con rodamiento",
    "not_when": "Ejes sin carga",
}
APPROVE = {"action": "approve", "id": "ajuste-ejes", "by": "Ana", "text": "apruébala como regla"}

ORDERS: list[tuple[str, dict[str, Any], str]] = [
    # every place that records the engineer's word: (tool, a call that carries his order, its kind)
    ("drawing", {"action": "answer", "id": "req.thread", "text": "no aplica: x", "by": "Ana", "reason": "la rosca la hace el cliente"}, "answer"),
    ("drawing", {"action": "remove", "items": ["rq_callouts"], "by": "Ana", "reason": "van en otra hoja"}, "remove"),
    ("drawing", {"action": "review_report", "sheet": "Plano", "by": "engineer", "items": [{"kind": "missing", "text": "falta el número"}]}, "review_report"),
    ("drawing", {"action": "review_report", "sheet": "Plano", "by": "ingeniero", "items": []}, "review_report"),
    ("drawing", {"action": "review_choice", "sheet": "Plano", "by": "engineer"}, "review_choice"),
    ("drawing", {"action": "review_choice", "sheet": "Plano", "by": "fabricator"}, "review_choice"),
    ("drawing", {"action": "approve", "sheet": "Plano", "by": "Ana", "reason": "apruebo el plano"}, "approve_sheet"),
    ("drawing", {"action": "compose", "decisions": {"notes": ["x"]}, "recompose": True, "by": "Ana", "reason": "hazlo de nuevo"}, "compose"),
    ("drawing", {"action": "compose", "decisions": {"answers": {"req.thread": "no aplica: x"}}, "by": "Ana", "reason": "no lleva rosca"}, "compose"),
    ("memory", {"action": "answer", "id": "machining-inner-corners", "by": "Ana", "text": "does not apply: déjalas vivas"}, "keep_alert"),
    ("memory", APPROVE, "approve_entry"),
    ("memory", {"action": "brief", "drawing_review": "own", "text": "revisa tú"}, "brief"),
    ("memory", {"action": "brief", "sector": "moldes"}, "brief"),
    ("memory", {"action": "brief", "processes": ["fresado"]}, "brief"),
    ("channel", {"action": "reply", "role": "drafter", "name": "soporte", "q": 1, "by": "engineer", "text": "H7"}, "reply"),
    ("channel", {"action": "decide", "role": "drafter", "name": "soporte", "q": 1, "text": "libre"}, "decide"),
]
NOT_ORDERS: list[tuple[str, dict[str, Any]]] = [
    ("drawing", {"action": "answer", "id": "q.callouts", "text": "no aplica: un solo tipo de agujero"}),  # the agent's own answer
    ("drawing", {"action": "review_report", "sheet": "Plano", "by": "fabricator", "items": []}),  # the manufacturer agent's
    ("drawing", {"action": "add", "items": [{"type": "note", "text": "x"}]}),
    ("drawing", {"action": "export"}),
    ("memory", {"action": "answer", "id": "x", "text": "applies: here"}),
    ("memory", {"action": "brief"}),
    ("memory", {"action": "brief", "language": "en"}),
    ("memory", {"action": "brief", "drawing_review": "engineer_or_fabricator:20"}),  # the default needs no authority
    ("memory", {"action": "save", "entry": ENTRY}),
    ("channel", {"action": "reply", "role": "designer", "name": "soporte", "q": 1, "by": "designer", "text": "H7"}),
    ("channel", {"action": "ask", "role": "drafter", "name": "soporte", "text": "¿Ajuste?"}),
    ("op", {"name": "box", "by": "Ana", "reason": "x"}),  # another tool
]
INCOMPLETE: list[tuple[str, dict[str, Any]]] = [
    # an order that lacks his name or his words is not put to him: the server refuses its shape
    ("drawing", {"action": "approve", "sheet": "Plano"}),
    ("drawing", {"action": "approve", "sheet": "Plano", "by": "Ana"}),
    ("drawing", {"action": "answer", "id": "req.thread", "text": "no aplica: x", "by": "Ana"}),
    ("drawing", {"action": "compose", "decisions": {}, "recompose": True}),
    ("memory", {"action": "approve", "id": "x"}),
    ("channel", {"action": "decide", "role": "drafter", "name": "soporte", "q": 1}),
]


@pytest.fixture
def engineer(settings: Settings) -> Engineer:
    assert settings.home is not None
    return Engineer(settings.home, settings.workspace)


def decision(answer: dict[str, Any] | None) -> tuple[str, str]:
    assert answer is not None
    said = answer["hookSpecificOutput"]
    assert said["hookEventName"] == "PreToolUse"
    return said["permissionDecision"], said["permissionDecisionReason"]


def hook(engineer: Engineer, tool: str, arguments: dict[str, Any], call_id: str = "toolu_01") -> dict[str, Any] | None:
    return engineer_orders.decide(engineer.payload(tool, arguments, call_id), engineer.environ())


def tickets(settings: Settings) -> list[str]:
    folder = engineer_orders.orders_dir(settings.home) / "tickets"
    return sorted(p.stem for p in folder.glob("*.json")) if folder.is_dir() else []


# --- which calls carry an order ----------------------------------------------------------------------------------------


@pytest.mark.parametrize(("tool", "arguments", "kind"), ORDERS)
def test_a_call_that_records_the_engineers_word_is_an_order(tool: str, arguments: dict[str, Any], kind: str) -> None:
    order = engineer_orders.order_of(tool, arguments)
    assert order is not None and order.kind == kind
    assert order.closes("es") and order.closes("en")


@pytest.mark.parametrize(("tool", "arguments"), [*NOT_ORDERS, *INCOMPLETE])
def test_a_call_of_the_agents_own_is_not_an_order(tool: str, arguments: dict[str, Any]) -> None:
    assert engineer_orders.order_of(tool, arguments) is None


def test_the_order_says_his_phrase_and_what_it_closes() -> None:
    order = engineer_orders.order_of("drawing", ORDERS[0][1])
    assert order is not None and order.words == "la rosca la hace el cliente"
    assert engineer_orders.question(order, "es") == (
        '¿Es orden tuya? Frase: "la rosca la hace el cliente". Cierra: req.thread («no aplica: x»).'
    )
    assert engineer_orders.question(order, "en").startswith('Is this your order? Phrase: "la rosca la hace el cliente". Closes: req.thread')
    report = engineer_orders.order_of("drawing", ORDERS[2][1])
    assert report is not None and "1 observaciones: missing: falta el número" in report.closes("es") and "la hoja Plano" in report.closes("es")
    assert "(ninguna)" in engineer_orders.question(report, "es")


# --- the hook: pass, ask ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("tool", "arguments"), [*NOT_ORDERS, *INCOMPLETE])
def test_a_call_without_an_order_passes_untouched(engineer: Engineer, settings: Settings, tool: str, arguments: dict[str, Any]) -> None:
    payload = json.dumps(engineer.payload(tool, arguments, "toolu_01")).encode("utf-8")
    out = io.StringIO()
    assert engineer_orders.main(io.BytesIO(payload), out, engineer.environ()) == 0
    assert out.getvalue() == "" and tickets(settings) == []


@pytest.mark.parametrize(("tool", "arguments", "kind"), ORDERS)
def test_an_order_is_put_to_the_engineer_with_its_ticket(engineer: Engineer, settings: Settings, tool: str, arguments: dict[str, Any], kind: str) -> None:
    verdict, shown = decision(hook(engineer, tool, arguments, "toolu_77"))
    assert verdict == "ask" and shown.startswith("¿Es orden tuya? Frase: ") and "Cierra: " in shown
    assert tickets(settings) == ["toolu_77"]
    ticket = json.loads((engineer_orders.orders_dir(settings.home) / "tickets" / "toolu_77.json").read_text(encoding="utf-8"))
    assert ticket["digest"] == engineer_orders.digest(tool, arguments) and ticket["kind"] == kind


def test_the_dialog_is_in_the_language_of_the_project(engineer: Engineer, settings: Settings) -> None:
    design_memory.write_language(settings.workspace, "en")
    _, shown = decision(hook(engineer, "memory", APPROVE))
    assert shown == 'Is this your order? Phrase: "apruébala como regla". Closes: the approval of memory entry ajuste-ejes.'


def test_the_hook_prints_ascii_json_and_never_fails(engineer: Engineer) -> None:
    out = io.StringIO()
    payload = json.dumps(engineer.payload("memory", APPROVE, "toolu_01"), ensure_ascii=False).encode("utf-8")
    assert engineer_orders.main(io.BytesIO(payload), out, engineer.environ()) == 0
    assert out.getvalue().isascii() and decision(json.loads(out.getvalue()))[0] == "ask"
    for broken in (b"", b"not json", b"[]", b'{"hook_event_name": "PreToolUse"}', b'{"hook_event_name": "Stop"}'):
        out = io.StringIO()
        assert engineer_orders.main(io.BytesIO(broken), out, engineer.environ()) == 0 and out.getvalue() == ""


def test_the_hook_loads_without_the_servers_libraries() -> None:
    """It runs before every drawing, memory and channel call: it must cost no more than starting Python."""
    code = "import sys; import cotaforge_mcp.engineer_orders; print(sorted(m for m in ('pydantic', 'yaml', 'mcp') if m in sys.modules))"
    done = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=60, check=True)  # noqa: S603
    assert done.stdout.strip() == "[]"


# --- the hook: he sees everything he confirms -------------------------------------------------------------------------


def shown_for(engineer: Engineer, tool: str, arguments: dict[str, Any]) -> str:
    verdict, text = decision(hook(engineer, tool, arguments))
    assert verdict == "ask", text
    return text


def test_the_dialog_shows_every_remark_of_his_report_whole(engineer: Engineer) -> None:
    items = [{"kind": "missing", "text": f"punto {n} " + "largo " * 20 + f"fin{n}"} for n in range(1, 10)]  # 9 remarks of 130 characters
    shown = shown_for(engineer, "drawing", {"action": "review_report", "sheet": "Plano", "by": "engineer", "items": items})
    for n in range(1, 10):
        assert f"fin{n}" in shown, f"remark {n} was cut or left out"
    assert "9 observaciones" in shown


def test_the_dialog_shows_an_answer_and_the_words_whole(engineer: Engineer) -> None:
    said = "no aplica: " + "porque el cliente rosca la pieza y " * 12 + "fin-de-la-respuesta"  # about 430 characters
    arguments = {"action": "answer", "id": "req.thread", "text": said, "by": "Ana", "reason": "dijo " + "muchas cosas " * 10 + "fin-de-sus-palabras"}
    shown = shown_for(engineer, "drawing", arguments)
    assert "fin-de-la-respuesta" in shown and "fin-de-sus-palabras" in shown


def test_an_order_too_long_to_show_whole_is_refused_so_the_agent_splits_it(engineer: Engineer, settings: Settings) -> None:
    items = [{"kind": "missing", "text": f"punto {n} " + "largo " * 20} for n in range(1, 20)]
    verdict, reason = decision(hook(engineer, "drawing", {"action": "review_report", "sheet": "Plano", "by": "engineer", "items": items}))
    assert verdict == "deny" and "NOT RECORDED" in reason and "too long" in reason
    assert "append=true" in reason and "replaces his report" in reason  # a report is split with append, not "split it"
    assert tickets(settings) == [] and engineer_orders.pending(settings.home, str(settings.workspace)) == []  # nothing to wait for: it must be split


def test_the_other_orders_too_long_keep_the_generic_refusal(engineer: Engineer) -> None:
    verdict, reason = decision(hook(engineer, "memory", {**APPROVE, "text": "apruébala " * 200}))
    assert verdict == "deny" and "Split it" in reason and "append" not in reason


def test_a_piece_of_a_report_says_in_the_dialog_that_it_adds_to_what_is_recorded(engineer: Engineer) -> None:
    arguments = {"action": "review_report", "sheet": "Plano", "by": "engineer", "items": [{"kind": "missing", "text": "falta el número"}], "append": True}
    shown = shown_for(engineer, "drawing", arguments)
    assert "se AÑADE a lo ya registrado" in shown and "1 observaciones más: missing: falta el número" in shown
    assert "AÑADE" not in shown_for(engineer, "drawing", {**arguments, "append": False})
    assert "AÑADE" not in shown_for(engineer, "drawing", {k: v for k, v in arguments.items() if k != "append"})
    design_memory.write_language(engineer.workspace, "en")
    assert "ADDED to what is already recorded, 1 more remarks" in shown_for(engineer, "drawing", arguments)
    assert engineer_orders.digest("drawing", arguments) != engineer_orders.digest("drawing", {**arguments, "append": False})  # his OK covers append


def test_an_order_too_long_is_refused_whether_or_not_he_is_there(engineer: Engineer) -> None:
    for minutes in (0, 60):
        engineer.leaves(minutes=minutes)
        verdict, reason = decision(hook(engineer, "memory", {**APPROVE, "text": "apruébala " * 200}))
        assert verdict == "deny" and "Split it" in reason


# --- the hook: nobody is there to ask ---------------------------------------------------------------------------------------


def test_when_the_engineer_is_away_the_order_is_denied_at_once_and_kept_for_him(engineer: Engineer, settings: Settings) -> None:
    engineer.leaves(minutes=30)
    verdict, reason = decision(hook(engineer, "memory", APPROVE))
    assert verdict == "deny" and reason.startswith("el ingeniero no está: queda pendiente para cuando vuelva")
    assert "NOT RECORDED" in reason and "go on with every step" in reason
    assert tickets(settings) == []  # no ticket: the server would refuse the call as well
    (waiting,) = engineer_orders.pending(settings.home, str(settings.workspace))
    assert waiting["question"].startswith('¿Es orden tuya? Frase: "apruébala como regla"')
    hook(engineer, "memory", APPROVE)  # the agent insists: still one point, not two
    assert len(engineer_orders.pending(settings.home, str(settings.workspace))) == 1


def test_he_counts_as_present_for_the_minutes_the_brief_says(engineer: Engineer, settings: Settings) -> None:
    engineer.leaves(minutes=1.5)
    assert decision(hook(engineer, "memory", APPROVE))[0] == "ask"  # 2 minutes by default: a dialog has no timeout
    engineer.leaves(minutes=2.5)
    assert decision(hook(engineer, "memory", APPROVE))[0] == "deny"
    (settings.workspace / "brief.yaml").write_text("order_presence_min: 5\n", encoding="utf-8")
    engineer.leaves(minutes=4.5)
    assert decision(hook(engineer, "memory", APPROVE))[0] == "ask"
    engineer.leaves(minutes=5.5)
    assert decision(hook(engineer, "memory", APPROVE))[0] == "deny"


@pytest.mark.parametrize(
    ("written", "minutes"),
    [("5", 5.0), ("0.5", 0.5), ("10", 10.0), ("99999", 10.0), ("11", 10.0), (".inf", 2.0), ("0", 2.0), ("-3", 2.0), ("true", 2.0), ("'much'", 2.0), ("[1]", 2.0)],
)
def test_the_presence_window_is_at_most_ten_minutes_and_an_invalid_one_is_the_default(written: str, minutes: float) -> None:
    """The agent can edit the brief: with 99999 a dialog would open with nobody there and the session would hang."""
    import yaml

    brief = yaml.safe_load(f"order_presence_min: {written}\n")
    assert engineer_orders.presence_minutes(brief) == minutes
    assert engineer_orders.presence_minutes({}) == engineer_orders.PRESENCE_MIN_DEFAULT == 2.0


def test_a_huge_presence_window_in_the_brief_does_not_keep_him_present_for_hours(engineer: Engineer, settings: Settings) -> None:
    (settings.workspace / "brief.yaml").write_text("order_presence_min: 99999\n", encoding="utf-8")
    engineer.leaves(minutes=9)
    assert decision(hook(engineer, "memory", APPROVE))[0] == "ask"
    engineer.leaves(minutes=11)  # ten is the most, whatever the file says
    verdict, reason = decision(hook(engineer, "memory", APPROVE))
    assert verdict == "deny" and "10 min" in reason
    assert engineer_orders.pending(settings.home, str(settings.workspace))  # it waits for him instead of opening a dialog


def test_the_presence_setting_survives_a_change_of_the_brief(settings: Settings) -> None:
    (settings.workspace / "brief.yaml").write_text("order_presence_min: 30\n", encoding="utf-8")
    design_memory.save_brief(settings, "moldes", None)
    data = design_memory._read_yaml(settings.workspace / "brief.yaml")  # noqa: SLF001
    assert data["sector"] == "moldes" and data["order_presence_min"] == 30  # the file keeps what was written; the hook clamps it


@pytest.mark.parametrize("origin", ["sdk", "task_notification"])
def test_a_message_no_person_typed_does_not_make_him_present(engineer: Engineer, origin: str) -> None:
    engineer.leaves(minutes=60)
    engineer.types("apruébala como regla", origin=origin)  # a program's prompt, the result of a background task
    assert decision(hook(engineer, "memory", APPROVE))[0] == "deny"


def test_the_first_message_of_a_drawing_window_is_the_harness_s_not_his(engineer: Engineer) -> None:
    engineer.transcript.unlink()
    engineer.types(opening_prompt("soporte"))
    assert decision(hook(engineer, "memory", APPROVE))[0] == "deny"
    engineer.types("sigue")
    assert decision(hook(engineer, "memory", APPROVE))[0] == "ask"


def test_a_session_with_no_transcript_asks_nobody(engineer: Engineer) -> None:
    engineer.transcript.unlink()
    assert decision(hook(engineer, "memory", APPROVE))[0] == "deny"


@pytest.mark.parametrize("mode", ["bypassPermissions", "dontAsk", "plan", "somethingNew", None])
def test_a_mode_in_which_nobody_answers_a_dialog_is_denied_with_the_reason(engineer: Engineer, settings: Settings, mode: str | None) -> None:
    engineer.mode = mode  # type: ignore[assignment]
    verdict, reason = decision(hook(engineer, "memory", APPROVE))
    assert verdict == "deny" and "NOT RECORDED" in reason and f"permission mode {mode!r}" in reason
    assert tickets(settings) == []


@pytest.mark.parametrize("mode", ["default", "acceptEdits", "auto"])
def test_the_modes_that_show_him_the_dialog_ask(engineer: Engineer, mode: str) -> None:
    engineer.mode = mode
    assert decision(hook(engineer, "memory", APPROVE))[0] == "ask"


def test_a_call_claude_code_gave_no_id_is_denied(engineer: Engineer, settings: Settings) -> None:
    payload = engineer.payload("memory", APPROVE, "x")
    del payload["tool_use_id"]
    assert decision(engineer_orders.decide(payload, engineer.environ()))[0] == "deny" and tickets(settings) == []
    payload["tool_use_id"] = "../../outside"
    assert decision(engineer_orders.decide(payload, engineer.environ()))[0] == "deny" and tickets(settings) == []


def age(path: Path, days: float) -> None:
    """Make what ``path`` holds ``days`` older: the time inside its entries and the time of the file."""
    entries = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    for entry in entries:
        entry["at"] -= days * 86400
    path.write_text("".join(json.dumps(entry) + "\n" for entry in entries), encoding="utf-8")
    old = time.time() - days * 86400
    os.utime(path, (old, old))


def test_an_order_that_waits_expires_like_a_ticket(engineer: Engineer, settings: Settings) -> None:
    engineer.leaves(minutes=30)
    hook(engineer, "memory", APPROVE)
    path = engineer_orders.pending_path(settings.home, str(settings.workspace))
    age(path, days=engineer_orders.KEEP_DAYS - 1)
    assert len(engineer_orders.pending(settings.home, str(settings.workspace))) == 1  # still within its week
    age(path, days=2)
    assert engineer_orders.pending(settings.home, str(settings.workspace)) == []
    assert engineer.back("ya volví") is None  # an old point is not handed to the agent as if it were new


def test_the_orders_of_a_space_nobody_came_back_to_are_swept(engineer: Engineer, settings: Settings) -> None:
    folder = engineer_orders.orders_dir(settings.home) / "pending"
    folder.mkdir(parents=True)
    forgotten = folder / "0123456789abcdef01234567.jsonl"
    forgotten.write_text(json.dumps({"key": "k", "at": time.time(), "question": "?"}) + "\n", encoding="utf-8")
    age(forgotten, days=engineer_orders.KEEP_DAYS + 1)
    engineer.leaves(minutes=30)
    hook(engineer, "memory", APPROVE)  # another order waits: the old file goes
    assert not forgotten.exists()
    assert len(engineer_orders.pending(settings.home, str(settings.workspace))) == 1  # the live one stays


# --- the command that starts the hook ------------------------------------------------------------------------------------


@pytest.mark.parametrize(("tool", "arguments", "kind"), ORDERS)
def test_every_kind_of_order_has_a_marker_the_prefilter_looks_for(tool: str, arguments: dict[str, Any], kind: str) -> None:
    """The shell prefilter lists the markers of ``order_of``; a kind of order without one would never reach the hook."""
    text = json.dumps({"tool_name": f"mcp__cad__{tool}", "tool_input": arguments}, separators=(",", ":"), ensure_ascii=False)
    assert any(marker in text for marker in engineer_orders.ORDER_MARKERS), kind


@pytest.mark.skipif(shutil.which("sh") is None, reason="the hook command is for sh and Git Bash")
def test_the_prefilter_starts_the_hook_only_for_a_call_that_may_carry_an_order(tmp_path: Path) -> None:
    started = tmp_path / "started"
    command = cli.prefilter(f"cat > {shlex.quote(started.as_posix())}")

    def run(tool: str, arguments: dict[str, Any]) -> str:
        line = json.dumps({"hook_event_name": "PreToolUse", "tool_name": f"mcp__cad__{tool}", "tool_input": arguments}, separators=(",", ":"), ensure_ascii=False)
        done = subprocess.run(["sh", "-c", command], input=line.encode("utf-8"), capture_output=True, timeout=60, check=False)  # noqa: S603, S607
        assert done.returncode == 0 and done.stdout == b"", done
        return line

    plain = [  # the calls of the agent's own work, by far the most of them
        ("drawing", {"action": "add", "items": [{"type": "note", "text": "x"}]}),
        ("drawing", {"action": "export"}),
        ("drawing", {"action": "answer", "id": "q.callouts", "text": "no aplica: un solo tipo de agujero"}),
        ("memory", {"action": "save", "entry": ENTRY}),
        ("memory", {"action": "brief"}),
        ("memory", {"action": "answer", "id": "x", "text": "applies: here"}),
        ("channel", {"action": "ask", "role": "drafter", "name": "soporte", "text": "¿Ajuste?"}),
    ]
    for tool, arguments in plain:
        assert engineer_orders.order_of(tool, arguments) is None
        run(tool, arguments)
        assert not started.exists(), (tool, arguments)
    for tool, arguments, _kind in ORDERS:
        line = run(tool, arguments)
        assert started.read_text(encoding="utf-8") == line + "\n"  # handed on unchanged
        started.unlink()


# --- when he writes again -------------------------------------------------------------------------------------------------


def test_what_waited_is_handed_over_once_when_he_writes_again(engineer: Engineer, settings: Settings) -> None:
    assert engineer.back("hola") is None  # nothing waits
    engineer.leaves(minutes=45)
    hook(engineer, "memory", APPROVE)
    hook(engineer, "drawing", ORDERS[6][1])
    notification = {"hook_event_name": "UserPromptSubmit", "cwd": str(settings.workspace), "prompt": "<task-notification>\n<status>completed</status>"}
    assert engineer_orders.back(notification, engineer.environ()) is None  # a background task ended: he is not back
    assert len(engineer_orders.pending(settings.home, str(settings.workspace))) == 2
    context = engineer.back("ya volví")
    assert context is not None and "2 order(s)" in context and "ONE short message" in context
    assert '1. ¿Es orden tuya? Frase: "apruébala como regla"' in context and '2. ¿Es orden tuya? Frase: "apruebo el plano"' in context
    assert engineer.back("¿qué más?") is None and engineer_orders.pending(settings.home, str(settings.workspace)) == []
    assert decision(hook(engineer, "memory", APPROVE))[0] == "ask"  # he is here now: the dialog


def test_an_order_he_is_asked_about_no_longer_waits(engineer: Engineer, settings: Settings) -> None:
    engineer.leaves(minutes=45)
    hook(engineer, "memory", APPROVE)
    engineer.types("ya volví")
    assert decision(hook(engineer, "memory", APPROVE))[0] == "ask"
    assert engineer_orders.pending(settings.home, str(settings.workspace)) == []


def test_the_hook_prints_what_waited_as_context_for_the_agent(engineer: Engineer, settings: Settings) -> None:
    engineer.leaves(minutes=45)
    hook(engineer, "memory", APPROVE)
    out = io.StringIO()
    payload = {"hook_event_name": "UserPromptSubmit", "cwd": str(settings.workspace), "prompt": "ya volví"}
    assert engineer_orders.main(io.BytesIO(json.dumps(payload).encode("utf-8")), out, engineer.environ()) == 0
    said = json.loads(out.getvalue())["hookSpecificOutput"]
    assert said["hookEventName"] == "UserPromptSubmit" and "apru" in said["additionalContext"] and out.getvalue().isascii()


# --- the server ---------------------------------------------------------------------------------------------------------


def saved(call: Caller) -> None:
    call.ok("memory", action="save", entry=ENTRY)


def test_an_order_the_engineer_accepts_in_the_dialog_is_recorded(call: Caller, settings: Settings) -> None:
    saved(call)
    done = call.ok("memory", action="approve", id="ajuste-ejes", by="Ana", text=call.says("apruébala como regla"))
    assert done.startswith("approved: ajuste-ejes in project memory by Ana (apruébala como regla)")
    assert call.engineer.dialogs == ['¿Es orden tuya? Frase: "apruébala como regla". Cierra: la aprobación de la entrada de memoria ajuste-ejes.']
    assert tickets(settings) == []  # used up


def test_an_order_he_rejects_never_reaches_the_server(call: Caller) -> None:
    saved(call)
    refused = call.err("memory", **APPROVE)  # he never said it: he rejects the dialog
    assert refused == REJECTED and len(call.engineer.dialogs) == 1
    assert "status: proposed" in call.ok("memory", action="get", id="ajuste-ejes")


def test_an_order_that_did_not_go_through_the_dialog_is_refused(session: Any, settings: Settings) -> None:
    call_tool(session, "memory", {"action": "save", "entry": ENTRY})
    for call_id in (None, "toolu_invented"):  # a client that sends no id; a space without the hook: no ticket
        reply = call_tool(session, "memory", APPROVE, call_id=call_id)
        assert reply.is_error and "order_not_confirmed" in reply.text and "nothing was changed" in reply.text
    assert "cotaforge init" in reply.text
    assert "status: proposed" in call_tool(session, "memory", {"action": "get", "id": "ajuste-ejes"}).text


def test_a_ticket_is_of_one_call_and_of_exactly_its_arguments(session: Any, engineer: Engineer) -> None:
    call_tool(session, "memory", {"action": "save", "entry": ENTRY})
    assert decision(hook(engineer, "memory", APPROVE, "toolu_a"))[0] == "ask"  # he accepts THIS call
    other = {**APPROVE, "text": "apruébala para todos mis diseños", "scope": "user"}
    assert "order_not_confirmed" in call_tool(session, "memory", other, call_id="toolu_a").text  # not what he was shown
    assert "order_not_confirmed" in call_tool(session, "memory", APPROVE, call_id="toolu_b").text  # not the call he was shown
    assert call_tool(session, "memory", APPROVE, call_id="toolu_a").text.startswith("approved: ajuste-ejes")
    again = call_tool(session, "memory", APPROVE, call_id="toolu_a")  # the ticket was used up
    assert again.is_error and "order_not_confirmed" in again.text


@pytest.mark.parametrize(("tool", "arguments", "kind"), ORDERS)
def test_each_place_that_takes_the_engineers_word_refuses_a_call_he_did_not_confirm(settings: Settings, tool: str, arguments: dict[str, Any], kind: str) -> None:
    session = make_session(settings, DrawingFake())
    reply = call_tool(session, tool, arguments, call_id="toolu_none")
    assert reply.is_error and "order_not_confirmed" in reply.text and kind in reply.text, reply.text


def test_a_place_the_gate_does_not_know_still_records_nothing_unconfirmed() -> None:
    with pytest.raises(ToolError) as caught:
        engineer_orders.confirmed("a new kind of order")
    assert caught.value.code == "order_not_confirmed"
    order = engineer_orders.order_of("memory", APPROVE)
    with engineer_orders.confirming(order):
        assert engineer_orders.confirmed("the approval") is order
    with pytest.raises(ToolError):
        engineer_orders.confirmed("the approval")


def test_an_incomplete_order_is_refused_by_the_server_for_its_shape(call: Caller) -> None:
    saved(call)
    assert "approve is the engineer's gate" in call.err("memory", action="approve", id="ajuste-ejes")
    assert call.engineer.dialogs == []  # he was not bothered with it


def test_while_he_is_away_the_order_waits_and_the_work_goes_on(call: Caller, settings: Settings) -> None:
    saved(call)
    call.engineer.leaves(minutes=40)
    denied = call.err("memory", **APPROVE)
    assert denied.startswith("el ingeniero no está: queda pendiente para cuando vuelva") and call.engineer.dialogs == []
    assert "status: proposed" in call.ok("memory", action="get", id="ajuste-ejes")  # not recorded
    call.ok("memory", action="index")  # every other step goes on
    context = call.engineer.back("ya estoy")
    assert context is not None and "apruébala como regla" in context
    call.engineer.agreed.append("apruébala como regla")  # he answers the question the agent asks him
    assert call.ok("memory", **APPROVE).startswith("approved: ajuste-ejes")


def test_the_server_reads_the_call_id_claude_code_sends() -> None:
    meta = types.SimpleNamespace(model_dump=lambda: {"progressToken": 2, "claudecode/toolUseId": "toolu_01"})
    assert call_id_of(meta) == "toolu_01"
    assert call_id_of(None) is None
    assert call_id_of(types.SimpleNamespace(model_dump=lambda: {"progressToken": 2})) is None


# --- cotaforge init ---------------------------------------------------------------------------------------------------


def orders_hooks(settings: dict[str, Any], event: str) -> list[dict[str, Any]]:
    return [e for e in settings["hooks"][event] if any("engineer_orders" in h["command"] for h in e["hooks"])]


def test_init_writes_the_orders_hook_on_its_two_events_once(tmp_path: Path) -> None:
    folder, home = tmp_path / "space", tmp_path / "home"
    for _ in range(2):
        cli.init_space(folder, home=home, interpreter="/usr/bin/python3", adapter="fake")
    settings = json.loads((folder / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    (before,) = orders_hooks(settings, "PreToolUse")
    assert before["matcher"] == "mcp__cad__(drawing|memory|channel)"
    assert before["hooks"] == [{"type": "command", "command": cli.prefilter("/usr/bin/python3 -m cotaforge_mcp.engineer_orders"), "timeout": 10}]
    (prompt,) = orders_hooks(settings, "UserPromptSubmit")
    assert "matcher" not in prompt  # a message starts one turn: it needs no look first
    assert prompt["hooks"] == [{"type": "command", "command": "/usr/bin/python3 -m cotaforge_mcp.engineer_orders", "timeout": 10}]
    assert len(settings["hooks"]["PreToolUse"]) == 2  # with the run_native hook


def test_init_keeps_the_hooks_of_the_user(tmp_path: Path) -> None:
    folder, home = tmp_path / "space", tmp_path / "home"
    (folder / ".claude").mkdir(parents=True)
    mine = {"hooks": {"UserPromptSubmit": [{"hooks": [{"type": "command", "command": "echo mine"}]}], "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo bash"}]}]}}
    (folder / ".claude" / "settings.local.json").write_text(json.dumps(mine), encoding="utf-8")
    cli.init_space(folder, home=home, interpreter="/usr/bin/python3", adapter="fake")
    settings = json.loads((folder / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert settings["hooks"]["UserPromptSubmit"][0] == mine["hooks"]["UserPromptSubmit"][0]
    assert settings["hooks"]["PreToolUse"][0] == mine["hooks"]["PreToolUse"][0]
    assert len(orders_hooks(settings, "UserPromptSubmit")) == 1 and len(orders_hooks(settings, "PreToolUse")) == 1


def test_a_caller_that_gives_only_the_run_native_hook_gets_the_orders_hook_of_the_same_interpreter() -> None:
    command = cli.hook_command("/opt/my tools/python", windows=False, save_methods=("SaveAs3",))
    settings = cli.build_settings({}, {}, [], command)
    (before,) = orders_hooks(settings, "PreToolUse")
    assert before["hooks"][0]["command"] == cli.prefilter("'/opt/my tools/python' -m cotaforge_mcp.engineer_orders")


def test_nothing_in_the_template_answers_the_dialog_for_him() -> None:
    """The template allows every tool of the ``cad`` server; a hook's ``ask`` still shows the dialog (measured on Claude
    Code 2.1.325, ``default`` and ``auto``). What would answer it for him is a permission mode set by the design space."""
    template = json.loads((cli.template_root() / ".claude" / "settings.local.json").read_text(encoding="utf-8"))
    assert "defaultMode" not in template.get("permissions", {}) and "hooks" not in template
    assert not any("engineer_orders" in rule for rule in template["permissions"]["allow"])
