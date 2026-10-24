# Starter question banks for the four interview tracks

Four paste-ready files, one per track in `interview_matrix.SPECIALIZATIONS`:

| File     | Track code | Interviewer the student meets                     | Questions |
|----------|------------|---------------------------------------------------|-----------|
| `hr.txt` | `hr`       | an empathetic yet compliant CHRO                   | 41 |
| `dm.txt` | `dm`       | a growth-oriented, data-driven CMO                 | 43 |
| `ba.txt` | `ba`       | a highly technical Director of Analytics           | 43 |
| `fa.txt` | `fa`       | a sharp, risk-conscious Managing Director / CFO    | 43 |

## Putting them in

Main Admin → **Interview questions** → pick the track's tab → **Add many** →
paste the file's contents (or use the file picker, which reads the text in the
browser) → submit. One file per track; the track is chosen on the screen, not
in the file.

The response reports what went in and names, by line number, anything it
skipped. Every line in these four files parses, so a non-empty `skipped` list
means the paste was edited or truncated on the way in.

## The format, and what the parser will and will not take

`app/interview_bank.py::parse_bulk` is the reader. One question per line:

```
[probing] Tell me about a conflict you resolved.
probing | Tell me about a conflict you resolved.
probing, Tell me about a conflict you resolved.
Tell me about a conflict you resolved.            <- no phase, filed as probing
```

These files use the bracket form throughout, because brackets are read as
**explicit intent**: a phase the parser does not recognise inside brackets is
skipped with its line number rather than quietly filed under `probing`. The
four phases are `opening`, `probing`, `deep_dive` and `wrap_up` (`ENDED` is
terminal and asks nothing). Blank lines and `#` lines are ignored, which is
what the section headers in each file are. A question must be 8–600
characters.

## Two things worth knowing before you paste

**It appends, it does not replace.** Each new question takes the next
`position` after whatever the track already holds. Pasting a file twice gives
you the bank twice — there is no de-duplication. Clear the track first if you
are re-importing.

**Order is load-bearing.** The prompt asks the interviewer to work the bank *in
this order*, so each file is grouped `opening → probing → deep_dive → wrap_up`
and the positions land in that arc. Reordering afterwards is the **Reorder**
action on the screen, not an edit to a position field.

## What these questions are, and are not

They are a guide to **coverage**, not a script. `build_instructions` hands the
bank to the model framed as "rephrase each naturally, follow up on what the
student actually says", so the interviewer will not recite them and no turn can
be attributed back to a row — which is why the screen's per-question "Asked"
and "Avg score" columns do not exist (B6.6). An admin decides *what* is
covered; the model decides *how* it is asked.

Content-wise they follow each track's own `frameworks` list, and `dm.txt`
additionally follows the five modules of the 22MDM23 syllabus carried on that
row, including the metric arithmetic (CTR, CPC, CPM, cost per conversion,
ROAS) the student is expected to do out loud. Nothing here is a model answer:
the source material for DM deliberately ships questions without its answer key
so the model cannot mark a student against a memorised script.

Edit them freely — they are a starting bank for a new deployment, not a
specification. Nothing in the product reads these files; they exist to be
pasted.
