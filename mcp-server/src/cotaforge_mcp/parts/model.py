"""A catalog part as the tool shows it, and the provenance labels every result carries."""

from dataclasses import dataclass, field

CATALOG = "catálogo"
USER_CATALOG = "catálogo del usuario"
UNVERIFIED = "SIN VERIFICAR"


@dataclass(frozen=True)
class Source:
    """Where a table comes from. ``label`` is the short provenance printed with every result; ``citation`` has the
    full reference (standard, edition, publisher, URL, date read)."""

    key: str
    label: str
    citation: str


@dataclass(frozen=True)
class Part:
    id: str
    kind: str
    designation: str
    dims: dict[str, float | str]
    """Nominal dimensions (mm unless the key says otherwise), in display order."""
    source: Source
    """Provenance of the dimensions and of ``mass``."""
    mass: tuple[float, str] | None = None
    """``(value, unit)`` per metre, per square metre or per piece; ``None`` when the source gives none."""
    mass_basis: str = ""
    """``publicado`` (taken from the source) or ``calculado`` (nominal geometry x 7850 kg/m3)."""
    props: dict[str, float] = field(default_factory=dict)
    """Section properties computed from ``dims`` (see geometry.py for the convention), keys carry the unit."""
    props_note: str = ""
    verified: bool = True
    """False: the row could not be confirmed in a source that was read; the tool says so."""
    note: str = ""
    keywords: str = ""
    """Extra words the search matches (Spanish and English names, standard numbers, other size orders)."""

    @property
    def provenance(self) -> str:
        return self.source.label if self.verified else f"{UNVERIFIED}: {self.source.label}"
