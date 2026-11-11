"""``edit_feature``: in-place editing of box, cylinder and move_body features on Part chains.

Plain asserts run inside ``freecadcmd`` (see ``run.py``). The oracle is a FRESH document built from the modified plan:
every case compares the edited document with it (volume, area, box, Placement, face and edge counts and per-index
order) and the failure classes of the experiment (the ones FreeCAD does not report) with the edit rolled back or
reported. The mini plan below has the shape of a tubular frame: hollow tubes (cylinder - cylinder), a rotated cut, a
plate with a hole and a union of several parts.
"""

import math
from typing import Any

import FreeCAD as App
import main
import registry as reg
from check_undo_depth import active_fc, active_rec, fingerprint, registry_fingerprint
from harness import IDENTITY, call, close, expect_error, face_info, faces_of, new_document, op

# --- a plan as data ---------------------------------------------------------------------------------------------------


def cyl(op_id: str, d: float, h: float, origin: tuple[float, float, float], axis: str = "Z") -> dict[str, Any]:
    return {"id": op_id, "kind": "cylinder", "d": d, "h": h, "origin": list(origin), "axis": axis}


def box(op_id: str, size: tuple[float, float, float], origin: tuple[float, float, float] = (0, 0, 0)) -> dict[str, Any]:
    return {"id": op_id, "kind": "box", "size": list(size), "origin": list(origin)}


def sub(op_id: str, target: str, *tools: str, keep_tools: bool = False) -> dict[str, Any]:
    return {"id": op_id, "kind": "subtract", "target": target, "tools": list(tools), "keep_tools": keep_tools}


def union(op_id: str, target: str, *tools: str) -> dict[str, Any]:
    return {"id": op_id, "kind": "union", "target": target, "tools": list(tools)}


def rotate(op_id: str, target: str, axis: str, angle: float, about: tuple[float, float, float]) -> dict[str, Any]:
    return {"id": op_id, "kind": "rotate", "target": target, "axis": axis, "angle": angle, "about": list(about)}


def translate(op_id: str, target: str, offset: tuple[float, float, float]) -> dict[str, Any]:
    return {"id": op_id, "kind": "translate", "target": target, "offset": list(offset)}


AXES = {"X": (1.0, 0.0, 0.0), "Y": (0.0, 1.0, 0.0), "Z": (0.0, 0.0, 1.0)}


def _rotation_matrix(axis: str, angle_deg: float) -> list[list[float]]:
    if not math.isfinite(angle_deg):  # a request a caller could still send: every entry carries the bad number
        return [[angle_deg] * 3 for _ in range(3)]
    a = math.radians(angle_deg)
    c, s = math.cos(a), math.sin(a)
    if axis == "X":
        return [[1, 0, 0], [0, c, -s], [0, s, c]]
    if axis == "Y":
        return [[c, 0, s], [0, 1, 0], [-s, 0, c]]
    return [[c, -s, 0], [s, c, 0], [0, 0, 1]]


def transform_for(item: dict[str, Any]) -> dict[str, Any]:
    if item["kind"] == "translate":
        return {"origin": item["offset"], "rotation": IDENTITY["rotation"]}
    rot = _rotation_matrix(item["axis"], item["angle"])
    about = item["about"]
    moved = [sum(rot[i][j] * about[j] for j in range(3)) for i in range(3)]
    return {"origin": [about[i] - moved[i] for i in range(3)], "rotation": rot}


def request_of(item: dict[str, Any], ids: dict[str, dict[str, Any]]) -> tuple[str, dict[str, Any]]:
    """``(adapter method, request params)`` of one plan item: exactly what the executor's builders produce."""
    kind = item["kind"]
    if kind == "cylinder":
        axis = {"origin": item["origin"], "direction": list(AXES[item["axis"]])}
        return "cylinder", op(item["id"], diameter_mm=item["d"], height_mm=item["h"], axis=axis)
    if kind == "box":
        transform = {"origin": item["origin"], "rotation": IDENTITY["rotation"]}
        return "box", op(item["id"], size=item["size"], transform=transform)
    if kind in ("subtract", "union"):
        tools = [ids[t]["body"] for t in item["tools"]]
        params = op(
            item["id"],
            kind=kind,
            target=ids[item["target"]]["body"],
            tools=tools,
            keep_tools=item.get("keep_tools", False),
        )
        return "boolean", params
    if kind in ("rotate", "translate"):
        return "move_body", op(item["id"], body=ids[item["target"]]["body"], transform=transform_for(item))
    raise AssertionError(kind)


def build(plan: list[dict[str, Any]], name: str = "frame") -> dict[str, dict[str, Any]]:
    """Run the plan in a NEW document (it becomes the active one) and return op id -> {feature, body}."""
    new_document(name)
    ids: dict[str, dict[str, Any]] = {}
    for item in plan:
        method, params = request_of(item, ids)
        result = call(method, params)
        body = result["created_bodies"][0] if result["created_bodies"] else ids[item["target"]]["body"]
        ids[item["id"]] = {"feature": result["feature"], "body": body}
    return ids


def with_changes(plan: list[dict[str, Any]], **changes: dict[str, Any]) -> list[dict[str, Any]]:
    return [{**item, **changes.get(item["id"], {})} for item in plan]


def frame_plan() -> list[dict[str, Any]]:
    """Hollow tube, a hollow stay aimed by a rotation and trimmed by the tube, a lug (plate with a hole)
    and the
    union of the three: 14 ops, 5 consecutive moves/unions on shared objects."""
    return [
        cyl("tube_o", 28.6, 150, (120, 0, 150)),
        cyl("tube_i", 25.4, 150, (120, 0, 150)),
        sub("hollow_tube", "tube_o", "tube_i"),
        cyl("sl_o", 19.05, 123.7, (120, 0, 150), "X"),
        cyl("sl_i", 15.9, 123.7, (120, 0, 150), "X"),
        sub("sl_hollow", "sl_o", "sl_i"),
        rotate("sl_aimed", "sl_hollow", "Z", 165.964, (120, 0, 150)),
        cyl("sl_cut_tube", 28.6, 250, (120, 0, 100)),
        sub("sl_arm", "sl_aimed", "sl_cut_tube"),
        box("dl_plate", (24, 10, 36), (-12, 17, 132)),
        cyl("dl_hole", 10, 14, (0, 15, 150), "Y"),
        sub("dl_drop", "dl_plate", "dl_hole"),
        union("sl_full", "sl_arm", "dl_drop"),
        union("frame", "hollow_tube", "sl_full"),
    ]


# --- fingerprints and comparison --------------------------------------------------------------------------------------


def _r(x: float, nd: int = 9) -> float:
    return round(float(x), nd)


def shapes_of(doc_rec: reg.DocumentRec) -> dict[str, dict[str, Any]]:
    """Per live body: volume, area, box, Placement, counts and the signature of every face and edge in index order."""
    import geometry as g
    import Part

    out: dict[str, dict[str, Any]] = {}
    for body in doc_rec.live_bodies():
        obj = doc_rec.fc_doc.getObject(body.result_obj)
        shape = Part.getShape(obj)
        box_ = g.exact_box(shape)
        out[body.id] = {
            "vol": float(shape.Volume),
            "area": float(shape.Area),
            "bbox": [box_.XMin, box_.YMin, box_.ZMin, box_.XMax, box_.YMax, box_.ZMax],
            "placement": list(obj.Placement.toMatrix().A),
            "nf": len(shape.Faces),
            "ne": len(shape.Edges),
            "nsol": len(shape.Solids),
            "faces": [
                [
                    type(f.Surface).__name__,
                    _r(f.Area, 6),
                    *(_r(c, 5) for c in (f.CenterOfMass.x, f.CenterOfMass.y, f.CenterOfMass.z)),
                ]
                for f in shape.Faces
            ],
            "edges": [[type(e.Curve).__name__, _r(e.Length, 6)] for e in shape.Edges],
        }
    return out


def assert_same_shapes(a: dict[str, dict[str, Any]], b: dict[str, dict[str, Any]], what: str) -> None:
    assert sorted(a) == sorted(b), f"{what}: bodies {sorted(a)} vs {sorted(b)}"
    for key in a:
        x, y = a[key], b[key]
        assert abs(x["vol"] - y["vol"]) <= 1e-9 * max(1.0, abs(y["vol"])), (
            f"{what}: {key} volume {x['vol']} vs {y['vol']}"
        )
        assert abs(x["area"] - y["area"]) <= 1e-9 * max(1.0, abs(y["area"])), f"{what}: {key} area"
        assert max(abs(p - q) for p, q in zip(x["bbox"], y["bbox"], strict=True)) <= 1e-9, f"{what}: {key} bbox"
        assert max(abs(p - q) for p, q in zip(x["placement"], y["placement"], strict=True)) <= 1e-9, (
            f"{what}: {key} placement"
        )
        assert (x["nf"], x["ne"], x["nsol"]) == (y["nf"], y["ne"], y["nsol"]), (
            f"{what}: {key} counts {x['nf'], x['ne'], x['nsol']} vs {y['nf'], y['ne'], y['nsol']}"
        )
        assert x["faces"] == y["faces"], f"{what}: {key} face order or signature"
        assert x["edges"] == y["edges"], f"{what}: {key} edge order or signature"


def edit(
    ids: dict[str, dict[str, Any]], plan: list[dict[str, Any]], *changed: str, dry_run: bool = False
) -> dict[str, Any]:
    """Edit the ops ``changed`` of the live document to the values ``plan`` carries for them (one batch)."""
    edits = []
    for item in plan:
        if item["id"] in changed:
            method, params = request_of(item, ids)
            edits.append({"feature": ids[item["id"]]["feature"], "kind": method, "request": params})
    return call("edit_feature", op("edit", edits=edits, dry_run=dry_run))


def tree_params() -> dict[str, Any]:
    return {n["name"]: n["parameters"] for n in call("feature_tree", op("q"))["nodes"]}


# --- differential: edit in place == rebuild from scratch --------------------------------------------------------------


CASES: dict[str, dict[str, dict[str, Any]]] = {
    "tube_radius": {"tube_o": {"d": 30.0}},
    "tube_height": {"tube_o": {"h": 140.0}, "tube_i": {"h": 140.0}},
    "box_size": {"dl_plate": {"size": [24.0, 12.0, 40.0]}},
    "cut_tool_origin": {"sl_cut_tube": {"origin": [120.0, -37.0, 100.0]}},
    "rotate_angle_on_a_cut": {"sl_aimed": {"angle": 170.0}},
    "early_op_feeding_a_union": {"sl_o": {"d": 21.0}},
    "five_at_once": {
        "tube_o": {"d": 30.0},
        "dl_plate": {"size": [24.0, 12.0, 40.0]},
        "sl_aimed": {"angle": 170.0},
        "dl_hole": {"origin": [0.0, 15.0, 148.0]},
        "sl_o": {"d": 20.0},
    },
}


def _differential(name: str) -> None:
    plan = frame_plan()
    changes = CASES[name]
    ids = build(plan, "edited")
    baseline = shapes_of(active_rec())
    modified = with_changes(plan, **changes)
    result = edit(ids, modified, *changes)
    assert result["applied"] and result["rebuild_ok"] and not result["refused"], result
    assert result["failure"] is None and result["health"] == [], result
    edited = shapes_of(active_rec())
    build(modified, "fresh")
    assert_same_shapes(edited, shapes_of(active_rec()), f"{name} in place vs fresh")
    # the other document is the fresh one now; go back to the edited one and revert: bit-identical round trip
    main.dispatch("set_active_document", op("s", document={"id": "doc:1"}))
    restore = edit(ids, plan, *changes)
    assert restore["applied"], restore
    assert_same_shapes(shapes_of(active_rec()), baseline, f"{name} round trip")


def check_edit_tube_radius_matches_a_fresh_build() -> None:
    _differential("tube_radius")


def check_edit_cylinder_height_matches_a_fresh_build() -> None:
    _differential("tube_height")


def check_edit_box_size_matches_a_fresh_build() -> None:
    _differential("box_size")


def check_edit_cut_tool_origin_matches_a_fresh_build() -> None:
    _differential("cut_tool_origin")


def check_edit_rotate_angle_on_a_cut_matches_a_fresh_build() -> None:
    _differential("rotate_angle_on_a_cut")


def check_edit_early_op_feeding_a_union_matches_a_fresh_build() -> None:
    _differential("early_op_feeding_a_union")


def check_five_simultaneous_edits_with_one_recompute_match_a_fresh_build() -> None:
    _differential("five_at_once")
    # one batch is ONE recompute: a single recompute line recorded between the edit's transaction markers
    lines = main.fc.recorder.lines
    transaction = [i for i, line in enumerate(lines) if "openTransaction('edit" in line]
    assert transaction, "the edit opened no transaction"
    opened = transaction[0]
    committed = next(i for i in range(opened, len(lines)) if "commitTransaction" in lines[i])
    assert sum("recompute()" in line for line in lines[opened:committed]) == 1, lines[opened:committed]


def check_the_edit_reports_what_changed() -> None:
    plan = frame_plan()
    ids = build(plan)
    modified = with_changes(plan, tube_o={"d": 30.0})
    result = edit(ids, modified, "tube_o")
    assert [h["id"] for h in result["recomputed"]] == [
        ids[i]["feature"]["id"] for i in ("tube_o", "hollow_tube", "frame")
    ]
    (delta,) = [d for d in result["delta"] if d["after"] is not None]
    assert delta["body"] == ids["frame"]["body"] or delta["body"] == ids["tube_o"]["body"]
    before, after = delta["before"], delta["after"]
    assert after["volume_mm3"] > before["volume_mm3"]  # a thicker wall
    assert after["bbox"]["max"][0] >= before["bbox"]["max"][0]
    assert after["faces"] >= 1 and before["solids"] >= 1
    assert result["flags"] == [] or all(f["severity"] == "warning" for f in result["flags"])
    refs = result["refs"]
    assert set(refs) == {"created", "removed", "modified"}
    assert refs["created"] or refs["removed"] or refs["modified"], refs  # the wall faces moved
    # the registry follows: no stale handle is resolved to a face that is gone
    assert tree_params()["tube_o"]["diameter_mm"] == 30.0


def check_removed_faces_are_stale_handles_and_created_ones_resolve() -> None:
    plan = [box("block", (100, 60, 20)), cyl("tool", 30, 20, (50, 30, 0)), sub("bore", "block", "tool")]
    ids = build(plan)
    before = {f["handle"]["id"] for f in faces_of(ids["bore"]["feature"])}
    assert before, "the bore feature owns faces"
    result = edit(ids, with_changes(plan, tool={"d": 40.0}), "tool")
    assert result["applied"], result
    refs = result["refs"]
    for handle in refs["removed"]:
        expect_error("invalid_reference", "face_info", op("q", face=handle))
    for handle in [*refs["created"], *refs["modified"]]:
        assert face_info(handle)["handle"] == handle
    after = {f["handle"]["id"] for f in faces_of(ids["bore"]["feature"])}
    assert {h["id"] for h in refs["created"]} <= after, (
        "faces the edit created belong to the feature that built the body"
    )
    assert not ({h["id"] for h in refs["removed"]} & after), "a removed face is not offered by faces_of any more"
    # the bore wall is one cylindrical face of the new radius, reachable through its feature
    walls = [f for f in faces_of(ids["bore"]["feature"]) if f["surface"] == "cylinder"]
    assert len(walls) == 1 and close(walls[0]["radius"], 20.0, 1e-6), walls


def check_dry_run_touches_nothing_and_names_what_would_recompute() -> None:
    plan = frame_plan()
    ids = build(plan)
    before = fingerprint(active_fc())
    registry_before = registry_fingerprint(active_rec())
    result = edit(ids, with_changes(plan, tube_o={"d": 30.0}), "tube_o", dry_run=True)
    assert result["dry_run"] and not result["applied"] and result["refused"] == {}
    assert [h["id"] for h in result["recomputed"]] == [
        ids[i]["feature"]["id"] for i in ("tube_o", "hollow_tube", "frame")
    ]
    assert fingerprint(active_fc()) == before and registry_fingerprint(active_rec()) == registry_before


# --- the failure classes FreeCAD does not report ----------------------------------------------------------------------


def _rolled_back(
    ids: dict[str, dict[str, Any]],
    plan: list[dict[str, Any]],
    modified: list[dict[str, Any]],
    *changed: str,
    kind: str = "semantic",
) -> dict[str, Any]:
    objects, registry_state, shapes = (
        fingerprint(active_fc()),
        registry_fingerprint(active_rec()),
        shapes_of(active_rec()),
    )
    params = tree_params()
    result = edit(ids, modified, *changed)
    assert not result["applied"] and not result["rebuild_ok"] and result["failure"], result
    assert result["failure_kind"] == kind, (kind, result)
    assert fingerprint(active_fc()) == objects, "objects changed after the rollback"
    assert registry_fingerprint(active_rec()) == registry_state, "registry changed after the rollback"
    assert_same_shapes(shapes_of(active_rec()), shapes, "after the rollback")
    assert tree_params() == params
    return result


def check_an_empty_cut_is_rolled_back() -> None:
    """The cutter grows past the tube: the Cut is empty and FreeCAD says nothing."""
    plan = frame_plan()
    ids = build(plan)
    result = _rolled_back(ids, plan, with_changes(plan, tube_i={"d": 40.0}), "tube_i")
    assert any(f["kind"] == "body_disappeared" and f["severity"] == "failure" for f in result["flags"]), result
    assert "no material" in result["failure"]


def check_the_failure_kind_separates_the_part_from_the_mechanism() -> None:
    """Only a failure of the mechanism's own invariants (a pose or an untouched body moved) may fall back to a rebuild;
    anything about the part wins, because a rebuild would apply the change without these checks."""
    import edit as worker_edit

    drift = {"kind": "placement_drift", "severity": "failure"}
    volume = {"kind": "volume_changed", "severity": "failure"}
    split = {"kind": "solids_increased", "severity": "failure"}
    warning = {"kind": "cut_without_effect", "severity": "warning"}
    assert worker_edit._failure_kind([], [drift]) == "technical"
    assert worker_edit._failure_kind([], [drift, volume, warning]) == "technical"
    assert worker_edit._failure_kind([], [drift, split]) == "semantic"
    assert worker_edit._failure_kind([{"object": "Box"}], [drift]) == "semantic"


# --- measure_bodies: the reading a rebuild needs, because its steps only check themselves (p1b, p5c) ------------------


def _measure() -> dict[str, Any]:
    return call("measure_bodies", op("measure"))


def check_measure_bodies_reads_every_body_and_cut_without_touching_the_part() -> None:
    plan = frame_plan()
    ids = build(plan)
    objects, registry_state, shapes = (
        fingerprint(active_fc()),
        registry_fingerprint(active_rec()),
        shapes_of(active_rec()),
    )
    reading = _measure()
    assert fingerprint(active_fc()) == objects and registry_fingerprint(active_rec()) == registry_state
    assert_same_shapes(shapes_of(active_rec()), shapes, "after measure_bodies")
    assert reading["bodies"] and all(b["measure"]["solids"] == 1 and b["valid"] for b in reading["bodies"]), reading
    assert all(b["label"] for b in reading["bodies"]) and reading["overlaps"] == []
    removed = {c["feature"]["id"]: c["removed_mm3"] for c in reading["cuts"]}
    hollow, arm_cut = ids["hollow_tube"]["feature"]["id"], ids["sl_arm"]["feature"]["id"]
    assert removed[hollow] > 100.0 and removed[arm_cut] > 1.0, removed


def check_measure_bodies_sees_a_split_a_cut_that_misses_and_an_overlap_that_a_rebuild_hides() -> None:
    """A fresh build of the changed plan is exactly what a replay leaves behind: every step succeeds on its own."""
    plan = frame_plan()
    build(with_changes(plan, sl_aimed={"angle": 20.0}), "split")  # the stay leaves the lug: the union comes apart
    reading = _measure()
    assert any(b["measure"]["solids"] > 1 for b in reading["bodies"]), reading["bodies"]

    ids = build(with_changes(plan, sl_cut_tube={"origin": [900.0, 0.0, 100.0]}), "misses")
    removed = {c["feature"]["id"]: c["removed_mm3"] for c in _measure()["cuts"]}
    assert removed[ids["sl_arm"]["feature"]["id"]] <= 1e-6, (
        removed
    )  # the cylinder moved away: the subtraction cuts nothing

    overlapping = [
        cyl("a_o", 30, 100, (0, 0, 0)),
        cyl("a_i", 26, 100, (0, 0, 0)),
        sub("a", "a_o", "a_i"),
        box("pad", (10, 10, 10), (8, -5, 20)),
    ]
    build(overlapping, "overlap")
    (pair,) = _measure()["overlaps"]
    assert pair["volume_mm3"] > 1.0 and pair["a"]["id"] != pair["b"]["id"]


def check_a_union_that_splits_is_rolled_back() -> None:
    """The lug moves away from the stay: the union stops being one connected piece, silently."""
    plan = frame_plan()
    ids = build(plan)
    result = _rolled_back(ids, plan, with_changes(plan, dl_plate={"origin": [-12.0, 17.0, 400.0]}), "dl_plate")
    assert any(f["kind"] == "solids_increased" and f["severity"] == "failure" for f in result["flags"]), result


def check_a_subtraction_that_splits_a_body_is_rolled_back() -> None:
    """not a union that came apart, a bar that a growing cutter slices in two: any body that has more solids than
    before the edit fails it (a body in several pieces is never what a parametric edit meant)."""
    plan = [box("bar", (100, 10, 10)), cyl("cutter", 4, 20, (50, 5, -5)), sub("bar_cut", "bar", "cutter")]
    ids = build(plan)
    result = _rolled_back(ids, plan, with_changes(plan, cutter={"d": 14.0}), "cutter")
    (flag,) = [f for f in result["flags"] if f["kind"] == "solids_increased"]
    assert flag["severity"] == "failure" and flag["bodies"] and flag["value"] == 1.0, result
    assert "2 solids" in flag["message"] and result["failure_kind"] == "semantic"
    # a cutter that still leaves one piece is a normal edit
    assert edit(ids, with_changes(plan, cutter={"d": 8.0}), "cutter")["applied"]


def check_a_cut_that_misses_is_applied_with_a_warning() -> None:
    plan = frame_plan()
    ids = build(plan)
    modified = with_changes(plan, sl_cut_tube={"origin": [900.0, 0.0, 100.0]})
    result = edit(ids, modified, "sl_cut_tube")
    assert result["applied"] and result["rebuild_ok"], result
    (flag,) = [f for f in result["flags"] if f["kind"] == "cut_without_effect"]
    assert flag["severity"] == "warning" and flag["features"] == [ids["sl_arm"]["feature"]]
    build(modified, "fresh")
    fresh = shapes_of(active_rec())
    main.dispatch("set_active_document", op("s", document={"id": "doc:1"}))
    assert_same_shapes(shapes_of(active_rec()), fresh, "a missing cut still equals the rebuild")


def check_a_new_interference_is_reported() -> None:
    """A lug dragged into the tube's wall (two bodies that did not overlap now do)."""
    plan = [
        cyl("a_o", 30, 100, (0, 0, 0)),
        cyl("a_i", 26, 100, (0, 0, 0)),
        sub("a", "a_o", "a_i"),
        box("pad", (10, 10, 10), (100, 0, 0)),
    ]
    ids = build(plan)
    modified = with_changes(plan, pad={"origin": [8.0, -5.0, 20.0]})
    result = edit(ids, modified, "pad")
    assert result["applied"], result
    (flag,) = [f for f in result["flags"] if f["kind"] == "new_interference"]
    assert flag["severity"] == "warning" and flag["value"] > 1.0 and len(flag["bodies"]) == 2


def check_a_rotation_that_leaves_the_union_is_rolled_back() -> None:
    """The stay swings away from the lug it was joined to: the union splits, silently in FreeCAD."""
    plan = frame_plan()
    ids = build(plan)
    result = _rolled_back(ids, plan, with_changes(plan, sl_aimed={"angle": 20.0}), "sl_aimed")
    assert any(f["kind"] == "solids_increased" and f["severity"] == "failure" for f in result["flags"]), result
    assert "no longer touch" in result["failure"]


def check_a_rotation_into_another_body_is_reported() -> None:
    """A rod swung into a tube it did not touch: the edit equals a rebuild and the overlap is a warning."""
    plan = [
        cyl("a_o", 30, 100, (0, 0, 0)),
        cyl("a_i", 26, 100, (0, 0, 0)),
        sub("a", "a_o", "a_i"),
        cyl("rod", 10, 100, (100, 0, 50), "X"),
        rotate("swing", "rod", "Z", 0.0, (100, 0, 50)),
    ]
    ids = build(plan)
    modified = with_changes(plan, swing={"angle": 180.0})
    result = edit(ids, modified, "swing")
    assert result["applied"], result
    (flag,) = [f for f in result["flags"] if f["kind"] == "new_interference"]
    assert flag["severity"] == "warning" and flag["value"] > 1.0
    edited = shapes_of(active_rec())
    build(modified, "fresh")
    assert_same_shapes(edited, shapes_of(active_rec()), "rotation into another body vs rebuild")


def check_a_non_positive_dimension_is_rejected_before_it_is_touched() -> None:
    plan = frame_plan()
    ids = build(plan)
    before = fingerprint(active_fc())
    for item, field, value in (
        ("tube_o", "d", 0.0),
        ("tube_o", "d", -5.0),
        ("tube_o", "h", 0.0),
        ("dl_plate", "size", [24.0, -3.0, 36.0]),
        ("dl_plate", "size", [0.0, 10.0, 36.0]),
        # nan and inf pass every ``<= 0`` / ``> 0`` test: no number of the request may be one
        ("tube_o", "d", math.nan),
        ("tube_o", "d", math.inf),
        ("tube_o", "h", math.inf),
        ("tube_o", "origin", [math.nan, 0.0, 150.0]),
        ("tube_o", "origin", [120.0, math.inf, 150.0]),
        ("dl_plate", "size", [math.nan, 10.0, 36.0]),
        ("dl_plate", "size", [24.0, math.inf, 36.0]),
        ("dl_plate", "origin", [0.0, 0.0, math.inf]),
        ("sl_aimed", "angle", math.nan),
        ("sl_aimed", "angle", math.inf),
        ("sl_aimed", "about", [0.0, math.inf, 0.0]),
    ):
        item_plan = {**next(i for i in plan if i["id"] == item), field: value}
        method, request = request_of(item_plan, ids)
        expect_error(
            "invalid_request",
            "edit_feature",
            op("e", edits=[{"feature": ids[item]["feature"], "kind": method, "request": request}]),
        )
    assert fingerprint(active_fc()) == before


def check_a_property_bound_to_an_expression_is_refused() -> None:
    plan = frame_plan()
    ids = build(plan)
    fc_doc = active_fc()
    tube_o = next(f for f in active_rec().features if f.label == "tube_o")
    fc_doc.getObject(tube_o.fc_main).setExpression("Radius", "<<tube_i>>.Radius + 1.6 mm")
    fc_doc.recompute()
    before = fingerprint(fc_doc)
    result = edit(ids, with_changes(plan, tube_o={"d": 31.0}), "tube_o")
    assert not result["applied"] and result["rebuild_ok"], result
    reason = result["refused"][ids["tube_o"]["feature"]["id"]]
    assert "expression" in reason and "Radius" in reason, reason
    assert fingerprint(fc_doc) == before
    # an expression on a property that is NOT edited does not matter
    ok = edit(ids, with_changes(plan, tube_o={"h": 140.0}), "tube_o")
    assert ok["applied"], ok


def check_static_copy_dependents_refuse_the_edit() -> None:
    plan = frame_plan()[:3]
    ids = build(plan, "static")
    call(
        "copy_body",
        op(
            "twin",
            source=ids["tube_o"]["body"],
            transform={"origin": [0, 100, 0], "rotation": IDENTITY["rotation"]},
            scale=1.0,
            scale_about=[0, 0, 0],
        ),
    )
    before = fingerprint(active_fc())
    result = edit(ids, with_changes(plan, tube_o={"d": 30.0}), "tube_o")
    reason = result["refused"][ids["tube_o"]["feature"]["id"]]
    assert "twin" in reason and "static copy" in reason, reason
    assert not result["applied"] and fingerprint(active_fc()) == before

    # a subtract with keep_tools keeps a copy of its tool: refused too
    keep = [
        cyl("k_o", 28.6, 150, (0, 0, 0)),
        cyl("k_i", 25.4, 150, (0, 0, 0)),
        sub("k", "k_o", "k_i", keep_tools=True),
    ]
    ids = build(keep, "keep")
    result = edit(ids, with_changes(keep, k_i={"d": 20.0}), "k_i")
    assert "keep_tools" in result["refused"][ids["k_i"]["feature"]["id"]], result


def check_ops_outside_the_slice_are_refused() -> None:
    plan = frame_plan()
    ids = build(plan)
    boolean = next(i for i in plan if i["id"] == "hollow_tube")
    method, params = request_of(boolean, ids)
    result = call(
        "edit_feature", op("e", edits=[{"feature": ids["hollow_tube"]["feature"], "kind": method, "request": params}])
    )
    assert "not supported yet" in result["refused"][ids["hollow_tube"]["feature"]["id"]]
    # a move cannot be retargeted
    method, params = request_of(next(i for i in plan if i["id"] == "sl_aimed"), ids)
    other = {**params, "body": ids["tube_o"]["body"]}
    result = call(
        "edit_feature", op("e", edits=[{"feature": ids["sl_aimed"]["feature"], "kind": "move_body", "request": other}])
    )
    assert "retargeting" in result["refused"][ids["sl_aimed"]["feature"]["id"]]


def check_a_placement_changed_by_hand_is_not_overwritten() -> None:
    plan = frame_plan()
    ids = build(plan)
    tube_o = next(f for f in active_rec().features if f.label == "tube_o")
    obj = active_fc().getObject(tube_o.fc_main)
    obj.Placement = App.Placement(obj.Placement.Base + App.Vector(1, 2, 3), obj.Placement.Rotation)
    active_fc().recompute()  # the GUI recomputes after a hand move: the object is not left Touched
    result = edit(ids, with_changes(plan, tube_o={"d": 30.0}), "tube_o")
    assert "outside the harness" in result["refused"][ids["tube_o"]["feature"]["id"]], result


def check_a_value_the_document_already_has_is_a_no_op_not_a_failure() -> None:
    """the radius was set by hand to the value the edit asks for. The answer is ok, not applied, nothing refused
    and no failure: the executor adopts it as unchanged instead of rebuilding the part for nothing."""
    plan = frame_plan()
    ids = build(plan)
    tube_i = next(f for f in active_rec().features if f.label == "tube_i")
    active_fc().getObject(tube_i.fc_main).Radius = 12.7  # 25.4 / 2: what the plan already says
    active_fc().recompute()
    objects, registry_state = fingerprint(active_fc()), registry_fingerprint(active_rec())
    result = edit(ids, with_changes(plan, tube_i={"d": 25.4}), "tube_i")
    assert result["rebuild_ok"] and not result["applied"] and not result["refused"], result
    assert result["failure"] is None and result.get("failure_kind") is None and result["flags"] == []
    assert any("nothing changed" in w for w in result["warnings"]), result
    assert fingerprint(active_fc()) == objects and registry_fingerprint(active_rec()) == registry_state


def _three_tubes() -> list[dict[str, Any]]:
    """Three separate hollow tubes: an edit of all three rebinds three live bodies."""
    plan: list[dict[str, Any]] = []
    for index, name in enumerate("abc"):
        x = 100.0 * index
        plan += [
            cyl(f"{name}_o", 30, 100, (x, 0, 0)),
            cyl(f"{name}_i", 26, 100, (x, 0, 0)),
            sub(f"{name}", f"{name}_o", f"{name}_i"),
        ]
    return plan


_BATCH = ("a_i", "b_i", "c_i")


def _three_edit_batch(plan: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return with_changes(plan, a_i={"d": 24.0}, b_i={"d": 22.0}, c_i={"d": 20.0})


def _edit_params(ids: dict[str, dict[str, Any]], modified: list[dict[str, Any]], *changed: str) -> dict[str, Any]:
    edits = []
    for item in modified:
        if item["id"] in changed:
            method, request = request_of(item, ids)
            edits.append({"feature": ids[item["id"]]["feature"], "kind": method, "request": request})
    return op("edit", edits=edits)


def check_an_exception_in_any_registry_refresh_of_the_rebind_rolls_everything_back() -> None:
    """the rebind refreshes one body after another and adds the faces it created to the owner feature. If the Nth
    refresh raises, the faces added by the first N-1 must go too: document, registry (feature face lists included),
    shapes and parameters are exactly as before, and the edit then still works."""
    plan = _three_tubes()
    modified = _three_edit_batch(plan)
    calls = {"n": 0}
    original = reg.refresh

    def counting(*args: Any, **kwargs: Any) -> Any:
        calls["n"] += 1
        return original(*args, **kwargs)

    ids = build(plan, "probe")
    reg.refresh = counting
    try:
        assert edit(ids, modified, *_BATCH)["applied"]
    finally:
        reg.refresh = original
    refreshes = calls["n"]
    assert refreshes >= 3, refreshes  # three bodies are rebound: the case that leaves a half-rebound registry

    ids = build(plan, "victim")
    for failing in range(1, refreshes + 1):
        objects, registry_state, shapes = (
            fingerprint(active_fc()),
            registry_fingerprint(active_rec()),
            shapes_of(active_rec()),
        )
        params = tree_params()
        seen = {"n": 0}

        def boom(*args: Any, _failing: int = failing, _seen: dict[str, int] = seen, **kwargs: Any) -> Any:
            _seen["n"] += 1
            if _seen["n"] == _failing:
                msg = f"injected failure in refresh {_failing}"
                raise RuntimeError(msg)
            return original(*args, **kwargs)

        reg.refresh = boom
        try:
            expect_error("internal", "edit_feature", _edit_params(ids, modified, *_BATCH))  # a bug, not the value
        finally:
            reg.refresh = original
        assert fingerprint(active_fc()) == objects, f"objects changed after a failure in refresh {failing}"
        assert registry_fingerprint(active_rec()) == registry_state, f"registry leaked after refresh {failing}"
        assert_same_shapes(shapes_of(active_rec()), shapes, f"after a failure in refresh {failing}")
        assert tree_params() == params
    assert edit(ids, modified, *_BATCH)["applied"]


def check_a_touched_object_refuses_the_edit_before_anything_is_touched() -> None:
    """an object with a pending change (set by hand, not recomputed) would be recomputed along with the edit and
    look like a drift of something nobody edited; the edit would fail, and its rollback could leave the document
    different from the start. The edit is REFUSED up front, with the object named, and nothing is touched."""
    plan = frame_plan()
    ids = build(plan)
    hole = next(f for f in active_rec().features if f.label == "dl_hole")
    obj = active_fc().getObject(hole.fc_main)
    obj.Height = 16.0  # not recomputed: the object stays Touched
    assert "Touched" in [str(s) for s in obj.State]
    objects, registry_state, shapes = (
        fingerprint(active_fc()),
        registry_fingerprint(active_rec()),
        shapes_of(active_rec()),
    )
    modified = with_changes(plan, tube_i={"d": 24.0})
    refused = edit(ids, modified, "tube_i")
    assert not refused["applied"] and refused["rebuild_ok"], refused
    reason = refused["refused"][ids["tube_i"]["feature"]["id"]]
    assert obj.Name in reason and "Touched" in reason and "recompute" in reason, reason
    assert edit(ids, modified, "tube_i", dry_run=True)["refused"], "a dry run reports the same refusal"
    assert fingerprint(active_fc()) == objects and registry_fingerprint(active_rec()) == registry_state
    assert_same_shapes(shapes_of(active_rec()), shapes, "after the refusal")
    assert "Touched" in [str(s) for s in obj.State]  # the pending change is still the engineer's, untouched
    active_fc().recompute()  # the engineer's own recompute clears it...
    assert edit(ids, modified, "tube_i")["applied"]  # ...and the edit works again


# --- guards nothing else exercised: each check below fails when its guard is removed (mutation-checked) --------------


def _object_of(label: str) -> Any:
    rec = next(f for f in active_rec().features if f.label == label)
    return active_fc().getObject(rec.fc_main)


def check_an_object_nobody_edited_that_moves_rolls_the_edit_back_as_a_technical_failure() -> None:
    """an expression ties the lug's position to the tube's radius: editing the radius moves a body that is not
    among the edited objects (guard 4). The mechanism, not the value, is at fault: ``failure_kind`` technical."""
    plan = frame_plan()
    ids = build(plan)
    plate, bore = _object_of("dl_plate"), _object_of("tube_i")
    plate.setExpression(".Placement.Base.x", f"-12 mm + ({bore.Name}.Radius - 12.7 mm)")
    active_fc().recompute()  # the expression holds at the current radius: nothing moves yet
    result = _rolled_back(ids, plan, with_changes(plan, tube_i={"d": 24.0}), "tube_i", kind="technical")
    (flag,) = [f for f in result["flags"] if f["kind"] == "placement_drift"]
    assert flag["severity"] == "failure" and plate.Name in flag["message"], result


def check_an_independent_body_that_changes_volume_rolls_the_edit_back_as_a_technical_failure() -> None:
    """An expression ties another tube's outer radius to the edited tube's inner one: a body that does not depend on the
    edit changes (guard 4, volume)."""
    plan = _three_tubes()
    ids = build(plan)
    other, edited_tube = _object_of("c_o"), _object_of("a_i")
    other.setExpression("Radius", f"15 mm + ({edited_tube.Name}.Radius - 13 mm)")
    active_fc().recompute()
    modified = with_changes(plan, a_i={"d": 24.0})
    result = _rolled_back(ids, plan, modified, "a_i", kind="technical")
    (flag,) = [f for f in result["flags"] if f["kind"] == "volume_changed"]
    assert flag["severity"] == "failure" and flag["bodies"] and flag["value"] < 0.0, result


def _stub_doc(*body_ids: str) -> Any:
    class Body:
        def __init__(self, body_id: str) -> None:
            self.id, self.result_obj, self.created_by = body_id, None, None

    class Doc:
        features: list[Any] = []

        def live_bodies(self) -> list[Any]:
            return [Body(b) for b in body_ids]

    return Doc()


def _state(measure: dict[str, Any], body_id: str = "body:1") -> Any:
    import edit as worker_edit

    state = worker_edit.State()
    state.bodies[body_id] = measure
    return state


def check_an_independent_body_whose_topology_changes_at_equal_volume_fails_the_edit() -> None:
    """Same volume, a different number of faces: ``evaluate`` still calls it a change of something nobody edited."""
    import edit as worker_edit

    box_ = {"min": [0.0, 0.0, 0.0], "max": [1.0, 1.0, 1.0]}
    base = {"volume_mm3": 10.0, "bbox": box_, "centroid": None, "solids": 1, "faces": 6, "edges": 12, "valid": True}
    for field, value in (("faces", 7), ("edges", 13), ("solids", 2)):
        _, _, flags = worker_edit.evaluate(
            _stub_doc("body:1"),
            worker_edit.Analysis(),  # no body is a dependent of the edit
            [],
            _state(base),
            _state({**base, field: value}),
        )
        kinds = [(f["kind"], f["severity"]) for f in flags]
        assert ("volume_changed", "failure") in kinds, (field, kinds)
    _, _, same = worker_edit.evaluate(_stub_doc("body:1"), worker_edit.Analysis(), [], _state(base), _state(base))
    assert same == []


def check_a_degenerate_value_that_freecad_flags_is_rolled_back_with_its_health() -> None:
    """a diameter of 1e-9 leaves an object the CAD itself marks (guard 2 reads its state, not our measures)."""
    plan = frame_plan()
    ids = build(plan)
    result = _rolled_back(ids, plan, with_changes(plan, sl_i={"d": 1e-9}), "sl_i")
    assert result["health"], result
    assert any("Invalid" in h["state"] or "Error" in h["state"] for h in result["health"]), result["health"]
    assert result["failure"].startswith("FreeCAD reports"), result["failure"]


def check_the_edit_leaves_no_undo_steps_behind() -> None:
    """After an applied edit a Ctrl+Z (or ``doc.undo()``) must not be able to undo it behind the registry's back."""
    plan = frame_plan()
    ids = build(plan)
    assert active_fc().UndoCount > 0, "the build left no undo steps: this check could not tell"
    assert edit(ids, with_changes(plan, tube_o={"d": 30.0}), "tube_o")["applied"]
    assert active_fc().UndoCount == 0 and active_fc().RedoCount == 0, (active_fc().UndoCount, active_fc().RedoCount)


def check_a_dependent_the_document_graph_does_not_link_refuses_the_edit() -> None:
    """The registry says ``a`` reads ``a_i``; FreeCAD's own graph (rewired by hand) does not: the two must agree, or the
    recompute would not reach what the registry expects to follow."""
    plan = _three_tubes()
    ids = build(plan)
    cut = _object_of("a")
    cut.Base, cut.Tool = _object_of("b_o"), _object_of("b_i")  # the cut no longer reads a_o / a_i
    active_fc().recompute()
    objects = fingerprint(active_fc())
    refused = edit(ids, with_changes(plan, a_i={"d": 24.0}), "a_i")
    reason = refused["refused"][ids["a_i"]["feature"]["id"]]
    assert "not linked" in reason and "document graph" in reason, refused
    assert not refused["applied"] and fingerprint(active_fc()) == objects


def _journals() -> dict[str, Any]:
    # the entries hold FreeCAD Placements (not copyable): their repr is the exact value
    return {
        f.id: (repr(f.journal.previous), repr(f.journal.after)) for f in active_rec().features if f.journal is not None
    }


def check_a_failed_rebind_gives_the_undo_journals_back() -> None:
    """The journals follow the edited moves before the rebind runs; when the rebind raises they must be restored, or a
    later undo would put the wrong Placement back."""
    plan = frame_plan()
    ids = build(plan)
    modified = with_changes(plan, tube_i={"d": 24.0}, sl_aimed={"angle": 168.0})
    before = _journals()
    assert any(entry != ("{}", "{}") for entry in before.values())
    original = reg.refresh

    def boom(*args: Any, **kwargs: Any) -> Any:
        reg.refresh = original
        msg = "injected failure in the rebind"
        raise RuntimeError(msg)

    reg.refresh = boom
    try:
        expect_error("internal", "edit_feature", _edit_params(ids, modified, "tube_i", "sl_aimed"))
    finally:
        reg.refresh = original
    assert _journals() == before
    assert edit(ids, modified, "tube_i", "sl_aimed")["applied"]
    assert _journals() != before  # and a standing edit does move them


def check_a_rigid_move_keeps_every_face_id_on_the_face_it_named() -> None:
    """a pure move re-poses the body, it does not rebuild it, so each face handle follows ITS face (by element
    order, like ``move_body``). Matching by shape instead would hand the old +X face's id to whichever face now sits at
    +X when a symmetric part is turned half a revolution."""
    plan = [box("b", (10, 20, 30)), rotate("turn", "b", "Z", 0.0, (5, 10, 15))]
    ids = build(plan)
    body = active_rec().bodies[ids["b"]["body"]["id"]]
    before = {rid: (rec.index, tuple(rec.shape.CenterOfMass)) for rid, rec in body.faces.items() if not rec.stale}
    assert len(before) == 6
    result = edit(ids, with_changes(plan, turn={"angle": 180.0}), "turn")
    assert result["applied"] and result["refs"]["created"] == [] and result["refs"]["removed"] == [], result["refs"]
    body = active_rec().bodies[ids["b"]["body"]["id"]]
    for rid, (index, (x, y, z)) in before.items():
        rec = body.faces[rid]
        assert not rec.stale and rec.index == index, (rid, rec.index, index)
        expected = (10.0 - x, 20.0 - y, z)  # a half turn about the vertical axis through the centre of the box
        got = tuple(rec.shape.CenterOfMass)
        assert all(abs(a - b) < 1e-6 for a, b in zip(got, expected, strict=True)), (rid, got, expected)


def check_atomicity_on_the_expensive_union_chain() -> None:
    """Five consecutive rolled-back edits of a tube that feeds the big union leave the document, the registry and the
    undo journals exactly as they were; the edit then still works."""
    plan = frame_plan()
    ids = build(plan)
    for _ in range(5):
        _rolled_back(ids, plan, with_changes(plan, tube_i={"d": 40.0}), "tube_i")
    result = edit(ids, with_changes(plan, tube_o={"d": 30.0}), "tube_o")
    assert result["applied"], result


def check_the_machine_readable_macro_replays_the_edit() -> None:
    """The recorder keeps the edit as the property assignments it was (and nothing of a rolled-back one)."""
    plan = frame_plan()
    ids = build(plan)
    _rolled_back(ids, plan, with_changes(plan, tube_i={"d": 40.0}), "tube_i")
    assert not any("openTransaction('edit" in line for line in main.fc.recorder.lines)
    edit(ids, with_changes(plan, tube_o={"d": 30.0}), "tube_o")
    macro = "\n".join(main.fc.recorder.lines)
    assert ".Radius = 15.0" in macro


# --- journals: undo stays consistent after an edit --------------------------------------------------------------------


def _surviving_prefix_equals_fresh(plan: list[dict[str, Any]], keep: int, label: str) -> None:
    edited_shapes = shapes_of(active_rec())
    build(plan[:keep], "fresh")
    assert_same_shapes(edited_shapes, shapes_of(active_rec()), label)


def _undo_to(keep: int) -> list[str]:
    warnings: list[str] = []
    while len(active_rec().features) > keep:
        reverted = call("undo_last", op("u"))
        warnings.extend(reverted.get("warnings", []))
    return warnings


def check_undo_to_before_an_edited_op_matches_a_fresh_prefix() -> None:
    plan = frame_plan()
    ids = build(plan)
    assert edit(ids, with_changes(plan, sl_aimed={"angle": 170.0}, sl_o={"d": 21.0}), "sl_aimed", "sl_o")["applied"]
    keep = [i["id"] for i in plan].index("sl_o")  # undo the edited cylinder and everything after it
    warnings = _undo_to(keep)
    assert warnings == [], warnings
    _surviving_prefix_equals_fresh(
        with_changes(plan, sl_aimed={"angle": 170.0}, sl_o={"d": 21.0}), keep, "undo before the edited op"
    )


def check_undo_of_the_edited_move_restores_the_object_before_it() -> None:
    """The rotate gets a different angle; undoing it gives the pose the chain has BEFORE it."""
    plan = frame_plan()
    ids = build(plan)
    assert edit(ids, with_changes(plan, sl_aimed={"angle": 170.0}), "sl_aimed")["applied"]
    keep = [i["id"] for i in plan].index("sl_aimed")
    assert _undo_to(keep) == []
    _surviving_prefix_equals_fresh(plan, keep, "undo of the edited move")


def check_a_move_that_was_the_identity_can_be_edited_and_undone() -> None:
    """An op whose pose did not change carries no Placement entry in its journal; after an edit that gives it a real
    rotation the entry must exist, or undoing it would leave the object rotated."""
    plan = [
        cyl("o", 20, 100, (0, 0, 0), "X"),
        rotate("turn", "o", "Z", 0.0, (0, 0, 0)),
    ]
    ids = build(plan)
    assert edit(ids, with_changes(plan, turn={"angle": 30.0}), "turn")["applied"]
    assert _undo_to(1) == []
    _surviving_prefix_equals_fresh(plan, 1, "undo of a move that became real")


def check_undoing_ops_after_the_edit_matches_a_fresh_prefix() -> None:
    plan = frame_plan()
    ids = build(plan)
    changed = with_changes(plan, tube_o={"d": 30.0})
    assert edit(ids, changed, "tube_o")["applied"]
    keep = len(plan) - 4
    assert _undo_to(keep) == []
    _surviving_prefix_equals_fresh(changed, keep, "undo of the ops after the edit")


def check_an_edit_after_an_undo_works_on_the_surviving_chain() -> None:
    plan = frame_plan()
    ids = build(plan)
    keep = [i["id"] for i in plan].index("sl_full")
    _undo_to(keep)
    survivors = {k: v for k, v in ids.items() if k in [i["id"] for i in plan[:keep]]}
    changed = with_changes(plan[:keep], sl_o={"d": 21.0})
    result = edit(survivors, changed, "sl_o")
    assert result["applied"], result
    _surviving_prefix_equals_fresh(changed, keep, "an edit after an undo")


def check_the_edit_runs_under_the_busy_flag_so_the_manual_edit_watcher_stays_quiet() -> None:
    """The document-change observer reports a recompute only while fc.harness_busy() is down: the whole edit (its
    recompute included) must run with the flag up, and the flag must be down again afterwards."""
    import edit as worker_edit

    plan = frame_plan()
    ids = build(plan)
    seen: list[bool] = []
    original = worker_edit.edit_feature

    def spy(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
        seen.append(main.fc.harness_busy())
        return original(doc, params)

    worker_edit.edit_feature = spy  # type: ignore[assignment]
    try:
        assert edit(ids, with_changes(plan, tube_o={"d": 30.0}), "tube_o")["applied"]
    finally:
        worker_edit.edit_feature = original  # type: ignore[assignment]
    assert seen == [True] and main.fc.harness_busy() is False
