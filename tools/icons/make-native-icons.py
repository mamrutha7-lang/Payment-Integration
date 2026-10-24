#!/usr/bin/env python3
"""Render the SOURCE images for the phone app's icon and splash screen.

    pip install 'pillow' 'fonttools[woff]'
    python tools/icons/make-native-icons.py
    cd apps/web && npm run mobile:icons

The first command draws four large PNGs into apps/web/resources/; the second
(@capacitor/assets) cuts them into every size Android (and iOS) asks for and
writes them into the native projects. Same brandmark as the website's icons —
this imports make-app-icons.py's drawing code rather than copying it, so the
app and the website cannot drift apart.

  icon-only.png         1024  the icon, square and full bleed (older Android,
                              and iOS, which rounds it itself)
  icon-foreground.png   1024  the white R alone on transparent, for Android's
                              "adaptive" icons (the launcher masks it into a
                              circle, squircle, ... and may animate it)
  icon-background.png   1024  the gradient alone, the layer behind it
  splash.png            2732  the mark centred on the app's lilac background
"""

from __future__ import annotations

import importlib.util
import pathlib

from PIL import Image, ImageDraw

HERE = pathlib.Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("make_app_icons", HERE / "make-app-icons.py")
icons = importlib.util.module_from_spec(spec)
spec.loader.exec_module(icons)

OUT = icons.ROOT / "apps" / "web" / "resources"
#: The page wash, the same as manifest.webmanifest's background_color.
LILAC = (0xEC, 0xE4, 0xF5)


def foreground(size: int, letter_ratio: float) -> Image.Image:
    big = size * icons.SS
    tile = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    font = icons.load_font(icons.WOFF2, int(big * letter_ratio))
    draw = ImageDraw.Draw(tile)
    left, top, right, bottom = draw.textbbox((0, 0), "R", font=font)
    draw.text(
        ((big - (right - left)) / 2 - left, (big - (bottom - top)) / 2 - top),
        "R",
        font=font,
        fill=(255, 255, 255, 255),
    )
    return tile.resize((size, size), Image.LANCZOS)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    # Drawn once at 1024 and reused: the gradient is a per-pixel Python loop.
    full = icons.draw_mark(1024, rounded=False, letter_ratio=0.42, opaque=True)
    full.save(OUT / "icon-only.png", "PNG", optimize=True)
    icons.gradient(1024).save(OUT / "icon-background.png", "PNG", optimize=True)
    # Android's adaptive icon shows only the middle 66% of the layer safely.
    foreground(1024, 0.34).save(OUT / "icon-foreground.png", "PNG", optimize=True)

    splash = Image.new("RGB", (2732, 2732), LILAC)
    mark = icons.draw_mark(512, rounded=True, letter_ratio=0.56, opaque=False)
    splash.paste(mark, ((2732 - 512) // 2, (2732 - 512) // 2), mark)
    splash.save(OUT / "splash.png", "PNG", optimize=True)

    for name in ("icon-only.png", "icon-foreground.png", "icon-background.png", "splash.png"):
        print(f"{name:22} {(OUT / name).stat().st_size:>8} B")


if __name__ == "__main__":
    main()
