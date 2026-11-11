"""The name a part shows in the parts list of an assembly (no FreeCAD import).

A FreeCAD document ``Label`` is NOT a name to print: ``saveAs`` rewrites it from the file name, and FreeCAD strips the
digits at the end of names it makes unique, so ``rodamiento_6204`` can come back as ``4_rodamiento_``. The names the
list takes are the ones the engineer gave, in this order: the document name cotaforge recorded in the part's metadata
(``new_document name=`` or ``import name=``), the title of the part's own drawing sheet, the stem of the part's file
and, when there is nothing else, the component's own id.
"""

import os

SOURCE_SUFFIXES = (".step", ".stp", ".iges", ".igs")


def without_extension(name: str) -> str:
    """``eje_d20.step`` -> ``eje_d20`` (only the suffixes of the files cotaforge imports; ``eje_d20`` stays)."""
    stem, suffix = os.path.splitext(name)
    return stem if suffix.lower() in SOURCE_SUFFIXES else name


def display_name(doc_name: str | None, sheet_title: str | None, file_path: str | None, fallback: str) -> str:
    for candidate in (doc_name, sheet_title):
        if candidate and candidate.strip():
            return without_extension(candidate.strip())
    if file_path:
        stem = os.path.splitext(os.path.basename(file_path.replace("\\", "/")))[0]
        if stem:
            return stem
    return fallback
