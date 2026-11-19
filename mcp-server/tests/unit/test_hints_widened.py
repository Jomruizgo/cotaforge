"""The hint for a counterbore or countersink made of two steps: the geometry on synthetic faces (the hint line itself is
checked against the real CAD in the integration test)."""

from cotaforge.contract import Axis, BodyHandle, BoundingBox, FaceHandle, FaceInfo, Provenance, SurfaceType

from cotaforge_mcp import hints
from cotaforge_mcp.hints import _round_of, _widens


def _round_face(
    kind: SurfaceType, n: int, radius: float, z0: float, z1: float, at: tuple[float, float] = (0, 0)
) -> FaceInfo:
    """A cylinder (or a cone, whose widest circle is ``radius``) on a vertical axis through ``at``, from z0 to z1."""
    x, y = at
    return FaceInfo(
        handle=FaceHandle(id=f"face:{n}"),
        body=BodyHandle(id="body:1"),
        surface=kind,
        axis=Axis(origin=(x, y, (z0 + z1) / 2), direction=(0, 0, 1)),
        center=(x, y, (z0 + z1) / 2),
        radius=radius,
        area_mm2=1.0,
        bbox=BoundingBox(min=(x - radius, y - radius, z0), max=(x + radius, y + radius, z1)),
        provenance=Provenance(created_by={"id": "s1"}, modified_by=()),  # type: ignore[arg-type]
    )


def _of(face: FaceInfo) -> hints._Round:
    found = _round_of(face)
    assert found is not None
    return found


def test_a_wider_coaxial_cylinder_is_a_counterbore_of_the_hole() -> None:
    hole = _of(_round_face(SurfaceType.cylinder, 1, 4.5, 0, 20, at=(60, 35)))
    pocket = _of(_round_face(SurfaceType.cylinder, 2, 7.5, 11.5, 20, at=(60, 35)))
    assert _widens(pocket, hole)
    assert pocket.length == 8.5  # the depth the hint offers to hole


def test_a_pocket_beside_the_hole_or_as_wide_is_not() -> None:
    hole = _of(_round_face(SurfaceType.cylinder, 1, 4.5, 0, 20, at=(60, 35)))
    beside = _of(_round_face(SurfaceType.cylinder, 2, 7.5, 11.5, 20, at=(40, 35)))
    same = _of(_round_face(SurfaceType.cylinder, 3, 4.5, 11.5, 20, at=(60, 35)))
    assert not _widens(beside, hole) and not _widens(same, hole)


def test_a_wide_cone_is_a_countersink_and_an_edge_chamfer_is_not() -> None:
    hole = _of(_round_face(SurfaceType.cylinder, 1, 3.3, 0, 20))
    countersink = _of(_round_face(SurfaceType.cone, 2, 6.3, 17, 20))  # D12.6 at the entry
    chamfer = _of(_round_face(SurfaceType.cone, 3, 3.8, 19.5, 20))  # 0.5 mm break of the edge
    assert _widens(countersink, hole) and not _widens(chamfer, hole)


def test_a_cone_with_a_slanted_axis_and_a_plane_are_not_read() -> None:
    slanted = _round_face(SurfaceType.cone, 1, 6, 0, 3).model_copy(
        update={"axis": Axis(origin=(0, 0, 0), direction=(1, 1, 1))}
    )
    assert _round_of(slanted) is None
    assert _round_of(_round_face(SurfaceType.plane, 2, 6, 0, 3)) is None
