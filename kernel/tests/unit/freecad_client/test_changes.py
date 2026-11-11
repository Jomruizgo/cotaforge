"""``worker/changes.py``'s property snapshot/diff, in isolation: no FreeCAD needed, against plain
stand-in objects (duck-typed: only ``.Name`` and ``.PropertiesList`` + attribute access are used)."""

import importlib.util
import sys
from pathlib import Path
from typing import Any


class FakeObject:
    def __init__(self, name: str, **props: Any) -> None:
        self.Name = name
        self.PropertiesList = list(props)
        for key, value in props.items():
            setattr(self, key, value)


class FakeQuantity:
    """Duck-typed stand-in for FreeCAD's ``Base.Quantity`` (what ``App::PropertyLength``/``PropertyAngle``
    return from Python, e.g. ``Part::Box.Length`` is ``10.0 mm``, not the plain float ``10.0``)."""

    def __init__(self, value: float) -> None:
        self.Value = value

    def __repr__(self) -> str:
        return f"{self.Value} mm"


class RaisingObject(FakeObject):
    """A property whose getter raises (a stale/partially-built object) must not crash the snapshot."""

    def __getattribute__(self, name: str) -> Any:
        if name == "Broken":
            msg = "not available right now"
            raise RuntimeError(msg)
        return object.__getattribute__(self, name)


WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"


def _load_changes() -> Any:
    spec = importlib.util.spec_from_file_location("worker_changes", WORKER_DIR / "changes.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


changes = _load_changes()


def test_snapshot_keeps_only_scalar_properties() -> None:
    obj = FakeObject("Pad", Length=10.0, Reversed=False, Name2="pad", Shape=object())
    snap = changes.snapshot([obj])
    assert snap == {"Pad": {"Length": 10.0, "Reversed": False, "Name2": "pad"}}


def test_snapshot_skips_a_property_getter_that_raises() -> None:
    obj = RaisingObject("Pad", Length=10.0, Broken=1)
    snap = changes.snapshot([obj])
    assert snap == {"Pad": {"Length": 10.0}}


def test_diff_reports_only_changed_scalar_properties() -> None:
    before = changes.snapshot([FakeObject("Pad", Length=10.0, Reversed=False)])
    after = changes.snapshot([FakeObject("Pad", Length=15.0, Reversed=False)])
    assert changes.diff(before, after) == {"Pad": ["Length: 10.0 -> 15.0"]}


def test_snapshot_leaves_drawing_objects_out_a_sheet_recomputing_is_not_a_hand_edit() -> None:
    sheet = FakeObject("Drawing", TypeId="TechDraw::DrawPage", Scale=1.0)
    view = FakeObject("ProjItem", TypeId="TechDraw::DrawProjGroupItem", X=3.0)
    pad = FakeObject("Pad", TypeId="PartDesign::Pad", Length=10.0)
    assert changes.snapshot([pad, sheet, view]) == {"Pad": {"TypeId": "PartDesign::Pad", "Length": 10.0}}


def test_diff_is_empty_when_nothing_changed() -> None:
    before = changes.snapshot([FakeObject("Pad", Length=10.0)])
    after = changes.snapshot([FakeObject("Pad", Length=10.0)])
    assert changes.diff(before, after) == {}


def test_diff_skips_an_object_absent_from_the_before_snapshot() -> None:
    """A freshly created object (e.g. by hand, or the very first recompute of a new document) has no
    ``before`` entry -- it is not reported, see the module docstring."""
    before: changes.Snapshot = {}
    after = changes.snapshot([FakeObject("Pad", Length=10.0)])
    assert changes.diff(before, after) == {}


def test_snapshot_unwraps_a_quantity_like_property_to_its_plain_value() -> None:
    """The real bug this guards: FreeCAD's dimensioned properties are ``Base.Quantity`` objects, not
    plain floats -- treating them as "not a scalar" would silently drop every ``Length``/``Angle``/...
    property from tracking, and a hand-edited dimension is exactly the case the manual-edit notice
    exists for."""
    obj = FakeObject("Box", Length=FakeQuantity(10.0), Shape=object())
    snap = changes.snapshot([obj])
    assert snap == {"Box": {"Length": 10.0}}


def test_diff_reports_a_changed_quantity_like_property_by_its_plain_value() -> None:
    before = changes.snapshot([FakeObject("Box", Length=FakeQuantity(10.0))])
    after = changes.snapshot([FakeObject("Box", Length=FakeQuantity(15.0))])
    assert changes.diff(before, after) == {"Box": ["Length: 10.0 -> 15.0"]}


def test_diff_reports_every_changed_object_independently() -> None:
    before = changes.snapshot([FakeObject("Pad", Length=10.0), FakeObject("Pocket", Depth=5.0)])
    after = changes.snapshot([FakeObject("Pad", Length=12.0), FakeObject("Pocket", Depth=5.0)])
    assert changes.diff(before, after) == {"Pad": ["Length: 10.0 -> 12.0"]}
