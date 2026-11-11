"""What undoing a ``run_native`` step needs on top of ``journal``.

An op of the catalogue only creates objects and moves the four properties ``journal`` tracks, so deleting what it
created and writing those four back is a complete inverse. A native fragment can do anything: edit ``Length`` or a
sketch, replace a ``Shape``, delete an object. To give it the SAME undo as every other op (revert by per-op data, no
depth limit; FreeCAD's own undo stack keeps 20 transactions and is cleared by every revert), the step records, besides
the journal, the pre-step *persisted content* of exactly the properties it changed and of every object it deleted:

* before the step, ``capture`` dumps every persistable property of every object (``dumpPropertyContent``: the
  serialisation FreeCAD itself writes into an ``.FCStd``) and keeps a CRC per property;
* after it, ``diff`` finds the properties whose CRC moved, the objects that disappeared and the dynamic properties the
  fragment added; only those dumps are kept (``Restorer``);
* ``Restorer.apply`` runs inside ``journal.revert``'s transaction: recreate the deleted objects by type and name, write
  every changed property back with ``restorePropertyContent``, drop the dynamic properties the step added. Then the
  journal restores its tracked properties, the document is recomputed and ``Restorer.verify`` compares the touched
  objects with the fingerprint they had before the step.

Skipped on purpose: properties FreeCAD itself marks ``Transient``, ``Output`` (``Visibility`` and ``Label2`` are
output-only; ``Visibility`` is the journal's) or ``ReadOnly`` (derived values), and ``Shape`` of every object that
computes it (a recompute rebuilds it); the ``Shape`` of a plain ``Part::Feature`` is data and IS captured.
"""

import io
import zipfile
from dataclasses import dataclass, field
from typing import Any

import native_state

_SKIP_STATUS = frozenset({"Transient", "Output", "ReadOnly"})
_KEEP = frozenset({"Label2"})  # flagged Output by FreeCAD, but it is the engineer's own text
_STATIC_SHAPE_TYPES = frozenset({"Part::Feature", "Part::FeaturePython"})
_NEVER = frozenset({"Proxy"})
_skip_cache: dict[tuple[str, str], bool] = {}


def _skipped(obj: Any, prop: str) -> bool:
    key = (str(obj.TypeId), prop)
    hit = _skip_cache.get(key)
    if hit is None:
        if prop in _NEVER:
            hit = True
        elif prop in _KEEP:
            hit = False
        elif prop == "Shape":
            hit = key[0] not in _STATIC_SHAPE_TYPES
        else:
            try:
                hit = bool({str(s) for s in obj.getPropertyStatus(prop)} & _SKIP_STATUS)
            except Exception:  # noqa: BLE001 - a property without status info is captured
                hit = False
        _skip_cache[key] = hit
    return hit


Value = Any
"""What is kept of a property: the ``bytes`` ``dumpPropertyContent`` returns, or the ``TopoShape`` of a ``Shape``
that is data (a ``TopoShape`` shares its geometry with the object's, so keeping it is free)."""


def _dump(obj: Any, prop: str) -> Value | None:
    try:
        if prop == "Shape":
            return obj.Shape
        return bytes(obj.dumpPropertyContent(prop, Compression=0))
    except Exception:  # noqa: BLE001 - a property type that cannot be serialised is not restorable either
        return None


def _key(value: Value) -> Any:
    """A comparable identity of ``value``. For a zip dump: the CRC and sizes of its entry, because the local header's
    modification time (bytes 10-13) changes every second; these 12 bytes depend on the content only (a dump with
    more than one entry falls back to the CRC of every entry)."""
    if not isinstance(value, bytes):
        return value
    if value[-12:-10] == b"\x01\x00":
        return value[14:26]
    with zipfile.ZipFile(io.BytesIO(value)) as archive:
        return tuple((info.filename, info.CRC) for info in archive.infolist())


def _same(old: Value, new: Value) -> bool:
    if isinstance(old, bytes):
        return _key(old) == _key(new)
    try:
        return bool(old.isEqual(new))
    except Exception:  # noqa: BLE001 - null shapes
        return bool(old.isNull() and new.isNull())


def _put(obj: Any, prop: str, value: Value) -> None:
    if prop == "Shape":
        obj.Shape = value
    else:
        obj.restorePropertyContent(prop, value)


@dataclass
class Captured:
    """Every persistable property of every object, before a step: ``dumps[object][property]``."""

    types: dict[str, str] = field(default_factory=dict)
    props: dict[str, frozenset[str]] = field(default_factory=dict)  # every property name, dumped or not
    dumps: dict[str, dict[str, Value]] = field(default_factory=dict)


def capture(fc_doc: Any) -> Captured:
    captured = Captured()
    for obj in fc_doc.Objects:
        captured.types[obj.Name] = str(obj.TypeId)
        captured.props[obj.Name] = frozenset(obj.PropertiesList)
        props: dict[str, Value] = {}
        for prop in obj.PropertiesList:
            if _skipped(obj, prop):
                continue
            data = _dump(obj, prop)
            if data is not None:
                props[prop] = data
        captured.dumps[obj.Name] = props
    return captured


@dataclass
class Restorer:
    """The pre-step content of what a step changed or deleted, kept next to the op's ``Journal``."""

    removed: list[tuple[str, str, dict[str, Value]]] = field(default_factory=list)  # (name, type, props)
    changed: dict[str, dict[str, Value]] = field(default_factory=dict)
    added_props: dict[str, list[str]] = field(default_factory=dict)
    lost_props: dict[str, list[str]] = field(default_factory=dict)
    fingerprints: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def touched(self) -> bool:
        return bool(self.removed or self.changed or self.added_props)

    def irreversible(self) -> list[str]:
        """What the fragment did that no restore can take back (empty when the step can be undone completely)."""
        return [f"{name} lost the dynamic property {', '.join(props)}" for name, props in self.lost_props.items()]

    # --- apply -----------------------------------------------------------------------------------------------

    def apply(self, fc_doc: Any, warnings: list[str]) -> None:
        """Recreate the deleted objects, write the changed properties back, drop the added dynamic properties."""
        for name, type_id, _ in self.removed:
            existing = fc_doc.getObject(name)
            if existing is None:
                existing = fc_doc.addObject(type_id, name)
            if existing is None or existing.Name != name or str(existing.TypeId) != type_id:
                raise RuntimeError(f"could not recreate {name} ({type_id})")
        failures: list[str] = []
        targets = [(name, props) for name, _, props in self.removed] + list(self.changed.items())
        for name, props in targets:
            obj = fc_doc.getObject(name)
            if obj is None:
                warnings.append(f"{name} no longer exists (deleted by hand): its properties were not restored")
                continue
            for prop in _link_last(props):
                try:
                    _put(obj, prop, props[prop])
                except Exception as exc:  # noqa: BLE001 - reported, then the whole revert is rolled back
                    failures.append(f"{name}.{prop}: {exc}")
        for name, props in self.added_props.items():
            obj = fc_doc.getObject(name)
            for prop in props:
                if obj is not None and prop in obj.PropertiesList:
                    try:
                        obj.removeProperty(prop)
                    except Exception as exc:  # noqa: BLE001
                        failures.append(f"{name}.{prop}: {exc}")
        if failures:
            raise RuntimeError("could not restore " + "; ".join(failures[:5]))

    def verify(self, fc_doc: Any) -> list[str]:
        """Touched objects that do NOT look like they did before the step (empty when the revert is complete)."""
        problems: list[str] = []
        for name, before in self.fingerprints.items():
            obj = fc_doc.getObject(name)
            if obj is None:
                problems.append(f"{name} is missing")
            elif native_state.object_fingerprint(obj) != before:
                problems.append(f"{name} differs from its pre-step state")
        return problems


_LINKISH = frozenset(
    {"Group", "Profile", "Base", "Tool", "Shapes", "Originals", "BaseFeature", "Tip", "Support", "AttachmentSupport"}
)


def _link_last(props: dict[str, Value]) -> list[str]:
    """Plain values first, then the links: a link is resolved against objects whose values are already back."""
    return sorted(props, key=lambda p: (p in _LINKISH, p))


def diff(fc_doc: Any, before: Captured, before_fp: dict[str, dict[str, Any]]) -> Restorer:
    """Compare the document now with ``before``: what a restore must put back."""
    restorer = Restorer()
    now = {obj.Name: obj for obj in fc_doc.Objects}
    for name, type_id in before.types.items():
        if name not in now:
            restorer.removed.append((name, type_id, before.dumps.get(name, {})))
            if name in before_fp:
                restorer.fingerprints[name] = before_fp[name]
    for name, obj in now.items():
        old = before.dumps.get(name)
        if old is None:
            continue
        props_now = list(obj.PropertiesList)
        changed: dict[str, Value] = {}
        for prop in props_now:
            if prop not in before.props[name]:
                restorer.added_props.setdefault(name, []).append(prop)
                continue
            if prop not in old:  # skipped or not serialisable before the step: nothing to compare with
                continue
            data = _dump(obj, prop)
            if data is not None and not _same(old[prop], data):
                changed[prop] = old[prop]
        lost = [prop for prop in before.props[name] if prop not in props_now]
        if lost:
            restorer.lost_props[name] = lost
        if changed:
            restorer.changed[name] = changed
        if (changed or name in restorer.added_props or lost) and name in before_fp:
            restorer.fingerprints[name] = before_fp[name]
    return restorer
