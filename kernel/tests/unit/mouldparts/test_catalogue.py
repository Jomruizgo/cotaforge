"""The DME tables and what is built from them: every number is one the catalogue prints, every part is consistent with the
others (no hole without its pin, no pin that leaves its plate), and a reference that is not in the tables is refused with
what is."""

import math

import pytest

from cotaforge.contract import PartSpec
from cotaforge.mouldparts import ComponentError, Placement, angle_pin_rows, dme, expand, wear_rows
from cotaforge.mouldparts.dme import IN


def _one(kind: str, ref: str | None = None, size: str | None = None) -> PartSpec:
    (spec,) = expand(kind, ref, size, [Placement()])
    return spec


# --- the single parts ---------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("ref", "dia_in", "length_in"),
    [
        ("5000GL", 0.75, 1.75),
        ("5104GL", 0.875, 4.25),
        ("5117GL", 0.875, 10.75),
        ("5219GL", 1.0, 12.25),
        ("5326GL", 1.25, 15.75),
    ],
)
def test_a_guide_pin_item_gives_the_diameter_and_the_length_of_its_row(
    ref: str, dia_in: float, length_in: float
) -> None:
    spec = _one("guide_pin", ref)
    pin = next(p for p in dme.GUIDE_PINS if p.nominal_in == dia_in)
    assert spec.volume_mm3 == pytest.approx(
        math.pi * ((pin.head_d_in * IN / 2) ** 2 * pin.head_k_in * IN + (pin.d_in * IN / 2) ** 2 * length_in * IN)
    )
    assert spec.catalog is not None and spec.catalog.reference == ref and f"{length_in:g}" in spec.catalog.size


def test_a_guide_pin_length_the_table_leaves_out_is_refused() -> None:
    with pytest.raises(ComponentError, match="not a DME guide pin"):
        _one("guide_pin", "5114GL")  # 7/8 in dia is not stocked at 9 1/4 in (a dash in the table)


@pytest.mark.parametrize(
    ("ref", "id_in", "length_in"),
    [
        ("5700", 0.75, 0.875),
        ("5709", 0.75, 5.875),
        ("5710", 0.875, 0.875),
        ("5718", 0.875, 4.875),
        ("5720", 0.875, 5.875),
        ("5760", 1.25, 5.875),
    ],
)
def test_a_shoulder_bushing_item_gives_its_bore_and_its_length(ref: str, id_in: float, length_in: float) -> None:
    spec = _one("guide_bush", ref)
    assert spec.catalog is not None
    assert f"ID {id_in:g} in" in spec.catalog.size and f"{length_in:g} in" in spec.catalog.size.split("x")[-1]


def test_the_item_the_bushing_table_skips_does_not_exist() -> None:
    with pytest.raises(ComponentError):
        _one("guide_bush", "5719")


def test_the_sprue_bushing_is_a_revolved_profile_with_a_tapered_bore_and_the_catalogue_volume() -> None:
    spec = _one("sprue_bush", "B0271")  # B02: stem 1 29/32, head 7/8, orifice 7/32, R 1/2
    stem, head = 1 + 29 / 32, 7 / 8
    r_top = (7 / 32) * IN / 2
    r_bottom = r_top + (stem + head) * IN * (0.5 / 12) / 2
    outer = math.pi * ((0.5 * IN) ** 2 * stem * IN + (1.0 * IN) ** 2 * head * IN)
    bore = math.pi * (stem + head) * IN / 3 * (r_top**2 + r_top * r_bottom + r_bottom**2)
    assert spec.volume_mm3 == pytest.approx(outer - bore)
    assert spec.features[0].kind == "revolve"


@pytest.mark.parametrize("ref", ["B9971", "B0261", "B0275", "X0271", "B02"])
def test_a_sprue_item_that_is_not_b_series_is_refused(ref: str) -> None:
    with pytest.raises(ComponentError, match="B-series"):
        _one("sprue_bush", ref)


def test_the_locating_ring_is_the_published_diameter_and_height() -> None:
    spec = _one("locating_ring", "6501")
    assert spec.volume_mm3 == pytest.approx(math.pi / 4 * ((3.99 * IN) ** 2 - (2.0 * IN) ** 2) * (17 / 32) * IN)
    with pytest.raises(ComponentError, match="locating ring"):
        _one("locating_ring", "6502")


@pytest.mark.parametrize(
    "args",
    [("ejector_pin", "EJP-EHN-030-0160", None), ("ejector_pin", None, "3x160"), ("ejector_pin", None, "3 x 160")],
)
def test_an_ejector_pin_is_given_by_item_or_by_size_and_means_the_same(
    args: tuple[str, str | None, str | None],
) -> None:
    spec = _one(*args)
    assert spec.catalog is not None and spec.catalog.reference == "EJP-EHN-030-0160"
    assert spec.volume_mm3 == pytest.approx(
        math.pi * ((6 / 2) ** 2 * 3 + (3 / 2) ** 2 * 160)
    )  # head 6 x 3, shank 3 x 160


@pytest.mark.parametrize("size", ["3x80", "3.3x160", "x", "abc"])
def test_an_ejector_pin_off_the_tables_is_refused(size: str) -> None:
    with pytest.raises(ComponentError):
        _one("ejector_pin", None, size)


def test_every_published_ejector_diameter_has_a_head_thicker_than_zero_and_wider_than_the_shaft() -> None:
    for d, (head, k) in dme.EJECTOR_PINS.items():
        assert head > d and k > 0


def test_a_return_pin_item_gives_diameter_head_and_length() -> None:
    spec = _one("return_pin", "7514")
    d, head, length = dme.RETURN_PINS["7514"]
    assert (d, head, length) == (0.625, 0.875, 6.063)
    assert spec.volume_mm3 == pytest.approx(
        math.pi * ((head * IN / 2) ** 2 * 0.25 * IN + (d * IN / 2) ** 2 * length * IN)
    )


def test_the_angle_pin_rows_are_the_published_stock_lengths_with_the_catalogue_items() -> None:
    rows = angle_pin_rows(12)
    assert [r.length for r in rows] == [40, 60, 80, 100, 110, 120, 140, 160]
    assert rows[1].item == "APD1260" and rows[-1].item == "APD12160"
    assert (rows[0].head_d, rows[0].head_k) == (16, 6)
    with pytest.raises(ComponentError):
        angle_pin_rows(13)
    spec = _one("angle_pin", "APD1260")
    assert spec.volume_mm3 == pytest.approx(math.pi * ((16 / 2) ** 2 * 6 + (12 / 2) ** 2 * 60))


def test_wear_plate_items_follow_the_published_rule() -> None:
    items = {r.item: r for r in wear_rows()}
    assert (items["SLP0604"].thickness, items["SLP0604"].width, items["SLP0604"].length) == (0.5 * IN, 1.5 * IN, 4 * IN)
    assert items["SLP0504"].width == pytest.approx(1.0 * IN) and items["SLP0504"].thickness == pytest.approx(0.375 * IN)
    assert items["SLP0762"].length == pytest.approx(12 * IN) and items["SLP0762"].width == pytest.approx(4 * IN)
    assert "SLP0653" not in items  # 4 in wide plates start at 4 in long
    assert len(items) == len(wear_rows())  # no item twice
    spec = _one("wear_plate", "SLP0604")
    assert spec.volume_mm3 == pytest.approx(0.5 * 1.5 * 4 * IN**3)


def test_a_component_with_no_ref_says_so() -> None:
    with pytest.raises(ComponentError, match="needs ref"):
        _one("guide_pin")
    with pytest.raises(ComponentError, match="unknown mould component kind"):
        expand("magic_wand", None, None, [])


def test_several_placements_make_several_bodies_with_their_own_roles() -> None:
    parts = expand(
        "ejector_pin", None, "3x100", [Placement(origin=(0, 0, 0)), Placement(origin=(10, 0, 0), z=(0, 1, 0))]
    )
    assert [p.role for p in parts] == ["ejector_pin_1", "ejector_pin_2"]
    assert parts[1].frame.origin == (10, 0, 0) and parts[1].frame.z == pytest.approx((0, 1, 0))
    assert abs(sum(a * b for a, b in zip(parts[1].frame.x, parts[1].frame.z, strict=True))) < 1e-9


# --- the base kit -------------------------------------------------------------------------------------------------


def _kit(**place: object) -> dict[str, PartSpec]:
    return {p.role: p for p in expand("mould_base", None, "1012", [Placement(**place)])}  # type: ignore[arg-type]


def test_the_kit_has_nine_plates_and_the_standard_parts_with_distinct_roles() -> None:
    kit = _kit()
    assert len(kit) == 23
    plates = [r for r in kit if r.endswith("plate") or r.startswith("rail")]
    assert len(plates) == 9
    assert sum(r.startswith("guide_pin") for r in kit) == sum(r.startswith("guide_bush") for r in kit) == 4
    assert sum(r.startswith("return_pin") for r in kit) == 4 and {"locating_ring", "sprue_bush"} <= set(kit)


def test_the_plates_stack_without_gaps_or_overlaps_from_the_clamp_plate_to_the_other() -> None:
    kit = _kit()

    def z_range(role: str) -> tuple[float, float]:
        pad = kit[role].features[0]
        return pad.plane.origin[2], pad.plane.origin[2] + pad.depth

    top, a, b, support = (z_range(r) for r in ("top_clamp_plate", "a_plate", "b_plate", "support_plate"))
    assert (
        top[1] == pytest.approx(a[0]) and a[1] == pytest.approx(0.0) and b[0] == pytest.approx(0.0)
    )  # the parting face is z = 0
    assert b[1] == pytest.approx(support[0])
    rail, bottom = z_range("rail_left"), z_range("bottom_clamp_plate")
    assert rail[0] == pytest.approx(support[1]) and rail[1] == pytest.approx(bottom[0])
    ejector, retainer = z_range("ejector_plate"), z_range("ejector_retainer_plate")
    assert ejector[1] == pytest.approx(bottom[0]) and retainer[1] == pytest.approx(
        ejector[0]
    )  # at home, on the clamp plate
    assert retainer[0] > support[1]  # and clear of the support plate
    assert top[1] - top[0] == pytest.approx(0.875 * IN) and a[1] - a[0] == pytest.approx(
        1.875 * IN
    )  # the catalogue example XPA1012-17-17-35


def test_the_thickness_codes_of_the_configuration_change_the_plates_and_the_pins_follow() -> None:
    thick = {p.role: p for p in expand("mould_base", "XPA1012-23-27-40", None, [Placement()])}
    assert thick["a_plate"].features[0].depth == pytest.approx(2.375 * IN)
    assert thick["b_plate"].features[0].depth == pytest.approx(2.875 * IN)
    assert thick["rail_left"].features[0].depth == pytest.approx(4.0 * IN)
    assert thick["a_plate"].catalog is not None and thick["a_plate"].catalog.reference == "XPA1012-23-27-40"


def test_every_leader_pin_stands_in_a_bore_of_its_bush_and_its_pin_seat() -> None:
    kit = _kit()
    pin_bores = {(c.cx, c.cy) for f in kit["b_plate"].features[1:] if f.name == "PinBores" for c in f.circles}  # type: ignore[union-attr]
    bush_bores = {(c.cx, c.cy) for f in kit["a_plate"].features[1:] if f.name == "BushBores" for c in f.circles}  # type: ignore[union-attr]
    pins = {tuple(round(v, 6) for v in kit[f"guide_pin_{i}"].frame.origin[:2]) for i in range(1, 5)}
    bushes = {tuple(round(v, 6) for v in kit[f"guide_bush_{i}"].frame.origin[:2]) for i in range(1, 5)}
    assert (
        pins
        == bushes
        == {(round(x, 6), round(y, 6)) for x, y in pin_bores}
        == {(round(x, 6), round(y, 6)) for x, y in bush_bores}
    )
    # three at +-5.062 x +-4.000 in and the offset one at -4.875 in (page 17 of the catalogue)
    assert sorted(round(x / IN, 3) for x, _ in pin_bores) == [-5.062, -4.875, 5.062, 5.062]
    assert sorted(round(abs(y) / IN, 3) for _, y in pin_bores) == [4.0] * 4


def test_the_pins_stay_inside_their_plates_and_inside_the_base() -> None:
    kit = _kit()
    a_floor = -1.875 * IN  # the A plate's outer face
    for i in range(1, 5):
        pin = kit[f"guide_pin_{i}"]
        head_at = pin.frame.origin[2]
        shaft = pin.features[0].profile  # type: ignore[union-attr]
        length = max(z for _, z in shaft)
        tip = head_at - length  # the pin points to -z
        assert a_floor < tip < 0.0 < head_at
        bush = kit[f"guide_bush_{i}"]
        bush_bottom = bush.frame.origin[2] - max(z for _, z in bush.features[0].profile)  # type: ignore[union-attr]
        assert a_floor <= bush_bottom < tip  # the pin ends inside its bushing


def test_the_return_pins_end_flush_with_the_parting_face_and_start_in_the_retainer_plate() -> None:
    kit = _kit()
    retainer_top = (
        kit["ejector_retainer_plate"].features[0].plane.origin[2] + kit["ejector_retainer_plate"].features[0].depth
    )
    for i in range(1, 5):
        pin = kit[f"return_pin_{i}"]
        length = max(z for _, z in pin.features[0].profile)  # type: ignore[union-attr]
        assert pin.frame.origin[2] - length == pytest.approx(0.0, abs=1e-6)  # ground to end on the parting face
        assert pin.frame.origin[2] == pytest.approx(retainer_top - 0.25 * IN)  # the head seated in the retainer plate
        assert pin.catalog is not None and "ground to" in pin.catalog.note


def test_the_sprue_bushing_fills_the_clamp_plate_opening_and_ends_on_the_parting_face() -> None:
    kit = _kit()
    sprue, tcp = kit["sprue_bush"], kit["top_clamp_plate"]
    profile = sprue.features[0].profile  # type: ignore[union-attr]
    total = max(z for _, z in profile)
    assert total == pytest.approx(
        1.875 * IN + 0.875 * IN
    )  # the A plate (ground flush) and the head, as thick as the plate
    assert sprue.frame.origin == (0.0, 0.0, 0.0) and sprue.frame.z == (0.0, 0.0, -1.0)
    assert tcp.features[0].depth == pytest.approx(0.875 * IN)
    assert sprue.catalog is not None and "ground to" in sprue.catalog.note


def test_a_placement_moves_and_turns_the_whole_kit_with_one_frame() -> None:
    kit = _kit(origin=(0.0, 0.0, 30.0), z=(0.0, 1.0, 0.0))
    ring = kit["locating_ring"]
    assert ring.frame.origin[1] < 0.0 and ring.frame.origin[2] == pytest.approx(
        30.0
    )  # the platen side is -pull: here -y
    assert ring.frame.z == pytest.approx((0.0, 1.0, 0.0)) or ring.frame.z == pytest.approx((0.0, -1.0, 0.0))
    for spec in kit.values():
        assert abs(math.sqrt(sum(c * c for c in spec.frame.z)) - 1.0) < 1e-9


def test_a_base_size_the_tables_do_not_hold_is_refused_with_the_encoded_ones() -> None:
    with pytest.raises(ComponentError, match="1012"):
        expand("mould_base", None, "1212", [Placement()])
    with pytest.raises(ComponentError, match="configuration code"):
        expand("mould_base", "base-1012", None, [Placement()])
    with pytest.raises(ComponentError, match="plate codes"):
        expand("mould_base", "XPA1012-99-17-35", None, [Placement()])


def test_every_part_of_the_kit_names_its_catalogue_source_and_reference() -> None:
    for spec in _kit().values():
        assert spec.catalog is not None and spec.catalog.source.startswith("DME") and spec.catalog.reference
        assert spec.volume_mm3 is not None and spec.volume_mm3 > 0


def test_the_cut_volumes_of_every_plate_stay_inside_it() -> None:
    for role, spec in _kit().items():
        if spec.features[0].kind == "pad" and role.endswith(("plate", "left", "right")):
            pad = spec.features[0]
            box = pad.outline.w * pad.outline.h * pad.depth  # type: ignore[union-attr]
            assert 0 < spec.volume_mm3 <= box  # type: ignore[operator]
