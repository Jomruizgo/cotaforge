"""Text shown to the agent for catalog parts and user entries. Every line ends with where it came from."""

from cotaforge_mcp.parts import geometry
from cotaforge_mcp.parts.model import Part
from cotaforge_mcp.parts.user import UserEntry

REPLY_CHARS = 5000  # the size of a catalog reply (parts_tools.RESULT_CHARS): a size table is cut to fit in it

PROP_LABELS: dict[str, str] = {
    "area_cm2": "A",
    "ix_cm4": "Ix",
    "iy_cm4": "Iy",
    "i_cm4": "I",
    "wx_cm3": "Wx",
    "wy_cm3": "Wy",
    "w_cm3": "W",
    "rx_cm": "rx",
    "ry_cm": "ry",
    "r_cm": "r",
    "c_cm": "c (centroid from the back)",
    "imin_cm4": "Imin",
    "rmin_cm": "rmin",
}


def short(value: float) -> str:
    return f"{value:.4g}" if abs(value) < 1000 else f"{value:.0f}"


def mass_text(part: Part) -> str:
    if part.mass is None:
        return "mass n/a"
    value, unit = part.mass
    return f"{short(value)} {unit}"


def line(part: Part) -> str:
    """One search row: id | designation | mass | provenance."""
    return f"{part.id} | {part.designation} | {mass_text(part)} | {part.provenance}"


def geometric_mass_kg_m(part: Part) -> float | None:
    if part.mass is None or part.mass[1] != "kg/m" or "area_cm2" not in part.props:
        return None
    return geometry.mass_per_metre_kg(part.props["area_cm2"] * 100.0)


def detail(part: Part, user_prices: list[UserEntry]) -> str:
    lines = [f"{part.id} ({part.kind}): {part.designation}"]
    lines.append("dims: " + ", ".join(f"{key}={value}" for key, value in part.dims.items()))
    if part.mass is not None:
        text = f"mass: {mass_text(part)} ({part.mass_basis}; {part.provenance})"
        geometric = geometric_mass_kg_m(part)
        if geometric is not None and part.mass_basis == "publicado":
            text += f"; from the nominal geometry: {short(geometric)} kg/m"
        lines.append(text)
    if part.props:
        shown = ", ".join(
            f"{PROP_LABELS.get(key, key)}={short(value)} {key.rpartition('_')[2]}" for key, value in part.props.items()
        )
        lines.append(f"properties: {shown} ({part.props_note})")
    lines.append(f"source: {part.source.citation}")
    if not part.verified:
        lines.append("status: SIN VERIFICAR: this row was not confirmed in a source that was read; confirm it")
    if part.note:
        lines.append(f"note: {part.note}")
    if user_prices:
        lines.append("user catalog for this part:")
        lines.extend(f"- {user_line(entry)}" for entry in user_prices)
    return "\n".join(lines)


def user_line(entry: UserEntry) -> str:
    if entry.role == "medidas":
        return (
            f"{entry.id} | {entry.name} | medidas según {entry.standard} "
            f"(referencia de medidas: no es proveedor, sin precio) | {entry.provenance()}"
        )
    price = ""
    if entry.price is not None:
        price = f"{short(entry.price)} {entry.currency}/{entry.unit} | "
    availability = f"{entry.availability} | " if entry.availability else ""
    return f"{entry.id} | {entry.name} | {price}{availability}{entry.provenance()}"


def _table(entry: UserEntry, budget: int) -> list[str]:
    """The size table as markdown rows, cut on a whole row when it would not fit in ``budget`` characters."""
    columns, rows = entry.columns or [], entry.rows or []

    def pipe(cells: list[str]) -> str:
        return "| " + " | ".join(cell.replace("|", "/").replace("\n", " ") for cell in cells) + " |"

    lines = [pipe(columns), pipe(["---"] * len(columns))]
    used = sum(len(text) + 1 for text in lines)
    for shown, row in enumerate(rows):
        text = pipe(row)
        if used + len(text) + 1 > budget:
            lines.append(
                f"... +{len(rows) - shown} more rows not shown ({len(rows)} in all; the entry file has them all)"
            )
            break
        lines.append(text)
        used += len(text) + 1
    return lines


def _medidas_detail(entry: UserEntry) -> str:
    # the instruction goes before the table: a long table is the part that gets cut
    lines = [
        f"{entry.id} (user catalog, medidas): {entry.name}",
        "this is a size reference only, not a supplier, and it has no price; pick a size from the table and write in "
        f"the design «medida comercial según {entry.standard}; conseguir local o fabricar»; never name the source "
        "as where to buy",
        f"standard: {entry.standard}",
        f"table source: {entry.source}",
        f"date read: {entry.date.isoformat()}",
    ]
    for label, value in (("url", entry.url), ("notes", entry.notes)):
        if value:
            lines.append(f"{label}: {value}")
    last = f"source: {entry.provenance()} (the engineer's data; a size table, not a quote: check the date)"
    room = REPLY_CHARS - sum(len(text) + 1 for text in lines) - len(last) - 100  # 100: the "more rows" line
    lines.extend(_table(entry, room))
    lines.append(last)
    return "\n".join(lines)


def user_detail(entry: UserEntry) -> str:
    if entry.role == "medidas":
        return _medidas_detail(entry)
    lines = [
        f"{entry.id} (user catalog): {entry.name}",
        f"supplier: {entry.supplier}",
        f"date: {entry.date.isoformat()}",
    ]
    if entry.price is not None:
        lines.append(f"price: {short(entry.price)} {entry.currency} per {entry.unit}")
    for label, value in (
        ("availability", entry.availability),
        ("catalog part", entry.part),
        ("url", entry.url),
        ("notes", entry.notes),
    ):
        if value:
            lines.append(f"{label}: {value}")
    lines.append(f"source: {entry.provenance()} (the engineer's data; prices go stale: check the date)")
    return "\n".join(lines)
