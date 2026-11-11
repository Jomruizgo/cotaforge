"""``worker/sketch_defined.py`` in isolation: no FreeCAD. Sketches and features are stand-ins that answer what the
module reads (``DoF``, ``Constraints``, ``InList``, ``getGeometryWithDependentParameters``...)."""

import importlib.util
import sys
from pathlib import Path
from typing import Any

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, WORKER_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


sd = _load("sketch_defined")


class Constraint:
    def __init__(self, kind: str, active: bool = True) -> None:
        self.Type = kind
        self.Active = active


class Geom:
    def __init__(self, type_id: str) -> None:
        self.TypeId = type_id


class Node:
    """A document object: a sketch, a pad, a body or a datum."""

    def __init__(self, name: str, type_id: str, bases: tuple[str, ...] = ()) -> None:
        self.Name = name
        self.TypeId = type_id
        self._bases = {type_id, *bases}
        self.InList: list[Node] = []

    def isDerivedFrom(self, type_id: str) -> bool:  # noqa: N802 - FreeCAD's own method name
        return type_id in self._bases


def pad(name: str = "Pad") -> Node:
    return Node(name, "PartDesign::Pad", ("Part::Feature", "PartDesign::Feature"))


class Sketch(Node):
    def __init__(
        self,
        name: str = "Sketch",
        *,
        dof: int = 0,
        constraints: tuple[Constraint, ...] = (),
        free: list[tuple[int, int]] | None = None,
        geometry: tuple[str, ...] = (),
        dependent_api: bool = True,
    ) -> None:
        super().__init__(name, "Sketcher::SketchObject", ("Part::Part2DObject", "Part::Feature"))
        self.DoF = dof
        self.Constraints = list(constraints)
        self.State: list[str] = []
        self.Geometry = [Geom(t) for t in geometry]
        self._free = free or []
        self._dependent_api = dependent_api
        self.solved = 0
        self.purged = 0
        self.touch_on_solve = False

    def solve(self) -> int:
        self.solved += 1
        if self.touch_on_solve:
            self.State = ["Touched"]
        return 0

    def purgeTouched(self) -> None:  # noqa: N802
        self.purged += 1
        self.State = []

    def getGeometryWithDependentParameters(self) -> list[tuple[int, int]]:  # noqa: N802
        if not self._dependent_api:
            raise AttributeError("getGeometryWithDependentParameters")
        return self._free


class Doc:
    def __init__(self, *objects: Node) -> None:
        self.Objects = list(objects)


def used_by(sketch: Sketch, *features: Node) -> None:
    sketch.InList = list(features)


# --- judging one sketch ---------------------------------------------------------------------------------------------


def test_a_sketch_with_no_degree_of_freedom_and_no_block_is_fully_defined() -> None:
    assert sd.judge(Sketch(dof=0, constraints=(Constraint("Coincident"), Constraint("DistanceX")))) is None


def test_the_degrees_of_freedom_are_counted_and_the_free_geometry_named() -> None:
    sketch = Sketch(
        dof=3,
        geometry=("Part::GeomLineSegment", "Part::GeomCircle", "Part::GeomArcOfCircle"),
        free=[(0, 2), (0, 2), (1, 3), (2, 0), (-1, 1)],
    )
    verdict = sd.judge(sketch)
    assert verdict is not None and verdict.reason == sd.NOT_DEFINED and verdict.dof == 3
    assert verdict.free == ("line 0 end", "circle 1 centre", "arc 2")  # repeated pairs once, the axis not at all
    text = verdict.text("the sketch s1")
    assert "3 degree(s) of freedom" in text and "line 0 end, circle 1 centre, arc 2" in text


def test_the_free_geometry_is_capped_so_the_message_stays_short() -> None:
    sketch = Sketch(dof=9, geometry=("Part::GeomLineSegment",) * 10, free=[(i, 1) for i in range(10)])
    verdict = sd.judge(sketch)
    assert verdict is not None and len(verdict.free) == sd.MAX_LISTED + 1
    assert verdict.free[-1] == "and 4 more"


def test_a_build_that_cannot_name_the_free_geometry_gives_the_number_alone() -> None:
    verdict = sd.judge(Sketch(dof=2, dependent_api=False))
    assert verdict is not None and verdict.free == ()
    assert "2 degree(s) of freedom" in verdict.text("it") and "still free" not in verdict.text("it")


def test_a_block_constraint_is_not_a_definition_even_when_the_solver_reports_none_left() -> None:
    sketch = Sketch(dof=0, constraints=(Constraint("Block"), Constraint("Block"), Constraint("DistanceX")))
    verdict = sd.judge(sketch)
    assert verdict is not None and verdict.reason == sd.HAS_BLOCK and verdict.blocks == 2
    assert "2 Block constraint(s)" in verdict.text("it") and "dimension" in verdict.text("it")


def test_a_block_that_was_switched_off_does_not_count() -> None:
    assert sd.judge(Sketch(dof=0, constraints=(Constraint("Block", active=False),))) is None


def test_solving_does_not_leave_a_clean_sketch_marked_as_changed() -> None:
    sketch = Sketch(dof=0)
    sketch.touch_on_solve = True
    assert sd.judge(sketch) is None
    assert sketch.solved == 1 and sketch.purged == 1 and sketch.State == []


def test_a_sketch_that_was_touched_before_stays_touched() -> None:
    sketch = Sketch(dof=0)
    sketch.State = ["Touched"]
    sd.judge(sketch)
    assert sketch.purged == 0 and sketch.State == ["Touched"]


# --- who uses a sketch ----------------------------------------------------------------------------------------------


def test_only_a_feature_counts_as_using_a_sketch() -> None:
    body = Node("Body", "PartDesign::Body", ("Part::Feature",))
    datum = Node("Plane", "PartDesign::Plane", ("Part::Datum", "Part::Feature"))
    other_sketch = Sketch("Other")
    feature = pad()
    sketch = Sketch()
    used_by(sketch, body, datum, other_sketch, feature)
    assert sd.users(sketch) == [feature]


def test_a_shape_binder_only_reads_a_sketch_until_a_feature_is_built_on_the_binder() -> None:
    sketch = Sketch()
    binder = Node("Binder", "PartDesign::ShapeBinder", ("Part::Feature",))
    sub_binder = Node("SubBinder", "PartDesign::SubShapeBinder", ("Part::Feature",))
    used_by(sketch, binder, sub_binder)
    binder.InList = [Node("Body", "PartDesign::Body", ("Part::Feature",))]
    sub_binder.InList = []
    assert sd.users(sketch) == []  # a reference, not a use: the sketch may stay under defined
    assert sd.notes(Doc(sketch)) == []
    feature = pad("Boss")
    binder.InList.append(feature)
    assert sd.users(sketch) == [binder]  # the binder is now the profile of a feature
    chained = Node("Outer", "PartDesign::ShapeBinder", ("Part::Feature",))
    chained.InList = [binder, feature]  # a cycle through the binder must not loop forever
    binder.InList.append(chained)
    sub_binder.InList = [chained]
    assert sd.users(sketch) == [binder, sub_binder]


# --- a native step --------------------------------------------------------------------------------------------------


def test_a_step_that_builds_a_feature_on_an_under_defined_sketch_is_blamed() -> None:
    sketch, feature = Sketch(dof=2), pad("Boss")
    used_by(sketch, feature)
    found = sd.unfit_uses(Doc(sketch, feature), before={}, after={"Sketch": {"a": 1}, "Boss": {"a": 1}})
    assert list(found) == ["Sketch"]
    assert found["Sketch"][0].startswith("sketch_not_fully_defined: ") and "Boss" in found["Sketch"][0]


def test_a_step_that_builds_a_feature_on_a_sketch_held_by_blocks_is_blamed() -> None:
    sketch, feature = Sketch(dof=0, constraints=(Constraint("Block"),)), pad()
    used_by(sketch, feature)
    found = sd.unfit_uses(Doc(sketch, feature), before={}, after={"Sketch": {}, "Pad": {}})
    assert found["Sketch"][0].startswith("sketch_has_block: ")


def test_a_step_may_leave_a_sketch_under_defined_when_no_feature_uses_it() -> None:
    sketch = Sketch(dof=5)
    assert sd.unfit_uses(Doc(sketch), before={}, after={"Sketch": {"a": 1}}) == {}


def test_a_sketch_and_a_feature_the_step_did_not_touch_are_not_blamed_on_it() -> None:
    sketch, feature = Sketch(dof=4), pad()
    used_by(sketch, feature)
    same = {"Sketch": {"a": 1}, "Pad": {"a": 1}, "Elsewhere": {"a": 1}}
    assert sd.unfit_uses(Doc(sketch, feature), before=same, after={**same, "Elsewhere": {"a": 2}}) == {}


def test_a_step_that_changes_the_sketch_or_points_a_feature_at_it_is_blamed() -> None:
    sketch, feature = Sketch(dof=4), pad()
    used_by(sketch, feature)
    before = {"Sketch": {"a": 1}, "Pad": {"links": {"Profile": "Other"}}}
    assert sd.unfit_uses(Doc(sketch, feature), before, {**before, "Sketch": {"a": 2}}) != {}
    relinked = {**before, "Pad": {"links": {"Profile": "Sketch"}}}
    assert sd.unfit_uses(Doc(sketch, feature), before, relinked) != {}


def test_a_step_that_only_edits_a_property_of_the_feature_is_not_a_new_use() -> None:
    """A file brought an under defined sketch under its pad; changing the pad's length is not building a feature on
    it, so the step stands (the sketch is refused the moment something is built on it again)."""
    sketch, feature = Sketch(dof=4), pad()
    used_by(sketch, feature)
    before = {"Sketch": {"a": 1}, "Pad": {"scalars": {"Length": 5}, "links": {"Profile": "Sketch"}}}
    after = {**before, "Pad": {"scalars": {"Length": 9}, "links": {"Profile": "Sketch"}}}
    assert sd.unfit_uses(Doc(sketch, feature), before, after) == {}


def _on_a_face(placement_z: float, support: str = "Pad") -> dict[str, Any]:
    return {"frames": {"Placement": [0, 0, placement_z]}, "links": {"AttachmentSupport": [[support, ["Face6"]]]}}


def test_a_sketch_that_followed_its_support_is_not_an_edit_of_the_sketch() -> None:
    """A sketch of an old file sits on the top face of a pad and is not fully defined. A step that only changes the
    pad's length moves the sketch's Placement (it is attached to the face): that is not the step editing the sketch."""
    sketch, pocket = Sketch(dof=2), Node("Pocket", "PartDesign::Pocket", ("Part::Feature",))
    used_by(sketch, pocket)
    links = {"links": {"Profile": "Sketch"}}
    before = {"Sketch": _on_a_face(10), "Pocket": links}
    after = {"Sketch": _on_a_face(20), "Pocket": links}
    assert sd.unfit_uses(Doc(sketch, pocket), before, after) == {}


def test_what_the_sketch_owns_is_still_an_edit_even_when_its_placement_moved_too() -> None:
    sketch, pocket = Sketch(dof=2), Node("Pocket", "PartDesign::Pocket", ("Part::Feature",))
    used_by(sketch, pocket)
    links = {"links": {"Profile": "Sketch"}}
    before = {"Sketch": {**_on_a_face(10), "sketch": {"geometry": ["a"]}}, "Pocket": links}
    for changed in (
        {**_on_a_face(20), "sketch": {"geometry": ["b"]}},  # its geometry
        {**_on_a_face(20), "sketch": {"geometry": ["a"], "constraints": [["Distance"]]}},  # its constraints
        {**_on_a_face(20, "Other"), "sketch": {"geometry": ["a"]}},  # its support
        {**_on_a_face(10), "sketch": {"geometry": ["a"]}, "frames": {"Placement": [0, 0, 10], "AttachmentOffset": [1]}},
    ):
        assert sd.unfit_uses(Doc(sketch, pocket), before, {"Sketch": changed, "Pocket": links}) != {}


def test_a_step_that_builds_a_feature_on_a_binder_of_a_free_sketch_is_blamed() -> None:
    sketch, binder, feature = Sketch(dof=3), Node("Binder", "PartDesign::ShapeBinder", ("Part::Feature",)), pad("Boss")
    used_by(sketch, binder)
    binder.InList = [feature]
    before = {"Sketch": {"a": 1}, "Binder": {"links": {"Support": "Sketch"}}}
    assert sd.unfit_uses(Doc(sketch, binder, feature), before, {**before, "Boss": {}}) != {}
    old_pad = {**before, "Boss": {"links": {"Profile": "Binder"}}}
    assert sd.unfit_uses(Doc(sketch, binder, feature), old_pad, old_pad) == {}  # already there: not this step's


def test_a_step_that_defines_the_sketch_passes() -> None:
    sketch, feature = Sketch(dof=0), pad()
    used_by(sketch, feature)
    assert sd.unfit_uses(Doc(sketch, feature), before={}, after={"Sketch": {}, "Pad": {}}) == {}


# --- the lines of check ---------------------------------------------------------------------------------------------


def test_check_lists_the_sketches_behind_a_feature_that_are_not_fully_defined() -> None:
    loose, held, fine, spare = (
        Sketch("Loose", dof=2),
        Sketch("Held", constraints=(Constraint("Block"),)),
        Sketch("Fine"),
        Sketch("Spare", dof=7),
    )
    for index, sketch in enumerate((loose, held, fine)):
        used_by(sketch, pad(f"Pad{index}"))
    lines = sd.notes(Doc(loose, held, fine, spare))
    assert lines[0] == "sketches: 1 of 3 behind a feature fully defined by dimensions"
    assert lines[1].startswith("sketch Loose: it still has 2 degree(s) of freedom")
    assert lines[2].startswith("sketch Held: it holds 1 Block constraint(s)")
    assert len(lines) == 3  # Spare is a construction sketch: no feature uses it


def test_check_says_nothing_when_all_is_defined_or_there_is_no_sketch() -> None:
    fine = Sketch()
    used_by(fine, pad())
    assert sd.notes(Doc(fine)) == [] and sd.notes(Doc()) == []
