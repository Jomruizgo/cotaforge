# ruff: noqa: E501
"""The record an ``external_thread`` leaves in the document, and how it is read back (no FreeCAD import).

An external thread adds no geometry: it is an object of the model tree (an ``App::FeaturePython`` with no shape) whose
properties, in the ``Cotaforge`` group, say what the thread is. They travel with the ``.FCStd`` file, so a document the
engineer reopens (or hands over) still says which shaft ends are threaded. ``ops_threads`` writes the record; the sheet
(``drawing.py`` -> ``sheet_thread``) reads it with ``read_threads`` for the ISO 6410 representation and the callout, and
the thread row of the table of requirements of the drawing finds the feature by its ``thread`` kind and its label.
"""

from typing import Any

OBJECT_TYPE = "App::FeaturePython"
GROUP = "Cotaforge"
LABEL_PREFIX = "thread"
"""The label of the object starts with it (``thread_<id>``): the same name the feature of the operation carries."""

# property name -> (FreeCAD type, what it says)
PROPERTIES: dict[str, tuple[str, str]] = {
    "ThreadSize": ("App::PropertyString", "ISO metric designation (M12, M45x1.5)"),
    "ThreadMajor": ("App::PropertyFloat", "Major (nominal) diameter, mm"),
    "ThreadPitch": ("App::PropertyFloat", "Pitch, mm"),
    "ThreadMinor": ("App::PropertyFloat", "Basic minor diameter d1 = d - 1.0825 P, mm"),
    "ThreadLength": ("App::PropertyFloat", "Thread length from the end face, mm"),
    "ThreadStart": ("App::PropertyVector", "Centre of the end face the thread starts from (on the axis), mm"),
    "ThreadDirection": ("App::PropertyVector", "Unit direction along the axis, from the end face into the shaft"),
    "ThreadCylinder": ("App::PropertyString", "The cylindrical face the thread is on (handle at the time it was made)"),
    "ThreadEndFace": ("App::PropertyString", "The end face the thread starts from (handle at the time it was made)"),
    "ThreadClass": ("App::PropertyString", "ISO 965 tolerance class (6g); empty when none was given"),
}


END_TOLERANCE_MM = 0.01  # the end face is at the end of the cylinder
END_GAP_MIN_MM = 1.0
END_GAP_RATIO = 0.25


def end_placement(at: float, low: float, high: float, major: float) -> tuple[float, float, float] | None:
    """Where a thread starts on a cylinder that runs from ``low`` to ``high`` along its axis when the end face is at ``at``:
    ``(sense, start, room)``: the way it runs (+1 towards ``high``, -1 towards ``low``), where it starts (the end face) and
    how far the shaft goes from there. ``None`` when the end face is not at an end of the cylinder. A chamfered or rounded
    end leaves the cylinder a little short of the end face, so the end face may lie beyond the end of the cylinder by
    ``max(END_GAP_MIN_MM, END_GAP_RATIO * major)``."""
    reach = max(END_GAP_MIN_MM, END_GAP_RATIO * major)
    if -END_TOLERANCE_MM <= at - high <= reach:
        return -1.0, at, at - low
    if -END_TOLERANCE_MM <= low - at <= reach:
        return 1.0, at, high - at
    return None


def is_record(obj: Any) -> bool:
    """Whether a document object is the record of an external thread."""
    return getattr(obj, "TypeId", "") == OBJECT_TYPE and "ThreadSize" in getattr(obj, "PropertiesList", ())


def read(obj: Any) -> dict[str, Any]:
    """The numbers of one record: designation, major and minor diameter, pitch, length, the centre of the end face and the
    direction from it into the shaft (world, mm), and the cylinder face it was made on."""
    start, direction = obj.ThreadStart, obj.ThreadDirection
    return {
        "name": str(obj.Label),
        "size": str(obj.ThreadSize),
        "major_mm": float(obj.ThreadMajor),
        "pitch_mm": float(obj.ThreadPitch),
        "minor_mm": float(obj.ThreadMinor),
        "length_mm": float(obj.ThreadLength),
        "start": [float(start.x), float(start.y), float(start.z)],
        "direction": [float(direction.x), float(direction.y), float(direction.z)],
        "cylinder": str(obj.ThreadCylinder),
        "tolerance_class": str(getattr(obj, "ThreadClass", "") or ""),  # a record of an older file has none
    }


def read_threads(fc_doc: Any) -> list[dict[str, Any]]:
    """The external threads of a document, read from its objects (so a file reopened, or edited by hand, says what it
    holds)."""
    return [read(obj) for obj in fc_doc.Objects if is_record(obj)]
