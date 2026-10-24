"""Render a composed resume (markdown) to a one-page-ish PDF with ReportLab.

Runs entirely on this machine — no network, no model — so the student-data
egress gate does not apply here: the student's PII is turned into bytes locally
and streamed straight back to the authenticated owner. Keep it that way; do not
add any remote call to this module.

The markdown we render is what `_compose_resume_markdown` / the AI-polish step
produces: `# Name`, `## Section`, `- bullet`, `| a | b |` tables and plain
paragraphs, with simple `**bold**` inline emphasis. Anything richer degrades
gracefully to plain text.

The standard template (`RESUME_TEMPLATE_SECTIONS` in `routers/student.py`) is
drawn the conventional campus-placement way: the name centred, the paragraphs
between it and the first section (the contact line) centred under it, each
section heading in small capitals over a rule, and Education as a table.
"""

import html
import io
import re
from dataclasses import dataclass

from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    ListFlowable,
    ListItem,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)


def _inline(md: str) -> str:
    """Minimal inline markdown -> ReportLab mini-HTML. Escape first so a stray
    '<' in the data can never inject markup, then re-introduce only <b>."""
    escaped = html.escape(md)
    # **bold** -> <b>bold</b>
    return re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", escaped)


def _styles() -> dict:
    base = getSampleStyleSheet()
    name = ParagraphStyle(
        "ResumeName", parent=base["Title"], fontSize=20, spaceAfter=2, leading=24, alignment=TA_CENTER
    )
    section = ParagraphStyle(
        "ResumeSection",
        parent=base["Heading2"],
        fontSize=12,
        textColor="#1a3c5e",
        spaceBefore=10,
        spaceAfter=2,
        leading=15,
    )
    body = ParagraphStyle("ResumeBody", parent=base["BodyText"], fontSize=10, leading=14, spaceAfter=3)
    bullet = ParagraphStyle("ResumeBullet", parent=body, spaceAfter=1)
    header = ParagraphStyle("ResumeHeader", parent=body, fontSize=9.5, alignment=TA_CENTER)
    cell = ParagraphStyle("ResumeCell", parent=body, fontSize=9, leading=11, spaceAfter=0)
    return {
        "name": name,
        "section": section,
        "body": body,
        "bullet": bullet,
        "header": header,
        "cell": cell,
    }


_RULE = "#1a3c5e"


def _row_cells(line: str) -> list[str]:
    return [c.strip() for c in line.strip().strip("|").split("|")]


def _is_separator(line: str) -> bool:
    cells = _row_cells(line)
    return bool(cells) and all(c and set(c) <= set("-: ") for c in cells)


def _table(lines: list[str], styles: dict, width: float) -> Table | None:
    """A markdown table as a ReportLab table: header row shaded, grid ruled.

    None when every line was a separator: a student's own text can hold a line
    such as `|---|`, and a table with no rows is not a table.
    """
    rows = [_row_cells(ln) for ln in lines if not _is_separator(ln)]
    if not rows:
        return None
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    data = [
        [Paragraph(("<b>%s</b>" if i == 0 else "%s") % _inline(c), styles["cell"]) for c in r]
        for i, r in enumerate(rows)
    ]
    # The standard template's Education table is four columns and the
    # institution is the long one; any other table splits evenly.
    weights = [0.26, 0.4, 0.14, 0.2] if ncols == 4 else [1 / ncols] * ncols
    table = Table(data, colWidths=[width * w for w in weights], repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8eef5")),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#9fb1c6")),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 3),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ]
        )
    )
    return table


def render_resume_pdf(markdown: str, *, fallback_title: str = "Resume") -> bytes:
    """Turn resume markdown into PDF bytes."""
    styles = _styles()
    story: list = []
    pending_bullets: list = []

    def flush_bullets() -> None:
        if pending_bullets:
            story.append(
                ListFlowable(
                    [ListItem(Paragraph(b, styles["bullet"]), leftIndent=10) for b in pending_bullets],
                    bulletType="bullet",
                    start="•",
                    leftIndent=12,
                )
            )
            pending_bullets.clear()

    saw_name = False
    in_header = False  # between the `# ` name and the first `## ` section
    pending_table: list[str] = []
    width = A4[0] - 36 * mm

    def flush_table() -> None:
        if pending_table:
            table = _table(pending_table, styles, width)
            if table is None:
                # Not a table after all: print the lines as the text they were.
                for ln in pending_table:
                    story.append(Paragraph(_inline(ln), styles["body"]))
            else:
                story.append(table)
                story.append(Spacer(1, 2 * mm))
            pending_table.clear()

    for raw in (markdown or "").splitlines():
        line = raw.rstrip()
        if line.lstrip().startswith("|"):
            flush_bullets()
            pending_table.append(line.strip())
            continue
        flush_table()
        if not line.strip():
            flush_bullets()
            continue
        if line.startswith("# "):
            flush_bullets()
            story.append(Paragraph(_inline(line[2:].strip()), styles["name"]))
            saw_name = True
            in_header = True
        elif line.startswith("## "):
            flush_bullets()
            if in_header:
                story.append(HRFlowable(width="100%", thickness=1, color=_RULE, spaceAfter=4))
                in_header = False
            story.append(Paragraph(_inline(line[3:].strip().upper()), styles["section"]))
            story.append(HRFlowable(width="100%", thickness=0.5, color=_RULE, spaceAfter=3))
        elif line.lstrip().startswith(("- ", "* ")):
            pending_bullets.append(_inline(line.lstrip()[2:].strip()))
        else:
            flush_bullets()
            style = styles["header"] if in_header else styles["body"]
            story.append(Paragraph(_inline(line.strip()), style))
    flush_table()
    if in_header:
        story.append(HRFlowable(width="100%", thickness=1, color=_RULE, spaceAfter=4))
    flush_bullets()

    if not saw_name:
        story.insert(0, Paragraph(_inline(fallback_title), styles["name"]))
    if len(story) <= 1:
        story.append(Paragraph("No resume content.", styles["body"]))
        story.append(Spacer(1, 4 * mm))

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        title=fallback_title,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    )
    doc.build(story)
    return buf.getvalue()


# --------------------------------------------------------------------------- #
# The evidence appendix
# --------------------------------------------------------------------------- #
#
# THE CHECKBOX USED TO DO NOTHING. "Include an evidence appendix with proof
# links" changed one sentence of consent copy and no bytes: `exportPdf()` opened
# the same URL either way, so a student who ticked it believed their
# certificates travelled with the resume and sent a document that did not carry
# them. That is a worse failure than the box not existing — they stopped
# attaching the files themselves.
#
# "Proof LINKS" could not have worked as written, either: an upload URL needs
# the student's own session cookie, so a recruiter following one gets a login
# page. The proof has to be the FILE, embedded, or it is not proof.


@dataclass(frozen=True)
class EvidenceProof:
    """One verified skill and the file a mentor checked it against."""

    skill: str
    title: str
    original_name: str
    mime_type: str
    content: bytes
    verified_on: str | None = None


def _appendix_index(proofs: list[EvidenceProof]) -> bytes:
    """The contents page: what follows, and what each page is evidence of."""
    styles = _styles()
    story: list = [
        Paragraph("Evidence appendix", styles["name"]),
        HRFlowable(width="100%", thickness=1, color="#1a3c5e", spaceAfter=6),
        Paragraph(
            "Each document below was checked by a faculty mentor against the skill it is "
            "listed under. Nothing here is self-certified.",
            styles["body"],
        ),
        Spacer(1, 4 * mm),
    ]
    items = []
    for p in proofs:
        line = f"<b>{html.escape(p.skill)}</b> — {html.escape(p.title or p.original_name)}"
        if p.verified_on:
            line += f" (verified {html.escape(p.verified_on)})"
        items.append(ListItem(Paragraph(line, styles["bullet"]), leftIndent=10))
    if items:
        story.append(ListFlowable(items, bulletType="bullet", start="•", leftIndent=12))

    buf = io.BytesIO()
    SimpleDocTemplate(
        buf,
        pagesize=A4,
        title="Evidence appendix",
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
    ).build(story)
    return buf.getvalue()


def _caption_page(text: str) -> bytes:
    """A single page carrying one line — used when a proof cannot be embedded.

    A missing page is silent; a page saying the file could not be included is
    not. The student can see the gap before an employer does.
    """
    styles = _styles()
    buf = io.BytesIO()
    SimpleDocTemplate(
        buf, pagesize=A4, leftMargin=18 * mm, rightMargin=18 * mm, topMargin=16 * mm
    ).build([Paragraph(_inline(text), styles["body"])])
    return buf.getvalue()


def _image_page(proof: EvidenceProof) -> bytes:
    """One page holding one image proof, scaled to fit inside the margins."""
    page_w, page_h = A4
    margin = 18 * mm
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)

    c.setFont("Helvetica-Bold", 11)
    c.drawString(margin, page_h - margin, f"{proof.skill} — {proof.title or proof.original_name}")

    top = page_h - margin - 8 * mm
    box_w, box_h = page_w - 2 * margin, top - margin
    try:
        image = ImageReader(io.BytesIO(proof.content))
        iw, ih = image.getSize()
        # Fit, never enlarge: a 240px certificate blown up to A4 is unreadable
        # in a way the original was not.
        scale = min(box_w / iw, box_h / ih, 1.0)
        w, h = iw * scale, ih * scale
        c.drawImage(
            image,
            margin + (box_w - w) / 2,
            margin + (box_h - h) / 2,
            width=w,
            height=h,
            preserveAspectRatio=True,
            anchor="c",
            mask="auto",
        )
    except Exception:
        c.setFont("Helvetica", 10)
        c.drawString(margin, top - 6 * mm, "This image could not be rendered into the appendix.")
    c.showPage()
    c.save()
    return buf.getvalue()


def append_evidence(resume_pdf: bytes, proofs: list[EvidenceProof]) -> bytes:
    """The resume, then an index, then every proof, as one PDF.

    Defensive at every step: a student's upload is a file the SERVER accepted by
    magic bytes, which is not the same as a file pypdf can parse — an encrypted
    or truncated PDF must not 500 the export of a resume that is otherwise
    perfect. Each unusable proof becomes a page that says so.
    """
    if not proofs:
        return resume_pdf

    writer = PdfWriter()
    for page in PdfReader(io.BytesIO(resume_pdf)).pages:
        writer.add_page(page)
    for page in PdfReader(io.BytesIO(_appendix_index(proofs))).pages:
        writer.add_page(page)

    for proof in proofs:
        try:
            if proof.mime_type == "application/pdf":
                reader = PdfReader(io.BytesIO(proof.content))
                if reader.is_encrypted:
                    # decrypt("") opens the common "owner password only" case;
                    # anything else is genuinely closed to us.
                    try:
                        reader.decrypt("")
                    except Exception:
                        raise ValueError("encrypted")
                pages = list(reader.pages)
                if not pages:
                    raise ValueError("no pages")
                for page in pages:
                    writer.add_page(page)
            else:
                for page in PdfReader(io.BytesIO(_image_page(proof))).pages:
                    writer.add_page(page)
        except Exception:
            caption = (
                f"<b>{proof.skill}</b> — {proof.title or proof.original_name} could not be "
                "included in this appendix. The original is still on your REEP record."
            )
            for page in PdfReader(io.BytesIO(_caption_page(caption))).pages:
                writer.add_page(page)

    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
