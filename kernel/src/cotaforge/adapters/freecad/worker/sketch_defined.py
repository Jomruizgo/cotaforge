"""The rule behind every feature: the sketch it is built on ends fully defined by dimensions.

A sketch the ``sketch`` op makes always is (its constraint set is complete, ``ops_common.materialise_sketch`` checks
it); one made by hand or by a ``run_native`` fragment may not be. This module judges a ``Sketcher::SketchObject``:

* ``DoF > 0``: it can still move. The count is the solver's own; the geometry that is still free comes from
  ``getGeometryWithDependentParameters`` (present in FreeCAD 1.1; a build without it gives the number only).
* a ``Block`` constraint: it pins a geometry without a dimension, as a ``Fix`` does in other software of CAD, so it is
  not a definition. Whatever the solver says, a sketch holding one is not fully defined.

Pure duck typing (no FreeCAD import): the tests drive it with stand-in objects. A sketch no feature uses is free to
stay under defined (a construction sketch): only the sketches a feature is built on are judged.
"""

from dataclasses import dataclass
from typing import Any

SKETCH_TYPE = "Sketcher::SketchObject"
BODY_TYPE = "PartDesign::Body"
BINDER_TYPES = ("PartDesign::ShapeBinder", "PartDesign::SubShapeBinder")

NOT_DEFINED = "sketch_not_fully_defined"
HAS_BLOCK = "sketch_has_block"

MAX_LISTED = 6

_GEOMETRY_NAMES = {
    "Part::GeomLineSegment": "line",
    "Part::GeomCircle": "circle",
    "Part::GeomArcOfCircle": "arc",
    "Part::GeomPoint": "point",
    "Part::GeomEllipse": "ellipse",
    "Part::GeomArcOfEllipse": "elliptical arc",
    "Part::GeomBSplineCurve": "spline",
}
# PosId of a Sketcher point: 0 the edge itself, 1 its start, 2 its end, 3 its centre
_POSITIONS = {0: "", 1: " start", 2: " end", 3: " centre"}


def is_sketch(obj: Any) -> bool:
    return str(obj.TypeId) == SKETCH_TYPE


def consumes(obj: Any, _seen: frozenset[str] = frozenset()) -> bool:
    """Whether ``obj`` is a feature built on the sketches it references (a pad, a pocket, a revolution, a pipe...): a
    body, another sketch (attached to it) or a datum only refers to a sketch. So does a shape binder, which only
    reads it (FreeCAD 1.1: both binders derive from ``Part::Feature``); it counts only once a feature is built on
    the binder itself."""
    if obj.isDerivedFrom(SKETCH_TYPE) or str(obj.TypeId) == BODY_TYPE or obj.isDerivedFrom("Part::Datum"):
        return False
    if any(obj.isDerivedFrom(binder) for binder in BINDER_TYPES):
        name = str(obj.Name)
        return name not in _seen and any(consumes(user, _seen | {name}) for user in obj.InList)
    return bool(obj.isDerivedFrom("Part::Feature"))


def users(sketch: Any) -> list[Any]:
    """The features built on ``sketch``."""
    return [obj for obj in sketch.InList if consumes(obj)]


def _built_from(user: Any, _seen: frozenset[str] = frozenset()) -> list[Any]:
    """``user`` and, when it is a shape binder, the features built on the binder (the use of the sketch is theirs)."""
    if not any(user.isDerivedFrom(binder) for binder in BINDER_TYPES) or str(user.Name) in _seen:
        return [user]
    seen = _seen | {str(user.Name)}
    return [user, *(f for obj in user.InList if consumes(obj) for f in _built_from(obj, seen))]


def block_count(sketch: Any) -> int:
    """How many ``Block`` constraints the sketch holds (one that was switched off does not count)."""
    return sum(1 for c in sketch.Constraints if str(c.Type) == "Block" and getattr(c, "Active", True) is not False)


def solved_dof(sketch: Any) -> int:
    """The sketch's degrees of freedom after the solver ran on it. Solving must not leave the object marked as changed
    (a recompute of the document would then be due): what was clean stays clean."""
    clean = "Touched" not in list(getattr(sketch, "State", ()))
    sketch.solve()
    if clean and "Touched" in list(getattr(sketch, "State", ())):
        try:
            sketch.purgeTouched()
        except Exception:  # noqa: BLE001 - cosmetic: the next recompute settles it
            pass
    return int(sketch.DoF)


def free_parts(sketch: Any, limit: int = MAX_LISTED) -> list[str]:
    """What is still free, in the words of the sketch ("line 2 end", "circle 0 centre"); empty when FreeCAD cannot
    say (the call is missing in this build, or it fails). Indices are those of the sketch's geometry list."""
    try:
        pairs = sketch.getGeometryWithDependentParameters()
        geometry = list(sketch.Geometry)
    except Exception:  # noqa: BLE001 - an older or odd build: the number of degrees of freedom alone
        return []
    parts: list[str] = []
    for geo_id, pos in pairs:
        index = int(geo_id)
        if not 0 <= index < len(geometry):
            continue  # an axis or an external reference
        kind = _GEOMETRY_NAMES.get(str(getattr(geometry[index], "TypeId", "")), "geometry")
        label = f"{kind} {index}{_POSITIONS.get(int(pos), '')}"
        if label not in parts:
            parts.append(label)
    if len(parts) > limit:
        return [*parts[:limit], f"and {len(parts) - limit} more"]
    return parts


@dataclass(frozen=True)
class Verdict:
    reason: str  # NOT_DEFINED | HAS_BLOCK
    dof: int
    blocks: int
    free: tuple[str, ...]

    def text(self, what: str) -> str:
        """The refusal, ``what`` being how the sketch is called ("the sketch s1 (Sketch)", "Sketch")."""
        if self.reason == HAS_BLOCK:
            return (
                f"{what} holds {self.blocks} Block constraint(s): a Block pins geometry without a dimension and does "
                "not count as a definition; replace it with dimensions"
            )
        free = f"; still free: {', '.join(self.free)}" if self.free else ""
        return (
            f"{what} still has {self.dof} degree(s) of freedom: every sketch behind a feature ends fully defined by "
            f"dimensions{free}"
        )


def judge(sketch: Any) -> Verdict | None:
    """``None`` when the sketch is fully defined by dimensions; otherwise why not. A sketch the solver reports as
    conflicting or redundant (negative or inconsistent) is not this module's concern: those have their own checks."""
    blocks = block_count(sketch)
    if blocks:
        return Verdict(HAS_BLOCK, 0, blocks, ())
    dof = solved_dof(sketch)
    if dof > 0:
        return Verdict(NOT_DEFINED, dof, 0, tuple(free_parts(sketch)))
    return None


def _own(fingerprint: Any) -> Any:
    """The fingerprint of a sketch without what follows from elsewhere: a sketch attached to a face is moved by an
    earlier feature that changed (a pad's length), so its ``Placement`` and the shape it gives are no edit of the
    sketch. Its geometry, constraints, support and attachment offset still are."""
    if not isinstance(fingerprint, dict):
        return fingerprint
    own = {key: value for key, value in fingerprint.items() if key != "shape"}
    frames = own.get("frames")
    if isinstance(frames, dict):
        own["frames"] = {key: value for key, value in frames.items() if key != "Placement"}
    return own


def unfit_uses(fc_doc: Any, before: dict[str, Any], after: dict[str, Any]) -> dict[str, list[str]]:
    """``{sketch name: [reason]}`` for every sketch a ``run_native`` step left under defined while a feature uses it,
    when the step made the use: it built the feature, pointed a feature at the sketch, or changed the sketch itself.
    ``before`` / ``after`` are the document fingerprints around the step. A feature whose other properties the step
    changed (a pad's length) is not a new use: an opened file keeps whatever it brought, and a sketch the step did not
    touch under a feature it did not build is not blamed on it."""

    def sketch_touched(name: str) -> bool:
        return name not in before or _own(after.get(name)) != _own(before.get(name))

    def new_use(name: str) -> bool:
        if name not in before:
            return True
        return after.get(name, {}).get("links") != before[name].get("links")

    out: dict[str, list[str]] = {}
    for sketch in fc_doc.Objects:
        if not is_sketch(sketch):
            continue
        used_by = users(sketch)
        if not used_by:
            continue  # a construction sketch: free to stay under defined
        built = [feature for user in used_by for feature in _built_from(user)]
        if not sketch_touched(str(sketch.Name)) and not any(new_use(str(feature.Name)) for feature in built):
            continue
        verdict = judge(sketch)
        if verdict is not None:
            names = ", ".join(str(user.Name) for user in used_by)
            out[str(sketch.Name)] = [f"{verdict.reason}: " + verdict.text(f"the sketch under {names}")]
    return out


def notes(fc_doc: Any) -> list[str]:
    """The lines ``check`` adds about the sketches of the document: the ones a feature is built on that are not fully
    defined by dimensions (an opened file keeps them; they are refused the moment an operation uses them again).
    Nothing when every one is fine or there is none (an imported model has no sketch to judge)."""
    lines: list[str] = []
    used = 0
    for sketch in fc_doc.Objects:
        if not is_sketch(sketch):
            continue
        used_by = users(sketch)
        if not used_by:
            continue
        used += 1
        verdict = judge(sketch)
        if verdict is not None:
            lines.append(f"sketch {sketch.Name}: " + verdict.text("it"))
    if not lines:
        return []
    return [f"sketches: {used - len(lines)} of {used} behind a feature fully defined by dimensions", *lines]
