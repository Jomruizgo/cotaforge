"""Importing a STEP assembly stays linear in its bodies: progress per phase, each measure taken once and kept, the cheap
validity check by default for bodies nobody changed, sub-shapes of the registry taken out of the body only when asked.

A 4 x 10 solid assembly (a few holes each) is written by the check itself; ``main.PROGRESS_EMITTER`` collects what the
worker would push to the client as ``progress`` notifications.
"""

import os
import tempfile
from typing import Any

import FreeCAD as App
import Import
import main
import Part
import queries
import registry as reg
from harness import IDENTITY, call, op

V = App.Vector
GROUPS = 4
PER_GROUP = 10
SOLIDS = GROUPS * PER_GROUP
FACES_PER_SOLID = 6 + 3  # a box with three through holes


def _write_assembly(path: str) -> list[float]:
    """The STEP of ``GROUPS`` sub-assemblies of ``PER_GROUP`` boxes with three holes; the volume of each solid."""
    doc = App.newDocument("maker")
    groups = []
    volumes = []
    for g_index in range(GROUPS):
        group = doc.addObject("App::Part", f"Sub{g_index}")
        group.Placement = App.Placement(V(g_index * 500, 0, 0), App.Rotation())
        for k in range(PER_GROUP):
            width = 20.0 + k  # every solid has its own volume
            shape = Part.makeBox(width, 20, 10)
            for h in range(3):
                shape = shape.cut(Part.makeCylinder(1.5, 20, V(4 + h * 5, 10, -5)))
            obj = doc.addObject("Part::Feature", f"p{g_index}_{k}")
            obj.Shape = shape
            obj.Placement = App.Placement(V(k * 40, 0, 0), App.Rotation())
            group.addObject(obj)
            volumes.append(float(shape.Volume))
        groups.append(group)
    doc.recompute()
    Import.export(groups, path)
    App.closeDocument(doc.Name)
    return volumes


def _import(tmp: str, name: str = "asm") -> tuple[dict[str, Any], list[dict[str, Any]], list[float]]:
    path = os.path.join(tmp, f"{name}.step")
    volumes = _write_assembly(path)
    heard: list[dict[str, Any]] = []
    previous = main.PROGRESS_EMITTER
    main.PROGRESS_EMITTER = heard.append
    try:
        result = call("import_geometry", op("imp", path=path, name=name))
    finally:
        main.PROGRESS_EMITTER = previous
    return result, heard, volumes


def check_import_reports_each_phase_in_order_with_a_count() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result, heard, _ = _import(tmp)
    assert len(result["bodies"]) == SOLIDS, len(result["bodies"])
    phases = [m["phase"] for m in heard]
    order = ["reading", "preparing", "building", "adopting", "checking"]
    first_seen = [phases.index(p) for p in order]
    assert first_seen == sorted(first_seen), phases
    assert all(m["operation_id"] == "imp" for m in heard)
    for phase in ("adopting", "checking"):
        counted = [m for m in heard if m["phase"] == phase]
        assert counted[0]["current"] == 1 and counted[-1]["current"] == counted[-1]["total"] == SOLIDS, counted
        assert [m["current"] for m in counted] == sorted(m["current"] for m in counted)
        assert f"{phase[:-3]}" in counted[0]["message"] and f"/{SOLIDS}" in counted[-1]["message"], counted[0]
    reading = [m for m in heard if m["phase"] == "reading"]
    assert len(reading) == 1 and reading[0]["current"] is None and "no percentage" in reading[0]["message"]


def check_import_gives_every_body_its_feature_and_the_facts_it_measured_once() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result, _, volumes = _import(tmp)
    tree = call("feature_tree", op("q"))["nodes"]
    features = {n["handle"]["id"] for n in tree}
    bodies = result["bodies"]
    assert {b["feature"]["id"] for b in bodies} == features and len(features) == SOLIDS
    assert sorted(round(b["volume_mm3"], 3) for b in bodies) == sorted(round(v, 3) for v in volumes)
    assert all(b["solids"] == 1 and b["faces"] == FACES_PER_SOLID and b["valid"] and b["closed"] for b in bodies)
    bodies_of = call("bodies_of", op("q", feature=bodies[3]["feature"]))["bodies"]
    assert [b["handle"]["id"] for b in bodies_of] == [bodies[3]["body"]["id"]]
    total = call("mass_properties", op("q"))["volume_mm3"]
    assert abs(total - sum(volumes)) < 1e-6 * sum(volumes), (total, sum(volumes))


def check_import_keeps_what_it_measured_and_does_not_hold_sub_shapes() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _import(tmp)
    doc = main.WORKER.active
    assert doc is not None
    bodies = doc.live_bodies()
    assert len(bodies) == SOLIDS
    assert all(body.is_lazy and body.pristine is not None for body in bodies), "records are built when asked"
    for body in bodies:
        assert body._vols is not None and body._valid is True and body.reference is not None
        assert all(rec._shape is None for rec in (*body.faces.values(), *body.edges.values())), body.id
    # a record still stands for its element: taken out of the body on demand, the same one `Faces`/`Edges` give
    body = bodies[0]
    shape = reg.topo_shape(doc, body)
    face = next(iter(body.faces.values()))
    edge = next(iter(body.edges.values()))
    assert face.shape.isSame(shape.Faces[face.index]) and edge.shape.isSame(shape.Edges[edge.index])
    clone = face.clone()
    assert clone.shape.isSame(face.shape)
    face.shape = None  # explicitly dropped: signature-only matching, never a sub-shape of somewhere else
    assert face.shape is None


def check_validity_is_the_cheap_one_for_untouched_imported_bodies_until_asked_deeply() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        result, _, _ = _import(tmp)
    deep_calls: list[str] = []
    original = queries._shape_problems

    def counting(name: str, shape: Any) -> list[str]:
        deep_calls.append(name)
        return original(name, shape)

    queries._shape_problems = counting
    try:
        assert call("is_valid", op("q")) == {"valid": True, "problems": []}
        assert deep_calls == [], "the BOP check ran on bodies nobody changed"
        assert call("is_valid", op("q", deep=True)) == {"valid": True, "problems": []}
        assert len(deep_calls) == SOLIDS
        call("is_valid", op("q", deep=True))
        assert len(deep_calls) == SOLIDS, "a deep result is kept until the body changes"
        # a body that an operation changed is no longer the supplier's: it gets the full check from then on
        moved = result["bodies"][0]["body"]
        deep_calls.clear()
        call("move_body", op("mv", body=moved, transform={"origin": [0, 0, 100], "rotation": IDENTITY["rotation"]}))
        assert call("is_valid", op("q"))["valid"] is True
        assert deep_calls == [moved["id"]], deep_calls
    finally:
        queries._shape_problems = original


def check_mass_properties_do_not_measure_a_body_twice() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        _import(tmp)
    doc = main.WORKER.active
    assert doc is not None
    first = call("mass_properties", op("q"))
    kept = [body._vols for body in doc.live_bodies()]
    second = call("mass_properties", op("q"))
    assert first == second
    assert all(a is b for a, b in zip(kept, (body._vols for body in doc.live_bodies()), strict=True))
