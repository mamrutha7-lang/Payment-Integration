# Cost review, 2026-09-15

Account `445363794125`, region `ap-south-1` (Mumbai), with the backup vault in
`ap-southeast-1` (Singapore) and Nova Sonic reached in `ap-northeast-1` (Tokyo).
Every number here came from Cost Explorer and the live AWS APIs on 2026-09-15,
not from a pricing calculator.

## The headline

**The bill was ~$514/month at list price. $361 of it — 70% — was an OpenSearch
Serverless collection that nothing read, whose removal had been merged to `main`
on 2026-09-08 and never deployed. It was deployed on 2026-09-15; the collection
is gone and the run rate is now ~$153/month.**

Commit `ecdb37b` ("infra: remove OpenSearch — $361/month for two indexes nothing
read") deleted the collection from `reep_voice_platform/stack.py`, deleted
`storage/opensearch.py`, and dropped `platform_call_sessions.opensearch_synced`.
It was reviewed and merged the same day. At the start of this review the
collection `reep-voice` was still `ACTIVE`, still one of the deployed
`reep-voice-platform` stack's 34 resources, and still billing **$11.87/day** —
ten days after it went live and seven days after the fix was on `main`.

The evidence that it does nothing, gathered independently of the commit message:

* `grep -ri opensearch apps/api-py/` returns four hits, all of them prose — two
  docstrings, a migration and its predecessor. **Zero call sites.**
* CloudWatch `AWS/AOSS` has published no `SearchRequestRate`, no
  `IngestionRequestRate`, no `SearchableDocuments` and no `StorageUsedInS3` for
  the collection, ever.
* `standbyReplicas: ENABLED`, which is what makes it 2 OCU (1 indexing +
  1 search, each with a standby) rather than the 0.5 OCU floor.

It went live on 2026-09-05 and has cost about **$120 gross** since, of which
roughly **$83 was spent after the fix was already merged.**

### Why nobody noticed

Two reasons, and both are worth fixing rather than remembering.

**The credits hid it.** This account is running on promotional credits that are
absorbing about 75% of the bill. Grouped by service the way the console shows it
by default, OpenSearch reads **$0.00** — the credit exactly cancels the charge.
It only appears when you filter `RECORD_TYPE=Usage`:

| | 14 days |
|---|---:|
| Gross usage | $180.52 |
| Credits applied | −$136.24 |
| Tax (18% GST) | $8.05 |
| **Actually paid** | **$52.34** |

Credits are being consumed at roughly **$296/month**. When they run out the bill
does not rise gently — it lands at the gross number, which today is **$514/month
before tax, $606 after**. *Check Billing → Credits for the expiry date; the
urgency of everything below depends on it.*

**The drift check asked the wrong question.** `docs/cdk-cutover.md` step 5 runs
`detect-stack-drift`, and on 2026-09-15 that returns clean — correctly. The
deployed template still declares the collection and AWS still has it; nothing has
drifted. These are two different questions:

| Check | Asks | Would it have caught this? |
|---|---|---|
| `detect-stack-drift` | does AWS match the deployed template? | **No** — they matched |
| `cdk diff` vs `main` | does the deployed template match the repo? | **Yes**, on 2026-09-09 |

Only the first was ever run, and only by hand, once, during the cutover.
`.github/workflows/infra-drift.yml` now runs both on a schedule — see
*Automation* below.

## Where the rest of the money goes

Gross, per month, at list price. Measured over 14 days and scaled; OpenSearch is
shown at its full-month rate since it only ran 10 of those days.

| Line | $/month | Notes |
|---|---:|---|
| OpenSearch Serverless (2 OCU) | **361.0** | reads nothing |
| NAT Gateway (hours + data) | 43.6 | |
| Fargate vCPU + memory | 38.0 | 2 × (0.5 vCPU, 1 GB) |
| ALB | 17.5 | LCU charges are ~$0.001 — pure idle hours |
| RDS `db.t4g.micro`, Multi-AZ | 15.4 | |
| Public IPv4 × 3 | 11.0 | 1 NAT EIP + 2 ALB nodes |
| WAF (web ACL + rules) | 8.1 | |
| Backup recovery points + Singapore copy | 7.6 | |
| CloudWatch alarms | 5.3 | 16 alarms |
| RDS storage (gp3 40 GB billed) + backups | 3.4 | |
| Secrets Manager | 0.8 | |
| **Bedrock Nova Sonic** | **1.1** | see below |
| Everything else (S3, ECR, EFS, CloudFront, DynamoDB, SES) | ~0.8 | |
| **Total** | **~513.6** | +18% GST = **~$606** |

### The load this is carrying

| Signal | Value |
|---|---|
| ALB requests/day | 1 – 1,851 |
| Fargate CPU | **1.7%** |
| Fargate memory | 16% → 32% over 14 days |
| RDS CPU | 4.5% |
| RDS connections | 3.5 – 7.6 |
| AOSS / DynamoDB / SQS | zero |

This is an enterprise HA topology — Multi-AZ database, two tasks across two AZs,
NAT gateway, WAF, cross-region DR vault, 35-day backups with a 365-day archive —
carrying a workload that peaks at under two thousand requests a day. That is not
an argument for tearing the resilience out; it is the reason the *fixed* costs
dominate and the variable ones are invisible.

### Nova Sonic is not the problem, and it is worth saying so

The instinct is that the speech-to-speech interviewer is the thing that will
bankrupt this. Measured, it is the cheapest meaningful line on the bill.

| | 30 days | implied rate |
|---|---:|---|
| speech input | 11,222 tokens | $3.63 / M |
| speech output | 30,299 tokens | $14.52 / M |
| text input | 15,533 tokens | $0.40 / M |
| text output | 8,925 tokens | $3.32 / M |

That works out to roughly **$0.06 per completed 8-minute interview**. A thousand
students doing four rounds each is about **$240** — a one-off, not a run rate,
and less than eight months of the collection that reads nothing. The cap at
`nova_sonic_connection_seconds` (480) is what keeps it bounded, and it is doing
its job.

## What to do, in order

### 1. Deploy `ecdb37b` — $361/month — **DONE, 2026-09-15 16:12 UTC**

Deployed from `main` through `cdk-deploy.yml` (`voice-platform` / `deploy`), not
by deleting the collection by hand: hand-deleting would have left AWS and
CloudFormation disagreeing, which is the second failure this review is about.

**The diff was proven before it ran, not read afterwards.** The repo's template
was synthesised in-process and compared against the deployed template fetched
with `cloudformation get-template`, first by resource, then by property:

| | |
|---|---:|
| Deployed resources before | 34 |
| Resources after | 29 |
| Removed | `Search` (Collection), `SearchAccess`, `SearchEncryption`, `SearchNetwork`, `ParamPLATFORM_OPENSEARCH_ENDPOINT` |
| Added | 0 |
| Unchanged | 28 |

The one property change outside those five was the `aoss:APIAccessAll` statement
dropping off the `reep-api-task` policy — dead the moment the collection was.
**The `CandidateIngest` Lambda's asset hash was identical** before and after, so
no unrelated code change rode along on the deploy.

Verified after: `ListCollections` returns empty, no `OpenSearchServerless`
resource remains in the stack, both DynamoDB tables and all four SQS queues
survive, the remaining eight `PLATFORM_*` SSM parameters survive, and the api
service is `ACTIVE` at 2/2 with **both ALB targets healthy**. The api never read
the deleted parameter — `ssm_config.LOADABLE` has no `PLATFORM_OPENSEARCH_ENDPOINT`
key — so nothing about its boot changed.

This review also removed two stale `OpenSearch` mentions in `infra/cdk/app.py`,
one of which is the `description=` passed to `VoicePlatformStack` — that string
is deployed CloudFormation metadata, so it was itself a live repo-vs-AWS diff.

**Also done:** `/aws/rds/instance/reep-postgres/postgresql` and the voice
platform's bucket-notification Lambda log group had **no retention at all** and
grew forever. Both are 30 days now, matching `/reep/api`'s existing choice.
Neither held anything older than 30 days, so nothing was lost.

**An orphan sweep found nothing else**: no unattached EBS volumes, no EBS
snapshots, no AMIs, one target group, one transient ECS-managed ENI. The single
manual RDS snapshot, `reep-postgres-pre-cdk-20260907` (20 GB, ~$0.19/month), is
the pre-cutover undo and was deliberately **kept**.

### 2. Put a guardrail under it — before the credits expire

The only budget on this account is AWS's default **"My Zero-Spend Budget" at
$1.00/month**, which tracks *net* spend. It could not have seen a fully credited
$361/month resource and it will not see the next one. Replace it with a budget on
gross spend:

```sh
# A real ceiling, on gross usage, alerting before it is hit rather than after.
aws budgets create-budget --account-id 445363794125 --region us-east-1 \
  --budget '{"BudgetName":"reep-gross-monthly","BudgetLimit":{"Amount":"250","Unit":"USD"},
             "TimeUnit":"MONTHLY","BudgetType":"COST",
             "CostTypes":{"IncludeCredit":false,"IncludeDiscount":true,"UseAmortized":true}}' \
  --notifications-with-subscribers '[{"Notification":{"NotificationType":"FORECASTED",
      "ComparisonOperator":"GREATER_THAN","Threshold":80,"ThresholdType":"PERCENTAGE"},
      "Subscribers":[{"SubscriptionType":"EMAIL","Address":"bdarshan5@bgscet.ac.in"}]}]'
```

`IncludeCredit: false` is the whole point of the command.

### 3. The menu, after OpenSearch is gone

Remaining gross is about **$153/month**. Everything below is optional and each
line is a real trade, not free money.

| Action | Saves/mo | What it costs you |
|---|---:|---|
| **NAT gateway → tasks in public subnets, ALB-only ingress SG** | **~$40** | Tasks hold public IPs. Ingress stays SG-locked to the ALB; you lose a layer of defence in depth. |
| NAT gateway → NAT instance on `t4g.nano` | ~$40 | Same saving, keeps the private subnets, but it is now an EC2 box you patch. |
| **Fargate → ARM64 / Graviton** | **~$7.6** | **Built — see below.** **No capacity change at all** — same vCPU, same memory, 20% cheaper. |
| RDS Multi-AZ → Single-AZ | ~$9 | AZ failure becomes a ~20–30 min restore instead of a failover. A business decision, and one step 9b deliberately made in the other direction. |
| Consolidate CloudWatch alarms | ~$2 | Four of the sixteen are autoscaling's own. Low value. |
| Weekly rather than daily Singapore copy | ~$2.5 | A worse recovery point for very little. Not recommended. |

**VPC interface endpoints are not the answer to the NAT bill.** Replacing NAT
needs ECR (×2), Secrets Manager, CloudWatch Logs, SSM, Bedrock and SES — roughly
six interface endpoints at ~$7.30/month each, which is the NAT cost back again —
and Google's JWKS endpoint and the LLM providers are not AWS services, so they
would still need a route to the internet. Public subnets or a NAT instance are
the only two real options.

Taking the NAT and ARM64 rows gets to **~$105/month gross (~$124 with GST)**.
Taking Single-AZ as well gets to **~$96**. Against $606 today, that is an
**80–84% reduction**, and the only thing given up is the NAT layer and
(optionally) database failover.

### 3a. Graviton — **LIVE, 2026-09-16**, and the way it was first shipped was the defect

**Deployed and verified**: `reep-api:7`, both tasks reporting `cpuArch: arm64`,
`rolloutState: COMPLETED`, **0 failed tasks**. The multi-arch build took ~9
minutes under QEMU and the manifest assertion passed
(`application/vnd.oci.image.index.v1+json`, `["linux/amd64","linux/arm64"]`).
Same vCPU, same memory, ~20% less money.

**And then it sat in AWS without being in `main`, which is this review's own
lesson arriving from the other direction.** The deploy was run by passing
`-c apiArm64=true` on the command line from a `core-arm64` dropdown option, and
that flag was **not written into `cdk.json`**. That is wrong twice over:

* The deployed template carried `RuntimePlatform: ARM64` while `main` rendered
  none — the repo-vs-AWS divergence `infra-drift.yml` exists to catch. It would
  have opened an issue and gone red at 08:00 IST the next morning, **correctly**,
  and a check that is red for a known-good reason is a check people learn to
  ignore. The OpenSearch failure was *merged and never deployed*; this is
  *deployed and never merged*. One workflow catches both because it asks the
  question in both directions.
* Every other `core-*` option passes only its **own** context flag. So the next
  deploy of `core-9a`, `core-9b` or either NAT option would have rendered no
  `RuntimePlatform` and **quietly rolled the api back to x86 at twice the
  price**, with CI green, the diff unremarkable and nothing on any screen
  saying so.

The fix is one line — `"apiArm64": true` in `infra/cdk/cdk.json` — and it needed
**no deploy at all**: the repository catches up to AWS, and `cdk diff` goes
quiet. It is pinned by
`test_cdk_json_carries_the_arm64_the_deployment_is_actually_running`, which was
mutation-tested by deleting the key and confirming the guard fails.

`core-arm64` has been **removed from the `cdk-deploy.yml` dropdown**. With the
flag in `cdk.json` it would be a second source of truth for one setting, and it
was in any case a bare `reep-core` deploy gated only by the word `deploy`.

> **A context flag set at deploy time and not persisted is not configuration.
> It is a thing somebody has to remember.** `cdk.json` is the file that
> remembers, and every `-c` flag in a deploy button is a flag that will one day
> be left off.

The rest of this section is the original reasoning, which still holds.

---


`-c apiArm64=true` on `reep-core` puts `RuntimePlatform: {ARM64, LINUX}` on the
api task definition. Nothing else moves: the synth guard
`test_graviton_changes_nothing_but_the_platform` compares the two templates
property by property and asserts `Cpu`, `Memory` and the whole container
definition are identical. Same vCPU, same memory, ~20% less money — which is why
this is the only row on the menu above with no trade written beside it.

**The flag's CODE default is still false, and false renders *no*
`RuntimePlatform` property at all** — not an explicit `X86_64`,
which would be a diff against the import mirror and would fail
`test_the_database_half_does_not_touch_the_ecs_trio` on a change that alters
nothing about how the api runs.

**The order this was done in, which is the only safe one:**

1. **Merge, then run Deploy once.** `deploy.yml` builds a MULTI-ARCH manifest
   (`linux/amd64,linux/arm64`) and asserts both are present before the job can
   pass. Until that has run, the newest image is amd64-only. *(Run 72,
   2026-09-16 — build ~9 min under QEMU, assertion passed.)*
2. **Then** the flag. *(Done the same day.)*

That assertion in `deploy.yml` is now load-bearing in a way it was not when it
was written. With `apiArm64: true` in `cdk.json` the api runs ARM64
unconditionally, so the manifest check is the only thing standing between a
routine image build and a service that cannot pull. **Do not remove it.**

Flipping first gives `image Manifest does not contain descriptor matching
platform linux/arm64` — every task dies at pull, before any health check, and
the circuit breaker rolls it back. Recoverable, but it is an avoidable incident.

**Rollback is the flag alone**, with no rebuild and no retag, because the
manifest carries both architectures. That is the reason it is multi-arch rather
than arm64-only: an arm64-only image is one line simpler to publish and makes
going back a release.

Two things were verified rather than assumed, because this image is on **Python
3.14** and a `cp313` wheel will not install on it:

* **Every dependency has a Python-3.14-compatible aarch64 wheel.** All 40 direct
  pins and the known-risky transitives were checked against PyPI for a `cp314`
  or `abi3` `manylinux…aarch64` wheel — `psycopg`, `cryptography`, `awscrt`,
  `pillow`, `numpy`, `pydantic-core`, `grpcio`, `uvloop`, `tokenizers`,
  `litellm` included. Nothing compiles from source, which is also why QEMU
  emulation costs minutes rather than hours.
* **`--provenance=false`.** buildx otherwise attaches attestations that appear
  in the manifest list as `unknown/unknown` platform entries; ECR stores them
  and some pull paths mishandle them. Nothing here consumes an attestation.

QEMU rather than an `ubuntu-24.04-arm` runner is deliberate: the native runner
is faster and a fine swap later, but it is a label whose availability depends on
the plan, and a deploy path that fails with "no runner matching labels" on the
evening somebody needs to ship is the worse trade.

**That caveat is now discharged.** When this was written the arm64 image had
never been built — there was no Docker daemon in the environment, so the wheel
evidence above was from PyPI's index rather than from a build. Run 72 built it:
both architectures in the manifest, the service rolled onto `reep-api:7`, and
**zero failed tasks**. Nothing compiled from source, as predicted.


### 3b. The NAT gateway — built as an instance swap, both flags OFF

~$43.6/month at list price (hours $40.9 + $2.7 of processing on **48 GB a
month**) for the egress of a deployment that peaks at 1,851 requests a day. A
`t4g.nano` doing the identical job is **~$4.6/month** and carries **no per-GB
charge at all**, so the saving is ~$39/month.

**Why an instance and not public subnets.** Moving the tasks to public subnets
with `assignPublicIp=ENABLED` saves slightly more and was the plan until the
call sites were counted. **Seven** places pin the private subnets and
`DISABLED`:

| | |
|---|---|
| `_api_service` | the API service |
| `RetentionSchedule` | nightly sweep, 03:00 IST |
| `IdentityLedgerSchedule` | 23:30 IST |
| `DbDumpSchedule` | 01:00 IST |
| `deploy.yml` | migrations, and seed_kb |
| `ops-task.yml` | every ops task |

The three workflow call sites even hardcode `subnet-0792c0ddcdd02f34f,subnet-0a41b8c5a98485d1a`
as their defaults, and the override is a GitHub repository variable that lives
outside this repository. **Three of the seven are scheduled**, so a missed one
does not fail a deploy — it fails at 23:30, 01:00 or 03:00 with nobody watching,
and no alarm covers them (the backup alarms watch AWS Backup, not these tasks).
Swapping the route target changes ONE thing and leaves all seven working
untouched. It also keeps the egress address stable, which per-task public IPs do
not — so the question "does anything allowlist our IP?" never has to be answered.

**VPC endpoints were rejected outright**: roughly seven interface endpoints at
~$7.30/month each is ~$51/month, *more* than the gateway — and it would not even
work, because Google's JWKS, Sentry and the LLM providers are not AWS services
and Bedrock is in **Tokyo**, where an ap-south-1 endpoint cannot reach it.

**TWO FLAGS, and the middle state is the point.**

1. `core-nat-instance` → `-c natInstance=true`. Builds the instance and points
   the private default route at it. **The gateway stays up.** If egress
   misbehaves, flip back and the route returns to a gateway that never went
   away.
2. `core-nat-retire` → `-c natInstance=true -c natGateway=false`. Deletes the
   gateway and its EIP. This is where the money stops, and it demands the typed
   sentence **`EGRESS IS PROVEN`** for `core-9b`'s reason: nothing in this
   repository, this workflow's role, the template or the diff can tell you
   whether the instance is actually forwarding packets.

Deleting the gateway in the same update that first routes away from it would
make the rollback a *re-create*, and a re-created gateway gets a **new public
address** — breaking anything that allowlisted the old one, on the worst possible
day. `natGateway=false` **without** `natInstance=true` raises at synth rather
than deploying a template with no egress at all.

Two failure modes are guarded because both are total and silent:

* **`SourceDestCheck: false`.** Without it EC2 drops every packet whose source
  is not the instance. The instance is healthy, the route is correct, and every
  connection from a private subnet simply times out.
* **A systemd unit, not inline rules.** cloud-init runs user data on FIRST BOOT
  ONLY, so rules applied inline vanish on the first reboot — and the deployment
  loses egress behind a green instance.

**WHAT IS NOT PROVEN: no packet has ever crossed this instance.** The synth
guards prove the template, not that the box forwards. That is the whole reason
step 1 leaves the gateway standing, and the reason step 2 asks for a sentence a
human can only type after checking. After step 1, before step 2, run an outbound
HTTPS call from a task in a private subnet and confirm it answers.

What the swap costs honestly: a `t4g.nano` is now an instance somebody patches
(Session Manager is attached for exactly that, with no inbound port). It is a
single point of failure — but so is what it replaces: **the current gateway is
one gateway in `ap-south-1a` serving both private subnets**, so an
`ap-south-1a` failure already takes egress away from the `ap-south-1b` task.
This swap does not make availability worse; it makes the same topology cheaper.
Two gateways, the genuinely-HA answer, would be ~$87/month.


### 3c. The middle option — chosen 2026-10-03, before 3,000 students join

The owner weighed three shapes (everything as it is, ~₹20,000/month; one EC2
box running `docker-compose.prod.yml`, ~₹7,000; and this) and chose the middle
one: **keep every managed piece and take only the two rows above that give up
the least.** Expected run rate ~₹13,000–14,000/month gross with GST.

| Change | Saves/mo | Given up |
|---|---:|---|
| NAT gateway → NAT instance (§3b, already built) | ~$50 incl. data processing | a `t4g.nano` somebody patches |
| RDS Multi-AZ → Single-AZ, `dbMultiAz: false` in `cdk.json` | ~$15–25 at `db.t4g.small` | a ~1 min failover becomes a ~20–30 min recovery; backups, PITR and the 35-day retention are unchanged |

Fargate (2–10 tasks), the ALB, the WAF, the Singapore copy, the archive tier
and every alarm stay exactly as they are.

**The order is the whole safety of it**, because both changes ride `reep-core`
and the NAT flags are not in `cdk.json` yet:

1. Merge the `dbMultiAz: false` change, then run `cdk-deploy.yml` →
   `core-9b` → `deploy` (the full harden — the cutover is long done). Do it
   at night: if `dbInstanceClassTarget` (`db.t4g.small`) has not been applied
   yet, the same update resizes the instance, and on a single-AZ instance a
   class change is a reboot of a few minutes rather than a failover.
2. `core-nat-instance` → prove egress → `core-nat-retire` (`EGRESS IS PROVEN`).
3. **Then** write `natInstance: true` and `natGateway: false` into `cdk.json`
   in a follow-up. Until that lands, any bare `core-9b` deploy re-renders the
   NAT gateway and re-points the route at it — a NEW gateway with a NEW public
   address — and `infra-drift.yml` reports the repo-vs-AWS difference every
   morning. Persisting the flags before step 2 would be the opposite mistake:
   the next `core-9b` would build the instance and delete the gateway in one
   update, which is exactly the single step §3b splits in two.

**What happened on the first attempt (2026-10-03, 05:16–05:34 UTC).** Step 1
was deployed and the instance forwarded NOTHING: the proof task (an ops-task
dry run, which can only start if it reaches Secrets Manager through the route)
died with `ResourceInitializationError ... connection issue between the task and
AWS Secrets Manager`. Rolled back with a bare `core-9b` (the gateway had never
gone away, which is the whole point of the two-flag split), and the same dry run
then passed. About 13 minutes in which the api tasks had no outbound route, so
Google sign-in callbacks, mail and new interviews would have failed. The cause is
in the user data: **Amazon Linux 2023 does not ship `iptables`**, so the unit's
first rule failed with "command not found". The fix installs `iptables-nft` at
first boot; the synth guard pins it. Prove egress the same way next time: the
dry run is the check, and `core-nat-retire` waits until it passes.

**Second attempt, same day (13:19–13:45 UTC), and the swap is DONE.** The
first rerun failed at change-set validation, harmlessly: every resource here is
`Retain`, so the rollback had left the morning's instance, its security group,
role and instance profile in the account, and the named role collided. They were
deleted by hand after checking nothing referenced them. The next deploy then
built the instance and the route moved to it, and it forwarded nothing AGAIN,
for a second reason: **dnf was OOM-killed on the 512 MB `t4g.nano`** while
loading AL2023's ~80 MB metadata, so the user data stopped before the unit
existed. Fixed in place over SSM (1 GB swap, install, unit), MASQUERADE counters
moving within the minute, two ops-task dry runs green, then `core-nat-retire`.
**`Retain` struck a third time**: the retire removed `Nat` and `NatEip` from the
stack and left both RUNNING AND BILLING; deleted and released by hand. The
lesson for every future removal in this stack: a resource leaving the template
is not a resource leaving the bill. Check the account afterwards.

### 4. Two things I declined to change

**`apiCpu` stays at 512.** Dropping to 256 would save about $16/month and it is
the obvious call from the CPU graph — 1.7% average. It is the wrong call. The
interview relay carries 24 kHz PCM in both directions inside the API process
(`interview_max_sessions: 100` per worker, and `config.py` is explicit that "one
CPython process cannot carry 1000 sessions (~96 MB/s of PCM)"). CPU starvation on
a real-time audio path is not a slower page, it is choppy audio in a student's
mock interview — which is the product. Autoscaling reacts on a 60-second
cooldown, far too slow to rescue a call already in progress. $16/month is a
cheap price for headroom on the one path where latency is the feature. If you
want it anyway the knob is `apiCpu` in `infra/cdk/cdk.json`; it is a one-line
context change and a task-definition roll.

**`apiMinTasks` stays at 2.** One task would save ~$19/month and would put the
whole API in a single AZ, with every deploy a single-task event.

## Automation

`.github/workflows/infra-drift.yml` — daily at 08:00 IST, and on demand.

1. **`cdk diff` every stack against `main`.** Each stack separately, so a noisy
   core diff cannot hide a quiet platform one. `reep-core` is diffed both bare
   and with `-c hardenEcs=false`, because those render different task
   definitions and diffing only one form would report the other as drift on
   every run — a check that cries wolf daily is a check nobody reads.
2. **`detect-stack-drift`** on `reep-core` and `reep-voice-platform` — the
   cutover runbook's manual step, on a timer. Reported separately from the diff
   because they fail for opposite reasons: a diff means *deploy it*, drift means
   *reconcile it*.
3. **Gross spend by service, credits excluded** — so a credited resource is
   visible while it is still cheap to remove.

It opens one labelled issue, comments on it while the condition persists, and
closes it when everything is clean. It **never deploys**: `core-9b`'s
precondition cannot be read from this repository at all, so an auto-deploy here
could run exactly the update `cdk-deploy.yml`'s 9a/9b split exists to prevent.

The gross-spend step needs `ce:GetCostAndUsage` on `reep-github-deploy`, which
that role does not currently hold. Until it is added the step says so loudly and
reports **blank, not clean** — a cost check that silently passes because it has
no permission is worse than no cost check.

### Smaller things found on the way

* **`/aws/rds/instance/reep-postgres/postgresql` has no retention set** — it
  never expires. RDS creates this log group, not CDK, so it is an API call:
  `aws logs put-retention-policy --log-group-name /aws/rds/instance/reep-postgres/postgresql --retention-in-days 30`.
  The same is true of the Lambda log group the voice platform's bucket
  notification handler creates.
* **Fargate memory climbed 16% → 32% over 14 days** on a service with almost no
  traffic. It may be nothing more than caches warming. It is worth one look at
  the next restart; at 1 GB there is plenty of room before it matters.
* **ECR holds 23 images, 4.7 GB**, under a working "keep the last 20" lifecycle
  rule. Fine, and left alone.
* **The archive bucket has no lifecycle rule and `dbDumpArchiveYears` is still a
  placeholder**, exactly as `infra/cdk/reep_core/stack.py` says. Storage is
  negligible today and unbounded by construction; it becomes a real number once
  the college answers the retention question.
