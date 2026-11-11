# ruff: noqa: E501
"""The convention applied to the requests an adapter receives.

An adapter calls these just before it makes the thing, so the request carries the colour it must paint and the class it
comes from, and what is recorded in the document is what was in force when the hole was made or edited. They take the
requests as they come (any model with ``model_copy``), so this package imports nothing from the contract."""

from typing import Any, TypeVar

from cotaforge.conventions.holes import Conventions, HoleConvention

R = TypeVar("R")


def hole_convention(conventions: Conventions | None) -> HoleConvention:
    return HoleConvention() if conventions is None else conventions.holes()


def colour_hole[R](request: R, convention: HoleConvention) -> R:
    """A ``HoleRequest`` with the colour and class the convention gives the hole (both ``None`` when it is switched off).
    ``fit`` is the engineer's declaration and stays as it came."""
    chosen = convention.colour_for(threaded=request.thread is not None, fit=request.fit)  # type: ignore[attr-defined]
    cls, colour = chosen if chosen is not None else (None, None)
    grade = convention.max_precision_grade if chosen is not None else None
    return request.model_copy(  # type: ignore[attr-defined]
        update={"hole_class": cls, "colour": colour, "precision_grade": grade}
    )


def colour_edits[R](request: R, convention: HoleConvention) -> R:
    """An ``EditFeatureRequest`` whose hole edits are coloured: editing a hole re-evaluates its class, so the new request
    carries the colour and class the convention gives it now, as the one the hole was made with did."""
    edits: list[Any] = []
    for edit in request.edits:  # type: ignore[attr-defined]
        if edit.kind == "hole" and hasattr(edit.request, "fit"):
            edit = edit.model_copy(update={"request": colour_hole(edit.request, convention)})  # noqa: PLW2901
        edits.append(edit)
    return request.model_copy(update={"edits": tuple(edits)})  # type: ignore[attr-defined]


def iso_colour_drawing[R](request: R, convention: HoleConvention) -> R:
    """A ``DrawingRequest`` / ``AnnotateDrawingRequest`` that says whether the isometric view is in colour."""
    return request.model_copy(update={"iso_colour": convention.iso_colour})  # type: ignore[attr-defined]
