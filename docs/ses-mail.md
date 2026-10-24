# Outbound mail: Amazon SES

Activation links, password resets, the onboarding code, the change-password
code, registration decisions and (since 2026-09-15) leave notifications all
leave through Amazon SES. `app/mail_transport.py` is the transport and the only
place that knows it; `app/mailer.py::deliver_once` is the send-exactly-once
wrapper above it, and `mail_logs` is the record.

This document is two things: the decision record for **which domain REEP sends
as**, and the runbook for **adopting the SES resources into CloudFormation**.
Both exist because the mail path was built in a console and lived outside every
repository for six days.

---

## Where it stands

Verified against the live account (`445363794125`, `ap-south-1`) on
2026-09-15, not read off a board:

| | |
|---|---|
| Sending identity | `sast-skills.com` — verified, DKIM signing on, three SES-managed tokens |
| Account status | **Production access granted** (case `178892941200817`). 50 000/day, 14/s |
| From address | `no-reply@sast-skills.com` |
| Configuration set | `reep-transactional`, attached as the identity's default *and* named on every send |
| Events | `reep-transactional` → SNS `reep-ses-notifications` on BOUNCE, COMPLAINT, DELIVERY, REJECT, SEND |
| Suppression | Account-level, on for BOUNCE and COMPLAINT |
| Permission | `reep-api-task` inline `send-mail`, scoped to the identity ARN with a `ses:FromAddress` condition |
| Task | `reep-api:5`, registered 13:54 UTC 2026-09-15, carries `SES_FROM_ADDRESS` |

**The transport reached the running api at 13:55 UTC on 2026-09-15 and not
before.** The log group `/reep/api` shows `outbound mail: NO TRANSPORT
(SES_FROM_ADDRESS blank)` at 12:03, 12:04 and twice at 13:34 that day, then
`outbound mail: Amazon SES from no-reply@sast-skills.com` at 13:55:30 and
13:55:48. Task definition revisions 1, 3 and 4 carry no `SES_FROM_ADDRESS` at
all. So the two messages SES has ever sent — 2026-09-10, both delivered, no
bounces — did **not** come from the application. Nothing has yet travelled the
real path, which is why the first item under "What is still open" is a
smoke test and not a configuration change.

---

## The domain: REEP sends as `sast-skills.com`

Every recipient is `@bgscet.ac.in`; the mail is not. That is a decision, taken
2026-09-15, and not an unfinished migration.

There was a `bgscet.ac.in` domain identity in the account, left in
**`VerificationStatus: FAILED`** — created, never given its DNS records,
never used. It has been **deleted**, because a failed identity sitting beside a
working one reads as a job somebody abandoned halfway, and the next person to
look would have to re-derive which of the two is live before they could touch
anything. The verified `bdarshan5@bgscet.ac.in` *address* identity is a
different object and was left alone.

**What sending as `sast-skills.com` costs, stated plainly**, so nobody has to
rediscover it:

* students see mail from a domain that is not their college's, which is the
  ordinary shape of a phishing lesson. The copy has to carry the weight the
  domain does not;
* delivery into `bgscet.ac.in` mailboxes is subject to whatever that domain's
  filters make of an unrelated sender. DKIM alignment is intact — SES signs as
  `sast-skills.com` and the From domain matches — so this is a reputation
  question, not an authentication one.

**What moving would take**, if the college later decides it wants its own
domain on the envelope:

1. college IT publishes three DKIM CNAMEs for `bgscet.ac.in` (SES generates
   them when the identity is created; they are per-identity and cannot be
   reused from `sast-skills.com`);
2. wait for `VerificationStatus: SUCCESS`;
3. change `sesIdentityDomain` and `sesFromAddress` in `infra/cdk/cdk.json` and
   deploy. The IAM condition pins the From address, so this is a stack deploy
   and not a console edit — the grant and the sender move together or the task
   loses permission to send at all.

Steps 1 and 2 are the whole cost and neither is in this team's hands, which is
why the deployment is not waiting on them.

---

## Adopting the SES resources: `sesManaged`

Until 2026-09-15 the identity, the configuration set, its event destination, the
notifications topic and both reputation alarms existed **only in the console**.
None appeared in any template, any `.tf` file or any other file in this
repository: the `send-mail` policy and `SES_FROM_ADDRESS` were the managed half,
so `cdk deploy` could rebuild the *permission* to send and nothing that makes
sending work. A rebuild in a new region or a new account produced an api that
was allowed to mail and could not.

They are declared in `infra/cdk/reep_core/stack.py` now, behind
`-c sesManaged=true`, **which defaults OFF and must stay off until the adoption
below has been run.** CloudFormation cannot CREATE an SES identity that is
already verified: it fails `AlreadyExists` and rolls the stack back, and a
rollback of `reep-core` is a rollback of the whole api.

**Recreating the identity is not an alternative to importing it.** SES-managed
DKIM mints new tokens on create, so a delete-and-recreate publishes three CNAMEs
nobody has added yet and stops mail until DNS catches up — on a domain whose DNS
this team does not hold.

### The run

Human-attended, with credentials in hand, for the same reason `cdk import` was
in step 8 of `docs/cdk-cutover.md` and not in a workflow. `cdk-deploy.yml`
offers `diff` and `deploy` and cannot express an import.

```bash
cd infra/cdk

# 0. What the registry says the identifiers are. Read-only, and the
#    authoritative cross-check: a wrong key fails when the IMPORT change set
#    is created, before anything changes, but a runbook that fails at step 2
#    is not a runbook.
cdk synth reep-core -c sesManaged=true -c sesNotificationsEmail= --quiet
aws cloudformation get-template-summary \
    --template-body file://cdk.out/reep-core.template.json \
    --query ResourceIdentifierSummaries

# 1. Adopt the five. NOTE `-c sesNotificationsEmail=` — an
#    AWS::SNS::Subscription cannot be imported, and `cdk import` refuses a
#    change set that also CREATES something. The flag is written here rather
#    than left to memory, which is the lesson core-9a/9b already paid for.
cdk import reep-core -c sesManaged=true -c sesNotificationsEmail= \
    --resource-mapping ses-import-map.json

# 2. The deploy that follows adopts the subscription and sends the properties.
#    SNS dedupes on (topic, endpoint), so the existing confirmed subscription
#    is reused rather than doubled; a confirmation mail may arrive once more.
cdk deploy reep-core -c sesManaged=true
```

Then set `"sesManaged": true` in `cdk.json` in the same commit that records the
run, so the next `cdk deploy` from the workflow carries them.

`infra/cdk/ses-import-map.json` holds the six logical ids and their identifiers.
`tests/test_core_synth.py::test_the_ses_import_map_names_every_resource_the_adoption_must_carry`
compares that file against the rendered template, so the two cannot drift apart
quietly. One identifier is worth knowing about: the event destination's key is
**composite** (`Id,ConfigurationSetName`), and `Id` is a provider-assigned
read-only property whose value is the destination's name,
`sns-bounces-complaints`. If step 1 refuses it, the refusal names the value it
wanted and nothing has changed yet.

### What the mirror deliberately does not fix

`cdk import` does not compare properties — it adopts whatever the template says
— so the **first deploy after the adoption** is what sends them. Two values look
wrong and are mirrored rather than corrected, because changing either is a
decision with its own consequence and does not belong in the commit that is
supposed to change nothing:

* **`TlsPolicy: OPTIONAL`** on the configuration set. `REQUIRE` refuses delivery
  to a receiver with no STARTTLS rather than falling back to plaintext.
* **`ReputationMetricsEnabled: false`**. Turning it on publishes the per-set
  reputation metrics, which is what would move `reep-ses-bounce-rate` and
  `reep-ses-complaint-rate` off `INSUFFICIENT_DATA`.

Two more, for the same reason:

* the reputation alarms notify `reep-ses-notifications` rather than the ops
  topic `reep-alerts`, so an alarm arrives among per-message event JSON;
* there is **no custom MAIL FROM domain**, so the envelope sender is
  `amazonses.com` and SPF is unaligned. DMARC passes on DKIM alignment alone
  today.

---

## What is still open

1. **Nothing has travelled the real path.** Trigger `POST /api/auth/forgot` for
   a real address, add a faculty account (the create response's own `emailed`
   flag is the answer), approve a registration; then check that `AWS/SES`
   `Send` and `Delivery` increment.
2. **Bounces never reach the application.** The SNS topic goes to one inbox.
   `MailStatus.SUPPRESSED` exists and nothing sets it from SES, and
   `deliver_once` writes SENT the moment `send_email` returns — which means
   "SES accepted it", not "it arrived". Once a bounce puts an address on the
   account suppression list, every later send succeeds and delivers nothing
   while `mail_logs` reads SENT. That is the shape of the failure that killed
   `PENDING_VERIFICATION`, one layer down.
3. **No operator view and no retry for FAILED rows.** `deliver_once` records
   FAILED and never retries; there is no screen over `mail_logs` and no alarm on
   it. A student whose link failed is invisible until somebody runs SQL.
4. **`GET /api/admin/platform/status`** — B3.7's last unbuilt endpoint, meant to
   report `mail: ses|console` on the console.
   `features/admin/faculty-new/faculty-new.component.ts` and
   `features/admin/colleges/colleges.component.ts` both say so in comments and
   fall back to the per-response `emailed` flag.
5. `SES_REGION` is unset and `AWS_REGION` is not in the task environment, so
   `_region()` lands on the hardcoded `ap-south-1` default. Correct today;
   silently wrong the day the stack moves region.
