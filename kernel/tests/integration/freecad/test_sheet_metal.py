"""Sheet metal against the real FreeCAD worker and the real SheetMetal add-on (pinned release, see
``sheetmetal_addon.py``), in a temporary FreeCAD user home: the add-on is detected, the ops build a bent part, the flat
pattern agrees with the closed form of the bend allowance to 1e-4 mm, and the flat pattern is exported to DXF.

Without the add-on the same worker offers no sheet-metal op and says how to install it.
"""

import math
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from cotaforge.adapters.freecad import FreeCADAdapter, FreeCADSession, find_freecad_root, rpc
from cotaforge.contract import (
    AdapterError,
    BaseFlangeRequest,
    DocumentKind,
    Empty,
    ErrorCode,
    ExportDxfRequest,
    FeatureHandle,
    MassRequest,
    NewDocumentRequest,
    SketchHandle,
)
from cotaforge.executor.config import ExecutorConfig
from cotaforge.executor.executor import Executor
from cotaforge.executor.trace import ExecutionTrace, ExecutorState, StepStatus
from cotaforge.ir import parse_plan
from cotaforge.verification import formulas
from cotaforge.verification.results import CheckKind
from cotaforge.verification.verifier import Verifier
from tests.integration.freecad.sheetmetal_addon import ADDON_COMMIT, ADDON_VERSION, make_user_home

pytestmark = pytest.mark.freecad

SHEET_TOOLS = {"base_flange", "edge_flange", "unfold"}
T, R, K = 2.0, 3.0, 0.4


@pytest.fixture(scope="module")
def adapter(tmp_path_factory: pytest.TempPathFactory) -> Iterator[FreeCADAdapter]:
    """A worker whose FreeCAD user home (a temporary one) holds the pinned SheetMetal add-on."""
    root = find_freecad_root()
    if root is None:
        pytest.skip("FreeCAD root not found")
    home = make_user_home(tmp_path_factory.mktemp("sheetmetal-home"), with_addon=True)
    patch = pytest.MonkeyPatch()
    patch.setenv("FREECAD_USER_HOME", str(home))
    patch.setenv("FREECAD_USER_DATA", str(home))  # a set FREECAD_USER_DATA would win and hide the Mod folder
    try:
        with FreeCADSession(freecad_root=root, timeout_s=120.0, log_dir=tmp_path_factory.mktemp("logs")) as connected:
            yield connected
    finally:
        patch.undo()


@pytest.fixture(scope="module")
def adapter_without_addon(tmp_path_factory: pytest.TempPathFactory) -> Iterator[FreeCADAdapter]:
    root = find_freecad_root()
    if root is None:
        pytest.skip("FreeCAD root not found")
    home = make_user_home(tmp_path_factory.mktemp("empty-home"), with_addon=False)
    patch = pytest.MonkeyPatch()
    patch.setenv("FREECAD_USER_HOME", str(home))
    patch.setenv("FREECAD_USER_DATA", str(home))  # a set FREECAD_USER_DATA would win and hide the Mod folder
    try:
        with FreeCADSession(freecad_root=root, timeout_s=120.0, log_dir=tmp_path_factory.mktemp("logs")) as connected:
            yield connected
    finally:
        patch.undo()


# --- plans ----------------------------------------------------------------------------------------------------------


def face(op: str, normal: list[float], **select: Any) -> dict[str, Any]:
    return {"kind": "face", "from_op": op, "select": {"normal": normal, **select}}


def outline(*extra: dict[str, Any]) -> dict[str, Any]:
    rect = {"kind": "rect", "name": "o", "center": [50, 30], "width": 100, "height": 60}
    return {
        "id": "s1",
        "op": "sketch",
        "intent": "outline",
        "on": {"kind": "plane", "name": "XY"},
        "entities": [rect, *extra],
    }


BASE = {"id": "b1", "op": "base_flange", "intent": "sheet", "sketch": "s1", "thickness_mm": T}


def flange(op_id: str, towards: list[float], **extra: Any) -> dict[str, Any]:
    body = {
        "id": op_id,
        "op": "edge_flange",
        "intent": "lip",
        "edge": {
            "kind": "edge",
            "between": [face("b1", [0, 0, 1], largest=True), face("b1", [*towards], largest=True)],
        },
        "length_mm": 20,
        "angle_deg": 90,
        "bend_radius_mm": R,
    }
    return {**body, **extra}


def unfold(k: float = K, op_id: str = "u1") -> dict[str, Any]:
    return {"id": op_id, "op": "unfold", "intent": "flat", "face": face("b1", [0, 0, -1], largest=True), "k_factor": k}


def run(adapter: FreeCADAdapter, *operations: dict[str, Any]) -> tuple[Executor, ExecutionTrace]:
    plan = parse_plan({"id": "sheet", "kind": "part", "operations": list(operations)})
    executor = Executor(plan, adapter, Verifier(), ExecutorConfig())
    executor.start()
    return executor, executor.run_to_end()


def green(trace: ExecutionTrace) -> None:
    detail = [
        (s.op_id, s.status.value, s.plan_error, s.attempts[-1].error if s.attempts else None)
        for s in trace.steps
        if s.status is not StepStatus.success
    ]
    assert trace.final_state is ExecutorState.completed and not detail, detail


def asserted(trace: ExecutionTrace, op_id: str) -> set[str]:
    step = trace.step(op_id)
    assert step is not None
    return {c.name for c in step.last_level2() if c.kind is CheckKind.asserted and c.ok}


def flat_of(trace: ExecutionTrace, op_id: str = "u1") -> tuple[float, float]:
    step = trace.step(op_id)
    assert step is not None and step.attempts and step.attempts[-1].result is not None
    flat = step.attempts[-1].result.flat_pattern
    assert flat is not None
    return flat.size_mm


# --- detection ------------------------------------------------------------------------------------------------------


def test_the_worker_found_the_pinned_addon_and_offers_the_ops(adapter: FreeCADAdapter) -> None:
    assert adapter.sheet_metal is not None
    assert adapter.sheet_metal["available"] is True and adapter.sheet_metal["version"] == ADDON_VERSION
    assert SHEET_TOOLS <= adapter.capabilities().tools and adapter.unavailable_tools() == {}
    assert len(ADDON_COMMIT) == 40  # the tag is pinned to a commit


def test_without_the_addon_the_ops_are_hidden_and_a_call_says_how_to_install_it(
    adapter_without_addon: FreeCADAdapter,
) -> None:
    worker = adapter_without_addon
    assert worker.sheet_metal is not None and worker.sheet_metal["available"] is False
    assert not SHEET_TOOLS & worker.capabilities().tools
    assert "Addon manager" in worker.unavailable_tools()["base_flange"]
    worker.new_document(NewDocumentRequest(operation_id="n", kind=DocumentKind.part, name="no_addon"))
    with pytest.raises(AdapterError) as info:  # someone calls one anyway: never a silent failure
        worker.base_flange(BaseFlangeRequest(operation_id="x", sketch=SketchHandle(id="sketch:1"), thickness_mm=2))
    assert info.value.info.code is ErrorCode.not_supported
    assert "not installed" in info.value.info.message and "Addon manager" in info.value.info.message
    assert worker.canary(Empty(operation_id="c")).healthy  # the worker itself is fine


def _visible_mode_session(tmp_path: Path, mod_dirs: list[Path], patch: pytest.MonkeyPatch) -> FreeCADSession:
    """A headless worker set up the way visible mode sets one up: the private (empty) FreeCAD home that
    ``worker_env(visible=True)`` makes hides every Mod folder, and the engineer's real ones (``mod_dirs``, given through
    the override variable) reach the worker only through the variable ``worker_env`` fills in."""
    root = find_freecad_root()
    if root is None:
        pytest.skip("FreeCAD root not found")
    patch.setenv(rpc.ENV_MOD_DIRS, os.pathsep.join(str(p) for p in mod_dirs))
    env = rpc.worker_env(tmp_path / "private" / "worker.log", visible=True)
    patch.setenv("FREECAD_USER_HOME", env[rpc.ENV_USER_HOME])
    patch.setenv("FREECAD_USER_DATA", env[rpc.ENV_USER_HOME])
    patch.setenv(rpc.ENV_USER_MODS, env[rpc.ENV_USER_MODS])
    return FreeCADSession(freecad_root=root, timeout_s=120.0, log_dir=tmp_path / "logs")


def test_in_visible_mode_the_addon_of_the_engineers_own_mod_folder_is_found(tmp_path: Path) -> None:
    real_mod = make_user_home(tmp_path, with_addon=True) / "Mod"  # stands for the engineer's own Mod folder
    patch = pytest.MonkeyPatch()
    try:
        with _visible_mode_session(tmp_path, [tmp_path / "no-such-mod", real_mod], patch) as worker:
            assert worker.sheet_metal is not None and worker.sheet_metal["available"] is True
            assert worker.sheet_metal["version"] == ADDON_VERSION
            assert SHEET_TOOLS <= worker.capabilities().tools and worker.unavailable_tools() == {}
    finally:
        patch.undo()


def test_in_visible_mode_a_missing_addon_names_the_mod_folders_searched(tmp_path: Path) -> None:
    empty_mod = make_user_home(tmp_path, with_addon=False) / "Mod"
    missing = tmp_path / "no-such-mod"
    patch = pytest.MonkeyPatch()
    try:
        with _visible_mode_session(tmp_path, [empty_mod, missing], patch) as worker:
            assert worker.sheet_metal is not None and worker.sheet_metal["available"] is False
            assert worker.sheet_metal["searched"] == [str(empty_mod), str(missing)]
            assert not SHEET_TOOLS & worker.capabilities().tools
            why = worker.unavailable_tools()["base_flange"]
            assert str(empty_mod) in why and str(missing) in why and "Addon manager" in why
    finally:
        patch.undo()


# --- the ops --------------------------------------------------------------------------------------------------------


def test_a_base_flange_is_a_flat_sheet_of_the_exact_volume(adapter: FreeCADAdapter) -> None:
    _, trace = run(adapter, outline(), BASE)
    green(trace)
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(100 * 60 * T)
    assert {"volume_delta", "bbox_after"} <= asserted(trace, "b1")


def test_the_sheet_goes_to_the_side_the_direction_names(adapter: FreeCADAdapter) -> None:
    _, trace = run(adapter, outline(), {**BASE, "direction": "negative"})
    green(trace)
    assert trace.final_bbox is not None and trace.final_bbox.min[2] == pytest.approx(-T)
    assert trace.final_bbox.max[2] == pytest.approx(0.0)


def test_holes_in_the_sketch_are_holes_in_the_sheet(adapter: FreeCADAdapter) -> None:
    hole = {"kind": "circle", "name": "h", "center": [30, 30], "diameter": 8}
    slot = {"kind": "rect", "name": "slot", "center": [70, 30], "width": 20, "height": 6}
    _, trace = run(adapter, outline(hole, slot), BASE)
    green(trace)
    blank = 100 * 60 - math.pi * 16 - 20 * 6
    assert trace.final_mass is not None and trace.final_mass.volume_mm3 == pytest.approx(blank * T)


@pytest.mark.parametrize("position", ["material_inside", "material_outside"])
@pytest.mark.parametrize("length_from", ["outer_sharp", "leg"])
@pytest.mark.parametrize("angle", [45, 90, 135])
def test_the_flat_pattern_agrees_with_the_closed_form_of_the_bend_allowance(
    adapter: FreeCADAdapter, position: str, length_from: str, angle: int
) -> None:
    """The verifier asserts it too (a disagreement fails the step); this states the numbers."""
    extra = {"position": position, "length_from": length_from, "angle_deg": angle}
    _, trace = run(adapter, outline(), BASE, flange("f1", [0, -1, 0], **extra), unfold())
    green(trace)
    gain = formulas.edge_flange_flat_gain(
        angle, T, R, K, 20, material_inside=position == "material_inside", from_outer_sharp=length_from == "outer_sharp"
    )
    assert sorted(flat_of(trace), reverse=True) == pytest.approx(sorted((100.0, 60 + gain), reverse=True), abs=1e-4)
    assert {"volume_delta", "bbox_contains"} <= asserted(trace, "f1")
    assert {"flat_length_mm", "flat_width_mm", "flat_thickness_mm", "flat_pattern_valid"} <= asserted(trace, "u1")


def test_the_flat_pattern_is_outside_the_solid(adapter: FreeCADAdapter) -> None:
    _, trace = run(adapter, outline(), BASE, flange("f1", [0, -1, 0]), unfold())
    green(trace)
    bent, flat = trace.step("f1"), trace.step("u1")
    assert bent is not None and flat is not None and bent.mass_after is not None and flat.mass_after is not None
    assert flat.mass_after.volume_mm3 == pytest.approx(bent.mass_after.volume_mm3)
    assert adapter.is_valid(Empty(operation_id="v")).valid


def test_the_flat_size_reflects_each_flange_and_survives_opposite_and_adjacent_ones(adapter: FreeCADAdapter) -> None:
    gain = formulas.edge_flange_flat_gain(90, T, R, K, 20, material_inside=True, from_outer_sharp=True)
    _, opposite = run(adapter, outline(), BASE, flange("f1", [0, -1, 0]), flange("f2", [0, 1, 0]), unfold())
    green(opposite)
    assert flat_of(opposite) == pytest.approx((100, 60 + 2 * gain), abs=1e-4)
    # adjacent: the bounding rectangle grows along both axes (the corner is relieved, the size is only reported)
    _, adjacent = run(adapter, outline(), BASE, flange("f1", [0, -1, 0]), flange("f2", [-1, 0, 0]), unfold())
    green(adjacent)
    assert sorted(flat_of(adjacent), reverse=True) == pytest.approx(
        sorted((100 + gain, 60 + gain), reverse=True), abs=1e-3
    )


def test_a_wrong_k_factor_changes_the_flat_size_the_way_the_form_says(adapter: FreeCADAdapter) -> None:
    shorter = {}
    for k in (0.3, 0.5):
        _, trace = run(adapter, outline(), BASE, flange("f1", [0, -1, 0]), unfold(k))
        green(trace)
        shorter[k] = flat_of(trace)[1]  # 100 is the long side; the bent direction is the shorter one
    assert shorter[0.5] - shorter[0.3] == pytest.approx(math.pi / 2 * 0.2 * T, abs=1e-4)


def test_undo_takes_the_unfold_and_the_flange_away_and_leaves_no_ghost(adapter: FreeCADAdapter) -> None:
    executor, trace = run(adapter, outline(), BASE, flange("f1", [0, -1, 0]), unfold())
    green(trace)
    assert executor.undo_to("b1") == ("u1", "f1")
    assert adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(100 * 60 * T)
    kinds = [node.kind for node in adapter.feature_tree(Empty(operation_id="t")).nodes]
    assert "unfold" not in kinds and "edge_flange" not in kinds and "base_flange" in kinds


# --- what it refuses ------------------------------------------------------------------------------------------------


def test_a_flange_with_no_straight_leg_or_a_bend_longer_than_the_sheet_is_refused_before_the_cad(
    adapter: FreeCADAdapter,
) -> None:
    _, short = run(adapter, outline(), BASE, flange("f1", [0, -1, 0], length_mm=4))
    step = short.step("f1")
    assert step is not None and step.status is StepStatus.plan_error and "length_from=leg" in str(step.plan_error)
    _, huge = run(adapter, outline(), BASE, flange("f1", [0, -1, 0], bend_radius_mm=500, length_from="leg"))
    step = huge.step("f1")
    assert step is not None and step.status is StepStatus.plan_error and "material_outside" in str(step.plan_error)


def test_a_solid_feature_on_a_sheet_is_refused_with_where_holes_go(adapter: FreeCADAdapter) -> None:
    pocket = {
        "id": "c1",
        "op": "cut",
        "intent": "hole",
        "sketch": "s2",
        "end_condition": "through_all",
    }
    on_top = {"id": "s2", "op": "sketch", "intent": "hole", "on": face("b1", [0, 0, 1]), "entities": [
        {"kind": "circle", "name": "h", "center": [0, 0], "diameter": 8}]}  # fmt: skip
    _, trace = run(adapter, outline(), BASE, on_top, pocket)
    step = trace.step("c1")
    assert step is not None and step.status is StepStatus.failed
    message = step.attempts[-1].error.message if step.attempts and step.attempts[-1].error else ""
    assert "base_flange sketch" in message
    assert adapter.mass_properties(MassRequest(operation_id="m")).volume_mm3 == pytest.approx(100 * 60 * T)


def test_a_flange_on_a_body_that_is_not_sheet_metal_is_refused(adapter: FreeCADAdapter) -> None:
    solid = {"id": "e1", "op": "extrude", "intent": "block", "sketch": "s1", "depth_mm": 5}
    bad = {
        "id": "f1",
        "op": "edge_flange",
        "intent": "lip",
        "edge": {"kind": "edge", "between": [face("e1", [0, 0, 1]), face("e1", [0, -1, 0])]},
        "length_mm": 10,
        "bend_radius_mm": 2,
    }
    _, trace = run(adapter, outline(), solid, bad)
    step = trace.step("f1")
    assert step is not None and step.status is StepStatus.failed
    assert step.attempts[-1].error is not None and "not one" in step.attempts[-1].error.message


def test_unfolding_a_curved_face_is_refused_naming_the_fix(adapter: FreeCADAdapter) -> None:
    bend = {
        "id": "u1",
        "op": "unfold",
        "intent": "flat",
        "face": {"kind": "face", "from_op": "f1", "select": {"surface": "cylinder", "largest": True}},
        "k_factor": K,
    }
    _, trace = run(adapter, outline(), BASE, flange("f1", [0, -1, 0]), bend)
    step = trace.step("u1")
    assert step is not None and step.status is StepStatus.plan_error and "flat face" in str(step.plan_error)


# --- the flat pattern to DXF ----------------------------------------------------------------------------------------


def read_dxf(path: Path) -> list[tuple[str, list[tuple[int, str]]]]:
    lines = path.read_text(encoding="latin-1").splitlines()
    pairs = [(int(lines[i].strip()), lines[i + 1].strip()) for i in range(0, len(lines) - 1, 2)]
    entities: list[tuple[str, list[tuple[int, str]]]] = []
    inside = False
    for code, value in pairs:
        if code == 2 and value == "ENTITIES":
            inside = True
        elif code == 0 and value == "ENDSEC":
            inside = False
        elif inside and code == 0:
            entities.append((value, []))
        elif inside and entities:
            entities[-1][1].append((code, value))
    return entities


def extent(entities: list[tuple[str, list[tuple[int, str]]]]) -> tuple[float, float]:
    xs: list[float] = []
    ys: list[float] = []
    for kind, data in entities:
        if kind not in ("LINE", "LWPOLYLINE", "CIRCLE"):
            continue
        radius = next((float(v) for code, v in data if code == 40 and kind == "CIRCLE"), 0.0)
        for (c1, v), (c2, w) in zip(data, data[1:], strict=False):
            if c1 in (10, 11) and c2 in (20, 21):
                xs += [float(v) - radius, float(v) + radius]
                ys += [float(w) - radius, float(w) + radius]
    return max(xs) - min(xs), max(ys) - min(ys)


def test_the_flat_pattern_is_exported_to_dxf_at_the_flat_size_with_its_holes(
    adapter: FreeCADAdapter, tmp_path: Path
) -> None:
    hole = {"kind": "circle", "name": "h", "center": [30, 30], "diameter": 8}
    _, trace = run(adapter, outline(hole), BASE, flange("f1", [0, -1, 0]), unfold())
    green(trace)
    step = trace.step("u1")
    assert step is not None and step.attempts[-1].result is not None
    feature: FeatureHandle = step.attempts[-1].result.feature  # type: ignore[assignment]
    path = tmp_path / "flat.dxf"
    adapter.export_dxf(ExportDxfRequest(operation_id="x", path=str(path), flat_pattern=feature))
    entities = read_dxf(path)
    kinds = {kind for kind, _ in entities}
    assert "CIRCLE" in kinds and kinds <= {"LINE", "LWPOLYLINE", "CIRCLE", "ARC"}
    width, height = extent(entities)
    assert sorted((width, height), reverse=True) == pytest.approx(sorted(flat_of(trace), reverse=True), abs=1e-3)
    circle = next(data for kind, data in entities if kind == "CIRCLE")
    assert float(next(v for code, v in circle if code == 40)) == pytest.approx(4.0)  # the 8 mm hole, at real scale


def test_a_dxf_needs_exactly_one_source_and_a_flat_pattern_comes_from_an_unfold() -> None:
    with pytest.raises(ValueError, match="exactly one source"):
        ExportDxfRequest(operation_id="x", path="a.dxf")
    with pytest.raises(ValueError, match="exactly one source"):
        ExportDxfRequest(operation_id="x", path="a.dxf", flat_pattern=FeatureHandle(id="f"), face={"id": "face:1"})  # type: ignore[arg-type]


def test_a_feature_that_is_not_an_unfold_has_no_flat_pattern(adapter: FreeCADAdapter, tmp_path: Path) -> None:
    _, trace = run(adapter, outline(), BASE)
    green(trace)
    step = trace.step("b1")
    assert step is not None and step.attempts[-1].result is not None and step.attempts[-1].result.feature is not None
    with pytest.raises(AdapterError) as info:
        adapter.export_dxf(
            ExportDxfRequest(
                operation_id="x", path=str(tmp_path / "x.dxf"), flat_pattern=step.attempts[-1].result.feature
            )
        )
    assert "not an unfold" in info.value.info.message and not (tmp_path / "x.dxf").exists()
