"""Plan loader (ir/loader.py).

``load_plan`` accepts a dict (already-parsed plan), a ``Path`` to a YAML/JSON
file, or a ``str`` that is either a path to such a file or literal YAML/JSON
text. YAML is a JSON superset, so parsing YAML alone covers both.

Gotcha: PyYAML's default (YAML 1.1) resolver treats the bare scalars
``on``/``off``/``yes``/``no`` (in any case) as booleans. ``sketch.on`` is a
real IR field name, so a plain ``yaml.safe_load`` would
silently turn ``on: {kind: plane, name: XY}`` into ``{True: {...}}`` and drop
the ``on`` key entirely. ``_PlanYamlLoader`` below narrows the implicit bool
resolver to ``true``/``false`` only (YAML 1.2 Core-schema behaviour), so
``on``/``off``/``yes``/``no`` parse as plain strings while real booleans
(``merge: true``, ``both_directions: false``, ...) are unaffected.
"""

import re
from pathlib import Path

import yaml

from cotaforge.ir.errors import PlanError
from cotaforge.ir.plan import Plan, parse_plan

__all__ = ["load_plan", "load_yaml_document", "parse_yaml_text"]

_BOOL_TAG = "tag:yaml.org,2002:bool"


class _PlanYamlLoader(yaml.SafeLoader):
    """SafeLoader restricted to ``true``/``false`` booleans (see module docstring)."""


_PlanYamlLoader.yaml_implicit_resolvers = {
    first_char: [(tag, regexp) for tag, regexp in resolvers if tag != _BOOL_TAG]
    for first_char, resolvers in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_PlanYamlLoader.add_implicit_resolver(
    _BOOL_TAG,
    re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"),
    list("tTfF"),
)


def load_plan(source: str | Path | dict) -> Plan:
    """Load and validate a plan from a dict, a file path, or YAML/JSON text.

    ``allow_stamp=False`` (the default): this is the user/agent-input door, so a ``validated``
    stamp present in ``source`` is rejected as a ``PlanError`` rather than trusted.
    """
    data = _read(source)
    if not isinstance(data, dict):
        msg = f"plan source did not parse to a mapping, got {type(data).__name__}"
        raise PlanError(plan_id=None, op_id=None, rule="schema", message=msg)
    return parse_plan(data, allow_stamp=False)


def load_yaml_document(source: str | Path) -> object:
    """Parse a YAML/JSON file path or literal text with the bool-safe dialect (see module docstring).

    Equivalent to ``_read(source)`` restricted to ``str | Path`` (no dict passthrough). Public so
    other loaders (e.g. ``cotaforge.design.loader`` for concept rounds) share the same
    ``on``/``off``/``yes``/``no`` fix instead of re-implementing it.
    """
    return _read(source)


def parse_yaml_text(text: str) -> object:
    """Parse literal YAML/JSON text with the bool-safe dialect — never a file path (tool inputs must not read
    the filesystem; ``load_yaml_document`` is the path-or-text variant). ``yaml.YAMLError`` on bad syntax."""
    return _parse_yaml(text)


def _parse_yaml(text: str) -> object:
    return yaml.load(text, Loader=_PlanYamlLoader)  # noqa: S506 (restricted loader, not yaml.load's default)


def _read(source: str | Path | dict) -> object:
    if isinstance(source, dict):
        return source
    if isinstance(source, Path):
        return _parse_yaml(source.read_text(encoding="utf-8"))
    if isinstance(source, str):
        text = source
        try:
            candidate = Path(source)
            if candidate.is_file():
                text = candidate.read_text(encoding="utf-8")
        except OSError:
            pass  # not a usable path (e.g. embedded newlines) -- treat as literal text
        return _parse_yaml(text)
    msg = f"unsupported plan source type: {type(source)!r}"
    raise TypeError(msg)
