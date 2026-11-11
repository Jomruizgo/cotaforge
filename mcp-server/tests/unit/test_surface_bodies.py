"""A body that is a surface: inspect, the BOM and the tree say so, state its area and never report a volume or a mass.
The CAD is the fake with some bodies declared surfaces (an adapter that says nothing about surfaces yields solids)."""

from typing import Any

from cotaforge.contract import BodiesResult, FeatureList

from cotaforge_mcp.reply import area_text
from tests.conftest import Caller, ExportFake, MakeSession, plate
from tests.unit.test_feature_edit import FeatureFake

AREA = 250.0


class SurfaceFake(ExportFake):
    """The fake whose bodies of the chosen steps read as surfaces: no volume, an area."""

    def __init__(self) -> None:
        super().__init__()
        self.surface_ids: set[str] = set()

    def bodies_of(self, request: Any) -> BodiesResult:
        result = super().bodies_of(request)
        bodies = tuple(
            b.model_copy(update={"kind": "surface", "volume_mm3": None, "area_mm2": AREA})
            if b.handle.id in self.surface_ids
            else b
            for b in result.bodies
        )
        return result.model_copy(update={"bodies": bodies})


class SurfaceFeatureFake(FeatureFake):
    def list_features(self, request: Any) -> FeatureList:
        listing = super().list_features(request)
        bodies = tuple(b.model_copy(update={"kind": "surface", "area_mm2": AREA}) for b in listing.bodies)
        return listing.model_copy(update={"bodies": bodies})


def _with_surface(make: MakeSession) -> tuple[Caller, str]:
    adapter = SurfaceFake()
    call = Caller(make(adapter))
    plate(call)
    call.ok("doc", action="set_material", material="Acero estructural")
    call.ok("op", name="box", id="skin", params={"size": [10, 10, 10]})
    skin = next(i for i, ref in call.session.named_bodies().items() if ref["from_op"] == "skin")
    adapter.surface_ids.add(skin)
    return call, skin


def test_the_bom_leaves_a_surface_out_of_the_parts_and_the_mass_and_says_so(make: MakeSession) -> None:
    call, _ = _with_surface(make)
    bom = call.ok("inspect", action="bom")
    assert "bom: 1 line(s), 1 part(s)" in bom
    assert "left out (surface: no volume, no mass):" in bom and "area 250 mm2" in bom
    assert f"total mass: {100 * 60 * 8 * 7850e-9:.4f} kg" in bom  # the plate alone


def test_a_bom_of_surfaces_only_is_not_an_empty_document(make: MakeSession) -> None:
    adapter = SurfaceFake()
    call = Caller(make(adapter))
    call.ok("doc", action="new", name="skins")
    call.ok("op", name="box", id="skin", params={"size": [10, 10, 10]})
    adapter.surface_ids.update(call.session.named_bodies())
    bom = call.ok("inspect", action="bom")
    assert "no solid body yet" in bom and "left out (surface" in bom and "0 mm3" not in bom


def test_measure_says_surface_and_its_area_not_a_zero_volume(make: MakeSession) -> None:
    call, skin = _with_surface(make)
    lines = [row for row in call.ok("inspect", action="measure").splitlines() if row.startswith(skin)]
    assert len(lines) == 1 and "surface, area 250 mm2" in lines[0] and "volume" not in lines[0]


def test_properties_of_a_surface_body_has_an_area_and_no_volume_or_mass(make: MakeSession) -> None:
    call, skin = _with_surface(make)
    text = call.ok("inspect", action="properties", body=skin)
    assert "surface body, area 250 mm2" in text
    assert "volume: none" in text and "mass: none" in text and " kg" not in text


def test_a_solid_is_reported_as_before(make: MakeSession) -> None:
    call, _ = _with_surface(make)
    text = call.ok("inspect", action="measure")
    assert "volume 48000 mm3" in text and text.count("surface, area") == 1


def test_the_tree_names_a_surface_body_and_its_area(make: MakeSession) -> None:
    call = Caller(make(SurfaceFeatureFake()))
    plate(call)
    assert "body Body (body:1), surface, area 250 mm2: tip" in call.ok("inspect", action="tree")


def test_a_surface_without_an_area_says_it_was_not_measured() -> None:
    assert area_text(None) == "area not measured"
    assert area_text(12.5) == "area 12.5 mm2"
