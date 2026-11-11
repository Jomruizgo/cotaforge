"""``worker/sketches.py`` in isolation: no FreeCAD. Sketches, datums and features are stand-ins that answer what
the module reads (``TypeId``, ``isDerivedFrom``, ``InList``, ``Visibility``, ``getObject``...).

What is checked: a datum plane, line or point (and the sketches that hang on it) is hidden once a feature uses it; one
nothing uses yet stays shown; what the engineer showed by hand is not hidden again; the origin of a body is left alone;
and a hidden reference can still be found by name."""

import importlib.util
import sys
import types
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"


def _load(name: str, **fakes: Any) -> Any:
    for key, module in fakes.items():
        sys.modules[key] = module
    spec = importlib.util.spec_from_file_location(name, WORKER_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@contextmanager
def _unmodified(_doc: Any) -> Iterator[None]:
    yield


@pytest.fixture
def sketches(monkeypatch: pytest.MonkeyPatch) -> Any:
    gui = types.ModuleType("gui")
    gui.unmodified = _unmodified  # type: ignore[attr-defined]
    for name in ("sketch_defined", "sketches"):
        monkeypatch.setitem(sys.modules, name, None)  # what was there comes back after the test
    monkeypatch.setitem(sys.modules, "gui", gui)
    _load("sketch_defined")
    return _load("sketches")


class Node:
    """A document object: a sketch, a datum, a body, an origin feature or a feature."""

    def __init__(self, name: str, type_id: str, bases: tuple[str, ...] = (), visible: bool = True) -> None:
        self.Name = name
        self.Label = name
        self.TypeId = type_id
        self._bases = {type_id, *bases}
        self.InList: list[Node] = []
        self.Visibility = visible

    def isDerivedFrom(self, type_id: str) -> bool:  # noqa: N802 - FreeCAD's own method name
        return type_id in self._bases


class Doc:
    def __init__(self, *objects: Node) -> None:
        self.Objects = list(objects)

    def getObject(self, name: str) -> Node | None:  # noqa: N802
        return next((o for o in self.Objects if o.Name == name), None)

    def getObjectsByLabel(self, label: str) -> list[Node]:  # noqa: N802
        return [o for o in self.Objects if o.Label == label]


def sketch(name: str = "Sketch", visible: bool = True) -> Node:
    return Node(name, "Sketcher::SketchObject", visible=visible)


def plane(name: str = "DatumPlane", visible: bool = True) -> Node:
    return Node(name, "PartDesign::Plane", ("Part::Datum",), visible)


def line(name: str = "DatumLine", visible: bool = True) -> Node:
    return Node(name, "PartDesign::Line", ("Part::Datum",), visible)


def point(name: str = "DatumPoint", visible: bool = True) -> Node:
    return Node(name, "PartDesign::Point", ("Part::Datum",), visible)


def feature(kind: str, name: str | None = None) -> Node:
    return Node(name or kind.split("::")[-1], kind, ("Part::Feature", "PartDesign::Feature"))


def body() -> Node:
    return Node("Body", "PartDesign::Body")


def use(user: Node, *used: Node) -> None:
    """``user`` links to each of ``used`` (FreeCAD: the user is in the ``InList`` of what it links to)."""
    for obj in used:
        obj.InList.append(user)


def hang(plane_: Node, sk: Node, pad: Node, main: Node) -> Doc:
    """A sketch attached to a plane, a pad built on the sketch; the body holds them all."""
    use(sk, plane_)
    use(pad, sk)
    for obj in (plane_, sk, pad):
        use(main, obj)
    return Doc(main, plane_, sk, pad)


def test_a_plane_used_by_a_sketch_a_pad_is_built_on_is_hidden_with_the_sketch(sketches: Any) -> None:
    p, s, pad = plane(), sketch(), feature("PartDesign::Pad")
    doc = hang(p, s, pad, body())
    assert sorted(sketches.hide_consumed(doc)) == ["DatumPlane", "Sketch"]
    assert (p.Visibility, s.Visibility) == (False, False)


def test_a_plane_nothing_uses_yet_stays_shown(sketches: Any) -> None:
    waiting = plane("Plane2")
    done_plane, s, pad = plane("Plane1"), sketch(), feature("PartDesign::Pad")
    doc = hang(done_plane, s, pad, body())
    doc.Objects.append(waiting)
    sketches.hide_consumed(doc)
    assert waiting.Visibility is True
    assert done_plane.Visibility is False


def test_a_plane_with_a_sketch_that_no_feature_uses_yet_stays_shown_with_it(sketches: Any) -> None:
    p, s = plane(), sketch()
    use(s, p)
    doc = Doc(body(), p, s)
    assert sketches.hide_consumed(doc) == []
    assert (p.Visibility, s.Visibility) == (True, True)


def test_a_plane_made_for_a_plane_that_was_used_is_used_too(sketches: Any) -> None:
    base, offset, s, pad = plane("Plane1"), plane("Plane2"), sketch(), feature("PartDesign::Pad")
    use(offset, base)
    doc = hang(offset, s, pad, body())
    doc.Objects.append(base)
    sketches.hide_consumed(doc)
    assert (base.Visibility, offset.Visibility) == (False, False)


def test_an_axis_and_a_mirror_plane_a_feature_links_to_are_hidden(sketches: Any) -> None:
    axis, mirror = line("RevolveAxis"), plane("MirrorPlane")
    revolution, mirrored = feature("PartDesign::Revolution"), feature("PartDesign::Mirrored")
    use(revolution, axis)
    use(mirrored, mirror)
    doc = Doc(body(), axis, mirror, revolution, mirrored)
    assert sorted(sketches.hide_consumed(doc)) == ["MirrorPlane", "RevolveAxis"]


def test_a_point_a_loft_section_hangs_on_is_hidden(sketches: Any) -> None:
    tip, s1, s2, loft = point("Tip"), sketch("S1"), sketch("S2"), feature("PartDesign::AdditiveLoft")
    use(s1, tip)
    use(loft, s1, s2)
    doc = Doc(body(), tip, s1, s2, loft)
    assert sorted(sketches.hide_consumed(doc)) == ["S1", "S2", "Tip"]


@pytest.mark.parametrize("kind", ["PartDesign::AdditivePipe", "PartDesign::SubtractiveLoft", "PartDesign::Hole"])
def test_sketches_other_features_consume_hide_too(sketches: Any, kind: str) -> None:
    s, user = sketch(), feature(kind)
    use(user, s)
    doc = Doc(body(), s, user)
    assert sketches.hide_consumed(doc) == ["Sketch"]


def test_what_was_already_consumed_and_is_shown_stays_shown(sketches: Any) -> None:
    p, s, pad = plane(), sketch(), feature("PartDesign::Pad")
    doc = hang(p, s, pad, body())
    known = sketches.consumed_names(doc)
    assert known == {"DatumPlane", "Sketch"}  # the engineer shows them by hand after this
    second = feature("PartDesign::Pocket", "Pocket")
    use(second, s)  # the next step uses the same sketch again
    doc.Objects.append(second)
    assert sketches.hide_consumed(doc, known) == []
    assert (p.Visibility, s.Visibility) == (True, True)


def test_a_sketch_and_a_plane_that_became_consumed_in_this_step_are_hidden_beside_one_the_engineer_showed(
    sketches: Any,
) -> None:
    p1, s1, pad1 = plane("Plane1"), sketch("S1"), feature("PartDesign::Pad", "Pad1")
    doc = hang(p1, s1, pad1, body())
    known = sketches.consumed_names(doc)
    p2, s2, pad2 = plane("Plane2"), sketch("S2"), feature("PartDesign::Pocket", "Pocket")
    use(s2, p2)
    use(pad2, s2)
    doc.Objects.extend([p2, s2, pad2])
    assert sorted(sketches.hide_consumed(doc, known)) == ["Plane2", "S2"]
    assert (p1.Visibility, s1.Visibility) == (True, True)


def test_the_origin_of_a_body_is_not_touched(sketches: Any) -> None:
    origin_plane = Node("XY_Plane", "App::Plane")
    s, pad = sketch(), feature("PartDesign::Pad")
    use(s, origin_plane)
    use(pad, s)
    doc = Doc(body(), origin_plane, s, pad)
    sketches.hide_consumed(doc)
    assert origin_plane.Visibility is True


def test_opening_a_document_tidies_the_sketches_but_leaves_its_datums(sketches: Any) -> None:
    p, s, pad = plane(), sketch(), feature("PartDesign::Pad")
    doc = hang(p, s, pad, body())
    assert sketches.hide_consumed(doc, datums=False) == ["Sketch"]
    assert p.Visibility is True


def test_a_hidden_plane_and_a_hidden_sketch_are_found_by_name_or_by_label_and_shown_again(sketches: Any) -> None:
    p, s = plane("DatumPlane", visible=False), sketch("Sketch001", visible=False)
    s.Label = "base_sketch"
    doc = Doc(body(), p, s)
    assert sketches.find_reference(doc, "DatumPlane") is p
    assert sketches.find_reference(doc, "base_sketch") is s
    reply = sketches.set_visibility(doc, ["DatumPlane", "base_sketch", "Body", "Nothing"], True)
    assert reply == {"changed": ["DatumPlane", "base_sketch"], "already": [], "unknown": ["Body", "Nothing"]}
    assert (p.Visibility, s.Visibility) == (True, True)


def test_showing_a_reference_that_is_shown_changes_nothing_and_hiding_it_again_works(sketches: Any) -> None:
    p = plane()
    doc = Doc(body(), p)
    assert sketches.set_visibility(doc, ["DatumPlane"], True)["already"] == ["DatumPlane"]
    assert sketches.set_visibility(doc, ["DatumPlane"], False)["changed"] == ["DatumPlane"]
    assert p.Visibility is False
