"""The rule catalog says what the code does: every finding the review can raise belongs to a rule, every check a rule
names exists, the weights agree with what holds the export, and the debt only shrinks."""

import ast
import re
from pathlib import Path

from cotaforge.drawing import catalog

SRC = Path(__file__).resolve().parents[3] / "src" / "cotaforge"
WORKER = SRC / "adapters" / "freecad" / "worker"
# Rules with no check. An owner rule enters the catalog the same day, with its check or marked as debt (construction rule 1 of
# ARQUITECTURA-planos), so the ceiling rises only by owner rules; any other change only lowers it, when a rule gets its check.
# 25: the 21 of the plan (+3 that lived only in the prompt) and the 4 owner rules of the shaft and keyway sheet; the real count of rules with no check.
DEBT_CEILING = 25

FINDING = re.compile(r"(?<![A-Za-z])(?:_finding|Finding)\(\s*(f?)\"([a-z_0-9{}]+)\"(?:\s*if [^,]+ else \"([a-z_0-9]+)\")?")  # noqa: E501


def _worker_text() -> str:
    return "\n".join(p.read_text(encoding="utf-8") for p in sorted(WORKER.glob("*.py")))


def _raised_codes() -> set[str]:
    codes: set[str] = set()
    for fstring, code, other in FINDING.findall(_worker_text()):
        if fstring:  # notes_{code}: the notes rows, named one by one in the catalog
            continue
        codes.add(code)
        if other:
            codes.add(other)
    return codes


def _non_blocking() -> set[str]:
    tree = ast.parse((WORKER / "sheet_checks.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign) and any(getattr(t, "id", "") == "NON_BLOCKING" for t in node.targets):
            return {e.value for e in ast.walk(node.value) if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    raise AssertionError("NON_BLOCKING not found in sheet_checks.py")


def test_the_catalog_loads() -> None:
    cat = catalog.load()
    assert cat.rules and len({r.id for r in cat.rules}) == len(cat.rules)


def test_every_finding_the_review_raises_belongs_to_a_rule() -> None:
    missing = sorted(_raised_codes() - catalog.load().codes())
    assert not missing, f"findings with no rule in catalog.yaml: {missing}"


def test_every_non_blocking_code_belongs_to_a_rule() -> None:
    missing = sorted(_non_blocking() - catalog.load().codes())
    assert not missing, f"NON_BLOCKING codes with no rule in catalog.yaml: {missing}"


def test_every_check_a_rule_names_exists_or_awaits_its_merge() -> None:
    cat = catalog.load()
    text = _worker_text()
    absent = sorted(c for c in cat.codes() if f'"{c}"' not in text and not _is_notes_code(c, text))
    unexplained = [c for c in absent if c not in cat.awaiting_merge]
    assert not unexplained, f"checks named in catalog.yaml that the review does not raise: {unexplained}"


def _is_notes_code(code: str, text: str) -> bool:
    return code.startswith("notes_") and 'f"notes_{code}"' in text


def test_the_weight_agrees_with_what_holds_the_export() -> None:
    cat = catalog.load()
    non_blocking = _non_blocking()
    text = _worker_text()
    wrong = []
    for rule in cat.rules:
        present = [c for c in rule.checks if c not in cat.awaiting_merge or f'"{c}"' in text]
        if not present:
            continue
        warns = all(c in non_blocking for c in present)
        if (rule.weight == "warns") != warns:
            holds = "never hold" if warns else "hold"
            wrong.append(f"{rule.id}: weight {rule.weight}, but its checks {holds} the export")
    assert not wrong, wrong


def test_the_debt_only_shrinks() -> None:
    debt = catalog.load().debt()
    assert len(debt) <= DEBT_CEILING, f"{len(debt)} rules with no check: {[r.id for r in debt]}"


def test_a_rule_a_tool_gate_enforces_names_its_gate_and_is_not_debt() -> None:
    rule = next(r for r in catalog.load().rules if r.id == "drawing.destructive_needs_order")
    assert rule.weight == "gate" and rule.gate and not rule.checks
    assert rule not in catalog.load().debt()  # a gate refuses it: nobody has to read the sheet for it


def test_gate_and_check_do_not_mix() -> None:
    import pytest
    from pydantic import ValidationError

    base = {"id": "x.y", "step": "purpose", "text": "t", "origin": "owner"}
    with pytest.raises(ValidationError):
        catalog.Rule(**base, weight="gate")  # a gate rule names its gate
    with pytest.raises(ValidationError):
        catalog.Rule(**base, weight="blocks", checks=("a",), gate="g")  # a rule has a check or a gate, not both
    with pytest.raises(ValidationError):
        catalog.Rule(**base, weight="debt", gate="g")
    assert catalog.Rule(**base, weight="gate", gate="the tool refuses it").gate


def test_what_the_old_drafter_prompt_said_of_the_sheet_is_in_the_catalog() -> None:
    text = {r.id: " ".join(r.text.split()) for r in catalog.load().rules}
    assert "symmetry=true only if the intent file declares" in text["references.functional_datums"]
    assert "the sheet says which scheme it follows" in text["dimensions.scheme_from_function"]
    assert "Colour and fit belong to the model" in text["callouts.clear"] and "hole_dim" in text["callouts.clear"]
    assert "cannot draw a symbol for is written in a note" in text["welded.weld_symbols"]
    destructive = text["drawing.destructive_needs_order"]
    assert "fresh=true" in destructive and "remove_sheet" in destructive
