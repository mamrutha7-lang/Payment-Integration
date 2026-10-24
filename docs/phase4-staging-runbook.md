# REEP Phase 4 staging runbook

This runbook is for a disposable or approved staging environment only. It does not authorize AWS deployment or production changes.

## Release gates

Before running anything:

- Confirm the source revision and database target are staging.
- Confirm `ENV=staging`, a unique non-development `AUTH_SECRET`, and `REEP_REQUIRE_DB=1`.
- Use PostgreSQL 17 with the `vector` extension available.
- Confirm no other migration process is running.
- Take an off-host `pg_dump -Fc` backup and record its SHA-256 checksum.
- Do not run the development fixture command `python -m app.seed`.

## Migration

From `apps/api-py`, with the staging environment loaded:

```powershell
python -m alembic current
python -m alembic heads
python -m alembic upgrade head
python -m alembic current
```

Expected final head: `d6a4e7f91b22`.

Preflight checks:

```sql
SELECT current_database(), version();
SELECT extname, extversion FROM pg_extension WHERE extname = 'vector';
SELECT COUNT(*) FROM knowledge_chunks WHERE embedding IS NOT NULL;
```

The historical pgvector migration may recreate the legacy embedding column. Stop and review the migration if existing legacy vectors are non-null. Run migrations once through the dedicated migration task, never concurrently from API replicas.

Verify:

- `redesign_*` tables exist.
- `redesign_knowledge_chunk_embeddings.embedding` is nullable until a worker writes a validated vector.
- The model and database use `redesign_notebook_status` for knowledge-version status.
- The migration head is `d6a4e7f91b22`.
- Legacy tables and rows remain present.

## Safe seed and backfill

Use only production-safe, idempotent seeds:

```powershell
python -m app.seed_kb
python -m app.seed_roster
```

Do not use `python -m app.seed` in staging-like environments because it creates development fixture data and credentials.

If embedding configuration is intentionally enabled, verify vector counts and dimensions after the seed. If it is not enabled, record that retrieval is using the full-text fallback. Phase 4 embedding jobs must use a registered model, canonical content hash, exact expected dimension, and retry-safe job ID.

## Worker smoke tests

The durable worker boundary is transport-independent and can be tested without AWS:

```powershell
python -m pytest apps/api-py/tests/test_phase4_workers.py apps/api-py/tests/test_redesign_contracts.py
```

The runtime entrypoints are:

```powershell
python -m app.worker relay --once
python -m app.worker domain --once
python -m app.worker embedding --once
```

Required worker settings:

```text
REEP_QUEUE_URL_DEFAULT=<queue URL or local fake transport configuration>
REEP_QUEUE_URL_EMBEDDING=<embedding queue URL>
REEP_WORKER_ID=<stable worker instance ID>
```

Workers claim database rows using lease tokens, commit the claim before external work, complete only with the active lease, retry with bounded backoff, and preserve the original job ID. SQS deletion occurs only after the database transition commits. Duplicate delivery is expected and must be harmless.

## Notebook authorization tests

Exercise these flows with separate Student, Mentor, Director, and Admin sessions:

- Mentor can list only assigned students.
- Mentor can create a private draft with an `Idempotency-Key`.
- Reusing the same key and body replays the same response.
- Reusing the key with a different body returns `409`.
- Student cannot retrieve a private draft.
- Publish changes visibility explicitly to student-visible.
- Student can retrieve only published entries for their own session-derived student ID.
- Stale notebook updates return `409`.
- Archive and attachment commands require idempotency keys.
- Audit and outbox records are written with the domain mutation.
- Admin capability does not imply Director programme authority.

## Vector validation

For each registered embedding model:

1. Create a pending embedding job without a fake vector.
2. Claim the job with a lease.
3. Call the provider through the strict worker adapter.
4. Reject wrong dimensions, malformed responses, and non-finite values.
5. Write `READY` only after validation and content-hash verification.
6. Keep full-text retrieval available during partial backfills.
7. Activate a new model only after shadow retrieval comparison and rollback confidence.

Student data must not be sent to a remote embedding provider unless the explicit egress policy permits it. Public knowledge text is the initial safer workload.

## Rollback and restore

Application rollback and database rollback are separate:

- For application defects, stop workers first and roll back to the previous immutable image.
- For migration defects, restore the verified backup to a disposable database and rehearse the repair before changing staging.
- The current local compose backup is a logical dump, not PITR. WAL archiving or an equivalent managed backup policy must be verified separately.
- Record migration revision, image digest, backup checksum, operator, and timestamps.

## CI and review acceptance

Required before merge:

- Python compile and full API tests pass.
- Angular build and tests pass.
- Migration upgrade succeeds on an empty database and a representative existing database.
- Worker contract and failure-injection tests pass.
- Human architecture and security review approvals are present.
- No production deployment or AWS apply is performed as part of this PR.
