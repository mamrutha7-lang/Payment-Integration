"""Flat vector icons for the flow poster.

Deliberately ORIGINAL simplified glyphs — a cylinder for a database, a stack of
boxes for containers, a capsule for a microphone — drawn in each vendor's
familiar colour and captioned with the product name. They read at a glance on a
wall without reproducing anyone's trademarked logo artwork, which a generated
poster has no licence to redraw.

Every icon draws centred on (cx, cy) inside a box of side `s`, so the caller
places icons on a grid and never thinks about internal geometry.
"""


def _capsule_person(p, cx, top, w, h, color):
    r = w / 2
    p.add(f'<circle cx="{cx}" cy="{top}" r="{r * 0.78}" fill="{color}"/>')
    y0 = top + r * 1.15
    p.add(f'<path d="M{cx - r} {y0 + h} v-{h * 0.45} a{r} {r} 0 0 1 {w} 0 v{h * 0.45} z" '
          f'fill="{color}"/>')


def people(p, cx, cy, s, color):
    _capsule_person(p, cx - s * 0.30, cy - s * 0.10, s * 0.26, s * 0.34, color)
    _capsule_person(p, cx + s * 0.30, cy - s * 0.10, s * 0.26, s * 0.34, color)
    _capsule_person(p, cx, cy - s * 0.20, s * 0.34, s * 0.42, color)


def browser(p, cx, cy, s, color):
    x, y, w, h = cx - s / 2, cy - s * 0.40, s, s * 0.80
    p.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="7" fill="none" '
          f'stroke="{color}" stroke-width="5"/>')
    p.add(f'<path d="M{x} {y + h * 0.26} H{x + w}" stroke="{color}" stroke-width="5"/>')
    for i in range(3):
        p.add(f'<circle cx="{x + 14 + i * 13}" cy="{y + h * 0.13}" r="3.6" fill="{color}"/>')
    gx, gy, gr = cx, cy + s * 0.12, s * 0.20
    p.add(f'<circle cx="{gx}" cy="{gy}" r="{gr}" fill="none" stroke="{color}" stroke-width="4"/>')
    p.add(f'<path d="M{gx - gr} {gy} H{gx + gr} M{gx} {gy - gr} q{gr * 0.75} {gr} 0 {gr * 2} '
          f'M{gx} {gy - gr} q-{gr * 0.75} {gr} 0 {gr * 2}" fill="none" stroke="{color}" '
          f'stroke-width="3.4"/>')


def cloud(p, cx, cy, s, color):
    p.add(f'<path d="M{cx - s * 0.42} {cy + s * 0.16} a{s * 0.17} {s * 0.17} 0 0 1 {s * 0.04} '
          f'-{s * 0.33} a{s * 0.22} {s * 0.22} 0 0 1 {s * 0.42} -{s * 0.10} a{s * 0.18} '
          f'{s * 0.18} 0 0 1 {s * 0.38} {s * 0.14} a{s * 0.15} {s * 0.15} 0 0 1 -{s * 0.04} '
          f'{s * 0.29} z" fill="{color}"/>')
    p.add(f'<path d="M{cx} {cy + s * 0.20} l{s * 0.20} {s * 0.07} v{s * 0.12} q0 {s * 0.14} '
          f'-{s * 0.20} {s * 0.20} q-{s * 0.20} -{s * 0.06} -{s * 0.20} -{s * 0.20} '
          f'v-{s * 0.12} z" fill="#1f2937"/>')


def bucket(p, cx, cy, s, color):
    top, bot = cy - s * 0.30, cy + s * 0.34
    p.add(f'<path d="M{cx - s * 0.36} {top} L{cx - s * 0.26} {bot} H{cx + s * 0.26} '
          f'L{cx + s * 0.36} {top} z" fill="{color}"/>')
    p.add(f'<ellipse cx="{cx}" cy="{top}" rx="{s * 0.36}" ry="{s * 0.10}" fill="#1f2937" '
          f'opacity="0.25"/>')
    p.add(f'<ellipse cx="{cx}" cy="{top}" rx="{s * 0.36}" ry="{s * 0.10}" fill="none" '
          f'stroke="{color}" stroke-width="4"/>')


def balancer(p, cx, cy, s, color):
    p.add(f'<rect x="{cx - s * 0.44}" y="{cy - s * 0.14}" width="{s * 0.30}" '
          f'height="{s * 0.28}" rx="6" fill="{color}"/>')
    for dy in (-s * 0.26, 0, s * 0.26):
        p.add(f'<path d="M{cx - s * 0.14} {cy} H{cx + s * 0.10} V{cy + dy} H{cx + s * 0.24}" '
              f'fill="none" stroke="{color}" stroke-width="4.5"/>')
        p.add(f'<circle cx="{cx + s * 0.32}" cy="{cy + dy}" r="{s * 0.09}" fill="{color}"/>')


def containers(p, cx, cy, s, color):
    for i, (dx, dy, o) in enumerate([(-0.16, -0.20, 0.45), (0.06, -0.06, 0.7), (-0.06, 0.16, 1)]):
        p.add(f'<rect x="{cx + s * dx - s * 0.24}" y="{cy + s * dy - s * 0.14}" '
              f'width="{s * 0.48}" height="{s * 0.28}" rx="6" fill="{color}" opacity="{o}"/>')


def database(p, cx, cy, s, color):
    rx, top, h = s * 0.34, cy - s * 0.32, s * 0.60
    p.add(f'<path d="M{cx - rx} {top} v{h} a{rx} {s * 0.12} 0 0 0 {rx * 2} 0 v-{h} z" '
          f'fill="{color}"/>')
    p.add(f'<ellipse cx="{cx}" cy="{top}" rx="{rx}" ry="{s * 0.12}" fill="{color}"/>')
    for i in (1, 2):
        p.add(f'<path d="M{cx - rx} {top + h * i / 3} a{rx} {s * 0.12} 0 0 0 {rx * 2} 0" '
              f'fill="none" stroke="#ffffff" stroke-width="3.5" opacity="0.75"/>')


def drive(p, cx, cy, s, color):
    p.add(f'<rect x="{cx - s * 0.40}" y="{cy - s * 0.30}" width="{s * 0.80}" '
          f'height="{s * 0.26}" rx="6" fill="{color}"/>')
    p.add(f'<rect x="{cx - s * 0.40}" y="{cy + s * 0.04}" width="{s * 0.80}" '
          f'height="{s * 0.26}" rx="6" fill="{color}" opacity="0.62"/>')
    for dy in (-s * 0.17, s * 0.17):
        p.add(f'<circle cx="{cx + s * 0.26}" cy="{cy + dy}" r="{s * 0.045}" fill="#ffffff"/>')


def chip(p, cx, cy, s, color):
    half = s * 0.28
    for i in range(3):
        off = (i - 1) * s * 0.18
        for dx, dy, w, h in ((0, -half - s * 0.10, s * 0.06, s * 0.10),
                             (0, half, s * 0.06, s * 0.10)):
            p.add(f'<rect x="{cx + off - w / 2}" y="{cy + dy}" width="{w}" height="{h}" '
                  f'fill="{color}"/>')
        for dx, dy, w, h in ((-half - s * 0.10, 0, s * 0.10, s * 0.06),
                             (half, 0, s * 0.10, s * 0.06)):
            p.add(f'<rect x="{cx + dx}" y="{cy + off - h / 2}" width="{w}" height="{h}" '
                  f'fill="{color}"/>')
    p.add(f'<rect x="{cx - half}" y="{cy - half}" width="{half * 2}" height="{half * 2}" '
          f'rx="7" fill="{color}"/>')
    p.add(f'<circle cx="{cx}" cy="{cy}" r="{s * 0.11}" fill="#ffffff"/>')


def mic(p, cx, cy, s, color):
    p.add(f'<rect x="{cx - s * 0.13}" y="{cy - s * 0.36}" width="{s * 0.26}" '
          f'height="{s * 0.44}" rx="{s * 0.13}" fill="{color}"/>')
    p.add(f'<path d="M{cx - s * 0.26} {cy - s * 0.02} a{s * 0.26} {s * 0.26} 0 0 0 {s * 0.52} 0" '
          f'fill="none" stroke="{color}" stroke-width="5"/>')
    p.add(f'<path d="M{cx} {cy + s * 0.24} v{s * 0.12} M{cx - s * 0.16} {cy + s * 0.36} '
          f'H{cx + s * 0.16}" stroke="{color}" stroke-width="5"/>')


def codebranch(p, cx, cy, s, color):
    lx, rx = cx - s * 0.18, cx + s * 0.18
    p.add(f'<path d="M{lx} {cy - s * 0.24} V{cy + s * 0.24}" stroke="{color}" stroke-width="5"/>')
    p.add(f'<path d="M{rx} {cy - s * 0.14} v{s * 0.10} q0 {s * 0.16} -{s * 0.36} {s * 0.16}" '
          f'fill="none" stroke="{color}" stroke-width="5"/>')
    for x, y in ((lx, cy - s * 0.30), (lx, cy + s * 0.30), (rx, cy - s * 0.22)):
        p.add(f'<circle cx="{x}" cy="{y}" r="{s * 0.10}" fill="{color}"/>')


def pipeline(p, cx, cy, s, color):
    p.add(f'<circle cx="{cx}" cy="{cy}" r="{s * 0.30}" fill="none" stroke="{color}" '
          f'stroke-width="6"/>')
    p.add(f'<path d="M{cx - s * 0.08} {cy - s * 0.13} l{s * 0.22} {s * 0.13} '
          f'l-{s * 0.22} {s * 0.13} z" fill="{color}"/>')
    for i in range(4):
        a = i * 90 + 45
        import math
        rad = math.radians(a)
        p.add(f'<circle cx="{cx + math.cos(rad) * s * 0.42}" cy="{cy + math.sin(rad) * s * 0.42}" '
              f'r="{s * 0.05}" fill="{color}"/>')


def registry(p, cx, cy, s, color):
    p.add(f'<rect x="{cx - s * 0.36}" y="{cy - s * 0.32}" width="{s * 0.72}" '
          f'height="{s * 0.64}" rx="8" fill="none" stroke="{color}" stroke-width="5"/>')
    for dx in (-0.16, 0.16):
        for dy in (-0.14, 0.14):
            p.add(f'<rect x="{cx + s * dx - s * 0.09}" y="{cy + s * dy - s * 0.09}" '
                  f'width="{s * 0.18}" height="{s * 0.18}" rx="3" fill="{color}"/>')


def key(p, cx, cy, s, color):
    p.add(f'<circle cx="{cx - s * 0.20}" cy="{cy}" r="{s * 0.18}" fill="none" stroke="{color}" '
          f'stroke-width="7"/>')
    p.add(f'<path d="M{cx - s * 0.03} {cy} H{cx + s * 0.38}" stroke="{color}" stroke-width="7"/>')
    p.add(f'<path d="M{cx + s * 0.20} {cy} v{s * 0.14} M{cx + s * 0.34} {cy} v{s * 0.14}" '
          f'stroke="{color}" stroke-width="7"/>')


def bug(p, cx, cy, s, color):
    p.add(f'<ellipse cx="{cx}" cy="{cy + s * 0.04}" rx="{s * 0.22}" ry="{s * 0.28}" '
          f'fill="{color}"/>')
    p.add(f'<circle cx="{cx}" cy="{cy - s * 0.28}" r="{s * 0.13}" fill="{color}"/>')
    for sgn in (-1, 1):
        for dy in (-0.10, 0.04, 0.18):
            p.add(f'<path d="M{cx + sgn * s * 0.20} {cy + s * dy} l{sgn * s * 0.18} '
                  f'{s * 0.08}" stroke="{color}" stroke-width="5"/>')
        p.add(f'<path d="M{cx + sgn * s * 0.08} {cy - s * 0.36} l{sgn * s * 0.12} '
              f'-{s * 0.12}" stroke="{color}" stroke-width="5"/>')


def bars(p, cx, cy, s, color):
    for i, h in enumerate((0.28, 0.48, 0.66)):
        p.add(f'<rect x="{cx - s * 0.34 + i * s * 0.24}" y="{cy + s * 0.32 - s * h}" '
              f'width="{s * 0.16}" height="{s * h}" rx="4" fill="{color}" '
              f'opacity="{0.55 + i * 0.22}"/>')


def clock(p, cx, cy, s, color):
    p.add(f'<circle cx="{cx}" cy="{cy}" r="{s * 0.34}" fill="none" stroke="{color}" '
          f'stroke-width="6"/>')
    p.add(f'<path d="M{cx} {cy - s * 0.20} V{cy} H{cx + s * 0.18}" fill="none" '
          f'stroke="{color}" stroke-width="5.5" stroke-linecap="round"/>')


def doc(p, cx, cy, s, color):
    x, y, w, h, f = cx - s * 0.28, cy - s * 0.36, s * 0.56, s * 0.72, s * 0.18
    p.add(f'<path d="M{x} {y} H{x + w - f} L{x + w} {y + f} V{y + h} H{x} z" fill="none" '
          f'stroke="{color}" stroke-width="5"/>')
    p.add(f'<path d="M{x + w - f} {y} V{y + f} H{x + w}" fill="none" stroke="{color}" '
          f'stroke-width="5"/>')
    for i in range(3):
        p.add(f'<path d="M{x + s * 0.08} {y + h * 0.45 + i * s * 0.12} H{x + w - s * 0.08}" '
              f'stroke="{color}" stroke-width="4"/>')
