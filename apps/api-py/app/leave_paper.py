"""The leave paper: the official BGSCET form, with the request written onto it.

THE FORM IS THE COLLEGE'S OWN PDF, unchanged. `assets/leave_form_template.pdf`
is the file the office hands out ("Leave Form.pdf", 84 718 bytes, Letter, one
page). This module never redraws it: it renders a transparent overlay with
ReportLab - the values in the table cells, the date in its gap, the options
that do not apply struck through, the signatures above their labels, the
director's mark beside "Sanctioned", the alternate arrangements in their
rows - and pypdf merges that overlay onto the template page. What comes out
is the form, filled in. The coordinates below are MEASURED from this file
(pymupdf, top-left origin, points) and the file's size is pinned in tests, so
a swapped template with a different layout fails loudly rather than printing
values into the wrong boxes.

Runs entirely on this machine - no network, no model - like app/english_report.py
and app/resume_pdf.py; keep it that way.

A SIGNATURE IS STILL A NAME AND A TIME. The uploaded image (app/models/staff_signature.py)
is drawn ABOVE the printed label, and the name and time are printed in small
type beneath it, so an image never stands without the record of who and when.
No image on file prints the name and time alone; nothing signed prints
"Not signed" / "Awaiting", as the screen does.

B10.8 ADDS THE FUNCTION TO THAT LINE AND NOTHING ELSE TO THIS FILE. The
attestation is a name, then the function the signer was acting in, then the
time - "Asha Rao . Mentor . 10 Sep 2026, 09:00" - and it is drawn where it was
already drawn, in blank margin, in 8pt muted type. It is NOT part of the form.

TWO THINGS B10.8 IS WRITTEN AS ASKING FOR AND MUST NOT DO:

  * "print the function INSTEAD OF a fixed PROGRAM DIRECTOR label". Those two
    words are PRINTED ON THE COLLEGE'S OWN PDF. Replacing them means whiting the
    block out and reprinting over it - defacing the office's form - and
    `tests/test_leave_paper.py` asserts "PROGRAM" survives in the extracted
    text. The label stays; the attestation beneath it says who actually signed.
  * "refuse to render a decided request whose signer has no signature image
    (422)". That contradicts the paragraph above, contradicts
    `tests/test_leave_paper.py`'s explicit assertion that deleting a signature
    still returns 200, and would refuse an APPLICANT their own sanctioned leave
    because a DIFFERENT person never uploaded a PNG. An image is decoration on
    top of a record that already exists. If the office wants pressure on
    signers, that belongs in the console, not in a download.

A function is printed ONLY where one was recorded. Every row decided before
`leave_requests.first_signed_as` existed has NULL there, and NULL prints the
line exactly as it printed before - never a function guessed from who the
signer is today, which would put a job title on a signed form on the strength
of a mentor group that may have changed since.
"""

from __future__ import annotations

import html
import io
import logging
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import Paragraph

from .models.leave import SIGNED_AS_DELEGATE, SIGNED_AS_MAIN_ADMIN, SIGNED_AS_MENTOR

log = logging.getLogger(__name__)


def _renormalised(content: bytes) -> bytes | None:
    """`content` re-encoded as a flat RGBA PNG through Pillow, EXIF rotation
    applied, or None when Pillow cannot read it either."""
    try:
        from PIL import Image, ImageOps

        with Image.open(io.BytesIO(content)) as image:
            image = ImageOps.exif_transpose(image) or image
            flat = image.convert("RGBA")
        out = io.BytesIO()
        flat.save(out, format="PNG")
        return out.getvalue()
    except Exception as exc:  # noqa: BLE001
        log.warning("leave paper: Pillow could not re-encode the signature image: %s", exc)
        return None

TEMPLATE = Path(__file__).resolve().parent / "assets" / "leave_form_template.pdf"
TEMPLATE_BYTES = 84718  # pinned in tests: the layout below is measured from this exact file

PAGE_W, PAGE_H = 612.0, 792.0  # Letter

# --- measured geometry, top-left origin -----------------------------------
# The main table's value column, and each row's top/bottom.
VALUE_X0, VALUE_X1 = 181.4, 553.4
ROWS: dict[str, tuple[float, float]] = {
    "name": (188.7, 219.1),
    "designation": (219.6, 250.0),
    "department": (250.5, 281.0),
    "date": (281.5, 311.8),
    "purpose": (312.3, 342.8),
    "credit": (343.2, 373.6),
}
# "Date:" ends at 440.4 and the pre-printed "2026" starts at 489.3, baseline ~177.
DATE_GAP_X, DATE_BASELINE = 446.0, 177.0
YEAR_BOX = (488.0, 163.5, 520.0, 181.8)  # whited out only when the year is not 2026
# The five printed options on the "Application for" line, and where to strike.
OPTIONS: dict[str, tuple[float, float]] = {
    "CASUAL": (158.3, 231.4),
    "PERMISSION": (235.2, 295.2),
    "OOD": (298.8, 328.1),
    "RH": (491.4, 510.0),
    "LOP": (513.8, 539.3),
}
OPTIONS_STRIKE_Y = 149.8
# "Sanctioned", the director's mark, right of centre below the table.
SANCTIONED = (445.5, 393.1, 512.2, 409.8)
# The two signature labels: images go above them, attestations beneath.
STAFF_LABEL_TOP, STAFF_X0 = 442.6, 72.0
DIRECTOR_LABEL_TOP, DIRECTOR_LABEL_BOTTOM, DIRECTOR_X0, DIRECTOR_X1 = 442.6, 474.2, 444.5, 553.0
# Alternate arrangements: the dotted name line, and the table.
ALT_NAME_DOTS = (112.9, 556.0, 361.5, 571.0)
ALT_NAME_X, ALT_NAME_BASELINE = 116.0, 567.0
ALT_COLS: list[tuple[float, float]] = [(66.9, 152.8), (153.3, 278.8), (279.3, 368.8), (369.3, 449.5), (450.0, 545.3)]
ALT_GRID_X = (66.4, 152.8, 278.8, 368.8, 449.5, 545.3)
ALT_ROW_TOPS = (620.1, 644.7)  # the two printed rows
ALT_ROW_PITCH = 24.6
ALT_TABLE_BOTTOM = 669.3
# "Signature of staff" at the foot, right.
FOOT_LABEL_TOP, FOOT_LABEL_BOTTOM, FOOT_X0, FOOT_X1 = 718.3, 733.8, 415.0, 553.0

INK = colors.HexColor("#111111")
MUTED = colors.HexColor("#555555")
BODY_FONT = "Times-Roman"

SANCTIONED_WORDS: dict[str, str] = {
    "REJECTED": "Not sanctioned",
    "FIRST_APPROVED": "Pending",
    "SUBMITTED": "Pending",
    "CANCELLED": "Cancelled",
    "REVOKED": "Sanction revoked",
}

#: How each stored function reads on the paper (B10.8). The keys are
#: `app/models/leave.py`'s SIGNED_AS tuple and `tests/test_leave_chain.py`
#: compares the two sets, so a fourth function cannot be stamped on a row
#: without a word for it here - the alternative is a form that prints a raw
#: database token at somebody's signature.
#:
#: "Delegated approver" and not "HOD": there is no HOD account in this product
#: (`departments.head` is free text) and no principal. What DELEGATE records is
#: a `mentor.leave_approve` grant the office made in Governance, scoped to a
#: department or a college - which is a real, revocable, audited decision, and
#: the nearest true thing to what 04-backend-changes.md asked for.
SIGNED_AS_LABELS: dict[str, str] = {
    SIGNED_AS_MENTOR: "Mentor",
    SIGNED_AS_DELEGATE: "Delegated approver",
    SIGNED_AS_MAIN_ADMIN: "Main Admin",
}


# ------------------------------------------------------------ helpers --


def _y(top_left_y: float) -> float:
    """Measured (top-left) y -> ReportLab (bottom-left) y."""
    return PAGE_H - top_left_y


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _when(value: datetime | None, with_time: bool = True) -> str:
    """"9 Sep 2026, 21:20" - portable strftime codes only, leading zero stripped."""
    if value is None:
        return "-"
    local = value.astimezone(timezone.utc) if value.tzinfo else value
    text = local.strftime("%d %b %Y, %H:%M" if with_time else "%d %b %Y")
    return text.lstrip("0")


def _date_span(a: date, b: date) -> str:
    return a.isoformat() if a == b else f"{a.isoformat()} to {b.isoformat()}"


def _fit(c: canvas.Canvas, text: str, x0: float, top: float, x1: float, bottom: float,
         *, size: float = 12.0, min_size: float = 8.0, pad: float = 4.0) -> None:
    """Write `text` inside a measured box, wrapping, and shrinking the type
    until it fits; vertically centred. A cell never overflows its lines."""
    if not text:
        return
    width = (x1 - x0) - 2 * pad
    height = (bottom - top) - 2
    s = size
    while True:
        style = ParagraphStyle("cell", fontName=BODY_FONT, fontSize=s, leading=s * 1.15, textColor=INK)
        p = Paragraph(_esc(text), style)
        _w, h = p.wrap(width, height)
        if h <= height or s <= min_size:
            break
        s -= 1
    p.drawOn(c, x0 + pad, _y(bottom) + (height - h) / 2 + 1)


def _text(c: canvas.Canvas, x: float, baseline: float, text: str, *, size: float = 12.0,
          font: str = BODY_FONT, color=INK, right: bool = False) -> None:
    c.setFont(font, size)
    c.setFillColor(color)
    if right:
        c.drawRightString(x, _y(baseline), text)
    else:
        c.drawString(x, _y(baseline), text)


def _function_label(value: Any) -> str | None:
    """The printed word for a recorded signing function, or None for none.

    None is the state of every request decided before `first_signed_as` existed
    and of every step nobody has signed, and it must print as nothing at all -
    see the module docstring. An unrecognised value prints itself rather than
    disappearing: a token nobody has a word for is a bug to be seen, not a fact
    to be dropped off a signed form.
    """
    text = str(value or "").strip()
    if not text:
        return None
    return SIGNED_AS_LABELS.get(text, text.replace("_", " ").title())


def _attest(c: canvas.Canvas, x: float, baseline: float, name: Any, function: Any, when: str,
            *, size: float, right_edge: float = 585.0) -> None:
    """The muted attestation under a signature block: who, in what function, when.

    NO FUNCTION RECORDED PRINTS TODAY'S LINE, BYTE FOR BYTE - the same string at
    the same size at the same baseline, with no fitting applied. That is the
    fallback every historical row takes and it must not be re-laid-out by this
    change.

    The three-part line is shrunk, and only downwards, to keep it inside the
    page margin: the PROGRAM DIRECTOR block starts at x=444.5 and a name plus a
    function plus a timestamp is wider than that column in 8pt Times. It is
    overlay text in blank margin - no coordinate constant and no cell of the
    form is involved.
    """
    label = _function_label(function)
    if label is None:
        _text(c, x, baseline, f"{_esc_free(name)} · {when}", size=size, color=MUTED)
        return
    text = f"{_esc_free(name)} · {label} · {when}"
    fitted = size
    while fitted > 6.0 and c.stringWidth(text, BODY_FONT, fitted) > (right_edge - x):
        fitted -= 0.25
    _text(c, x, baseline, text, size=fitted, color=MUTED)


def _esc_free(value: Any) -> str:
    """`_text` draws raw strings (no Paragraph markup), so nothing is escaped
    here; this only turns None into an empty string."""
    return "" if value is None else str(value)


def _image(c: canvas.Canvas, sig: tuple[bytes, str] | None, x: float, bottom: float,
           *, max_h: float, max_w: float) -> None:
    """The uploaded signature, its bottom edge on `bottom`, scaled to fit.

    AN UNREADABLE IMAGE PRINTS NOTHING AND SAYS SO IN THE LOG (2026-09-16).
    It used to be swallowed silently, which is how "the signature is not on
    the PDF" reached the office with no line anywhere saying why. The image
    is first handed to ReportLab as stored; if ReportLab cannot read it (a
    palette PNG with transparency, a CMYK or EXIF-rotated phone JPEG), it is
    re-encoded through Pillow as a flat RGBA PNG and tried once more. The
    upload path normalises new images the same way (`routers/signature.py`),
    so this fallback is for files stored before it did.
    """
    if sig is None or max_h <= 6:
        return
    content, _mime = sig
    reader = None
    for attempt, payload in enumerate((content, _renormalised(content))):
        if payload is None:
            continue
        try:
            reader = ImageReader(io.BytesIO(payload))
            w, h = reader.getSize()
            break
        except Exception as exc:  # noqa: BLE001 - reported, never fatal to the paper
            reader = None
            log.warning("leave paper: signature image unreadable (attempt %d): %s", attempt + 1, exc)
    if reader is None:
        log.warning("leave paper: the signature image could not be drawn; name and time print alone")
        return
    if not w or not h:
        return
    height = max_h
    width = height * (w / h)
    if width > max_w:
        width = max_w
        height = width * (h / w)
    c.drawImage(reader, x, _y(bottom), width=width, height=height, mask="auto")


def _white(c: canvas.Canvas, box: tuple[float, float, float, float]) -> None:
    x0, top, x1, bottom = box
    c.setFillColor(colors.white)
    c.setStrokeColor(colors.white)
    c.rect(x0, _y(bottom), x1 - x0, bottom - top, stroke=0, fill=1)


def _line(c: canvas.Canvas, x0: float, y: float, x1: float, *, width: float = 1.0) -> None:
    c.setStrokeColor(INK)
    c.setLineWidth(width)
    c.line(x0, _y(y), x1, _y(y))


def _alt_cells(a: Any) -> list[str]:
    return [str(getattr(a, k, "") or "") for k in ("date", "staff_name", "cls", "time", "remarks")]


# ------------------------------------------------------------- render --


def _overlay(leave: Any, staff_signature, director_signature) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=(PAGE_W, PAGE_H))
    c.setTitle(f"Leave application - {leave.requester_name}")
    c.setAuthor("REEP")

    # -- "Application for": strike what does not apply -----------------------
    if leave.leave_kind in OPTIONS:
        for key, (x0, x1) in OPTIONS.items():
            if key != leave.leave_kind:
                _line(c, x0, OPTIONS_STRIKE_Y, x1, width=1.1)

    # -- Date: the day and month go in the gap before the printed 2026 ---------
    signed = leave.signed_at
    if signed is not None:
        local = signed.astimezone(timezone.utc) if signed.tzinfo else signed
        if local.year == 2026:
            _text(c, DATE_GAP_X, DATE_BASELINE, local.strftime("%d %b").lstrip("0"), size=13)
        else:
            _white(c, YEAR_BOX)
            _text(c, DATE_GAP_X, DATE_BASELINE, _when(signed, with_time=False), size=13)

    # -- the six rows -----------------------------------------------------------
    values = {
        "name": leave.requester_name,
        "designation": leave.requester_designation or "",
        "department": leave.requester_department or "",
        "date": _date_span(leave.from_date, leave.to_date),
        "purpose": leave.reason,
        "credit": leave.credit or "",
    }
    for key, (top, bottom) in ROWS.items():
        _fit(c, values[key], VALUE_X0, top, VALUE_X1, bottom)

    # -- "Sanctioned": the director's mark ------------------------------------
    sx0, stop, sx1, sbottom = SANCTIONED
    mid_y = (stop + sbottom) / 2
    if leave.status == "APPROVED":
        # A tick just left of the printed word.
        c.setStrokeColor(INK)
        c.setLineWidth(1.6)
        p = c.beginPath()
        p.moveTo(sx0 - 16, _y(mid_y + 1))
        p.lineTo(sx0 - 11, _y(mid_y + 6))
        p.lineTo(sx0 - 3, _y(stop - 1))
        c.drawPath(p, stroke=1, fill=0)
    elif leave.status == "REJECTED":
        _line(c, sx0, mid_y + 0.5, sx1, width=1.1)
        _text(c, sx0 - 8, sbottom - 3.5, SANCTIONED_WORDS["REJECTED"], size=12, right=True)
    else:
        _text(c, sx0 - 8, sbottom - 3.5, SANCTIONED_WORDS.get(leave.status, str(leave.status).title()),
              size=11, color=MUTED, right=True)

    # -- SIGNATURE OF STAFF: image above the label, name and time beneath ------
    if leave.signed_at is not None:
        _image(c, staff_signature, STAFF_X0, STAFF_LABEL_TOP - 3, max_h=40, max_w=150)
        # NO FUNCTION on the applicant's own blocks, and never their designation:
        # the applicant signs as themselves, their designation is already in the
        # table two inches above, and a second copy of it here would read as a
        # claim about the authority they signed with.
        _attest(c, STAFF_X0, STAFF_LABEL_TOP + 27, leave.requester_name, None,
                _when(leave.signed_at), size=8.5)
    else:
        _text(c, STAFF_X0, STAFF_LABEL_TOP + 27, "Not signed", size=8.5, color=MUTED)

    # -- PROGRAM DIRECTOR: image above, attestation beneath ---------------------
    # The LABEL is printed on the college's own form and is not ours to rename
    # (module docstring). The attestation beneath it names the function.
    if leave.director_name and leave.director_decided_at is not None:
        _image(c, director_signature, DIRECTOR_X0, DIRECTOR_LABEL_TOP - 3,
               max_h=DIRECTOR_LABEL_TOP - 3 - (sbottom + 2), max_w=DIRECTOR_X1 - DIRECTOR_X0)
        # Second-then-first, exactly as `_leave_out` chooses `director_name` and
        # `director_decided_at`: whichever signature decided this request is the
        # one whose name, time AND function are printed against it. `getattr`
        # because this renderer's contract is "anything with LeaveOut's fields",
        # and a caller built before B10.1 has neither column.
        _attest(c, DIRECTOR_X0, DIRECTOR_LABEL_BOTTOM + 10.5, leave.director_name,
                getattr(leave, "second_signed_as", None) or getattr(leave, "first_signed_as", None),
                _when(leave.director_decided_at), size=8)
    else:
        _text(c, DIRECTOR_X0, DIRECTOR_LABEL_BOTTOM + 10.5, "Awaiting", size=8, color=MUTED)

    # -- Alternate arrangements --------------------------------------------------
    if leave.alt_name:
        _white(c, ALT_NAME_DOTS)
        _text(c, ALT_NAME_X, ALT_NAME_BASELINE, str(leave.alt_name), size=12)
    alt_rows = list(getattr(leave, "alt_rows", []) or [])
    table_bottom = ALT_TABLE_BOTTOM
    # The two printed rows, then at most one drawn row: the foot signature
    # needs the space below. Anything past that is counted, not squeezed.
    max_rows = 3
    for i, a in enumerate(alt_rows[:max_rows]):
        if i < len(ALT_ROW_TOPS):
            top = ALT_ROW_TOPS[i]
        else:
            top = ALT_ROW_TOPS[-1] + ALT_ROW_PITCH * (i - len(ALT_ROW_TOPS) + 1)
        bottom = top + ALT_ROW_PITCH - 0.4
        if i >= len(ALT_ROW_TOPS):
            # Draw the extra row's grid exactly like the printed ones (0.5 pt).
            c.setFillColor(INK)
            for gx in ALT_GRID_X:
                c.rect(gx, _y(bottom + 0.4), 0.5, bottom + 0.4 - top, stroke=0, fill=1)
            c.rect(ALT_GRID_X[0], _y(bottom + 0.4), ALT_GRID_X[-1] - ALT_GRID_X[0] + 0.5, 0.4, stroke=0, fill=1)
            table_bottom = bottom + 0.4
        for (x0, x1), text in zip(ALT_COLS, _alt_cells(a)):
            _fit(c, text, x0, top, x1, bottom, size=10.5, min_size=7, pad=3)
    if len(alt_rows) > max_rows:
        _text(c, ALT_GRID_X[0], table_bottom + 10,
              f"+ {len(alt_rows) - max_rows} more arrangement(s) recorded on REEP", size=8, color=MUTED)
        table_bottom += 12

    # -- Signature of staff (foot): image above the label, attestation beneath --
    if leave.signed_at is not None:
        _image(c, staff_signature, FOOT_X0, FOOT_LABEL_TOP - 3,
               max_h=min(34.0, FOOT_LABEL_TOP - 3 - table_bottom - 3), max_w=FOOT_X1 - FOOT_X0)
        _attest(c, FOOT_X0, FOOT_LABEL_BOTTOM + 10.5, leave.requester_name, None,
                _when(leave.signed_at), size=8)
    else:
        _text(c, FOOT_X0, FOOT_LABEL_BOTTOM + 10.5, "Not signed", size=8, color=MUTED)

    # -- The director's remarks have no cell on the form: the bottom margin ----
    if leave.director_note:
        label = "Rejected - remarks" if leave.status == "REJECTED" else "Sanctioned - remarks"
        _fit(c, f"{label}: {leave.director_note}", 66.0, 752.0, 546.0, 772.0, size=8.5, min_size=7, pad=0)
    _text(c, 66.0, 782.0,
          f"Generated by REEP on {_when(datetime.now(timezone.utc))} UTC. A signature on this form is a name "
          "and a time recorded against the signer's REEP account; an image is that person's uploaded signature.",
          size=6.5, color=MUTED)

    c.showPage()
    c.save()
    return buf.getvalue()


def render_leave_paper_pdf(
    leave: Any,
    *,
    staff_signature: tuple[bytes, str] | None = None,
    director_signature: tuple[bytes, str] | None = None,
) -> bytes:
    """`leave` is routers/leave.py's LeaveOut (or anything with its fields).
    The official form, with this request written onto it."""
    overlay = PdfReader(io.BytesIO(_overlay(leave, staff_signature, director_signature)))
    template = PdfReader(str(TEMPLATE))
    page = template.pages[0]
    page.merge_page(overlay.pages[0])
    writer = PdfWriter()
    writer.add_page(page)
    writer.add_metadata({"/Title": f"Leave application - {leave.requester_name}", "/Producer": "REEP"})
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
