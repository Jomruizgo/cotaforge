# ruff: noqa: E501
"""A tiny valid PDF written by hand (no PDF library), so the tests of the drawing export and of ``pdf_check`` work on real
bytes: ``minimal_pdf`` is a sound one-page file whose content stream the test chooses, ``corrupt`` breaks one."""

PT_PER_MM = 72.0 / 25.4

SOUND_CONTENT = b"0 0 0 RG 1 w 10 10 m 110 10 l 110 60 l 10 60 l h S\n"


def minimal_pdf(
    width_mm: float = 297.0,
    height_mm: float = 210.0,
    content: bytes = SOUND_CONTENT,
    pages: int = 1,
    colour_spaces: str = "/CSp /DeviceRGB",
) -> bytes:
    """``pages`` identical pages of ``width_mm`` x ``height_mm`` whose content is ``content``; the resources define the
    colour space ``/CSp`` (``colour_spaces`` is the body of the /ColorSpace dictionary)."""
    width, height = round(width_mm * PT_PER_MM), round(height_mm * PT_PER_MM)
    objects: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    first_page = 4
    kids = " ".join(f"{first_page + 2 * n} 0 R" for n in range(pages))
    objects.append(f"<< /Type /Pages /Kids [{kids}] /Count {pages} >>".encode())
    objects.append(b"")  # object 3 is unused: keeps the page numbering simple
    for n in range(pages):
        stream_ref = first_page + 2 * n + 1
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] /Contents {stream_ref} 0 R "
                f"/Resources << /ColorSpace << {colour_spaces} >> >> >>"
            ).encode()
        )
        objects.append(b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n" + content + b"endstream")
    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{number} 0 obj\n".encode() + (body or b"null") + b"\nendobj\n"
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode() + b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    return bytes(out)
