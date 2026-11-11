"""Model-tree labels: every FreeCAD object a construction op creates is labelled after the op that made it.

The op id (``base``, ``f_fork``, ...) is what the engineer wrote in the plan; FreeCAD's own default names
(``Fusion002``, ``Cylinder005``) say nothing. The object an op creates as ITS feature carries the op id as its
``Label``; the auxiliary objects of the same op carry ``<op id>_<role>``; a sketch object carries the id of its own
sketch step. Exceptions, asserted below: an op with no single feature object (a split into two pieces, a mirror of
both bosses and cuts, a chamfer in two flip groups) names
its pieces ``<op id>_<role>``/``<op id>_<n>``; ``move_body`` creates nothing, so the moved object keeps the label of
its creator. Where FreeCAD had to de-duplicate a label, the feature's own label follows the object. ``Name`` (the
internal id every registry, selection token and the macro recorder use) is never touched. Driven straight on the
worker, on real FreeCAD."""

import re
from typing import Any

import FreeCAD as App
import main
from harness import (
    IDENTITY,
    build_part01,
    build_part02,
    build_part03,
    build_part04,
    build_part06,
    call,
    circle,
    expect_error,
    extrude,
    faces_of,
    new_document,
    op,
    rect,
    select_face,
    sketch,
)

# the objects FreeCAD itself puts inside every PartDesign::Body (not created by an op)
ORIGIN_TYPES = {"App::Origin", "App::Line", "App::Plane", "App::Point"}
DEFAULT_NAME = re.compile(
    r"^(Box|Cylinder|Fusion|Common|Cut|Body|Base|BaseFeature|Pad|Pocket|Hole|Revolution|Sketch|HoleSketch|Copy|Mirror|Mirrored|"
    r"LinearPattern|MultiTransform|PolarPattern|Chamfer|Fillet|Thickness|Draft|Tools|ToolCopy|Pipe|Component|"
    r"RevolveAxis|PatternDir\d|PatternAxis|MirrorPlane|NeutralPlane|SweepSpine\w*|Split\w+)\d*$"
)


def _doc() -> Any:
    return main.WORKER.active.fc_doc


def _objects(fc_doc: Any = None) -> list[Any]:
    return [o for o in (fc_doc or _doc()).Objects if o.TypeId not in ORIGIN_TYPES]


def _labels(fc_doc: Any = None) -> list[str]:
    return sorted(o.Label for o in _objects(fc_doc))


def _by_name(fc_doc: Any = None) -> dict[str, str]:
    return {o.Name: o.Label for o in _objects(fc_doc)}


def _assert_no_default_names(fc_doc: Any = None) -> None:
    bad = [(o.Name, o.Label) for o in _objects(fc_doc) if DEFAULT_NAME.match(o.Label)]
    assert not bad, f"objects left with FreeCAD's default label: {bad}"


def _plane(origin: tuple[float, float, float], normal: tuple[float, float, float]) -> dict[str, Any]:
    import geometry as g

    n = g.unit(normal)
    seed = g.Z if abs(g.dot(n, g.Z)) < 0.9 else g.X
    u = g.unit(g.perpendicular_part(seed, n))
    v = g.cross(n, u)
    return {"origin": list(origin), "u": list(u), "v": list(v), "n": list(n)}


def _box(
    operation_id: str, size: tuple[float, float, float] = (100, 60, 20), at: tuple[float, float, float] = (0, 0, 0)
) -> dict[str, Any]:
    return call(
        "box", op(operation_id, size=list(size), transform={"origin": list(at), "rotation": IDENTITY["rotation"]})
    )


# --- concept ops ---------------------------------------------------------------------------------------------------


def check_concept_ops_are_labelled_with_the_op_id() -> None:
    new_document("lbl_concept")
    block = _box("block")
    tool = call(
        "cylinder",
        op("bore_tool", diameter_mm=30, height_mm=20, axis={"origin": [50, 30, 0], "direction": [0, 0, 1]}),
    )
    call("boolean", op("bored", kind="subtract", target=block["created_bodies"][0], tools=[tool["created_bodies"][0]]))
    assert _labels() == ["block", "bore_tool", "bored"], _labels()
    copy = call("copy_body", op("twin", source=block["created_bodies"][0], transform=IDENTITY))
    call(
        "move_body",
        op(
            "place", body=copy["created_bodies"][0], transform={"origin": [0, 100, 0], "rotation": IDENTITY["rotation"]}
        ),
    )
    call("mirror_body", op("flipped", source=copy["created_bodies"][0], plane=_plane((0, 0, 0), (1, 0, 0))))
    # move_body creates nothing and must not relabel the object it moved
    assert _by_name() == {
        "Box": "block",
        "Cylinder": "bore_tool",
        "Cut": "bored",
        "Copy": "twin",
        "Mirror": "flipped",
    }, _by_name()
    _assert_no_default_names()
    tree = call("feature_tree", op("t"))["nodes"]
    assert [n["name"] for n in tree] == ["block", "bore_tool", "bored", "twin", "place", "flipped"], tree


def check_union_and_multi_tool_subtract_label_their_helpers() -> None:
    new_document("lbl_boolean")
    a = _box("part_a", (10, 10, 10))["created_bodies"][0]
    b = _box("part_b", (10, 10, 10), (5, 0, 0))["created_bodies"][0]
    call("boolean", op("joined", kind="union", target=a, tools=[b]))
    assert _labels() == ["joined", "part_a", "part_b"], _labels()

    new_document("lbl_boolean_tools")
    base = _box("base_block", (100, 100, 20))["created_bodies"][0]
    t1 = _box("pin_a", (10, 10, 40), (20, 20, -10))["created_bodies"][0]
    t2 = _box("pin_b", (10, 10, 40), (60, 60, -10))["created_bodies"][0]
    call("boolean", op("coped", kind="subtract", target=base, tools=[t1, t2], keep_tools=True))
    assert _labels() == sorted(
        ["base_block", "pin_a", "pin_b", "coped", "coped_tools", "coped_tool_1", "coped_tool_2"]
    ), _labels()
    _assert_no_default_names()


def check_split_labels_each_piece() -> None:
    new_document("lbl_split")
    source = _box("slab")["created_bodies"][0]
    call("split", op("halves", source=source, plane=_plane((50, 0, 0), (1, 0, 0)), keep="both"))
    assert _labels() == ["halves_negative", "halves_positive", "slab"], _labels()
    new_document("lbl_split_one")
    source = _box("slab")["created_bodies"][0]
    call("split", op("keep_one", source=source, plane=_plane((50, 0, 0), (1, 0, 0)), keep="positive"))
    assert _labels() == ["keep_one", "slab"], _labels()


# --- detail ops ----------------------------------------------------------------------------------------------------


def check_plate_with_hole_and_pattern_is_labelled_after_its_ops() -> None:
    build_part01()
    # per object, so a swap between two objects would fail too
    assert _by_name() == {
        "Body": "base_body",
        "Sketch": "base_sketch",  # the id of the sketch step the op ``base`` consumed
        "Pad": "base",
        "HoleSketch": "hole_1_sketch",
        "Hole": "hole_1",
        "PatternDir1": "holes_dir1",
        "PatternDir2": "holes_dir2",
        "LinearPattern": "holes_pattern1",
        "LinearPattern001": "holes_pattern2",
        "MultiTransform": "holes",
    }, _by_name()
    _assert_no_default_names()
    # the registry keeps tracking by Name: the tree names are the op ids, and the feature object of each carries it
    active = main.WORKER.active
    assert [f.label for f in active.features] == ["base", "hole_1", "holes"]
    for feature in active.features:
        assert active.fc_doc.getObject(feature.fc_main).Label == feature.label, (feature.id, feature.label)
    assert [n["name"] for n in call("feature_tree", op("t"))["nodes"]] == ["base", "hole_1", "holes"]


def check_pocket_fillet_and_chamfer_are_labelled() -> None:
    build_part03()
    labels = _labels()
    for wanted in ("base", "pocket", "pocket_sketch", "inner_fillet", "lead_in", "base_body"):
        assert wanted in labels, (wanted, labels)
    _assert_no_default_names()


def check_circular_pattern_and_its_axis_are_labelled() -> None:
    build_part04()
    labels = _labels()
    for wanted in ("disc", "bore", "bolt_1", "bolts", "bolts_axis", "bore_sketch", "bolt_1_sketch", "disc_body"):
        assert wanted in labels, (wanted, labels)
    _assert_no_default_names()


def check_revolve_labels_profile_axis_and_result() -> None:
    build_part02()
    assert _by_name() == {
        "Body": "body_body",
        "Sketch": "profile",  # the id of the sketch step the revolve consumed
        "RevolveAxis": "body_axis",
        "Revolution": "body",
    }, _by_name()


def check_a_consumed_sketch_carries_the_id_of_its_own_step() -> None:
    """The sketch step (``outline``) and the extrude that consumes it (``plate``) are two steps with two ids: the tree
    shows the sketch object under the id the agent gave the sketch step, not as ``plate_sketch``."""
    new_document("lbl_sketch_step")
    sk = sketch("outline", "XY", [rect("r", (50, 30), 100, 60)])
    extrude("plate", sk, 8)
    assert _by_name() == {"Body": "plate_body", "Sketch": "outline", "Pad": "plate"}, _by_name()
    _assert_no_default_names()


def check_mirror_labels_plane_and_reflection() -> None:
    new_document("lbl_mirror")
    sk = sketch("s0", "XY", [rect("r", (50, 30), 100, 60)])
    base = extrude("base", sk, 8)
    top = select_face(faces_of(base["feature"]), normal=(0, 0, 1))
    boss_sketch = sketch("bs", top["handle"], [circle("c", (-40, -20), 10)])
    boss = extrude("boss", boss_sketch, 5)
    call("mirror", op("flip", features=[boss["feature"]], plane=_plane((50, 0, 0), (1, 0, 0))))
    labels = _labels()
    for wanted in ("base", "boss", "bs", "flip", "flip_plane"):  # ``bs``: the id of the sketch step of the boss
        assert wanted in labels, (wanted, labels)
    _assert_no_default_names()


def check_shell_and_draft_on_a_primitive_label_the_converted_body() -> None:
    new_document("lbl_shell")
    block = _box("block", (40, 30, 20))
    top = select_face(faces_of(block["feature"]), normal=(0, 0, 1))
    call("shell", op("hollow", faces=[top["handle"]], thickness_mm=2.0, outward=False, join="intersection"))
    # a primitive becomes a PartDesign body: its shape copy, the link FreeCAD makes to it and the Body are the shell
    # op's helpers
    assert _by_name() == {
        "Box": "block",
        "Base": "hollow_base",
        "BaseFeature": "hollow_base_feature",
        "Body": "hollow_body",
        "Thickness": "hollow",
    }, _by_name()

    new_document("lbl_draft")
    block = _box("block", (40, 30, 20))
    side = select_face(faces_of(block["feature"]), normal=(1, 0, 0))
    xy = {"origin": [0.0, 0.0, 0.0], "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0], "n": [0.0, 0.0, 1.0]}
    call("draft", op("lean", faces=[side["handle"]], neutral=xy, pull=[0, 0, 1], angle_deg=3.0))
    assert _by_name() == {
        "Box": "block",
        "Base": "lean_base",
        "BaseFeature": "lean_base_feature",
        "Body": "lean_body",
        "NeutralPlane": "lean_neutral_plane",
        "Draft": "lean",
    }, _by_name()


def check_sweep_labels_spine_and_binder() -> None:
    new_document("lbl_sweep")
    sec = sketch("sec", "XY", [circle("c", (0, 0), 10)])
    call("sweep", op("pipe", sketch=sec["sketch"], path=[[0, 0, 0], [0, 0, 100]], corner_radius_mm=None, merge=True))
    labels = _labels()
    for wanted in ("pipe", "sec", "pipe_spine", "pipe_body"):
        assert wanted in labels, (wanted, labels)
    _assert_no_default_names()


def check_assembly_components_are_labelled_after_their_op() -> None:
    parts = build_part06()
    assert parts["asm"] is not None
    asm = main.WORKER.active.fc_doc  # the assembly is the active document
    # the Assembly workbench's container, then each component (an App::Part named after its op) and its link to the
    # body of the part document (named after the op and the body)
    assert _labels(asm) == ["Assembly", "ins_block", "ins_block_body1", "ins_pin", "ins_pin_body1"], _labels(asm)
    _assert_no_default_names(asm)


# --- the label follows the transaction ------------------------------------------------------------------------------


def check_a_failed_op_and_an_undone_op_leave_the_label_free() -> None:
    new_document("lbl_undo")
    sec = sketch("sec", "XY", [circle("c", (0, 0), 10)])
    hairpin = [[0, 0, 0], [0, 0, 100], [8, 0, 100], [8, 0, 0]]
    expect_error(
        "geometry_failed", "sweep", op("tube", sketch=sec["sketch"], path=hairpin, corner_radius_mm=3.0, merge=True)
    )
    assert _labels() == []  # the rolled-back transaction took every object (and its label) with it
    call("sweep", op("tube", sketch=sec["sketch"], path=[[0, 0, 0], [0, 0, 100]], corner_radius_mm=None, merge=True))
    done = _labels()
    assert "tube" in done and not [label for label in done if re.search(r"\d{3}$", label)], done

    call("undo_last", op("u"))
    assert _labels() == []
    call("sweep", op("tube", sketch=sec["sketch"], path=[[0, 0, 0], [0, 0, 100]], corner_radius_mm=None, merge=True))
    assert _labels() == done, (_labels(), done)


def check_a_label_taken_by_hand_is_kept_apart_not_stolen() -> None:
    """FreeCAD makes a Label unique inside a document: an object the engineer already named ``base`` keeps it, the
    op's object gets FreeCAD's own deterministic suffix, nothing raises -- and the feature's label (what feature_tree
    reports) follows the object instead of claiming a name the tree does not show."""
    new_document("lbl_collision")
    doc = _doc()
    hand = doc.addObject("Part::Feature", "ByHand")
    hand.Label = "base"
    sk = sketch("s0", "XY", [rect("r", (50, 30), 100, 60)])
    extrude("base", sk, 8)
    assert hand.Label == "base"
    feature = main.WORKER.active.features[-1]
    assert doc.getObject(feature.fc_main).Label == "base001", doc.getObject(feature.fc_main).Label
    assert feature.label == "base001", feature.label
    tree = call("feature_tree", op("t"))["nodes"]
    # the registry's feature first, then the object made by hand (a manual node, named by its own Label)
    flags = [(n["name"], bool(n["parameters"].get("manual"))) for n in tree]
    assert flags == [("base001", False), ("base", True)], tree


def check_an_op_id_that_collides_with_another_ops_helper_label_is_reported_as_it_is() -> None:
    """Op ``base`` labels its Body ``base_body``; a later op NAMED ``base_body`` gets FreeCAD's ``base_body001``, and
    both its feature and the feature tree say so."""
    new_document("lbl_helper_collision")
    sk = sketch("s0", "XY", [rect("r", (50, 30), 100, 60)])
    extrude("base", sk, 8)
    _box("base_body", (10, 10, 10), (200, 0, 0))
    assert _by_name()["Box"] == "base_body001", _by_name()
    assert _by_name()["Body"] == "base_body"
    assert [f.label for f in main.WORKER.active.features] == ["base", "base_body001"]
    assert [n["name"] for n in call("feature_tree", op("t"))["nodes"]] == ["base", "base_body001"]


# --- no helper object leaks into the feature tree as a hand-made feature -------------------------------------------


def _assert_tree_is_only(*names: str) -> None:
    nodes = call("feature_tree", op("t"))["nodes"]
    assert [n["name"] for n in nodes] == list(names), nodes
    assert not [n for n in nodes if n["parameters"].get("manual")], nodes  # nothing the harness made reads as manual


def check_converting_a_primitive_leaves_no_manual_node_in_the_tree() -> None:
    """``PartDesign::FeatureBase`` is made by FreeCAD when a primitive becomes a PartDesign body; it belongs to the op
    that converted it, it is not something the engineer made by hand."""
    xy = {"origin": [0.0, 0.0, 0.0], "u": [1.0, 0.0, 0.0], "v": [0.0, 1.0, 0.0], "n": [0.0, 0.0, 1.0]}
    new_document("lbl_manual_shell")
    block = _box("block", (40, 30, 20))
    top = select_face(faces_of(block["feature"]), normal=(0, 0, 1))
    call("shell", op("hollow", faces=[top["handle"]], thickness_mm=2.0, outward=False, join="intersection"))
    _assert_tree_is_only("block", "hollow")

    new_document("lbl_manual_draft")
    block = _box("block", (40, 30, 20))
    side = select_face(faces_of(block["feature"]), normal=(1, 0, 0))
    call("draft", op("lean", faces=[side["handle"]], neutral=xy, pull=[0, 0, 1], angle_deg=3.0))
    _assert_tree_is_only("block", "lean")

    new_document("lbl_manual_fillet")
    block = _box("block", (40, 30, 20))
    faces = faces_of(block["feature"])
    top, side = select_face(faces, normal=(0, 0, 1)), select_face(faces, normal=(1, 0, 0))
    edge = call("edges_between", op("q", a=top["handle"], b=side["handle"]))["edges"][0]
    call("fillet", op("round", edges=[edge["handle"]], radius_mm=2))
    _assert_tree_is_only("block", "round")

    new_document("lbl_manual_chamfer")
    block = _box("block", (40, 30, 20))
    faces = faces_of(block["feature"])
    top, side = select_face(faces, normal=(0, 0, 1)), select_face(faces, normal=(1, 0, 0))
    edge = call("edges_between", op("q", a=top["handle"], b=side["handle"]))["edges"][0]
    call("chamfer", op("bevel", edges=[edge["handle"]], distance1_mm=2, distance2_mm=None, distance1_face=None))
    _assert_tree_is_only("block", "bevel")


# --- ops without a single feature object ----------------------------------------------------------------------------


def check_a_chamfer_in_two_flip_groups_numbers_its_features() -> None:
    """Two vertical edges of a side face: the box lists the face before one neighbour and after the other, so the two
    edges need opposite ``FlipDirection`` and become two ``Chamfer`` features of one op."""
    new_document("lbl_chamfer_groups")
    sk = sketch("s0", "XY", [rect("r", (50, 30), 100, 60)])
    base = extrude("base", sk, 8)
    faces = faces_of(base["feature"])
    right = select_face(faces, normal=(1, 0, 0))
    edges = [
        call("edges_between", op("q", a=right["handle"], b=select_face(faces, normal=n)["handle"]))["edges"][0]
        for n in ((0, -1, 0), (0, 1, 0))
    ]
    call(
        "chamfer",
        op(
            "edge_break", edges=[e["handle"] for e in edges], distance1_mm=2, distance2_mm=3,
            distance1_face=right["handle"]
        ),
    )
    chamfers = {o.Name: o.Label for o in _objects() if o.TypeId == "PartDesign::Chamfer"}
    assert chamfers == {"Chamfer": "edge_break_1", "Chamfer001": "edge_break_2"}, chamfers
    assert main.WORKER.active.features[-1].label == "edge_break"
    _assert_no_default_names()


def check_mirror_of_a_boss_and_a_cut_names_both_reflections() -> None:
    new_document("lbl_mirror_two")
    sk = sketch("s0", "XY", [rect("r", (50, 30), 100, 60)])
    base = extrude("base", sk, 8)
    top = select_face(faces_of(base["feature"]), normal=(0, 0, 1))
    boss = extrude("boss", sketch("bs", top["handle"], [circle("c", (-40, -20), 10)]), 5)
    hole = call(
        "hole",
        op(
            "bore",
            face=top["handle"],
            position=[-40, 10],
            standard="simple",
            size=None,
            diameter_mm=6,
            end_condition="through_all",
            depth_mm=None,
        ),
    )
    call("mirror", op("twin", features=[boss["feature"], hole["feature"]], plane=_plane((50, 0, 0), (1, 0, 0))))
    mirrored = {o.Name: o.Label for o in _objects() if o.TypeId in ("PartDesign::Mirrored", "PartDesign::Plane")}
    assert mirrored == {"MirrorPlane": "twin_plane", "Mirrored": "twin_boss", "Mirrored001": "twin_cut"}, mirrored
    _assert_no_default_names()


def check_keep_tools_with_a_single_tool_names_its_copy_tool() -> None:
    new_document("lbl_single_tool")
    base = _box("base_block", (100, 100, 20))["created_bodies"][0]
    pin = _box("pin", (10, 10, 40), (20, 20, -10))["created_bodies"][0]
    call("boolean", op("coped", kind="subtract", target=base, tools=[pin], keep_tools=True))
    assert _by_name() == {
        "Box": "base_block",
        "Box001": "pin",
        "ToolCopy": "coped_tool",
        "Cut": "coped",
    }, _by_name()


def check_split_names_follow_the_pieces_kept() -> None:
    new_document("lbl_split_names")
    source = _box("slab")["created_bodies"][0]
    call("split", op("halves", source=source, plane=_plane((50, 0, 0), (1, 0, 0)), keep="both"))
    assert _by_name() == {"Box": "slab", "SplitPositive": "halves_positive", "SplitNegative": "halves_negative"}


# --- the macro recorder replays the labels --------------------------------------------------------------------------


def check_the_exported_macro_replays_the_labels() -> None:
    build_part01()
    original = _by_name()
    script = call("export_macro", op("x", path="unused"))["script"]
    assert ".Label = 'holes'" in script and ".Label = 'hole_1_sketch'" in script  # the labels are recorded lines
    namespace: dict[str, Any] = {}
    exec(script, namespace)  # noqa: S102 - replay of the recorded calls on a fresh document
    replayed = namespace["docs"]["doc:1"]
    assert _by_name(replayed) == original, (_by_name(replayed), original)
    App.closeDocument(replayed.Name)


def check_the_exported_macro_replays_a_primitive_converted_to_partdesign() -> None:
    """The conversion adds objects FreeCAD makes by itself (the ``FeatureBase`` link): the replay must label them
    like the live document did."""
    new_document("lbl_replay_shell")
    block = _box("block", (40, 30, 20))
    top = select_face(faces_of(block["feature"]), normal=(0, 0, 1))
    call("shell", op("hollow", faces=[top["handle"]], thickness_mm=2.0, outward=False, join="intersection"))
    original = _by_name()
    assert original["BaseFeature"] == "hollow_base_feature", original
    script = call("export_macro", op("x", path="unused"))["script"]
    namespace: dict[str, Any] = {}
    exec(script, namespace)  # noqa: S102 - replay of the recorded calls on a fresh document
    replayed = namespace["docs"]["doc:1"]
    assert _by_name(replayed) == original, (_by_name(replayed), original)
    App.closeDocument(replayed.Name)
