"""Commercial first: the conduct that buys what exists on the market, the trace it leaves and the reviewer that checks
it. The texts are read by the agent, so the tests pin the rules that must stay in them; the one behaviour of the server
that the rule leans on is that an imported (STEP) body can carry the role ``comercial``."""

import json
from pathlib import Path

from tests.conftest import Caller

REPO = Path(__file__).resolve().parents[3]
TEMPLATE = (REPO / "templates" / "design-space" / "CLAUDE.md").read_text(encoding="utf-8")
PLAN = (REPO / "plugin" / "skills" / "plan" / "SKILL.md").read_text(encoding="utf-8")
REVIEWER = (REPO / "plugin" / "agents" / "reviewer.md").read_text(encoding="utf-8")
TRACE_SECTION = "«Commercial parts»"


def test_the_template_asks_to_look_for_the_commercial_part_before_modelling_it() -> None:
    bullet = next(line for line in TEMPLATE.splitlines() if line.startswith("- Commercial first"))
    assert "`catalog`" in bullet and "the web" in bullet and bullet.index("`catalog`") < bullet.index("the web")
    for kind in ("bearings", "pillow blocks", "couplings", "seals", "retaining rings", "springs"):
        assert kind in bullet
    assert TRACE_SECTION in bullet and "assumptions-and-decisions.md" in bullet
    assert "buy" in bullet and "make and why" in bullet
    assert "STEP" in bullet and "role `comercial`" in bullet and "simplified envelope" in bullet
    assert "never from your memory" in bullet
    assert "register" in bullet and "personal data" in bullet


def test_the_template_stays_short_enough_to_be_read_every_session() -> None:
    assert len(TEMPLATE) < 14000


def test_the_skill_that_starts_a_design_gives_the_trace_format_and_the_no_3d_paths() -> None:
    section = PLAN.split("## Commercial first", 1)[1]
    assert TRACE_SECTION in section and "searched:" in section and "found:" in section and "decision:" in section
    assert "doc` with `import`" in section and "env_<part>" in section and "never from your memory" in section
    assert "register" in section and "personal data" in section and "next source" in section
    assert "Commercial first" in PLAN.split("## Commercial first", 1)[0]  # also named in the 'Always' list


def test_the_reviewer_checks_the_trace_of_every_commercial_looking_body() -> None:
    assert "assumptions-and-decisions.md" in REVIEWER and TRACE_SECTION in REVIEWER
    assert "no commercial search trace" in REVIEWER
    assert "envelope" in REVIEWER and "Read" in REVIEWER.split("---")[1]  # it can read the register


def test_an_imported_step_body_takes_the_role_comercial(call: Caller, tmp_path: Path) -> None:
    (tmp_path / "unit.step").write_text(json.dumps({"solids": [{"box": [10, 10, 10]}]}), encoding="utf-8")
    call.ok("doc", action="import", path="unit.step", name="unit")

    assert "role: box_1 = comercial" in call.ok("op", edit="box_1", params={"role": "comercial"})
    assert "roles: box_1=comercial" in call.ok("doc", action="status")


def test_a_catalog_miss_reminds_to_log_the_search() -> None:
    from cotaforge_mcp.parts_tools import NOT_ONLY_SOURCE

    assert "not the only source" in NOT_ONLY_SOURCE and TRACE_SECTION in NOT_ONLY_SOURCE
