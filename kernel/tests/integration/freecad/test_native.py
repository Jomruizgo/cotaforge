"""``run_native`` against the real FreeCAD worker.

The two corpora under ``native_corpus/`` are the acceptance evidence of the capability:

* ``valid/``: fragments that must run, recompute and leave nothing broken (``# expect: ok``), and whose single
  ``undo_last`` must return the document to exactly the state before the step;
* ``broken/``: fragments that raise, time out, or silently leave the document broken (``# expect: rolled_back``);
  each must end ``ok=False, rolled_back=True`` with the document equal to its pre-step self.

"Equal" is the worker's own document fingerprint (``test_document_state``: every object's type, label, scalar
properties, placement, links, expressions, sketch geometry/constraints, spreadsheet cells and shape summary), reached
through the test hook the adapter enables with ``enable_test_hooks=True``.

Header lines of a corpus file (before the first non-comment line): ``# expect:``, ``# setup: a, b`` (earlier valid
snippets to run first, recursively), ``# error: <ExceptionType>`` and ``# broken: <substring of a reason>`` (checked
when present), ``# timeout: <seconds>`` and ``# about:``.
"""

import re
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter, FreeCADSession
from cotaforge.contract import (
    AdapterError,
    DocumentKind,
    Empty,
    ErrorCode,
    NewDocumentRequest,
    RunNativeRequest,
    RunNativeResult,
)
from cotaforge.contract.native import MAX_STDOUT_CHARS

pytestmark = pytest.mark.freecad

CORPUS = Path(__file__).parent / "native_corpus"
MIN_VALID = 80
MIN_BROKEN = 25


@dataclass(frozen=True)
class Case:
    name: str
    kind: str
    expect: str
    setup: tuple[str, ...]
    error: str | None
    broken: str | None
    timeout: float
    code: str


def _load(kind: str) -> list[Case]:
    cases = []
    for path in sorted((CORPUS / kind).glob("*.py")):
        text = path.read_text(encoding="utf-8")
        headers: dict[str, str] = {}
        for line in text.splitlines():
            match = re.match(r"#\s*(\w+):\s*(.*)", line)
            if not match:
                break
            headers[match.group(1)] = match.group(2).strip()
        cases.append(
            Case(
                name=path.stem,
                kind=kind,
                expect=headers.get("expect", ""),
                setup=tuple(s.strip() for s in headers.get("setup", "").split(",") if s.strip()),
                error=headers.get("error"),
                broken=headers.get("broken"),
                timeout=float(headers.get("timeout", 60)),
                code=text,
            )
        )
    return cases


VALID = _load("valid")
BROKEN = _load("broken")
VALID_BY_NAME = {case.name: case for case in VALID}


# --- fixtures and helpers ------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def native(freecad_root: Path, tmp_path_factory: pytest.TempPathFactory) -> Iterator[FreeCADAdapter]:
    log_dir = tmp_path_factory.mktemp("native-logs")
    with FreeCADSession(freecad_root=freecad_root, timeout_s=120.0, log_dir=log_dir, enable_test_hooks=True) as adapter:
        yield adapter


def new_doc(adapter: FreeCADAdapter, name: str) -> None:
    safe = re.sub(r"\W", "_", name)
    adapter.new_document(NewDocumentRequest(operation_id=f"new.{safe}", kind=DocumentKind.part, name=safe))


def state(adapter: FreeCADAdapter) -> dict[str, Any]:
    return adapter._raw_call("test_document_state", Empty(operation_id="state"))


def run(adapter: FreeCADAdapter, code: str, intent: str = "test", timeout_s: float = 60.0) -> RunNativeResult:
    return adapter.run_native(RunNativeRequest(operation_id="native", code=code, intent=intent, timeout_s=timeout_s))


def run_setup(adapter: FreeCADAdapter, name: str, done: set[str]) -> None:
    case = VALID_BY_NAME[name]
    for dependency in case.setup:
        if dependency not in done:
            run_setup(adapter, dependency, done)
    result = run(adapter, case.code, intent=f"setup {name}")
    assert result.ok, f"setup {name} failed: {result.error} {result.broken}"
    done.add(name)


def changed(result: RunNativeResult) -> bool:
    return bool(result.created or result.modified or result.removed)


def undo(adapter: FreeCADAdapter) -> None:
    adapter.undo_last(Empty(operation_id="undo"))


# --- the corpora ---------------------------------------------------------------------------------------------------


def test_corpora_are_large_enough_and_every_file_declares_its_expectation() -> None:
    assert len(VALID) >= MIN_VALID
    assert len(BROKEN) >= MIN_BROKEN
    assert {case.expect for case in VALID} == {"ok"}
    assert all(case.expect.startswith("rolled_back") for case in BROKEN)
    for case in VALID:
        for dependency in case.setup:
            assert dependency in VALID_BY_NAME, f"{case.name}: unknown setup {dependency}"
    assert len({case.code for case in VALID}) == len(VALID), "two valid snippets are identical"


@pytest.mark.parametrize("case", VALID, ids=lambda c: c.name)
def test_valid_snippet_passes_and_one_undo_restores_the_previous_state(native: FreeCADAdapter, case: Case) -> None:
    new_doc(native, case.name)
    done: set[str] = set()
    for dependency in case.setup:
        if dependency not in done:
            run_setup(native, dependency, done)
    before = state(native)

    result = run(native, case.code, intent=f"corpus {case.name}", timeout_s=case.timeout)

    assert result.ok, f"{result.error} / broken={result.broken}"
    assert not result.rolled_back
    assert result.broken == ()
    assert result.error is None
    assert result.duration_ms > 0
    after = state(native)
    if changed(result):
        assert after["objects"] != before["objects"], "the step reports changes but the fingerprint did not move"
        assert after["features"] == before["features"] + 1, "a step is exactly one entry of the harness' history"
        reverted = native.undo_last(Empty(operation_id="undo")).reverted
        assert result.feature is not None and reverted == result.feature, "the result names the entry undo reverts"
        restored = state(native)
        assert restored["objects"] == before["objects"], "undo_last did not restore the pre-step state"
        assert restored["features"] == before["features"]
    else:
        assert result.feature is None
        assert after["objects"] == before["objects"]
        assert after["features"] == before["features"], "a step that changed nothing must not enter the history"


@pytest.mark.parametrize("case", BROKEN, ids=lambda c: c.name)
def test_broken_snippet_is_rolled_back_to_the_exact_previous_state(native: FreeCADAdapter, case: Case) -> None:
    new_doc(native, case.name)
    done: set[str] = set()
    for dependency in case.setup:
        if dependency not in done:
            run_setup(native, dependency, done)
    before = state(native)

    result = run(native, case.code, intent=f"corpus {case.name}", timeout_s=case.timeout)

    assert not result.ok
    assert result.rolled_back, f"not rolled back; warnings={result.warnings}"
    assert result.error is not None
    assert result.error.message
    assert (result.created, result.modified, result.removed) == ((), (), ())
    if case.error is not None:
        assert result.error.type == case.error, result.error
    if case.broken is not None:
        assert result.broken, "expected broken objects to be reported"
        assert case.broken in " ".join(f"{b.name} {b.reason}" for b in result.broken), result.broken
    after = state(native)
    assert after["objects"] == before["objects"], "the document differs from its pre-step state"
    assert after["features"] == before["features"]


# --- one step is one undo, whatever it did -------------------------------------------------------------------------


def test_creating_changing_and_deleting_in_one_step_is_one_undo(native: FreeCADAdapter) -> None:
    new_doc(native, "mixed_step")
    run(native, 'a = doc.addObject("Part::Box", "A")\nb = doc.addObject("Part::Cylinder", "B")', "two objects")
    before = state(native)

    result = run(
        native,
        'doc.getObject("A").Length = 77\ndoc.removeObject("B")\ndoc.addObject("Part::Sphere", "C")',
        "change, delete, create",
    )

    assert result.ok
    assert [o.name for o in result.created] == ["C"]
    assert [o.name for o in result.modified] == ["A"]
    assert [o.name for o in result.removed] == ["B"]
    undo(native)
    assert state(native)["objects"] == before["objects"]


def test_user_transactions_inside_the_fragment_are_undone_with_the_step(native: FreeCADAdapter) -> None:
    new_doc(native, "user_tx")
    before = state(native)
    code = (
        'doc.openTransaction("mine")\ndoc.addObject("Part::Box", "X")\ndoc.commitTransaction()\n'
        'doc.openTransaction("mine2")\ndoc.addObject("Part::Box", "Y")\ndoc.commitTransaction()\n'
    )
    result = run(native, code, "manages its own transactions")
    assert result.ok
    assert any("undo" in w.lower() for w in result.warnings)
    undo(native)
    assert state(native)["objects"] == before["objects"]


def test_a_presentation_only_change_is_not_a_model_change(native: FreeCADAdapter) -> None:
    new_doc(native, "visibility")
    run(native, 'doc.addObject("Part::Box", "Box")', "box")
    before = state(native)
    result = run(native, 'doc.getObject("Box").Visibility = False', "hide")
    assert result.ok
    assert not changed(result)
    assert state(native)["features"] == before["features"]


def test_the_step_is_listed_in_the_feature_tree_with_its_intent_and_its_objects_are_not_manual(
    native: FreeCADAdapter,
) -> None:
    new_doc(native, "tree")
    run(native, 'doc.addObject("Part::Box", "Box")', "a base box")
    tree = native.feature_tree(Empty(operation_id="tree"))
    assert [n.kind for n in tree.nodes] == ["native"]
    assert tree.nodes[0].parameters == {"intent": "a base box"}
    undo(native)
    assert native.feature_tree(Empty(operation_id="tree")).nodes == ()


def test_a_step_keeps_the_harness_tools_working_on_a_body_it_modified(native: FreeCADAdapter) -> None:
    from cotaforge.contract import BoxRequest, MassRequest

    new_doc(native, "harness_body")
    made = native.box(BoxRequest(operation_id="box", size=(10, 20, 30)))
    assert made.created_bodies
    volume = native.mass_properties(MassRequest(operation_id="m")).volume_mm3
    assert volume == pytest.approx(6000.0)
    names = [name for name, o in state(native)["objects"].items() if o["type"].startswith("Part::")]
    code = f'o = doc.getObject("{names[0]}")\no.Placement = Placement(Vector(100, 0, 0), Rotation())'
    result = run(native, code, "move the harness body by hand")
    assert result.ok
    assert native.bounding_box(MassRequest(operation_id="b")).min[0] == pytest.approx(100.0)
    undo(native)
    assert native.bounding_box(MassRequest(operation_id="b")).min[0] == pytest.approx(0.0)
    undo(native)
    assert native.feature_tree(Empty(operation_id="tree")).nodes == ()


# --- the result a caller gets ----------------------------------------------------------------------------------------


def test_result_reports_volume_boxes_and_output(native: FreeCADAdapter) -> None:
    new_doc(native, "result_fields")
    result = run(native, 'b = doc.addObject("Part::Box", "B")\nb.Length, b.Width, b.Height = 10, 20, 30\nprint("hi")')
    assert result.ok
    assert result.volume_delta_mm3 == pytest.approx(6000.0)
    assert result.bbox_before is None
    assert result.bbox_after is not None
    assert result.bbox_after.max == pytest.approx((10.0, 20.0, 30.0))
    assert result.stdout == "hi\n"
    assert [(o.name, o.label, o.type_id) for o in result.created] == [("B", "B", "Part::Box")]
    second = run(native, 'doc.getObject("B").Length = 40', "stretch")
    assert second.volume_delta_mm3 == pytest.approx(18000.0)
    assert second.bbox_before is not None
    assert second.bbox_before.max == pytest.approx((10.0, 20.0, 30.0))
    assert second.bbox_after is not None
    assert second.bbox_after.max == pytest.approx((40.0, 20.0, 30.0))


def test_error_carries_type_message_line_and_hint(native: FreeCADAdapter) -> None:
    new_doc(native, "error_fields")
    result = run(native, "x = 1\ny = 2\nz = Vectr(1, 2, 3)\n")
    assert not result.ok
    assert result.error is not None
    assert (result.error.type, result.error.line) == ("NameError", 3)
    assert "Vector" in result.error.hint


def test_print_before_a_failure_is_kept_whole_and_a_flood_is_counted(native: FreeCADAdapter) -> None:
    """The worker sends the output back whole (the client shortens it and keeps the rest in a file); only what goes
    beyond ``MAX_STDOUT_CHARS`` is dropped, and then it is counted, never cut silently."""
    new_doc(native, "stdout_fields")
    result = run(native, 'print("before")\nprint("x" * 20000)\nraise RuntimeError("late")')
    assert not result.ok
    assert result.stdout == "before\n" + "x" * 20000 + "\n"
    assert result.stdout_dropped_chars == 0
    flood = run(native, f'print("y" * {MAX_STDOUT_CHARS + 500}, end="")')
    assert flood.ok
    assert flood.stdout == "y" * MAX_STDOUT_CHARS and flood.stdout_dropped_chars == 500


def test_the_headless_worker_warns_that_there_is_no_gui(native: FreeCADAdapter) -> None:
    new_doc(native, "no_gui")
    result = run(native, "import FreeCADGui")
    assert any("GUI module is not available" in w for w in result.warnings)


def test_input_in_the_fragment_does_not_consume_the_request_pipe(native: FreeCADAdapter) -> None:
    new_doc(native, "stdin_guard")
    result = run(native, "answer = input('value? ')", timeout_s=10.0)
    assert not result.ok
    assert result.error is not None
    assert result.error.type == "EOFError"
    assert run(native, 'doc.addObject("Part::Box", "AfterInput")').ok


def test_missing_intent_large_code_and_traps_are_warnings_not_rejections(native: FreeCADAdapter) -> None:
    new_doc(native, "warnings")
    padding = "\n".join(f"v{i} = {i}" for i in range(250))
    code = f'{padding}\nb = doc.addObject("Part::Box", "B")\nb.Placement.Base.x = 5\n'
    result = run(native, code, intent="")
    assert result.ok
    text = " | ".join(result.warnings)
    assert "intent" in text.lower()
    assert "Large fragment" in text
    assert "Placement" in text
    clean = run(native, 'doc.addObject("Part::Box", "C")', intent="a box")
    assert clean.ok
    assert not any("intent" in w.lower() for w in clean.warnings)


def test_a_pocket_that_misses_the_material_is_a_warning(native: FreeCADAdapter) -> None:
    new_doc(native, "far_pocket")
    done: set[str] = set()
    run_setup(native, "100_pd_body_pad", done)
    code = (
        'body = doc.getObject("Body")\n'
        'xy = [o for o in body.Origin.OriginFeatures if o.Role == "XY_Plane"][0]\n'
        'sk = body.newObject("Sketcher::SketchObject", "FarSketch")\n'
        'sk.AttachmentSupport = (xy, [""])\nsk.MapMode = "FlatFace"\n'
        "sk.AttachmentOffset = Placement(Vector(0, 0, 10), Rotation())\n"
        "sk.addGeometry(Part.Circle(Vector(500, 500, 0), Vector(0, 0, 1), 3), False)\n"
        'sk.addConstraint(Sketcher.Constraint("Diameter", 0, 2 * 3))\n'
        'sk.addConstraint(Sketcher.Constraint("DistanceX", -1, 1, 0, 3, 500))\n'
        'sk.addConstraint(Sketcher.Constraint("DistanceY", -1, 1, 0, 3, 500))\n'
        'pocket = body.newObject("PartDesign::Pocket", "FarPocket")\npocket.Profile = sk\n'
        'pocket.Type = "Length"\npocket.Length = 4\n'
    )
    result = run(native, code, "pocket far away")
    assert result.ok
    assert any("did not decrease" in w for w in result.warnings), result.warnings


def test_the_preloaded_namespace_is_the_documented_one(native: FreeCADAdapter) -> None:
    new_doc(native, "namespace")
    code = (
        "names = ['doc', 'App', 'FreeCAD', 'Part', 'Base', 'Vector', 'Placement', 'Rotation', 'Sketcher', "
        "'PartDesign', 'math', 'faces_where', 'edges_where', 'solids']\n"
        "missing = [n for n in names if n not in globals()]\n"
        "print('missing', missing)\n"
        "print('active', doc is App.ActiveDocument, Base.Vector is App.Vector)\n"
        "print(solids(), faces_where(Part.makeBox(1, 1, 1), lambda f: f.Area > 0)[0][0])\n"
    )
    result = run(native, code)
    assert result.ok, result.error
    assert "missing []" in result.stdout
    assert "active True True" in result.stdout
    assert "[] Face1" in result.stdout


# --- undo depth ----------------------------------------------------------------------------------------------------


def test_forty_native_steps_undo_all_the_way_back_past_freecads_twenty_step_cap(native: FreeCADAdapter) -> None:
    """The undo of a native step is its journal, not FreeCAD's undo stack (capped at 20 transactions): 40 consecutive
    steps that create, edit and delete all undo one by one, each returning the document to the state before it, and
    the feature tree is empty at the end."""
    new_doc(native, "depth")
    snapshots = [state(native)["objects"]]
    for i in range(40):
        if i % 4 == 3:  # delete an object an earlier step made
            code = f'doc.removeObject("Step{i - 2:02d}")'
        elif i % 4 == 2:  # edit an object an earlier step made
            code = f'doc.getObject("Step{i - 2:02d}").Length = {i + 100}'
        else:
            code = f'b = doc.addObject("Part::Box", "Step{i:02d}")\nb.Length = {i + 1}'
        result = run(native, code, f"step {i}")
        assert result.ok, f"step {i}: {result.error}"
        snapshots.append(state(native)["objects"])
    assert state(native)["features"] == 40
    for k in range(40):
        undo(native)
        assert state(native)["objects"] == snapshots[39 - k], f"undo number {k + 1} did not restore step {39 - k}"
    assert state(native)["objects"] == snapshots[0] == {}
    assert native.feature_tree(Empty(operation_id="tree")).nodes == ()
    assert native.undo_last(Empty(operation_id="undo")).reverted is None


def test_native_and_catalogue_ops_interleave_in_one_history_deeper_than_the_cap(native: FreeCADAdapter) -> None:
    from cotaforge.contract import BoxRequest, MassRequest

    new_doc(native, "interleaved")
    for i in range(15):
        native.box(BoxRequest(operation_id=f"box{i}", size=(10, 10, 10)))
        assert run(native, f'doc.Objects[-1].Label = "renamed{i}"', f"rename {i}").ok
    assert native.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(15000.0)
    for _ in range(30):
        assert native.undo_last(Empty(operation_id="undo")).reverted is not None
    assert state(native)["objects"] == {}
    assert native.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(0.0)


# --- timeouts ------------------------------------------------------------------------------------------------------


def test_soft_deadline_stops_a_runaway_loop_and_the_session_survives(native: FreeCADAdapter) -> None:
    new_doc(native, "soft_timeout")
    result = run(native, "while True:\n    pass", timeout_s=1.5)
    assert not result.ok
    assert result.rolled_back
    assert result.error is not None
    assert result.error.type == "Timeout"
    assert native.worker_pid is not None
    follow_up = run(native, 'doc.addObject("Part::Box", "StillAlive")', "worker still serves requests")
    assert follow_up.ok


def test_uninterruptible_call_loses_the_worker_and_a_reconnect_recovers(
    freecad_root: Path, tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fragment stuck inside one long native call (``time.sleep`` stands in for an OCC operation) cannot be
    interrupted between lines: the client's RPC wait is the backstop. It kills the worker, answers ``timeout``
    (recoverable), the session is then ``session_lost``, and ``connect`` starts a fresh worker."""
    import cotaforge.adapters.freecad.adapter as adapter_module
    from cotaforge.contract import ConnectRequest

    monkeypatch.setattr(adapter_module, "NATIVE_RPC_GRACE_S", 1.0)
    log_dir = tmp_path_factory.mktemp("native-hard-timeout")
    adapter = FreeCADAdapter(freecad_root=freecad_root, timeout_s=60.0, log_dir=log_dir)
    adapter.connect(ConnectRequest(operation_id="connect", timeout_s=60.0))
    try:
        new_doc(adapter, "hard_timeout")
        with pytest.raises(AdapterError) as lost:
            run(adapter, "import time\ntime.sleep(60)", timeout_s=1.0)
        assert lost.value.info.code is ErrorCode.timeout
        assert lost.value.info.recoverable
        with pytest.raises(AdapterError) as after:
            run(adapter, "x = 1")
        assert after.value.info.code is ErrorCode.session_lost
        adapter.connect(ConnectRequest(operation_id="reconnect", timeout_s=60.0))
        new_doc(adapter, "after_reconnect")
        assert run(adapter, 'doc.addObject("Part::Box", "B")').ok
    finally:
        adapter.disconnect(Empty(operation_id="disconnect"))
