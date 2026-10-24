"""Render the poster SVGs in docs/diagrams/ to the PDF and PNG beside them.

The SVG is the source of truth (it is what the render_*.py scripts write); the
PDF is what gets printed at A3 and the PNG is the 150 dpi preview GitHub shows
inline. Both are derived here rather than drawn, so a wording change in
tools/diagrams/ reaches the printed sheet in one command:

    python tools/diagrams/export_print.py               # every *.svg in docs/diagrams
    python tools/diagrams/export_print.py reep-flow-a3  # one sheet, by stem

Headless Chromium does the layout, because the posters are plain SVG text and a
browser is what lays that text out with the font the SVG names (Inter), the
same way a reader's browser does; `pdftoppm` (poppler) rasterises the PDF. The
page size is read from the SVG's own width/height, so the portrait sheet comes
out portrait and the physical size needs no scaling. Exit codes follow
tools/ci/preflight.sh: 0 rendered, 1 a render failed, 2 a tool is missing — a
missing Chromium must never read as a refreshed poster.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DIAGRAMS = ROOT / "docs" / "diagrams"
DPI = 150
CHROME_CANDIDATES = ("chromium", "chromium-browser", "google-chrome", "chrome")
PLAYWRIGHT_CHROME = Path("/opt/pw-browsers/chromium")


def find_chrome() -> str | None:
    """CHROME in the environment wins; then PATH; then Playwright's install."""
    explicit = os.environ.get("CHROME")
    if explicit:
        return explicit
    for name in CHROME_CANDIDATES:
        found = shutil.which(name)
        if found:
            return found
    if PLAYWRIGHT_CHROME.exists():
        return str(PLAYWRIGHT_CHROME)
    return None


def page_size_mm(svg_text: str) -> tuple[str, str]:
    """The SVG root's width/height — '420mm', '297mm' — which become @page's size."""
    root = re.search(r"<svg\b[^>]*>", svg_text)
    if not root:
        raise ValueError("no <svg> root element")
    width = re.search(r'\bwidth="([^"]+)"', root.group(0))
    height = re.search(r'\bheight="([^"]+)"', root.group(0))
    if not width or not height:
        raise ValueError("the <svg> root carries no width/height — the poster kit always writes both")
    return width.group(1), height.group(1)


def wrap(svg_text: str, width: str, height: str) -> str:
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><style>"
        f"@page{{size:{width} {height};margin:0}}"
        "html,body{margin:0;padding:0}"
        f"svg{{display:block;width:{width};height:{height}}}"
        "</style></head><body>" + svg_text + "</body></html>"
    )


def render(svg: Path, chrome: str, workdir: Path) -> None:
    text = svg.read_text(encoding="utf-8")
    width, height = page_size_mm(text)
    page = workdir / f"{svg.stem}.html"
    page.write_text(wrap(text, width, height), encoding="utf-8")
    pdf = svg.with_suffix(".pdf")
    subprocess.run(
        [chrome, "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={pdf}", page.as_uri()],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    # -singlefile writes <stem>.png beside the SVG, with no page-number suffix.
    subprocess.run(
        ["pdftoppm", "-png", "-r", str(DPI), "-singlefile", str(pdf), str(svg.with_suffix(""))],
        check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    png = svg.with_suffix(".png")
    print(f"{svg.name}: {width} x {height} -> {pdf.name} ({pdf.stat().st_size // 1024} kB), "
          f"{png.name} ({png.stat().st_size // 1024} kB)")


def main(argv: list[str]) -> int:
    chrome = find_chrome()
    if chrome is None:
        print("export_print: no Chromium found — set CHROME=/path/to/chromium (SKIP)", file=sys.stderr)
        return 2
    if shutil.which("pdftoppm") is None:
        print("export_print: pdftoppm (poppler-utils) is not installed (SKIP)", file=sys.stderr)
        return 2
    stems = argv or sorted(p.stem for p in DIAGRAMS.glob("*.svg"))
    failed = 0
    with tempfile.TemporaryDirectory() as tmp:
        for stem in stems:
            svg = DIAGRAMS / f"{stem}.svg"
            if not svg.exists():
                print(f"export_print: {svg} does not exist", file=sys.stderr)
                failed += 1
                continue
            try:
                render(svg, chrome, Path(tmp))
            except (subprocess.CalledProcessError, ValueError) as exc:
                print(f"export_print: {svg.name} failed: {exc}", file=sys.stderr)
                failed += 1
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
