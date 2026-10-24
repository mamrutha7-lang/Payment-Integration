"""Render an English Proficiency Baseline attempt to a PDF with ReportLab.

Runs entirely on this machine — no network, no model — so the student-data
egress gate does not apply: the student's own CEFR record is turned into bytes
locally and streamed straight back to the authenticated owner. Keep it that way;
do not add a remote call to this module, for the same reason `app/resume_pdf.py`
says so about itself.

A PENDING SECTION PRINTS "Not yet taken", NEVER A NUMBER. This is the document a
student may hand to somebody — a placement cell, a recruiter, a parent — and a
speaking section that has not been sat rendering as "0 / 100" in print is worse
than the same mistake on screen, because print cannot be corrected by reloading.
The nullable score columns exist for exactly this distinction
(app/models/english_baseline.py); this module is the last place it has to hold.
"""

from __future__ import annotations

import html
import io
from datetime import date
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

# The brand purple and the ink from the v2 design system. Restated here rather
# than imported from the stylesheet because a PDF cannot read CSS custom
# properties; if the palette moves, this is the second place to change.
_BRAND = colors.HexColor("#552C7E")
_INK = colors.HexColor("#3A1F52")
_MUTED = colors.HexColor("#7A6392")
_HAIRLINE = colors.HexColor("#D6C9E2")


def _esc(value: Any) -> str:
    return html.escape("" if value is None else str(value))


def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "EngTitle", parent=base["Title"], fontSize=19, leading=23,
            alignment=TA_LEFT, textColor=_INK, spaceAfter=2,
        ),
        "sub": ParagraphStyle(
            "EngSub", parent=base["Normal"], fontSize=9.5, leading=13, textColor=_MUTED,
        ),
        "h2": ParagraphStyle(
            "EngH2", parent=base["Heading2"], fontSize=11, leading=14,
            textColor=_BRAND, spaceBefore=12, spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "EngBody", parent=base["Normal"], fontSize=9.5, leading=13.5, textColor=_INK,
        ),
        "cell": ParagraphStyle(
            "EngCell", parent=base["Normal"], fontSize=9, leading=12, textColor=_INK,
        ),
        "note": ParagraphStyle(
            "EngNote", parent=base["Normal"], fontSize=8.5, leading=11.5, textColor=_MUTED,
        ),
    }


def render_english_report_pdf(*, student_name: str, usn: str | None, view: Any) -> bytes:
    """`view` is the EnglishBaselineOut the API already computes.

    Taking the composed VIEW rather than the ORM rows is deliberate: the band,
    the provisional flag and the scored/pending split are decided in one place
    (`compose_english_baseline`), and a PDF that recomputed them could disagree
    with the screen the student is reading it from.
    """
    styles = _styles()
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm,
        topMargin=16 * mm, bottomMargin=16 * mm,
        title=f"English Proficiency Baseline — {student_name}",
        author="REEP",
    )

    flow: list[Any] = [
        Paragraph("English Proficiency Baseline", styles["title"]),
        Paragraph(
            " · ".join(
                part for part in (
                    _esc(student_name),
                    _esc(usn) if usn else "",
                    f"Taken {view.taken_on:%d %b %Y}" if view.taken_on else "Not yet completed",
                ) if part
            ),
            styles["sub"],
        ),
        Spacer(1, 5 * mm),
        HRFlowable(width="100%", thickness=0.6, color=_HAIRLINE, spaceAfter=6),
    ]

    # --- headline ---------------------------------------------------------
    band = view.band or "—"
    band_word = f"{band}" + (f" · {view.band_label}" if view.band_label else "")
    overall = f"{view.overall_score} / 100" if view.overall_score is not None else "Not yet scored"
    heading = "Provisional band" if view.provisional else "Band"

    flow += [
        Paragraph(f"{heading}: <b>{_esc(band_word)}</b>", styles["body"]),
        Paragraph(f"Overall: <b>{_esc(overall)}</b>", styles["body"]),
        Paragraph(
            f"{view.sections_scored} of {view.sections_total} sections scored"
            + (f" — {_esc(view.pending_label)}" if view.pending_label else ""),
            styles["sub"],
        ),
    ]
    if view.provisional:
        flow.append(
            Paragraph(
                "This band is provisional: it is calculated from the sections scored so "
                "far and will change when the remaining section is assessed.",
                styles["note"],
            )
        )

    # --- per-skill table --------------------------------------------------
    flow += [Paragraph("Sections", styles["h2"])]
    rows: list[list[Any]] = [
        [
            Paragraph("<b>Skill</b>", styles["cell"]),
            Paragraph("<b>Score</b>", styles["cell"]),
            Paragraph("<b>CEFR</b>", styles["cell"]),
            Paragraph("<b>Detail</b>", styles["cell"]),
        ]
    ]
    for section in view.sections:
        if section.status == "SCORED" and section.score is not None:
            score_text = f"{section.score} / 100"
            band_text = section.band or "—"
            detail = ", ".join(
                f"{_esc(s.label)} {s.value}" for s in section.subscores if s.value is not None
            ) or "—"
        else:
            # THE POINT OF THIS MODULE. Not "0", not "-", not an empty cell that
            # a reader fills in with the worst assumption.
            score_text = "Not yet taken"
            band_text = "—"
            detail = "Pending assessment"
        rows.append(
            [
                Paragraph(_esc(section.label), styles["cell"]),
                Paragraph(score_text, styles["cell"]),
                Paragraph(band_text, styles["cell"]),
                Paragraph(detail, styles["cell"]),
            ]
        )

    table = Table(rows, colWidths=[30 * mm, 26 * mm, 18 * mm, None], hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, 0), 0.7, _BRAND),
                ("LINEBELOW", (0, 1), (-1, -2), 0.3, _HAIRLINE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
            ]
        )
    )
    flow.append(table)

    # --- scorer's prose, where there is any -------------------------------
    for section in view.sections:
        if section.status == "SCORED" and getattr(section, "ai_report", None):
            flow += [
                Paragraph(f"{_esc(section.label)} — assessor's note", styles["h2"]),
                Paragraph(_esc(section.ai_report), styles["body"]),
            ]

    if view.strengths:
        flow.append(Paragraph("Strengths", styles["h2"]))
        flow += [Paragraph(f"• {_esc(item)}", styles["body"]) for item in view.strengths]
    if view.focus_areas:
        flow.append(Paragraph("Focus areas", styles["h2"]))
        flow += [Paragraph(f"• {_esc(item)}", styles["body"]) for item in view.focus_areas]
    if view.next_steps:
        flow.append(Paragraph("Recommended next", styles["h2"]))
        flow += [
            Paragraph(f"• <b>{_esc(step.title)}</b> — {_esc(step.sub)}", styles["body"])
            for step in view.next_steps
        ]

    flow += [
        Spacer(1, 6 * mm),
        HRFlowable(width="100%", thickness=0.4, color=_HAIRLINE, spaceAfter=4),
        Paragraph(
            f"Generated from the REEP programme record on {date.today():%d %b %Y}. "
            "Scores are produced by the programme's assessment pipeline.",
            styles["note"],
        ),
    ]

    doc.build(flow)
    return buf.getvalue()
