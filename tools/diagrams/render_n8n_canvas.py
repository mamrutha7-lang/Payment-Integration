#!/usr/bin/env python3
"""Print the n8n workflow's canvas, exactly as n8n draws it, to a PDF anyone can open.

`reep-roles-features-crud.n8n.json` is a diagram that only n8n can draw: in a
phone's file viewer, or on GitHub, the .json is code. This script asks a real
n8n to draw it, then cuts the canvas into pages a phone can show:

  1. Plan the pages. One column per role lane, plus the left-hand column
     (title, request path, AWS estate, schedules, backups, pipeline, tables).
     Each column is cut between feature areas, never through one, so every
     page holds whole areas.
  2. Render (tools/diagrams/render_n8n_canvas.cjs). n8n opens the workflow at
     100% zoom and prints each page's region as VECTOR PDF, so text stays sharp
     at any zoom. A fit-to-screen screenshot becomes the map on page 1.
  3. Assemble. Every page gets a title band, page 1 is a map whose boxes link
     to the pages, and the outline lists every lane and feature area.

Output: docs/diagrams/n8n/reep-n8n-canvas.pdf

With --tiles DIR it also photographs the whole canvas at 100% and writes a
deep-zoom tile pyramid (WebP tiles and manifest.json) that a pan-and-zoom
viewer can show, the way n8n's own canvas pans and zooms.

Needs a running n8n (any 2.x; this was made with 2.41.6, on Node 24, with
N8N_SECURE_COOKIE=false so its session cookie survives plain http) with an owner account,
Playwright's Chromium (`npm ci` at the root), and a Python with pypdf, Pillow and
reportlab (apps/api-py's venv has all three):

  N8N_EMAIL=you@example.com N8N_PASSWORD=... \\
    python tools/diagrams/render_n8n_canvas.py [--url http://127.0.0.1:5678] [--tiles DIR]

It imports the workflow as a new, inactive workflow each run. Pass
--workflow-id to reuse one already imported.
"""

from __future__ import annotations

import argparse
import io
import json
import math
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / "docs" / "diagrams" / "n8n" / "reep-roles-features-crud.n8n.json"
OUT = ROOT / "docs" / "diagrams" / "n8n" / "reep-n8n-canvas.pdf"
BROWSER = Path(__file__).with_name("render_n8n_canvas.cjs")

MARGIN = 20        # canvas units around a page's content
MAX_PAGE = 5000    # canvas units; areas are packed onto a page up to this height
BAND = 220         # px of title band above each page
PX = 0.75          # Chromium prints 1 CSS px as 0.75 pt
TILE = 2048        # deep-zoom tile size, px
CANVAS = (250, 250, 250)  # n8n's canvas colour with the dot grid hidden
INK, FAINT, BRAND = (0.11, 0.10, 0.18), (0.40, 0.39, 0.49), (0.33, 0.17, 0.49)
LEFT_COLUMN = "Request path, AWS and the data model"
LEFT_BAND = (0.93, 0.91, 0.97)  # the left column has no lane colour of its own


def sticky_box(n: dict) -> tuple[int, int, int, int]:
    x, y = n["position"]
    p = n["parameters"]
    return x, y, x + p.get("width", 240), y + p.get("height", 160)


def heading(n: dict) -> str:
    return n["parameters"].get("content", "").split("\n", 1)[0].lstrip("#").strip()


def pack(blocks: list[tuple[int, int, list[str]]], top: int, bottom: int) -> list[tuple[int, int, list[str]]]:
    """Cut a column of (y0, y1, names) blocks into as few pages as fit MAX_PAGE, as evenly as possible.

    A block taller than MAX_PAGE gets a page of its own. The first page starts at
    the column's top and the last ends at its bottom.
    """
    def height(i: int, j: int) -> int:  # blocks i..j-1 on one page
        return (bottom if j == len(blocks) else blocks[j - 1][1]) - (top if i == 0 else blocks[i][0])

    best: dict[tuple[int, int], tuple[int, list[int]]] = {}

    def split(i: int, pages: int) -> tuple[int, list[int]]:
        """The smallest possible tallest page for blocks i.. on this many pages, and where to cut."""
        if (i, pages) not in best:
            if pages == 1:
                best[i, pages] = (height(i, len(blocks)), [])
            else:
                best[i, pages] = min(((max(height(i, j), split(j, pages - 1)[0]), [j] + split(j, pages - 1)[1])
                                      for j in range(i + 1, len(blocks) - pages + 2)), default=(10 ** 9, []))
        return best[i, pages]

    tallest = max(height(i, i + 1) for i in range(len(blocks)))
    for n in range(1, len(blocks) + 1):
        worst, cuts = split(0, n)
        if worst <= max(MAX_PAGE, tallest):
            break
    edges = [0] + cuts + [len(blocks)]
    return [(top if a == 0 else blocks[a][0], bottom if b == len(blocks) else blocks[b - 1][1],
             [name for blk in blocks[a:b] for name in blk[2]]) for a, b in zip(edges, edges[1:])]


def plan(wf: dict) -> list[dict]:
    """Every page: its region in canvas units, the column it belongs to, and the areas on it."""
    stickies = [n for n in wf["nodes"] if n["type"].endswith("stickyNote")]
    pages: list[dict] = []

    # The left-hand column: sticky rows, merged where they share a band of y.
    left = sorted((n for n in stickies if n["position"][0] < 0), key=lambda n: n["position"][1])
    rows: list[list] = []
    for n in left:
        x0, y0, x1, y1 = sticky_box(n)
        if rows and y0 < rows[-1][1]:
            rows[-1][1] = max(rows[-1][1], y1)
            if not heading(n).startswith("*"):  # a note inside a section is not a section of its own
                rows[-1][2].append(heading(n))
            continue
        rows.append([y0, y1, [heading(n)]])
    lx0 = min(sticky_box(n)[0] for n in left)
    lx1 = max(sticky_box(n)[2] for n in left)
    for y0, y1, names in pack([tuple(r) for r in rows], rows[0][0], rows[-1][1]):
        pages.append(dict(column=LEFT_COLUMN,
                          x=lx0 - MARGIN, y=y0 - MARGIN, w=lx1 - lx0 + 2 * MARGIN, h=y1 - y0 + 2 * MARGIN,
                          areas=names))

    # One column per role lane, cut between its feature areas.
    lanes = sorted((n for n in stickies if n["position"][0] >= 0 and n["parameters"]["content"].startswith("# ")),
                   key=lambda n: n["position"][0])
    for lane in lanes:
        x0, y0, x1, y1 = sticky_box(lane)
        areas = sorted((n for n in stickies if n is not lane and x0 <= n["position"][0] < x1
                        and n["parameters"]["content"].startswith("## ")), key=lambda n: n["position"][1])
        blocks = [(sticky_box(a)[1], sticky_box(a)[3], [heading(a)]) for a in areas]
        title = heading(lane)
        for top, bottom, names in pack(blocks, y0, y1):
            pages.append(dict(column=title,
                              x=x0 - MARGIN, y=top - MARGIN, w=x1 - x0 + 2 * MARGIN, h=bottom - top + 2 * MARGIN,
                              areas=names))
    for i, p in enumerate(pages):
        p["name"] = f"page{i + 1:02d}"
    return pages


def content_boxes(wf: dict) -> list[tuple[int, int, int, int]]:
    """Generous boxes around everything n8n draws, edges included, to decide which tiles to photograph."""
    pos = {n["name"]: n["position"] for n in wf["nodes"]}
    boxes = [sticky_box(n) if n["type"].endswith("stickyNote") else
             (n["position"][0] - 60, n["position"][1] - 60, n["position"][0] + 260, n["position"][1] + 520)
             for n in wf["nodes"]]
    for src, outs in wf["connections"].items():
        for branch in outs.get("main", []):
            for c in branch or []:
                (ax, ay), (bx, by) = pos[src], pos[c["node"]]
                boxes.append((min(ax, bx), min(ay, by) - 60, max(ax, bx) + 120, max(ay, by) + 520))
    return boxes


def bounds(wf: dict) -> tuple[int, int, int, int]:
    boxes = [sticky_box(n) for n in wf["nodes"] if n["type"].endswith("stickyNote")]
    return (min(b[0] for b in boxes) - 100, min(b[1] for b in boxes) - 100,
            max(b[2] for b in boxes) + 100, max(b[3] for b in boxes) + 100)


def render(args, jobs: list[dict], out: Path) -> None:
    jobs_file = out / "jobs.json"
    jobs_file.write_text(json.dumps(jobs), encoding="utf-8")
    cmd = ["node", str(BROWSER), "--url", args.url, "--jobs", str(jobs_file), "--out", str(out)]
    cmd += ["--workflow-id", args.workflow_id] if args.workflow_id else ["--import", str(WORKFLOW)]
    subprocess.run(cmd, check=True)


def lane_colour(png: Path) -> tuple[float, float, float]:
    """The lane sticky's fill: the commonest colour in the strip down its left edge.

    One pixel is not enough: at the top of a lane it lands on the heading's ink.
    The strip, left of the lane's switch, holds nothing but the fill.
    """
    from PIL import Image
    with Image.open(png) as im:
        strip = im.convert("RGB").crop((MARGIN + 12, MARGIN + 150, MARGIN + 60, min(im.height - MARGIN, MARGIN + 1200)))
    colours = [c for c in strip.getcolors(strip.width * strip.height) if c[1] != CANVAS]
    r, g, b = max(colours)[1] if colours else CANVAS
    return r / 255, g / 255, b / 255


def fitted(text: str, font: str, size: float, width: float) -> float:
    """The largest font size up to `size` at which `text` fits in `width`."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    return min(size, size * width / max(stringWidth(text, font, size), 1))


def band(width_pt: float, height_pt: float, title: str, sub: str, folio: str, colour) -> tuple[bytes, tuple]:
    """A one-page PDF holding only the title band, drawn over the top of a page, and its "map" link."""
    from reportlab.pdfbase.pdfmetrics import stringWidth
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width_pt, height_pt))
    top = height_pt - BAND * PX
    c.setFillColorRGB(*colour)
    c.rect(0, top, width_pt, BAND * PX, stroke=0, fill=1)
    right = max(stringWidth(folio, "Helvetica", 30), stringWidth("Back to the map", "Helvetica-Bold", 30)) + 80
    c.setFillColorRGB(*INK)
    c.setFont("Helvetica-Bold", fitted(title, "Helvetica-Bold", 60, width_pt - right - 40))
    c.drawString(40, top + 86, title)
    c.setFillColorRGB(*FAINT)
    c.setFont("Helvetica", fitted(sub, "Helvetica", 30, width_pt - right - 40))
    c.drawString(40, top + 36, sub)
    c.setFont("Helvetica", 30)
    c.drawRightString(width_pt - 40, top + 96, folio)
    c.setFillColorRGB(*BRAND)
    c.setFont("Helvetica-Bold", 30)
    c.drawRightString(width_pt - 40, top + 40, "Back to the map")
    c.save()
    link = (width_pt - 40 - stringWidth("Back to the map", "Helvetica-Bold", 30) - 10, top + 28, width_pt - 30, top + 76)
    return buf.getvalue(), link


def cover(pages: list[dict], fit: dict, overview: Path, width: float) -> tuple[bytes, list]:
    """Page 1: the whole canvas with a numbered, linked box per page, then the contents."""
    from PIL import Image
    from reportlab.lib.utils import ImageReader, simpleSplit
    from reportlab.pdfgen import canvas
    k, tx, ty = fit["k"], fit["tx"], fit["ty"]
    # Crop n8n's fit-to-screen padding: keep the canvas, every page plus a margin.
    bx0 = min(p["x"] for p in pages) - 60
    by0 = min(p["y"] for p in pages) - 60
    bx1 = max(p["x"] + p["w"] for p in pages) + 60
    by1 = max(p["y"] + p["h"] for p in pages) + 60
    box = (round(bx0 * k + tx), round(by0 * k + ty), round(bx1 * k + tx), round(by1 * k + ty))
    with Image.open(overview) as im:
        cropped = im.convert("RGB").crop(box)
    img = ImageReader(cropped)
    iw, ih = cropped.size
    m = 60
    scale = (width - 2 * m) / iw
    title = "REEP: every role, feature and CRUD operation, as n8n draws it"
    intro = [
        "The n8n workflow reep-roles-features-crud.n8n.json, drawn by n8n at 100% zoom and cut into "
        f"{len(pages)} pages. Every page is vector, so the text stays sharp however far you zoom.",
        "Tap a numbered box below, or a line of the contents, to go to that page. Each page's band has a "
        "link back here.",
        "Each lane reads left to right: role, feature area, feature (its CREATE / READ / UPDATE / DELETE "
        "outputs), API call, FastAPI handler, tables and AWS services, then the feature card.",
    ]
    title_lines = simpleSplit(title, "Helvetica-Bold", 96, width - 2 * m)
    intro_lines = [simpleSplit(t, "Helvetica", 40, width - 2 * m) for t in intro]
    columns = len({p["column"] for p in pages})
    height = (m + len(title_lines) * 110 + 20 + sum(len(x) * 54 + 22 for x in intro_lines) + 40
              + ih * scale + 110 + columns * 70 + len(pages) * 52 + m)
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(width, height))
    y = height - m
    c.setFillColorRGB(*INK)
    c.setFont("Helvetica-Bold", 96)
    for line in title_lines:
        y -= 96
        c.drawString(m, y, line)
        y -= 14
    y -= 20
    c.setFillColorRGB(*FAINT)
    c.setFont("Helvetica", 40)
    for para in intro_lines:
        for line in para:
            y -= 44
            c.drawString(m, y, line)
            y -= 10
        y -= 22
    y -= 40
    img_top = y
    c.drawImage(img, m, img_top - ih * scale, iw * scale, ih * scale)
    links = []
    c.setStrokeColorRGB(*BRAND)
    c.setLineWidth(4)
    for n, p in enumerate(pages, start=2):
        x0 = m + (p["x"] * k + tx - box[0]) * scale
        x1 = m + ((p["x"] + p["w"]) * k + tx - box[0]) * scale
        y1 = img_top - (p["y"] * k + ty - box[1]) * scale
        y0 = img_top - ((p["y"] + p["h"]) * k + ty - box[1]) * scale
        c.rect(x0 + 3, y0 + 3, x1 - x0 - 6, y1 - y0 - 6, stroke=1, fill=0)
        c.setFillColorRGB(*BRAND)
        c.roundRect(x0 + 10, y1 - 70, 84, 58, 10, stroke=0, fill=1)
        c.setFillColorRGB(1, 1, 1)
        c.setFont("Helvetica-Bold", 40)
        c.drawCentredString(x0 + 52, y1 - 55, str(n))
        links.append(((x0, y0, x1, y1), n - 1))
    y = img_top - ih * scale - 110
    c.setFillColorRGB(*INK)
    c.setFont("Helvetica-Bold", 60)
    c.drawString(m, y, "Contents")
    column = None
    for n, p in enumerate(pages, start=2):
        if p["column"] != column:
            column = p["column"]
            y -= 70
            c.setFillColorRGB(*INK)
            c.setFont("Helvetica-Bold", fitted(column, "Helvetica-Bold", 42, width - 2 * m))
            c.drawString(m, y, column)
        y -= 52
        text = "; ".join(p["areas"])
        c.setFillColorRGB(*FAINT)
        c.setFont("Helvetica", fitted(text, "Helvetica", 34, width - 2 * m - 260))
        c.drawString(m + 40, y, text)
        c.setFillColorRGB(*BRAND)
        c.setFont("Helvetica-Bold", 34)
        c.drawRightString(width - m, y, f"page {n}")
        links.append(((m, y - 14, width - m, y + 38), n - 1))
    c.setTitle(title)
    c.save()
    return buf.getvalue(), links


def add_link(writer, page_index: int, rect: tuple, target: int) -> None:
    """Add a link from one page of this file to another.

    pypdf's Link(target_page_index=...) writes the page NUMBER into /Dest, which
    is only valid for a link into another file; viewers may ignore it. A link
    within the file names the page object itself, so the annotation is built here
    and appended to the page's /Annots directly (add_annotation rewrites /Dest).
    """
    from pypdf.generic import ArrayObject, DictionaryObject, FloatObject, NameObject, NumberObject
    annotation = DictionaryObject({
        NameObject("/Type"): NameObject("/Annot"),
        NameObject("/Subtype"): NameObject("/Link"),
        NameObject("/Rect"): ArrayObject([FloatObject(v) for v in rect]),
        NameObject("/Border"): ArrayObject([NumberObject(0)] * 3),
        NameObject("/Dest"): ArrayObject([writer.pages[target].indirect_reference, NameObject("/Fit")]),
    })
    page = writer.pages[page_index]
    if "/Annots" not in page:
        page[NameObject("/Annots")] = ArrayObject()
    page["/Annots"].append(writer._add_object(annotation))


def assemble(pages: list[dict], work: Path, fit: dict) -> None:
    from pypdf import PageObject, PdfReader, PdfWriter, Transformation
    writer = PdfWriter()
    links: list[tuple[int, tuple, int]] = []
    widths = [p["w"] * PX for p in pages]
    cover_pdf, cover_links = cover(pages, fit, work / "overview.png", max(widths))
    writer.add_page(PdfReader(io.BytesIO(cover_pdf)).pages[0])
    total = len(pages) + 1
    for n, p in enumerate(pages, start=2):
        crop = json.loads((work / f"{p['name']}.crop.json").read_text(encoding="utf-8"))
        src = PdfReader(work / f"{p['name']}.raw.pdf").pages[0]
        w, h = crop["w"] * PX, crop["h"] * PX
        page = PageObject.create_blank_page(width=w, height=h + BAND * PX)
        # Move the canvas region to the bottom-left of the new page; the title band goes above it.
        x0, y0 = crop["left"] * PX, (crop["H"] - crop["top"] - crop["h"]) * PX
        page.merge_transformed_page(src, Transformation().translate(-x0, -y0))
        same = [q for q in pages if q["column"] == p["column"]]
        part = f" · {same.index(p) + 1} of {len(same)}" if len(same) > 1 else ""
        colour = LEFT_BAND if p["column"] == LEFT_COLUMN else lane_colour(work / f"{p['name']}.png")
        overlay, back = band(w, h + BAND * PX, p["column"] + part, " · ".join(p["areas"]), f"page {n} of {total}",
                             colour)
        page.merge_page(PdfReader(io.BytesIO(overlay)).pages[0])
        writer.add_page(page).compress_content_streams()
        links.append((n - 1, back, 0))
    for page_index, rect, target in links + [(0, rect, target) for rect, target in cover_links]:
        add_link(writer, page_index, rect, target)
    writer.add_outline_item("Map of the canvas", 0)
    column, parent = None, None
    for n, p in enumerate(pages, start=1):
        if p["column"] != column:
            column = p["column"]
            parent = writer.add_outline_item(column, n)
        writer.add_outline_item("; ".join(p["areas"]), n, parent=parent)
    writer.add_metadata({"/Title": "REEP: every role, feature and CRUD operation, as n8n draws it",
                         "/Subject": "docs/diagrams/n8n/reep-roles-features-crud.n8n.json rendered by n8n"})
    writer.page_mode = "/UseOutlines"
    writer.compress_identical_objects(remove_identicals=True, remove_unreferenced=True)
    with OUT.open("wb") as fh:
        writer.write(fh)
    print(f"wrote {OUT.relative_to(ROOT)} ({len(pages) + 1} pages, {OUT.stat().st_size // 1024} KB)")


def tiles(pages: list[dict], work: Path, dest: Path, box: tuple[int, int, int, int]) -> None:
    """Stitch the 100% photographs into one canvas and cut a deep-zoom pyramid from it."""
    from PIL import Image, ImageChops
    Image.MAX_IMAGE_PIXELS = None
    x0, y0, x1, y1 = box
    width, height = x1 - x0, y1 - y0
    full = Image.new("RGB", (width, height), CANVAS)
    for png in work.glob("shot_*.png"):
        _, col, row = png.stem.split("_")
        with Image.open(png) as im:
            full.paste(im.convert("RGB"), (int(col) * TILE, int(row) * TILE))
    top = math.ceil(math.log2(max(width, height)))
    dest.mkdir(parents=True, exist_ok=True)
    present, level, im = [], top, full
    while True:
        cols, rows = math.ceil(im.width / TILE), math.ceil(im.height / TILE)
        (dest / str(level)).mkdir(exist_ok=True)
        for c in range(cols):
            for r in range(rows):
                tile = im.crop((c * TILE, r * TILE, min((c + 1) * TILE, im.width), min((r + 1) * TILE, im.height)))
                if ImageChops.difference(tile, Image.new("RGB", tile.size, CANVAS)).getbbox() is None:
                    continue
                tile.save(dest / str(level) / f"{c}_{r}.webp", "WEBP", quality=88, method=6)
                present.append(f"{level}/{c}_{r}")
        if cols == 1 and rows == 1:
            break
        im = im.reduce(2)
        level -= 1
    manifest = dict(width=width, height=height, tileSize=TILE, maxLevel=top, minLevel=level, origin=[x0, y0],
                    background="#%02x%02x%02x" % CANVAS, tiles=present,
                    regions=[dict(page=n, column=p["column"], areas=p["areas"],
                                  rect=[p["x"] - x0, p["y"] - y0, p["w"], p["h"]])
                             for n, p in enumerate(pages, start=2)])
    (dest / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    full.reduce(8).save(dest / "overview.png", optimize=True)
    print(f"wrote {len(present)} tiles to {dest} (levels {level}..{top})")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--url", default="http://127.0.0.1:5678", help="the running n8n")
    ap.add_argument("--workflow-id", help="reuse a workflow already imported into that n8n")
    ap.add_argument("--tiles", type=Path, help="also write a deep-zoom tile pyramid to this directory")
    ap.add_argument("--work", type=Path, help="keep the renders in this directory")
    args = ap.parse_args()

    wf = json.loads(WORKFLOW.read_text(encoding="utf-8"))
    pages = plan(wf)
    box = bounds(wf)
    jobs = [dict(name=p["name"], x=p["x"], y=p["y"], w=p["w"], h=p["h"], pdf=True) for p in pages]
    if args.tiles:
        boxes = content_boxes(wf)
        x0, y0, x1, y1 = box
        for col in range(math.ceil((x1 - x0) / TILE)):
            for row in range(math.ceil((y1 - y0) / TILE)):
                tx, ty = x0 + col * TILE, y0 + row * TILE
                tw, th = min(TILE, x1 - tx), min(TILE, y1 - ty)
                if any(b[0] < tx + tw and b[2] > tx and b[1] < ty + th and b[3] > ty for b in boxes):
                    jobs.append(dict(name=f"shot_{col}_{row}", x=tx, y=ty, w=tw, h=th, pdf=False))
    jobs.append(dict(name="overview", fit=True, w=2400, h=2400 * (box[3] - box[1]) // (box[2] - box[0])))

    with tempfile.TemporaryDirectory() as tmp:
        work = args.work or Path(tmp)
        work.mkdir(parents=True, exist_ok=True)
        render(args, jobs, work)
        fit = json.loads((work / "overview.json").read_text(encoding="utf-8"))
        assemble(pages, work, fit)
        if args.tiles:
            tiles(pages, work, args.tiles, box)
    return 0


if __name__ == "__main__":
    sys.exit(main())
