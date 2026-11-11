# ruff: noqa: E501
"""The convention of holes: the colour of a hole says how it is made, and the isometric view of a drawing shows it.

The defaults live here, in the product's code: a fresh install has them with no file at all. The engineer overrides them
with ``convenciones.yaml`` in the data folder (``COTAFORGE_HOME``, shared by projects) and in the design space (one
project); the project file wins over the user file, which wins over the defaults. Keys are Spanish and a person can write
them by hand::

    agujeros:
      activa: true                    # false: no hole is coloured
      colores:
        preciso: [255, 128, 128]      # fine tolerance, needs a tool such as a reamer ("#ff8080" also works)
        liso: [0, 192, 0]             # an ordinary drill is enough
        roscado: [128, 128, 255]
      grado_it_preciso: 8             # a fit of this IT grade or finer (H6, H7, H8) makes a hole precision
      isometrico_en_color: true       # the isometric view of a drawing is shaded in colour

A wrong file never stops the work: the layer (or the value) that is wrong is ignored, the rest applies, and
``Resolved.problems`` says what is wrong so the agent can tell the engineer.
"""

import contextlib
import os
import re
import tempfile
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from cotaforge.conventions.classify import (
    CLASSES,
    DEFAULT_MAX_PRECISION_GRADE,
    PLAIN,
    PRECISION,
    THREADED,
    classify,
)

FILENAME = "convenciones.yaml"
HOME_ENV_VAR = "COTAFORGE_HOME"
SECTION = "agujeros"
SCOPES = ("proyecto", "usuario")

Rgb = tuple[int, int, int]

DEFAULT_COLOURS: dict[str, Rgb] = {PRECISION: (255, 128, 128), PLAIN: (0, 192, 0), THREADED: (128, 128, 255)}
KEY_OF_CLASS = {PRECISION: "preciso", PLAIN: "liso", THREADED: "roscado"}
CLASS_OF_KEY = {key: cls for cls, key in KEY_OF_CLASS.items()}
GRADE_RANGE = (1, 18)
_HEX = re.compile(r"^#?([0-9a-fA-F]{6})$")
_KEYS = {"activa", "colores", "grado_it_preciso", "isometrico_en_color"}


class ConventionError(ValueError):
    """A change to the convention that cannot be made; the message says what to give instead."""


@dataclass(frozen=True)
class HoleConvention:
    active: bool = True
    colours: dict[str, Rgb] = field(default_factory=lambda: dict(DEFAULT_COLOURS))
    max_precision_grade: int = DEFAULT_MAX_PRECISION_GRADE
    iso_colour: bool = True

    def hole_class(self, *, threaded: bool, fit: str | None) -> str:
        return classify(threaded=threaded, fit=fit, max_precision_grade=self.max_precision_grade)

    def colour_of(self, cls: str) -> Rgb:
        return self.colours.get(cls, DEFAULT_COLOURS[cls])

    def colour_for(self, *, threaded: bool, fit: str | None) -> tuple[str, Rgb] | None:
        """``(class, colour)`` of a hole, or ``None`` while the convention is switched off."""
        if not self.active:
            return None
        cls = self.hole_class(threaded=threaded, fit=fit)
        return cls, self.colour_of(cls)

    def describe(self) -> dict[str, Any]:
        """The convention in the file's own words (what the agent shows the engineer)."""
        return {
            "activa": self.active,
            "colores": {KEY_OF_CLASS[c]: list(self.colour_of(c)) for c in CLASSES},
            "grado_it_preciso": self.max_precision_grade,
            "isometrico_en_color": self.iso_colour,
        }


@dataclass(frozen=True)
class Resolved:
    holes: HoleConvention
    problems: tuple[str, ...] = ()
    sources: tuple[str, ...] = ()
    """The files that took part, lowest priority first (empty: the defaults of the product)."""


def parse_colour(value: Any) -> Rgb:
    """``[r, g, b]`` (0..255) or ``"#rrggbb"``."""
    if isinstance(value, str):
        found = _HEX.match(value.strip())
        if found is None:
            msg = f"{value!r} is not a colour: write [r, g, b] with 0..255 or '#rrggbb'"
            raise ConventionError(msg)
        digits = found.group(1)
        return (int(digits[0:2], 16), int(digits[2:4], 16), int(digits[4:6], 16))
    if (
        isinstance(value, list | tuple)
        and len(value) == 3
        and all(isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= 255 for c in value)
    ):
        return (int(value[0]), int(value[1]), int(value[2]))
    if value is None:  # YAML: an unquoted #ff8080 is a comment, so the value is empty
        msg = "the colour is empty: write the hex colour in quotes, '#rrggbb' (an unquoted # starts a comment in YAML), or [r, g, b]"
        raise ConventionError(msg)
    msg = f"{value!r} is not a colour: write [r, g, b] with 0..255 or '#rrggbb'"
    raise ConventionError(msg)


def _as_bool(name: str, value: Any) -> bool:
    if isinstance(value, bool):
        return value
    msg = f"{name} must be true or false"
    raise ConventionError(msg)


def parse_section(data: Any) -> tuple[dict[str, Any], list[str]]:
    """One layer of ``agujeros:`` as partial overrides ``{active, colours, grade, iso}`` and the problems found. A value
    that is wrong is dropped on its own; the others of the layer still count."""
    problems: list[str] = []
    out: dict[str, Any] = {}
    if data is None:
        return out, problems
    if not isinstance(data, dict):
        return out, [f"'{SECTION}' must be a list of keys ({', '.join(sorted(_KEYS))})"]
    for key in data:
        if key not in _KEYS:
            problems.append(f"unknown key '{SECTION}.{key}' (known: {', '.join(sorted(_KEYS))})")
    try:
        if "activa" in data:
            out["active"] = _as_bool("agujeros.activa", data["activa"])
    except ConventionError as exc:
        problems.append(str(exc))
    try:
        if "isometrico_en_color" in data:
            out["iso"] = _as_bool("agujeros.isometrico_en_color", data["isometrico_en_color"])
    except ConventionError as exc:
        problems.append(str(exc))
    grade = data.get("grado_it_preciso")
    if "grado_it_preciso" in data:
        if isinstance(grade, int) and not isinstance(grade, bool) and GRADE_RANGE[0] <= grade <= GRADE_RANGE[1]:
            out["grade"] = grade
        else:
            problems.append(f"agujeros.grado_it_preciso must be a whole IT grade {GRADE_RANGE[0]}..{GRADE_RANGE[1]}")
    colours = data.get("colores")
    if "colores" in data:
        if not isinstance(colours, dict):
            problems.append("agujeros.colores must list preciso, liso, roscado")
        else:
            found: dict[str, Rgb] = {}
            for key, value in colours.items():
                if key not in CLASS_OF_KEY:
                    problems.append(f"unknown colour 'agujeros.colores.{key}' (known: preciso, liso, roscado)")
                    continue
                try:
                    found[CLASS_OF_KEY[key]] = parse_colour(value)
                except ConventionError as exc:
                    problems.append(f"agujeros.colores.{key}: {exc}")
            out["colours"] = found
    return out, problems


def _apply(base: HoleConvention, layer: dict[str, Any]) -> HoleConvention:
    return replace(
        base,
        active=layer.get("active", base.active),
        colours={**base.colours, **layer.get("colours", {})},
        max_precision_grade=layer.get("grade", base.max_precision_grade),
        iso_colour=layer.get("iso", base.iso_colour),
    )


def _read_layer(path: Path) -> tuple[dict[str, Any] | None, list[str]]:
    """The ``agujeros`` overrides of a file; ``None`` when there is no file or it cannot be used (then a problem says why)."""
    if not path.is_file():
        return None, []
    try:
        loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
        reason = str(exc).splitlines()[0] if str(exc) else type(exc).__name__
        return None, [f"{path}: cannot be read ({reason}); the other layers apply"]
    if loaded is None:
        return {}, []
    if not isinstance(loaded, dict):
        return None, [f"{path}: it must be a list of sections such as '{SECTION}:'; the other layers apply"]
    layer, problems = parse_section(loaded.get(SECTION))
    return layer, [f"{path}: {p}" for p in problems]


class Conventions:
    """The conventions in force for one server: the user file under the data folder and the project file in the design
    space. Files are read on every call (they are tiny), so an edit by hand or by the agent takes effect at once."""

    def __init__(self, home: Path | None = None, workspace: Path | None = None) -> None:
        self.home = home
        self.workspace = workspace

    def path_of(self, scope: str) -> Path | None:
        if scope == "usuario":
            if self.home is not None:
                return self.home / FILENAME
            env = os.environ.get(HOME_ENV_VAR, "").strip()
            return (Path(env) if env else Path.home() / ".cotaforge") / FILENAME
        if scope == "proyecto":
            return None if self.workspace is None else self.workspace / FILENAME
        return None

    def resolve(self) -> Resolved:
        holes = HoleConvention()
        problems: list[str] = []
        sources: list[str] = []
        for scope in ("usuario", "proyecto"):  # the project file is last: it wins
            path = self.path_of(scope)
            if path is None:
                continue
            layer, found = _read_layer(path)
            problems += found
            if layer is not None:
                holes = _apply(holes, layer)
                sources.append(str(path))
        return Resolved(holes, tuple(problems), tuple(sources))

    def holes(self) -> HoleConvention:
        return self.resolve().holes

    def set_holes(self, scope: str, changes: dict[str, Any] | None, *, reset: bool = False) -> Path:
        """Write ``changes`` (the keys of ``agujeros``) into the file of ``scope`` ('usuario' or 'proyecto'), keeping the
        other keys it has. ``reset`` removes the section instead (back to the layer below). A change that is not valid
        raises ``ConventionError`` and writes nothing."""
        path = self.path_of(scope)
        if scope not in SCOPES or path is None:
            msg = f"scope must be one of: {', '.join(SCOPES)}"
            raise ConventionError(msg)
        document: dict[str, Any] = {}
        if path.is_file():
            try:
                loaded = yaml.safe_load(path.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError, yaml.YAMLError) as exc:
                msg = f"{path} cannot be read ({exc}); fix or delete it first"
                raise ConventionError(msg) from exc
            if isinstance(loaded, dict):
                document = loaded
            elif loaded is not None:
                msg = f"{path} is not a list of sections; fix or delete it first"
                raise ConventionError(msg)
        if reset:
            document.pop(SECTION, None)
        else:
            section = document.get(SECTION) if isinstance(document.get(SECTION), dict) else {}
            section = dict(section)
            for key, value in (changes or {}).items():
                if key == "colores":
                    if not isinstance(value, dict):
                        msg = "colores must list preciso, liso, roscado"
                        raise ConventionError(msg)
                    colours = dict(section.get("colores") or {})
                    colours.update(value)
                    value = colours  # noqa: PLW2901
                section[key] = value
            _, problems = parse_section(section)
            if problems:
                raise ConventionError("; ".join(problems))
            document[SECTION] = _normalised(section)
        _write_atomic(path, document)
        return path


def _normalised(section: dict[str, Any]) -> dict[str, Any]:
    """The section with colours written as plain ``[r, g, b]`` lists, so the file stays easy to read by a person."""
    out = dict(section)
    if isinstance(out.get("colores"), dict):
        out["colores"] = {k: list(parse_colour(v)) for k, v in out["colores"].items()}
    return out


def _write_atomic(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(document, allow_unicode=True, sort_keys=False, default_flow_style=None)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".convenciones-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
