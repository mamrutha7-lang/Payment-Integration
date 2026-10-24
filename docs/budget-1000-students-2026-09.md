# Budget: 1,000 students, seven mock interviews a month each (2026-09-18)

Prepared for the board. Every number here is one of two things: read from the
live AWS account `445363794125` on 2026-09-18 (Cost Explorer with credits
excluded, CloudWatch, Service Quotas, the API's own log, the running ECS/RDS
resources), or computed by `tools/cost/budget_1000_students.py` from unit
prices that account was **actually billed** on 2026-09-16. Rupee figures use
₹96 per US dollar (95.96 on 18 Sep 2026) and include 18% GST. The plan the
board is pricing is **7 interviews per student per month**, 7,000 a month;
the "two a day" figures the question started from are kept in §6 as the
ceiling. What changed on recheck, and the one measurement still worth making,
are in §7.

## 1. The answer

| The plan: 1,000 students × 7 interviews a month | |
|---|---:|
| Interviews a month | 7,000 |
| AWS bill before GST | $879 |
| With 18% GST | $1,037 |
| **Per month** | **₹1.0 lakh** |
| **Per year** | **₹12 lakh** |
| **Per student** | **₹100 a month, ₹1,200 a year** |
| Range, low to high case (§5) | ₹0.86 – ₹1.18 lakh a month |

Four things to take away.

1. **A quarter of the bill is the platform, two thirds is the interviews.**
   Web, API, database, backups, security and monitoring bill about **$180 a
   month** at list price today and need about **$40 a month** more for 1,000
   students; the 7,000 interviews cost about **$620 a month** on top, of which
   $567 is the Amazon Nova 2 Sonic speech model.
2. **One 8-minute interview costs about ₹8.5** (₹6.9 to ₹10.8 depending on how
   much each side talks), built on the one session this account has billed at
   full length (₹5.5 with the tester barely speaking). Seven a month is
   ₹60 a student; the other ₹40 is their share of the platform.
3. **₹50,000 to ₹70,000 a month buys 2 to 4 interviews a student a month, not
   7.** At these prices ₹70,000 buys about 4,100 interviews a month with the
   platform included; 7 a month is ₹1.0 lakh. The gap is ₹30,000 a month of
   speech-model time, and §6 shows every level between one a week and two a
   day so the board can choose.
4. **The plan still needs a quota increase.** 318 interviews a working day
   means 3 to 4 running at once on average and **bursts of 15 to 20** in a busy
   evening hour. AWS currently allows this account **2 simultaneous Nova 2
   Sonic interviews** (its own default is 20; no increase request is on file).
   Two streams could serve 318 a day only by running back to back for 21
   hours. The request should ask for 20 now; §8 has the ramp and the interim
   options.

## 2. What is deployed today

Angular 22 single-page app, FastAPI back end, PostgreSQL 17 — on AWS in
Mumbai (`ap-south-1`), described by AWS CDK (`infra/cdk/`), deployed from GitHub
Actions. Backup copies go to Singapore (`ap-southeast-1`); the speech model is
reached in Tokyo (`ap-northeast-1`) because it is not offered in Mumbai.

| Layer | Service (as deployed) | What it does | Measured cost today, $/month |
|---|---|---|---:|
| Edge | **CloudFront** distribution `reep.sast-skills.com` (price class 200) + **AWS WAF** web ACL `reep-edge` (managed rule sets) | Serves the app, proxies `/api/*` to the load balancer, filters attacks | 0 (free tier) + 8.2 |
| Web | **S3** bucket for the built app; S3 bucket for load-balancer logs | Static hosting | < 0.5 |
| Ingress | **Application Load Balancer** `reep-alb` | Routes to the API tasks; carries the interview WebSockets | 17.5 |
| Compute | **ECS Fargate** cluster `reep`, service `api`: 2 tasks × (0.5 vCPU, 1 GB), **ARM64 Graviton**, autoscaling 2–10 on CPU/memory | The FastAPI application, including the in-process interview relay | 21.5 |
| Network | 1 **NAT gateway** + 3 public IPv4 addresses | Egress for the private subnets (Bedrock, Google sign-in, SES) | 40.9 + 13.6 data + 10.9 |
| Database | **RDS PostgreSQL 17.9** `reep-postgres`, `db.t4g.micro`, **Multi-AZ**, 20 GB gp3 (autogrows to 100 GB), 35-day automated backups, Performance Insights | The system of record: 111 tables | 30.7 + 5.3 storage + 4.4 backups |
| Files | **EFS** (uploads, signatures, interview audio; 20 MB today; moves to Infrequent Access after 30 days) | Student documents and any recordings | ≈ 0 |
| Backup | **AWS Backup**: daily plan (database + EFS) with a copy to the Singapore vault, monthly archive plan (365 days), weekly restore test | Disaster recovery | 3.6 + 6.4 copy transfer |
| Archives | **S3**: `reep-db-dumps` (90-day `pg_dump`), `reep-db-archive` (monthly, Object Lock), `reep-documents-archive` (every uploaded file, Object Lock, never deleted), `reep-identity-ledger` (daily account ledger, Object Lock) | Portable copies that outlive the schema | < 0.5 |
| AI, interviews | **Amazon Bedrock — Nova 2 Sonic** (`amazon.nova-2-sonic-v1:0`, Tokyo) | The speech-to-speech mock interviewer | usage: $1.05 so far in September |
| AI, text | **Amazon Bedrock — Nova Pro** (APAC inference profile, Mumbai) | The REEP Agent chat and the resume builder | usage: $0.005 so far |
| Mail | **Amazon SES**, `no-reply@sast-skills.com`, configuration set `reep-transactional`, production access | Onboarding, reset, leave, badge mail | $0.16 per 1,000 messages |
| Secrets & config | **Secrets Manager** (2 secrets), **SSM Parameter Store** | Credentials, platform parameters | 0.85 |
| Monitoring | **CloudWatch**: 16 alarms, custom metrics, 4 log groups at 30-day retention; **Sentry** (3 projects, external; plan not verified — Developer is $0, Team $26/month) | Alerts and error tracking | 12.0 + Sentry |
| Scheduled jobs | ECS scheduled tasks: identity ledger 23:30, database dump 01:00, document archive 02:00, retention sweep 03:00 IST | Nightly hygiene and backups | inside the Fargate line |
| Voice platform | CDK stack `reep-voice-platform`: 2 **DynamoDB** tables, 4 **SQS** queues, 2 **Lambda** functions, 2 S3 buckets | The dual-path platform ingest; idle today (the OpenSearch collection was removed on 15 Sep) | ≈ 0 |
| Identity | **Google Sign-In** (Google Cloud OAuth client) | Login for every role | 0 |
| Delivery | **GitHub** (repository, Actions: 5 CI jobs, deploy, daily infra-drift check) | Build and deploy | within the free allowance |

DNS for `sast-skills.com` is hosted outside AWS (no Route 53 charge). There is
no AWS Support plan on the account.

## 3. What it bills today

Read from Cost Explorer with `RECORD_TYPE=Usage`, so the promotional credits do
not hide anything.

| | Gross usage | Credits applied | GST | Paid |
|---|---:|---:|---:|---:|
| August 2026 (first days live) | $21.07 | −$21.07 | $0 | $0 |
| 1–17 September 2026 | $204.37 | −$138.94 | $11.78 | $77.21 |

Of September's $204, **$118 was the OpenSearch collection** that nothing read; it
was removed on 15 September (`docs/cost-review-2026-09.md`). The run rate since
is what matters:

| Day | Gross usage |
|---|---:|
| 14 Sep (OpenSearch still up, backup charge day) | $18.73 |
| 15 Sep (removed 16:12 UTC) | $14.24 |
| **16 Sep — first clean day** | **$6.20** |

The 16 September day, item by item, is the basis of the fixed-platform line in
every scenario below: **$5.91/day ≈ $180/month** at list price, plus that
day's interview testing ($0.26) and Cost Explorer queries ($0.17). Credits
covered 68% of September; the budget assumes they are gone, because the expiry
date is not readable through the API and must be checked under Billing → Credits.

The load this carries today: 11,814 requests to the load balancer in 14 days,
API CPU under 2%, database CPU under 5%, and — from the API's own log — 28
interview sessions between 15 and 17 September, all of them tests or the Main
Admin's rehearsals, one of which ran to the 8-minute cap.

## 4. The load

| | The plan | The ceiling the question started from |
|---|---:|---:|
| Students | 1,000 | 1,000 |
| Interviews per student | 7 a month | 2 a day |
| Interviews a month | 7,000 | 44,000 on working days; 60,000 every day |
| Length of an interview | 8 minutes (Bedrock closes the stream at 8:00; the engine forces the wrap-up 90 s before) | same |
| Interviews per working day | 318 | 2,000 |
| Running at once, average over 12 hours | 3–4 | 22 |
| Running at once, busy evening (60% of the day inside 3 hours) | 8–9, bursts of 15–20 | 60–80 |

The product already caps a student at 8 completed interviews and 20 attempts
in a rolling 24 hours (`interview_max_per_student_per_day`,
`interview_max_attempts_per_student_per_day`), and a college can set its own
`daily_cap` per course on the Interview records screen. Seven a month is not a
daily figure, so it is enforced by the monthly count on the Interview records
screen rather than by that cap; until a monthly cap exists in the product, a
`daily_cap` of 1 keeps the ceiling at 22 a month per student. §6 prices every
level between one a week and two a day.

## 5. What one interview costs

Unit prices are the ones billed to this account on 16 September (Tokyo prices
for Nova 2 Sonic: $3.63 per million speech-input tokens, $14.52 per million
speech-output tokens, $0.396 / $3.311 per million text tokens — 21% above the
US list of $3 / $12). AWS tokenises audio at **25 tokens per second**, bills
input speech only while the student is actually speaking, and counts the prompt
once per session — all three read off this account's own sessions (§7). The
central case is a real interview: the student speaks for about 50% of the
8 minutes (240 s) and the interviewer's generated speech is billed for 30%
(144 s; Nova generates ahead of playback, so a student who interrupts is billed
for a little audio never heard).

| Item | Basis | USD | INR |
|---|---|---:|---:|
| Nova speech output | 144 s × 25 = 3,600 tokens × $14.52/M | 0.0523 | 5.02 |
| Nova speech input | 240 s × 25 = 6,000 tokens × $3.63/M | 0.0218 | 2.09 |
| Nova text input (the prompt, transcripts, control notes) | 4,000 tokens × $0.396/M | 0.0016 | 0.15 |
| Nova text output (speech transcripts, the scorecard) | 1,600 tokens × $3.311/M | 0.0053 | 0.51 |
| Mic audio through CloudFront to the API | 23 MB × $0.16/GB (India, viewer to origin) | 0.0037 | 0.35 |
| Audio to and from Tokyo through the NAT gateway | 30 MB × $0.056/GB + 20.5 MB × $0.086/GB inter-region | 0.0034 | 0.33 |
| Load balancer capacity units, CloudWatch logs | | 0.0006 | 0.06 |
| **Per interview, central case** | | **0.0886** | **8.5** |

| Case | What it assumes | Per interview |
|---|---|---:|
| Measured | The 17 Sep session that ran to the cap: tester spoke 60 s, interviewer 130 s, 2,979 / 1,025 text tokens | $0.057 · ₹5.5 |
| Low | Short answers: student 35%, interviewer 25% | $0.072 · ₹6.9 |
| **Central** | **Student 50%, interviewer 30%** | **$0.089 · ₹8.5** |
| High | Both sides talkative: student 60%, interviewer 40% | $0.112 · ₹10.8 |

The interviewer's audio back to the browser stays inside CloudFront's free 1 TB
a month even at 60,000 interviews, so it is not a line.

## 6. The budget

Output of `python tools/cost/budget_1000_students.py`, central case, before
GST, per month.

| Line | **The plan: 7 a month** | Ceiling: 2 a day, working days | Ceiling: 2 a day, every day |
|---|---:|---:|---:|
| Interviews a month | **7,000** | 44,000 | 60,000 |
| Today's platform (measured) | $180 | $180 | $180 |
| Database step-up to `db.t4g.small` Multi-AZ | $31 | $31 | $31 |
| API scale-out in interview hours | $2 | $12 | $16 |
| Database storage growth | $5 | $5 | $5 |
| **Nova 2 Sonic interviews** | **$567** | $3,561 | $4,856 |
| Network and logs for interviews | $54 | $338 | $461 |
| REEP Agent (Nova Pro, 10 questions per student a month) | $39 | $39 | $39 |
| Mail | $2 | $2 | $2 |
| **Total before GST** | **$879** | $4,167 | $5,589 |
| GST 18% | $158 | $750 | $1,006 |
| **Per month, INR** | **₹1.00 lakh** | ₹4.72 lakh | ₹6.33 lakh |
| **Per year, INR** | **₹12 lakh** | ₹57 lakh | ₹76 lakh |
| Per student, per month | **₹100** | ₹472 | ₹633 |

Where the plan's money goes: Nova 65%, the platform 25%, network 6%, the REEP
Agent and mail 5%.

The bill scales with the interview count, not the student count:

| Interviews a month | What it corresponds to | Before GST | Per month incl. GST | Per year, USD |
|---:|---|---:|---:|---:|
| 2,000 | a pilot: 100 students, 1 a day | $434 | ₹0.49 lakh | $5.2k |
| 4,300 | 1,000 students, 1 a week | $639 | ₹0.72 lakh | $7.7k |
| **7,000** | **the plan: 1,000 students, 7 a month** | **$879** | **₹1.00 lakh** | **$10.5k** |
| 8,600 | 1,000 students, 2 a week | $1,021 | ₹1.16 lakh | $12.2k |
| 22,000 | 1,000 students, 1 a day on working days | $2,212 | ₹2.51 lakh | $26.5k |
| 44,000 | 1,000 students, 2 a day on working days | $4,167 | ₹4.72 lakh | $50.0k |
| 60,000 | 1,000 students, 2 a day, every day | $5,589 | ₹6.33 lakh | $67.1k |

Read the other way, what a monthly budget buys (central case, after the
$256/month of fixed platform, agent and mail, and after GST):

| Budget a month | Before GST | Interviews a month | Per student |
|---:|---:|---:|---|
| ₹50,000 | $441 | 2,100 | 2 a month |
| ₹70,000 | $618 | 4,100 | 4 a month |
| **₹1 lakh** | **$883** | **7,100** | **7 a month — the plan** |
| ₹2 lakh | $1,766 | 17,000 | 17 a month |
| ₹5 lakh | $4,414 | 46,900 | 2 a day on working days |

Variants of the plan, before GST:

| Variant | Total | Change |
|---|---:|---:|
| Low case (short answers) | $759 | −$120 |
| High case (both sides talkative) | $1,044 | +$165 |
| **Voice recording switched on** (see §8) | $1,342 | +$463, and growing by about $8 every month |
| Nova billed at US-region prices (run the model in N. Virginia) | $787 | −$92, at ~120 ms more latency |
| 6-minute interviews instead of 8 | $733 | −$146 |

## 7. How this reconciles with the real bill

**The unit prices are the account's own.** Every price in the model's first
block is the `cost ÷ quantity` of a usage line on the 16 September bill:
`APS3-NatGateway-Hours` $0.056, `APS3-Multi-AZUsage:db.t4g.micro` $0.042,
`APS3-Fargate-ARM-vCPU-Hours` $0.02383, `APS3-LoadBalancerUsage` $0.0239,
`APN1-NovaSonic2.0-speech-output-tokens` $0.01452 per thousand, and so on.
Items the deployment does not buy yet (the bigger database, EFS at scale, backup
storage for recordings) come from the AWS price list for Mumbai and Singapore and
are marked "list" in the script.

**The fixed platform reproduces the bill.** The model's $5.91/day against the
real $6.20 clean day; the difference is the day's interview testing and the
Cost Explorer calls made for this document.

**A full-length session has been billed, and the model is built on it.** The
API log (`/reep/api`) records every interview's close with its turn count and
the bytes of microphone audio it received. Between 15 and 17 September it
holds 28 sessions — tests and the Main Admin's rehearsals, most abandoned in
the opening phase — and one, on 17 September at 08:40 UTC, that ran to the
8-minute cap. CloudWatch's one-minute Bedrock metrics show that session's bill
by itself: **1,509 input speech tokens (60 s of speech), 3,252 output speech
tokens (130 s), 2,979 text-input and 1,025 text-output tokens — $0.057, ₹5.5.**
Three facts follow. Silence is not billed: the session streamed 343 s of
microphone audio and paid for 60 s of speech. The prompt is counted once per
session, not once per turn, so text is ₹0.7 of an interview and not more. And
the interviewer took 27% of the session, which is where the central case's 30%
comes from; the tester's one minute of talking is what a real student's four
minutes replaces.

**The token rate reproduces September as a whole.** At 25 tokens a second, the
24,999 speech-input and 59,860 speech-output tokens billed from 1 to 17
September are 17 minutes of student speech and 40 minutes of interviewer speech
across the test sessions — consistent with the per-day audio the log shows
(14 minutes of microphone stream on 16 September against 4 minutes of billed
input speech). The earlier note in `docs/cost-review-2026-09.md` ("$0.06 per
interview") was the cost of a test session in which the tester barely spoke;
that figure is real, and it is the floor, not the expectation, for a student who
actually answers.

**What changed on recheck.** The first version of this document priced the
"two a day" ceiling as if it were the plan, put the interviewer at 50% of the
session and derived text tokens from a ratio measured on the short test
sessions; the second counted the prompt once per turn. The measured full-length
session corrected the interview to ₹8.5 (range ₹6.9–10.8, from ₹12.7), and the
board's volume of 7 a month per student replaced the ceiling as the headline:
₹1.0 lakh a month instead of ₹9.3 lakh. The unit prices, the quota finding and
the recording arithmetic did not change. One label was also corrected: one
interview a day on working days is 22,000 a month, not 20,000.

**What is still worth measuring: a student who talks.** The remaining
assumption is how long a real student speaks. Run 20–30 students through full
interviews on one day; the next day Cost Explorer shows that day's four
`APN1-NovaSonic2.0-*` lines; divide by the count. Put the measured split into
`CASES["central"]` in the script and re-run. If the measured cost lands between
₹6.9 and ₹10.8 an interview, this document stands as written; at 7,000 a month
the whole range is ₹0.86–1.18 lakh.

## 8. What gates the plan

1. **The Nova 2 Sonic concurrency quota — the real blocker.** Service Quotas
   shows this account's applied value for "On-demand InvokeModel concurrent
   requests for Amazon Nova 2 Sonic" as **2** in Tokyo, N. Virginia and Oregon,
   against an AWS default of **20**; Stockholm shows 20, and the older Nova
   Sonic (v1) shows 20 in Tokyo. No increase request is on file. The plan's
   318 interviews a working day are 3–4 at once on average and 15–20 in a busy
   evening burst; two streams would need 21 hours of back-to-back use to serve
   a day. The quota is marked "not adjustable" in the console but AWS's
   documentation says a request can still be made through the limit-increase
   form, that on-demand model quotas go through the account manager, and that
   **priority is given to accounts already consuming their allocation**. So:
   file the request now for 20 (the default), run the pilot at full
   utilisation of whatever is granted, and ask again with the usage graph if
   the volume grows. Interim capacity if the request stalls:
   `NOVA_SONIC_MODEL=amazon.nova-sonic-v1:0` (quota 20 in Tokyo today, same
   API, same price class) or `NOVA_SONIC_REGION=eu-north-1` (quota 20, roughly
   150 ms more round trip).
2. **The credits.** 68% of September's usage was paid by promotional credits
   whose balance and expiry the API does not expose. Read them in the console
   before the meeting; the day they run out the bill lands at gross.
3. **Recording stays off.** The operator switch is on
   (`INTERVIEW_RECORDING_ENABLED=true` in the task definition) and no college
   policy row has "Allow voice recording" ticked, so nothing is recorded today.
   Ticking it at 7,000 interviews a month writes 320 GB of WAV a month: 180
   days on EFS, backed up daily to Mumbai and copied to Singapore, and archived
   to an Object-Locked S3 bucket that never deletes. That is the recording
   variant in §6: **+₹52,000 a month in year one**, and about ₹900 a month
   more every month after. It is a policy decision with a price, and the price
   should be in front of whoever makes it.
4. **A budget alarm that can see gross spend.** The only budget on the account
   is AWS's default $1 zero-spend budget, which tracks net-of-credits and could
   not see the $361-a-month collection. `docs/cost-review-2026-09.md` §2 has the
   one command that creates a gross budget with a forecast alert; set it at
   about $1,000 a month for the plan.
5. **A cap in policy.** The product caps interviews per day, not per month. A
   `daily_cap` of 1 on the college's interview policy bounds a student at 22 a
   month on working days; a true monthly cap of 7 is a small product change
   (`interview_policies`) if the board wants the number enforced exactly. The
   attempt cap (20 a day) still counts every session that opened a Bedrock
   stream, which is what bounds spend from a student who keeps reconnecting.
6. **A load test is optional at this volume.** Two always-on API tasks carry
   3–4 simultaneous interviews comfortably; the 60-concurrent test only matters
   if the board later moves toward the ceiling.

## 9. Levers the board can pull

| Lever | Effect on the plan (₹1.0 lakh/month) | Cost of pulling it |
|---|---:|---|
| Four interviews a month instead of seven | ₹0.7 lakh/month | Less practice per student |
| One interview a week (4.3 a month) | ₹0.72 lakh/month | Same, on a weekly rhythm |
| 6-minute interviews | −17% | A shorter arc; the wrap-up still needs 90 s |
| Run Nova at US-region prices | −11% | ~120 ms more latency on every turn; the quota is 2 there too |
| Swap the NAT gateway for a NAT instance (`docs/cost-review-2026-09.md` §3b) | −4% (≈ ₹4,400/month) | An EC2 box somebody patches; at 3–4 concurrent streams a `t4g.nano` is enough |
| Single-AZ database | −2% (≈ ₹1,700/month) | Failover becomes a 20–30 minute restore; not recommended |

## 10. Assumptions and sources

Assumptions, all named in `tools/cost/budget_1000_students.py`: 7 interviews
per student per month on 22 working days; 8-minute sessions; a central case of
student 50% / interviewer 30% with 4,000 / 1,600 text tokens a session, a low
case (35% / 25%) and a high case (60% / 40%); 25 audio tokens a second; extra
API task-hours in proportion to interviews (0.018 task-hours each); 0.5 MB of
logs per interview; 10 REEP Agent questions per student a month; recordings of
46 MB per interview (two 24 kHz 16-bit tracks padded to 480 s), 180-day
retention, EFS Infrequent Access after 30 days; ₹96 per dollar; 18% GST.

Sources read on 2026-09-18: AWS Cost Explorer (`GetCostAndUsage`, RECORD_TYPE
Usage/Credit/Tax, by service and by usage type); CloudWatch `AWS/Bedrock`
metrics for `amazon.nova-2-sonic-v1:0` in `ap-northeast-1` at one-minute
resolution; the API log group `/reep/api` (CloudWatch Logs Insights, the
"Interview ended" lines of 15–17 September); Service Quotas
(`GetServiceQuota`, `GetAWSDefaultServiceQuota`,
`ListRequestedServiceQuotaChangeHistory`) in five regions; ECS, RDS, EC2, EFS,
S3, CloudFront, WAF, Budgets and Backup describe calls; the AWS Price List API
for Mumbai and Singapore; the
[Amazon Nova quotas page](https://docs.aws.amazon.com/nova/latest/nova2-userguide/quotas.html);
the [Nova 2 Sonic model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-amazon-nova-2-sonic.html)
(regions, no reserved tier); the [Nova 2 Sonic output-events page](https://docs.aws.amazon.com/nova/latest/nova2-userguide/sonic-output-events.html)
(usage events, faster-than-real-time generation); the 25-tokens-per-second rate
from the AWS Builders community's
[published Nova Sonic cost arithmetic](https://dev.to/aws-builders/bi-directional-voice-controlled-recipe-assistant-with-nova-sonic-v2-4p59);
US list prices for Nova 2 Sonic from [llm-stats](https://llm-stats.com/models/nova-2-sonic);
the exchange rate from [open.er-api.com](https://open.er-api.com/v6/latest/USD)
(95.96 on 18 Sep 2026) and [exchangerates.org.uk](https://www.exchangerates.org.uk/USD-INR-spot-exchange-rates-history-2026.html);
and `docs/cost-review-2026-09.md` for the September clean-up.
