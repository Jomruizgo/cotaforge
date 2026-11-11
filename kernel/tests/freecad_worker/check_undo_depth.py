"""``undo_last`` past FreeCAD's undo cap (it keeps at most 20 transactions; ``doc.undo()`` beyond that is a silent
no-op that used to leave the reverted ops' objects in the document as ghost geometry while the registry forgot them).

Every check compares the live FreeCAD document -- object set, properties the ops touch, shapes -- against a FRESH
document that ran only the surviving prefix of the same plan (a differential oracle), so a ghost object, a Tip left
dangling, a Placement or a Visibility not given back is caught whatever op caused it.
"""

from collections.abc import Callable
from typing import Any

import main
import queries
import registry as reg
from harness import IDENTITY, call, close, extrude, faces_of, new_document, op, rect, select_face, sketch
from protocol import WorkerError

# --- fingerprint of the live FreeCAD document ------------------------------------------------------------------------


def _r(values: Any, digits: int = 5) -> tuple[float, ...]:
    return tuple(round(float(v), digits) + 0.0 for v in values)


def _entry(obj: Any) -> tuple[Any, ...]:
    placement = obj.Placement if "Placement" in obj.PropertiesList else None
    pose = None if placement is None else (_r(placement.Base), _r(placement.Rotation.Q))
    shape = obj.Shape if "Shape" in obj.PropertiesList else None
    geometry = None
    if shape is not None and not shape.isNull():
        box = shape.BoundBox
        geometry = (
            round(float(shape.Volume), 4) if shape.Solids else 0.0,
            _r((box.XMin, box.YMin, box.ZMin, box.XMax, box.YMax, box.ZMax)),
            len(shape.Faces),
            len(shape.Edges),
        )
    tip = getattr(obj, "Tip", None) if obj.TypeId == "PartDesign::Body" else None
    group = [o.Name for o in obj.Group] if obj.TypeId == "PartDesign::Body" else None
    base = obj.BaseFeature.Name if "BaseFeature" in obj.PropertiesList and obj.BaseFeature else None
    return (obj.TypeId, bool(obj.Visibility), pose, geometry, tip.Name if tip else None, group, base, str(obj.Label))


def fingerprint(fc_doc: Any) -> dict[str, tuple[Any, ...]]:
    return {obj.Name: _entry(obj) for obj in fc_doc.Objects}


def registry_fingerprint(rec: reg.DocumentRec) -> dict[str, Any]:
    """The harness's own view of the document: bodies with their face/edge/vertex handles (index, creator, modifiers,
    staleness, axis sense), the sketches a feature consumed, datums, components, the id counters and the feature
    list. Left out on purpose: the ``feature`` and ``sketch`` counters and feature labels (an id is allocated before the
    op's snapshot, so a redo skips a number; a ``sketch`` op runs before the op that consumes it) and sketch records not
    consumed yet (they live outside the transaction)."""

    def faces(items: dict[str, Any]) -> dict[str, Any]:
        return {
            k: (f.index, f.created_by, tuple(f.modified_by), f.stale, None if f.axis_dir is None else _r(f.axis_dir))
            for k, f in items.items()
        }

    def edges(items: dict[str, Any]) -> dict[str, Any]:
        return {k: (e.index, e.created_by, tuple(e.faces), e.stale) for k, e in items.items()}

    def vertices(items: dict[str, Any]) -> dict[str, Any]:
        return {k: (v.index, tuple(v.edges), v.stale) for k, v in items.items()}

    return {
        "bodies": {
            k: (
                b.kind,
                b.fc_body,
                b.result_obj,
                b.deleted,
                b.created_by,
                tuple(b.modified_by),
                faces(b.faces),
                edges(b.edges),
                vertices(b.vertices),
            )
            for k, b in rec.bodies.items()
        },
        "sketches": {k: (s.fc_name, s.on_face, s.body) for k, s in rec.sketches.items() if s.fc_name is not None},
        "datums": {k: d.kind for k, d in rec.datums.items()},
        "components": {
            k: (c.part_doc, c.fc_part, c.fixed, tuple(c.body_ids), tuple(c.mates)) for k, c in rec.components.items()
        },
        "counters": {k: v for k, v in rec.counters.items() if k not in ("feature", "sketch")},
        "features": [(f.kind, tuple(f.fc_names), f.fc_main, tuple(f.created_faces)) for f in rec.features],
    }


def active_fc() -> Any:
    assert main.WORKER.active is not None
    return main.WORKER.active.fc_doc


def active_rec() -> reg.DocumentRec:
    assert main.WORKER.active is not None
    return main.WORKER.active


def manual_nodes() -> list[Any]:
    return [n for n in call("feature_tree", op("q"))["nodes"] if n["parameters"].get("manual")]


def assert_no_ghosts() -> None:
    """Every model object left in the document is one the registry knows (feature_tree flags the others 'manual')."""
    assert manual_nodes() == [], manual_nodes()


def feature_count() -> int:
    return len(active_rec().features)


def undo_to(keep: int) -> int:
    """``undo_last`` until ``keep`` features are left (what ``Executor.undo_to`` drives); returns how many went."""
    reverted = 0
    while feature_count() > keep:
        expected = active_rec().features[-1].id
        assert call("undo_last", op("u"))["reverted"] == {"id": expected}
        reverted += 1
    return reverted


def _paths(a: Any, b: Any, path: str = "") -> list[str]:
    if isinstance(a, dict) and isinstance(b, dict):
        out = [f"{path}/{k}: only in reverted" for k in sorted(set(a) - set(b), key=str)]
        out += [f"{path}/{k}: only in fresh" for k in sorted(set(b) - set(a), key=str)]
        for k in sorted(set(a) & set(b), key=str):
            out += _paths(a[k], b[k], f"{path}/{k}")
        return out
    if isinstance(a, tuple | list) and isinstance(b, tuple | list) and len(a) == len(b):
        out = []
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            out += _paths(x, y, f"{path}[{i}]")
        return out
    return [] if a == b else [f"{path}: {a!r} != {b!r}"]


def diff(a: dict[str, Any], b: dict[str, Any]) -> str:
    """Where two fingerprints differ, one line per differing leaf (reverted first, fresh second)."""
    return "\n".join(_paths(a, b)[:12])


class Plan:
    """A sequence of single-feature steps driven against whatever document is active. ``state`` carries the handles."""

    def __init__(self, steps: list[Callable[[dict[str, Any]], None]]) -> None:
        self.steps = steps
        self.state: dict[str, Any] = {}

    def run(self, start: int, stop: int) -> None:
        for index in range(start, stop):
            before = feature_count()
            self.steps[index](self.state)
            assert feature_count() == before + 1, f"step {index} must add exactly one feature"


def assert_matches_fresh(make: Callable[[], Plan], keep: int, label: str, *, registry: bool = True) -> None:
    """The live document equals a fresh one that ran only the first ``keep`` steps (and so does its volume), and so
    does the harness's registry of it -- except after a REDO (``registry=False``): a feature id is allocated before the
    op's snapshot, so ops re-run after an undo get ids one number higher than in a fresh run (ids stay unique)."""
    got = fingerprint(active_fc())
    got_volume = call("mass_properties", op("q"))["volume_mm3"] if keep else 0.0
    got_registry = registry_fingerprint(active_rec())
    original = main.WORKER.active
    new_document(f"fresh_{label}_{keep}")
    make().run(0, keep)
    want = fingerprint(active_fc())
    want_volume = call("mass_properties", op("q"))["volume_mm3"] if keep else 0.0
    want_registry = registry_fingerprint(active_rec())
    main.WORKER.active = original
    assert got == want, f"{label}: document after undo to {keep} differs from a fresh run\n{diff(got, want)}"
    if registry:
        assert got_registry == want_registry, f"{label}: registry differs from a fresh run\n" + diff(
            got_registry, want_registry
        )
    assert close(got_volume, want_volume, 1e-6), (got_volume, want_volume)
    assert_no_ghosts()


# --- plans -----------------------------------------------------------------------------------------------------------


def cylinders(count: int) -> Plan:
    def step(i: int) -> Callable[[dict[str, Any]], None]:
        def run(state: dict[str, Any]) -> None:
            call(
                "cylinder",
                op(f"cyl{i}", diameter_mm=10, height_mm=10, axis={"origin": [i * 30, 0, 0], "direction": [0, 0, 1]}),
            )

        return run

    return Plan([step(i) for i in range(count)])


def mixed(rounds: int) -> Plan:
    """Part bodies: a box, then per round a tool cylinder + a subtract (tool consumed), a boss cylinder + a union (boss
    consumed), a translation and a rotation of the box -- Placement and Visibility change on earlier objects."""
    steps: list[Callable[[dict[str, Any]], None]] = []

    def box(state: dict[str, Any]) -> None:
        state["box"] = call("box", op("box", size=[400, 400, 20], transform=IDENTITY))["created_bodies"][0]

    steps.append(box)

    def round_steps(r: int) -> None:
        def tool(state: dict[str, Any]) -> None:
            state[f"tool{r}"] = call(
                "cylinder",
                op(
                    f"tool{r}",
                    diameter_mm=8,
                    height_mm=40,
                    axis={"origin": [20 + 60 * r, 50, -10], "direction": [0, 0, 1]},
                ),
            )["created_bodies"][0]

        def subtract(state: dict[str, Any]) -> None:
            call("boolean", op(f"sub{r}", kind="subtract", target=state["box"], tools=[state[f"tool{r}"]]))

        def boss(state: dict[str, Any]) -> None:
            state[f"boss{r}"] = call(
                "cylinder",
                op(
                    f"boss{r}",
                    diameter_mm=12,
                    height_mm=10,
                    axis={"origin": [20 + 60 * r, 150, 15], "direction": [0, 0, 1]},
                ),
            )["created_bodies"][0]

        def union(state: dict[str, Any]) -> None:
            call("boolean", op(f"uni{r}", kind="union", target=state["box"], tools=[state[f"boss{r}"]]))

        def translate(state: dict[str, Any]) -> None:
            call(
                "move_body",
                op(f"mv{r}", body=state["box"], transform={"origin": [1.5, 0.5, 0], "rotation": IDENTITY["rotation"]}),
            )

        def rotate(state: dict[str, Any]) -> None:
            c, s = 0.9961946980917455, 0.08715574274765817  # 5 degrees about Z
            call(
                "move_body",
                op(
                    f"rot{r}",
                    body=state["box"],
                    transform={"origin": [0, 0, 0], "rotation": [[c, -s, 0], [s, c, 0], [0, 0, 1]]},
                ),
            )

        steps.extend([tool, subtract, boss, union, translate, rotate])

    for r in range(rounds):
        round_steps(r)
    return Plan(steps)


def _top(state: dict[str, Any]) -> dict[str, Any]:
    return select_face(faces_of(state["base"]["feature"]), normal=(0, 0, 1), area_min=5000)


def partdesign(pockets: int = 4, bosses: int = 3, holes: int = 3, filler: int = 2) -> Plan:
    """A 200 x 120 x 10 plate (one PartDesign body) then pockets, bosses, holes, a linear pattern, a fillet and a
    chamfer, each PartDesign op followed by ``filler`` cheap cylinders: every reverted feature owns a sketch, a Body
    feature and a Tip move, and the history is long."""
    steps: list[Callable[[dict[str, Any]], None]] = []

    def base(state: dict[str, Any]) -> None:
        sk = sketch("base_sketch", "XY", [rect("outline", (0, 0), 200, 120)])
        state["base"] = call(
            "extrude",
            op(
                "base",
                sketch=sk["sketch"],
                depth_mm=10,
                direction="positive",
                both_directions=False,
                depth2_mm=None,
                merge=True,
            ),
        )

    steps.append(base)

    def pocket(i: int) -> Callable[[dict[str, Any]], None]:
        def run(state: dict[str, Any]) -> None:
            sk = sketch(f"pk{i}_sketch", _top(state)["handle"], [rect("r", (-85 + 14 * i, -45), 8, 8)])
            call(
                "cut",
                op(
                    f"pk{i}",
                    sketch=sk["sketch"],
                    end_condition="blind",
                    depth_mm=3,
                    direction="negative",
                    both_directions=False,
                    depth2_mm=None,
                ),
            )

        return run

    def boss(i: int) -> Callable[[dict[str, Any]], None]:
        def run(state: dict[str, Any]) -> None:
            sk = sketch(f"bs{i}_sketch", _top(state)["handle"], [rect("r", (-85 + 24 * i, 40), 10, 10)])
            call(
                "extrude",
                op(
                    f"bs{i}",
                    sketch=sk["sketch"],
                    depth_mm=4,
                    direction="positive",
                    both_directions=False,
                    depth2_mm=None,
                    merge=True,
                ),
            )

        return run

    def hole(i: int) -> Callable[[dict[str, Any]], None]:
        def run(state: dict[str, Any]) -> None:
            state["hole"] = call(
                "hole",
                op(
                    f"ho{i}",
                    face=_top(state)["handle"],
                    position=[-85 + 14 * i, 0],
                    standard="simple",
                    size=None,
                    diameter_mm=5,
                    end_condition="blind",
                    depth_mm=4,
                ),
            )

        return run

    def pattern(state: dict[str, Any]) -> None:
        call(
            "linear_pattern",
            op(
                "pat",
                features=[state["hole"]["feature"]],
                direction=[0, 1, 0],
                spacing_mm=15,
                count=3,
                direction2=None,
                spacing2_mm=None,
                count2=None,
            ),
        )

    def fillet(state: dict[str, Any]) -> None:
        faces = faces_of(state["base"]["feature"])
        front = select_face(faces, normal=(0, -1, 0), area_min=1000)
        edges = call("edges_between", op("q", a=_top(state)["handle"], b=front["handle"]))["edges"]
        assert len(edges) == 1, edges
        call("fillet", op("fil", edges=[edges[0]["handle"]], radius_mm=2))

    def chamfer(state: dict[str, Any]) -> None:
        faces = faces_of(state["base"]["feature"])
        back = select_face(faces, normal=(0, 1, 0), area_min=1000)
        edges = call("edges_between", op("q", a=_top(state)["handle"], b=back["handle"]))["edges"]
        assert len(edges) == 1, edges
        call("chamfer", op("cha", edges=[edges[0]["handle"]], distance1_mm=1.5, distance2_mm=None, distance1_face=None))

    for i in range(pockets):
        steps.append(pocket(i))
    for i in range(bosses):
        steps.append(boss(i))
    for i in range(holes):
        steps.append(hole(i))
    steps.extend([pattern, fillet, chamfer])
    return Plan(_with_fillers(steps, filler))


def _filler(k: int) -> Callable[[dict[str, Any]], None]:
    """A cheap, independent Part cylinder far from everything else: one more transaction on the undo stack."""

    def run(state: dict[str, Any]) -> None:
        call(
            "cylinder",
            op(f"fill{k}", diameter_mm=6, height_mm=6, axis={"origin": [k * 20, 900, 0], "direction": [0, 0, 1]}),
        )

    return run


def _with_fillers(steps: list[Callable[[dict[str, Any]], None]], filler: int) -> list[Callable[[dict[str, Any]], None]]:
    """``steps`` with ``filler`` cheap cylinders after every step but the first. A PartDesign recompute grows with the
    body, so a long undo history is built from few PartDesign features and many cheap Part ops: the 20-transaction
    cap is about transactions, not about how heavy each one is."""
    out = [steps[0]]
    count = 0
    for step in steps[1:]:
        out.append(step)
        for _ in range(filler):
            out.append(_filler(count))
            count += 1
    return out


CONVERSION_AT = 4  # step index of the first PartDesign feature of ``converted`` (box + 3 moves come before)


def converted(pockets: int = 6, filler: int = 3) -> Plan:
    """A Part box moved three times, then a boss on its top face (``ensure_partdesign`` bakes a ``Base`` feature and a
    Body around it) and pockets on the converted body, each followed by ``filler`` cheap cylinders."""
    steps: list[Callable[[dict[str, Any]], None]] = []

    def box(state: dict[str, Any]) -> None:
        state["box"] = call("box", op("box", size=[200, 120, 20], transform=IDENTITY))

    steps.append(box)
    for n in range(3):

        def move(state: dict[str, Any], n: int = n) -> None:
            call(
                "move_body",
                op(
                    f"mv{n}",
                    body=state["box"]["created_bodies"][0],
                    transform={"origin": [3, 2, 1], "rotation": IDENTITY["rotation"]},
                ),
            )

        steps.append(move)

    def top() -> dict[str, Any]:
        return select_face(faces_of(box_state["feature"]), normal=(0, 0, 1), area_min=5000)

    box_state: dict[str, Any] = {}

    def boss(state: dict[str, Any]) -> None:
        box_state["feature"] = state["box"]["feature"]
        sk = sketch("boss_sketch", top()["handle"], [rect("r", (0, 0), 30, 30)])
        call(
            "extrude",
            op(
                "boss",
                sketch=sk["sketch"],
                depth_mm=6,
                direction="positive",
                both_directions=False,
                depth2_mm=None,
                merge=True,
            ),
        )

    steps.append(boss)
    for i in range(pockets):

        def pocket(state: dict[str, Any], i: int = i) -> None:
            box_state["feature"] = state["box"]["feature"]
            sk = sketch(f"cp{i}_sketch", top()["handle"], [rect("r", (-90 + 7 * i, -45), 4, 4)])
            call(
                "cut",
                op(
                    f"cp{i}",
                    sketch=sk["sketch"],
                    end_condition="blind",
                    depth_mm=2,
                    direction="negative",
                    both_directions=False,
                    depth2_mm=None,
                ),
            )

        steps.append(pocket)
        first = len(steps) * 10
        steps.extend(_filler(first + k) for k in range(filler))
    return Plan(steps)


# --- the checks ------------------------------------------------------------------------------------------------------


def check_forty_independent_cylinders_undo_to_five_leaves_no_ghost() -> None:
    new_document("cyl40")
    plan = cylinders(40)
    plan.run(0, 40)
    assert len(active_fc().Objects) == 40
    assert undo_to(5) == 35
    assert len(active_fc().Objects) == 5, [o.Name for o in active_fc().Objects]
    assert_no_ghosts()
    assert close(call("mass_properties", op("q"))["volume_mm3"], 5 * 785.3981633974483, 1e-3)
    assert_matches_fresh(lambda: cylinders(40), 5, "cyl40")


def check_owners_case_seventy_eight_ops_revert_fifty_five() -> None:
    new_document("cyl78")
    cylinders(78).run(0, 78)
    assert undo_to(23) == 55
    assert len(active_fc().Objects) == 23
    assert_matches_fresh(lambda: cylinders(78), 23, "cyl78")


def check_twenty_ops_revert_fifteen_stays_exact() -> None:
    new_document("cyl20")
    cylinders(20).run(0, 20)
    assert undo_to(5) == 15
    assert_matches_fresh(lambda: cylinders(20), 5, "cyl20")


def check_mixed_part_ops_beyond_the_cap_restore_placements_and_visibility() -> None:
    new_document("mixed")
    plan = mixed(4)  # 25 features
    plan.run(0, len(plan.steps))
    assert len(plan.steps) == 25
    assert undo_to(3) == 22
    assert_matches_fresh(lambda: mixed(4), 3, "mixed")
    # and back up again: redo the undone tail on the same document, the result is the fresh full run
    plan2 = Plan(plan.steps)
    plan2.state = dict(plan.state)
    plan2.run(3, len(plan.steps))
    assert_matches_fresh(lambda: mixed(4), len(plan.steps), "mixed_redo", registry=False)


def check_partdesign_plan_beyond_the_cap_restores_body_tip_and_chain() -> None:
    new_document("pd")
    plan = partdesign()  # 1 + 13 PartDesign ops, each followed by 2 cheap cylinders = 40 features
    plan.run(0, len(plan.steps))
    assert len(plan.steps) == 40
    body = active_fc().getObject("Body")
    assert len(body.Group) >= 24  # a sketch + a feature per pocket/boss/hole, the pattern, the fillet, the chamfer
    assert undo_to(7) == 33
    assert_matches_fresh(lambda: partdesign(), 7, "pd7")
    tip = active_fc().getObject("Body").Tip
    assert tip is not None and tip.Name == "Pocket001", tip
    # the engineer-facing tree: only the surviving features, no phantom 'manual' node for a ghost
    kinds = [n["kind"] for n in call("feature_tree", op("q"))["nodes"]]
    assert kinds == ["extrude", "cut", "cylinder", "cylinder", "cut", "cylinder", "cylinder"], kinds
    assert manual_nodes() == []
    assert undo_to(1) == 6
    assert_matches_fresh(lambda: partdesign(), 1, "pd1")
    assert undo_to(0) == 1
    assert [o.Name for o in active_fc().Objects] == []


def check_partdesign_undo_inside_the_native_window_matches_fresh() -> None:
    """Six reverts of a 15-feature plan: FreeCAD's own undo would have handled these; the deletion-based revert must
    give the very same document."""
    new_document("pd_window")
    plan = partdesign(2, 1, 1, 1)
    plan.run(0, len(plan.steps))
    assert len(plan.steps) == 15
    assert undo_to(9) == 6
    assert_matches_fresh(lambda: partdesign(2, 1, 1, 1), 9, "pd9")


def check_conversion_from_part_body_is_undone_past_the_cap() -> None:
    new_document("conv")
    plan = converted()  # box + 3 moves + boss + 6 x (pocket + 3 cheap cylinders) = 29 features
    plan.run(0, len(plan.steps))
    assert len(plan.steps) == 29
    assert active_fc().getObject("Base") is not None and active_fc().getObject("Body") is not None
    assert undo_to(CONVERSION_AT + 1) == 24  # every pocket and filler; the boss that converted the body stays
    assert_matches_fresh(lambda: converted(), CONVERSION_AT + 1, "conv_inside")
    assert undo_to(CONVERSION_AT) == 1  # the boss that converted the body goes: Base and Body are deleted
    assert active_fc().getObject("Base") is None and active_fc().getObject("Body") is None
    assert_matches_fresh(lambda: converted(), CONVERSION_AT, "conv_before")
    rec = active_rec()
    body = next(iter(rec.live_bodies()))
    assert body.kind == "part"


def check_keep_undoing_redoing_and_undoing_never_resurrects_a_removed_object() -> None:
    new_document("cycle")
    plan = cylinders(24)
    plan.run(0, 24)
    for keep in (12, 2, 18, 0):
        undo_to(keep)
        assert_matches_fresh(lambda: cylinders(24), keep, f"cycle_down_{keep}", registry=keep == 12)  # 1st: no redo yet
        # a native undo here would resurrect the last deletion (removeObject outside a transaction is itself undoable)
        active_fc().undo()
        assert len(active_fc().Objects) == keep, "a stray doc.undo() must not bring a deleted object back"
        plan.run(keep, 24)
        assert_matches_fresh(lambda: cylinders(24), 24, f"cycle_up_{keep}", registry=False)


def check_revert_leaves_nothing_on_the_undo_stack_that_could_resurrect_it() -> None:
    new_document("stack")
    cylinders(30).run(0, 30)
    undo_to(10)
    fc_doc = active_fc()
    assert fc_doc.UndoCount == 0 and fc_doc.RedoCount == 0, (fc_doc.UndoCount, fc_doc.RedoCount)


def check_macro_replays_a_long_revert_to_the_same_document() -> None:
    new_document("macro")
    cylinders(30).run(0, 30)
    undo_to(8)
    want = fingerprint(active_fc())
    script = call("export_macro", op("m"))["script"]
    namespace: dict[str, Any] = {}
    exec(compile(script, "<macro>", "exec"), namespace)  # noqa: S102 - the recorded script is the thing under test
    replayed = namespace["docs"]
    got = fingerprint(replayed["doc:1"])
    assert got == want, diff(got, want)


def check_a_failure_inside_the_revert_raises_and_leaves_document_and_registry_untouched() -> None:
    new_document("fail")
    plan = partdesign(2, 1, 1, 1)
    plan.run(0, len(plan.steps))
    assert len(plan.steps) == 15
    undo_to(6)  # the next revert (the boss, 6th feature) deletes a sketch and a pad: two removals
    before = fingerprint(active_fc())
    features = [f.id for f in active_rec().features]
    names = list(active_rec().undo_stack)
    import fc

    real = fc.remove_object
    calls = {"n": 0}

    def flaky(doc: Any, name: str) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("injected failure while deleting")
        real(doc, name)

    fc.remove_object = flaky
    try:
        try:
            call("undo_last", op("u"))
        except WorkerError as exc:
            # rolled back, so the CAD is as before: recoverable (the session is not dead), not degraded_session
            assert exc.code == "rebuild_failed" and exc.recoverable, (exc.code, exc.message)
            assert "injected failure" in exc.message and "unchanged" in exc.message, exc.message
        else:
            raise AssertionError("undo_last must raise when the delete-based revert fails")
    finally:
        fc.remove_object = real
    assert fingerprint(active_fc()) == before, "the failed revert must be rolled back"
    assert [f.id for f in active_rec().features] == features and list(active_rec().undo_stack) == names
    # the session is still usable: the very same undo now works
    undo_to(5)
    assert_matches_fresh(lambda: partdesign(2, 1, 1, 1), 5, "fail_retry")


def check_the_postcondition_catches_an_undo_that_did_nothing() -> None:
    """If the revert silently left an object behind (simulated by a ``remove_object`` that does nothing), the
    post-revert invariant raises instead of letting the registry forget the object."""
    new_document("post")
    cylinders(25).run(0, 25)
    import fc

    real = fc.remove_object
    fc.remove_object = lambda doc, name: None
    try:
        try:
            call("undo_last", op("u"))
        except WorkerError as exc:
            assert exc.code == "rebuild_failed" and exc.recoverable, exc.code
            assert "still in the document" in exc.message, exc.message
        else:
            raise AssertionError("the invariant must fire")
    finally:
        fc.remove_object = real
    assert feature_count() == 25 and len(active_fc().Objects) == 25
    assert queries._registered_object_names(active_rec()) >= {o.Name for o in active_fc().Objects}  # noqa: SLF001


# --- a wider vocabulary beyond the cap ---------------------------------------------------------------------------

PLANE_X250 = {"origin": [250.0, 0.0, 0.0], "u": [0.0, 1.0, 0.0], "v": [0.0, 0.0, 1.0], "n": [1.0, 0.0, 0.0]}


def variety() -> Plan:
    """A revolved shaft (PartDesign Body, datum axis), then three rounds of copy_body / mirror_body / split (the copy
    consumed) / a subtracted tool (consumed) / a translation of the box, then a shell of the shaft and a sweep into a
    new body: 22 features, every one of them cheap, each with its own kind of footprint on the document."""
    from harness import circle, line  # noqa: PLC0415

    steps: list[Callable[[dict[str, Any]], None]] = []

    def shaft(state: dict[str, Any]) -> None:
        sk = sketch(
            "profile",
            "XY",
            [
                line("axis_seg", (0, 0), (40, 0)),
                line("end_small", (40, 0), (40, 6)),
                line("seat", (40, 6), (20, 6)),
                line("shoulder", (20, 6), (20, 10)),
                line("journal", (20, 10), (0, 10)),
                line("end_big", (0, 10), (0, 0)),
            ],
        )
        axis = {"origin": sk["frame"]["origin"], "direction": sk["frame"]["u"]}
        state["shaft"] = call("revolve", op("shaft", sketch=sk["sketch"], axis=axis, angle_deg=360.0))

    steps.append(shaft)

    def box(state: dict[str, Any]) -> None:
        pose = {"origin": [200, 200, 0], "rotation": IDENTITY["rotation"]}
        state["box"] = call("box", op("box", size=[60, 40, 20], transform=pose))["created_bodies"][0]

    steps.append(box)

    for r in range(3):

        def copy(state: dict[str, Any], r: int = r) -> None:
            pose = {"origin": [0, 100 * (r + 1), 0], "rotation": IDENTITY["rotation"]}
            state[f"copy{r}"] = call("copy_body", op(f"cp{r}", source=state["box"], transform=pose))["created_bodies"][
                0
            ]

        def mirror(state: dict[str, Any], r: int = r) -> None:
            call("mirror_body", op(f"mb{r}", source=state["box"], plane=PLANE_X250))

        def split(state: dict[str, Any], r: int = r) -> None:
            plane = {"origin": [230, 220 + 100 * (r + 1), 10], "u": [0, 1, 0], "v": [0, 0, 1], "n": [1, 0, 0]}
            call("split", op(f"sp{r}", source=state[f"copy{r}"], plane=plane, keep="both"))

        def tool(state: dict[str, Any], r: int = r) -> None:
            axis = {"origin": [210 + 10 * r, 220, -5], "direction": [0, 0, 1]}
            state[f"tool{r}"] = call("cylinder", op(f"tl{r}", diameter_mm=6, height_mm=30, axis=axis))[
                "created_bodies"
            ][0]

        def subtract(state: dict[str, Any], r: int = r) -> None:
            call("boolean", op(f"sb{r}", kind="subtract", target=state["box"], tools=[state[f"tool{r}"]]))

        def shift(state: dict[str, Any], r: int = r) -> None:
            pose = {"origin": [1, 2, 0], "rotation": IDENTITY["rotation"]}
            call("move_body", op(f"mv{r}", body=state["box"], transform=pose))

        steps.extend([copy, mirror, split, tool, subtract, shift])

    def shell(state: dict[str, Any]) -> None:
        end = select_face(faces_of(state["shaft"]["feature"]), normal=(-1, 0, 0))
        call("shell", op("sh", faces=[end["handle"]], thickness_mm=1.0, outward=False, join="intersection"))

    def sweep(state: dict[str, Any]) -> None:
        sk = sketch("sec", "XY", [circle("c", (0, 300), 10)])
        path = [[0, 300, 0], [0, 300, 100]]
        call("sweep", op("sw", sketch=sk["sketch"], path=path, corner_radius_mm=None, merge=False))

    steps.extend([shell, sweep])
    return Plan(steps)


def check_a_wide_vocabulary_beyond_the_cap_matches_a_fresh_run_including_the_registry() -> None:
    """revolve (datum axis), copy_body, mirror_body, split, boolean, move_body, shell and sweep: 21 reverts and 10."""
    new_document("wide")
    plan = variety()
    plan.run(0, len(plan.steps))
    assert len(plan.steps) == 22
    assert undo_to(12) == 10
    assert_matches_fresh(variety, 12, "wide12")
    assert undo_to(1) == 11
    assert_matches_fresh(variety, 1, "wide1")  # only the revolved shaft is left: its Body, sketch, datum axis
    assert undo_to(0) == 1
    assert [o.Name for o in active_fc().Objects] == []


# --- the engineer's hand work: lenient restore and warnings -------------------------------------------------------


def _plate_body(depth: float = 10.0) -> Any:
    sk = sketch("s", "XY", [rect("r", (0, 0), 40, 40)])
    return extrude("f1", sk, depth)


def _warnings(result: dict[str, Any]) -> str:
    return " | ".join(result.get("warnings", []))


def check_undo_never_warns_when_nobody_touched_the_document() -> None:
    new_document("quiet")
    cylinders(3).run(0, 3)
    for _ in range(3):
        assert call("undo_last", op("u")).get("warnings", []) == []
    new_document("quiet_pd")
    plan = partdesign(1, 1, 1, 0)
    plan.run(0, len(plan.steps))
    while feature_count():
        assert call("undo_last", op("u")).get("warnings", []) == []


def check_an_object_the_engineer_deleted_does_not_block_the_undo_that_would_restore_it() -> None:
    """box -> move_body, the engineer deletes the Box by hand: reverting the move has nothing left to restore, it must
    still happen (the old behaviour: degraded_session forever) and say what was skipped."""
    new_document("gone")
    box = call("box", op("b", size=[10, 10, 10], transform=IDENTITY))["created_bodies"][0]
    call("move_body", op("m1", body=box, transform={"origin": [5, 0, 0], "rotation": IDENTITY["rotation"]}))
    active_fc().removeObject("Box")
    result = call("undo_last", op("u"))
    assert result["reverted"] is not None and feature_count() == 1
    assert "Box" in _warnings(result) and "no longer exists" in _warnings(result), result.get("warnings", [])
    result = call("undo_last", op("u"))  # the box op itself: its object is already gone
    assert result["reverted"] is not None and feature_count() == 0


def check_a_body_tip_pointing_at_a_hand_deleted_feature_does_not_block_the_undo() -> None:
    new_document("tip")
    base = _plate_body()
    top = select_face(faces_of(base["feature"]), normal=(0, 0, 1))
    sk2 = sketch("s2", top["handle"], [rect("r", (0, 0), 10, 10)])
    extrude("f2", sk2, 5)
    active_fc().removeObject("Pad")  # the engineer deletes the first pad by hand
    result = call("undo_last", op("u"))
    assert result["reverted"] is not None and feature_count() == 1
    assert "Pad" in _warnings(result), result.get("warnings", [])
    assert call("undo_last", op("u"))["reverted"] is not None and feature_count() == 0


def check_hand_made_children_of_a_deleted_body_are_reported_as_orphans() -> None:
    new_document("orphan")
    _plate_body()
    active_fc().getObject("Body").newObject("Sketcher::SketchObject", "ManualSketch")
    result = call("undo_last", op("u"))
    assert result["reverted"] is not None and active_fc().getObject("Body") is None
    assert "ManualSketch" in _warnings(result) and "Body" in _warnings(result), result.get("warnings", [])
    assert active_fc().getObject("ManualSketch") is not None  # never deleted behind the engineer's back


def check_a_hand_made_feature_that_depends_on_what_is_deleted_is_named_and_a_hand_set_tip_is_reported() -> None:
    new_document("dependent")
    base = _plate_body()
    top = select_face(faces_of(base["feature"]), normal=(0, 0, 1))
    sk2 = sketch("s2", top["handle"], [rect("r", (0, 0), 10, 10)])
    extrude("f2", sk2, 5)
    body = active_fc().getObject("Body")
    body.newObject("PartDesign::Fillet", "ManualFillet")  # on top of f2's pad; the Tip moves to it
    result = call("undo_last", op("u"))
    assert result["reverted"] is not None and feature_count() == 1
    text = _warnings(result)
    assert "ManualFillet" in text, result.get("warnings", [])
    assert "depend" in text, result.get("warnings", [])
    assert "Tip" in text and "discarded" in text, result.get("warnings", [])  # the Tip the engineer moved is put back


def check_a_hand_edit_of_a_placement_that_the_revert_overwrites_is_reported() -> None:
    from FreeCAD import Placement, Rotation, Vector  # noqa: PLC0415

    new_document("hand")
    box = call("box", op("b", size=[10, 10, 10], transform=IDENTITY))["created_bodies"][0]
    obj = active_fc().getObject("Box")
    call("move_body", op("m1", body=box, transform={"origin": [5, 0, 0], "rotation": IDENTITY["rotation"]}))
    obj.Placement = Placement(Vector(100, 100, 100), Rotation())  # by hand, after m1
    call("move_body", op("m2", body=box, transform={"origin": [1, 0, 0], "rotation": IDENTITY["rotation"]}))
    result = call("undo_last", op("u"))  # m2: restores the hand-made pose, nothing is lost
    assert result.get("warnings", []) == [], result.get("warnings", [])
    assert tuple(obj.Placement.Base) == (100.0, 100.0, 100.0)
    result = call("undo_last", op("u"))  # m1: back to the origin, the hand edit goes
    assert tuple(obj.Placement.Base) == (0.0, 0.0, 0.0)
    assert "Box.Placement" in _warnings(result) and "hand edit" in _warnings(result), result.get("warnings", [])


# --- what happens around the atomic section ------------------------------------------------------------------------


def check_a_clear_undos_failure_after_the_revert_is_not_fatal() -> None:
    new_document("clear")
    cylinders(3).run(0, 3)
    import fc  # noqa: PLC0415

    real = fc.clear_undos

    def broken(doc: Any) -> None:
        raise RuntimeError("clearUndos exploded")

    fc.clear_undos = broken
    try:
        result = call("undo_last", op("u"))
    finally:
        fc.clear_undos = real
    assert result["reverted"] is not None and feature_count() == 2 and len(active_fc().Objects) == 2
    assert "undo stack" in _warnings(result) and "clearUndos exploded" in _warnings(result), result.get("warnings", [])
    undo_to(0)  # the session carries on
    assert active_fc().Objects == []


def check_a_failure_sealing_the_journal_rolls_the_op_back_instead_of_leaving_it_committed() -> None:
    new_document("seal")
    cylinders(1).run(0, 1)
    before = fingerprint(active_fc())
    import journal  # noqa: PLC0415

    real = journal.seal

    def broken(fc_doc: Any, snapshot: Any) -> Any:
        raise RuntimeError("seal exploded")

    journal.seal = broken
    try:
        try:
            call(
                "cylinder", op("c2", diameter_mm=10, height_mm=10, axis={"origin": [50, 0, 0], "direction": [0, 0, 1]})
            )
        except WorkerError as exc:
            assert exc.code == "geometry_failed" and "seal exploded" in exc.message, (exc.code, exc.message)
        else:
            raise AssertionError("the op must fail")
    finally:
        journal.seal = real
    assert fingerprint(active_fc()) == before and feature_count() == 1  # nothing committed without a FeatureRec
    cylinders(2).run(1, 2)  # and the document is still usable


def check_the_api_validation_requires_clear_undos() -> None:
    import validate  # noqa: PLC0415

    assert "clearUndos" in validate.DOC_METHODS
    assert call("validate_api", op("v"))["ok"] is True


def check_a_failure_after_the_document_was_reverted_is_degraded_with_a_consistent_registry() -> None:
    """The document is already reverted when the registry refresh fails: the reply must NOT be 'recoverable, retry' (a
    retry would revert the NEXT feature), and the registry bookkeeping must already be complete."""
    new_document("late")
    plan = partdesign(2, 1, 1, 1)
    plan.run(0, len(plan.steps))
    objects_before = len(active_fc().Objects)
    features_before = feature_count()
    real = reg.refresh
    state = {"n": 0}

    def boom(*args: Any, **kwargs: Any) -> Any:
        state["n"] += 1
        if state["n"] == 1:
            raise RuntimeError("injected registry refresh failure")
        return real(*args, **kwargs)

    reg.refresh = boom
    try:
        try:
            call("undo_last", op("u"))
        except WorkerError as exc:
            assert exc.code == "degraded_session" and not exc.recoverable, (exc.code, exc.message)
            assert "reverted" in exc.message and "injected" in exc.message, exc.message
        else:
            raise AssertionError("the failure must surface")
    finally:
        reg.refresh = real
    assert len(active_fc().Objects) < objects_before  # the document did revert
    assert feature_count() == features_before - 1  # and the registry says so
    expected = active_rec().features[-1].id
    assert call("undo_last", op("u2"))["reverted"] == {"id": expected}  # the next undo reverts the NEXT feature


def check_a_sub_micron_difference_in_a_placement_is_not_a_hand_edit() -> None:
    from FreeCAD import Placement, Rotation, Vector  # noqa: PLC0415

    new_document("tolerance")
    box = call("box", op("b", size=[10, 10, 10], transform=IDENTITY))["created_bodies"][0]
    obj = active_fc().getObject("Box")
    call("move_body", op("m1", body=box, transform={"origin": [5, 0, 0], "rotation": IDENTITY["rotation"]}))
    placed = obj.Placement
    obj.Placement = Placement(Vector(placed.Base.x + 1e-9, placed.Base.y, placed.Base.z), Rotation(placed.Rotation))
    result = call("undo_last", op("u"))
    assert result.get("warnings", []) == [], result.get("warnings")
