"""What the client sees while a long ``doc(action=import)`` runs: MCP progress notifications on one rising scale, with a
message that says what is going on; and the limits the engineer can set for how long an import may take."""

import json
from pathlib import Path
from typing import Any

import pytest
from cotaforge.adapters.fake import FakeAdapter
from cotaforge.contract import ProgressNotice
from mcp.shared.memory import create_connected_server_and_client_session

from cotaforge_mcp.config import ConfigError, Settings, make_adapter
from cotaforge_mcp.progress import PHASE_SPAN, TOTAL, Reporter, position
from cotaforge_mcp.server import build_server
from tests.conftest import Caller, MakeSession

SOLIDS = 5


def supplier_file(tmp_path: Path, solids: int = SOLIDS) -> Path:
    path = tmp_path / "plate.step"
    path.write_text(
        json.dumps({"solids": [{"box": [10, 10, 10], "at": [20 * i, 0, 0]} for i in range(solids)]}), encoding="utf-8"
    )
    return path


# --- the scale ------------------------------------------------------------------------------------------------------


def test_each_phase_covers_its_own_share_of_the_scale_in_the_order_the_work_happens() -> None:
    spans = list(PHASE_SPAN.values())
    assert spans[0][0] == 0.0 and spans[-1][1] == TOTAL
    assert all(a[1] == b[0] for a, b in zip(spans, spans[1:], strict=False))
    adopting = PHASE_SPAN["adopting"]
    assert position(ProgressNotice(phase="adopting", current=0, total=10)) == adopting[0]
    assert position(ProgressNotice(phase="adopting", current=5, total=10)) == pytest.approx(sum(adopting) / 2)
    assert position(ProgressNotice(phase="adopting", current=10, total=10)) == adopting[1]


def test_a_phase_without_a_count_climbs_with_the_time_but_never_reaches_the_end_of_its_share() -> None:
    first, last = PHASE_SPAN["reading"]
    spots = [position(ProgressNotice(phase="reading", elapsed_s=s)) for s in (0, 5, 60, 600, 36000)]
    assert spots == sorted(spots) and spots[0] == first
    assert all(first <= s < last for s in spots)


def test_the_reporter_only_goes_forward_even_when_a_notice_stands_still_or_goes_back() -> None:
    sent: list[tuple[float, float, str]] = []
    report = Reporter(lambda progress, total, message: sent.append((progress, total, message)))
    report(ProgressNotice(phase="adopting", current=5, total=10, message="adopting body 5/10"))
    report(ProgressNotice(phase="adopting", current=5, total=10, message="adopting body 5/10: 80 s elapsed"))
    report(ProgressNotice(phase="reading", message="a late notice of an earlier phase"))
    report.say("saving", "saving")

    numbers = [p for p, _, _ in sent]
    assert numbers == sorted(set(numbers)), numbers  # strictly rising
    assert all(total == TOTAL for _, total, _ in sent)
    assert sent[1][2].endswith("80 s elapsed") and sent[-1][2] == "saving"


# --- through the session and the server -------------------------------------------------------------------------------


def test_the_session_reports_the_phases_of_an_import_and_stops_listening_afterwards(
    call: Caller, tmp_path: Path
) -> None:
    supplier_file(tmp_path)
    heard: list[tuple[float, float, str]] = []

    lines = call.session.import_document(
        "plate.step", "plate", progress=Reporter(lambda p, t, m: heard.append((p, t, m)))
    )

    assert lines[0].startswith("document: plate (part)")
    messages = [m for _, _, m in heard]
    assert messages[0] == "reading the file"
    assert [f"adopting body {i}/{SOLIDS}" for i in range(1, SOLIDS + 1)] == [m for m in messages if "adopting" in m]
    assert messages[-2].startswith("linking the 5 bodies")
    assert messages[-1].startswith("saving plate.") and "session record" in messages[-1]
    numbers = [p for p, _, _ in heard]
    assert numbers == sorted(set(numbers)) and numbers[-1] < TOTAL
    adapter = call.session.adapter
    assert isinstance(adapter, FakeAdapter) and adapter._progress_listener is None


def test_a_failed_import_does_not_leave_the_listener_behind(call: Caller, tmp_path: Path) -> None:
    (tmp_path / "broken.step").write_text("not even json", encoding="utf-8")
    heard: list[str] = []
    with pytest.raises(Exception, match="could not be read"):
        call.session.import_document("broken.step", "broken", progress=Reporter(lambda p, t, m: heard.append(m)))
    adapter = call.session.adapter
    assert isinstance(adapter, FakeAdapter) and adapter._progress_listener is None


def test_adopting_a_file_does_not_ask_the_cad_about_each_body(call: Caller, tmp_path: Path) -> None:
    supplier_file(tmp_path, solids=12)
    adapter = call.session.adapter
    asked: list[str] = []
    original = adapter.bodies_of
    adapter.bodies_of = lambda request: (asked.append(request.feature.id), original(request))[1]  # type: ignore[method-assign]

    text = call.ok("doc", action="import", path="plate.step", name="plate")

    assert "12 bodies" in text and "adopted: " in text
    assert len(asked) <= 12  # the facts come with the import; the fake itself asks once per solid while it builds
    asked.clear()
    call.ok("doc", action="status")
    assert asked == []


async def test_the_server_sends_mcp_progress_notifications_when_the_client_asks_for_them(
    make: MakeSession, tmp_path: Path
) -> None:
    supplier_file(tmp_path)
    session = make(FakeAdapter())
    received: list[tuple[float, float | None, str | None]] = []

    async def on_progress(progress: float, total: float | None, message: str | None) -> None:
        received.append((progress, total, message))

    async with create_connected_server_and_client_session(build_server(session)) as client:
        result = await client.call_tool(
            "doc",
            {"action": "import", "path": "plate.step", "name": "plate"},
            progress_callback=on_progress,
        )

    assert not result.isError
    assert received, "no progress notification reached the client"
    numbers = [p for p, _, _ in received]
    assert numbers == sorted(set(numbers))
    assert {t for _, t, _ in received} == {TOTAL}
    messages = [m for _, _, m in received]
    assert "reading the file" in messages and f"adopting body {SOLIDS}/{SOLIDS}" in messages
    assert messages[-1].startswith("saving plate.") and "session record" in messages[-1]


async def test_a_client_that_did_not_ask_for_progress_gets_none_and_the_same_answer(
    make: MakeSession, tmp_path: Path
) -> None:
    supplier_file(tmp_path)
    session = make(FakeAdapter())
    seen: list[Any] = []

    async def message_handler(message: Any) -> None:
        seen.append(message)

    async with create_connected_server_and_client_session(
        build_server(session), message_handler=message_handler
    ) as client:
        result = await client.call_tool("doc", {"action": "import", "path": "plate.step", "name": "plate"})

    assert not result.isError and "imported: plate.step" in result.content[0].text  # type: ignore[union-attr]
    assert not any("Progress" in type(m).__name__ or "progress" in repr(m).lower() for m in seen)


# --- the limits an engineer can set -----------------------------------------------------------------------------------


def test_the_import_limits_come_from_the_environment_and_reach_the_adapter(tmp_path: Path) -> None:
    settings = Settings.from_env(
        {"COTAFORGE_ADAPTER": "freecad", "COTAFORGE_IMPORT_STALL_S": "45", "COTAFORGE_IMPORT_MAX_S": "7200.5"}
    )
    assert settings.import_stall_s == 45.0 and settings.import_max_s == 7200.5
    adapter = make_adapter(settings)
    assert adapter._import_stall_s == 45.0 and adapter._import_max_s == 7200.5  # type: ignore[attr-defined]


def test_without_the_variables_the_adapter_keeps_its_defaults() -> None:
    settings = Settings.from_env({"COTAFORGE_ADAPTER": "freecad"})
    assert settings.import_stall_s is None and settings.import_max_s is None
    adapter = make_adapter(settings)
    assert adapter._import_stall_s == 120.0 and adapter._import_max_s == 3600.0  # type: ignore[attr-defined]


@pytest.mark.parametrize("value", ["soon", "0", "-5", "nan", "inf"])
def test_a_limit_that_is_not_a_positive_number_of_seconds_is_refused_by_name(value: str) -> None:
    with pytest.raises(ConfigError, match="COTAFORGE_IMPORT_STALL_S"):
        Settings.from_env({"COTAFORGE_IMPORT_STALL_S": value})
