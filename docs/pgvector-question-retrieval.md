# Retrieving interview questions from pgvector, and retiring the OpenSearch collection

**Status:** design + implementation, not yet applied. Written 2026-09-07.

## Why

`GET /api/platform/admin/status` reports an OpenSearch Serverless collection
called `reep-voice`. It costs **$11.87/day — about $361/month**, measured from
Cost Explorer on 2026-09-06, the first full day it ran. That is 73% of the
account's entire gross spend, against an account whose total is ~$495/month with
essentially no users.

It is not merely idle. **Nothing reads it.** `OpenSearchIndex.search` and
`OpenSearchIndex.knn` (`app/voice_platform/storage/opensearch.py:161-174`) have
zero callers in the repository. The `question-vectors` index is written only when
a request opts in with `index_vector: true`
(`app/voice_platform/api/admin.py:527-534`), is never re-indexed by `PATCH`
(`admin.py:545-556`), and is never cleaned up by `DELETE` (`admin.py:559-562`).

So this document does not port a working retrieval feature onto pgvector. There
is no retrieval feature. **It builds the one the index was created for**, on the
Postgres that is already running, and then the collection can go.

### What exists today instead of retrieval

`compile_specialization` (`app/voice_platform/engine/nova.py:56-73`) flattens the
*entire* active question bank into strings and
`build_instructions` (`app/interview_matrix.py:692-704`) pastes **all of them**
into Nova's system prompt:

```python
question_bank=tuple(f"[{q.phase}] {q.text}" for q in active)
```

Every question for a track is therefore paid for in input tokens on every
interview, the bank cannot grow past the context window, and `rubric` is dropped
on the floor — it never reaches the engine at all. Retrieval fixes all three.

---

## Three findings that shape the design

### 1. A dimensionless `vector` column cannot be indexed

Verified against the running database (pgvector 0.8.6, PostgreSQL 17.11):

```
CREATE TEMP TABLE t (id int, embedding vector);
CREATE INDEX ON t USING hnsw (embedding vector_cosine_ops);
  ERROR:  column does not have dimensions

CREATE TEMP TABLE t (id int, embedding vector(1024));
CREATE INDEX ON t USING hnsw (embedding vector_cosine_ops);
  CREATE INDEX
```

`KnowledgeChunk.embedding` is `Vector()` with no dimension
(`app/models/knowledge.py:117`), which is *why* the KB has no vector index — it
is not an oversight, it is not possible. The new column must therefore be
`Vector(1024)` (mistral-embed's width) if it is ever to be indexed.

The tradeoff is real and worth stating: a fixed dimension means changing
embedding provider becomes a migration, where the KB can silently mix widths.
For a column that must be indexed, that is the right side to err on — and
`redesign_knowledge_chunk_embeddings` already sets the precedent with
`Vector(1024)` (`app/models/redesign.py:379`).

### 2. HNSW cannot pre-filter, and at this volume should not exist yet

The brief asked to "use a metadata filter first to isolate the rows to that
specific course before calculating vector similarity". **An HNSW index scan
cannot do that.** It walks the graph, returns roughly `ef_search` candidates, and
the `WHERE` clause is applied *afterwards*. Filter to a slice that is a small
fraction of the table and the index hands back candidates the filter discards,
so the query returns **fewer rows than its `LIMIT`**, silently and without error.

pgvector 0.8 offers `hnsw.iterative_scan`, verified present:

```
SET hnsw.iterative_scan = strict_order;   -- SET
SET hnsw.ef_search = 100;                 -- SET
```

But the honest answer for a college question bank is that **the index should not
be built yet**. A few thousand questions filtered to one course is a few hundred
rows. An exact scan over that is sub-millisecond, exactly ranked, has perfect
recall, needs no `ef_search` tuning and has no recall cliff. HNSW earns its
complexity somewhere north of ~100k rows in the *filtered* set.

The DDL is given below, with the threshold for building it. Ship the column and
the btree; leave the HNSW commented until the row count justifies it.

### 3. The tenancy join was never made

`platform_specializations` is keyed on `(degree_level, key)` where `degree_level`
is the free-text 2-char string `"UG"`/`"PG"` (`app/models/voice_platform.py:85-129`).
It has **no link at all** to `colleges` / `departments` / `academic_courses` /
`academic_specializations` — the institutional spine that does exist, is fully
foreign-keyed down to `students.cohort_id`, and has ~22 tests behind it.

So `department` and `course` are not new columns to invent. They are that join.

**They go on the specialization, not on the question**, and they are *read
through* the join rather than copied. AGENTS.md already states this rule for the
student profile card: "read THROUGH that join and stored on nothing … Do not copy
any of it onto `students`: that is the backfill this shape exists to avoid." A
`department` string on 5,000 question rows is that backfill, times 5,000.

---

## Blockers: three engine changes required before a second tool can exist

`app/interview_nova.py` supports exactly one tool today, and three specifics
break if a second is added. These are not optional.

| # | Where | Problem |
|---|---|---|
| 1 | `_on_tool_use`, `interview_nova.py:1359-1370` | Any tool name that is not `submit_scorecard` is logged and **discarded**. Needs a dispatch table. |
| 2 | `_pending_tool`, `interview_nova.py:748` | A **single slot** (`dict | None`), and `_on_content_end` settles it on *any* `TOOL`-typed `contentEnd` **without matching `contentId`**. Two tools in flight cross-settle. Must become a dict keyed by `contentId`. |
| 3 | `_settle_tool_use`, `interview_nova.py:1655` | The tool-result `contentName` is `f"{self._prompt_name}-toolresult-{self._note_seq}"` — derived from the **control-note counter**, not a tool counter. Two results between two notes collide on one name. |

Rule 1 is enforced by a test that greps the module source
(`tests/test_interview_nova.py:232-241` bans `from .models`, `app.models`,
`SessionLocal`, `app.conversations`), so **no database code may enter
`interview_nova.py`**. The fetch arrives as an injected `Callable`, exactly like
`on_turn` / `on_report` / `on_finalize`.

---

## Part 1 — Schema

### The Alembic migration

```python
"""platform_questions.embedding + difficulty; the spine join on specializations.

Retrieval replaces the write-only OpenSearch `question-vectors` index. The
column is vector(1024) and NOT dimensionless, because pgvector refuses to build
any index on a column without a typmod ("column does not have dimensions") —
which is why knowledge_chunks has no vector index and never could. 1024 is
mistral-embed's width, matching redesign_knowledge_chunk_embeddings.

department_id and course_id go on the SPECIALIZATION, not on the question. A
question belongs to a track; a track belongs to a course. Copying the ancestry
onto every question row is the backfill AGENTS.md's institutional-spine section
exists to avoid. Both are NULLABLE: the catalogue predates the spine and
existing rows have no honest value to put there.

OFFLINE-CLEAN and SET LOCAL. Adding nullable columns with no default is
catalogue-only on PostgreSQL — no rewrite, no backfill.

Revision ID: c4a71e93b6d2
Revises: b9d4e7a2c318
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector

revision: str = "c4a71e93b6d2"
down_revision: Union[str, None] = "b9d4e7a2c318"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")

    # Idempotent; the live database already has it (b7e2f4a19c33 for the KB).
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.add_column(
        "platform_questions",
        sa.Column("embedding", Vector(1024), nullable=True),
    )
    # 1..5. A smallint with a CHECK rather than a PG enum: the vocabulary is
    # ordinal and gets compared with <=, which an enum makes awkward, and adding
    # a level must not be a type migration.
    op.add_column(
        "platform_questions",
        sa.Column("difficulty", sa.SmallInteger(), nullable=False, server_default="3"),
    )
    op.create_check_constraint(
        "ck_platform_question_difficulty",
        "platform_questions",
        "difficulty BETWEEN 1 AND 5",
    )
    # Which embedding produced the vector, so a provider change is detectable
    # rather than silently mixing geometries in one column.
    op.add_column(
        "platform_questions",
        sa.Column("embedded_model", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "platform_questions",
        sa.Column("embedded_at", sa.DateTime(timezone=True), nullable=True),
    )

    # THE HOT-PATH INDEX. Retrieval always filters by specialization and active
    # before it ranks, so this is what makes the filtered slice cheap to reach.
    # `difficulty` rides along so a difficulty band is answered from the index.
    op.create_index(
        "ix_platform_questions_retrieval",
        "platform_questions",
        ["specialization_id", "active", "difficulty"],
    )

    # The spine join, on the TRACK. Nullable forever: the catalogue predates the
    # hierarchy and no invented value would be honest.
    op.add_column(
        "platform_specializations",
        sa.Column("department_id", sa.String(), nullable=True),
    )
    op.add_column(
        "platform_specializations",
        sa.Column("course_id", sa.String(), nullable=True),
    )
    op.create_foreign_key(
        "fk_platform_spec_department", "platform_specializations",
        "departments", ["department_id"], ["id"],
    )
    op.create_foreign_key(
        "fk_platform_spec_course", "platform_specializations",
        "academic_courses", ["course_id"], ["id"],
    )
    # Every FK column is indexed here — tests/test_codebase_guards.py:496
    # (test_every_foreign_key_column_is_indexed) enforces it.
    op.create_index("ix_platform_spec_department", "platform_specializations", ["department_id"])
    op.create_index("ix_platform_spec_course", "platform_specializations", ["course_id"])


def downgrade() -> None:
    op.execute("SET LOCAL lock_timeout = '5s'")
    op.execute("SET LOCAL statement_timeout = '60s'")
    op.drop_index("ix_platform_spec_course", table_name="platform_specializations")
    op.drop_index("ix_platform_spec_department", table_name="platform_specializations")
    op.drop_constraint("fk_platform_spec_course", "platform_specializations", type_="foreignkey")
    op.drop_constraint("fk_platform_spec_department", "platform_specializations", type_="foreignkey")
    op.drop_column("platform_specializations", "course_id")
    op.drop_column("platform_specializations", "department_id")
    op.drop_index("ix_platform_questions_retrieval", table_name="platform_questions")
    op.drop_constraint("ck_platform_question_difficulty", "platform_questions", type_="check")
    op.drop_column("platform_questions", "embedded_at")
    op.drop_column("platform_questions", "embedded_model")
    op.drop_column("platform_questions", "difficulty")
    op.drop_column("platform_questions", "embedding")
    # The extension stays: the KB uses it. Same choice b7e2f4a19c33 made.
```

### The model additions

```python
# app/models/voice_platform.py — added to PlatformQuestion

    # vector(1024), NOT dimensionless. pgvector cannot index a column without a
    # typmod, so Vector() would foreclose the HNSW index this table is expected
    # to need one day. 1024 is mistral-embed's width (app/ai/embeddings.py).
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1024), nullable=True)
    difficulty: Mapped[int] = mapped_column(SmallInteger, nullable=False, server_default="3")
    embedded_model: Mapped[str | None] = mapped_column(String(64), nullable=True)
    embedded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
```

### The HNSW index — deliberately NOT created yet

```sql
-- DO NOT RUN THIS YET. See "HNSW cannot pre-filter" above.
--
-- Build it when the FILTERED slice — questions for one specialization, active —
-- routinely exceeds ~50,000 rows. Below that an exact scan is faster, exactly
-- ranked, and has no recall cliff.
--
-- m=16 / ef_construction=64 are pgvector's defaults and are correct until
-- measured otherwise. Cosine, matching app/knowledge.py's `<=>`.
CREATE INDEX CONCURRENTLY ix_platform_questions_embedding_hnsw
    ON platform_questions
    USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- And the session settings that make a FILTERED HNSW query correct rather than
-- silently short. Without iterative_scan the index returns ef_search candidates,
-- the WHERE drops most of them, and you get back fewer rows than LIMIT.
SET hnsw.iterative_scan = strict_order;
SET hnsw.ef_search = 100;
```

**The better index, when the time comes**, is partial — one per track. It is
genuinely pre-filtered, which is what the brief actually wanted, and it is only
viable because the set of specializations is small and administrator-managed:

```sql
CREATE INDEX CONCURRENTLY ix_pq_emb_<spec>
    ON platform_questions USING hnsw (embedding vector_cosine_ops)
    WHERE specialization_id = '<id>' AND active;
```

---

## Part 2 — The query

House style, not a new one: SQLAlchemy Core with pgvector's `cosine_distance()`
comparator, exactly as `app/knowledge.py:169` does. It renders `embedding <=> :p`
and binds the vector as a real parameter — no string interpolation, no
`'[1,2,3]'::vector` casting.

Three things are lifted from `app/knowledge.py` on purpose:

- **The distance floor.** Without it KNN *always* returns something — the nearest
  rows, however far. Here that means Nova confidently asking a Thermodynamics
  student a Python question because it was the least-bad match in an empty slice.
  The floor is what makes "no relevant question" an honest answer.
- **The hybrid blend** with `ts_rank`, because pure vector search misses exact
  technical terms that matter ("TCP", "Carnot", "p-value").
- **Degrading to full-text when the embedder is down.** `embed()` swallows all
  errors and returns `None` (`app/ai/embeddings.py:97-99`). A dead provider
  mid-interview must mean "questions ranked by keyword", never a dropped call.

```python
"""app/voice_platform/storage/questions.py

Retrieval over the placement office's question catalogue, replacing the
write-only OpenSearch `question-vectors` index.

SYNCHRONOUS on purpose. app/db.py holds exactly one engine and it is sync
(psycopg 3, pool 20 + 20 overflow, 5 s timeout); there is no async engine and no
asyncpg anywhere in the repository. Adding one here would open a SECOND pool
competing for the same RDS max_connections on a db.t4g.micro whose 1 GB of RAM
already makes connection memory the binding constraint. The live interview calls
this through asyncio.to_thread, which is the pattern app/routers/interview.py
already uses for every turn write.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final

from sqlalchemy import Float, String, bindparam, cast, func, literal, or_, select
from sqlalchemy.orm import Session

from ...ai.embeddings import embed, embedder_configured
from ...models.voice_platform import PlatformQuestion, PlatformSpecialization

log = logging.getLogger("reep.platform.questions")

#: Candidates pulled per branch before the blended re-rank. Same value and same
#: reason as app/knowledge.py:43.
_CANDIDATE_POOL: Final[int] = 24

#: Weight on the cosine signal; the remainder goes to full-text. 50/50, matching
#: the KB — technical interview questions are exactly the case where an exact
#: keyword match ("normalisation", "Carnot") is as informative as similarity.
_COSINE_WEIGHT: Final[float] = 0.5

#: The gate. Above this distance a "match" is noise, and returning it would have
#: the interviewer ask an off-track question with total confidence. Calibrated
#: with the KB's own floor (app/knowledge.py:56) because it is the same embedder
#: and therefore the same geometry.
_MAX_VEC_DISTANCE: Final[float] = 0.32


@dataclass(frozen=True, slots=True)
class RetrievedQuestion:
    """Staff-authored catalogue text. Rule 1: never a student's record."""

    id: str
    text: str
    phase: str
    difficulty: int
    rubric: str | None
    score: float


def search_questions(
    db: Session,
    *,
    specialization_id: str,
    topic: str,
    phase: str | None = None,
    max_difficulty: int | None = None,
    exclude_ids: frozenset[str] = frozenset(),
    limit: int = 3,
) -> list[RetrievedQuestion]:
    """Top questions for one track, ranked by blended keyword + cosine.

    `specialization_id` is resolved by the CALLER, before the interview starts —
    never from anything the model said. A tool argument that could widen the
    filter is a tool argument that can cross tenants.
    """
    limit = max(1, min(limit, 10))
    topic = (topic or "").strip()

    # THE METADATA FILTER, applied in the WHERE of both branches so the planner
    # can use ix_platform_questions_retrieval before it ranks anything. This is
    # the "filter first" the brief asked for, and it works precisely because
    # there is no ANN index to pre-empt it — an exact scan over a few hundred
    # already-filtered rows beats any approximate walk over the whole table.
    base_where = [
        PlatformQuestion.specialization_id == specialization_id,
        PlatformQuestion.active.is_(True),
    ]
    if phase:
        base_where.append(PlatformQuestion.phase == phase)
    if max_difficulty is not None:
        base_where.append(PlatformQuestion.difficulty <= max_difficulty)
    if exclude_ids:
        base_where.append(PlatformQuestion.id.notin_(exclude_ids))

    cols = (
        PlatformQuestion.id,
        PlatformQuestion.text,
        PlatformQuestion.phase,
        PlatformQuestion.difficulty,
        PlatformQuestion.rubric,
    )
    cand: dict[str, dict] = {}

    # ---- full-text branch -------------------------------------------------
    if topic:
        ts_vector = func.to_tsvector("english", PlatformQuestion.text)
        ts_query = func.plainto_tsquery(
            "english", bindparam("q", value=topic, type_=String)
        )
        rank = func.ts_rank(ts_vector, ts_query).label("rank")
        rows = db.execute(
            select(*cols, rank)
            .where(*base_where, ts_vector.op("@@")(ts_query))
            .order_by(rank.desc())
            .limit(_CANDIDATE_POOL)
        ).all()
        for r in rows:
            cand[r.id] = {
                "id": r.id, "text": r.text, "phase": r.phase,
                "difficulty": r.difficulty, "rubric": r.rubric,
                "rank": float(r.rank or 0.0), "distance": None,
            }

    # ---- vector branch ----------------------------------------------------
    query_vec: list[float] | None = None
    if topic and embedder_configured():
        vecs = embed([topic])          # swallows provider errors, returns None
        if vecs:
            query_vec = vecs[0]

    if query_vec is not None:
        dist_expr = PlatformQuestion.embedding.cosine_distance(query_vec)
        distance = dist_expr.label("distance")
        rows = db.execute(
            select(*cols, distance)
            .where(
                *base_where,
                PlatformQuestion.embedding.isnot(None),
                dist_expr <= _MAX_VEC_DISTANCE,   # the floor, DB-side
            )
            .order_by(distance)
            .limit(_CANDIDATE_POOL)
        ).all()
        for r in rows:
            existing = cand.get(r.id)
            if existing is not None:
                existing["distance"] = float(r.distance)
            else:
                cand[r.id] = {
                    "id": r.id, "text": r.text, "phase": r.phase,
                    "difficulty": r.difficulty, "rubric": r.rubric,
                    "rank": 0.0, "distance": float(r.distance),
                }

    # ---- no topic, or nothing matched: fall back to the curated order -----
    if not cand:
        rows = db.execute(
            select(*cols)
            .where(*base_where)
            .order_by(PlatformQuestion.order_index)
            .limit(limit)
        ).all()
        return [
            RetrievedQuestion(r.id, r.text, r.phase, r.difficulty, r.rubric, 0.0)
            for r in rows
        ]

    # ---- blend ------------------------------------------------------------
    max_rank = max((c["rank"] for c in cand.values()), default=0.0) or 1.0
    out: list[RetrievedQuestion] = []
    for c in cand.values():
        ft = c["rank"] / max_rank
        sim = (1.0 - c["distance"]) if c["distance"] is not None else None
        score = (
            (1 - _COSINE_WEIGHT) * ft + _COSINE_WEIGHT * sim
            if sim is not None
            else ft
        )
        out.append(
            RetrievedQuestion(
                c["id"], c["text"], c["phase"], c["difficulty"], c["rubric"],
                round(score, 6),
            )
        )
    out.sort(key=lambda q: q.score, reverse=True)
    return out[:limit]
```

### Keeping the vectors current

The OpenSearch path indexed on `POST` only, never on `PATCH`, never removed on
`DELETE`. Since the vector now lives in the same row as the text, that class of
drift is structurally impossible — but the column still has to be filled. One
required index, and one hook in each write path:

```sql
CREATE INDEX ix_platform_questions_fts
    ON platform_questions USING gin (to_tsvector('english', text));
```

```python
# app/voice_platform/storage/aurora.py — in create_question and update_question,
# after the text is set and before commit.
def _stamp_embedding(row: PlatformQuestion) -> None:
    """Best-effort. A question with no vector is still findable by full-text;
    a question that could not be SAVED because an embedding provider was down
    is a support call."""
    if not embedder_configured():
        return
    vecs = embed([row.text])
    if vecs:
        row.embedding = vecs[0]
        row.embedded_model = settings.embedding_model or "mistral-embed"
        row.embedded_at = datetime.now(timezone.utc)
```

Plus a backfill mirroring `embeddings.reembed_all` (`app/ai/embeddings.py:129`)
for rows written before this shipped, batching at 64.

---

## Part 3 — The Nova tool

### The specification

Matches `_SCORECARD_TOOL` exactly, including the detail that trips people up:
**`inputSchema.json` is a STRING**, not an object (`interview_nova.py:285`).

```python
# app/interview_nova.py

_FETCH_TOOL_NAME: Final[str] = "fetch_interview_questions"

# NOTE what is ABSENT: there is no department, course, or specialization
# argument. The track is fixed when the socket opens and the model cannot widen
# it. A tenancy filter the model can name is a tenancy filter the model can get
# wrong, and in a multi-tenant college that is one department's questions in
# another department's interview.
_FETCH_SCHEMA: Final[str] = json.dumps(
    {
        "type": "object",
        "properties": {
            "topic": {
                "type": "string",
                "description": (
                    "What you want to ask about next, in a few words — the "
                    "concept or skill, not a full question. E.g. 'normalisation "
                    "trade-offs' or 'handling a missed deadline'."
                ),
            },
            "phase": {
                "type": "string",
                "enum": ["opening", "probing", "deep_dive", "wrap_up"],
                "description": "The interview phase you are currently in.",
            },
            "max_difficulty": {
                "type": "integer",
                "description": "1-5. Ask easier when the candidate is struggling.",
            },
        },
        "required": ["topic", "phase"],
    }
)

_FETCH_TOOL: Final[dict[str, Any]] = {
    "toolSpec": {
        "name": _FETCH_TOOL_NAME,
        "description": (
            "Fetch questions from the placement office's own bank for this "
            "track. Call it when you need the next question and the bank has "
            "not already given you one. Never read the result aloud verbatim "
            "or mention that you looked anything up — rephrase it naturally as "
            "your own next question."
        ),
        "inputSchema": {"json": _FETCH_SCHEMA},
    }
}
```

Registered alongside the scorecard on `promptStart` (`interview_nova.py:1019`):

```python
"toolConfiguration": {"tools": [_SCORECARD_TOOL, _FETCH_TOOL]},
```

### Blocker fix 1 + 2 — dispatch, and a keyed pending map

```python
# was:  self._pending_tool: dict[str, Any] | None = None
# now:  keyed by contentId, because _on_content_end settles on ANY TOOL-typed
#       contentEnd and two tools in flight would cross-settle.
self._pending_tools: dict[str, dict[str, Any]] = {}
self._tool_seq: int = 0          # separate from _note_seq — blocker 3

_TOOL_HANDLERS: Final[frozenset[str]] = frozenset(
    {_SCORECARD_TOOL_NAME, _FETCH_TOOL_NAME}
)


def _on_tool_use(self, payload: dict[str, Any]) -> None:
    """Held, not settled: the tool block is not finished until its contentEnd,
    and answering the model mid-block is how a stream ends in a
    ValidationException on the last event of an otherwise perfect interview."""
    name = str(payload.get("toolName") or "")
    if name not in _TOOL_HANDLERS:
        self._log.warning("Ignoring unexpected tool call %r", name)
        return
    key = str(payload.get("contentId") or payload.get("toolUseId") or "")
    self._pending_tools[key] = payload


async def _on_content_end(self, payload: dict[str, Any]) -> None:
    ...
    elif kind == "TOOL":
        key = str(payload.get("contentId") or "")
        call = self._pending_tools.pop(key, None)
        # A contentId we never saw a toolUse for: settle the only one in flight
        # rather than stranding it, but say so.
        if call is None and len(self._pending_tools) == 1:
            self._log.warning("TOOL contentEnd %r did not match; settling the single pending call", key)
            _, call = self._pending_tools.popitem()
        if call is None:
            return
        if str(call.get("toolName")) == _SCORECARD_TOOL_NAME:
            await self._settle_scorecard(call)
        else:
            await self._settle_question_fetch(call)
```

### Blocker fix 3 + the handler

```python
async def _send_tool_result(self, tool_use_id: str, body: dict[str, Any]) -> None:
    """The contentStart / toolResult / contentEnd triple.

    contentName comes from _tool_seq, NOT _note_seq: the old name was derived
    from the control-note counter, so two tool results between two notes
    collided on one name.
    """
    if not tool_use_id:
        return
    self._tool_seq += 1
    name = f"{self._prompt_name}-toolresult-{self._tool_seq}"
    try:
        await self._upstream_send({"event": {"contentStart": {
            "promptName": self._prompt_name, "contentName": name,
            "interactive": False, "type": "TOOL", "role": "TOOL",
            "toolResultInputConfiguration": {
                "toolUseId": tool_use_id, "type": "TEXT",
                "textInputConfiguration": {"mediaType": "text/plain"},
            },
        }}})
        await self._upstream_send({"event": {"toolResult": {
            "promptName": self._prompt_name, "contentName": name,
            "content": json.dumps(body),
        }}})
        await self._upstream_send({"event": {"contentEnd": {
            "promptName": self._prompt_name, "contentName": name,
        }}})
    except Exception as exc:  # noqa: BLE001
        self._log.warning("Could not answer tool %s: %s", tool_use_id, exc)


#: The live student is WAITING while this runs. _pump_upstream is the only task
#: reading the stream and its handlers run inline, so every millisecond here is
#: silence in the room. Two seconds is already a long pause in a spoken
#: conversation; past that, ask the model to carry on unaided.
_FETCH_TIMEOUT_S: Final[float] = 2.0


async def _settle_question_fetch(self, call: dict[str, Any]) -> None:
    tool_use_id = str(call.get("toolUseId") or "")
    if self._on_fetch_questions is None:
        await self._send_tool_result(tool_use_id, {"questions": []})
        return

    content = call.get("content")
    try:
        args = json.loads(content) if isinstance(content, str) else (content or {})
    except (TypeError, ValueError):
        args = {}

    topic = str(args.get("topic") or "")[:200]
    phase = str(args.get("phase") or self._phase.value)
    max_difficulty = args.get("max_difficulty")

    try:
        async with asyncio.timeout(_FETCH_TIMEOUT_S):
            # to_thread, never a direct call: the hook is synchronous SQLAlchemy
            # and this loop carries every other live interview's audio.
            questions = await asyncio.to_thread(
                self._on_fetch_questions, topic, phase, max_difficulty,
                frozenset(self._asked_question_ids),
            )
    except TimeoutError:
        # Before Exception, deliberately: since 3.11 TimeoutError IS an OSError.
        self._log.error("Question fetch exceeded %.1fs", _FETCH_TIMEOUT_S)
        questions = []
    except Exception as exc:  # noqa: BLE001 - a failed lookup never ends a call
        self._log.error("Question fetch failed: %s", exc)
        questions = []

    for q in questions:
        self._asked_question_ids.add(q["id"])

    # An empty list is a legitimate answer and the description tells the model
    # what to do with it. Never fabricate a question to fill the silence.
    await self._send_tool_result(tool_use_id, {"questions": questions})
```

### The injected hook — Rule 1's boundary

```python
# app/routers/interview.py — beside _make_turn_writer

def _make_question_fetcher(specialization_id: str):
    """Synchronous on purpose: the engine calls it on a worker thread.

    Its own short-lived Session per call, never a long-lived one — a 15-minute
    Session pins a pooled connection and holds an idle transaction open, and
    this pool is 20 + 20 for the whole process.

    The specialization is BOUND HERE, at socket-open time, from the student's
    own enrolment. The model supplies only the topic. Rule 1 holds: what
    crosses back is staff-authored catalogue text and nothing from any record.
    """
    def fetch(topic: str, phase: str, max_difficulty, exclude_ids):
        db = SessionLocal()
        try:
            rows = search_questions(
                db,
                specialization_id=specialization_id,
                topic=topic,
                phase=phase,
                max_difficulty=max_difficulty,
                exclude_ids=exclude_ids,
                limit=3,
            )
        finally:
            db.close()
        return [
            {"id": r.id, "question": r.text, "phase": r.phase,
             "difficulty": r.difficulty}
            for r in rows
        ]
    return fetch
```

`rubric` is deliberately **not** returned to Nova. It is the grading bar, and a
model that can see it while asking the question will teach to it aloud. It
belongs in the scorecard prompt, which is where the catalogue always said it went
and where the current compiler drops it entirely.

---

## The latency budget, which is the real constraint

This is a live speech conversation. `_pump_upstream` is the **only** task reading
the Bedrock stream and its handlers run **inline**
(`interview_nova.py:1210`), so a slow tool handler is silence in the room for
that student — and Bedrock's 8-minute wall (`nova_sonic_connection_seconds = 480`)
is spending down the whole time.

| Stage | Budget | Note |
|---|---|---|
| `to_thread` hop | ~1 ms | |
| Connection from pool | ~1 ms | pool 20 + 20, `pool_timeout=5s` |
| Embedding the topic | **150–400 ms** | ← the dominant cost, a remote HTTPS call |
| Filtered exact scan | < 5 ms | few hundred rows after the metadata filter |
| **Total** | **~200–450 ms** | acceptable as a thinking pause |

**The embedding call dominates, and the database is noise.** Optimising the SQL
past this point is optimising the wrong 2%. The two changes that would actually
matter, in order:

1. **Cache topic embeddings.** Interviewers converge on the same handful of
   topics per track; a small in-process LRU removes the network hop on a hit.
2. **Skip the embedding entirely for short topics.** A one- or two-word topic is
   a keyword, and `ts_rank` answers it better and 300 ms sooner.

If measured latency proves unacceptable, the fallback is the existing design used
better: keep pre-loading the opening questions into the prompt as today, and use
the tool **only** for deep-dive follow-ups, where a half-second pause reads as
the interviewer thinking rather than as a glitch.

---

## Retiring the OpenSearch collection

Only after retrieval is live and verified:

1. Delete `index_question_vector` and its call site (`admin.py:527-534`), and the
   `index_vector` / `vector_indexed` fields from `QuestionIn` / `QuestionOut`.
2. **Session logs are a separate decision.** `index_session_log`
   (`call_close.py:262`) is the other user of the collection. Note it is almost
   certainly already broken: it passes raw `datetime` objects into `json.dumps`,
   which raises `TypeError`, which the "a projection never fails the call"
   handler swallows — leaving `opensearch_synced` **silently always `False`**.
   Confirm against a real deployment before assuming anything is lost. CloudWatch
   Logs already holds the same information.
3. Remove the AOSS collection from `infra/cdk/reep_voice_platform/stack.py:186-240`
   and the SSM parameter at `:336`.
4. Leave `PLATFORM_OPENSEARCH_*` config keys reading empty; `search_index()`
   returns `NullIndex` and `/api/platform/admin/status` reports it off — which is
   the honest state and requires no code change.

**Saving: ~$361/month**, against a total account spend of ~$495/month.

---

## Verification

```bash
docker compose up -d
cd apps/api-py
.venv/Scripts/python -m alembic upgrade head
.venv/Scripts/python -m pytest tests/test_interview_nova.py tests/test_voice_platform_engine.py -q
.venv/Scripts/python -m pytest -q          # the whole suite; exit 0
```

Tests to add, following the grammar already in `tests/test_interview_nova.py`
(no database, no socket, no AWS — a fake browser, a fake upstream, injected
callables):

- **Registration.** Run the handshake through `_ScriptedUpstream` and assert
  `upstream.sent[1]["event"]["promptStart"]["toolConfiguration"]["tools"]`
  contains both tools. *Nothing asserts `toolConfiguration` today — this closes
  a real gap.*
- **Dispatch.** Feed a `toolUse` for each tool plus its `contentEnd(type=TOOL)`
  and assert each reaches its own handler, and that two in flight settle
  independently — the regression that blockers 1 and 2 exist for.
- **The tool result triple.** Assert `contentStart` / `toolResult` /
  `contentEnd` over `upstream.sent` with the echoed `toolUseId`, and that two
  results in one session get **different** `contentName`s (blocker 3). *Also
  not asserted anywhere today.*
- **Timeout.** A hook that sleeps past `_FETCH_TIMEOUT_S` returns
  `{"questions": []}` and does **not** close the session.
- **No embedder.** With `embedder_configured()` false, `search_questions` still
  returns full-text results.
- **The floor.** A gibberish topic returns `[]` rather than the least-bad row —
  the same property `tests/test_knowledge.py:139` pins for the KB.
- **Tenancy.** `search_questions` never returns a row from another
  specialization, whatever the topic argument says.
- **Rule 1.** Automatic: `TestRuleOne.test_the_engine_touches_no_database_and_no_orm`
  greps the module source, so any stray import fails it.

Against a real database, measure before choosing an index:

```sql
EXPLAIN (ANALYZE, BUFFERS)
SELECT id, text, embedding <=> :q AS distance
FROM platform_questions
WHERE specialization_id = :spec AND active
ORDER BY distance LIMIT 3;
```

If that is a `Seq Scan` taking under 5 ms, **the HNSW index is not needed** and
building it would trade exact ranking for approximate with nothing gained.
