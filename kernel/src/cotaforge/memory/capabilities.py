"""Known process tags and the capabilities each one may declare.

A declared process is *not* a closed catalogue: the user may own a foundry, an EDM or a forge. A tag listed
here is a "known" tag: its capability names, shapes and units are checked at load time and the constraint
checks know what to do with them. Any other tag is a "free" tag, accepted without automatic checks (the
report says so).
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class CapabilityShape(StrEnum):
    scalar = "scalar"  # one number: ``value``
    number_set = "number_set"  # several numbers: ``value_set``
    names = "names"  # several names: ``value_names``


class CapabilitySpec(BaseModel):
    """Shape, unit and plausible range of one named capability of a known process tag."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    shape: CapabilityShape
    unit: str | None = None
    set_size: int | None = None  # exact length of a ``number_set``
    minimum: float | None = 0.0  # exclusive lower bound of every number (None: unbounded)
    maximum: float | None = None  # inclusive upper bound of every number


def _mm() -> CapabilitySpec:
    return CapabilitySpec(shape=CapabilityShape.scalar, unit="mm")


_MATERIALS = CapabilitySpec(shape=CapabilityShape.names)
_VOLUME = CapabilitySpec(shape=CapabilityShape.number_set, unit="mm", set_size=3)
_SHEETS = CapabilitySpec(shape=CapabilityShape.number_set, unit="mm")
_RATIO = CapabilitySpec(shape=CapabilityShape.scalar)
_IT_GRADE = CapabilitySpec(shape=CapabilityShape.scalar, maximum=18.0)
_K_FACTOR = CapabilitySpec(shape=CapabilityShape.scalar, maximum=1.0)
_OVERHANG = CapabilitySpec(shape=CapabilityShape.scalar, unit="deg", maximum=90.0)

PROCESS_CAPABILITIES: dict[str, dict[str, CapabilitySpec]] = {
    "turning": {
        "max_diameter_mm": _mm(),
        "max_length_mm": _mm(),
        "min_wall_mm": _mm(),
        "min_internal_radius_mm": _mm(),
        "max_hole_depth_ratio": _RATIO,
        "max_hole_diameter_mm": _mm(),
        "achievable_it_grade": _IT_GRADE,
        "materials": _MATERIALS,
    },
    "milling_3axis": {
        "build_volume_mm": _VOLUME,
        "min_wall_mm": _mm(),
        "min_internal_radius_mm": _mm(),
        "max_hole_depth_ratio": _RATIO,
        "max_hole_diameter_mm": _mm(),
        "achievable_it_grade": _IT_GRADE,
        "materials": _MATERIALS,
    },
    "laser_cut": {
        "max_length_mm": _mm(),
        "sheet_thicknesses_mm": _SHEETS,
        "min_feature_mm": _mm(),
        "kerf_mm": _mm(),
        "materials": _MATERIALS,
    },
    "sheet_bending": {
        "max_length_mm": _mm(),
        "sheet_thicknesses_mm": _SHEETS,
        "min_bend_radius_mm": _mm(),
        "min_flange_mm": _mm(),
        "k_factor": _K_FACTOR,
        "materials": _MATERIALS,
    },
    "fdm": {
        "build_volume_mm": _VOLUME,
        "min_wall_mm": _mm(),
        "min_feature_mm": _mm(),
        "min_thickness_mm": _mm(),
        "max_overhang_deg": _OVERHANG,
        "materials": _MATERIALS,
    },
    "sls": {
        "build_volume_mm": _VOLUME,
        "min_wall_mm": _mm(),
        "min_feature_mm": _mm(),
        "min_thickness_mm": _mm(),
        "materials": _MATERIALS,
    },
    "welding": {
        "max_length_mm": _mm(),
        "min_thickness_mm": _mm(),
        "materials": _MATERIALS,
    },
}


def known_process_tag(tag: str) -> str | None:
    """The canonical known tag for ``tag`` (case/space-insensitive), or None for a free tag."""
    canonical = tag.strip().lower()
    return canonical if canonical in PROCESS_CAPABILITIES else None
