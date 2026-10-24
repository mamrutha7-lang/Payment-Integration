"""The Real-Time AI Voice Assistant platform — the dual-path (Undergraduate /
Postgraduate) interview architecture, inside the REEP API process.

Module boundaries follow the architecture's `src/` layout one for one:

    api/         FastAPI routes — Admin Dashboard CRUD (per-degree catalogue,
                 candidates, recording policies), the /ws/media-bridge socket,
                 and the call-close handler (WAV buffer upload).
    queue/       SQS push/pull for the Undergraduate and Postgraduate candidate
                 streams, the candidate-record validator, the S3-trigger Lambda
                 that feeds the queues from bulk CSV/JSON uploads, and the
                 worker that drains them into Postgres.
    engine/      The AI Interview Engine integration: compiles the per-degree
                 catalogue rows into the SAME `Specialization` contract the
                 fixed MBA matrix uses and runs the existing Nova 2 Sonic
                 engine (app/interview_nova.py) — never a second copy of it.
    streaming/   The WebSocket media bridge's audio side: the dual-channel WAV
                 buffer (candidate = channel 1, AI response = channel 2) and the
                 mixer that renders one time-aligned stereo file at call close.
    storage/     Clients for Aurora PostgreSQL (the SQLAlchemy models in
                 app/models/voice_platform.py), Amazon S3 (recordings +
                 presigned `recording_s3_url`), Amazon DynamoDB (realtime
                 session state).

                 An Amazon OpenSearch Serverless collection held searchable
                 session logs and question vectors until 2026-09. Nothing ever
                 read it — `search`/`knn` had no callers — and the session-log
                 writer passed raw datetimes to `json.dumps`, so every write
                 raised TypeError and was swallowed. It cost $361/month.
    monitoring/  CloudWatch loggers/metrics and Sentry spans across the handlers.

WHY INSIDE `app/` AND NOT A SIBLING SERVICE. The engine the architecture names
is Nova Sonic 2, and REEP already runs it: the persona, the phase machine, the
close codes and the scorecard live in app/interview_core.py and
app/interview_nova.py and are pinned by tests. A second service would have to
copy them, and a copied phase machine drifts the first time either side gains
a field. The platform therefore ADDS the pieces the diagram has and REEP did
not — the per-degree catalogue, the queue-fed candidate roster, the S3 and
DynamoDB projections, the stereo recording — and reuses the interviewer.

THE TWO RULES STILL HOLD. Rule 1: nothing from a student's record enters the
model session; the catalogue rows are the placement office's own question
bank, and the uplink is the candidate's microphone. Rule 2: every admin route
is behind `require_admin`, and the call sessions a mentor may read go
through `_assert_can_access_student` via the linked `interview_sessions` row.

EVERY AWS PROJECTION IS OPTIONAL AND HONEST. A deployment that sets no
`PLATFORM_*` variable gets an in-memory session store, no S3 upload (the
stereo file stays on the local recording volume), a no-op search index and a
no-op queue — and every response SAYS which of those it did, so a missing bucket
is never mistaken for a recorded call.
"""
