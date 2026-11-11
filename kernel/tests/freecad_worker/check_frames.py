"""Sketch frames and the rotation_from_frame mapping."""

import geometry as g
from FreeCAD import Vector
from harness import new_document, rect, sketch, vclose

TABLE = {
    "XY": ((0, 0, 0), (1, 0, 0), (0, 1, 0), (0, 0, 1)),
    "YZ": ((0, 0, 0), (0, 1, 0), (0, 0, 1), (1, 0, 0)),
    "XZ": ((0, 0, 0), (1, 0, 0), (0, 0, -1), (0, 1, 0)),
}


def check_plane_frames_match_table() -> None:
    for name, (origin, u, v, n) in TABLE.items():
        frame = g.frame_for_plane(name)
        assert vclose(frame["origin"], origin) and vclose(frame["u"], u), (name, frame)
        assert vclose(frame["v"], v) and vclose(frame["n"], n), (name, frame)


def check_rotation_from_frame_columns() -> None:
    for name, (_, u, v, n) in TABLE.items():
        rot = g.rotation_from_frame(u, v, n)
        assert vclose(rot.multVec(Vector(1, 0, 0)), u), name
        assert vclose(rot.multVec(Vector(0, 1, 0)), v), name
        assert vclose(rot.multVec(Vector(0, 0, 1)), n), name
    frame = g.frame_for_face((1, 2, 3), (1, 1, 1))
    rot = g.rotation_from_frame(tuple(frame["u"]), tuple(frame["v"]), tuple(frame["n"]))
    assert vclose(rot.multVec(Vector(0, 0, 1)), frame["n"])
    assert abs(rot.toMatrix().determinant() - 1.0) < 1e-9


def check_face_frames_for_principal_normals() -> None:
    c = (5.0, 6.0, 7.0)
    cases = {
        (0, 0, 1): ((1, 0, 0), (0, 1, 0)),
        (0, 0, -1): ((1, 0, 0), (0, -1, 0)),
        (0, 1, 0): ((1, 0, 0), (0, 0, -1)),
        (0, -1, 0): ((1, 0, 0), (0, 0, 1)),
        (1, 0, 0): ((0, 1, 0), (0, 0, 1)),  # |X.n| >= 0.999: u from Y
        (-1, 0, 0): ((0, 1, 0), (0, 0, -1)),
    }
    for n, (u, v) in cases.items():
        frame = g.frame_for_face(c, n)
        assert vclose(frame["origin"], c)
        assert vclose(frame["u"], u) and vclose(frame["v"], v) and vclose(frame["n"], n), (n, frame)


def check_sketch_on_planes_reports_frame() -> None:
    new_document("frames")
    for name, (origin, u, v, n) in TABLE.items():
        result = sketch(f"s_{name}", name, [rect("r", (0, 0), 2, 2)])
        f = result["frame"]
        assert vclose(f["origin"], origin) and vclose(f["u"], u) and vclose(f["v"], v) and vclose(f["n"], n)
        assert result["profile_areas_mm2"] == [4.0]
