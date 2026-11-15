# ruff: noqa: E501
"""The marks of a sheet that are FreeCAD's own objects (welds, balloons, tables, leaders): what the items ask of TechDraw as
plain data, no FreeCAD needed."""

import sys
from pathlib import Path

WORKER_DIR = Path(__file__).resolve().parents[3] / "src" / "cotaforge" / "adapters" / "freecad" / "worker"
if str(WORKER_DIR) not in sys.path:
    sys.path.append(str(WORKER_DIR))

import sheet_marks as sm  # noqa: E402
import sheet_units as su  # noqa: E402
from sheet_model import DIMENSIONS, HATCH, TEXT, ZONE, Circle, Line, Solid, Text  # noqa: E402

VIEWS = {"front": (100.0, 100.0, 200.0, 160.0), "isometric": (250.0, 100.0, 350.0, 180.0)}


def _unit(item_type: str, entities: tuple, natives: tuple = ()) -> su.Unit:
    return su.Unit("k1", f"k1 {item_type}", su.KIND_ITEM, entities, item_type, None, natives)


def test_a_weld_is_a_leader_and_a_symbol_and_one_more_leader_per_extra_arrow() -> None:
    weld = sm.NativeWeld("w1", "front", ((120.0, 130.0), (140.0, 130.0), (160.0, 130.0)), (150.0, 175.0), 1.0, 14.0, "fillet", "arrow", "a3", "", "a3 4×", "fillet a3")
    assert sm.count(weld) == 5  # the leader of the reference line, the symbol, an arrow leader for each of the three joints
    unit = _unit("weld", (Line(0, 0, 1, 1, DIMENSIONS),), (weld,))
    assert su.object_count(unit) == 5
    assert unit.residue == ()  # the objects are the whole of the item: no entity stays as a drawing


def test_a_weld_that_changes_is_not_the_one_the_page_holds() -> None:
    base = sm.NativeWeld("w1", "front", ((120.0, 130.0),), (150.0, 175.0), 1.0, 14.0, "fillet", "arrow", "a3", "", "a3", "")
    other = sm.NativeWeld("w1", "front", ((120.0, 130.0),), (150.0, 175.0), 1.0, 14.0, "fillet", "both", "a3", "", "a3", "")
    assert su.signature(_unit("weld", (), (base,))) != su.signature(_unit("weld", (), (other,)))
    assert su.signature(_unit("weld", (), (base,))) == su.signature(_unit("weld", (), (base,)))


def test_the_aws_tiles_cover_every_weld_the_item_draws() -> None:
    for kind in ("fillet", "v", "square"):
        assert set(sm.AWS_TILES[kind]) == {"arrow", "other"}
        assert all(path.startswith("Welding/AWS/") and path.endswith(".svg") for path in sm.AWS_TILES[kind].values())
    assert sm.TILE_ROW == {"arrow": -1, "other": 0}  # FreeCAD 1.1.3 paints row -1 below the reference line (the arrow side in AWS) and row 0 above it


def test_the_arrow_side_is_the_file_that_hangs_below_the_line_and_the_other_side_the_one_that_stands_on_it() -> None:
    for kind, tiles in sm.AWS_TILES.items():
        assert tiles["arrow"].endswith("Down.svg") and tiles["other"].endswith("Up.svg"), kind  # Down: below the line, its leg on it
        assert tiles["arrow"] != tiles["other"]
    assert sm.TILE_ROW["arrow"] < sm.TILE_ROW["other"]  # the lower row is the arrow side


def test_the_aws_tiles_are_files_of_freecads_library_or_the_library_is_not_here() -> None:
    folder = next((p for p in (Path(r"C:\Program Files\FreeCAD 1.1\data\Mod\TechDraw\Symbols"),) if p.is_dir()), None)
    if folder is None:
        return  # no FreeCAD on this machine: the names are checked where it is
    missing = sorted(p for tiles in sm.AWS_TILES.values() for p in tiles.values() if not (folder / p).is_file())
    assert not missing, f"not in FreeCAD's weld symbols: {missing}"


def test_a_table_and_a_balloon_are_one_object_each() -> None:
    table = sm.NativeTable("l1", "parts_list", (("1", "4"), ("Pos", "Qty")), (10.0, 12.0), (6.0, 6.0), 400.0, 40.0)
    balloon = sm.NativeBalloon("b1", "isometric", "7", (300.0, 150.0), (290.0, 140.0))
    assert su.object_count(_unit("parts_list", (), (table,))) == 1
    assert su.object_count(_unit("balloons", (), (balloon, balloon))) == 2
    assert _unit("balloons", (Circle(0, 0, 5, DIMENSIONS),), (balloon,)).residue == ()


def test_the_opening_arrow_of_a_mould_is_a_leader_with_an_arrowhead_and_its_bar_a_cosmetic_line() -> None:
    tail, tip = (60.0, 130.0), (60.0, 146.0)
    shaft = Line(tail[0], tail[1], tip[0], tip[1], DIMENSIONS)
    head = Solid((tip, (59.0, 143.0), (61.0, 143.0)), DIMENSIONS)
    bar = Line(57.5, 130.0, 62.5, 130.0, DIMENSIONS)
    label = Text(63.0, 146.0, "Apertura", 3.5, TEXT, "left")
    unit = _unit("pull_direction", (shaft, head, bar, label))
    made, covered = su.mould_marks(unit, VIEWS, [], set())
    (leader,) = [n for n in made if isinstance(n, sm.NativeLeader)]
    assert leader.view == "front" and leader.points == (tip, tail) and leader.arrow  # the arrowhead is at the tip
    (cosmetic,) = [n for n in made if isinstance(n, su.NativeCosmetic)]
    assert cosmetic.kind == "line" and cosmetic.values == (57.5, 130.0, 62.5, 130.0) and cosmetic.style == "thin"
    (note,) = [n for n in made if isinstance(n, su.NativeNote)]
    assert note.lines == ("Apertura",) and note.at[0] == 63.0 and note.at[1] == 146.0 + 3.5
    assert covered == {id(shaft), id(head), id(bar), id(label)}  # the sheet draws none of them again


def test_a_text_anchored_right_or_centred_is_set_from_its_left_edge() -> None:
    right = Text(50.0, 100.0, "G", 3.5, TEXT, "right")
    centred = Text(80.0, 100.0, "EE", 3.5, TEXT, "center")
    made, _ = su.mould_marks(_unit("mould_zone", (right, centred)), VIEWS, [], set())
    notes = {n.lines[0]: n for n in made if isinstance(n, su.NativeNote)}
    assert abs(notes["G"].at[0] - (50.0 - 3.5 * 0.6)) < 1e-9
    assert abs(notes["EE"].at[0] - (80.0 - 3.5 * 0.6)) < 1e-9


def test_the_hatch_of_a_zone_and_a_circle_of_it_stand_on_the_view_beside_them() -> None:
    hatch = Line(140.0, 120.0, 142.0, 122.0, HATCH)
    ring = Circle(150.0, 130.0, 3.5, ZONE)
    made, covered = su.mould_marks(_unit("mould_zone", (hatch, ring)), VIEWS, [], {id(ring)})  # the ring is a cosmetic circle already
    assert [(n.view, n.kind) for n in made if isinstance(n, su.NativeCosmetic)] == [("front", "line")]
    assert covered == {id(hatch)}


def test_a_mark_inside_a_view_the_sheet_drew_itself_is_left_to_the_sheet() -> None:
    line = Line(10.0, 10.0, 20.0, 10.0, DIMENSIONS)
    made, covered = su.mould_marks(_unit("no_draft", (line,)), VIEWS, [(0.0, 0.0, 30.0, 30.0)], set())
    assert made == [] and covered == set()  # a section is no TechDraw view: nothing to hang a cosmetic line on


def test_other_items_are_not_touched() -> None:
    line = Line(110.0, 110.0, 120.0, 110.0, DIMENSIONS)
    assert su.mould_marks(_unit("note", (line,)), VIEWS, [], set()) == ([], set())


def test_two_labels_with_the_same_words_at_one_height_are_not_taken_for_each_other() -> None:
    import sheet_notefit as nf

    near = nf.TextBox("LP", 100.0, 4.0, 3.5, 102.0, 50.0)
    far = nf.TextBox("LP", 130.0, 4.0, 3.5, 132.0, 50.0)  # the label of another view, 30 mm to the right
    assert nf.pick_block([far, near], 104.0, 50.0, "LP") == (100.0, 4.0, 3.5)
    assert nf.pick_block([near, far], 128.0, 50.0, "LP") == (130.0, 4.0, 3.5)


def test_the_labels_of_a_mould_are_loose_notes_the_window_is_not_asked_about() -> None:
    made, _ = su.mould_marks(_unit("mould_zone", (Text(50.0, 100.0, "G", 3.5, TEXT, "left"),)), VIEWS, [], set())
    (note,) = [n for n in made if isinstance(n, su.NativeNote)]
    assert note.fit is False
    assert su.signature(_unit("mould_zone", (), (note,))) != su.signature(_unit("mould_zone", (), (su.NativeNote("k1", "m1", ("G",), note.at, 3.5, 4.9),)))
