"""Shared primitives for the printed posters in docs/diagrams.

One kit, two posters (the AWS architecture and the tech-stack interaction map),
so a card looks the same on both and a fix to the geometry guard is a fix for
both. Everything is laid out in a viewBox where ONE UNIT IS 0.25 mm, i.e. an
A3 landscape sheet is exactly 1680 x 1188 — so a size in units divided by four
is millimetres, and body text at 10 units is 2.5 mm on paper.

The guard matters more than the drawing: a poster that quietly prints a card
short is worse than one that fails loudly, because nobody proof-reads a wall.
`Poster.save()` returns every card whose text would clip or that would fall off
the sheet, and the callers print them.
"""

import html

INK = "#111827"
MUTED = "#475569"
FAINT = "#64748b"
PAPER = "#ffffff"
WASH = "#f8fafc"

FONT = "Inter, 'Helvetica Neue', Helvetica, Arial, sans-serif"
MONO = "'DejaVu Sans Mono', 'SF Mono', Menlo, Consolas, monospace"


def esc(s):
    return html.escape(str(s), quote=False)


class Poster:
    """An A3 sheet under construction. Draw onto it, then save()."""

    def __init__(self, width=1680, height=1188, palette=()):
        self.w, self.h = width, height
        self.out = []
        self.warnings = []
        self.palette = list(dict.fromkeys(list(palette) + [INK, MUTED, FAINT]))
        self._open()

    # --- plumbing ---------------------------------------------------------
    def add(self, s):
        self.out.append(s)

    def _open(self):
        # One unit is 0.25 mm, so the physical size follows from the viewBox and
        # a portrait sheet needs no special case: 1680x1188 is A3 landscape,
        # 1188x1680 is A3 portrait, and both print at true size.
        self.add(f'<svg xmlns="http://www.w3.org/2000/svg" width="{self.w * 0.25:g}mm" '
                 f'height="{self.h * 0.25:g}mm" viewBox="0 0 {self.w} {self.h}">')
        self.add("<defs>")
        for c in self.palette:
            self.add(f'<marker id="a-{c.lstrip("#")}" viewBox="0 0 10 10" refX="9" refY="5" '
                     f'markerWidth="6.5" markerHeight="6.5" orient="auto-start-reverse">'
                     f'<path d="M0 0 L10 5 L0 10 z" fill="{c}"/></marker>')
        self.add("</defs>")
        self.add(f'<rect width="{self.w}" height="{self.h}" fill="{PAPER}"/>')

    def save(self, path):
        self.add("</svg>")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(self.out), encoding="utf-8")
        return self.warnings

    # --- primitives -------------------------------------------------------
    def text(self, x, y, s, size=11, fill=INK, weight=400, anchor="start", ls=0,
             style=None, family=None):
        extra = f' letter-spacing="{ls}"' if ls else ""
        extra += f' font-style="{style}"' if style else ""
        self.add(f'<text x="{x}" y="{y}" font-family="{family or FONT}" font-size="{size}" '
                 f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{extra}>'
                 f'{esc(s)}</text>')

    def box(self, x, y, w, h, title, lines, color, body=10.5, lh=13, pad=11, head=26,
            title_size=12.5):
        """A card: white body, coloured header bar, one line of text per entry.

        Line prefixes: '!' emphasised in the card's colour, '~' muted,
        '#' a small internal section rule, '>' monospaced (paths, code).
        """
        need = head + 16 + max(0, len(lines) - 1) * lh + 6
        if need > h:
            self.warnings.append(f"OVERFLOW {title!r}: needs {need:.0f}, has {h} (y={y})")
        if y + h > self.h - 4:
            self.warnings.append(f"OFF-CANVAS {title!r}: bottom {y + h} > {self.h}")
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="9" fill="{PAPER}" '
                 f'stroke="{color}" stroke-width="1.7"/>')
        self.add(f'<path d="M{x+9} {y} H{x+w-9} A9 9 0 0 1 {x+w} {y+9} V{y+head} H{x} '
                 f'V{y+9} A9 9 0 0 1 {x+9} {y} Z" fill="{color}"/>')
        self.text(x + pad, y + head - 8, title, title_size, PAPER, 700, ls=0.5)
        ty = y + head + 16
        for ln in lines:
            weight, fill, fam, t = 400, INK, None, ln
            if ln.startswith("!"):
                weight, fill, t = 700, color, ln[1:]
            elif ln.startswith("~"):
                fill, t = MUTED, ln[1:]
            elif ln.startswith("#"):
                weight, fill, t = 700, FAINT, ln[1:]
            elif ln.startswith(">"):
                fam, fill, t = MONO, MUTED, ln[1:]
            if t:
                self.text(x + pad, ty, t, body if fam is None else body - 0.6, fill, weight,
                          family=fam)
            ty += lh

    def container(self, x, y, w, h, label, color, label_size=12):
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{WASH}" '
                 f'stroke="{color}" stroke-width="1.6" stroke-dasharray="7 4"/>')
        self.chip(x + 14, y - 13, label, color, label_size)

    def chip(self, x, y, label, color, size=12):
        w = 20 + len(label) * size * 0.63
        self.add(f'<rect x="{x}" y="{y}" width="{w:.0f}" height="26" rx="13" fill="{color}"/>')
        self.text(x + 10, y + 18, label, size, PAPER, 700, ls=0.9)

    def arrow(self, pts, color=MUTED, width=2.4, dash=None):
        d = " ".join(f"{px},{py}" for px, py in pts)
        da = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="{width}"'
                 f'{da} marker-end="url(#a-{color.lstrip("#")})" stroke-linejoin="round"/>')

    def line(self, pts, color=MUTED, width=2.4, dash=None):
        d = " ".join(f"{px},{py}" for px, py in pts)
        da = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="{width}"'
                 f'{da} stroke-linejoin="round"/>')

    def numbered(self, cx, cy, n, color, r=10.5):
        """A numbered disc — the key that ties a wire to its legend entry."""
        self.add(f'<circle cx="{cx}" cy="{cy}" r="{r}" fill="{color}"/>')
        self.text(cx, cy + 4.2, str(n), 12, PAPER, 800, anchor="middle")

    def wire(self, x1, x2, y, n, color, protocol, payload=None, note=None, dash=None,
             back=False):
        """A labelled interaction: numbered disc, arrow, protocol over payload.

        This is the whole point of the stack map — an arrow that does not say
        what travels on it, in what shape, is decoration.
        """
        self.numbered(x1 + 12, y, n, color)
        self.arrow([(x1 + 24, y), (x2, y)], color, 2.3, dash)
        if back:
            self.arrow([(x2 - 24, y + 8), (x1 + 26, y + 8)], color, 1.6, "4 3")
        # Labels clear the stroke on BOTH sides: the protocol sits above the
        # line, the payload and note below the return arrow. Anything closer
        # and the arrow is drawn straight through the words it belongs to.
        mid = x1 + 26
        below = y + (21 if back else 14)
        self.text(mid, y - 8, protocol, 9.4, INK, 700)
        if payload:
            self.text(mid, below, payload, 9, MUTED)
        if note:
            self.text(mid, below + 11, note, 8.6, FAINT, style="italic")
