# Hosting REEP on AWS

> **HISTORICAL from 2026-09-07 onward, for the Terraform parts.** `infra/aws/`
> was deleted at the cutover: CloudFormation now owns these resources through
> the CDK stacks in `infra/cdk/` (`reep-core`, `reep-edge-waf`, `reep-dr-vault`,
> `reep-voice-platform`). Every `terraform` command below is kept as the record
> of how the account was built and what each resource is FOR — that part is
> still accurate and still worth reading — but none of them can be run any
> more. For deploying and operating today, read `docs/cdk-cutover.md` and
> `infra/cdk/README.md`. The two Secrets Manager secrets described in section 3
> are still filled exactly as written; they are deliberately not owned by
> CloudFormation either.

The stack `infra/aws/` provisions, and the runbook to stand it up, operate it,
and diagnose it — with **Sentry as the one observability + traceability tool**
and **Amazon Nova (Bedrock)** as the in-account model. `terraform validate`
passes against provider `hashicorp/aws ~> 5.70`; nothing here has been applied
to a live account yet — the first apply is yours, section 3.

## 1. What gets built

```
Internet ── WAF ── CloudFront ──┬── S3 (Angular SPA, private, OAC)
                                └── /api/* ── ALB (HTTPS) ── ECS Fargate api ×2–10
                                                                │        │
                                                     RDS Postgres 17    EFS /data
                                                     (pgvector)          uploads +
                                                                         interview-audio
```

| Ask | How it is delivered |
|---|---|
| Autoscaling | ECS target tracking: CPU 60% / memory 75%, `api_min_tasks`(2) → `api_max_tasks`(10); RDS storage autoscaling 20→100 GB |
| Backups | RDS automated snapshots (point-in-time, 14 d) **and** an independent AWS Backup vault (daily, 35 d) covering the DB *and* EFS; deletion protection + final snapshot on the instance |
| Security | Private subnets for everything but the ALB; per-hop security groups; WAF managed rules + rate limit; TLS everywhere incl. CloudFront→ALB; secrets in Secrets Manager (never in task defs); the api task's IAM can invoke Nova and *nothing else*; the boot guard still refuses prod on dev credentials |
| Observability | **Sentry** — errors + performance traces from the API (`SENTRY_DSN`) and the SPA (`environment.sentryDsn`), PII off. CloudWatch keeps only what Sentry can't: raw logs, infra metrics/alarms → email, and the dropped-interview-turn tripwire |
| Traceability | One `X-Request-ID` per request: caller-supplied or minted, echoed on the response, tagged on every Sentry event, printed in every `reep.access` log line, with ALB access logs in S3 as the edge record. One id from a click to a log line |
| Claude connectivity | The `reep-claude-observer` IAM role (read-only logs/metrics/ECS/RDS) for AWS-side diagnosis, plus the Sentry MCP connector for issue-level work — see §6 |
| Voice AI on Nova | `BEDROCK_MODEL` (default `apac.amazon.nova-pro-v1:0`) drives the LLM adapter for the resume brief and the grounded assistant. The realtime interviewer runs **Nova 2 Sonic** over the bidirectional stream — §7 is the checklist for turning it on, and it is not one variable |
| Call recording | `INTERVIEW_RECORDING_ENABLED=true` (a stack variable): two WAVs per interview (student and AI tracks, deliberately unmixed) on EFS at `/data/interview-audio`, only for students whose consent grant ticks store-audio, downloadable only by a holder of `admin.interview_audio` (the Main Admin by baseline; a MENTOR only by an explicit grant) via the interview records endpoints, deleted on the 180-day retention clock |

## 2. Prerequisites

- An AWS account, `aws` CLI authenticated, Terraform ≥ 1.6.
- **Bedrock model access**: in the console (Bedrock → Model access) enable the
  Amazon Nova family in your region, or pick the inference profile for it
  (`apac.…` in ap-south-1).
- **One ACM certificate, in `var.region`, for the ALB** (`alb_acm_certificate_arn`)
  — this is what keeps the CloudFront→ALB hop encrypted, and it needs a domain
  you control, because no public CA issues for `*.elb.amazonaws.com`. Add a
  second certificate in **us-east-1** if you also want your domain on
  CloudFront itself.
  *Have no domain yet?* Omit `alb_acm_certificate_arn` and the stack builds an
  HTTP origin instead, with the ALB locked to CloudFront's IP ranges so it is
  not publicly reachable. Browsers still get TLS. **That mode is for a
  throwaway environment — never point real student records at it**; every
  apply prints the `origin_encryption` warning while it is in force.
- **`alb_origin_domain`** — a hostname you own that CNAMEs to the load
  balancer (e.g. `origin.reep.example.com`), set whenever the ALB certificate
  is set. CloudFront verifies the origin's certificate against the *origin
  domain name*, and no public CA issues for `*.elb.amazonaws.com`, so the
  origin needs a name your certificate can actually cover.
- A Sentry org with two projects (api → Python/FastAPI, web → Angular); note
  both DSNs.

### If your DNS is at Cloudflare

Three records, and **every one of them must be "DNS only" (grey cloud)**:
the ACM validation CNAMEs, the `alb_origin_domain` record, and the app record
pointing at CloudFront. An orange-cloud record breaks ACM validation and puts a
second CDN in front of a CDN. Cloudflare appends the zone to whatever you type
in *Name*, so paste `_abc123.reep` — not the full `_abc123.reep.example.com` —
or you will create `_abc123.reep.example.com.example.com` and wonder why
validation never completes.

## 3. First deployment

State lives in S3, not in the shell that happened to run the first apply — a
CloudShell session gets reclaimed, and a local `terraform.tfstate` reclaimed
with it leaves Terraform unable to manage the stack it just built. The bucket
and the DynamoDB lock table cannot be resources in this stack (a state store
cannot be described by the state it stores), so one script creates them first.
It is idempotent, so re-running it on an existing setup changes nothing.

```bash
cd infra/aws
./bootstrap-state.sh                      # writes backend.hcl
terraform init -backend-config=backend.hcl
terraform apply \
  -var alb_acm_certificate_arn=arn:aws:acm:ap-south-1:...:certificate/... \
  -var alert_email=you@bgscet.ac.in
# with a domain, add:
#  -var domain_name=reep.bgscet.ac.in
#  -var cloudfront_acm_certificate_arn=arn:aws:acm:us-east-1:...
```

Then, in order:

```bash
# 1. Fill the operator-owned secret (Terraform never overwrites it):
aws secretsmanager put-secret-value \
  --secret-id "$(terraform output -raw external_secret_arn)" \
  --secret-string '{"GOOGLE_CLIENT_ID":"…","GOOGLE_CLIENT_SECRET":"…","SENTRY_DSN":"https://…ingest.sentry.io/…","VOICE_WORKER_SECRET":""}'

# 2. Build & push the api image:
aws ecr get-login-password | docker login --username AWS --password-stdin "$(terraform output -raw api_ecr_repository | cut -d/ -f1)"
docker build -t "$(terraform output -raw api_ecr_repository):latest" apps/api-py
docker push "$(terraform output -raw api_ecr_repository):latest"

# 3. pgvector, once, as the master user (password: app secret's DATABASE_URL):
psql "postgresql://reep:…@$(terraform output -raw db_endpoint)/reep_py" \
  -c 'CREATE EXTENSION IF NOT EXISTS vector;'
# RDS is private — run this from a one-off ECS task (`aws ecs run-task` with a
# psql-capable image) or a temporary bastion; never open 5432 to the internet.

# 4. Migrate + production-safe seeds, as one-off tasks on the SAME image:
aws ecs run-task --cluster "$(terraform output -raw ecs_cluster)" \
  --launch-type FARGATE --task-definition reep-api \
  --network-configuration "awsvpcConfiguration={subnets=[…private…],securityGroups=[…api sg…]}" \
  --overrides '{"containerOverrides":[{"name":"api","command":["python","-m","alembic","upgrade","head"]}]}'
# repeat with: ["python","-m","app.seed_kb"]  and  ["python","-m","app.seed_roster","roster.csv",…]
# NEVER app.seed — it refuses on ENV=prod, and that refusal is load-bearing.

# 5. Build & publish the SPA (set the web Sentry DSN first in
#    apps/web/src/environments/environment.ts → sentryDsn):
cd apps/web && npx ng build
aws s3 sync dist/web/browser "s3://$(terraform -chdir=../../infra/aws output -raw web_bucket)" --delete

# 6. Force a fresh service deployment so tasks pick up the pushed image:
aws ecs update-service --cluster reep --service api --force-new-deployment
```

Finally: register `https://<domain>/api/auth/sso/google/callback` as the
authorised redirect URI on the Google OAuth client (docs/google-sign-in.md),
and confirm the SNS subscription email that lands in `alert_email`'s inbox.

## 3a. Doing all of this from a browser

`docs/deploy-from-chrome.md` is the same sequence written as a playbook for the
Claude Chrome extension, using AWS CloudShell as the terminal — including the
human checkpoints (read the plan before apply) and the list of things a browser
agent must never do to this account.

## 3b. Deploying from the browser (no terminal)

Once §3 has run once, every later deploy is a button in GitHub — useful when
you are not at a dev machine, and safer than pasting keys anywhere:

1. **Grant the door, once.** `infra/aws/github_oidc.tf` creates an IAM role
   GitHub Actions assumes over **OIDC** — no access key is ever stored in
   GitHub. Its trust policy is pinned to `darshani8/reep-` on `main`, so a
   fork or a stranger's pull request cannot deploy the account, and its
   permissions stop at: push this ECR repository, roll this ECS service, run a
   task in this family, write this web bucket, invalidate this distribution.
   It can read no secret and no database row.
2. **Wire the workflow, once.** Terraform prints the exact commands:
   ```bash
   terraform -chdir=infra/aws output -raw github_actions_setup   # then paste them
   ```
   (Optionally add a `WEB_SENTRY_DSN` repository *secret* to build the SPA with
   client telemetry.)
3. **Deploy.** In Chrome: **Actions → Deploy → Run workflow**, choose what to
   ship (`api-and-web` / `api-only` / `web-only`), leave migrations ticked, and
   type `deploy` in the confirm box.

What the run does, in order: build and push the image tagged with the commit
*and* `latest` → run `alembic upgrade head` as a one-off Fargate task on that
new image, **failing the deploy before rolling anything if the migration
fails** → force a new service deployment and wait for the tasks to be healthy →
build the SPA, sync it to S3 (hashed assets immutable, `index.html`
never cached) and invalidate the CloudFront entry point.

**It deploys code, never infrastructure.** `terraform apply` stays a human
action at a terminal where the plan can be read first — a button that can
silently recreate a database is not a button worth having.

## 4. Deploying updates

API: build + push the image, then `aws ecs update-service … --force-new-deployment`
(the circuit breaker rolls back a bad image on its own). Run the alembic
one-off task first whenever a release carries a migration. SPA: `ng build` +
`aws s3 sync` (CloudFront picks up hashed filenames immediately; after an
`index.html`-only change, `aws cloudfront create-invalidation --paths "/index.html"`).

## 5. Observability + traceability, in practice

- **Sentry is the pane of glass.** API exceptions, slow transactions, and SPA
  errors/route traces all land there, PII off (`send_default_pii=False`; the
  session cookie never leaves the process). Every API event carries the
  `request_id` tag.
- **The trace thread**: any response's `X-Request-ID` header → Sentry search
  `request_id:<id>` → CloudWatch Logs Insights over `/reep/api` filtering
  `rid=<id>` → the ALB access log line in S3. Same id at every hop; students'
  bug reports become greppable.
- **CloudWatch keeps the infra floor**: alarms → email for no-healthy-tasks,
  ALB 5xx, RDS storage/CPU, CPU-pegged-at-max-tasks, and the one AI tripwire —
  `Dropped interview turn` in the logs, the silent save-nothing failure the
  voice runbook in AGENTS.md documents, which no exception tracker can see.

## 6. Connecting Claude to monitor and fix

- **Sentry MCP connector** (claude.ai → Settings → Connectors → Sentry):
  authorize it once and Claude Code sessions can list issues, read events and
  traces, and go from a Sentry issue to a code fix in the same conversation.
  (It is attached to this repo's sessions already but unauthorized until you
  complete that OAuth step.)
- **AWS side**: `terraform output claude_observer_role_arn` is a read-only role
  (CloudWatch, Logs Insights, ECS, RDS, ALB describes). Give a session
  credentials that can assume it — via `aws sts assume-role` in the session, or
  an AWS MCP server configured with that role — and Claude can pull the exact
  log lines and metrics behind an alarm without any write access. Fixes ship
  through git and the deploy pipeline, never through a console session.

## 7. Voice on Nova — what is true today

Two different voices exist in this codebase:

1. **The adapter-driven text paths** (resume brief, grounded assistant,
   evaluations) run on **Amazon Nova today** via `BEDROCK_MODEL` — implemented
   in `app/ai/llm.py` as a first-class Bedrock transport with rule 1's egress
   gate still in force (`LLM_ALLOW_REMOTE_STUDENT_DATA=true` is the stack
   default, a defensible call for in-account Bedrock, which does not train on
   your traffic).
2. **The realtime speech-to-speech interviewer** (`/api/interview`) runs
   **Amazon Nova 2 Sonic** (`amazon.nova-2-sonic-v1:0`) over Bedrock's
   bidirectional stream, signed by this stack's task role with no key anywhere.
   `INTERVIEW_ENGINE` chooses between it and the on-machine engine, and it is
   the default. See `docs/interview-assistant.md` § *Engines* for what that
   means behind the socket (Nova owns the turn, the engine owns the phase; the
   scorecard is a tool call; the 8-minute stream limit caps the session).

   The OpenAI relay that served this until 2026-09 has been deleted, so there
   is no longer a second hosted engine to fall back to. **Nova has not spoken to
   a real student yet.** Everything below is what turning it on requires; none
   of it happens by deploying code.

### Turning the Nova interviewer on

The Deploy workflow ships code and never infrastructure, so steps 1–2 are a
`terraform apply` at a terminal and step 3 is a console visit.

1. **Bedrock model access** for `amazon.nova-2-sonic-v1:0`, in the account, in a
   region that serves it. As of this writing that is **us-east-1, us-west-2 and
   ap-northeast-1** — and pointedly **not ap-south-1**, where the rest of this
   stack lives. `var.nova_sonic_region` therefore defaults to Tokyo rather than
   inheriting `var.region`; the extra round trip is tens of milliseconds against
   a conversation, and the alternative is a socket that dies at the handshake.
   Re-check the model card before assuming the list has not moved.
2. **`terraform apply`.** `interview_engine` already defaults to `"nova"`; the
   task role already
   carries `bedrock:InvokeModelWithBidirectionalStream` (`infra/aws/ecs.tf`) —
   a **third, distinct** IAM action that `InvokeModel` and
   `InvokeModelWithResponseStream` do not imply. An earlier version of this page
   claimed the IAM "already permits it"; it did not, and the symptom of that
   mistake is close 4002 with an AccessDeniedException in the API log.
3. **Bump `INTERVIEW_CONSENT_VERSION`.** The consent panel names who receives
   the student's voice, and the server now supplies that label from the running
   engine (`GET /api/interview/consent` → `provider`). Changing engines changes
   the recipient, so the students who agreed to the old wording have not agreed
   to this one. `interview_consents` exists to answer *what did they agree to*;
   leaving the version alone makes it answer wrongly.
4. **Make one call yourself before a student does**, and check three things in
   the same session: that the interviewer greets you unprompted (the kick-off
   control note landed), that it does not ask two questions in a row at a phase
   boundary (`docs/interview-assistant.md` explains why that is the line to
   watch), and that a row lands in `interview_evaluations` with
   `report_status = 'ok'` — the scorecard arrives as a tool call, and a model
   that talks instead of calling the tool is the failure mode to catch here
   rather than in front of a cohort.
5. **There is no hosted rollback any more.** `interview_engine = "local"` runs
   the interview on this machine, but this stack has no model weights and no
   GPU, so on AWS that is "no interviews" rather than "different interviews".
   The honest rollback is a revert of the deletion commit — `OPENAI_API_KEY` is
   still in the operator-owned secret, untouched by any apply (`secrets.tf`
   says why). Interviews already conducted keep their rows and transcripts
   whatever you choose.

## 8. Interview call recording

Already in the engine (`app/interview_audio.py`), enabled by this stack's
`interview_recording_enabled=true`. What that means, precisely: recording is
**two independent switches** — the deployment flag AND the student's own
consent grant with the store-audio scope ticked (a separate, unticked-by-default
checkbox whose copy says staff can listen). Two WAV files per interview, one
per speaker, never mixed; capped by `INTERVIEW_RECORDING_MAX_BYTES` with a
truncation flag; stored on EFS (`/data/interview-audio`), covered by the AWS
Backup plan, retrievable only by a holder of `admin.interview_audio`, and deleted with the rest of
the interview record after `INTERVIEW_RETENTION_DAYS` (180). A student who
never ticks the box is never recorded, whatever the flag says.

## 9. Costs, roughly (ap-south-1, light term-time load)

Fargate 2×(0.5 vCPU/1 GB) ≈ $30/mo · RDS db.t4g.small single-AZ ≈ $25/mo +
storage · NAT ≈ $35/mo · ALB ≈ $20/mo · EFS/S3/CloudFront/logs ≈ $5–15/mo →
**≈ $115–140/mo** before Bedrock/OpenAI token spend; Multi-AZ RDS roughly
doubles the database line. Sentry's developer tier is free and sufficient to
start.
