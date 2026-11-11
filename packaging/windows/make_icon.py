"""Draw the cotaforge icon: a machined part with a dimension over it, on a steel-blue tile.

Not part of the build: run it by hand when the icon changes and commit the result.
    uv run --no-project --with pillow python packaging/windows/make_icon.py <output folder>
It writes cotaforge.ico (16 to 256 px) and two preview images.
"""

import sys
from pathlib import Path

from PIL import Image, ImageDraw

S = 1024
out = Path(sys.argv[1])
out.mkdir(parents=True, exist_ok=True)

TOP, BOTTOM = (40, 78, 120), (24, 48, 78)
WHITE = (245, 247, 250, 255)
ACCENT = (242, 140, 40, 255)


def tile() -> Image.Image:
    grad = Image.new("RGBA", (S, S))
    px = grad.load()
    for y in range(S):
        t = y / (S - 1)
        c = tuple(round(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)) + (255,)
        for x in range(S):
            px[x, y] = c
    mask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mask).rounded_rectangle((32, 32, S - 32, S - 32), radius=200, fill=255)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.paste(grad, (0, 0), mask)
    return img


def draw(img: Image.Image) -> None:
    d = ImageDraw.Draw(img)
    # The part: a block with a stepped shoulder and a through hole.
    left, right, top, bottom = 232, 792, 470, 800
    step = 150
    d.polygon(
        [
            (left, bottom),
            (left, top),
            (left + 260, top),
            (left + 260, top + step),
            (right, top + step),
            (right, bottom),
        ],
        fill=WHITE,
    )
    hole_c, hole_r = (left + 130, top + 175), 62
    d.ellipse(
        (hole_c[0] - hole_r, hole_c[1] - hole_r, hole_c[0] + hole_r, hole_c[1] + hole_r),
        fill=BOTTOM + (255,),
    )
    # The dimension: extension lines, a dimension line and two arrowheads.
    y = 300
    w = 26
    d.rectangle((left, y - 40, left + w, top - 30), fill=ACCENT)
    d.rectangle((right - w, y - 40, right, top + step - 30), fill=ACCENT)
    d.rectangle((left + w, y - 13, right - w, y + 13), fill=ACCENT)
    head = 92
    d.polygon([(left + w, y), (left + w + head, y - 52), (left + w + head, y + 52)], fill=ACCENT)
    d.polygon([(right - w, y), (right - w - head, y - 52), (right - w - head, y + 52)], fill=ACCENT)


NEW = (46, 158, 79, 255)
OPEN = (232, 163, 23, 255)
BADGE_C, BADGE_R, RING = (748, 748), 250, 34


def badge(img: Image.Image, color: tuple[int, int, int, int]) -> ImageDraw.ImageDraw:
    """A big round badge in the lower right corner, ringed in white so it reads on the tile at 16 px."""
    d = ImageDraw.Draw(img)
    cx, cy = BADGE_C
    r = BADGE_R
    d.ellipse((cx - r - RING, cy - r - RING, cx + r + RING, cy + r + RING), fill=WHITE)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=color)
    return d


def new_badge(img: Image.Image) -> None:
    """Create a new project: a white plus on green."""
    d = badge(img, NEW)
    cx, cy = BADGE_C
    arm, half = 150, 40
    d.rectangle((cx - arm, cy - half, cx + arm, cy + half), fill=WHITE)
    d.rectangle((cx - half, cy - arm, cx + half, cy + arm), fill=WHITE)


def open_badge(img: Image.Image) -> None:
    """Open an existing project: a white folder on amber."""
    d = badge(img, OPEN)
    cx, cy = BADGE_C
    left, right, top, bottom = cx - 150, cx + 150, cy - 95, cy + 125
    d.rounded_rectangle((left, top - 45, left + 130, top + 30), radius=18, fill=WHITE)  # the tab
    d.rounded_rectangle((left, top, right, bottom), radius=22, fill=WHITE)
    d.rectangle((left + 22, top + 45, right - 22, top + 60), fill=OPEN)  # the folder's front edge


SIZES = [(16, 16), (20, 20), (24, 24), (32, 32), (40, 40), (48, 48), (64, 64), (128, 128), (256, 256)]
icons = {}
for name, extra in (("cotaforge", None), ("cotaforge-new", new_badge), ("cotaforge-open", open_badge)):
    img = tile()
    draw(img)
    if extra is not None:
        extra(img)
    img.save(out / f"{name}.ico", sizes=SIZES)
    icons[name] = img

# Preview: every icon at the sizes Windows shows, on a light and on a dark background.
preview_sizes = (16, 24, 32, 48, 64, 128)
row_w = sum(preview_sizes) + 20 * (len(preview_sizes) + 1)
row_h = max(preview_sizes) + 20
sheet = Image.new("RGBA", (row_w * 2, row_h * len(icons)), (0, 0, 0, 255))
for row, img in enumerate(icons.values()):
    for col, bg in enumerate(((255, 255, 255, 255), (32, 32, 32, 255))):
        panel = Image.new("RGBA", (row_w, row_h), bg)
        x = 20
        for size in preview_sizes:
            panel.alpha_composite(img.resize((size, size), Image.LANCZOS), (x, row_h - 10 - size))
            x += size + 20
        sheet.paste(panel, (col * row_w, row * row_h))
sheet.save(out / "preview-sizes.png")
print("ok", out)
