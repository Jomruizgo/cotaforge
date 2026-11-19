"""AST scan enforcing the dependency rule of the kernel packages.

Allowed "may import" arrows:
    contract       -> nothing internal (pydantic + stdlib only)
    mouldparts     -> contract (catalogue tables of mould components: pure data and arithmetic)
    ir             -> contract, mouldparts
    executor       -> ir, verification, contract, mouldparts
    verification   -> ir, contract
    units          -> nothing from cotaforge (pydantic + stdlib only)
    memory         -> nothing from cotaforge (pydantic + pyyaml + stdlib only)
    constraints    -> memory (pydantic + stdlib only)
    native         -> nothing from cotaforge (stdlib only: it is the PreToolUse hook, started on every call)
    conventions    -> nothing from cotaforge (pyyaml + stdlib only; adapters.freecad reads it)
    adapters     -> nothing from cotaforge but its own root-level shared modules (adapters/rpc.py: CAD-agnostic
                       transport; it uses adapters/procstats.py: what the OS says about a process)
    adapters.fake  -> contract (only)
    adapters.freecad -> contract, adapters.rpc (the shared transport), conventions, ir (only the references
                       -- FaceRef... -- the models of its own extension operations are written with)
    adapters.freecad.worker -> nothing from cotaforge (runs inside freecadcmd on its own sys.path;
                       it imports no cotaforge module at all)

Anything not drawn in the diagram is forbidden.

This is a static (AST-based) scan: a dynamic `importlib.import_module("cotaforge.executor...")` call is
not statically detectable and so is not covered by it.
"""

import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "cotaforge"

ALLOWED_IMPORTS: dict[str, frozenset[str]] = {
    "contract": frozenset(),
    "mouldparts": frozenset({"contract"}),
    "ir": frozenset({"contract", "mouldparts"}),
    "executor": frozenset({"ir", "verification", "contract", "mouldparts"}),
    "verification": frozenset({"ir", "contract"}),
    "adapters": frozenset({"adapters.procstats"}),
    "adapters.fake": frozenset({"contract"}),
    "adapters.freecad": frozenset({"contract", "adapters.rpc", "conventions", "ir"}),
    "adapters.freecad.worker": frozenset(),
    "units": frozenset(),
    "memory": frozenset(),
    "constraints": frozenset({"memory"}),
    "native": frozenset(),
    "conventions": frozenset(),
}


def _owning_package(path: Path) -> str | None:
    """The top-level (or adapters.<name>[.worker]) package that owns a module, or None for root-level
    files. ``adapters.<cad>.worker`` is its own bucket, separate from the ``adapters.<cad>`` client
    package: the worker runs inside the CAD application's bundled interpreter on its own ``sys.path``
    and must import nothing from ``cotaforge``."""
    relative = path.relative_to(SRC)
    parts = relative.parts[:-1]  # drop the filename
    if not parts:
        return None
    if parts[0] == "adapters":
        if len(parts) >= 3 and parts[2] == "worker":
            return f"adapters.{parts[1]}.worker"
        if len(parts) >= 2:
            return f"adapters.{parts[1]}"
        return "adapters"
    return parts[0]


def _target_package(dotted_module: str) -> str:
    """Map an imported ``cotaforge.*`` dotted path to its owning package key."""
    parts = dotted_module.split(".")[1:]  # drop the leading "cotaforge"
    if not parts:
        return ""
    if parts[0] == "adapters" and len(parts) >= 2:
        return f"adapters.{parts[1]}"
    return parts[0]


def _imported_cotaforge_packages(tree: ast.Module) -> set[str]:
    packages: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == "cotaforge" or alias.name.startswith("cotaforge."):
                    packages.add(_target_package(alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module == "cotaforge" or node.module.startswith("cotaforge."):
                packages.add(_target_package(node.module))
    return packages


def _iter_source_files() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _collect_dependency_rule_violations() -> list[str]:
    """The scan body of ``test_dependency_rule_holds_for_every_module``, factored out so probe tests can
    run the real rule (not a duplicated mini-version) against a temporary tree by monkeypatching ``SRC``."""
    violations: list[str] = []
    for path in _iter_source_files():
        owning = _owning_package(path)
        if owning is None or owning not in ALLOWED_IMPORTS:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported = _imported_cotaforge_packages(tree)
        imported.discard("")
        allowed = ALLOWED_IMPORTS[owning] | {owning}
        forbidden = imported - allowed
        if forbidden:
            relative = path.relative_to(SRC)
            violations.append(
                f"{relative}: imports {sorted(forbidden)} but '{owning}' may only import {sorted(allowed)}"
            )
    return violations


def test_dependency_rule_holds_for_every_module() -> None:
    violations = _collect_dependency_rule_violations()
    assert not violations, "\n".join(violations)


def test_contract_imports_only_pydantic_and_stdlib() -> None:
    stdlib_names = set(sys.stdlib_module_names)
    violations: list[str] = []
    for path in _iter_source_files():
        if _owning_package(path) != "contract":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top == "cotaforge" or top == "pydantic" or top in stdlib_names:
                    continue
                relative = path.relative_to(SRC)
                violations.append(f"{relative}: imports non-pydantic/non-stdlib module '{name}'")
    assert not violations, "\n".join(violations)


def _third_party_import_violations(package: str, allowed_top_level: frozenset[str]) -> list[str]:
    """Every import in the modules owned by ``package`` must be ``cotaforge``, one of
    ``allowed_top_level`` or the stdlib (the purity scan)."""
    stdlib_names = set(sys.stdlib_module_names)
    violations: list[str] = []
    for path in _iter_source_files():
        if _owning_package(path) != package:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                continue
            for name in names:
                top = name.split(".")[0]
                if top == "cotaforge" or top in allowed_top_level or top in stdlib_names:
                    continue
                relative = path.relative_to(SRC)
                violations.append(f"{relative}: imports module '{name}' outside {sorted(allowed_top_level)} + stdlib")
    return violations


@pytest.mark.parametrize("package", ["units"])
def test_units_import_only_pydantic_and_stdlib(package: str) -> None:
    """``units`` imports only pydantic and the stdlib directly (a syntactic check on the package's own
    import statements)."""
    violations = _third_party_import_violations(package, frozenset({"pydantic"}))
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize(
    ("package", "allowed"),
    [("memory", frozenset({"pydantic", "yaml"})), ("constraints", frozenset({"pydantic"}))],
)
def test_memory_and_constraints_import_only_their_declared_third_party_modules(
    package: str, allowed: frozenset[str]
) -> None:
    """``memory`` may use pydantic and pyyaml; ``constraints`` only pydantic (it never parses files)."""
    violations = _third_party_import_violations(package, allowed)
    assert not violations, "\n".join(violations)


def test_native_imports_only_the_standard_library() -> None:
    """``native`` is the hook entry point (a fresh interpreter per ``run_native`` call): no third-party import."""
    violations = _third_party_import_violations("native", frozenset())
    assert not violations, "\n".join(violations)


@pytest.mark.parametrize("package", ["memory", "constraints", "native"])
def test_memory_and_constraints_cannot_reach_adapters_executor_or_ir(
    package: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Probe: the pure packages importing the heavy ones must be flagged by the real rule."""
    package_dir = tmp_path / package
    package_dir.mkdir()
    for forbidden in ("adapters.fake", "executor", "ir", "verification", "contract"):
        (package_dir / "probe.py").write_text(f"import cotaforge.{forbidden}\n", encoding="utf-8")
        violations = _run_dependency_rule_scan_against(monkeypatch, tmp_path)
        assert violations, f"{package} importing cotaforge.{forbidden} should have been flagged"


def test_dependency_rule_test_itself_finds_at_least_one_module() -> None:
    # Sanity check: the scan actually walks real files (guards against a silently empty SRC).
    assert _iter_source_files(), "no source files found under src/cotaforge"


def _run_dependency_rule_scan_against(monkeypatch: pytest.MonkeyPatch, tmp_src: Path) -> list[str]:
    """Runs the real ``_collect_dependency_rule_violations`` scan (not a duplicated mini-version) against
    a temporary tree by monkeypatching the module-level ``SRC`` constant."""
    monkeypatch.setattr(sys.modules[__name__], "SRC", tmp_src)
    return _collect_dependency_rule_violations()


def test_worker_probe_importing_adapters_rpc_is_flagged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``adapters.freecad.worker`` (runs inside ``freecadcmd`` on its own ``sys.path``) has an empty
    allow-set: it must import no ``cotaforge`` module at all, so even the shared transport
    (``adapters.rpc``) is forbidden."""
    worker_dir = tmp_path / "adapters" / "freecad" / "worker"
    worker_dir.mkdir(parents=True)
    (worker_dir / "probe.py").write_text("import cotaforge.adapters.rpc\n", encoding="utf-8")
    violations = _run_dependency_rule_scan_against(monkeypatch, tmp_path)
    assert violations, "adapters.freecad.worker importing cotaforge.adapters.rpc should have been flagged"


def test_adapters_fake_probe_importing_adapters_rpc_is_flagged(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Only ``adapters.freecad`` (and, when it exists, other ``adapters.<cad>`` client packages) may
    target the shared transport; ``adapters.fake`` importing ``adapters.rpc`` must be flagged."""
    fake_dir = tmp_path / "adapters" / "fake"
    fake_dir.mkdir(parents=True)
    (fake_dir / "probe.py").write_text("import cotaforge.adapters.rpc\n", encoding="utf-8")
    violations = _run_dependency_rule_scan_against(monkeypatch, tmp_path)
    assert violations, "adapters.fake importing cotaforge.adapters.rpc should have been flagged"
