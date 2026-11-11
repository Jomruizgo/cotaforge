"""``geometry.shape_bbox`` / ``exact_box``: boxes come from the exact B-rep, never from a display mesh.

``Shape.BoundBox`` uses the triangulation when the shape carries one; only the GUI tessellates, so on the
visible worker a vertical D40 rod's box shrank to (-19.941, -19.985)..(20, 19.985). ``tessellate`` attaches
the same mesh headless, which makes the failure reproducible here without a display."""

import geometry as g
import Part
from FreeCAD import Vector

TOL = 1e-6


def _rod() -> Part.Shape:
    return Part.makeCylinder(20.0, 100.0)  # standing on XY, axis +Z


def _assert_box(box: dict, lo: tuple, hi: tuple) -> None:
    assert all(abs(a - b) <= TOL for a, b in zip(box["min"], lo, strict=True)), box
    assert all(abs(a - b) <= TOL for a, b in zip(box["max"], hi, strict=True)), box


def check_a_tessellated_rod_has_the_exact_box() -> None:
    rod = _rod()
    rod.tessellate(0.1)  # what the GUI does to draw the object
    assert rod.BoundBox.YMin > -20.0 + 1e-3, "premise: the mesh-based BoundBox is smaller than the true box"
    _assert_box(g.shape_bbox(rod), (-20.0, -20.0, 0.0), (20.0, 20.0, 100.0))


def check_a_tessellated_cylindrical_face_has_the_exact_box() -> None:
    rod = _rod()
    rod.tessellate(0.1)
    (side,) = [f for f in rod.Faces if type(f.Surface).__name__ == "Cylinder"]
    _assert_box(g.shape_bbox(side), (-20.0, -20.0, 0.0), (20.0, 20.0, 100.0))


def check_a_tessellated_circular_edge_has_the_exact_box() -> None:
    rod = _rod()
    rod.tessellate(0.1)
    top = max(rod.Edges, key=lambda e: e.BoundBox.ZMax)
    _assert_box(g.shape_bbox(top), (-20.0, -20.0, 100.0), (20.0, 20.0, 100.0))


def check_a_torus_box_is_tight_not_the_loose_control_point_bound() -> None:
    torus = Part.makeTorus(30.0, 5.0)
    _assert_box(g.shape_bbox(torus), (-35.0, -35.0, -5.0), (35.0, 35.0, 5.0))


def check_a_placed_oblique_shape_keeps_its_placement() -> None:
    cone = Part.makeCone(10.0, 3.0, 40.0, Vector(1, 2, 3), Vector(1, 1, 1))
    before = g.shape_bbox(cone)
    cone.tessellate(0.05)
    after = g.shape_bbox(cone)
    assert all(
        abs(a - b) <= TOL for a, b in zip(before["min"] + before["max"], after["min"] + after["max"], strict=True)
    )


def check_a_void_shape_falls_back_to_the_zero_box() -> None:
    box = g.shape_bbox(Part.Shape())
    assert box["min"] == [0.0, 0.0, 0.0] and box["max"] == [0.0, 0.0, 0.0], box
