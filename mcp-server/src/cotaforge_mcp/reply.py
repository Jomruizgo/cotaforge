"""Tool results: compact key/value text, optional images, and the error shape every tool shares."""

from dataclasses import dataclass, field

from pydantic import ValidationError

MAX_CHARS = 1500
"""Soft cap for a tool result; list-style results pass their own larger limit."""


@dataclass
class Image:
    data: bytes
    mime: str = "image/png"


@dataclass
class Reply:
    text: str
    images: list[Image] = field(default_factory=list)
    is_error: bool = False


class ToolError(Exception):
    """A failure the agent can act on: what is wrong, which field, the correct shape and a minimal example."""

    def __init__(
        self, message: str, *, code: str = "error", field_name: str | None = None, usage: str = "", example: str = ""
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.field_name = field_name
        self.usage = usage
        self.example = example

    def render(self) -> str:
        lines = [f"error: {self.code}"]
        if self.field_name:
            lines.append(f"field: {self.field_name}")
        lines.append(self.message)
        if self.usage:
            lines.append(f"usage: {self.usage}")
        if self.example:
            lines.append(f"example: {self.example}")
        return "\n".join(lines)


def clip(text: str, limit: int = MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    cut = text[: limit - 60].rsplit("\n", 1)[0]
    return f"{cut}\n... (+{len(text) - len(cut)} chars cut; narrow the request)"


def ok(text: str, *, limit: int = MAX_CHARS) -> Reply:
    return Reply(clip(text, limit))


def fail(failure: ToolError) -> Reply:
    return Reply(failure.render(), is_error=True)


def first_validation_issues(exc: ValidationError, *, skip: int = 0, limit: int = 3) -> list[tuple[str, str]]:
    """``(dotted field path, message)`` for the first few pydantic errors; ``skip`` drops leading path parts (the
    discriminator tag of a union)."""
    issues: list[tuple[str, str]] = []
    for error in exc.errors()[:limit]:
        loc = [str(part) for part in error["loc"][skip:]]
        issues.append((".".join(loc) or "(root)", str(error["msg"])))
    return issues


def area_text(area: float | None) -> str:
    """The area of a surface body, or that the CAD gave none (never a 0 that reads as measured)."""
    return "area not measured" if area is None else f"area {num(area)} mm2"


def num(value: float, digits: int = 3) -> str:
    """A number for the agent: no trailing noise, no ``-0``."""
    rounded = round(float(value), digits)
    if rounded == 0:
        rounded = 0.0
    text = f"{rounded:.{digits}f}".rstrip("0").rstrip(".")
    return text or "0"


def vec(v: tuple[float, ...] | list[float], digits: int = 3) -> str:
    return "(" + ", ".join(num(x, digits) for x in v) + ")"
