# The Real-Time AI Voice Assistant platform

The dual-path (Undergraduate / Postgraduate) interview platform from the
system-architecture diagram, implemented inside the REEP API as
`apps/api-py/app/voice_platform/`. It reuses the Nova 2 Sonic interviewer the
dashboard already runs and adds the pieces the diagram has and REEP did not.

```
app/voice_platform/
  api/         admin.py         Admin Dashboard CRUD   /api/platform/admin/*
               media_bridge.py  WebSocket              /ws/media-bridge  (+ /api/platform/media-bridge)
               calls.py         call sessions          /api/platform/calls/*  (+ POST .../close)
               call_close.py    the WAV Buffer Upload handler (shared by both)
  queue/       validation.py    candidate-record validator (stdlib only — zipped into the Lambda)
               sqs.py           push/pull, one queue per degree level
               lambda_handler.py  S3 trigger: CSV/JSON -> validate -> SQS ug/pg, rejects report
               worker.py        python -m app.voice_platform.queue.worker --degree UG|PG
  engine/      nova.py          catalogue rows -> interview_matrix.Specialization -> NovaSonicSession
  streaming/   buffer.py        DualChannelBuffer (candidate = ch1, AI = ch2), the recorder hook
               mixer.py         resample / align / interleave / WAV / optional MP3
               tee.py           feeds the per-speaker recorder AND the buffer
  storage/     aurora.py        repository over app/models/voice_platform.py (Alembic b8f2d4c6a1e0)
               s3.py            recording bucket + presigned recording_s3_url
               dynamodb.py      realtime session state (UG / PG tables) with an in-memory fallback
               opensearch.py    session logs + question vectors (SigV4 via botocore, no new dependency)
  monitoring/  cloudwatch.py    loggers, PutMetricData, handler_span
               sentry.py        transactions per socket, spans per external call
infra/cdk/                      AWS CDK (Python): S3 x2, SQS x2 (+DLQs), Lambda, DynamoDB x2, OpenSearch Serverless, IAM, SSM
app/voice_platform/ssm_config.py  the api reads the stack's SSM parameters at boot (production only)
```

## Two catalogues, and which one each socket reads (B5.4, 2026-09-13)

There are now **two** interview catalogues in this repository, and this section
says which is which so nobody has to grep for it:

| | rows | keyed by | read by |
|---|---|---|---|
| the dashboard's | `interview_tracks` + `interview_bank_questions` | `(college_id, code)`, programme-wide when `college_id IS NULL` | `/api/interview` — `app/interview_tracks.resolve_specialization` |
| the platform's | `platform_specializations` + `platform_questions` | `(degree_level, key)` | `/ws/media-bridge` — `app/voice_platform/engine/nova.load_engine_config` |

**They are NOT merged, and that is a decision rather than an omission.**
04-backend-changes.md §B5.4 asks the platform to "read from `interview_tracks`",
on the premise that the two duplicate the interviewer. They do not: the platform
has compiled its rows into an `interview_matrix.Specialization` and run the
dashboard's own `NovaSonicSession`, `_open_records`, recorder, writers and close
codes since the day it shipped (`engine/nova.compile_specialization`). What is
duplicated is the **storage**, and the two schemas genuinely disagree —
`platform_specializations` is keyed on `degree_level` and carries a `rubric` per
question, neither of which `interview_tracks` has, and `interview_tracks` hangs
on the institutional spine, which the platform's does not. Retiring one CRUD
would take its admin screen, its SSM-driven status endpoint and this document
with it, for no change to what a candidate hears.

What §B5.4 is right about: **`platform_time_limits` and
`platform_recording_policies` stay**, because the per-degree time limit and
recording policy are the platform's own and B6.1's college policy does not
replace them.

If the two are ever merged, the shape to merge toward is a `degree_level` column
on `interview_tracks` and a `rubric` column on `interview_bank_questions` —
not a second reader of one table, which would put the office's tracks behind two
screens with different validation.

## How a call runs

1. The client opens `wss://<host>/api/platform/media-bridge?degree=UG&specialization=bsc-ai`
   with the ordinary `reep_session` cookie. Same wire contract as
   `/api/interview` (24 kHz PCM16 both ways, the same downstream events, the
   same close codes from `app/interview_core.py`), so the Angular interview
   client needs only the URL changed.
2. The bridge authenticates (STUDENT only), resolves the degree level and the
   specialization **from the catalogue tables**, takes the per-worker limiter
   slot, and calls the interview router's own `_open_records` — the consent
   gate (4013), the fleet-wide per-user cap (4012) and the daily cap (4015)
   apply unchanged, and an `interview_sessions` row is opened so the student's
   `/student/interviews` screen and rule 2's mentor gate see the call.
3. A `platform_call_sessions` row is written (Postgres is the source of
   truth) and projected to the degree level's DynamoDB table.
4. The catalogue rows are compiled into an `interview_matrix.Specialization`
   (persona, frameworks, syllabus, voice, and the **question bank** — a new
   optional field the four fixed MBA rows leave empty) and the per-degree
   **time limit** becomes the session cap (`NovaSonicSession(max_seconds=...)`,
   still bounded by Bedrock's 8-minute stream wall).
5. Audio is fed to the engine's `recorder=` hook through `TeeRecorder`: the
   existing per-speaker WAV recorder keeps working exactly as before, and the
   `DualChannelBuffer` stamps every frame with its wall-clock offset.
6. At close, `call_close.finish_call` renders one **stereo** WAV (candidate
   left, interviewer right, time-aligned by those stamps; MP3 via ffmpeg when
   the policy asks and ffmpeg exists — otherwise WAV, flagged in
   `recording_meta`), uploads it to the recording bucket, finalises the
   Postgres row, updates DynamoDB and indexes a session log (with the
   transcript) into OpenSearch. `recording_s3_url` is **derived on read**
   (`GET /api/platform/calls/{id}`) with the policy's TTL, never stored.

## Recording: three switches

Nothing is buffered unless **all** hold: the degree level's recording policy
(`PUT /api/platform/admin/recording-policies/UG {"enabled": true}`),
`INTERVIEW_RECORDING_ENABLED=true` on the process, and the candidate's own
live `scope_store_audio` consent grant. A policy can narrow what is kept
(format, channel layout, retention, presign TTL); it can never widen past
consent. This is AGENTS.md's "off is two independent switches", plus one.

## Candidate ingest

Bulk CSV/JSON/JSONL lands under `uploads/` in the upload bucket → the Lambda
validates every row (`queue/validation.py`; column aliases like `USN`,
`Roll No`, `Track` are accepted), pushes accepted candidates onto the **UG or
PG** queue by their `degree_level`, and writes `rejects/<key>.rejects.json`
naming each rejected row and field. `worker.py` drains a queue into
`platform_candidates` (upsert on `external_id`; a message that cannot be
stored is left for the DLQ, never silently acked). The same validator backs
`POST /api/platform/admin/candidates/bulk`, which **stores rows directly by
default** and says which path it took in `mode`.

That default used to be `auto` — queue when a queue is configured, store when
none is — and it was changed because **nothing in this repository runs
`app.voice_platform.queue.worker`**: the ingest Lambda's asset explicitly
excludes `worker.py`, and there is no compose service, ECS service or schedule
that starts it. So on a deployed stack `auto` silently turned the dashboard
upload from "stores rows" into "queues into a void": the response said
`{"mode": "queued", "accepted": 400}`, `platform_candidates` stayed empty, and
four days later the messages expired out of SQS without ever being RECEIVED —
so they never reached the DLQ either, which is the one place an operator was
told to look. `mode=queue` still works for an operator who asks for it, and the
result now carries a note saying no consumer is running. Until that worker has
a trigger, the S3 → Lambda → SQS ingest path has the same dead end.

## Configuration

Every `PLATFORM_*` variable is optional; blank means that projection is off,
and `GET /api/platform/admin/status` lists what is on. See `.env.example`.
`infra/cdk/` (AWS CDK, Python) creates the AWS resources — the buckets, the
queues and DLQs, the Lambda, the DynamoDB tables, the OpenSearch Serverless
collection, the api task role's policy — and publishes the `PLATFORM_*` values
as SSM parameters under `/reep/voice-platform/`. **The api reads them at boot**
(`ssm_config.py`: only on `ENV=prod` or with `PLATFORM_SSM_PREFIX` set; the
environment always wins; any failure leaves the setting blank and is logged),
so the Terraform-owned task definition never changes and Terraform is not part
of the platform's deploy path. Deploy order: `cdk deploy` (first time by hand
after `cdk bootstrap`, afterwards the `CDK deploy (voice platform)` workflow),
then the ordinary Deploy run so the api restarts; see `infra/cdk/README.md`.
The stack's template is pinned by `infra/cdk/tests/test_synth.py`, which
synthesises it without AWS; it has not been deployed from this repository yet.

## Tests

```
cd apps/api-py && .venv/bin/python -m pytest tests/test_voice_platform_queue.py \
    tests/test_voice_platform_mixer.py tests/test_voice_platform_s3.py \
    tests/test_voice_platform_engine.py
```

Queue validation and the Lambda run against fake SQS/S3 clients; the mixer is
checked with synthetic tones (alignment, resampling, channel separation,
saturation, the byte cap); the presigned URL is produced by a real boto3
client with dummy static credentials and asserted query by query. None of them
touches AWS or Postgres.
