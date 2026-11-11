"""The declared set: the active ``process`` and ``material`` entries of the memory (closed world).

Declaring processes or materials is optional. If at least one of a kind is declared, only the declared ones
may be used; if none is declared, that kind does not restrict anything.
"""

from collections.abc import Iterable, Sequence

from pydantic import BaseModel, ConfigDict

from cotaforge.memory import Capability, EntryType, MemoryEntry, MemoryStore, normalize_key, normalize_text

DENSITY_PROPERTY = "density_kg_m3"  # the material-entry property that carries a density (provenance is required)


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DeclaredProcess(_Frozen):
    """A declared process: the id of its entry (what a ``route`` lists), its tag and its capabilities."""

    entry_id: str
    title: str
    tag: str
    known_tag: str | None  # None: a free tag, no automatic checks
    capabilities: tuple[Capability, ...]
    reference: str | None  # provenance reference of the entry (where its numbers come from)


class DeclaredMaterial(_Frozen):
    entry_id: str
    name: str
    designations: tuple[str, ...]
    reference: str | None
    density_kg_m3: float | None = None  # the entry's ``density_kg_m3`` property; its provenance is ``reference``


class ValidationResult(_Frozen):
    """Outcome of validating a route or a material against the declared set.

    ``restricted`` is False when nothing of that kind is declared (``ok`` is then True and ``allowed`` is
    empty). ``allowed`` lists the declared ids (routes) or names (materials). ``matched`` is the id of the
    declared entry that matched a material.
    """

    ok: bool
    reason: str
    allowed: tuple[str, ...] = ()
    restricted: bool = True
    matched: str | None = None


def _declared_density(properties: dict[str, object]) -> float | None:
    """The positive ``density_kg_m3`` number of a material entry, or None (a boolean or a text is not a density)."""
    value = properties.get(DENSITY_PROPERTY)
    if isinstance(value, bool) or not isinstance(value, int | float) or not value > 0:
        return None
    return float(value)


class DeclaredSet:
    def __init__(self, entries: Iterable[MemoryEntry]) -> None:
        """``entries`` is the merged view (one entry per id, project already over user). Only active
        ``process`` and ``material`` entries count; an inactive one declares nothing."""
        processes: list[DeclaredProcess] = []
        materials: list[DeclaredMaterial] = []
        for entry in sorted(entries, key=lambda item: item.id):
            if not entry.is_active:
                continue
            reference = entry.provenance.reference if entry.provenance is not None else None
            if entry.type is EntryType.process and entry.process is not None:
                processes.append(
                    DeclaredProcess(
                        entry_id=entry.id,
                        title=entry.title,
                        tag=entry.process.tag,
                        known_tag=entry.process.known_tag,
                        capabilities=entry.process.capabilities,
                        reference=reference,
                    )
                )
            elif entry.type is EntryType.material and entry.material is not None:
                materials.append(
                    DeclaredMaterial(
                        entry_id=entry.id,
                        name=entry.material.name,
                        designations=entry.material.designations,
                        reference=reference,
                        density_kg_m3=_declared_density(entry.material.properties),
                    )
                )
        self.processes: tuple[DeclaredProcess, ...] = tuple(processes)
        self.materials: tuple[DeclaredMaterial, ...] = tuple(materials)

    @classmethod
    def from_store(cls, store: MemoryStore) -> "DeclaredSet":
        """The declared set of a store: both scopes merged, project over user by id."""
        return cls(item.entry for item in store.entries())

    @property
    def processes_declared(self) -> bool:
        return bool(self.processes)

    @property
    def materials_declared(self) -> bool:
        return bool(self.materials)

    def process(self, entry_id: str) -> DeclaredProcess | None:
        wanted = normalize_text(entry_id.strip())
        return next((item for item in self.processes if item.entry_id == wanted), None)

    def find_material(self, name: str) -> DeclaredMaterial | None:
        """The declared material whose ``name`` or any designation equals ``name`` (case, accent and
        space insensitive), or None."""
        wanted = normalize_key(name)
        if not wanted:
            return None
        for item in self.materials:
            if wanted in (normalize_key(label) for label in (item.name, *item.designations)):
                return item
        return None

    def validate_route(self, route: Sequence[str]) -> ValidationResult:
        """A route (process entry ids) must be a subset of the declared processes."""
        if not self.processes_declared:
            return ValidationResult(ok=True, reason="no processes are declared: nothing restricts", restricted=False)
        allowed = tuple(item.entry_id for item in self.processes)
        offending = [step for step in route if self.process(step) is None]
        if offending:
            return ValidationResult(
                ok=False,
                reason=f"process(es) {offending} are not declared; declared: {list(allowed)}",
                allowed=allowed,
            )
        return ValidationResult(ok=True, reason="every process of the route is declared", allowed=allowed)

    def validate_material(self, name: str) -> ValidationResult:
        """The material must be one of the declared ones (by name or designation)."""
        if not self.materials_declared:
            return ValidationResult(ok=True, reason="no materials are declared: nothing restricts", restricted=False)
        allowed = tuple(item.name for item in self.materials)
        found = self.find_material(name)
        if found is None:
            return ValidationResult(
                ok=False,
                reason=f"material '{name}' is not declared; declared: {list(allowed)}",
                allowed=allowed,
            )
        return ValidationResult(
            ok=True, reason=f"'{name}' is the declared material '{found.name}'", allowed=allowed, matched=found.entry_id
        )
