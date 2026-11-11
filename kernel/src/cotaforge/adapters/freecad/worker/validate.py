"""``validate_api`` and ``canary`` : the API surface the worker relies on, checked in a scratch
document, and the four health checks.
"""

import time
from typing import Any

import FreeCAD as App
import Part

REQUIRED_TYPES: dict[str, tuple[list[str], dict[str, list[str]]]] = {
    "PartDesign::Body": (["Tip", "Group", "BaseFeature", "Placement"], {}),
    "Sketcher::SketchObject": (["MapMode", "Placement", "Geometry", "Constraints", "FullyConstrained"], {}),
    "PartDesign::Pad": (
        ["Profile", "Type", "Length", "Length2", "Reversed", "Midplane"],
        {"Type": ["Length"]},
    ),
    "PartDesign::Pocket": (
        ["Profile", "Type", "Length", "Length2", "Reversed", "Midplane"],
        {"Type": ["Length", "ThroughAll"]},
    ),
    "PartDesign::Revolution": (["Profile", "ReferenceAxis", "Angle", "Reversed"], {}),
    "PartDesign::Hole": (
        [
            "Profile",
            "Diameter",
            "Depth",
            "DepthType",
            "DrillPoint",
            "Threaded",
            "ThreadType",
            "HoleCutType",
            "HoleCutCustomValues",
            "HoleCutDiameter",
            "HoleCutDepth",
            "HoleCutCountersinkAngle",
            "DrillPointAngle",
            "Reversed",
        ],
        {
            "DepthType": ["Dimension", "ThroughAll"],
            "DrillPoint": ["Flat", "Angled"],
            "ThreadType": ["None"],
            "HoleCutType": ["None", "Counterbore", "Countersink"],
        },
    ),
    "PartDesign::LinearPattern": (
        ["Originals", "Direction", "Length", "Occurrences", "Mode", "Reversed"],
        {},
    ),
    "PartDesign::PolarPattern": (
        ["Originals", "Axis", "Angle", "Occurrences", "Mode", "Offset"],
        {},
    ),
    "PartDesign::MultiTransform": (["Originals", "Transformations"], {}),
    "PartDesign::Chamfer": (
        ["Base", "ChamferType", "Size", "Size2", "FlipDirection"],
        {"ChamferType": ["Equal distance", "Two distances"]},
    ),
    "PartDesign::Fillet": (["Base", "Radius"], {}),
    "PartDesign::Line": (["Placement", "MapMode"], {}),
    "Part::Box": (["Length", "Width", "Height", "Placement"], {}),
    "Part::Cylinder": (["Radius", "Height", "Placement"], {}),
    "Part::Cut": (["Base", "Tool"], {}),
    "Part::MultiFuse": (["Shapes"], {}),
    "Part::MultiCommon": (["Shapes"], {}),
    "Part::Feature": (["Shape", "Placement"], {}),
    "App::Part": (["Placement", "Group"], {}),
}

SKETCH_METHODS = ["addGeometry", "addConstraint", "renameConstraint", "setDriving", "solve"]
FACE_METHODS = ["isPartner", "normalAt", "isInside"]
EDGE_METHODS = ["isSeam", "tangentAt", "valueAt"]
SHAPE_METHODS = ["ancestorsOfType", "check", "common", "isPartner"]
# ``clearUndos``: ``undo_last`` empties the undo stack after a deletion-based revert (a hard dependency since it landed)
DOC_METHODS = ["openTransaction", "commitTransaction", "abortTransaction", "undo", "clearUndos"]

# Two ways FreeCAD names LinearPattern/PolarPattern.Mode: 1.0's ``length``/``angle``/``offset``, or 1.1's
# ``Extent``/``Spacing`` (``fc.set_pattern_mode`` picks whichever the running version has). Detected at
# runtime via ``getEnumerationsOfProperty`` -- never a version-string branch.
_PATTERN_MODE_SETS = ({"length", "angle", "offset"}, {"Extent", "Spacing"})


def _check_two_sided_support(type_name: str, obj: Any, available: set[str], problems: list[str]) -> None:
    """Pad/Pocket two-length extrude: 1.0 exposes it as ``Type='TwoLengths'``; 1.1 hides that enum value
    behind a ``?`` prefix (unusable) and requires the new ``SideType`` property
    instead. Either is acceptable; neither means the canary must fail before the worker ever tries."""
    if "SideType" in available:
        return
    try:
        have = set(obj.getEnumerationsOfProperty("Type")) if "Type" in available else set()
    except Exception as exc:  # noqa: BLE001
        problems.append(f"{type_name}.Type: getEnumerationsOfProperty failed ({exc})")
        return
    if "TwoLengths" not in have:
        problems.append(f"{type_name}: no two-sided extrude support (neither SideType nor Type=TwoLengths)")


def _check_pattern_mode_support(type_name: str, obj: Any, available: set[str], problems: list[str]) -> None:
    """LinearPattern/PolarPattern.Mode enum: 1.0 uses ``length``/``angle``/``offset``, 1.1 renamed them to
    ``Extent``/``Spacing``. Accept either family; reject an unrecognised one."""
    if "Mode" not in available:
        return
    try:
        have = set(obj.getEnumerationsOfProperty("Mode"))
    except Exception as exc:  # noqa: BLE001
        problems.append(f"{type_name}.Mode: getEnumerationsOfProperty failed ({exc})")
        return
    if not any(have & wanted for wanted in _PATTERN_MODE_SETS):
        problems.append(f"{type_name}.Mode: unrecognised enumeration {sorted(have)!r}")


def validate_api(params: dict[str, Any]) -> dict[str, Any]:
    problems: list[str] = []
    version = App.Version()
    if tuple(version[:2]) not in (("1", "0"), ("1", "1")):
        problems.append(f"untested FreeCAD version {version[0]}.{version[1]}")
    doc = App.newDocument("__cadh_validate__")
    try:
        body = None
        for type_name, (props, enums) in REQUIRED_TYPES.items():
            try:
                obj = doc.addObject(type_name, "probe")
            except Exception as exc:  # noqa: BLE001
                problems.append(f"{type_name}: addObject failed ({exc})")
                continue
            if type_name == "PartDesign::Body":
                body = obj
            available = set(obj.PropertiesList)
            for p in props:
                if p not in available:
                    problems.append(f"{type_name}: missing property {p}")
            for prop, values in enums.items():
                if prop not in available:
                    continue
                try:
                    have = set(obj.getEnumerationsOfProperty(prop))
                except Exception as exc:  # noqa: BLE001
                    problems.append(f"{type_name}.{prop}: getEnumerationsOfProperty failed ({exc})")
                    continue
                for v in values:
                    if v not in have:
                        problems.append(f"{type_name}.{prop}: missing enum value {v!r}")
            if type_name == "Sketcher::SketchObject":
                for m in SKETCH_METHODS:
                    if not callable(getattr(obj, m, None)):
                        problems.append(f"Sketcher::SketchObject: missing method {m}")
            if type_name in ("PartDesign::Pad", "PartDesign::Pocket"):
                _check_two_sided_support(type_name, obj, available, problems)
            if type_name in ("PartDesign::LinearPattern", "PartDesign::PolarPattern"):
                _check_pattern_mode_support(type_name, obj, available, problems)
        _ = body
        if "UndoMode" not in doc.PropertiesList and not hasattr(doc, "UndoMode"):
            problems.append("Document: missing UndoMode")
        for m in DOC_METHODS:
            if not callable(getattr(doc, m, None)):
                problems.append(f"Document: missing method {m}")
        box = Part.makeBox(1, 1, 1)
        face = box.Faces[0]
        for m in FACE_METHODS:
            if not callable(getattr(face, m, None)):
                problems.append(f"Part.Face: missing method {m}")
        edge = box.Edges[0]
        for m in EDGE_METHODS:
            if not callable(getattr(edge, m, None)):
                problems.append(f"Part.Edge: missing method {m}")
        for m in SHAPE_METHODS:
            if not callable(getattr(box, m, None)):
                problems.append(f"Part.Shape: missing method {m}")
        if not callable(getattr(Part, "getShape", None)):
            problems.append("Part: missing getShape")
    finally:
        App.closeDocument(doc.Name)
    return {"ok": not problems, "problems": problems}


def canary(params: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    checks = {"plane": False, "box": False, "normal": False, "undo": False}
    doc = App.newDocument("__cadh_canary__")
    try:
        doc.UndoMode = 1
        try:
            plane = doc.addObject("Part::Plane", "CanaryPlane")
            doc.recompute()
            doc.removeObject(plane.Name)
            checks["plane"] = True
        except Exception:  # noqa: BLE001
            checks["plane"] = False
        try:
            box = doc.addObject("Part::Box", "CanaryBox")
            box.Length = box.Width = box.Height = 1.0
            doc.recompute()
            checks["box"] = abs(box.Shape.Volume - 1.0) < 1e-9
            top = max(box.Shape.Faces, key=lambda f: f.CenterOfMass.z)
            n = top.normalAt(0, 0)
            checks["normal"] = abs(n.x) < 1e-9 and abs(n.y) < 1e-9 and abs(n.z - 1.0) < 1e-9
        except Exception:  # noqa: BLE001
            pass
        try:
            count = len(doc.Objects)
            doc.openTransaction("canary")
            doc.addObject("Part::Box", "CanaryUndo")
            doc.recompute()
            doc.commitTransaction()
            doc.undo()
            doc.recompute()
            checks["undo"] = len(doc.Objects) == count
        except Exception:  # noqa: BLE001
            checks["undo"] = False
    finally:
        App.closeDocument(doc.Name)
    return {
        "healthy": all(checks.values()),
        "checks": checks,
        "duration_ms": (time.perf_counter() - started) * 1000.0,
    }
