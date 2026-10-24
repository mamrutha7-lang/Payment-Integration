# Moving REEP's core stack from Terraform to CDK — the cutover runbook

Every resource in `infra/aws/` is live and holds real student data. This
procedure moves ownership of those resources from Terraform's state file to a
CloudFormation stack **without creating, replacing or deleting any of them**.
It is written to be run by one person, in order, with a rollback at every step.
It was reviewed adversarially before being written down in this form; the
things the review found are folded in and marked *(review)* where the reason
is not obvious.

**The two rules that make this safe. Read them twice.**

1. **Never delete a `.tf` file before Terraform has released its state.** A
   `terraform apply` against files that are gone and a state that still lists
   the database is a DESTROY of the database. Step 6 releases the state; the
   files go in step 7, after it.
2. **Never release Terraform's state before `cdk import` has succeeded and
   drift detection is clean.** Until then Terraform is the only thing that
   knows these resources exist.

**And one rule about mirrors:** the CDK template is reconciled *to reality*,
never the reverse. CloudFormation import does not compare properties, so a
wrong value imports fine and is then never corrected — the harden deploy only
sends properties whose *template* value changed. A mirror error at step 5 is
fixed in `stack.py`; it is never "fixed" by deploying.

The order is therefore: **import into CloudFormation → prove drift is clean →
release from Terraform → delete the files → harden (in two deploys).** Not one
step is skipped and not one is reordered.

## What you are moving

| Terraform (78 resources) | CDK stack | Region |
|---|---|---|
| everything in `infra/aws/` except the WAF | `reep-core` (`infra/cdk/reep_core/stack.py`) | ap-south-1 |
| `aws_wafv2_web_acl.edge` | `reep-edge-waf` (`edge.py`) | us-east-1 |
| *(new)* cross-region backup copy target | `reep-dr-vault` (`dr.py`) | ap-southeast-1 |

**82 managed addresses, 65 imported, and every one of the other 17 is named.**
The accounting is not prose any more — `UNMAPPED_ON_PURPOSE` in
`tools/import_map.py` lists every Terraform type that has no resource of its
own in the mirror, with its reason, and the tool **refuses** on any managed
address that is neither mapped nor listed. Three template resources have no
Terraform address of their own in the other direction: the two default routes
(inline on `aws_route_table`) and the gateway attachment (a `vpc_id` argument
on `aws_internet_gateway`).

That check exists because it was briefly removed. The preflight's original
"78 addresses" heuristic was replaced with a bare non-zero count on the
grounds that `import_map.py` covered it — it did not, it only ever walked the
template, so for a few hours nothing checked the state→template direction at
all *(2026-09-07)*. After step 6 releases the state, a live resource that
neither side claims is one nobody will manage again.

### What is left unmanaged, on purpose

**The two Secrets Manager secrets are NOT imported and never will be.**
`reep/app-…` holds `AUTH_SECRET` and `DATABASE_URL`; `reep/external-…` holds
`GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `SENTRY_DSN` and
`VOICE_WORKER_SECRET`. The stack reads both **by ARN** and writes neither. A
CloudFormation resource for a secret is a resource CloudFormation can rewrite,
and rewriting `AUTH_SECRET` signs out every student while rewriting
`DATABASE_URL` takes the api off its database. Their ARNs are in
`cdk.context.json` as `appSecretArn` and `externalSecretArn`; the operator
fills them (`docs/aws-deployment.md` §3). From step 7 onward this paragraph is
the only record that they exist, which is why it is here rather than in a
comment in a deleted file.

Also deliberately not imported: `random_password.db` and
`.auth_secret` (Terraform-only generators — the values they produced live in
the secrets), the two `aws_secretsmanager_secret_version` resources, and the
SNS **email subscription**, which cannot be imported at all and which the
harden phase re-creates (SNS dedupes on topic + endpoint, so the existing one
is reused and a confirmation mail may arrive once more).

The `reep-core` stack has **two phases**, chosen by one context value:

- `-c phase=import` — a mirror of what exists and nothing else, carrying the
  *live* database values (single-AZ, 14-day retention, the current storage
  size) that `tools/import_map.py` reads from the state.
- `-c phase=harden` (the default) — the same stack plus every fix. It has a
  second switch, `hardenEcs`, so the database half and the ECS half deploy
  separately (step 9).

The synth tests (`infra/cdk/tests/test_core_synth.py`, no AWS) prove: the
import phase is a strict subset of harden; every physical name matches the
`.tf` files; the import template contains nothing that does not exist (no
generated ingress rules, no generated IAM policies); every type in it has a
registry identifier the tool knows; no `MasterUserPassword` can appear; every
resource in all three stacks is `Retain`. `tests/test_cutover_tools.py`
rehearses the two state-reading tools below against a synthetic state, in the
runbook's order, so neither runs for the first time with credentials in hand
*(rehearsal)*.

## Step 0 — Preconditions (one command, no risk)

Admin credentials for account 445363794125, on a machine with `terraform`
(>= 1.6), `aws`, `node` 20+ and Python 3.12. Installing them once:

```bash
# Windows, in Git Bash (the scripts are bash). Open a NEW shell afterwards: the
# installers change the user PATH, and a shell opened before them cannot see it.
winget install Hashicorp.Terraform
winget install Amazon.AWSCLI            # raises an elevation prompt — accept it, or winget reports "cancelled"
npm install -g aws-cdk
cd infra/cdk && py -3.12 -m venv .venv && .venv/Scripts/pip install -r requirements-dev.txt
# macOS / Linux: terraform and aws from their vendors' installers, then
npm install -g aws-cdk
cd infra/cdk && python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
```

**Every `cdk` command below runs with the venv active** (`cdk.json` is
`"app": "python app.py"`), and with the region pinned — a profile pointed at
another region imports nothing, confusingly:

```bash
export AWS_PROFILE=<admin>  CDK_DEFAULT_REGION=ap-south-1  CDK_DEFAULT_ACCOUNT=445363794125
cd infra/cdk && source .venv/Scripts/activate      # Windows;  source .venv/bin/activate elsewhere
```

Then the preflight — **read-only against AWS**, and safe to rerun until it
exits 0:

```bash
tools/cutover_preflight.sh
```

It checks, in order, everything this step used to list by hand: the four tools
and the venv; that `cleanup-orphans.sh` is gone and `tf-state.json` is
untracked; the synth guards and a full `cdk synth` (a CLI/library mismatch
fails here, not at import); that the credentials are for account
445363794125; that all three regions are bootstrapped — for each one it names,
run `cdk bootstrap aws://445363794125/<region>`; that `reep-core` and
`reep-edge-waf` do not exist yet. Then Terraform: it writes `backend.hcl` from
the account id if the file is missing, runs `terraform init`, exports the state
to `infra/cdk/tf-state.json` (**contains secret values** — gitignored, deleted
in step 6), **reconstructs `infra/aws/prod.tfvars` from that state** with
`tools/tfvars_from_state.py`, and runs
`terraform plan -var-file=prod.tfvars -detailed-exitcode`, which **must exit
0**: no drift, no pending change.

Why the tfvars are reconstructed rather than looked for: the first apply was
given its values as `-var` flags (`docs/aws-deployment.md` §3) and they were
recorded nowhere. Terraform's state does not store variable values, but every
variable lands in an attribute of a resource the state does hold, so the tool
reads each one back — the certificate from the 443 listener, the alias and
certificate from the distribution, the alert address from the SNS
subscription, the sizes from the task definition, the OIDC subjects from the
deploy role's trust policy — and refuses to write anything it cannot source.
Without them the plan proposes to "fix" the certificate, the domain and the
alert address back to their defaults. If `plan` still proposes anything,
resolve it first: importing a resource whose real state differs from its
Terraform state means the mirror in CDK is wrong too.

**Expect it not to be clean the first time, and resolve it in this order.** On
2026-09-07 the state was stale three ways, all because the account is edited by
things that are not Terraform — a console visit and CI:

1. **Reality changed, the config did not.** The distribution had been given an
   alias, an ACM certificate and a TLS policy in the console.
   `terraform apply -refresh-only -var-file=prod.tfvars` records reality into
   the state and **changes nothing in AWS** — it is the honest resolution, and
   it is what makes the reconstructed `prod.tfvars` come out right, because the
   tool reads the state. Re-run `tools/tfvars_from_state.py` after it (delete
   the file first; the tool never overwrites).
2. **A resource was replaced outside Terraform.** CI had registered ECS task
   definition revision 4 and the service runs it; the state held revision 3.
   `terraform state rm aws_ecs_task_definition.api` then
   `terraform import aws_ecs_task_definition.api <arn>:4`. An imported task
   definition then comes back with AWS's *populated* form — auto-named port
   mappings, empty default lists, the volume's `configure_at_launch` — which
   the source never spells out, so Terraform proposes to REPLACE it and roll
   the service to "fix" defaults. `ecs.tf` carries a `lifecycle` block ignoring
   exactly that noise; the values themselves are still the config's.
3. **A value the provider cannot express.** The live minimum TLS version is
   `TLSv1.3_2025` and **no aws provider 5.x accepts that string** — 5.100
   validates against a list ending at `TLSv1.2_2021`, so the plan *errors*
   rather than drifting. `cdn.tf` ignores
   `viewer_certificate[0].minimum_protocol_version` and carries a placeholder
   that is never sent. The CDK mirror renders the real value, because
   CloudFormation has no such limitation.

One change was genuinely applied, through a saved plan
(`terraform plan -out=…` then `terraform apply <file>`, so that what was
reviewed is what ran): the nightly retention schedule still targeted revision
3. That one matters to the import — the mirror renders the schedule's target as
a `Ref` to the task definition, so leaving it would have produced a drift row
the runbook does not list.

Only then does `terraform plan -detailed-exitcode` exit 0.

**Remove the one script in the tree that deletes production by name** *(review)*:
`infra/aws/cleanup-orphans.sh` targets cluster `reep`, service `api`, log group
`/reep/api` and the five IAM roles — the production names — and after step 6
*everything* looks like an orphan to Terraform, which is exactly when someone
would reach for it. It is deleted in this repository; if you are on an older
checkout, `git rm` it before continuing (the preflight fails while it exists).

The voice-platform stack (`infra/cdk/README.md`) is not needed for the import,
but step 10's browser check and every later CDK deploy from CI do need it: it
holds the grant that lets `reep-github-deploy` assume the CDK bootstrap roles.
The preflight warns if it is missing.

Take one manual snapshot, by hand, that nothing automated will age out — the
one action in this step the preflight will not do for you (it reports whether
one exists):

```bash
aws rds create-db-snapshot --db-instance-identifier reep-postgres \
  --db-snapshot-identifier "reep-postgres-pre-cdk-$(date -u +%Y%m%d)"
aws rds wait db-snapshot-available --db-snapshot-identifier "reep-postgres-pre-cdk-$(date -u +%Y%m%d)"
```

## Step 1 — The context from the state, then the mirror from the context

The preflight already exported the state; re-exporting is harmless. **The
tool runs before the synth**, because a context-free synth renders the
*default* shape — one plain-HTTP listener, a declared OIDC provider — and a
map built against that template has nothing to attach the live 443 listener's
ARN to. The first draft of this runbook synthesised first and would have been
refused at step 2 with credentials in hand *(rehearsal)*.

```bash
cd infra/aws && terraform show -json > ../cdk/tf-state.json     # CONTAINS SECRET VALUES. gitignored. Delete after step 6.
cd ../cdk
python tools/import_map.py tf-state.json                 # pass 1: cdk.context.json, from the state alone
cdk synth reep-core -c phase=import --quiet              # the mirror, now with the live shape
```

Pass 1 merges into `cdk.context.json` everything the stack must render
identically: the random-suffix names, the secret ARNs, the AZs, the prefix
list, the WAF ARN — **and the variable-driven values** *(review)*: the ALB
certificate and origin domain, the CloudFront alias and certificate,
cpu/memory, min/max tasks, the instance class, the live Multi-AZ / retention /
storage size, the alert address, and the container environment. Without those
the mirror has the wrong *shape* (one HTTP listener where there are two). It
writes `githubOidcProviderArn` **only when the provider is not in the state**
— written while it is, the re-synth would *reference* the provider and drop
the very resource the map lists *(rehearsal)*.

## Step 2 — Build the import map against that template, never by hand

```bash
python tools/import_map.py tf-state.json cdk.out/reep-core.template.json
```

It writes nothing unless every check passes, then writes `import-map.json`
(the registry identifier, every part of a composite one, for each resource in
the import template) and re-merges the context. It **skips `AWS::CDK::Metadata`**,
which the CLI appends to every synth and which is a base64 analytics string
rather than a resource: the synth tests build their template with
`Template.from_stack()`, which does not add it, so the rehearsal never saw it
and the tool refused the first real template *(2026-09-07)*.

What the rehearsal cannot check is the registry itself. Do that with the tool,
not by eye:

```bash
python tools/check_identifiers.py     # exit 0 = every identifier matches
```

It asks `get-template-summary` what keys each type requires **for this exact
template** and compares all of them. **Do not do this comparison by reading the
table**, which is what this step used to say. CloudFormation returns a
*composite* identifier as ONE comma-joined string —
`["ResourceId,ScalableDimension,ServiceNamespace"]`, one element, not three —
while the map correctly sends three separate keys. By eye, all seven composite
types read as mismatches: the ECS service, both scaling policies, the scalable
target, the metric filter, the IGW attachment, the EIP and both default routes.
Following the old instruction, an operator would have "fixed" a table that was
right and broken an import that was going to work. That happened here, to the
person who wrote the table. A real mismatch is a bug in `IDENTIFIERS`; fix the
table, never the map.

The template is larger than CloudFormation's 51,200-byte inline limit, so the
tool stages a copy in the CDK bootstrap assets bucket and summarises it by URL.
That staged copy is the only thing it writes.

Commit `cdk.context.json` and `import-map.json` — they are the audit record of
what was adopted, and neither is secret. **Never commit `tf-state.json`.**

## Step 3 — Rehearse on the WAF (one resource, one region)

```bash
CDK_DEFAULT_REGION=us-east-1 cdk import reep-edge-waf
# EdgeAcl's identifier is composite — Name | Id | Scope; the tool printed all three:
#   Name: reep-edge   Id: <from the state>   Scope: CLOUDFRONT
CDK_DEFAULT_REGION=us-east-1 cdk diff reep-edge-waf
```

**This step overwrites the import mirror. Re-synthesise before step 4.** Every
`cdk` command runs the app, and the app synthesises *all four stacks* in
whatever phase the context says — which for a bare `cdk import reep-edge-waf`
is `harden`, the default in `cdk.json`. So `cdk.out/reep-core.template.json`
comes back as a HARDEN template, and anything that reads it afterwards
(`tools/check_identifiers.py`, an eyeball, a diff) is reading the wrong one.
The import in step 4 passes `-c phase=import` and so re-synthesises correctly,
but do not trust the file on disk in between:

```bash
cdk synth reep-core -c phase=import --quiet      # restore the mirror
python tools/check_identifiers.py                # and re-prove it
```

The diff will show `[+]` for the `CDKMetadata` resource and the `WebAclArn`
output *and nothing else* — `cdk import` strips those two from what it
stores, so they are the expected residue *(review)*. Any property difference
means `edge.py` does not describe the ACL as it is: **fix `edge.py`**, re-run
`cdk import` after `delete-stack` (the ACL is `Retain`), and repeat. Do not
`cdk deploy` to make the diff go away — that changes the live WAF.

## Step 4 — Import the core stack

```bash
cdk import reep-core -c phase=import --resource-mapping import-map.json
```

CloudFormation validates every identifier before it adopts anything. If it
refuses a resource TYPE, remove that construct from the import phase in
`stack.py`, re-run from step 1, and record the resource here as *left
unmanaged*. Do not add it back in `harden` as a new resource unless creating
it a second time is harmless — for an internet-gateway attachment it is not.

## Step 5 — Prove the mirror is exact. This step is the whole point.

Two checks, and only the second one tells the truth *(review)*.

```bash
cdk diff reep-core -c phase=import
```

Expect `[+]` for `CDKMetadata` and the nine outputs, and **no property
changes on any resource**. A property change here is a value the next deploy
would send; for a create-only property (a security-group name, a subnet CIDR,
a task family) that is a REPLACEMENT.

Then drift detection, which compares the template to *reality*:

```bash
DRIFT=$(aws cloudformation detect-stack-drift --stack-name reep-core --query StackDriftDetectionId --output text)
until [ "$(aws cloudformation describe-stack-drift-detection-status --stack-drift-detection-id "$DRIFT" --query DetectionStatus --output text)" != DETECTION_IN_PROGRESS ]; do sleep 5; done
aws cloudformation describe-stack-resource-drifts --stack-name reep-core \
  --stack-resource-drift-status-filters MODIFIED DELETED \
  --query 'StackResourceDrifts[].{Id:LogicalResourceId,Status:StackResourceDriftStatus,Diffs:PropertyDifferences[].PropertyPath}' --output table
```

`NOT_CHECKED` rows are types CloudFormation cannot drift-check; ignore them.
`DELETED` must be empty. `MODIFIED` is acceptable **only** for these known,
harmless differences — anything else is a mirror error to fix in `stack.py`:

**This ran on 2026-09-07. The result is below, not a prediction.** 63 resources
were checkable: **60 `IN_SYNC`, 3 `MODIFIED`, 0 `DELETED`.**

| Resource | Property | Why it differs |
|---|---|---|
| `ApiTaskRole` | `Policies/1` | **`reep-voice-platform` owns the `voice-platform` inline policy on this role** |
| `GithubDeployRole` | `Policies/1` | **`reep-voice-platform` owns `deploy-cdk-stacks` on this role** |
| `ApiTaskDef` | `ContainerDefinitions/0/MountPoints/0/ReadOnly` | the mirror renders `false`; the live definition omits it, which means the same thing. CDK's `MountPoint` requires the field, AWS defaults it. Self-resolves at 9b, which registers a new revision |

Only when that table is the whole `MODIFIED` list is the mirror proven.

Four rows this table used to predict came back `IN_SYNC` and have been removed:
the ALB-logs and web bucket policies, the ECR lifecycle JSON whitespace, and
`Db.EngineVersion`. Do not re-add them from memory — if one appears, something
changed.

`DriftedStackResourceCount` reports **3**, so the stack's own status reads
`DRIFTED`. That is expected and is not a failure: two of the three are another
CloudFormation stack's property and the third is a default. What must be empty
is `DELETED`, and it is.

**Do not "fix" the last two.** Adding `voice-platform` or `deploy-cdk-stacks`
to `reep_core/stack.py` puts two CloudFormation stacks in charge of the same
inline policy name, and the next deploy of either overwrites the other —
removing them from the voice-platform stack then issues `DeleteRolePolicy` and
takes away the api's S3/SQS/DynamoDB/OpenSearch grants, or CI's ability to
assume the CDK bootstrap roles. They are safe as they are: CloudFormation
diffs `Role.Policies` template-to-template, never against the live inline list,
so a policy that was never in `reep-core`'s template is never touched by
`reep-core`'s deploys — the same mechanism that lets CDK's own
Role + DefaultPolicy pattern coexist. *(pre-import review, 2026-09-07)*

Four rows were found and fixed in the mirror rather than tolerated here, and
they are listed so a regression is recognisable: the ECS service's
`MinimumHealthyPercent` (rendered 50, live 100), the HTTP redirect's
`Host`/`Path`/`Query`, the web OAC's `Description`, and tags on the CloudFront
function (live has none — the aws provider cannot set them). If any of those
appears in a drift report, the mirror has regressed.
Terraform still manages everything; nothing has been changed yet. **This is
the last point at which nothing has happened.**

Rollback from here: `aws cloudformation delete-stack --stack-name reep-core`.
Every resource is `DeletionPolicy: Retain` — the database included; it is
*Retain*, not *Snapshot*, because Snapshot means delete-after-snapshot *(review)*
— so deleting the stack forgets the resources and touches none.

## Step 6 — Release Terraform's state

```bash
infra/cdk/tools/terraform_release.sh --i-have-run-cdk-import
```

The script checks that `reep-core` is `IMPORT_COMPLETE` before it does
anything, pulls a timestamped copy of the state (**keep it**), removes every
managed address in one `terraform state rm`, then runs `terraform plan` —
which must now propose to CREATE everything. That plan is the proof Terraform
no longer knows these resources. **Do not apply it.**

Rollback from here: `terraform state push terraform.tfstate.before-release.<stamp>`
restores Terraform's knowledge exactly, and `aws cloudformation delete-stack`
forgets the CDK side. Nothing on AWS has changed at either point.

Now delete the export: `rm infra/cdk/tf-state.json`.

## Step 7 — Delete the Terraform files, in a commit

```bash
git rm infra/aws/*.tf infra/aws/backend.hcl.example infra/aws/dev-setup-terraform.sh
# keep bootstrap-state.sh (the state bucket stays, holding the backup) and grant.sh (unrelated)
git commit -m "infra: Terraform released; reep-core is CloudFormation via CDK"
```

**Do NOT add `core` back to `.github/workflows/cdk-deploy.yml` in this
commit.** It was removed so a browser click could not create a second VPC
before the import *(review)*, and between here and step 9 it would be a worse
hazard, not a smaller one: the workflow runs a bare `cdk deploy reep-core`,
which is the FULL harden — both halves in one update, the Multi-AZ conversion
and the API roll together, which is precisely what step 9 splits apart. Add it
in a commit **after 9b**, when a one-click `reep-core` deploy is a no-op
against a stack that is already hardened *(pre-import review, 2026-09-07)*.

The synth guards that read `infra/aws/*.tf` (`requires_terraform` in
`tests/test_core_synth.py` and `tests/test_cutover_tools.py`) skip themselves
once the files are gone, so CI stays green on this commit; the state-reading
tools stay in the tree, harmless without a state to read.

From this commit on, `terraform apply` cannot destroy anything because there
is nothing for it to read.

## Step 8 — The DR vault (a new resource, created normally)

```bash
CDK_DEFAULT_REGION=ap-southeast-1 cdk deploy reep-dr-vault
# note the DrVaultArn output → cdk.context.json as drVaultArn
```

## Step 9 — Harden, in two deploys

Set the remaining context in `cdk.context.json`: `drVaultArn` from step 8,
`sesIdentityDomain=bgscet.ac.in` and `sesFromAddress` once the domain is
verified in SES, `alertEmail` if the tool did not find a subscription.

**9a — the database and backup half, at night IST** (the Multi-AZ conversion
builds a standby and dips performance while it syncs):

```bash
cdk diff reep-core -c hardenEcs=false
```

Expect: `Db` MultiAZ false→true and **BackupRetentionPeriod 1→35**; the vault's
`LockConfiguration`; the plan's `CopyActions`; the restore-testing plan and
selection; three backup alarms; `AWSBackupServiceRolePolicyForRestores` on the
backup role; the `send-mail` policy on the task role; the SNS email
subscription; tags on everything except the ECS trio. **No `Replace` on
anything.**

**One, not fourteen.** The live database keeps **one day** of automated
backups (`db_backup_retention_days` defaults to 14, but the instance was
created with 1 and the refresh recorded it). So point-in-time recovery covers
only the last 24 hours for the whole of this cutover, and the manual
`reep-postgres-pre-cdk-*` snapshot from step 0 is the real safety net until
this deploy lands. An earlier version of this line said 14→35, which would
have made a correct diff look wrong at the one gate where an operator confirms
a `ModifyDBInstance` against the live database *(2026-09-07)*.

**And 9a does not touch the API.** It used to: the harden phase flips every
resource's `ManagedBy` tag, and a task definition is immutable, so tagging it
registers a **new revision** the service then rolls onto — in the same update
as the Multi-AZ conversion, which is exactly what splitting the deploy was
supposed to prevent. The tag flip on the task definition, the service and the
target group is now held back with the rest of the ECS half, so 9a's diff
shows those three as unchanged. `test_the_database_half_does_not_touch_the_ecs_trio`
is the guard.

```bash
cdk deploy reep-core -c hardenEcs=false
```

**9b — the ECS half**, once 9a is `UPDATE_COMPLETE` and the instance shows
Multi-AZ:

```bash
cdk diff reep-core
```

Expect: a **new task-definition revision** (this *always* shows as a Replace
of `AWS::ECS::TaskDefinition` — the old revision is retained, and it is the
one Replace that is expected *(review)*), the target group's
`deregistration_delay` 30→600, and the new task definition. Nothing else.

`MinimumHealthyPercent` is **not** in that list and must not appear in any
diff. The live service is already at 100 — `ecs.tf` never sets it, so 100 is
the ECS default — and the mirror renders 100 in both phases *(2026-09-07)*.
It used to render 50 at import, which would have put an unlisted MODIFIED row
in step 5's drift report and, because step 9a updates this service anyway to
flip its tag, would have dropped the live service to a 50 % minimum for the
length of the Multi-AZ conversion. If you see it in a diff, the mirror has
regressed; `test_the_service_keeps_both_tasks_in_every_phase` is the guard.

```bash
cdk deploy reep-core
```

**This deploy rolls the service itself** *(review)*: CloudFormation registers
the revision, updates the service, and waits for the deployment — with
100/200 the two old tasks stay healthy until two new ones are, and then each
old task lingers **600 s** in `draining` before it stops. Expect the deploy to
take 12–15 minutes. Watch `/ready` and the `reep-no-healthy-api` alarm.

The two halves are separate so that an ECS circuit-breaker rollback in 9b
cannot also undo the Multi-AZ conversion from 9a in the same stack update.

## Step 10 — What to check afterwards

- `deploy.yml` no longer uses `aws ecs wait services-stable` *(review)*: with a
  600 s drain, every deploy would have hit that waiter's 10-minute cap and
  reported failure on a successful roll. It now polls `describe-services`
  until one deployment remains and `runningCount == desiredCount`.
- `aws iam list-role-policies --role-name reep-api-task` lists `invoke-nova`,
  `send-mail` and the voice platform's policy — the voice stack's by-name
  attachment survives core owning the role.
- The next 03:00 IST backup job completes, and a *copy* job appears in
  `reep-vault-dr` in ap-southeast-1.
- The first Sunday 09:30 IST restore test runs and reports success; the three
  `reep-backup-*-failed` alarms stay OK.
- `cdk diff reep-core` shows no differences.
- CI's `cdk` job (the synth guards) is green.
- Run the **CDK deploy** workflow with `stack = core`, `action = diff` once
  from the browser: no differences.

## What this does NOT change

- The GitHub Actions **Deploy** workflow's names (`reep`, `reep-api`, `api`).
- Any secret value. The stack references the two secrets by ARN and never
  writes a version.
- The database's master password. See the synth test that refuses the
  template if `MasterUserPassword` appears.
- `DesiredCount` on the service: never sent, autoscaling owns it *(review)*.
- The state bucket `reep-tfstate-<account>`. It stays, holding the last state
  and the release backup.

## If something goes wrong in the middle

| You are at | Undo |
|---|---|
| after step 4/5, before 6 | `aws cloudformation delete-stack --stack-name reep-core` — every resource is Retain; Terraform still owns them |
| after step 6, before 7 | `terraform state push <backup>` and delete the CDK stack as above |
| after step 7 | `git revert` the deletion commit, then the row above |
| after 9a | `git revert` and `cdk deploy -c hardenEcs=false` walks the properties back. The governance vault lock is removable by an administrator; only the opt-in compliance lock is not |
| after 9b | `git revert` and `cdk deploy` — one more 12-minute roll |
