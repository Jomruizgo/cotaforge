"""Sheet-metal ops over the external SheetMetal add-on: ``base_flange``, ``edge_flange`` and ``unfold``.

The workbench is NOT bundled with FreeCAD (https://github.com/shaise/FreeCAD_SheetMetal, installed through the Addon
Manager into the user's ``Mod`` folder). ``status()`` says whether it can be imported and which version it is; the
worker reports that once in its hello, the client then offers the three ops only when the add-on is there, and a call
that arrives anyway fails with how to install it instead of a bare ImportError.

The add-on's own modules (``SheetMetalBaseCmd``, ``SheetMetalCmd``, ``SheetMetalUnfoldCmd``) are imported here, inside
the worker, and used the way its GUI commands do: a ``Part::FeaturePython`` object with the add-on's proxy class
(``SMBaseBend``, ``SMBendWall``, ``SMUnfold``). Nothing else of the add-on is run. The sheet is one ``part`` body whose
``result_obj`` moves to the newest object of the chain (base -> flange -> flange ...), like a boolean's.
"""

import importlib
import importlib.util
import os
import re
import sys
from pathlib import Path
from typing import Any

import fc
import geometry as g
import ops_common as oc
import registry as reg
from protocol import geometry_failed, invalid_request, not_supported, unrecoverable
from queries import flat_pattern_face, flat_pattern_outline
from recorder import literal

MODULES = ("SheetMetalTools", "SheetMetalBaseCmd", "SheetMetalCmd", "SheetMetalUnfoldCmd")
INSTALL_HINT = (
    "Install it in FreeCAD: Tools > Addon manager > search 'SheetMetal' > Install, then restart FreeCAD "
    "(and this server)."
)
# same name as ``rpc.ENV_USER_MODS`` (the worker imports nothing of the client)
ENV_USER_MODS = "COTAFORGE_WORKER_USER_MODS"
OBJECT_TYPE = "Part::FeaturePython"
BEND_TYPES = {"material_inside": "Material Inside", "material_outside": "Material Outside"}
LENGTH_SPECS = {"outer_sharp": "Outer Sharp", "leg": "Leg"}

_status: dict[str, Any] | None = None
_loaded: dict[str, Any] = {}


def _op(params: dict[str, Any]) -> str:
    return str(params.get("operation_id", ""))


# --- detection ---------------------------------------------------------------------------------------------------


def _version_next_to(origin: str | None) -> str | None:
    """The ``<version>`` of the add-on's package.xml (plain text match: the file is not trusted data to parse)."""
    if not origin:
        return None
    try:
        text = (Path(origin).parent / "package.xml").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    match = re.search(r"<version>\s*([^<\s]+)\s*</version>", text)
    return match.group(1) if match else None


def _expose_user_mods() -> list[str]:
    """Put the add-ons of the engineer's own ``Mod`` folders on ``sys.path`` and return the folders searched.

    The visible worker runs with a private ``FREECAD_USER_HOME`` that hides the engineer's Addon Manager installs; the
    client hands their real ``Mod`` folders over in ``COTAFORGE_WORKER_USER_MODS``. Like FreeCAD's own start-up, an
    add-on is a sub-folder with an ``Init.py``/``InitGui.py``; it is only added to the END of ``sys.path`` (nothing
    in it is run here, and the folders are only read). Without the variable (headless: the real profile is
    inherited) nothing happens."""
    searched = [p for p in os.environ.get(ENV_USER_MODS, "").split(os.pathsep) if p.strip()]
    for mod in searched:
        try:
            children = sorted(Path(mod).iterdir())
        except OSError:
            continue  # the folder does not exist (or cannot be read): it is still named in the report
        for child in children:
            if child.name.startswith(".") or not child.is_dir():
                continue
            if (child / "Init.py").is_file() or (child / "InitGui.py").is_file():
                path = str(child)
                if path not in sys.path:
                    sys.path.append(path)
    importlib.invalidate_caches()
    return searched


def _detect() -> dict[str, Any]:
    searched = _expose_user_mods()
    try:
        spec = importlib.util.find_spec("SheetMetalCmd")
    except (ImportError, ValueError):
        spec = None
    if spec is None:
        reason = "the SheetMetal add-on is not installed"
        if searched:
            reason += " (searched the user Mod folders: " + ", ".join(searched) + ")"
        return {
            "available": False,
            "version": None,
            "reason": reason,
            "install": INSTALL_HINT,
            "searched": searched,
        }
    version = _version_next_to(spec.origin)
    try:
        for name in MODULES:
            _loaded[name] = importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001 - any failure of the add-on's own import means it cannot be used
        _loaded.clear()
        reason = f"the SheetMetal add-on is installed but cannot be loaded ({type(exc).__name__}: {exc})"
        return {"available": False, "version": version, "reason": reason, "install": INSTALL_HINT}
    return {"available": True, "version": version, "reason": "", "install": INSTALL_HINT}


def status() -> dict[str, Any]:
    """``{"available", "version", "reason"}``, detected once per worker."""
    global _status  # noqa: PLW0603
    if _status is None:
        _status = _detect()
    return dict(_status)


def _module(name: str, op: str) -> Any:
    current = status()
    if not current["available"]:
        raise not_supported(f"{current['reason']}. {INSTALL_HINT}", op)
    return _loaded[name]


# --- helpers -----------------------------------------------------------------------------------------------------


def _addon_object(ctx: oc.OpContext, module: Any, cls: str, name: str, label: str | None, *args: Any) -> Any:
    """A ``Part::FeaturePython`` carrying one of the add-on's proxies, the way its commands make it. The macro line is
    recorded too (the recorded script imports the add-on, so replaying it needs it installed)."""
    obj = fc.add_object(ctx.doc.fc_doc, OBJECT_TYPE, name, label=label)
    getattr(module, cls)(obj, *args)
    fc.recorder.record(f"import {module.__name__}")
    fc.recorder.record(f"{module.__name__}.{cls}({literal(obj)}, " + ", ".join(literal(a) for a in args) + ")")
    ctx.feature.fc_names.append(obj.Name)
    ctx.adopt(obj)
    ctx.check_objects.append(obj)
    return obj


def _hide(obj: Any) -> None:
    try:
        fc.set_prop(obj, "Visibility", False)
    except Exception:  # noqa: BLE001 - cosmetic only
        pass


def _sheet_body(doc: reg.DocumentRec, body_id: str, what: str, op: str) -> reg.BodyRec:
    body = reg.resolve_body(doc, body_id, op)
    if not body.sheet_metal:
        raise invalid_request(
            f"{what} works on a sheet-metal body (made by base_flange, bent by edge_flange); {body.id} is not one", op
        )
    return body


def _volume(shape: Any) -> float:
    return 0.0 if shape.isNull() else float(shape.Volume)


def _single_valid_solid(shape: Any, what: str, op: str) -> None:
    if shape.isNull() or not shape.isValid():
        raise unrecoverable(geometry_failed(f"{what} produced an invalid shape", op))
    if len(shape.Solids) != 1:
        raise unrecoverable(geometry_failed(f"{what} produced {len(shape.Solids)} solids instead of one", op))


# --- base_flange -------------------------------------------------------------------------------------------------


def base_flange(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """The closed profiles of a sketch as a flat sheet: ``SheetMetalBaseCmd.SMBaseBend`` (the closed wires become one
    face, inner wires being holes; it is extruded by the thickness along the normal of that face, and the side is
    checked and flipped when needed)."""
    op = _op(params)
    module = _module("SheetMetalBaseCmd", op)
    sk = reg.resolve_sketch(doc, reg.handle_id(params["sketch"], op, "sketch"), op)
    thickness = float(params["thickness_mm"])
    if thickness <= 0:
        raise invalid_request("base_flange thickness_mm must be positive", op)
    wanted = -1.0 if params.get("direction", "positive") == "negative" else 1.0
    normal = g.vec(sk.frame["n"])
    origin = g.vec(sk.frame["origin"])

    def build(ctx: oc.OpContext) -> None:
        sketch_obj = oc.materialise_sketch(doc, None, sk, "Sketch", label=ctx.label("sketch"))
        ctx.feature.fc_names.append(sketch_obj.Name)
        ctx.feature.sketch = sk.id
        ctx.feature.named_dimensions = dict(sk.named_dimensions)
        ctx.check_objects.append(sketch_obj)
        obj = _addon_object(ctx, module, "SMBaseBend", "SheetBase", ctx.label(), sketch_obj)
        fc.set_prop(obj, "Thickness", thickness)
        fc.set_prop(obj, "Radius", thickness)  # only an open profile bends: unused for a closed outline
        fc.set_prop(obj, "MidPlane", False)
        fc.set_prop(obj, "Reverse", wanted < 0)
        fc.recompute(doc.fc_doc)
        # the add-on extrudes along the normal of the face it makes of the wire, which follows the wire's winding:
        # the side is read from the result and flipped once when it is the other one
        centre = g.vec(obj.Shape.CenterOfMass) if not obj.Shape.isNull() else origin
        if g.dot(g.sub(centre, origin), normal) * wanted < 0:
            fc.set_prop(obj, "Reverse", not obj.Reverse)
            fc.recompute(doc.fc_doc)
        _hide(sketch_obj)
        body = reg.BodyRec(doc.next_id("body"), "part", None, obj.Name, ctx.feature.id)
        body.sheet_metal = True
        doc.bodies[body.id] = body
        ctx.feature.created_bodies.append(body.id)
        ctx.feature.fc_main = obj.Name
        ctx.bodies.append(body)
        ctx.expect_volume = "increase"

        def post_check() -> None:
            body.invalidate()
            _single_valid_solid(reg.topo_shape(doc, body), "base_flange", op)

        ctx.post_check = post_check

    return oc.run_op(doc, "base_flange", params, build)


# --- edge_flange -------------------------------------------------------------------------------------------------


def edge_flange(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """A wall bent out of one straight edge: ``SheetMetalCmd.SMBendWall`` linked to the edge of the sheet's newest
    object (the add-on's own selection: the thickness-side edge of a big face; the wall rises on that face's side)."""
    op = _op(params)
    module = _module("SheetMetalCmd", op)
    rec, comp = reg.resolve_edge(doc, reg.handle_id(params["edge"], op, "edge"), op)
    if comp is not None:
        raise not_supported("edge_flange on a component edge is not supported", op)
    body = _sheet_body(doc, rec.body, "edge_flange", op)
    length = float(params["length_mm"])
    angle = float(params.get("angle_deg", 90.0))
    radius = float(params["bend_radius_mm"])
    position = str(params.get("position", "material_inside"))
    length_from = str(params.get("length_from", "outer_sharp"))
    if length <= 0 or radius <= 0 or not 0.0 < angle < 180.0:
        raise invalid_request("edge_flange needs length_mm > 0, bend_radius_mm > 0 and 0 < angle_deg < 180", op)
    if position not in BEND_TYPES or length_from not in LENGTH_SPECS:
        raise invalid_request(
            f"edge_flange position must be one of {sorted(BEND_TYPES)} and length_from one of {sorted(LENGTH_SPECS)}",
            op,
        )
    base_obj = doc.fc_doc.getObject(body.result_obj)
    volume_before = _volume(reg.topo_shape(doc, body))

    def build(ctx: oc.OpContext) -> None:
        obj = _addon_object(ctx, module, "SMBendWall", "SheetFlange", ctx.label(), base_obj, [rec.sub_name])
        fc.set_prop(obj, "angle", angle)
        fc.set_prop(obj, "radius", radius)
        fc.set_prop(obj, "length", length)
        fc.set_prop(obj, "BendType", BEND_TYPES[position])
        fc.set_prop(obj, "LengthSpec", LENGTH_SPECS[length_from])
        fc.set_prop(obj, "AutoMiter", True)
        _hide(base_obj)
        ctx.feature.fc_main = obj.Name
        body.result_obj = obj.Name
        body.invalidate()
        ctx.bodies.append(body)

        def post_check() -> None:
            body.invalidate()
            shape = reg.topo_shape(doc, body)
            _single_valid_solid(shape, "edge_flange", op)
            if abs(_volume(shape) - volume_before) <= 1e-6:
                raise unrecoverable(geometry_failed("edge_flange changed nothing (the wall has no size)", op))

        ctx.post_check = post_check

    return oc.run_op(doc, "edge_flange", params, build)


# --- unfold ------------------------------------------------------------------------------------------------------


def flat_facts(shape: Any, op: str) -> dict[str, Any]:
    """Size, thickness, blank area and volume of a flat pattern, read from its own shape."""
    face = flat_pattern_face(shape, op)
    outline = flat_pattern_outline(shape, op)
    box = outline.BoundBox
    volume = _volume(shape)
    area = float(face.Area)
    return {
        "size_mm": sorted((float(box.XLength), float(box.YLength)), reverse=True),
        "thickness_mm": volume / area if area > 0 else 0.0,
        "area_mm2": area,
        "volume_mm3": volume,
        "valid": bool(shape.isValid()) and len(shape.Solids) == 1,
    }


def unfold(doc: reg.DocumentRec, params: dict[str, Any]) -> dict[str, Any]:
    """The flat pattern: ``SheetMetalUnfoldCmd.SMUnfold`` on a planar face of the sheet, with a manual ANSI K-factor.
    The unfolded solid is a document object the registry does NOT count as a body, so mass, bounding box and validity
    keep describing the folded part."""
    op = _op(params)
    module = _module("SheetMetalUnfoldCmd", op)
    rec, comp = reg.resolve_face(doc, reg.handle_id(params["face"], op, "face"), op)
    if comp is not None:
        raise not_supported("unfold on a component face is not supported", op)
    body = _sheet_body(doc, rec.body, "unfold", op)
    if rec.sig.surface != "plane":
        raise invalid_request(f"unfold keeps one flat face fixed; {rec.id} is a {rec.sig.surface} face", op)
    k_factor = float(params["k_factor"])
    if not 0.0 < k_factor < 1.0:
        raise invalid_request("unfold k_factor must be between 0 and 1 (ANSI convention)", op)
    base_obj = doc.fc_doc.getObject(body.result_obj)

    def build(ctx: oc.OpContext) -> None:
        obj = _addon_object(ctx, module, "SMUnfold", "SheetUnfold", ctx.label(), base_obj, [rec.sub_name])
        fc.set_prop(obj, "KFactorStandard", "ansi")
        fc.set_prop(obj, "KFactor", k_factor)
        fc.set_prop(obj, "MaterialSheet", "_manual")
        fc.set_prop(obj, "GenerateSketch", False)
        fc.set_prop(obj, "GenerateBendCuts", False)
        fc.set_prop(obj, "ManualRecompute", False)
        ctx.feature.fc_main = obj.Name

        def post_check() -> None:
            shape = obj.Shape
            if shape.isNull() or not shape.isValid() or len(shape.Solids) != 1:
                raise unrecoverable(geometry_failed("unfold produced no valid flat solid", op))
            facts = flat_facts(shape, op)
            if facts["thickness_mm"] <= 0:
                raise unrecoverable(geometry_failed("unfold produced a flat pattern without thickness", op))

        ctx.post_check = post_check

    result = oc.run_op(doc, "unfold", params, build)
    feature = doc.feature(result["feature"]["id"], op)
    obj = doc.fc_doc.getObject(feature.fc_main)
    result["flat_pattern"] = flat_facts(obj.Shape, op)
    return result
