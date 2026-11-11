"""run_checks: material, route and envelope findings, placeholders, several processes and the honest limit."""

import pytest

from cotaforge.constraints import DeclaredSet, Finding, MeasuredPart, blocking, run_checks
from tests.unit.memory.builders import capability, make_entry, make_material, make_process

LATHE = make_process("lathe", capabilities=[capability("max_diameter_mm", 200), capability("max_length_mm", 500)])


def _by_check(findings: tuple[Finding, ...], check: str) -> list[Finding]:
    return [finding for finding in findings if finding.check == check]


def _only(findings: tuple[Finding, ...], check: str) -> Finding:
    found = _by_check(findings, check)
    assert len(found) == 1, findings
    return found[0]


# ---- nothing declared -------------------------------------------------------------------------


def test_nothing_declared_gives_no_findings() -> None:
    part = MeasuredPart(material="Unobtainium", route=("magic",), bbox_extents_mm=(1e6, 1e6, 1e6))
    assert run_checks(DeclaredSet([]), part) == ()
    assert run_checks(DeclaredSet([make_entry("note")]), part) == ()


def test_only_the_declared_kind_produces_findings() -> None:
    declared = DeclaredSet([make_material("steel", name="Steel 1045")])
    findings = run_checks(declared, MeasuredPart(route=("anything",), bbox_extents_mm=(1e6, 1e6, 1e6)))
    assert [finding.check for finding in findings] == ["material"]  # processes are not declared: no route check


# ---- material ---------------------------------------------------------------------------------


def test_unassigned_material_is_unverified_when_materials_are_declared() -> None:
    declared = DeclaredSet([make_material("steel", name="Steel 1045")])
    finding = _only(run_checks(declared, MeasuredPart()), "material")
    assert finding.status == "unverified" and "Steel 1045" in finding.detail
    assert blocking([finding]) == ()


def test_declared_material_passes_by_name_or_designation() -> None:
    declared = DeclaredSet([make_material("steel", name="Steel 1045", designations=["AISI 1045"])])
    finding = _only(run_checks(declared, MeasuredPart(material="aisi1045")), "material")
    assert finding.status == "pass" and finding.entry_id == "steel" and finding.limit == "Steel 1045"


def test_undeclared_material_is_a_violation_naming_the_allowed_ones() -> None:
    declared = DeclaredSet([make_material("steel", name="Steel 1045")])
    findings = run_checks(declared, MeasuredPart(material="Aluminium 6061"))
    finding = _only(findings, "material")
    assert finding.status == "violation"
    assert finding.measured == "Aluminium 6061" and finding.limit == "Steel 1045"
    assert blocking(findings) == (finding,)


# ---- route ------------------------------------------------------------------------------------


def test_route_subset_passes_and_outside_route_is_a_violation() -> None:
    declared = DeclaredSet([LATHE])
    assert _only(run_checks(declared, MeasuredPart(route=("lathe",))), "route").status == "pass"
    finding = _only(run_checks(declared, MeasuredPart(route=("lathe", "foundry"))), "route")
    assert finding.status == "violation" and "foundry" in finding.detail
    assert finding.limit == "lathe"


def test_empty_route_is_unverified_and_skips_the_envelope() -> None:
    findings = run_checks(DeclaredSet([LATHE]), MeasuredPart(bbox_extents_mm=(1000, 900, 800)))
    assert [(finding.check, finding.status) for finding in findings] == [("route", "unverified")]


# ---- envelope: scalar limits ------------------------------------------------------------------


def test_envelope_within_the_limits_passes_with_provenance() -> None:
    findings = run_checks(DeclaredSet([LATHE]), MeasuredPart(route=("lathe",), bbox_extents_mm=(100, 40, 38)))
    diameter = _only(findings, "envelope.max_diameter_mm")
    length = _only(findings, "envelope.max_length_mm")
    assert diameter.status == "pass" and diameter.measured == 40 and diameter.limit == 200
    assert length.status == "pass" and length.measured == 100 and length.limit == 500
    assert diameter.entry_id == "lathe" and diameter.reference == "Tool table 2026-03"
    assert blocking(findings) == ()


def test_diameter_over_the_limit_is_a_violation() -> None:
    # an axis along the longest extent (400): diameter is the larger of the other two (250)
    findings = run_checks(DeclaredSet([LATHE]), MeasuredPart(route=("lathe",), bbox_extents_mm=(250, 400, 60)))
    diameter = _only(findings, "envelope.max_diameter_mm")
    assert diameter.status == "violation" and diameter.measured == 250 and diameter.limit == 200
    assert diameter.entry_id == "lathe" and diameter.reference == "Tool table 2026-03"
    assert _only(findings, "envelope.max_length_mm").status == "pass"
    assert blocking(findings) == (diameter,)


def test_length_over_the_limit_is_a_violation() -> None:
    findings = run_checks(DeclaredSet([LATHE]), MeasuredPart(route=("lathe",), bbox_extents_mm=(30, 700, 20)))
    length = _only(findings, "envelope.max_length_mm")
    assert length.status == "violation" and length.measured == 700 and length.limit == 500


def test_a_limit_met_exactly_or_within_measurement_noise_passes() -> None:
    findings = run_checks(DeclaredSet([LATHE]), MeasuredPart(route=("lathe",), bbox_extents_mm=(200.0000001, 500, 40)))
    assert _only(findings, "envelope.max_diameter_mm").status == "pass"
    assert _only(findings, "envelope.max_length_mm").status == "pass"


def test_ambiguous_axis_is_unverified_with_the_explanation() -> None:
    findings = run_checks(DeclaredSet([LATHE]), MeasuredPart(route=("lathe",), bbox_extents_mm=(300, 295, 290)))
    for name in ("max_diameter_mm", "max_length_mm"):
        finding = _only(findings, f"envelope.{name}")
        assert finding.status == "unverified" and "within 5%" in finding.detail and "axis" in finding.detail
        assert finding.measured is None
    assert blocking(findings) == ()


def test_extents_just_outside_the_axis_tolerance_are_checked() -> None:
    findings = run_checks(DeclaredSet([LATHE]), MeasuredPart(route=("lathe",), bbox_extents_mm=(300, 285, 280)))
    assert _only(findings, "envelope.max_diameter_mm").status == "violation"  # 285 > 200; 300 / 280 differ by 6.7%


def test_extents_may_come_in_any_order() -> None:
    sorted_first = run_checks(DeclaredSet([LATHE]), MeasuredPart(route=("lathe",), bbox_extents_mm=(400, 250, 60)))
    shuffled = run_checks(DeclaredSet([LATHE]), MeasuredPart(route=("lathe",), bbox_extents_mm=(60, 400, 250)))
    assert sorted_first == shuffled


def test_missing_bounding_box_is_unverified_not_approximated() -> None:
    findings = run_checks(DeclaredSet([LATHE]), MeasuredPart(route=("lathe",)))
    diameter = _only(findings, "envelope.max_diameter_mm")
    assert diameter.status == "unverified" and "no bounding box" in diameter.detail
    assert blocking(findings) == ()


# ---- envelope: build volume -------------------------------------------------------------------

PRINTER = make_process("printer", tag="fdm", capabilities=[capability("build_volume_mm", [220, 220, 250])])


def test_build_volume_compares_sorted_extents_with_sorted_limits() -> None:
    declared = DeclaredSet([PRINTER])
    inside = run_checks(declared, MeasuredPart(route=("printer",), bbox_extents_mm=(240, 100, 200)))
    finding = _only(inside, "envelope.build_volume_mm")
    assert finding.status == "pass" and finding.measured == "240 x 200 x 100" and finding.limit == "250 x 220 x 220"
    outside = run_checks(declared, MeasuredPart(route=("printer",), bbox_extents_mm=(260, 100, 100)))
    assert _only(outside, "envelope.build_volume_mm").status == "violation"
    second_axis = run_checks(declared, MeasuredPart(route=("printer",), bbox_extents_mm=(240, 230, 100)))
    assert _only(second_axis, "envelope.build_volume_mm").status == "violation"  # 230 > 220


def test_build_volume_has_no_axis_ambiguity() -> None:
    findings = run_checks(DeclaredSet([PRINTER]), MeasuredPart(route=("printer",), bbox_extents_mm=(300, 299, 298)))
    assert _only(findings, "envelope.build_volume_mm").status == "violation"


# ---- placeholders -----------------------------------------------------------------------------


def test_placeholder_is_pending_and_never_blocks() -> None:
    pending = make_process("big-lathe", capabilities=[capability("max_diameter_mm", 1, placeholder=True)])
    findings = run_checks(DeclaredSet([pending]), MeasuredPart(route=("big-lathe",), bbox_extents_mm=(900, 800, 800)))
    finding = _only(findings, "envelope.max_diameter_mm")
    assert finding.status == "unverified" and finding.detail == "placeholder value, not measured"
    assert finding.entry_id == "big-lathe"
    assert blocking(findings) == ()


def test_placeholder_is_reported_even_without_a_bounding_box() -> None:
    pending = make_process("big-lathe", capabilities=[capability("max_diameter_mm", 1, placeholder=True)])
    findings = run_checks(DeclaredSet([pending]), MeasuredPart(route=("big-lathe",)))
    assert [(finding.check, finding.status) for finding in findings] == [
        ("route", "pass"),
        ("envelope.max_diameter_mm", "unverified"),
    ]


def test_a_failing_process_with_a_placeholder_sibling_is_not_a_violation() -> None:
    real = make_process("small-lathe", capabilities=[capability("max_diameter_mm", 100)])
    pending = make_process("big-lathe", capabilities=[capability("max_diameter_mm", 1, placeholder=True)])
    part = MeasuredPart(route=("small-lathe", "big-lathe"), bbox_extents_mm=(500, 300, 20))
    findings = run_checks(DeclaredSet([real, pending]), part)
    statuses = [finding.status for finding in _by_check(findings, "envelope.max_diameter_mm")]
    assert "violation" not in statuses and statuses.count("unverified") == 2
    assert blocking(findings) == ()


# ---- several processes in the route ---------------------------------------------------------------------------------


def test_d4_violation_only_if_every_process_of_the_route_fails() -> None:
    small = make_process("small-lathe", capabilities=[capability("max_diameter_mm", 100)])
    large = make_process("large-lathe", capabilities=[capability("max_diameter_mm", 300)])
    declared = DeclaredSet([small, large])
    both = run_checks(declared, MeasuredPart(route=("small-lathe", "large-lathe"), bbox_extents_mm=(500, 350, 20)))
    finding = _only(both, "envelope.max_diameter_mm")
    assert finding.status == "violation"
    assert finding.limit == 300 and finding.entry_id == "large-lathe"  # the most permissive limit
    assert "every process" in finding.detail
    one_fits = run_checks(declared, MeasuredPart(route=("small-lathe", "large-lathe"), bbox_extents_mm=(500, 250, 20)))
    ok = _only(one_fits, "envelope.max_diameter_mm")
    assert ok.status == "pass" and ok.entry_id == "large-lathe"


def test_d4_only_route_processes_count() -> None:
    small = make_process("small-lathe", capabilities=[capability("max_diameter_mm", 100)])
    large = make_process("large-lathe", capabilities=[capability("max_diameter_mm", 300)])
    findings = run_checks(
        DeclaredSet([small, large]), MeasuredPart(route=("small-lathe",), bbox_extents_mm=(500, 150, 20))
    )
    finding = _only(findings, "envelope.max_diameter_mm")
    assert finding.status == "violation" and finding.limit == 100 and "every process" not in finding.detail


def test_d4_build_volumes_pass_if_any_process_accommodates_the_part() -> None:
    tall = make_process("tall", tag="fdm", capabilities=[capability("build_volume_mm", [100, 100, 400])])
    wide = make_process("wide", tag="sls", capabilities=[capability("build_volume_mm", [300, 300, 100])])
    declared = DeclaredSet([tall, wide])
    part = MeasuredPart(route=("tall", "wide"), bbox_extents_mm=(350, 20, 20))
    finding = _only(run_checks(declared, part), "envelope.build_volume_mm")
    assert finding.status == "pass" and finding.entry_id == "tall"  # 350 fits 400 of the tall one
    too_big = MeasuredPart(route=("tall", "wide"), bbox_extents_mm=(450, 20, 20))
    violation = _only(run_checks(declared, too_big), "envelope.build_volume_mm")
    assert violation.status == "violation" and violation.entry_id == "wide"  # larger volume = most permissive


# ---- free (unknown) tags ----------------------------------------------------------------------


def test_unknown_process_tag_is_unverified_with_a_clear_message() -> None:
    foundry = make_process("foundry", tag="sand_casting", capabilities=[capability("max_pour_kg", 50, unit="kg")])
    findings = run_checks(DeclaredSet([foundry]), MeasuredPart(route=("foundry",), bbox_extents_mm=(9e3, 9e3, 9e3)))
    finding = _only(findings, "process_capabilities")
    assert finding.status == "unverified" and finding.detail == "no automatic checks for process sand_casting"
    assert finding.entry_id == "foundry"
    assert _only(findings, "route").status == "pass"
    assert blocking(findings) == ()


def test_known_and_free_processes_can_share_a_route() -> None:
    foundry = make_process("foundry", tag="sand_casting", capabilities=[])
    findings = run_checks(
        DeclaredSet([LATHE, foundry]), MeasuredPart(route=("foundry", "lathe"), bbox_extents_mm=(10, 9, 5))
    )
    assert _only(findings, "process_capabilities").entry_id == "foundry"
    assert _only(findings, "envelope.max_diameter_mm").status == "pass"


# ---- combined ---------------------------------------------------------------------------------


def test_findings_are_deterministic_and_only_violations_block() -> None:
    declared = DeclaredSet([LATHE, make_material("steel", name="Steel 1045")])
    part = MeasuredPart(material="Brass", route=("lathe",), bbox_extents_mm=(250, 400, 60))
    first = run_checks(declared, part)
    assert first == run_checks(declared, part)
    assert [finding.status for finding in blocking(first)] == ["violation", "violation"]
    assert {finding.check for finding in blocking(first)} == {"material", "envelope.max_diameter_mm"}


@pytest.mark.parametrize("route", [("lathe", "lathe"), ("lathe", " lathe ")])
def test_repeated_route_ids_are_collapsed(route: tuple[str, ...]) -> None:
    findings = run_checks(DeclaredSet([LATHE]), MeasuredPart(route=route, bbox_extents_mm=(10, 9, 5)))
    assert len(_by_check(findings, "envelope.max_length_mm")) == 1
