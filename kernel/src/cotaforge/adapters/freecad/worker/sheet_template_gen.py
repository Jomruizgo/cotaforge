# ruff: noqa: E501
"""The drawing templates of any paper size and projection angle, as the SVG TechDraw reads (no FreeCAD import).

The two templates cotaforge shipped (A4 and A3, first angle, Spanish: ``templates/A4_es.svg`` and ``A3_es.svg``) fix the
look: an ISO 5457 frame with its centring marks and grid reference, the ISO 7200 title block (180 x 36 mm) in the lower right
corner, the revision table left of it and the projection symbol above the table. This module makes the same sheet for the
other papers (A2, A1, A0, Letter) and for third-angle projection (the symbol mirrored), in Spanish or English: the page is
the paper size, the frame, the grid and the centring marks follow it, the title block keeps its size and stays in the
corner, the revision table takes the room left of it.

The text fields keep the names TechDraw's own templates use (``title``, ``creator``, ``part_material``...): they are the
fields the engineer edits in FreeCAD, and the ones ``drawing`` fills.
"""

from xml.sax.saxutils import escape

PAGES_MM = {
    "A4": (297.0, 210.0),
    "A3": (420.0, 297.0),
    "A2": (594.0, 420.0),
    "A1": (841.0, 594.0),
    "A0": (1189.0, 841.0),
    "Letter": (279.4, 215.9),
}  # landscape
TITLE_W, TITLE_H = 180.0, 36.0
FRAME_LEFT, FRAME_OTHER = 20.0, 10.0  # the binding margin and the others (ISO 5457)
GRID_SPACING = 50.0
REVISION_ROWS = 5
MAX_REVISION_W = 110.0

LABELS = {
    "es": {
        "legal_owner": "Empresa:",
        "identification_number": "Número de plano:",
        "revision_index": "Revisión:",
        "date_of_issue": "Fecha:",
        "sheet_number": "Hoja:",
        "language_code": "Idioma:",
        "title": "Título:",
        "approval_person": "Aprobó:",
        "creator": "Dibujó:",
        "document_type": "Tipo de documento:",
        "part_material": "Material:",
        "general_tolerances": "Tolerancias generales:",
        "sheet_scale": "Escala:",
        "rev": ("Rev.", "Descripción", "Fecha", "Autor"),
        "name": "Plano ISO 5457 - {size} apaisado - rotulo en espanol",
    },
    "en": {
        "legal_owner": "Company:",
        "identification_number": "Drawing number:",
        "revision_index": "Revision:",
        "date_of_issue": "Date:",
        "sheet_number": "Sheet:",
        "language_code": "Language:",
        "title": "Title:",
        "approval_person": "Approved by:",
        "creator": "Drawn by:",
        "document_type": "Document type:",
        "part_material": "Material:",
        "general_tolerances": "General tolerances:",
        "sheet_scale": "Scale:",
        "rev": ("Rev.", "Description", "Date", "By"),
        "name": "ISO 5457 drawing - {size} landscape - title block in English",
    },
}


def _f(value: float) -> str:
    text = f"{value:.2f}".rstrip("0").rstrip(".")
    return text or "0"


def zone_ticks(centre: float, low: float, high: float) -> list[float]:
    """The ticks that divide an edge of the grid reference into zones of 50 mm counted from the middle of the edge (the
    centring mark is the middle one): every tick at least 10 mm in from the ends of the edge."""
    out = []
    k = 1
    while centre - GRID_SPACING * k > low + 10.0:
        out += [centre - GRID_SPACING * k, centre + GRID_SPACING * k]
        k += 1
    return sorted(out)


def _symbol(first_angle: bool, x0: float, y0: float) -> tuple[str, str]:
    """The projection symbol (ISO 5456-2) with its top left corner at ``(x0, y0)``: the frustum seen from the side and the
    two circles of the frustum seen from its end, as outlines and as the two centre lines through them. First angle: the
    frustum on the left, the circles on the right; third angle: the other way round (the whole symbol mirrored)."""

    def mirror(x: float) -> float:
        return x if first_angle else 22.0 - x

    def cx(x: float) -> float:
        return x0 + mirror(x)

    cone = [(10, 0), (0, 2.5), (0, 7.5), (10, 10)]
    points = " ".join(f"{'M' if i == 0 else 'L'} {_f(cx(x))},{_f(y0 + y)}" for i, (x, y) in enumerate(cone))
    outlines = (
        f'  <g id="extras_outlines" style="fill:none;stroke:#000000;stroke-width:0.5;stroke-linecap:round;stroke-linejoin:round">\n'
        f'    <path id="angle_trapezoid" d="{points} Z"/>\n'
        f'    <circle id="angle_inner_circle" cx="{_f(cx(17))}" cy="{_f(y0 + 5)}" r="2.5"/>\n'
        f'    <circle id="angle_outer_circle" cx="{_f(cx(17))}" cy="{_f(y0 + 5)}" r="5"/>\n'
        "  </g>\n"
    )
    left, right = sorted((cx(-1), cx(23)))
    lines = (
        '  <g id="extras_centre_lines" style="fill:none;stroke:#000000;stroke-width:0.25;stroke-linecap:round;stroke-linejoin:round;stroke-dasharray:6, 0.75, 0.125, 0.75">\n'
        f'    <path id="angle_horizontal" d="M {_f(left)},{_f(y0 + 5)} H {_f(right)}"/>\n'
        f'    <path id="angle_vertical" d="M {_f(cx(17))},{_f(y0 + 11)} V {_f(y0 - 1)}"/>\n'
        "  </g>\n"
    )
    return outlines, lines


def build(size: str, language: str = "es", angle: str = "first") -> str:
    """The SVG of the template for paper ``size`` (a key of ``PAGES_MM``), ``language`` (``es`` / ``en``) and projection
    ``angle`` (``first`` / ``third``)."""
    width, height = PAGES_MM[size]
    words = LABELS[language]
    fl, ft = FRAME_LEFT, FRAME_OTHER
    fr, fb = width - FRAME_OTHER, height - FRAME_OTHER  # right and bottom edge of the frame
    tx0, ty0 = fr - TITLE_W, fb - TITLE_H  # the title block's top left corner
    rev_w = min(MAX_REVISION_W, tx0 - fl)
    rx0 = tx0 - rev_w
    out: list[str] = [
        '<?xml version="1.0" encoding="UTF-8" standalone="no"?>\n',
        f'<svg width="{_f(width)}mm" height="{_f(height)}mm" viewBox="0 0 {_f(width)} {_f(height)}" version="1.1" id="svg"\n'
        '   xmlns="http://www.w3.org/2000/svg" xmlns:svg="http://www.w3.org/2000/svg"\n'
        '   xmlns:freecad="https://www.freecad.org/wiki/index.php?title=Svg_Namespace">\n',
        f'  <title id="title">{escape(words["name"].format(size=size))}</title>\n',
        f'  <rect id="drawing_space_frame" x="{_f(fl)}" y="{_f(ft)}" width="{_f(fr - fl)}" height="{_f(fb - ft)}" style="fill:none;stroke:#000000;stroke-width:0.7;stroke-linecap:round;stroke-linejoin:round" />\n',
    ]
    # centring marks and the grid reference: zones of 50 mm counted from the middle of each edge
    cx_mid, cy_mid = width / 2, height / 2
    bottom_len = 10.0 if cx_mid < tx0 else 6.0
    out.append(
        '  <g id="centring_marks" style="fill:none;stroke:#000000;stroke-width:0.7;stroke-linecap:round">\n'
        f'    <path id="top_centering" d="M {_f(cx_mid)},5 V 15" />\n'
        f'    <path id="right_centering" d="M {_f(width - 5)},{_f(cy_mid)} H {_f(width - 15)}" />\n'
        f'    <path id="bottom_centering" d="M {_f(cx_mid)},{_f(height - 5)} V {_f(height - 5 - bottom_len)}" />\n'
        f'    <path id="left_centering" d="M 15,{_f(cy_mid)} H 25" />\n'
        "  </g>\n"
    )
    xs = zone_ticks(cx_mid, 15.0, width - 5.0)
    ys = zone_ticks(cy_mid, 5.0, height - 5.0)
    out.append(
        '  <g id="grid_reference_system">\n'
        '    <g id="grid_reference_borders" style="fill:none;stroke:#000000;stroke-width:0.35;stroke-linecap:round;stroke-linejoin:round">\n'
        f'      <rect id="grid_reference_border" x="15" y="5" width="{_f(width - 20)}" height="{_f(height - 10)}" />\n'
    )
    for n, x in enumerate(xs, start=1):
        out.append(f'      <path id="upper_border_{n}" d="M {_f(x)},5 V 10"/>\n')
        out.append(f'      <path id="lower_border_{n}" d="M {_f(x)},{_f(height - 5)} V {_f(height - 10)}"/>\n')
    for n, y in enumerate(ys, start=1):
        out.append(f'      <path id="right_border_{n}" d="M {_f(width - 5)},{_f(y)} H {_f(width - 10)}"/>\n')
        out.append(f'      <path id="left_border_{n}" d="M 15,{_f(y)} H 20"/>\n')
    out.append("    </g>\n")
    cols = [15.0, *xs, width - 5.0]
    rows = [5.0, *ys, height - 5.0]
    # the centring mark is a tick of the middle of the edge too: zones are counted between ticks and the centre
    cols = sorted({*cols, cx_mid})
    rows = sorted({*rows, cy_mid})
    out.append(
        '    <g id="grid_reference_markers" style="font-size:3.5px;font-family:osifont;text-align:center;text-anchor:middle;fill:#000000;stroke:none">\n'
    )
    for n in range(len(cols) - 1):
        x = (max(cols[n], fl) + cols[n + 1]) / 2
        label = str(n + 1)
        out.append(f'      <text id="upper_marker_{n + 1}" x="{_f(x)}" y="8.8">{label}</text>\n')
        last = n == len(cols) - 2
        out.append(
            f'      <text id="lower_marker_{n + 1}" x="{_f(x)}" y="{_f(height - 6.2)}">{size if last else label}</text>\n'
        )
    for n in range(len(rows) - 1):
        y = (rows[n] + rows[n + 1]) / 2 + 1.3
        letter = chr(ord("A") + n)
        out.append(f'      <text id="right_marker_{n + 1}" x="{_f(width - 7.5)}" y="{_f(y)}">{letter}</text>\n')
        out.append(f'      <text id="left_marker_{n + 1}" x="17.5" y="{_f(y)}">{letter}</text>\n')
    out.append("    </g>\n  </g>\n")

    # the title block (ISO 7200): fixed size, in the corner
    def rect(name: str, dx: float, dy: float, w: float, h: float, extra: str = "") -> str:
        return f'    <rect id="{name}_border" x="{_f(tx0 + dx)}" y="{_f(ty0 + dy)}" width="{_f(w)}" height="{_f(h)}"{extra}/>\n'

    out.append(
        '  <g id="title_block_borders" style="fill:none;stroke:#000000;stroke-width:0.35;stroke-linecap:round;stroke-linejoin:round">\n'
        f'    <rect id="title_block_frame" x="{_f(tx0)}" y="{_f(ty0)}" width="{_f(TITLE_W)}" height="{_f(TITLE_H)}" style="stroke-width:0.7"/>\n'
        + rect("legal_owner", 140, 0, 40, 24)
        + rect("identification_number", 0, 24, 80, 12)
        + rect("revision_index", 140, 24, 20, 12)
        + rect("date_of_issue", 100, 24, 40, 12)
        + rect("sheet_number", 160, 24, 20, 12)
        + rect("language_code", 80, 24, 20, 12)
        + rect("title", 0, 0, 80, 12)
        + rect("approval_person", 80, 12, 60, 12)
        + rect("creator", 80, 0, 60, 12)
        + rect("document_type", 0, 12, 80, 12)
        + rect("part_material", 0, -12, 80, 12)
        + rect("general_tolerances", 80, -12, 60, 12)
        + rect("sheet_scale", 140, -12, 40, 12)
        + "  </g>\n"
    )

    def label(name: str, dx: float, dy: float) -> str:
        return f'    <text id="{name}_label" x="{_f(tx0 + dx + 0.8)}" y="{_f(ty0 + dy + 3.2)}">{escape(words[name])}</text>\n'

    out.append(
        '  <g id="title_block_labels" style="font-size:2.5px;font-family:osifont;text-anchor:start;fill:#000000;stroke:none">\n'
        + label("legal_owner", 140, 0)
        + label("identification_number", 0, 24)
        + label("revision_index", 140, 24)
        + label("date_of_issue", 100, 24)
        + label("sheet_number", 160, 24)
        + label("language_code", 80, 24)
        + label("title", 0, 0)
        + label("approval_person", 80, 12)
        + label("creator", 80, 0)
        + label("document_type", 0, 12)
        + label("part_material", 0, -12)
        + label("general_tolerances", 80, -12)
        + label("sheet_scale", 140, -12)
        + "  </g>\n"
    )

    def field(name: str, dx: float, dy: float, maxw: float, size_pt: float, anchor: str, autofill: str = "") -> str:
        auto = f' freecad:autofill="{autofill}"' if autofill else ""
        return (
            f'    <text id="{name}_data_field" x="{_f(tx0 + dx)}" y="{_f(ty0 + dy)}" freecad:editable="{name}"{auto} '
            f'data-maxwidth="{_f(maxw)}" style="font-size:{_f(size_pt)}px;text-anchor:{anchor}"><tspan/></text>\n'
        )

    out.append(
        '  <g id="title_block_data_fields" style="font-size:3.5px;font-family:osifont;text-anchor:start;fill:#000000;stroke:none">\n'
        + field("legal_owner_1", 160, 8.5, 38, 3.0, "middle", "organization")
        + field("legal_owner_2", 160, 12.5, 38, 3.0, "middle")
        + field("legal_owner_3", 160, 16.5, 38, 3.0, "middle")
        + field("legal_owner_4", 160, 20.5, 38, 3.0, "middle")
        + field("drawing_number", 1, 32.5, 78, 5.0, "start")
        + field("revision_index", 150, 32.5, 18, 5.0, "middle")
        + field("date_of_issue", 101, 32.5, 38, 3.5, "start", "date")
        + field("sheet_number", 170, 32.5, 18, 3.5, "middle", "sheet")
        + field("language_code", 90, 32.5, 18, 3.5, "middle")
        + field("title", 1, 9.8, 78, 5.0, "start", "title")
        + field("approval_person", 81, 21.5, 58, 3.5, "start")
        + field("creator", 81, 9.8, 58, 3.5, "start", "author")
        + field("document_type", 1, 21.5, 78, 3.5, "start")
        + field("scale", 160, -3.6, 38, 3.5, "middle", "scale")
        + field("general_tolerances", 81, -3.6, 58, 3.5, "start")
        + field("part_material", 1, -3.6, 78, 3.5, "start")
        + "  </g>\n"
    )

    # the revision table, left of the title block
    idx_w = 12.0 if rev_w >= 100 else 10.0
    by_w = 18.0 if rev_w >= 100 else 14.0
    date_w = 22.0 if rev_w >= 100 else 18.0
    desc_w = rev_w - idx_w - by_w - date_w
    c1, c2, c3 = rx0 + idx_w, rx0 + idx_w + desc_w, rx0 + idx_w + desc_w + date_w
    table = [
        '  <g id="revision_table_borders" style="fill:none;stroke:#000000;stroke-width:0.35;stroke-linecap:round;stroke-linejoin:round">\n',
        f'    <rect id="revision_table_frame" x="{_f(rx0)}" y="{_f(ty0)}" width="{_f(rev_w)}" height="{_f(TITLE_H)}" style="stroke-width:0.7"/>\n',
    ]
    for n, x in enumerate((c1, c2, c3), start=1):
        table.append(f'    <path id="revision_column_{n}" d="M {_f(x)},{_f(ty0)} V {_f(fb)}"/>\n')
    for n in range(REVISION_ROWS):
        y = ty0 + 6 * (n + 1)
        table.append(f'    <path id="revision_row_{n}" d="M {_f(rx0)},{_f(y)} H {_f(tx0)}"/>\n')
    table.append("  </g>\n")
    head = words["rev"]
    table.append(
        '  <g id="revision_table_labels" style="font-size:2.5px;font-family:osifont;text-anchor:start;fill:#000000;stroke:none">\n'
        + "".join(
            f'    <text x="{_f(x + 0.8)}" y="{_f(ty0 + 4.0)}">{escape(t)}</text>\n'
            for x, t in zip((rx0, c1, c2, c3), head, strict=True)
        )
        + "  </g>\n"
    )
    table.append(
        '  <g id="revision_table_data_fields" style="font-size:3px;font-family:osifont;text-anchor:start;fill:#000000;stroke:none">\n'
    )
    for n in range(1, REVISION_ROWS + 1):
        y = ty0 + 6 * n + 5.2
        for key, x, w in (("index", rx0, idx_w), ("description", c1, desc_w), ("date", c2, date_w), ("by", c3, by_w)):
            table.append(
                f'    <text id="rev_{n}_{key}_data_field" x="{_f(x + 0.8)}" y="{_f(y)}" freecad:editable="rev_{n}_{key}" data-maxwidth="{_f(w - 1.6)}"><tspan/></text>\n'
            )
    table.append("  </g>\n")
    out += table
    # the projection symbol above the revision table, and the fixed notes beside it
    outlines, lines = _symbol(angle == "first", rx0 + 2.0, ty0 - 12.0)
    out += [outlines, lines, fixed_notes(rx0 + FIXED_NOTES_X_MM, tx0 - 2.0, ty0), "</svg>\n"]
    return "".join(out)


FIXED_NOTES = 3
FIXED_NOTES_X_MM = 30.0  # from the left of the revision table: past the projection symbol


def fixed_notes(x: float, right: float, ty0: float) -> str:
    """The fixed notes of the title block: ``FIXED_NOTES`` editable lines above the revision table, beside the projection
    symbol (the engineer's standing statements: the tolerance table, the process, the surface treatment of every part)."""
    fields = "".join(
        f'    <text id="fixed_note_{n}_data_field" x="{_f(x)}" y="{_f(ty0 - 1.0 - 4.0 * (FIXED_NOTES - n))}" '
        f'freecad:editable="fixed_note_{n}" data-maxwidth="{_f(right - x)}"><tspan/></text>\n'
        for n in range(1, FIXED_NOTES + 1)
    )
    return (
        '  <g id="fixed_notes_data_fields" style="font-size:3px;font-family:osifont;text-anchor:start;fill:#000000;stroke:none">\n'
        + fields
        + "  </g>\n"
    )
