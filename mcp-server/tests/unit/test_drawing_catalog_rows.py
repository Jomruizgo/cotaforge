"""Every requirement row of the drawing is one rule of the kernel's rule catalog, with the codes it takes in."""

from cotaforge.drawing import catalog

from cotaforge_mcp import drawing_requirements as req


def test_every_requirement_row_is_one_catalog_rule() -> None:
    by_row: dict[str, list[str]] = {}
    for rule in catalog.load().rules:
        if rule.requirement:
            by_row.setdefault(rule.requirement, []).append(rule.id)
    missing = sorted(set(req.ROW_IDS) - set(by_row))
    assert not missing, f"requirement rows with no rule in catalog.yaml: {missing}"
    twice = {row: ids for row, ids in by_row.items() if len(ids) > 1}
    assert not twice, f"a requirement row is one rule: {twice}"
    unknown = sorted(set(by_row) - set(req.ROW_IDS))
    assert not unknown, f"catalog rules cite requirement rows that do not exist: {unknown}"


def test_the_codes_a_row_takes_in_are_named_by_its_rule() -> None:
    rules = {r.requirement: r for r in catalog.load().rules if r.requirement}
    missing = {row.id: sorted(set(row.codes) - set(rules[row.id].checks)) for row in req.ROWS if row.id in rules}
    missing = {k: v for k, v in missing.items() if v}
    assert not missing, f"codes a requirement row takes in, absent from its catalog rule: {missing}"
