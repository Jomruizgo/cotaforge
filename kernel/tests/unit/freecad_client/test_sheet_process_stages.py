# ruff: noqa: E501
"""The size of each process stage and the revision marker, as plain data (no FreeCAD needed): a ``dim`` carries ``stage`` (the
process, printed after the size) and ``size_mm`` (an intermediate stage's own target, drawn secondary); a ``revision`` item
names the changed dimensions (``at``) and gets FreeCAD's revision triangle beside each."""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import items_dims  # noqa: E402
import items_notes  # noqa: E402
import sheet_marks as sm  # noqa: E402
import sheet_units as su  # noqa: E402
from sheet_context import Context, ItemError  # noqa: E402
from sheet_model import DIMENSIONS, Line  # noqa: E402

DIAMETER = {"kind": "diameter", "face": "face:1"}


def test_a_stage_dimension_takes_the_process_and_an_intermediate_one_its_own_size() -> None:
    final = items_dims.check({**DIAMETER, "stage": "rectificado", "fit": "g6"})
    middle = items_dims.check({**DIAMETER, "stage": "torneado", "size_mm": 45.3})
    assert final["stage"] == "rectificado" and "size_mm" not in final
    assert middle["size_mm"] == 45.3
    assert items_dims.check({"kind": "distance", "from": {"face": "face:1"}, "to": {"face": "face:2"}, "stage": "rectificado"})["stage"]


@pytest.mark.parametrize(
    "spec, why",
    [
        ({**DIAMETER, "size_mm": 45.3}, "needs stage"),
        ({"kind": "radius", "face": "face:1", "stage": "torneado"}, "a distance or a diameter"),
        ({**DIAMETER, "stage": "torneado", "size_mm": 45.3, "fit": "H7/g6"}, "one class"),
        ({**DIAMETER, "stage": "torneado", "size_mm": 0}, "between"),
    ],
)
def test_a_stage_dimension_that_cannot_be_read_is_refused_with_what_to_change(spec: dict, why: str) -> None:
    with pytest.raises(ItemError, match=why):
        items_dims.check(spec)


def test_the_text_of_a_stage_is_its_size_and_the_process_and_an_intermediate_prints_its_own_size_not_the_models() -> None:
    ctx = SimpleNamespace(num=lambda v: f"{v:g}", comma=False)
    final, _ = items_dims._toleranced(ctx, {"stage": "ground"}, 45.0, "linear", prefix="Ø")
    middle, extra = items_dims._toleranced(ctx, {"stage": "turned", "size_mm": 45.3}, 45.0, "linear", prefix="Ø")
    toleranced, _ = items_dims._toleranced(ctx, {"stage": "ground", "tol": [0.0, -0.02]}, 45.0, "linear", prefix="Ø")
    assert final.text == "Ø45 ground" and middle.text == "Ø45.3 turned"
    assert "the model measures 45" in extra  # the answer says the printed size is not the model's
    assert toleranced.text.startswith("Ø45 ") and toleranced.text.endswith(" ground")


def _ctx(spec: dict) -> SimpleNamespace:
    ctx = SimpleNamespace(extras={"current": "s1", "items": [{"id": "s1", "type": "dim", **spec}]})
    ctx.current_spec = lambda: Context.current_spec(ctx)  # type: ignore[arg-type]
    return ctx


def _dim(**over: object) -> su.NativeDim:
    fields = {"rect": (5.0, 10.0, 25.0, 30.0), **over}
    return su.NativeDim("s1", "d1", "distance", "front", (su.Ref2("point", (0.0, 0.0)), su.Ref2("point", (0.0, 45.0))), (10.0, 20.0), fields.pop("expected", 45.0), "Ø45.3 turned", "v", **fields)


def test_an_intermediate_stage_is_drawn_secondary_and_the_final_one_is_not() -> None:
    middle = _ctx({"stage": "turned", "size_mm": 45.3})
    Context.native(middle, _dim())  # type: ignore[arg-type]
    final = _ctx({"stage": "ground"})
    Context.native(final, _dim())  # type: ignore[arg-type]
    assert middle.extras["natives"]["s1"][0].secondary is True
    assert final.extras["natives"]["s1"][0].secondary is False


def test_the_same_face_at_two_stages_is_not_one_dimension_said_twice() -> None:
    turned, ground, plain = _ctx({"stage": "turned"}), _ctx({"stage": "ground"}), _ctx({})
    for ctx in (turned, ground, plain):
        Context.record_dimension(ctx, ("dia", 45.0, "face:1"), "front")  # type: ignore[arg-type]
    keys = [ctx.extras["dim_records"][0][2] for ctx in (turned, ground, plain)]
    assert len(set(keys)) == 3 and keys[2] == ("dia", 45.0, "face:1")  # a dimension with no stage keeps the key it always had


def test_a_secondary_dimension_is_another_object_than_a_primary_one() -> None:
    unit = lambda d: su.Unit("s1", "s1 dim", su.KIND_ITEM, (Line(0, 0, 1, 1, DIMENSIONS),), "dim", None, (d,))  # noqa: E731
    assert su.signature(unit(_dim())) != su.signature(unit(_dim(secondary=True)))
    assert su.signature(unit(_dim())) == su.signature(unit(_dim()))


def test_a_revision_names_the_dimensions_it_changed() -> None:
    row = items_notes.check_revision({"index": "A", "description": "Ø8 a Ø10", "at": ["h1", "d2"]})
    assert row["at"] == ["h1", "d2"]
    assert items_notes.check_revision({"index": "A", "description": "x", "at": "h1"})["at"] == ["h1"]
    with pytest.raises(ItemError):
        items_notes.check_revision({"index": "A", "description": "x", "at": ["not an id!"]})
    with pytest.raises(ItemError):
        items_notes.check_revision({"index": "A", "description": "x", "at": [f"d{n}" for n in range(7)]})


def _sheet(blocked: int = 0) -> SimpleNamespace:
    """A stand-in for the context: the dimension of item ``h1`` has its room at (5,10)-(25,30); the first ``blocked`` places beside it are taken."""
    tried: list[tuple[float, float, float, float]] = []
    ctx = SimpleNamespace(extras={"current": "r1", "natives": {"h1": [_dim()]}}, native_made=[], ink=[])
    ctx.add = lambda entities: ctx.ink.extend(entities)
    ctx.free = lambda box: tried.append(box) or len(tried) > blocked
    ctx.inside = lambda box: True
    ctx.reserve = lambda box: None
    ctx.native = lambda *objects: ctx.native_made.extend(objects)
    return ctx


def test_the_marker_is_freecads_revision_triangle_with_the_letter_beside_the_changed_dimension() -> None:
    ctx = _sheet()
    assert items_notes._mark_at(ctx, "B", "h1") is True  # type: ignore[arg-type]
    (mark,) = ctx.native_made
    assert isinstance(mark, sm.NativeRevisionMark) and mark.letter == "B" and mark.item == "r1"
    assert mark.at[0] > 25.0  # to the right of the room the dimension takes
    assert [type(e).__name__ for e in ctx.ink] == ["Polyline", "Text"] and ctx.ink[1].text == "B"  # the same triangle as ink, for a sheet with no window
    assert su.object_count(su.Unit("r1", "r1 revision", su.KIND_ITEM, (), "revision", None, (mark,))) == 1


def test_the_marker_takes_the_next_free_side_and_none_when_there_is_none() -> None:
    ctx = _sheet(blocked=1)
    assert items_notes._mark_at(ctx, "A", "h1") is True  # type: ignore[arg-type]
    assert ctx.native_made[0].at[0] < 5.0  # right is taken: left of it
    assert items_notes._mark_at(_sheet(blocked=9), "A", "h1") is False  # type: ignore[arg-type]
    assert items_notes._mark_at(_sheet(), "A", "nope") is False  # type: ignore[arg-type]  # no such dimension on the sheet
    assert sm.dump(sm.NativeRevisionMark("r1", "A", (30.0, 20.0))) != sm.dump(sm.NativeRevisionMark("r1", "B", (30.0, 20.0)))


TRIANGLE = '<svg><path d="M0,8 L-4,0 L4,0 Z"/><text freecad:editable="ID"><tspan style="font-size:4px">{}</tspan></text></svg>'


def test_a_library_symbol_with_its_editable_text_changed_is_still_the_librarys_own() -> None:
    import sheet_symbols  # noqa: PLC0415 - the module imports no FreeCAD at load

    sheet_symbols._cache["unit-test"] = {"misc/Revision_Triangle.svg": TRIANGLE.format("B")}
    try:
        assert sheet_symbols.is_library_text("unit-test", TRIANGLE.format("B"))
        assert sheet_symbols.is_library_text("unit-test", TRIANGLE.format("A"))  # the letter is the engineer's to set (EditableTexts)
        assert sheet_symbols.is_library_text("unit-test", TRIANGLE.format("A").replace('"', "'"))  # FreeCAD writes the file again with other quotes
        assert not sheet_symbols.is_library_text("unit-test", TRIANGLE.format("A").replace("L4,0", "L9,0"))  # another drawing is not
    finally:
        del sheet_symbols._cache["unit-test"]


def test_the_marker_goes_beside_the_callout_that_states_the_changed_value_when_the_item_has_several() -> None:
    near = _dim(expected=3.3, rect=(5.0, 10.0, 25.0, 30.0))  # a callout of the 6.6 holes (it measures a radius)
    far = _dim(expected=5.5, rect=(105.0, 10.0, 125.0, 30.0))  # the callout of the hole that became 11
    ctx = _sheet()
    ctx.extras["natives"] = {"h1": [near, far]}
    assert items_notes._mark_at(ctx, "A", "h1:11") is True  # type: ignore[arg-type]
    assert ctx.native_made[0].at[0] > 125.0
    assert items_notes.check_revision({"index": "A", "description": "x", "at": ["h1:11", "d2:40.5"]})["at"] == ["h1:11", "d2:40.5"]


# --- the review checks: they fire on the defect they name and stay silent on a correct sheet ----------------------------------

import sheet_checks  # noqa: E402


def _review(items: list[dict], natives: dict | None = None) -> SimpleNamespace:
    ctx = SimpleNamespace(extras={"natives": natives or {}}, say=lambda es, en: en)
    results = {i["id"]: SimpleNamespace(status="ok") for i in items}
    return SimpleNamespace(ctx=ctx, items=items, results=results)


def _stage(id: str, stage: str, size_mm: float | None = None, face: str = "face:1") -> dict:
    return {"id": id, "type": "dim", "kind": "diameter", "face": [face], "stage": stage, **({"size_mm": size_mm} if size_mm else {})}


def test_an_intermediate_size_with_no_final_size_of_the_same_feature_is_a_blocking_finding() -> None:
    found = sheet_checks._stage_sizes(_review([_stage("s1", "turned", 45.3)]))
    (finding,) = found
    assert finding.code == "stage_no_final" and finding.items == ("s1",)
    assert "stage_no_final" not in sheet_checks.NON_BLOCKING  # it holds the export


def test_a_correct_staged_feature_stays_clean() -> None:
    clean = [_stage("s1", "turned", 45.3), _stage("s3", "ground")]
    assert sheet_checks._stage_sizes(_review(clean)) == []
    other_face = [_stage("s1", "turned", 45.3), _stage("s3", "ground", face="face:1"), _stage("s4", "turned", 20.3, "face:5"), _stage("s5", "ground", None, "face:5")]
    assert sheet_checks._stage_sizes(_review(other_face)) == []  # each feature has its own final size
    assert sheet_checks._stage_sizes(_review([{"id": "d1", "type": "dim", "kind": "diameter", "face": ["face:1"]}])) == []  # no stage at all


def test_a_feature_staged_on_one_face_with_the_final_on_another_is_still_flagged() -> None:
    found = sheet_checks._stage_sizes(_review([_stage("s1", "turned", 45.3, "face:1"), _stage("s3", "ground", None, "face:2")]))
    assert [f.code for f in found] == ["stage_no_final"]


def test_a_revision_row_whose_markers_are_all_placed_stays_clean_and_a_missing_one_is_a_notice() -> None:
    row = {"id": "rev_A", "type": "revision", "index": "A", "description": "x", "at": ["hd:9", "d2:40"]}
    mark = sm.NativeRevisionMark("rev_A", "A", (30.0, 20.0))
    assert sheet_checks._revision_marks(_review([row], {"rev_A": [mark, mark]})) == []
    (found,) = sheet_checks._revision_marks(_review([row], {"rev_A": [mark]}))
    assert found.code == "revision_marker_missing" and "revision_marker_missing" in sheet_checks.NON_BLOCKING  # a released row is never rewritten: it warns
    assert sheet_checks._revision_marks(_review([{"id": "rev_B", "type": "revision", "index": "B", "description": "x"}])) == []  # a row with no at has no marker to miss
