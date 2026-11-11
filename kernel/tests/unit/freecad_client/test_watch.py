"""``worker/watch.py``'s policy (what is a hand edit of the DESIGN) and ``worker/changes.py``'s notion of the design,
in isolation: no FreeCAD, no Qt. Documents are stand-in objects, the clock is a fake scheduler run by the test."""

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"


def _load(name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, WORKER_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module  # the worker imports its siblings by their flat names
    spec.loader.exec_module(module)
    return module


changes = _load("changes")
debounce = _load("debounce")
watch = _load("watch")


class FakeObject:
    """The slice of a FreeCAD document object the capture reads."""

    def __init__(self, name: str, type_id: str = "Part::Box", outputs: tuple[str, ...] = (), **props: Any) -> None:
        self.Name = name
        self.TypeId = type_id
        self._outputs = outputs
        self.PropertiesList = ["Label", *props]
        self.Label = name
        for key, value in props.items():
            setattr(self, key, value)

    def getPropertyStatus(self, name: str) -> list[str]:  # noqa: N802 - FreeCAD's own method name
        return ["Output"] if name in self._outputs else []


class FakeConstraint:
    def __init__(self, kind: str, value: float, name: str = "", driving: bool = True) -> None:
        self.Type, self.Value, self.Name, self.Driving = kind, value, name, driving


class FakePoint:
    def __init__(self, x: float, y: float) -> None:
        self.x, self.y, self.z = x, y, 0.0


class FakeLine:
    def __init__(self, a: tuple[float, float], b: tuple[float, float]) -> None:
        self.StartPoint, self.EndPoint = FakePoint(*a), FakePoint(*b)


class FakeScheduler:
    def __init__(self) -> None:
        self.now = 0.0
        self._pending: dict[int, tuple[float, Any]] = {}
        self._next = 0

    def schedule(self, delay_s: float, callback: Any) -> int:
        self._next += 1
        self._pending[self._next] = (self.now + delay_s, callback)
        return self._next

    def cancel(self, handle: int) -> None:
        self._pending.pop(handle, None)

    def advance(self, seconds: float) -> None:
        self.now += seconds
        for handle, (deadline, callback) in sorted(self._pending.items(), key=lambda item: item[1][0]):
            if deadline <= self.now and handle in self._pending:
                del self._pending[handle]
                callback()


class Rig:
    """One watched document, ``plate``, and everything the watch needs from FreeCAD, scripted by the test."""

    QUIET = ("recompute", "undo", "redo")  # the events that name no object

    def __init__(self) -> None:
        self.docs: dict[str, list[FakeObject]] = {"plate": [FakeObject("Box", Length=10.0)]}
        self.reports: list[tuple[str, list[Any]]] = []
        self.captures: list[tuple[str, set[str] | None]] = []
        self.busy = False
        self.clock = FakeScheduler()
        self.watch = watch.ManualEditWatch(
            capture=self._capture,
            known=lambda name: name in self.docs,
            known_docs=lambda: list(self.docs),
            busy=lambda: self.busy,
            report=lambda name, edits: self.reports.append((name, edits)),
            schedule=self.clock.schedule,
            cancel=self.clock.cancel,
        )
        self.harness_op()  # the harness built the plate: that is the baseline
        self.captures.clear()

    def _capture(self, doc_name: str, names: set[str] | None) -> Any:
        self.captures.append((doc_name, None if names is None else set(names)))
        if doc_name not in self.docs:
            return None
        return changes.capture([o for o in self.docs[doc_name] if names is None or o.Name in names])

    def _events(self, causes: tuple[str, ...], names: tuple[str, ...]) -> None:
        for cause in causes:
            if cause in self.QUIET:
                self.watch.on_event("plate", cause)
            else:
                for name in names:
                    self.watch.on_event("plate", cause, name)

    def harness_op(self, change: Any = None, names: tuple[str, ...] = ("Box",)) -> None:
        self.watch.before_harness()
        self.busy = True
        if change is not None:
            change()
            self._events(("changed", "recompute"), names)
        self.busy = False
        self.watch.after_harness()

    def hand(self, change: Any, *causes: str, names: tuple[str, ...] = ("Box",)) -> None:
        change()
        self._events(causes or ("changed", "recompute"), names)

    def settle(self) -> None:
        self.clock.advance(watch.SETTLE_S + 0.01)

    @property
    def box(self) -> FakeObject:
        return self.docs["plate"][0]


def test_a_property_edited_by_hand_is_reported_once_the_burst_settles() -> None:
    rig = Rig()
    rig.hand(lambda: setattr(rig.box, "Length", 15.0))
    assert rig.reports == []  # still inside the debounce window
    rig.settle()
    assert len(rig.reports) == 1
    doc, edits = rig.reports[0]
    assert doc == "plate"
    assert [(e.object, e.action, e.lines) for e in edits] == [("Box", "edited", ["Length: 10.0 -> 15.0"])]
    assert edits[0].reason == "hand edit in the FreeCAD GUI"


def test_display_only_changes_report_nothing() -> None:
    """Hiding an object, or anything FreeCAD computes (an Output property), is not a change of the design."""
    rig = Rig()
    shown = [FakeObject("Box", outputs=("Label2",), Length=10.0, Visibility=True, Label2="a")]
    rig.harness_op(lambda: rig.docs.update(plate=shown))
    rig.hand(lambda: setattr(rig.box, "Visibility", False), "changed")
    rig.hand(lambda: setattr(rig.box, "Label2", "b"), "changed", "recompute")
    rig.settle()
    assert rig.reports == []


def test_events_that_change_nothing_of_the_design_report_nothing() -> None:
    """The object announced a change but its design is the same (the engineer toggled the view, saved, selected)."""
    rig = Rig()
    rig.hand(lambda: None, "recompute", "changed")
    rig.settle()
    assert rig.reports == []


def test_a_recompute_alone_costs_no_capture_and_a_change_captures_only_the_object_it_names() -> None:
    rig = Rig()
    rig.harness_op(lambda: rig.docs["plate"].extend(FakeObject(f"B{i}", Length=1.0) for i in range(50)), names=())
    rig.harness_op(lambda: None)  # a request that changes nothing
    rig.captures.clear()
    rig.hand(lambda: None, "recompute")
    rig.settle()
    assert rig.captures == []
    rig.hand(lambda: setattr(rig.docs["plate"][7], "Length", 2.0), names=("B6",))
    rig.settle()
    assert rig.captures == [("plate", {"B6"})]
    assert [e.object for e in rig.reports[0][1]] == ["B6"]


def test_what_the_harness_does_is_never_reported_not_even_by_a_later_event() -> None:
    rig = Rig()
    rig.harness_op(lambda: setattr(rig.box, "Length", 20.0))
    rig.settle()
    assert rig.reports == []
    rig.hand(lambda: None, "recompute")  # an unrelated recompute afterwards: the baseline already holds 20
    rig.settle()
    assert rig.reports == []
    rig.hand(lambda: setattr(rig.box, "Length", 25.0))
    rig.settle()
    assert [e.lines for e in rig.reports[0][1]] == [["Length: 20.0 -> 25.0"]]


def test_a_hand_edit_just_before_a_harness_request_is_reported_first_and_not_swallowed() -> None:
    rig = Rig()
    rig.hand(lambda: setattr(rig.box, "Length", 15.0))
    assert rig.reports == []
    rig.harness_op(lambda: setattr(rig.box, "Length", 40.0))  # the debounce has not fired yet
    assert [e.lines for e in rig.reports[0][1]] == [["Length: 10.0 -> 15.0"]]
    rig.settle()
    assert len(rig.reports) == 1  # the harness's own 15 -> 40 is not reported


def test_a_harness_request_that_removes_an_object_leaves_no_ghost_in_the_baseline() -> None:
    rig = Rig()
    rig.harness_op(lambda: rig.docs["plate"].clear(), names=("Box",))
    rig.hand(lambda: rig.docs["plate"].append(FakeObject("Box", Length=3.0)), "added", names=("Box",))
    rig.settle()
    assert [(e.object, e.action) for e in rig.reports[0][1]] == [("Box", "added")]  # new, not "edited from 10"


def test_an_object_added_by_hand_is_reported_with_its_type() -> None:
    rig = Rig()
    rig.hand(
        lambda: rig.docs["plate"].append(FakeObject("Cylinder", "Part::Cylinder", Radius=3.0)),
        "added",
        "recompute",
        names=("Cylinder",),
    )
    rig.settle()
    ((_, edits),) = rig.reports
    assert [(e.object, e.action, e.type_id, e.lines) for e in edits] == [
        ("Cylinder", "added", "Part::Cylinder", ["added Part::Cylinder"])
    ]


def test_an_object_removed_by_hand_is_reported_with_the_type_it_had() -> None:
    rig = Rig()
    rig.hand(lambda: rig.docs["plate"].clear(), "removed")
    rig.settle()
    ((_, edits),) = rig.reports
    assert [(e.object, e.action, e.label, e.lines) for e in edits] == [("Box", "removed", "Box", ["removed Part::Box"])]


def test_a_rename_by_hand_is_reported_even_though_nothing_recomputed() -> None:
    rig = Rig()
    rig.hand(lambda: setattr(rig.box, "Label", "Base plate"), "changed")
    rig.settle()
    assert [e.lines for e in rig.reports[0][1]] == [["Label: 'Box' -> 'Base plate'"]]


@pytest.mark.parametrize(
    ("cause", "reason"), [("undo", "undo in the FreeCAD GUI"), ("redo", "redo in the FreeCAD GUI")]
)
def test_an_undo_or_a_redo_by_the_engineer_says_so_and_looks_at_the_whole_document(cause: str, reason: str) -> None:
    rig = Rig()
    rig.hand(lambda: setattr(rig.box, "Length", 5.0), cause, "recompute")  # an undo names no object
    rig.settle()
    assert rig.reports[0][1][0].reason == reason
    assert rig.captures == [("plate", None)]


def test_several_edits_in_one_burst_are_one_report() -> None:
    rig = Rig()
    rig.hand(lambda: setattr(rig.box, "Length", 15.0))
    rig.clock.advance(0.1)
    rig.hand(lambda: setattr(rig.box, "Length", 18.0))  # restarts the countdown
    rig.clock.advance(0.2)
    assert rig.reports == []
    rig.settle()
    assert len(rig.reports) == 1
    assert rig.reports[0][1][0].lines == ["Length: 10.0 -> 18.0"]


def test_a_document_the_harness_does_not_track_is_ignored() -> None:
    rig = Rig()
    rig.watch.on_event("opened_by_hand", "changed", "Box")
    rig.settle()
    assert rig.reports == [] and rig.captures == []


def test_a_long_list_is_cut_and_the_rest_counted() -> None:
    rig = Rig()
    many = [FakeObject(f"B{i}", Length=1.0) for i in range(watch.MAX_EDITS + 7)]
    all_names = tuple(o.Name for o in many)
    rig.harness_op(lambda: rig.docs.update(plate=many), names=all_names)
    rig.hand(lambda: [setattr(o, "Length", 2.0) for o in rig.docs["plate"]], names=all_names)
    rig.settle()
    edits = rig.reports[0][1]
    assert len(edits) == watch.MAX_EDITS + 1
    assert (edits[-1].action, edits[-1].lines) == ("more", ["7 more objects changed"])


def test_the_first_capture_of_a_document_only_seeds_the_baseline() -> None:
    rig = Rig()
    rig.watch.forget("plate")
    rig.hand(lambda: setattr(rig.box, "Length", 15.0))
    rig.settle()
    assert rig.reports == []  # nothing to compare with
    rig.hand(lambda: setattr(rig.box, "Length", 16.0))
    rig.settle()
    assert [e.lines for e in rig.reports[0][1]] == [["Length: 15.0 -> 16.0"]]


def test_a_capture_that_raises_does_not_take_the_worker_down() -> None:
    rig = Rig()

    def broken(name: str, names: Any) -> Any:
        raise RuntimeError(name)

    rig.watch._capture = broken
    rig.hand(lambda: setattr(rig.box, "Length", 15.0))
    rig.settle()
    assert rig.reports == []


def test_a_document_opened_by_a_harness_request_gets_its_baseline_when_the_request_ends() -> None:
    rig = Rig()
    rig.watch.before_harness()
    rig.busy = True
    rig.docs["part"] = [FakeObject("Pad", Length=5.0)]  # opened by the request: no event reached the watch for it
    rig.busy = False
    rig.watch.after_harness()
    rig.watch.on_event("part", "changed", "Pad")
    rig.docs["part"][0].Length = 6.0
    rig.settle()
    assert [(name, e.lines) for name, edits in rig.reports for e in edits] == [("part", ["Length: 5.0 -> 6.0"])]


# --- what the capture counts as the design -----------------------------------------------------------------------


def test_capture_leaves_out_display_internal_and_computed_properties() -> None:
    obj = FakeObject(
        "Pad",
        "PartDesign::Pad",
        outputs=("Label2", "Area"),
        Length=5.0,
        Visibility=True,
        _ElementMapVersion="15.70",
        Label2="x",
        Area=3.0,
    )
    assert changes.capture([obj]).props == {"Pad": {"Label": "Pad", "Length": 5.0}}


def test_snapshot_stays_the_fingerprint_of_everything_scalar_for_run_native() -> None:
    """``native_state`` tells that a fragment changed something from ``snapshot``: a description (an Output property)
    or a visibility it sets must still move it, whatever ``capture`` leaves out of the design."""
    obj = FakeObject("Box", outputs=("Label2",), Length=10.0, Visibility=True, Label2="a")
    assert changes.snapshot([obj])["Box"] == {"Label": "Box", "Length": 10.0, "Visibility": True, "Label2": "a"}
    assert changes.capture([obj]).props["Box"] == {"Label": "Box", "Length": 10.0}


def test_capture_remembers_the_type_of_each_object_and_skips_drawings() -> None:
    sheet = FakeObject("Page", "TechDraw::DrawPage", Scale=1.0)
    snap = changes.capture([FakeObject("Box"), sheet])
    assert snap.kinds == {"Box": "Part::Box"}


def test_a_sketch_edited_by_hand_shows_its_dimension_and_that_its_geometry_changed() -> None:
    def sketch(width: float, end: float) -> FakeObject:
        obj = FakeObject("Sketch", "Sketcher::SketchObject")
        obj.Constraints = [FakeConstraint("Distance", width, "Width"), FakeConstraint("Coincident", 0.0)]
        obj.Geometry = [FakeLine((0, 0), (end, 0))]
        return obj

    before = changes.capture([sketch(10.0, 10.0)]).props
    after = changes.capture([sketch(12.0, 12.0)]).props
    assert changes.diff(before, after) == {"Sketch": ["constraint Width: 10.0 -> 12.0", "geometry: changed"]}
    assert changes.diff(before, changes.capture([sketch(10.0, 10.0)]).props) == {}


def test_a_sketch_gaining_a_constraint_counts_it() -> None:
    plain = FakeObject("Sketch", "Sketcher::SketchObject")
    plain.Constraints, plain.Geometry = [], [FakeLine((0, 0), (1, 0))]
    more = FakeObject("Sketch", "Sketcher::SketchObject")
    more.Constraints, more.Geometry = [FakeConstraint("Horizontal", 0.0)], [FakeLine((0, 0), (1, 0))]
    assert changes.diff(changes.capture([plain]).props, changes.capture([more]).props) == {
        "Sketch": ["constraints: 0 -> 1"]
    }
