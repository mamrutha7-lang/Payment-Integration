# infra/cdk — the voice-assistant platform's AWS resources (AWS CDK, Python)

One stack, `reep-voice-platform`, for everything the platform
(`apps/api-py/app/voice_platform/`) projects into AWS:

| Piece | Resource |
|---|---|
| Bulk candidate uploads (`uploads/`) | S3 bucket → Lambda `reep-voice-candidate-ingest` |
| Candidate streams | SQS `reep-voice-candidates-ug` / `-pg`, each with a DLQ |
| Call recordings (dual-channel WAV/MP3) | S3 bucket, expiring on `recordingRetentionDays` |
| Realtime session state | DynamoDB `reep-voice-sessions-ug` / `-pg` (TTL `expires_at`) |
| Session logs + question vectors | OpenSearch Serverless `reep-voice` (VECTORSEARCH) |
| What the api may do | an IAM policy attached to the **existing** task role `reep-api-task` |
| The api's environment | SSM parameters `/reep/voice-platform/PLATFORM_*`, read by the api at boot |
| Later deploys from CI | a policy on the **existing** OIDC role `reep-github-deploy` (assume the CDK bootstrap roles) |

The rest of REEP (VPC, ALB, ECS, RDS, the task role itself) is **`reep-core`**
(`reep_core/stack.py`), with the CloudFront WAF in `reep-edge-waf` (us-east-1)
and the cross-region backup copy target in `reep-dr-vault` (ap-southeast-1).
Those three are the successor to the Terraform stack in `infra/aws/`; the
cutover — adopting the live resources into CloudFormation without touching
them — is `docs/cdk-cutover.md`, and until it has been run, Terraform still
owns them and `infra/aws/` must not be deleted. This voice-platform stack is
additive and imports the two roles it touches by name. **Terraform is not part
of the platform's deploy path.** The api reads the `PLATFORM_*` parameters this stack publishes at boot
(`app/voice_platform/ssm_config.py`, production only, environment wins), so no
task-definition change is ever needed.

## Deploy

First time, with admin credentials (creates the CDK bootstrap roles, then the
stack — including the grant that lets the GitHub deploy role run later deploys):

```
cd infra/cdk
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
npm install -g aws-cdk
.venv/bin/python -m pytest                          # synth + template assertions, no AWS
cdk bootstrap aws://<account>/ap-south-1
cdk --app ".venv/bin/python app.py" deploy
```

Every time after that: **Actions → "CDK deploy (voice platform)" → Run
workflow** with `action = diff` to preview and `action = deploy` (confirm word
`deploy`). Then run the ordinary Deploy workflow so the api restarts and reads
the parameters; `GET /api/platform/admin/status` shows `config_source`
`ssm:/reep/voice-platform` and each piece switched on.

Context values (`cdk.json`, or `-c key=value`): `project` (default `reep`),
`apiTaskRoleName` (default `<project>-api-task`), `recordingRetentionDays`
(default 180 — mirror the per-degree recording policy's `retention_days`).

## Why CDK here and Terraform there

The core stack was built in Terraform and is live; rewriting it would be a
migration of running infrastructure for no functional gain. The platform is
new, self-contained and additive, so it is written in CDK as asked, with the
SSM parameters as the one-directional seam between the two.
