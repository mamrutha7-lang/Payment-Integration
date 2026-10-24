"""The local resume PDF renderer — a valid PDF out, and input escaped so data
can never inject markup. Pure logic, no DB, no network.
"""

from app.resume_pdf import render_resume_pdf

SAMPLE = """# Asha Rao
MBA finance candidate.

## Skills
- Financial modelling
- **Advanced** Excel

## Education
- MBA, BGSCET, 2026
"""


def test_renders_valid_pdf_bytes():
    pdf = render_resume_pdf(SAMPLE, fallback_title="Asha Rao")
    assert pdf.startswith(b"%PDF-")
    assert b"%%EOF" in pdf
    assert len(pdf) > 800


def test_empty_markdown_still_valid_pdf():
    pdf = render_resume_pdf("", fallback_title="Blank Resume")
    assert pdf.startswith(b"%PDF-")
    assert b"%%EOF" in pdf


def test_angle_brackets_do_not_break_render():
    # A '<script>'-looking token in the data must not raise or inject markup.
    pdf = render_resume_pdf("# X\n- uses <b>C++</b> & <script>alert(1)</script>\n")
    assert pdf.startswith(b"%PDF-")
