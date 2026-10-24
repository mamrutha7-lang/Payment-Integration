#!/usr/bin/env python3
"""Render the home-screen icons for the installable student app.

    apps/api-py/.venv/bin/pip install 'pillow' 'fonttools[woff]'
    apps/api-py/.venv/bin/python tools/icons/make-app-icons.py

WHY A SCRIPT AND NOT FOUR COMMITTED PNGs NOBODY CAN REGENERATE. The icon is the
brandmark — `.brandmark` in styles/reep-v2.scss, a rounded square of
`--primary-gradient` with a white "R" in Plus Jakarta Sans 800 — and the
gradient's four stops are tokens that the design has already moved once. A PNG
exported by hand is a copy of those values that nothing keeps in step; this
reads them from the constants below, which sit next to the token names they
came from, and redraws every size in one command.

THE FONT IS THE APP'S OWN, CONVERTED ON THE FLY. Plus Jakarta Sans lives in
apps/web/public/fonts as woff2 (tools/fonts/fetch-fonts.sh put it there) and
Pillow cannot read woff2, so fontTools decompresses it to a TTF in a temp file.
Drawing the R in DejaVu instead would put a different letterform on the home
screen from the one in the app bar, which is the one place the two are seen
side by side.

WHAT IT WRITES, AND WHY FOUR FILES AND NOT ONE.

  icon-192.png / icon-512.png   `purpose: any`. Drawn as the brandmark is:
                                rounded corners, transparent outside them.
                                The platform shows these as-is.

  icon-maskable-512.png         `purpose: maskable`. FULL BLEED and square —
                                Android applies its own mask (a circle, a
                                squircle, a teardrop, per launcher) and clips
                                whatever falls outside. A rounded icon supplied
                                here gets rounded twice and lands as a small
                                mark floating in a white box. The letter is
                                sized to the 80% safe zone the spec guarantees.

  apple-touch-icon.png          iOS ignores the manifest's icons for "Add to
                                Home Screen" and reads this <link> instead. It
                                applies its own rounding and composites
                                transparency against BLACK, so this one is
                                opaque and square.
"""

from __future__ import annotations

import math
import pathlib
import tempfile

from PIL import Image, ImageDraw, ImageFont

ROOT = pathlib.Path(__file__).resolve().parents[2]
OUT = ROOT / "apps" / "web" / "public"
WOFF2 = ROOT / "apps" / "web" / "public" / "fonts" / "plus-jakarta-sans-700-latin.woff2"

#: `--primary-gradient` in styles/reep-v2.scss:
#: linear-gradient(120deg, #552c7e, #7a2f9e 38%, #a0248f 68%, #ba2185)
GRADIENT_DEG = 120
STOPS: list[tuple[float, tuple[int, int, int]]] = [
    (0.00, (0x55, 0x2C, 0x7E)),
    (0.38, (0x7A, 0x2F, 0x9E)),
    (0.68, (0xA0, 0x24, 0x8F)),
    (1.00, (0xBA, 0x21, 0x85)),
]

#: Supersample everything and downscale at the end. The corner radius and the
#: letter both have curves, and a 192px icon drawn directly has visibly ragged
#: ones on a phone that is showing it at 3x.
SS = 4

#: `--r-icon` is 8px on a 26px mark — 31%, which reads as a squircle rather
#: than a rounded square, and is what the app bar shows.
CORNER = 0.31


def gradient(size: int) -> Image.Image:
    """`linear-gradient(120deg, …)` over a square, as CSS defines it.

    CSS measures the angle CLOCKWISE FROM "to top", so 120deg points right and
    down; and the gradient LINE is sized so the corners of the box land exactly
    on its ends, which is why the projection below is normalised by the box's
    extent along that direction rather than by its width.
    """
    rad = math.radians(GRADIENT_DEG)
    dx, dy = math.sin(rad), -math.cos(rad)
    extent = abs(dx) + abs(dy)  # square box, unit direction

    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        for x in range(size):
            # Centre-relative, normalised to 0..1 along the gradient line.
            u = ((x - size / 2) * dx + (y - size / 2) * dy) / (size * extent / 2)
            t = min(1.0, max(0.0, (u + 1) / 2))
            for i in range(len(STOPS) - 1):
                t0, c0 = STOPS[i]
                t1, c1 = STOPS[i + 1]
                if t <= t1 or i == len(STOPS) - 2:
                    k = 0.0 if t1 == t0 else (t - t0) / (t1 - t0)
                    k = min(1.0, max(0.0, k))
                    px[x, y] = tuple(round(a + (b - a) * k) for a, b in zip(c0, c1))
                    break
    return img


#: `.brandmark` sets `font-weight: 800`, the top of this face's axis.
BRAND_WEIGHT = 800


def load_font(path: pathlib.Path, size: int) -> ImageFont.FreeTypeFont:
    """The brand face at the weight the app bar actually draws it.

    PINNED WITH `instancer`, AND THE FIRST VERSION WAS WRONG WITHOUT IT. The
    file is a VARIABLE font — one `wght` axis, 200 to 800, defaulting to 400 —
    despite being named `-700-`. A browser asked for `font-weight: 800` sets
    the axis and gets the heavy R that is in the app bar; Pillow has no such
    request to make and renders the DEFAULT instance, so the icon came out at
    Regular. It is not obviously wrong on its own, which is the problem: it
    only reads as wrong beside the app bar, where the two are seen together.
    `instancer` bakes the axis at 800 and hands back a static font.
    """
    from fontTools.ttLib import TTFont
    from fontTools.varLib import instancer

    font = TTFont(str(path))
    if "fvar" in font:
        font = instancer.instantiateVariableFont(font, {"wght": BRAND_WEIGHT})
    with tempfile.NamedTemporaryFile(suffix=".ttf", delete=False) as tmp:
        font.flavor = None  # decompress woff2 -> plain TTF
        font.save(tmp.name)
        return ImageFont.truetype(tmp.name, size)


def draw_mark(size: int, *, rounded: bool, letter_ratio: float, opaque: bool) -> Image.Image:
    big = size * SS
    tile = gradient(big).convert("RGBA")

    if rounded:
        mask = Image.new("L", (big, big), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, big - 1, big - 1), radius=int(big * CORNER), fill=255
        )
        tile.putalpha(mask)

    # The R, centred on its INK rather than on its advance width: a glyph's
    # bounding box is not symmetric inside its em, and centring on the em puts
    # a visible bias to one side at icon scale.
    font = load_font(WOFF2, int(big * letter_ratio))
    draw = ImageDraw.Draw(tile)
    left, top, right, bottom = draw.textbbox((0, 0), "R", font=font)
    draw.text(
        ((big - (right - left)) / 2 - left, (big - (bottom - top)) / 2 - top),
        "R",
        font=font,
        fill=(255, 255, 255, 255),
    )

    out = tile.resize((size, size), Image.LANCZOS)
    if opaque:
        flat = Image.new("RGB", (size, size), (255, 255, 255))
        flat.paste(out, mask=out.split()[3])
        return flat
    return out


def main() -> None:
    if not WOFF2.exists():
        raise SystemExit(f"{WOFF2} is missing — run tools/fonts/fetch-fonts.sh first")

    jobs = [
        # `any`: the brandmark as the app bar draws it.
        ("icon-192.png", 192, dict(rounded=True, letter_ratio=0.56, opaque=False)),
        ("icon-512.png", 512, dict(rounded=True, letter_ratio=0.56, opaque=False)),
        # `maskable`: full bleed, letter inside the 80% safe zone.
        ("icon-maskable-512.png", 512, dict(rounded=False, letter_ratio=0.42, opaque=True)),
        # iOS composites transparency against black, so this one is opaque.
        ("apple-touch-icon.png", 180, dict(rounded=False, letter_ratio=0.56, opaque=True)),
    ]
    for name, size, kw in jobs:
        img = draw_mark(size, **kw)
        img.save(OUT / name, "PNG", optimize=True)
        print(f"{name:26} {size}x{size}  {(OUT / name).stat().st_size:>6} B")


if __name__ == "__main__":
    main()
