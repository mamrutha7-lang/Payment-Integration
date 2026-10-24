# Printed posters

> **`reep-tech-stack-a3` IS PARTLY STALE and cannot say so on its own face.** The
> sheet draws the **LiveKit voice worker** ("Voice worker needs its OWN venv on
> Python 3.12") and the **`worker-imports`** CI job — "the same proof for the
> voice worker". Both were removed in 2026-09: there is no fourth process and no
> second venv, the one voice experience is `/student/assistant` (a WebSocket
> *inside* the API process to Amazon Nova 2 Sonic on Bedrock, no API key), and CI
> has **five** jobs — `api`, `pii-gate`, `api-imports`, `web`, `cdk`. The note is
> here rather than on the poster because a rendered sheet has no room for a
> warning that would still be legible at A3, and re-drawing it is a design job,
> not a docs job. `AGENTS.md` is the current truth.
>
> **The people vocabulary on all three sheets was corrected on 2026-10-08.** They
> said "DIRECTOR / ADMIN", "FACULTY / MENTOR", "Directors", `/director/*` and
> `require_director` for a month after DIRECTOR stopped being a role (2026-09-10).
> They now draw the pair on-screen-name first — **Main Admin (role ADMIN)**, the
> placement office, one account; **Faculty (role MENTOR)**, a mentor only once
> assigned a mentee — and `/admin/*` and `require_admin`. The words are
> `AGENTS.md` "Who is who"; a wording change here is a change to
> `tools/diagrams/`, never to the rendered files.

**`n8n/reep-roles-features-crud.n8n.json`** is not a poster but an importable
n8n workflow. It draws every role's every feature with its CREATE / READ /
UPDATE / DELETE operations and the endpoint, handler, tables and AWS services
behind each one, plus the request path and the AWS estate. It is generated from
`n8n/reep-feature-inventory.json`; [`n8n/README.md`](n8n/README.md) says how to
open it, read it and keep it true. `n8n/reep-roles-features-crud.html` is the same map as a
phone-friendly page, and `n8n/reep-n8n-canvas.pdf` is the canvas itself as n8n
draws it, in pages any phone opens without n8n.

Three A3 sheets, each as `.svg` (source of truth), `.pdf` (print) and `.png`
(150 dpi preview):

**`reep-architecture-a3`** — the deployed system: people and roles, the AWS
edge, compute, state, the AI plane, observability and traceability, the
interview call recorder, and the invariants that must not be broken.

**`reep-flow-a3`** (A3 **portrait**) — the read-from-across-the-room sheet:
big flat icons, two-line captions and numbered steps following ONE request
from a person to the data behind it, in the style of a vendor architecture
diagram. Hand this one to someone who has never seen the system.

**`reep-tech-stack-a3`** — the stack and its wiring: the browser tab, one
request descending through the API process, and the stores and services it
talks to — with every wire labelled by protocol, payload and the guard that
sits on it. Read this one when the question is *how do the pieces talk*.

## Printing

Print the **PDF** at **A3 landscape (420 × 297 mm), 100 % scale, no margins /
"actual size"** — do not let the driver "fit to page", which shrinks the body
text below comfortable reading size. Body text is ≈ 2.4 mm tall, which reads
from about a metre away. On A4 it is legible but cramped; A3 is the design
target. The SVG is vector, so it also scales cleanly to A2 or A1 if you want a
bigger wall copy.

## Regenerating

The poster is generated, not drawn — so it can be kept honest as the system
changes:

```bash
python tools/diagrams/render_architecture.py        # the deployment poster
python tools/diagrams/render_stack_interaction.py   # the stack interaction map
python tools/diagrams/render_flow_poster.py         # the icon flow poster (portrait)
```

All three draw on `tools/diagrams/poster_kit.py` (cards, containers, arrows,
the geometry guard), and the flow poster adds `icon_kit.py` — original
simplified glyphs in each vendor's familiar colour, captioned with the product
name rather than redrawing anyone's trademarked logo.

Print the portrait sheet as **A3 portrait**; the other two are **A3
landscape**. The physical size follows from each poster's viewBox, so both
orientations come out at true size with no scaling.

The generator validates its own geometry and refuses to pass silently: any card
whose text would clip, or that would fall off the canvas, is reported on stdout
("clean" when all is well). To refresh the PDF and PNG afterwards:

```bash
python tools/diagrams/export_print.py              # all three: SVG → PDF (A3) → PNG (150 dpi)
python tools/diagrams/export_print.py reep-flow-a3 # one sheet
```

It wraps each SVG in a page whose `@page` size is the SVG's own `width`/`height`
(so the portrait sheet comes out portrait), prints it through headless Chromium
(`CHROME=/path/to/chromium` if none is on `PATH`; Playwright's
`/opt/pw-browsers/chromium` is tried), and rasterises the PDF with `pdftoppm` at
150 dpi — A3 is 2482 × 1754 px, the size the committed previews have. It exits 2,
never 0, when a tool is missing, so a missing Chromium cannot read as a refreshed
poster. By hand it is the same two commands:

```bash
chromium --headless --no-pdf-header-footer --print-to-pdf=out.pdf page.html
pdftoppm -png -r 150 -singlefile out.pdf reep-architecture-a3
```

The SVG names Inter, and Chromium prints with it where it is installed (the
previews rendered before 2026-10-08 fell back to Liberation Sans, which is why
their glyphs differ from the current ones).
