> **A PROPOSAL, NOT THE API. Partly superseded, and not implemented as written.**
> The live surface is the one `app/main.py` mounts under `/api` — there is no
> `/api/v1` prefix. The role list below still names `DIRECTOR`, which was removed
> on 2026-09-10: the roles are STUDENT, MENTOR, ALUMNI and ADMIN (the Main Admin,
> and there is exactly one). Current truth is `AGENTS.md` and
> `docs/google-sign-in.md`.

# REEP v1 API and workflow contract

This document is the implementation contract for `arch/redesign-phase4`. It is additive: existing `/api` routes remain available while clients migrate to `/api/v1`.

## Authentication and roles

`GET /api/v1/auth/sso/google` starts the existing Google OIDC flow. The callback validates the Google identity, looks up the roster record, and issues the existing `reep_session` http-only cookie. The client never submits a role. The server derives one of `STUDENT`, `MENTOR`, `DIRECTOR`, `ADMIN`, or `ALUMNI` from the roster.

- Student entry: `/student`; API access is derived from `session.studentId`.
- Mentor entry: `/mentor/notebook`; API access requires `MENTOR` plus an assigned `session.mentorId`.
- Admin entry: `/director` today for the existing shell; platform-admin APIs must use explicit `ADMIN` capabilities and must not imply director business authority.
- Director entry: `/director`; programme oversight remains separate from platform administration.

The Angular role guard improves navigation only. FastAPI policy helpers are authoritative and fail closed.

## Mentor notebook lifecycle

1. `GET /api/v1/mentor/mentees` returns the caller's assigned students. A mentor with no assignment receives an empty list.
2. `POST /api/v1/mentor/notebook/students/{student_id}/entries` creates a `DRAFT` + `PRIVATE_STAFF` entry. It requires `Idempotency-Key`; retries replay the stored response.
3. `PATCH /api/v1/mentor/notebook/entries/{entry_id}` updates a draft with `expected_version`. A stale version returns `409`.
4. `POST /api/v1/mentor/notebook/entries/{entry_id}/publish` explicitly changes visibility to `STUDENT_VISIBLE` and status to `PUBLISHED`.
5. `GET /api/v1/student/mentor-notebook` returns only published, student-visible entries for the authenticated student; it never accepts a student ID.
6. `POST /api/v1/mentor/notebook/students/{student_id}/actions` records a follow-up action.
7. `POST /api/v1/mentor/notebook/entries/{entry_id}/attachments` registers private attachment metadata. Actual bytes must be uploaded to private object storage using a separate signed-upload flow and promoted only after checksum and malware validation.
8. Every mutation writes the domain row, revision where applicable, audit event, and outbox event in the same transaction.

## Cross-service event envelope

Each outbox payload contains:

```json
{
  "event_id": "unique-message-id",
  "event_type": "mentor.notebook.entry.published",
  "aggregate_type": "mentor_notebook_entry",
  "aggregate_id": "entry-id",
  "actor_id": "user-id",
  "tenant_id": "tenant-id-or-null",
  "request_id": "http-request-id-or-null",
  "correlation_id": "workflow-id-or-null",
  "payload": {"student_id": "student-id"}
}
```

A relay publishes pending outbox rows to SQS. Consumers acknowledge only after their side effect is committed, use `event_id` as a deduplication key, retry with backoff, and move poison messages to a DLQ. Workers use the job row and database authorization context as authority; they do not trust tenant or user scope from an unverified client payload.

## Vector-search workflow

Knowledge ingestion creates a document version and chunks, then queues embedding jobs. The worker validates the provider response dimension against `embedding_models.dimension` before writing `READY`. Retrieval filters by namespace and published version, prefers the active model, and retains full-text search as a fallback. Model replacement is blue/green: register, backfill, shadow compare, switch, retain the previous model, then remove it only after rollback confidence.

## Security boundaries

Private notebook content is excluded from student responses, notifications, logs, analytics, and AI prompts unless an explicit approved workflow is added. Attachment downloads require the same scope check and a short-lived signed URL. No client-provided ownership, role, student ID, or tenant ID overrides the authenticated session and database relationship. Secrets remain in Secrets Manager and never enter Git, Terraform variables, logs, or browser bundles.
