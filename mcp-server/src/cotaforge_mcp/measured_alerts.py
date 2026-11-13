"""The measured design alerts: a violation the harness measured on the model is not waved away with a sentence.

An alert kept only in memory is a prompt, and a prompt guarantees nothing. Some memory entries are activated by a
MEASUREMENT of the model (the sharp inside corners of a milled pocket, a bearing on a shaft run much longer than
itself): each has a detector (``pocket_corners``, ``bearing_seats``) that reads the geometry. This module is the one
rule for all of them, and the place a new one is registered (``detectors``):

* a measured entry is closed in only two ways: the violation is FIXED (the harness measures again and the trigger no
  longer fires) or the engineer ORDERED it kept (``by`` his name, and the ``text`` of the answer his words, quoted).
  ``does not apply: why`` from the agent is refused while the trigger fires, and so is any other answer: what closes it
  is the model, or him;
* an entry no detector measures (it is activated by an op or a role) keeps its plain answers;
* the order covers the violations that were measured when it was given (what they said is kept with it): a new one, or
  the same one changed, is open again;
* a drawing of a part with an open violation cannot be exported (a row of ``drawing_requirements``, the fact
  ``MEASURED_FACT``).

The reading is measured now, every time it is asked for (nothing is remembered of what fired before: a violation that
was fixed is gone, one that nobody saw is found), once per tool call. A reading that fails counts as nothing found: it
never breaks the call it comes with.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from cotaforge_mcp.session import Session

MEASURED_FACT = "measured_alerts_open"
"""The fact of a sheet's requirements: how many measured violations are open on the model (the server's own)."""
SEPARATOR = "\n"


@dataclass(frozen=True)
class Detector:
    """A memory entry activated by a measurement, and how to measure it now: the ``memory:`` lines that fire."""

    entry_id: str
    lines: Callable[["Session"], list[str]]


def detectors() -> tuple[Detector, ...]:
    """Every measured alert. A new one is added here and is then held by the review, the answers and the export."""
    from cotaforge_mcp import bearing_seats, pocket_corners  # they import design_memory, which imports this module

    return (
        Detector(bearing_seats.ENTRY_ID, bearing_seats.current_lines),
        Detector(pocket_corners.ENTRY_ID, pocket_corners.current_lines),
    )


def is_measured(entry_id: str) -> bool:
    return any(d.entry_id == entry_id for d in detectors())


def live(session: "Session") -> dict[str, list[str]]:
    """The measured violations firing on the model now, by entry id (an entry that does not fire is not there)."""
    if session.mem.measured is not None:  # read already in this tool call (the model cannot change inside one)
        return {entry: list(lines) for entry, lines in session.mem.measured.items()}
    found: dict[str, list[str]] = {}
    for detector in detectors():
        try:
            lines = detector.lines(session)
        except Exception:  # noqa: BLE001 - a reading that fails must never break the call it comes with
            lines = []
        if lines:
            found[detector.entry_id] = lines
    session.mem.measured = found
    return {entry: list(lines) for entry, lines in found.items()}


def covered(answer: dict[str, Any] | None, lines: list[str]) -> bool:
    """The engineer's order in ``answer`` was given for every one of ``lines`` (it names what was measured then)."""
    if not answer or not answer.get("ordered_by"):
        return False
    given = set(str(answer.get("measured", "")).split(SEPARATOR))
    return all(line in given for line in lines)


def open_now(session: "Session") -> dict[str, list[str]]:
    """The measured violations that fire now and that no order of the engineer covers."""
    answers = session.mem.answers
    return {entry: lines for entry, lines in live(session).items() if not covered(answers.get(entry), lines)}


def count_open(session: "Session") -> int:
    return sum(len(lines) for lines in open_now(session).values())


REFUSAL = (
    "{entry} was MEASURED on the model and still fires: {lines}. 'does not apply' (or any answer) is not accepted on "
    "the agent's word. Fix it in the model (the harness measures again and the alert is gone) or, if the ENGINEER "
    "orders it kept, answer again with by=<his name> and text='does not apply: <his words, quoted>'"
)
