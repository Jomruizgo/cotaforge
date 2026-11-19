# ruff: noqa: E501
"""The designation a part shows in the parts list of an assembly (no FreeCAD import).

A FreeCAD document ``Label`` is NOT a name to print: ``saveAs`` rewrites it from the file name, and FreeCAD strips the
digits at the end of names it makes unique, so ``rodamiento_6204`` can come back as ``4_rodamiento_``; and the name the
document was created with is an identifier, like its file name. The list prints what the designer called the part: the
title kept in the part's metadata (``doc set_title``) or the title of the part's own drawing sheet; with neither it prints
nothing and the review asks for it.
"""

import os

SOURCE_SUFFIXES = (".step", ".stp", ".iges", ".igs")


def without_extension(name: str) -> str:
    """``eje_d20.step`` -> ``eje_d20`` (only the suffixes of the files cotaforge imports; ``eje_d20`` stays)."""
    stem, suffix = os.path.splitext(name)
    return stem if suffix.lower() in SOURCE_SUFFIXES else name


def designation(title: str | None, sheet_title: str | None) -> str:
    """What the parts list calls a part: the designation the designer gave it (``doc set_title``), else the title of the
    part's own drawing sheet; ``""`` when there is neither. The name of the document and the name of its file are never
    shown: they are identifiers (often English, always without accents), not the designation a Spanish sheet prints, so a
    part with no designation is a question to the designer (``part_unnamed``), not a guess."""
    for candidate in (title, sheet_title):
        if candidate and candidate.strip():
            return candidate.strip()
    return ""
