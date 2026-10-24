> **PARTLY STALE.** Written before the 2026-09-10 role removal: DIRECTOR is not
> a role in this product. There is one office account, role ADMIN; faculty are
> MENTOR and reach a console screen only through a grant the Main Admin makes in
> Governance. Current truth is `AGENTS.md`.

# REEP Phase 4 Architecture Redesign

Branch: `arch/redesign-phase4`

This is an additive expand/contract redesign for the Angular + FastAPI + PostgreSQL REEP platform. Existing routes and tables remain available while new workflows migrate to the canonical API and new tables.

## Goals

- Separate student, mentor, director, and admin capabilities.
- Make mentor work a digital notebook rather than an append-only note form.
- Keep PostgreSQL as the system of record and use PostgreSQL `pgvector` before introducing a separate vector service.
- Make AI, embedding, notification, import, retention, and indexing work durable and retryable.
- Add tenant/institution boundaries, auditability, idempotency, optimistic concurrency, and safe cross-service communication.
- Preserve legacy student-visible mentor notes during migration.

## Target runtime

```text
CloudFront + WAF
  ├── immutable Angular assets from private S3
  └── /api/v1 and WebSocket traffic to private ALB
        ├── ECS API tasks (stateless)
        ├── ECS worker tasks consuming SQS + DLQs
        └── RDS PostgreSQL 17 + pgvector in private subnets

S3: source documents, notebook attachments, audio, generated reports
Secrets Manager: database, OAuth, AI provider and worker credentials
Outbox: PostgreSQL transaction -> worker delivery -> idempotent consumers
```

## Domains

```text
identity       users, tenants, memberships, sessions, capabilities
academic       profiles, results, attendance, courses, cohorts
placement      jobs, applications, interviews, offers
mentor         assignments, notebook entries, actions, attachments, reviews
knowledge      versioned documents, chunks, models, embeddings, retrieval events
communication  conversations, messages, notifications
platform       jobs, outbox, idempotency, audit, storage operations
```

## Role policy

Login has three supported programme entry roles—student, mentor, and admin—with the existing director oversight role retained. The browser may label the entry point, but the API never accepts a client-selected role: Google identity + roster membership + server-side role records determine the session.

- STUDENT: own profile, academics, applications, uploads, conversations, and published mentor-visible content.
- MENTOR: assigned students only; may read scoped records, create private drafts, publish student-visible notes, create actions, and perform permitted reviews.
- DIRECTOR: programme-wide business oversight and approved operational actions.
- ADMIN: platform/institution administration; not automatically a director of programme decisions.

The backend is authoritative. UI visibility is only a convenience.

## Database strategy

The first migration adds `tenants`, `tenant_memberships`, `audit_events`, `outbox_events`, `domain_jobs`, `api_idempotency_keys`, versioned knowledge/vector tables, and mentor notebook tables. Existing tables remain compatible. A later backfill adds `tenant_id` and real foreign keys to legacy domain tables, validates data, and only then changes columns to `NOT NULL`.

All external mutations use:

- `Idempotency-Key` for retry-safe commands.
- `If-Match` or an explicit `expected_version` for optimistic concurrency.
- An audit event in the same database transaction.
- An outbox event in the same database transaction.

## Vector strategy

Keep `pgvector` in RDS PostgreSQL initially. Store vectors in `knowledge_chunk_embeddings`, keyed by `embedding_models`, with a fixed dimension per model. The current Mistral path is expected to be 1024 dimensions, but workers must validate the provider response before writing `READY`.

Embedding changes are blue/green:

1. Register a new model row.
2. Create pending embedding jobs.
3. Generate vectors in a leased worker with retries.
4. Validate dimension and content hash.
5. Build/verify the ANN index.
6. Shadow compare retrieval.
7. Switch the active model.
8. Retain the previous model until rollback confidence exists.

Full-text search remains available during vector outages or partial backfills.

## Cross-service communication

The API writes domain state and an outbox event atomically. A worker publishes or handles the event idempotently. Every event carries `event_id`, `event_type`, `aggregate_type`, `aggregate_id`, `actor_id`, `tenant_id`, `request_id`, `correlation_id`, and a versioned payload.

Use SQS standard queues with DLQs for document ingestion, embeddings, imports, notifications, retention, and report generation. WebSockets remain direct and reconnectable; they are not placed behind a queue.

Workers receive least-privilege IAM roles and a signed internal job context. They must never trust tenant IDs supplied only by a client payload; the job row and database access policy are authoritative.

## Security invariants

- Unknown or staging-like environments cannot use published development secrets.
- Role changes revoke or invalidate existing privileged sessions.
- Session verification and privileged authorization fail closed when identity cannot be confirmed.
- Mentor access requires an active assignment; no assignment means no student access.
- Private notebook text is never returned to student endpoints, notifications, exports, logs, analytics, or AI context.
- Student PII reaches a remote model only through the existing egress gate and an explicit configuration decision.
- Attachments are private objects; downloads are authorization-checked and time-limited.
- Audit events survive account deletion/anonymization and are append-only to the application role.
- Secrets never enter Git, Terraform variables, logs, prompts, or client bundles.

## Rollout

1. Deploy additive migration and API behind feature flags.
2. Backfill the default tenant and current knowledge versions in staging.
3. Reconcile existing files and database rows.
4. Enable the mentor notebook for one mentor cohort.
5. Compare old and new note visibility and retrieval results.
6. Enable worker queues and monitor DLQs.
7. Migrate remaining cohorts.
8. Remove legacy paths only after a measured compatibility window.

ECS deployments use immutable image digests, an expand-compatible migration task with an advisory lock, health checks for both liveness and database readiness, and rollback to the previous task-definition revision. Database rollback is a separate restore/PITR procedure, not an automatic application rollback.

## Acceptance criteria

- Empty database upgrades to one Alembic head.
- Existing database upgrades without dropping data.
- Student, mentor, director, and admin authorization tests pass.
- Mentor private drafts never appear in student responses.
- Duplicate commands do not create duplicate notes/actions/events.
- Concurrent edits return a deterministic version conflict.
- Embedding jobs resume after worker restart and reject wrong dimensions.
- Outbox consumers are idempotent and DLQs are observable.
- Terraform plan uses immutable images, private storage, controlled IAM, backups, and a documented restore drill.
