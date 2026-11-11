"""Assemblies: an ``Assembly::AssemblyObject`` holding ``App::Part`` components linked to their part DOCUMENTS
(``components.py``), placed by pose, mated by translation (and rotation for concentric), exact interference through
``common()`` and ``distToShape()``. The component owning face ``b`` moves; ``a`` never moves.
"""

from typing import Any

import components as comps
import fc
import geometry as g
import ops_common as oc
import registry as reg
from FreeCAD import Placement, Rotation
from protocol import invalid_reference, invalid_request, mate_failed
from queries import face_descriptor

PARALLEL = 1.0 - 1e-6


def _op(params: dict[str, Any]) -> str:
    return str(params.get("operation_id", ""))


def _assembly_object(doc: reg.DocumentRec) -> Any:
    """The FreeCAD Assembly workbench's ``Assembly::AssemblyObject`` that holds the components (made with the first
    one; ``None`` when this FreeCAD cannot create it, and the components then sit loose in the document)."""
    for obj in doc.fc_doc.Objects:
        if str(obj.TypeId) == "Assembly::AssemblyObject":
            return obj
    try:
        import AssemblyApp  # noqa: F401, PLC0415 - registers the Assembly::* types
    except ImportError:
        return None
    return fc.add_object(doc.fc_doc, "Assembly::AssemblyObject", "Assembly", label="Assembly")


def insert_component(
    doc: reg.DocumentRec, documents: dict[str, reg.DocumentRec], params: dict[str, Any]
) -> dict[str, Any]:
    op = _op(params)
    part_id = reg.handle_id(params["part"], op, "part")
    part = documents.get(part_id)
    if part is None:
        raise invalid_reference(part_id, op)
    if part is doc:
        raise invalid_request("a document cannot be inserted into itself", op)
    if part.kind != "part":
        raise invalid_request(f"{part.name!r} is an {part.kind}: only a part document can be inserted", op)
    placement = g.placement_from_transform(params.get("transform") or {})
    if not comps.part_bodies(part):
        raise invalid_request(f"document {part_id} has no geometry to insert", op)
    comps.require_files(doc, part, op)

    def build(ctx: oc.OpContext) -> None:
        comp_id = doc.next_id("comp")
        container = _assembly_object(doc)
        grp = fc.add_object(doc.fc_doc, "App::Part", "Component", label=ctx.label())
        fc.set_prop(grp, "Placement", placement)
        if container is not None:
            fc.call(container, "addObject", grp)
        comp = reg.ComponentRec(comp_id, part.id, grp.Name, "", None)
        bodies = comps.populate(comp, part)
        comp.fc_links = comps.add_links(doc, grp, part, bodies, ctx.label())
        comp.fc_shape = comp.fc_links[0]
        comp.created_by = ctx.feature.id
        doc.components[comp_id] = comp
        ctx.feature.component = comp_id
        ctx.feature.fc_names.extend([grp.Name, *comp.fc_links])
        ctx.feature.fc_main = grp.Name
        ctx.adopt(grp)
        ctx.check_objects.append(grp)
        # The faces and edges of the part are the component's too, but they are counted, not listed: a vendor part has
        # tens of thousands, and describing each one (the observation of the step does) takes minutes. They answer to
        # ``faces_of`` of the step and to their own ids (``comp:1/face:12``), made when something asks.
        ctx.feature.face_count = comp.face_total
        ctx.feature.edge_count = comp.edge_total
        ctx.feature.created_bodies.extend(reg.prefixed(comp_id, b) for b in comp.body_ids)

    return oc.run_op(doc, "component", params, build)


def refuse_if_jointed(doc: reg.DocumentRec, comp: reg.ComponentRec, operation_id: str | None) -> None:
    """A component a joint carries (or that a joint hangs from) is placed by the joint: moving it by hand would leave
    the joint's axis and value describing a pose it no longer has."""
    joint = reg.joint_driving(doc, comp)
    if joint is not None:
        role = "joined by" if joint.b == comp.id else "the reference of"
        raise invalid_request(
            f"component {comp.id} is {role} joint {joint.id}: it cannot be moved by hand. Use set_joint to move "
            "the joint, or undo the joint first",
            operation_id,
        )


def place_component(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Set the pose of an instance (absolute): the ``Placement`` of its ``App::Part``. The part is untouched."""
    op = _op(params)
    comp = reg.resolve_component(doc, reg.handle_id(params["component"], op, "component"), op)
    refuse_if_jointed(doc, comp, op)
    placement = g.placement_from_transform(params.get("transform") or {})

    def build(ctx: oc.OpContext) -> None:
        grp = doc.fc_doc.getObject(comp.fc_part)
        fc.set_prop(grp, "Placement", placement)
        comp._placed = None
        ctx.feature.component = comp.id
        ctx.feature.fc_names.append(grp.Name)
        ctx.check_objects.append(grp)

    return oc.run_op(doc, "place_component", params, build)


def fix_component(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = _op(params)
    comp = reg.resolve_component(doc, reg.handle_id(params["component"], op, "component"), op)

    def build(ctx: oc.OpContext) -> None:
        comp.fixed = True
        ctx.feature.component = comp.id

    return oc.run_op(doc, "fix_component", params, build)


def _mate_faces(
    doc: reg.DocumentRec, params: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any], reg.ComponentRec]:
    op = _op(params)
    a_id = reg.handle_id(params["a"], op, "a")
    b_id = reg.handle_id(params["b"], op, "b")
    a_rec, a_comp = reg.resolve_face(doc, a_id, op)
    b_rec, b_comp = reg.resolve_face(doc, b_id, op)
    if a_comp is None or b_comp is None:
        raise mate_failed("mates require faces of two components", op)
    if a_comp is b_comp:
        raise mate_failed("mates require faces of two different components", op)
    if b_comp.fixed:
        raise mate_failed(f"component {b_comp.id} owning face b is fixed", op)
    refuse_if_jointed(doc, b_comp, op)
    return face_descriptor(doc, a_rec, a_comp), face_descriptor(doc, b_rec, b_comp), b_comp


def _translate(
    doc: reg.DocumentRec,
    kind: str,
    params: dict[str, Any],
    comp: reg.ComponentRec,
    offset: g.Vec3,
    *,
    rotation: Rotation | None = None,
    pivot: g.Vec3 | None = None,
) -> dict[str, Any]:
    """Move the component that owns face ``b``: an optional rotation about ``pivot`` (world space, a point that
    stays fixed by construction), followed by a translation -- both applied AFTER the existing placement, same
    composition order as a translation-only mate (``Placement.multiply``: the right-hand side runs first).
    """

    def build(ctx: oc.OpContext) -> None:
        grp = doc.fc_doc.getObject(comp.fc_part)
        delta = Placement(g.fcvec(offset), Rotation())
        if rotation is not None:
            assert pivot is not None
            pivot_v = g.fcvec(pivot)
            rotate_about_pivot = Placement(pivot_v - rotation.multVec(pivot_v), rotation)
            delta = delta.multiply(rotate_about_pivot)
        fc.set_prop(grp, "Placement", delta.multiply(grp.Placement))
        comp._placed = None
        ctx.feature.component = comp.id
        ctx.feature.mate = doc.next_id("mate")
        comp.mates.append(ctx.feature.mate)
        ctx.feature.fc_names.append(grp.Name)
        ctx.check_objects.append(grp)

    return oc.run_op(doc, kind, params, build)


def mate_concentric(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Concentric mate, general case (rotation for non-parallel axes).

    ``a``/``b`` axes already (anti-)parallel: unchanged from the original implementation, including the
    ``mate_failed`` when the requested ``alignment`` does not match the axes' actual sense -- no rotation is
    computed for this bucket, so the resulting placement is bit-for-bit identical to before.

    Otherwise (skew axes, any angle): rotate the component owning face ``b`` about ``b``'s axis origin so its
    axis direction maps onto ``a``'s axis direction (or its negative, for ``anti_aligned``), using FreeCAD's
    two-vector ``Rotation`` constructor -- verified (spikes, both 1.0.2 and 1.1.3) to pick a stable perpendicular
    axis for the exactly-antiparallel case instead of an undefined/zero axis. Then translate by the same
    perpendicular offset as the parallel case: once the directions are aligned, perpendicular_part is the same
    formula whether or not a rotation preceded it.
    """
    op = _op(params)
    a, b, comp = _mate_faces(doc, params)
    if a["axis"] is None or b["axis"] is None:
        raise mate_failed("mate_concentric requires two faces with an axis", op)
    da = g.unit(tuple(a["axis"]["direction"]))
    db = g.unit(tuple(b["axis"]["direction"]))
    a_origin = tuple(a["axis"]["origin"])
    b_origin = tuple(b["axis"]["origin"])
    sense = g.dot(da, db)
    wanted = 1.0 if params.get("alignment", "aligned") == "aligned" else -1.0
    if abs(sense) > PARALLEL:
        if sense * wanted < 0:
            raise mate_failed(
                f"axes are {'anti-parallel' if sense < 0 else 'parallel'} but {params.get('alignment', 'aligned')} was "
                "requested",
                op,
            )
        offset = g.perpendicular_part(g.sub(a_origin, b_origin), da)
        return _translate(doc, "mate_concentric", params, comp, offset)
    target = da if wanted > 0 else g.scale(da, -1.0)
    rotation = Rotation(g.fcvec(db), g.fcvec(target))
    offset = g.perpendicular_part(g.sub(a_origin, b_origin), da)
    return _translate(doc, "mate_concentric", params, comp, offset, rotation=rotation, pivot=b_origin)


def _planar_offset(a: dict[str, Any], b: dict[str, Any], alignment: str, distance: float, op: str) -> g.Vec3:
    if a["normal"] is None or b["normal"] is None:
        raise mate_failed("planar mates require two planar faces", op)
    na = g.unit(tuple(a["normal"]))
    nb = g.unit(tuple(b["normal"]))
    sense = g.dot(na, nb)
    wanted = 1.0 if alignment == "aligned" else -1.0
    if abs(sense - wanted) > 1e-6:
        raise mate_failed(f"face normals are not {alignment} (n_a.n_b = {sense:.3f})", op)
    gap = g.dot(g.sub(tuple(a["center"]), tuple(b["center"])), na) + distance
    return g.scale(na, gap)


def _mirrored(
    doc: reg.DocumentRec, comp: reg.ComponentRec, offset: g.Vec3, plane_point: g.Vec3, normal: g.Vec3
) -> g.Vec3:
    """Extra translation that puts the component, once moved by ``offset``, on the other side of the plane."""
    box = g.shape_bbox(reg.placed_component_shape(doc, comp))
    lo = float("inf")
    hi = float("-inf")
    for corner in g.bbox_corners(box):
        s = g.dot(g.sub(g.add(corner, offset), plane_point), normal)
        lo, hi = min(lo, s), max(hi, s)
    return g.scale(normal, -(lo + hi))


def mate_coincident(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = _op(params)
    a, b, comp = _mate_faces(doc, params)
    offset = _planar_offset(a, b, str(params.get("alignment", "anti_aligned")), 0.0, op)
    if bool(params.get("flip", False)):
        offset = g.add(offset, _mirrored(doc, comp, offset, tuple(a["center"]), g.unit(tuple(a["normal"]))))
    return _translate(doc, "mate_coincident", params, comp, offset)


def mate_distance(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = _op(params)
    a, b, comp = _mate_faces(doc, params)
    side = 1.0 if params.get("side", "positive") == "positive" else -1.0
    if bool(params.get("flip", False)):
        side = -side
    distance = float(params.get("distance_mm", 0.0))
    if distance < 0:
        raise invalid_request("distance_mm must be >= 0", op)
    offset = _planar_offset(a, b, str(params.get("alignment", "anti_aligned")), side * distance, op)
    return _translate(doc, "mate_distance", params, comp, offset)


OVERLAP_TOL_MM3 = 1e-6  # a common volume below this is contact, not overlap
CONTACT_TOL_MM = 1e-6


def check_interference(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """Every pair of components: the volume they share (``common``) and their minimum distance (``distToShape``). A
    value OCC cannot compute stays ``None`` with a note: a pair that was not measured is never reported as clear."""
    op = _op(params)
    if params.get("components") is None:
        ids = list(doc.components)
    else:
        ids = [reg.resolve_component(doc, reg.handle_id(c, op, "components"), op).id for c in params["components"]]
    shapes = {cid: reg.placed_component_shape(doc, doc.components[cid]) for cid in ids}
    pairs = []
    total = 0.0
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            volume, distance, note = measure_pair(shapes[a], shapes[b])
            pairs.append({"a": {"id": a}, "b": {"id": b}, "volume_mm3": volume, "distance_mm": distance, "note": note})
            total += volume
    return {"pairs": pairs, "total_volume_mm3": total}


def measure_pair(sa: Any, sb: Any) -> tuple[float, float | None, str]:
    """Common volume and minimum distance of two placed shapes; a number OCC cannot give stays ``None`` (distance)
    with the reason in the note."""
    volume = 0.0
    distance: float | None = None
    notes: list[str] = []
    if sa.isNull() or sb.isNull():
        notes.append("a component has no shape")
    else:
        if not sa.Solids or not sb.Solids:  # a surface component has no volume to share: the distance only
            notes.append("a surface body has no volume: distance only")
        else:
            try:
                volume = float(sa.common(sb).Volume) if sa.BoundBox.intersect(sb.BoundBox) else 0.0
                volume = 0.0 if volume < OVERLAP_TOL_MM3 else volume
            except Exception as exc:  # noqa: BLE001 - no number is better than a wrong one
                notes.append(f"common volume not computed ({type(exc).__name__}: {exc})")
        if volume > 0.0:
            distance = 0.0
        else:
            try:
                distance = float(sa.distToShape(sb)[0])
                distance = 0.0 if distance < CONTACT_TOL_MM else distance
            except Exception as exc:  # noqa: BLE001
                notes.append(f"distance not computed ({type(exc).__name__}: {exc})")
    return volume, distance, "; ".join(notes)


def component_bounding_box(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = _op(params)
    comp = reg.resolve_component(doc, reg.handle_id(params["component"], op, "component"), op)
    # The box the steps compare before and after (a mate moves it, an insert is expected to put the part's box there).
    # A component of what a foreign file brought in answers the quick box -- the exact one is a pass over every curved
    # surface of hundreds of bodies, and ``inspect measure`` gives it -- and says so; any other component, the exact
    # one.
    box, left_out = reg.component_box(doc, comp, exact=not comp.foreign)
    out = dict(box)
    if comp.foreign:
        out["exact"] = False
    if left_out:
        out["left_out"] = [f"{comp.id}: {left_out} face{'s' if left_out != 1 else ''}"]
    return out


def component_transform(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    op = _op(params)
    comp = reg.resolve_component(doc, reg.handle_id(params["component"], op, "component"), op)
    return g.transform_from_placement(reg.component_placement(doc, comp))
