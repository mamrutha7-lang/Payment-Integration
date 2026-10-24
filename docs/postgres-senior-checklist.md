# PostgreSQL — the senior developer's checklist, and REEP measured against it

Two parts. **Part 1** is the checklist itself: the rules a veteran engineer
applies to any PostgreSQL schema regardless of product, so the database does not
become the locking, performance or scaling bottleneck five years out. **Part 2**
audits REEP's actual schema, migrations, engine configuration and
infrastructure against every rule, with evidence (file:line), a severity, and
the fix. Written 2026-09-09 against branch `feat/institutional-spine`.

---

## Part 1 — The checklist

### 1. Data integrity and type strictness

| Rule | Why |
|---|---|
| **Default to `BIGINT` (or `GENERATED ALWAYS AS IDENTITY` on `BIGINT`) for surrogate keys and any auto-incrementing counter.** | `INT` tops out at 2 147 483 647. High-volume tables cross that in production and the failure is a hard write outage. |
| **Never `UUIDv4` as a primary key on a large table without thinking.** Prefer `UUIDv7`/ULID (time-ordered) or `BIGINT` identity. | Random keys insert into random B-tree pages: fragmentation, page splits, WAL amplification and cold-cache I/O on every insert. Time-ordered keys append. |
| **Store the native `uuid` type, not a 32/36-char string.** | 16 bytes vs 33–37 bytes per key, and every FK and every index on it repeats the cost. |
| **Always `TIMESTAMP WITH TIME ZONE` (`timestamptz`). Never `timestamp`.** | A naive timestamp is a number with no meaning; server-timezone drift and DST silently corrupt it. |
| **`TEXT` for free-form text; `CHECK (length(col) <= n)` when length is a business rule.** No cargo-cult `VARCHAR(255)`. | `varchar(n)` and `text` are the same storage; the limit is only worth having when it means something. |
| **Money is `NUMERIC(p, s)` or an integer in the lowest denomination. Never `FLOAT`/`REAL`/`DOUBLE PRECISION`.** | Binary floating point cannot represent 0.1; sums and comparisons drift. The same applies to marks, percentages and grades that are compared against thresholds. |
| **`JSONB`, never `JSON`.** | `json` is stored as text and reparsed on every read; `jsonb` is binary, indexable with GIN, and supports containment operators. |

### 2. Constraint and schema control

| Rule | Why |
|---|---|
| **The database is the last line of defence: `NOT NULL`, `UNIQUE`, `CHECK`, FK.** Do not rely on application validation alone. | Every application has a second writer eventually: a script, a migration, a new service, a bug. |
| **Every FK declares its `ON DELETE` strategy explicitly** (`RESTRICT`, `CASCADE`, `SET NULL`). | The implicit default (`NO ACTION`) is a decision nobody made; cascades that were not designed delete recursively, restrictions that were not designed block cleanup. |
| **Every FK column has an index.** PostgreSQL does not create one. | A delete or update on the parent scans the whole child table for each row; joins on the FK are sequential scans. |
| **Versioned, reviewed migration files only. No ORM auto-sync in production.** | Auto-sync cannot be reviewed, staged, or rolled back, and it will drop a column to "fix" a rename. |
| **Soft deletes must be part of unique constraints and partial indexes** (`UNIQUE (...) WHERE deleted_at IS NULL`). | Otherwise a deleted row still holds the unique value and the "same" record can never be recreated. |
| **Abstract volatile structure behind views or `SECURITY DEFINER` functions where the application contract must outlive the table shape.** | Lets a table be refactored without rewriting callers. |

### 3. Naming and structure

| Rule | Why |
|---|---|
| **`snake_case`, lowercase, for every identifier.** | Unquoted identifiers fold to lowercase; camelCase means quoting forever. |
| **Descriptive names** (`customer_id`, `transaction_status`), never `c1`, `t_id`. | |
| **No systemic prefixes** (`tbl_`, `vw_`). | Clutter, and friction when a table becomes a view. |
| **Prefer `READ COMMITTED`; escalate to `REPEATABLE READ`/`SERIALIZABLE` only where correctness demands, and retry `40001`.** | Serialization failures are normal under `SERIALIZABLE` and must be designed for, not discovered. |

### 4. Indexing and query architecture

| Rule | Why |
|---|---|
| **Partial indexes** (`... WHERE status = 'active'`) for hot subsets. | Smaller, cheaper to maintain, and the planner uses them for the queries that matter. |
| **GIN on JSONB** you actually query into; expression indexes for `lower(email)` and the like. | |
| **Composite indexes with the equality column first, range column last.** | |
| **No `SELECT *` in application code.** Name columns; defer heavy `TEXT`/`JSONB` columns on list endpoints. | Memory bloat, and a new wide column slows every existing query. |
| **CTEs for readability on analytical queries** (they are inlined since PG12 unless `MATERIALIZED`). | |

### 5. Concurrency and locking

| Rule | Why |
|---|---|
| **Set `statement_timeout`, `lock_timeout` and `idle_in_transaction_session_timeout` at the role or connection level.** | One stuck query, or one connection left idle in a transaction, holds locks and pool slots until something kills it. Nothing does by default. |
| **Queues use `SELECT … FOR UPDATE SKIP LOCKED`**, never bare `FOR UPDATE`. | Parallel workers otherwise serialise on the first locked row. |
| **Advisory locks (`pg_advisory_xact_lock`) for application-level mutual exclusion** instead of pulling in Redis. | Transaction-scoped, released on commit/rollback, no extra service. |
| **Hold no transaction open across an external call** (LLM, HTTP, S3). | It is idle-in-transaction with a lock and a pooled connection for the duration of someone else's latency. |

### 6. Scaling, partitioning, storage

| Rule | Why |
|---|---|
| **Declarative range partitioning early for append-only time-series tables** (logs, audits, events, turns). | `DROP PARTITION` is instant; `DELETE WHERE created_at < x` on a 100M-row table bloats it and pins autovacuum for hours. |
| **Batch bulk deletes** (`LIMIT n` in a loop) when partitioning is not in place. | One giant delete is one giant lock and one giant WAL burst. |
| **A connection pooler in front of Postgres** (PgBouncer, RDS Proxy, Supavisor) in transaction mode. | Process-per-connection: hundreds of app connections cost memory and context switches; serverless/containers multiply them. |
| **Size `workers × (pool_size + max_overflow) × tasks` against `max_connections`**, and write the arithmetic down. | |
| **Understand TOAST**; pick `MAIN`/`EXTENDED` deliberately for very large, frequently-read columns. | |
| **Replication lag is real**: route write-then-read to the primary; reports and exports to replicas. | |

### 7. Production and operational safety

| Rule | Why |
|---|---|
| **Never add a `NOT NULL` column with a volatile default to a hot table in one statement.** Add nullable, backfill in batches, then constrain. | (A *constant* default is metadata-only since PG11 and is fine.) |
| **`CREATE INDEX CONCURRENTLY`** on any live table; it cannot run inside a transaction block. | A plain `CREATE INDEX` blocks writes for the duration of the build. |
| **`ADD CONSTRAINT … NOT VALID` then `VALIDATE CONSTRAINT`** for FKs/CHECKs on big tables. | Validation takes a weaker lock separately. |
| **Dedicated application role with least privilege.** Never the superuser; never the RDS master. A separate migration/owner role. | A compromised app connection with superuser is a compromised server. |
| **`pg_stat_statements` loaded and the extension created.** | Without it there is no answer to "which query is slow". |
| **Tune autovacuum per hot table** (`autovacuum_vacuum_scale_factor` 0.01–0.05 on high-churn tables). | The 20 % default lets a 10M-row table accumulate 2M dead tuples before cleanup. |
| **Backups: PITR, cross-region copy, and a restore that has actually been rehearsed.** | A backup that has never been restored is a hope. |

### Relational vs semi-structured — when to use which

| | Relational (tables) | Semi-structured (`JSONB`) |
|---|---|---|
| Use for | Core business objects, financial records, anything audited or joined | Third-party payloads, dynamic attributes, presentation-shaped blobs, prototypes |
| Cost | Fast lookups, low CPU, reliable plans | Larger on disk, CPU to parse, harder statistics |
| Indexing | B-tree / hash | GIN (`jsonb_path_ops` for containment) |
| Enforcement | Native types and constraints | Manual (CHECK with `jsonb_typeof`, or application) |

### Cheat sheet: the naive approach vs the senior rule

| Concern | Naive | Senior |
|---|---|---|
| Primary keys | Random UUIDv4 as text | `BIGINT` identity, or `UUIDv7` in the native `uuid` type |
| High-volume deletion | `DELETE FROM t WHERE created_at < x` | `DROP TABLE t_2026_03` (partition), or batched deletes |
| Background jobs | `SELECT … FOR UPDATE` | `SELECT … FOR UPDATE SKIP LOCKED` with a lease |
| Connections | Every container straight to Postgres | Pooler in transaction mode |
| Runaway queries | Hope | `statement_timeout` / `lock_timeout` / `idle_in_transaction_session_timeout` on the role |
| Index on a live table | `CREATE INDEX` | `CREATE INDEX CONCURRENTLY`, outside the transaction |
| App credentials | `postgres` / the RDS master | Least-privilege app role + separate migration role |

---

## Part 2 — REEP audited against the checklist

**Scope:** 44 model modules under `apps/api-py/app/models/` (~91 tables, 131 FK
columns, 171 timestamp columns), 64 Alembic migrations, the engine in
`app/db.py`, `docker-compose*.yml`, and the RDS instance in
`infra/cdk/reep_core/stack.py`.

### Scorecard

| # | Rule | Status | Evidence |
|---|---|---|---|
| 1.1 | BIGINT / identity surrogate keys | **Deviation (deliberate)** | Every surrogate PK is `String` holding `uuid4().hex` (89 tables). Two natural keys (`courses.code`, `certifications.code`). No `Integer` autoincrement anywhere, so *overflow* is not a risk; the cost is 1.2 below. |
| 1.2 | No random UUIDv4 PKs / native `uuid` type | **Deviation (acknowledged)** | Random v4 hex stored as 32-char text; 131 FK columns repeat it. Build log already lists UUIDv7 as an L4 item (`docs/institutional-spine-build-log.md:961`). |
| 1.3 | `timestamptz` everywhere | **Pass** | 171 of 171 `DateTime` columns are `DateTime(timezone=True)`; zero naive. |
| 1.4 | TEXT / meaningful length limits | **Pass (minor)** | Unbounded `String` throughout; sized strings only in `redesign.py`, `voice_platform.py`, `governance.py`, mostly meaningful (`String(2)` degree level, `String(64)` sha256, `String(320)` email). One `String(255)` (`redesign.py:300`, filename). |
| 1.5 | Money not FLOAT | **Pass** | `placement_offers.ctc_inr`, `fixed_gross_inr` are integer rupees (`offer.py:69-70`). |
| 1.5b | Compared/summed decimals not FLOAT | **Fail (low-med)** | `Float` for `marks`, `sgpa`, `cgpa`, `min_cgpa`, `progress_pct`, `hours_logged`, `required_hours`, `score`. Eligibility compares doubles: `routers/student.py:869`. |
| 1.6 | JSONB not JSON | **Pass** | Every semi-structured column is `JSONB`; zero `JSON`. One GIN (KB full-text, `knowledge.py:104`). |
| 2.1 | Constraints in the schema | **Pass** | 92 `Index()`, 41 `UniqueConstraint()`, 21 `CheckConstraint()`, 53 `index=True`; migration `c2f7a9d41e63_check_constraints_and_fk_indexes` was a dedicated pass. |
| 2.2 | Explicit `ON DELETE` on every FK | **Partial** | 116 of 131 explicit; **15 rely on the implicit `NO ACTION`** (listed in F8). |
| 2.3 | Every FK indexed | **Pass** | 131 of 131 (leading column of an index, unique, or PK). |
| 2.4 | Migrations only, no auto-sync | **Pass** | Alembic with `compare_type=True`; `create_all` only in the dev seed; `alembic check` drift is documented and fixed (`redesign.py:152-155`). |
| 2.5 | Soft delete + partial unique | **Pass** | `conversations` one-active-per-owner is `UNIQUE … WHERE deleted_at IS NULL` (`conversation.py:60`); other soft-deleted tables carry no uniques that would conflict. |
| 2.6 | Views / functions as contract | **Not used** | No views or SQL functions. Not a fault at this size; noted. |
| 3.1 | snake_case | **Pass** | Throughout. |
| 3.3 | No systemic prefixes | **Note** | 15 tables prefixed `redesign_` (a module namespace, not `tbl_`). Defensible; noted (F11). |
| 3.4 | READ COMMITTED default, explicit escalation | **Pass** | No `isolation_level` set anywhere; correctness handled by row locks and advisory locks, not serializable. |
| 4.1 | Partial indexes | **Pass** | Four: one-active conversation, `interview_sessions … WHERE status='running'`, `… WHERE deleted_at IS NULL`, live consent `WHERE revoked_at IS NULL`. |
| 4.4 | No `SELECT *` | **Pass (note)** | No raw `SELECT *`. ORM loads full entities by default; nothing defers `resume_profiles.data` or `agent_runs.trace` yet (F12). |
| 5.1 | statement / lock / idle-in-txn timeouts | **FAIL (high)** | `app/db.py` `create_engine` has no `connect_args`; no role-level `ALTER ROLE … SET`; no RDS parameter group. Only *migrations* set `SET LOCAL lock_timeout/statement_timeout`. |
| 5.2 | `SKIP LOCKED` for queues | **Pass** | `workers/leasing.py:34,93` — `with_for_update(skip_locked=True)` with lease token, owner, `lease_until`, dead-letter. |
| 5.3 | Advisory locks | **Pass** | `routers/interview.py:1137` — `pg_advisory_xact_lock(hashtext(student_id))` for the daily cap. |
| 5.4 | No transaction held across external calls | **Pass** | All three LLM call sites `db.commit()` before the model call (`routers/agent.py:330,421`, `routers/student.py:1272`). |
| 6.1 | Partitioning for append-only tables | **Not done (low now)** | `messages`, `interview_turns`, `agent_runs`, `mail_logs`, `redesign_audit_events`, `redesign_outbox_events`, `auth_tokens`, `login_days` are plain tables. Retention deletes are unbounded `IN (list)` (`retention.py:280-296`). |
| 6.3 | Connection pooler | **FAIL (medium)** | Direct connections from every uvicorn worker. No RDS Proxy / PgBouncer in CDK or compose. On the L4 list (`build-log:964`). |
| 6.4 | Pool arithmetic written down and correct | **Inconsistent** | `docker-compose.prod.yml:37` says "4 workers × (10 pool + 10 overflow) = 80"; `config.py:203-205` defaults are **20 + 20** → 4 × 40 = **160** unless the compose sets `DB_POOL_SIZE`. RDS `db.t4g.small` default `max_connections` is roughly 200. |
| 6.6 | Read replicas / lag routing | **N/A today** | Single instance; Multi-AZ is in the unrun harden phase. No reporting load yet that justifies a replica. |
| 7.1 | Additive columns safe | **Pass** | 55 `add_column`s; every `nullable=False` one carries a `server_default`; none volatile. |
| 7.2 | `CREATE INDEX CONCURRENTLY` | **Not used (documented)** | 103 `create_index`, **zero** concurrent. `env.py` runs the whole upgrade in **one transaction** (`migrations/env.py:67-68`), which forbids it. Rationale recorded at `b41c9e2d7f05:26-30` ("thousands of rows"). |
| 7.4 | Least-privilege app role | **FAIL (high)** | Dev: app connects as `reep` = `POSTGRES_USER` = **superuser** (`docker-compose.yml:11`). Prod: `DATABASE_URL` is the **RDS master user** `reep` (`stack.py:566`, `docs/aws-deployment.md:110`). No separate migration/owner role. |
| 7.5 | `pg_stat_statements` | **Not created** | No migration or runbook creates the extension; RDS preloads the library but the extension is not created. Performance Insights **is** on (`stack.py:578`). |
| 7.6 | Autovacuum tuned per hot table | **Not done** | No `postgresql_with`/`autovacuum_*` storage parameters; no RDS parameter group at all (defaults: scale factor 0.2). |
| 7.7 | Backups / PITR / restore drill | **Designed, unrun** | CDK harden phase carries 35-day retention, cross-region vault, weekly restore test; it is **not yet applied** (single-AZ, 1-day backups live as of 2026-09-07). |

### Findings, ranked

#### F1 — HIGH: no statement, lock or idle-in-transaction timeout on the application connection

`app/db.py` builds the engine with pool sizing and `pool_pre_ping` only. Nothing
bounds how long a query may run, how long a DDL or row lock may wait, or how
long a connection may sit idle inside an open transaction. Behind the ALB, one
request that opens a transaction and then blocks on anything holds a pool slot
and its row locks indefinitely; twenty of them exhaust `db_pool_size` and every
other request fails after `pool_timeout` (5 s).

Migrations already do this right (`SET LOCAL lock_timeout = '5s'` /
`statement_timeout = '60s'` in every recent migration); the application does
not.

**Fix (one line, no migration):**

```python
engine = create_engine(
    settings.sqlalchemy_url,
    pool_pre_ping=True,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout_s,
    connect_args={
        "options": "-c statement_timeout=30000 "
                   "-c lock_timeout=5000 "
                   "-c idle_in_transaction_session_timeout=60000"
    },
    future=True,
)
```

Make the three numbers `Settings` fields like the pool knobs. The retention job
and the bulk CSV exports should raise `statement_timeout` for their own session
(`SET LOCAL` inside their transaction) rather than the default being sized for
them. Better still, set them on the application role (F2) so a psql session
from a human inherits them too.

#### F2 — HIGH: the application connects as the superuser (dev) and the RDS master (prod)

`docker-compose.yml` makes `reep` the `POSTGRES_USER`, which is the cluster
superuser, and the app's `DATABASE_URL` is that account. In production the app
secret's `DATABASE_URL` is the RDS `master_username` (`rds_superuser`: can
create roles, alter any table, terminate backends). A SQL injection or a leaked
secret is therefore the whole database, not one schema's DML. The `initdb`
script already anticipates an unprivileged role ("which is exactly what this
script runs as, and is why the KB migration cannot create it itself when the
app connects as an unprivileged role").

**Fix:** two roles, applied once as the master user and then encoded in
`docker/initdb/` for dev and in the ops-task runbook for prod:

```sql
-- owner of the schema; Alembic runs as this
CREATE ROLE reep_migrate LOGIN PASSWORD '…';
GRANT ALL ON DATABASE reep_py TO reep_migrate;
ALTER SCHEMA public OWNER TO reep_migrate;
REASSIGN OWNED BY reep TO reep_migrate;          -- existing tables, sequences, types

-- what the api runs as
CREATE ROLE reep_app LOGIN PASSWORD '…';
GRANT CONNECT ON DATABASE reep_py TO reep_app;
GRANT USAGE ON SCHEMA public TO reep_app;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO reep_app;
ALTER DEFAULT PRIVILEGES FOR ROLE reep_migrate IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO reep_app;

-- F1 at the role level
ALTER ROLE reep_app SET statement_timeout = '30s';
ALTER ROLE reep_app SET lock_timeout = '5s';
ALTER ROLE reep_app SET idle_in_transaction_session_timeout = '60s';
```

`CREATE EXTENSION vector` still needs the superuser/master once per database;
that step already exists in the runbook and is unaffected. The migrate task
(`docker-compose.prod.yml:131`, the ECS one-off) gets `reep_migrate`'s URL; the
api service gets `reep_app`'s. Two secrets, one decision.

#### F3 — MEDIUM: random UUIDv4 stored as 32-character text is every primary key

89 tables use `String` PKs holding `uuid4().hex`. Three costs, all proportional
to row count: 33 bytes per key instead of 16, repeated across 131 FK columns and
~200 indexes; random insert order into every PK B-tree, which is the write
pattern that fragments `messages` and `interview_turns` first; and text
comparison for every join. At today's scale (thousands of rows) it is
invisible; at the scale partitioning becomes relevant it is the dominant index
cost.

This is a repo-wide convention, and the build log says so. **Do not retrofit
the existing tables** in one migration; converting 131 FKs is a project of its
own. Do two cheaper things:

1. Adopt a **`uuid7()` default and the native `Uuid` type for every NEW table**
   from now on (Python 3.14 ships `uuid.uuid7()`; SQLAlchemy 2.0 has
   `sqlalchemy.Uuid`). Time-ordered keys append to the B-tree, and the type is
   16 bytes. Pin it with a codebase guard: a new model whose PK is
   `String, default=_uuid` fails the test.
2. When `messages` / `interview_turns` are migrated for partitioning (F9), take
   the key-type change in the same rebuild — that is the one moment it is free.

#### F4 — MEDIUM: no connection pooler, and the pool arithmetic disagrees with itself

Every uvicorn worker opens its own SQLAlchemy pool. `config.py` defaults are
`db_pool_size=20`, `db_max_overflow=20`; the production compose's own comment
sizes `max_connections=200` on the assumption of `10 + 10` per worker. With the
defaults, four workers is 160 connections before the migrate task, the retention
sidecar, `pg_dump` and a human. On RDS `db.t4g.small` the default
`max_connections` is in the same region. Adding one more ECS task doubles it.

**Fix, in order:** (a) make the compose set `DB_POOL_SIZE=10` /
`DB_MAX_OVERFLOW=10` explicitly, or change the config default and its comment
to agree — one number, one place; (b) add a guard test that
`workers × (pool + overflow) × api_desired_count < max_connections − 20`, the
way `test_codebase_guards.py` already pins the deregistration delay against the
Nova timeout; (c) in the CDK harden phase, put **RDS Proxy** in front of the
instance (IAM task-role auth, no password in the secret at all, transaction
pooling, and it absorbs the failover pause that Multi-AZ introduces). The
interview WebSocket does no long transactions, so transaction pooling is safe
for it; the only thing to verify is that nothing relies on session state
(`SET`, advisory *session* locks — REEP uses `_xact_` locks, which are fine).

#### F5 — MEDIUM: migrations run in one transaction and never build indexes concurrently

`migrations/env.py` wraps the entire `upgrade head` in a single
`begin_transaction()`. Consequences, all already observed in this repo:
`CREATE INDEX CONCURRENTLY` is impossible; `SET LOCAL statement_timeout` in one
revision applies to every revision after it in the same run (build log line
580); and a failure in migration 60 of 64 rolls back the other 59, which is
sometimes what you want and sometimes an hour of re-running. All 103
`create_index` calls are plain and take a write lock on the table for the
build. The rationale ("thousands of rows") is correct today and is written
where the next person will read it (`b41c9e2d7f05:26-30`).

**Fix before the first million-row table, not after:**

1. `context.configure(..., transaction_per_migration=True)` in `env.py`.
   Each revision commits on its own; `SET LOCAL` stops bleeding.
2. Adopt the pattern for concurrent builds where it is needed:

   ```python
   with op.get_context().autocommit_block():
       op.create_index("ix_messages_created", "messages", ["created_at"],
                       postgresql_concurrently=True)
   ```
3. Add a guard test: every migration file newer than a fixed revision contains
   `SET LOCAL lock_timeout`. The recent ones already do; the rule should be
   mechanical.
4. For FKs and CHECKs on large tables, `ADD CONSTRAINT … NOT VALID` +
   `VALIDATE CONSTRAINT` in a second step (`f7c3a1d92e54:32` explains why it was
   *not* done there — empty tables in the same migration — which is right).

#### F6 — MEDIUM: no RDS parameter group, so no server-side timeouts, default autovacuum, no `pg_stat_statements`

The instance uses the engine-default parameter group. That means:
`statement_timeout = 0`, `idle_in_transaction_session_timeout = 0`,
`autovacuum_vacuum_scale_factor = 0.2` on every table, no
`log_min_duration_statement` (the compose sets 500 ms; RDS does not), and
`pg_stat_statements` preloaded but never `CREATE EXTENSION`ed, so the view is
empty. Performance Insights is on, which covers the "which query is slow"
question at the instance level, but not per-statement totals over time.

**Fix, in the harden phase (parameter groups are the one RDS change that does
not replace the instance; a few of these need a reboot):**

```python
rds.CfnDBParameterGroup(self, "DbParams",
    family="postgres17",
    description="reep — timeouts, autovacuum, statement tracking",
    parameters={
        "statement_timeout": "30000",
        "idle_in_transaction_session_timeout": "60000",
        "lock_timeout": "5000",
        "log_min_duration_statement": "500",
        "autovacuum_vacuum_scale_factor": "0.05",
        "autovacuum_analyze_scale_factor": "0.02",
        "shared_preload_libraries": "pg_stat_statements",  # already RDS default; pin it
        "pg_stat_statements.track": "all",
    })
```

plus `CREATE EXTENSION IF NOT EXISTS pg_stat_statements` (needs the
master/`rds_superuser`, so it belongs in the same once-per-database runbook step
as `vector`). Per-table, the high-churn queue tables deserve tighter settings
than the global one:

```python
__table_args__ = (..., {"postgresql_with": {"autovacuum_vacuum_scale_factor": "0.01",
                                             "autovacuum_vacuum_cost_delay": "2"}})
```

on `redesign_outbox_events`, `redesign_domain_jobs`, `auth_tokens` and
`messages` — the tables whose rows are updated in place (status, lease) or
deleted on a clock.

#### F7 — LOW-MEDIUM: `Float` for marks, CGPA, percentages and hours that are compared and summed

`academic_history.marks`, `academics.sgpa/cgpa`, `jobs.min_cgpa`,
`placement_criteria.min_cgpa/min_*_pct`, `certifications.required_hours`,
`student_certifications.progress_pct/hours_logged`, `courses.*_hours`,
`capability_assessments.score`, `mock_tests.score`, `mentors.weekly_hour_target`
are all `double precision`. The eligibility engine does
`latest_cgpa < min_cgpa` (`routers/student.py:869`) on doubles, and
`progress_pct` is accumulated. A CGPA of 6.05 and a threshold of 6.05 typed on
two different screens are not guaranteed to compare equal after a round trip
through JSON and back. The repo already knows this rule — the Time Allocation
Ledger stores integer half-hours for exactly this reason (`AGENTS.md`, "The
unit is the half hour") — it just was not applied to the older tables.

**Fix:** `Numeric(4, 2)` for SGPA/CGPA/thresholds, `Numeric(6, 2)` for marks,
`Numeric(5, 2)` for percentages, `Numeric(6, 1)` for hours. Type change is an
`ALTER COLUMN … TYPE numeric USING col::numeric`, a table rewrite; on tables
this size it is instant, so do it now rather than at scale. The client already
receives numbers as JSON; Pydantic `Decimal` / `condecimal` on the schemas keeps
the contract.

#### F8 — LOW: 15 foreign keys with no explicit `ON DELETE`

They inherit `NO ACTION`, which refuses the parent delete. That is the *safe*
default, but it is a default, and the file next to it has 116 FKs that say what
they mean. The fifteen:

| Column | Suggested |
|---|---|
| `students.user_id`, `mentors.user_id` (`user.py:108,145`) | `RESTRICT` — a user with a role row must be off-boarded, not deleted |
| `login_days.user_id` (`user.py:161`) | `CASCADE` — a record of days signed in has no meaning without the user |
| `students.mentor_id` (`user.py:128`) | `SET NULL` — a mentor leaving must not block, and rule 2 already treats NULL as "no group" |
| `students.cohort_id` (`user.py:126`) | `RESTRICT` — a batch with students in it is not deletable |
| `cohorts.department_id / course_id / specialization_id` (`cohort.py:34,64,67`) | `RESTRICT` |
| `academic_courses.college_id`, `academic_specializations.department_id`, `cohorts→course` (`institution.py:199,253,289`) | `RESTRICT` |
| four `created_by_id → users.id` on the hierarchy (`institution.py:159,208,264,296`) | `SET NULL` — provenance survives the author leaving |

One migration, no data movement (`ALTER TABLE … DROP CONSTRAINT … ADD
CONSTRAINT … ON DELETE …`, each under the existing `SET LOCAL lock_timeout`).

#### F9 — LOW (now): append-only tables are not partitioned and retention deletes are unbounded

`messages`, `interview_turns`, `agent_runs`, `mail_logs`,
`redesign_audit_events`, `redesign_outbox_events`, `auth_tokens`, `login_days`
grow forever or are trimmed by `retention.py`, which builds an `IN (…)` list of
every doomed id and deletes in one statement (`retention.py:280-296`). At a few
thousand conversations a night that is fine; at a few hundred thousand it is a
single long transaction holding locks on `messages`.

**Fix now (cheap):** batch the hard-delete — select at most 5 000 ids, delete,
commit, repeat until empty. **Fix later (when `messages` or `interview_turns`
approach ~10 M rows):** monthly `PARTITION BY RANGE (created_at)`, with
`pg_partman` or a scheduled task creating partitions ahead, and retention
becomes `DROP TABLE messages_2026_03`. Take the PK type change (F3) in the same
rebuild. Every one of those tables already has a `created_at`/`sent_at` index,
so the query side needs nothing.

#### F10 — LOW: 54 native PostgreSQL enum types

Native enums are strict and cheap, and the repo documents their Alembic
gotchas. Two operational facts to keep in view: a value can be added
(`ALTER TYPE … ADD VALUE`, already used three times) but **never removed or
renamed without rebuilding the type**, and inside a transaction a newly added
value cannot be used until commit. For sets that change with policy
(`upload_status`, `registration_status`) a `CHECK (status IN (…))` or a lookup
table is easier to evolve; for fixed vocabularies (`role`, `offer_channel`) the
enum is the right call. No change required; do not add enums for anything an
admin will one day want to edit.

#### F11 — INFO: `redesign_` prefix on 15 tables

A module namespace rather than a `tbl_` habit, and the module is real. It is
still a prefix that will outlive the word "redesign". If the phase-4 layer
becomes the primary one, rename at that point; otherwise leave it.

#### F12 — INFO: full-entity ORM loads on wide rows

There is no raw `SELECT *`, but `select(Model)` fetches every column, including
`resume_profiles.data`, `agent_runs.trace/citations` and `student_profiles.*`
JSONB blobs, on list endpoints that only render a title and a date. Not a
problem until `pg_stat_statements` (F6) says it is; then `load_only(...)` /
`defer(...)` on those columns is a one-line change per endpoint.

### What is already right (do not "fix" these)

- **Every timestamp is `timestamptz`**, all 171 of them.
- **Every semi-structured column is `JSONB`**; the KB uses a GIN full-text
  index plus pgvector cosine with a distance floor.
- **Every FK column is indexed** (131/131); a dedicated migration did the pass
  and a guard test (`test_codebase_guards.py:516`) enforces it for new ones.
- **Constraints live in the schema**: 21 CHECKs, 41 uniques, partial uniques
  for "one active conversation", "one running interview", "one live consent".
- **Money is integer rupees**; the ledger is integer half-hours with the reason
  written down.
- **The job queue is `FOR UPDATE SKIP LOCKED`** with lease owner, lease token,
  lease expiry, attempt counter and dead-letter; **the interview cap is a
  transaction-scoped advisory lock**; **registration decisions are
  `FOR UPDATE`** on the row.
- **No transaction is held across an LLM call**: all three sites commit first.
- **Migrations are Alembic-only**, `compare_type=True`, `alembic check` drift is
  treated as a bug, additive columns always carry a constant `server_default`,
  and every recent migration sets `lock_timeout`/`statement_timeout`.
- **Pool is sized from settings** with `pool_pre_ping`, not SQLAlchemy's
  defaults; TLS `sslmode` is passed through, not dropped.
- **Snake_case throughout**; tokens stored as sha256; soft deletes with grace
  windows and a retention job; RDS encrypted, gp3 with autoscaling storage,
  deletion protection, Performance Insights, logs exported.

### Do this, in this order

| Order | Item | Effort | Where |
|---|---|---|---|
| 1 | F1 — `connect_args` timeouts on the engine, as Settings fields | 30 min | `app/db.py`, `app/config.py` |
| 2 | F4a — make pool numbers agree (compose vs config), add the connection-budget guard test | 1 h | `docker-compose.prod.yml`, `tests/test_codebase_guards.py` |
| 3 | F2 — `reep_app` / `reep_migrate` roles; dev initdb + prod runbook; two secrets | half a day | `docker/initdb/`, `docs/aws-deployment.md`, ops task |
| 4 | F8 — explicit `ON DELETE` on the 15 FKs | 1 h | one migration |
| 5 | F7 — `Numeric` for marks/CGPA/percentages/hours | half a day | one migration + Pydantic schemas |
| 6 | F5 — `transaction_per_migration=True`; lock_timeout guard test; concurrent-index pattern documented | 2 h | `migrations/env.py`, tests |
| 7 | F9a — batch retention deletes | 1 h | `app/retention.py` |
| 8 | F3 — `Uuid` + `uuid7()` for new tables, guard test | 1 h | models convention, tests |
| 9 | F6 — RDS parameter group + `pg_stat_statements` + per-table autovacuum | with the harden apply | `infra/cdk/reep_core/stack.py` |
| 10 | F4c — RDS Proxy | with the harden apply | CDK |
| 11 | F9b — partition `messages` / `interview_turns` | when they near 10 M rows | migration + rebuild |
