"""The cutover tools rehearse WITHOUT an account.

tools/import_map.py and tools/tfvars_from_state.py read `terraform show
-json`. Until this file, the first time either ran was docs/cdk-cutover.md
step 0 or 2 — with admin credentials in hand — so a mapping rule naming a
logical id the stack had renamed, a template synthesised in the wrong order,
or a variable Terraform gained without a rule here would have surfaced at the
console, mid-cutover. Writing this file found two such faults: the runbook
synthesised the mirror BEFORE running the tool, so a TLS-fronted ALB (two
listeners) was mapped against a plain-HTTP template (one) and refused; and
with the OIDC provider in the state the tool wrote its ARN into the context,
which made the re-synth REFERENCE the provider and drop the resource the map
had just listed.

The synthetic state below describes the same account the import-phase
template describes when it is synthesised from the context the tool writes.
What is pinned:

- context from the state, the template from that context, the map from both,
  then the template again from the merged context: a fixed point, with every
  resource mapped under exactly the registry's key set;
- the tfvars tool covers every `variable` infra/aws/ declares and agrees with
  the import tool on every value both read;
- both refuse a state missing a resource and write nothing;
- the runbook names tools that exist, the shell scripts parse, and the
  preflight only ever reads from AWS.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from reep_core import CoreStack

CDK_DIR = Path(__file__).resolve().parents[1]
TF_DIR = CDK_DIR.parent / "aws"
TOOLS = CDK_DIR / "tools"
DOCS = CDK_DIR.parents[1] / "docs"
sys.path.insert(0, str(TOOLS))
import check_identifiers  # noqa: E402
import import_map  # noqa: E402
import tfvars_from_state  # noqa: E402

CDK_JSON_CONTEXT = json.loads((CDK_DIR / "cdk.json").read_text(encoding="utf-8"))["context"]
ACCOUNT = "123456789012"
REGION = "ap-south-1"
SUFFIX = "20260101000000000000"
OIDC_ARN = f"arn:aws:iam::{ACCOUNT}:oidc-provider/token.actions.githubusercontent.com"
ALB_DNS = "reep-alb-0aaa.ap-south-1.elb.amazonaws.com"

#: The account the synthetic state describes, as the context the import tool
#: must write for it. The template synthesised from THIS must match the state
#: built from the same numbers — that agreement is what makes the rehearsal a
#: rehearsal, and the exact-equality assertion is what pins the key names.
LIVE_CONTEXT: dict[str, Any] = {
    "webBucketName": f"reep-web-{SUFFIX}",
    "albLogsBucketName": f"reep-alb-logs-{SUFFIX}",
    "appSecretArn": f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:reep/app-{SUFFIX}-AbCdEf",
    "externalSecretArn": f"arn:aws:secretsmanager:{REGION}:{ACCOUNT}:secret:reep/external-{SUFFIX}-AbCdEf",
    "albSecurityGroupName": f"reep-alb-{SUFFIX}",
    "apiSecurityGroupName": f"reep-api-{SUFFIX}",
    "dbSecurityGroupName": f"reep-db-{SUFFIX}",
    "efsSecurityGroupName": f"reep-efs-{SUFFIX}",
    "availabilityZones": ["ap-south-1a", "ap-south-1b"],
    "cloudfrontPrefixListId": "pl-0123456789abcdef0",
    "restrictAlbToCloudfront": True,
    "wafWebAclArn": f"arn:aws:wafv2:us-east-1:{ACCOUNT}:global/webacl/reep-edge/11111111-2222-3333-4444-555555555555",
    "albAcmCertificateArn": f"arn:aws:acm:{REGION}:{ACCOUNT}:certificate/alb-cert",
    "domainName": "reep.bgscet.ac.in",
    "cloudfrontAcmCertificateArn": f"arn:aws:acm:us-east-1:{ACCOUNT}:certificate/cf-cert",
    "albOriginDomain": "origin.reep.bgscet.ac.in",
    "apiCpu": 512,
    "apiMemory": 1024,
    "apiMinTasks": 2,
    "apiMaxTasks": 10,
    "dbInstanceClass": "db.t4g.small",
    "liveDbMultiAz": False,
    "liveBackupRetentionDays": 14,
    "liveAllocatedStorage": 20,
    "alertEmail": "ops@bgscet.ac.in",
    "bedrockModel": "apac.amazon.nova-pro-v1:0",
    "novaSonicRegion": "ap-northeast-1",
    "interviewEngine": "nova",
    "interviewRecordingEnabled": "true",
    "interviewConsentVersion": "2026-09",
    "allowRemoteStudentData": "true",
}

#: Values both tools read from the same state, and must agree on.
SHARED_VALUES = {
    "albAcmCertificateArn": "alb_acm_certificate_arn",
    "domainName": "domain_name",
    "cloudfrontAcmCertificateArn": "cloudfront_acm_certificate_arn",
    "albOriginDomain": "alb_origin_domain",
    "restrictAlbToCloudfront": "restrict_alb_to_cloudfront",
    "apiCpu": "api_cpu",
    "apiMemory": "api_memory",
    "apiMinTasks": "api_min_tasks",
    "apiMaxTasks": "api_max_tasks",
    "dbInstanceClass": "db_instance_class",
    "liveDbMultiAz": "db_multi_az",
    "liveBackupRetentionDays": "db_backup_retention_days",
    "alertEmail": "alert_email",
    "bedrockModel": "bedrock_model",
    "novaSonicRegion": "nova_sonic_region",
    "interviewEngine": "interview_engine",
    "interviewRecordingEnabled": "interview_recording_enabled",
    "interviewConsentVersion": "interview_consent_version",
    "allowRemoteStudentData": "allow_remote_student_data",
}

requires_terraform = pytest.mark.skipif(
    not list(TF_DIR.glob("*.tf")),
    reason="Terraform released (docs/cdk-cutover.md step 7); the tools have done their work",
)


def _fake_state(*, with_oidc_provider: bool = True, plain_http: bool = False, drop: tuple[str, ...] = ()) -> dict[str, Any]:
    """`terraform show -json` for an account shaped like LIVE_CONTEXT.
    `plain_http` is the no-certificate ALB (one listener, CloudFront talking to
    the raw ALB name); `drop` removes `type.name` addresses, as a broken state
    would."""
    rs: list[dict[str, Any]] = []

    def r(tf_type: str, name: str, values: dict[str, Any], index: int | None = None) -> None:
        if f"{tf_type}.{name}" in drop:
            return
        entry: dict[str, Any] = {"mode": "managed", "type": tf_type, "name": name, "values": values}
        if index is not None:
            entry["index"] = index
        rs.append(entry)

    c = LIVE_CONTEXT
    r("aws_vpc", "main", {"id": "vpc-0aaa"})
    r("aws_internet_gateway", "main", {"id": "igw-0aaa"})
    r("aws_eip", "nat", {"id": "eipalloc-0aaa", "allocation_id": "eipalloc-0aaa", "public_ip": "13.0.0.1"})
    r("aws_nat_gateway", "main", {"id": "nat-0aaa"})
    for i in range(2):
        r("aws_subnet", "public", {"id": f"subnet-pub{i}", "availability_zone": c["availabilityZones"][i]}, index=i)
        r("aws_subnet", "private", {"id": f"subnet-priv{i}", "availability_zone": c["availabilityZones"][i]}, index=i)
        r("aws_route_table_association", "public", {"id": f"rtbassoc-pub{i}"}, index=i)
        r("aws_route_table_association", "private", {"id": f"rtbassoc-priv{i}"}, index=i)
        r("aws_efs_mount_target", "data", {"id": f"fsmt-{i}"}, index=i)
    r("aws_route_table", "public", {"id": "rtb-pub"})
    r("aws_route_table", "private", {"id": "rtb-priv"})
    for key in ("alb", "api", "db", "efs"):
        ingress = [{"prefix_list_ids": [c["cloudfrontPrefixListId"]], "from_port": 443}] if key == "alb" else []
        r("aws_security_group", key, {"id": f"sg-{key}", "name": c[f"{key}SecurityGroupName"], "description": "Managed by Terraform", "ingress": ingress})
    r("aws_efs_file_system", "data", {"id": "fs-0aaa"})
    r("aws_efs_access_point", "data", {"id": "fsap-0aaa"})
    r("aws_s3_bucket", "web", {"bucket": c["webBucketName"]})
    r("aws_s3_bucket", "alb_logs", {"bucket": c["albLogsBucketName"]})
    r("aws_cloudwatch_log_group", "api", {"name": "/reep/api"})
    r("aws_cloudwatch_log_metric_filter", "dropped_turns", {"name": "interview-dropped-turns", "log_group_name": "/reep/api"})
    r("aws_sns_topic", "alerts", {"arn": f"arn:aws:sns:{REGION}:{ACCOUNT}:reep-alerts"})
    r("aws_sns_topic_subscription", "email", {"endpoint": c["alertEmail"]})
    r("aws_ecr_repository", "api", {"name": "reep/api"})
    r("aws_db_subnet_group", "main", {"name": "reep-db"})
    r(
        "aws_db_instance",
        "main",
        {
            "identifier": "reep-postgres",
            "instance_class": c["dbInstanceClass"],
            "multi_az": c["liveDbMultiAz"],
            "backup_retention_period": c["liveBackupRetentionDays"],
            "allocated_storage": c["liveAllocatedStorage"],
        },
    )
    r("aws_backup_vault", "main", {"name": "reep-vault"})
    r("aws_backup_plan", "daily", {"id": "plan-0aaa"})
    r("aws_backup_selection", "db_and_efs", {"id": "sel-0aaa", "plan_id": "plan-0aaa"})
    alb_arn = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:loadbalancer/app/reep-alb/0aaa"
    r("aws_lb", "main", {"arn": alb_arn, "dns_name": ALB_DNS})
    r("aws_lb_target_group", "api", {"arn": f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:targetgroup/reep-api/0aaa"})
    if plain_http:
        r("aws_lb_listener", "http_origin", {"arn": f"{alb_arn}/listener-80", "port": 80, "default_action": [{"type": "forward"}]}, index=0)
    else:
        r("aws_lb_listener", "http_redirect", {"arn": f"{alb_arn}/listener-80", "port": 80, "default_action": [{"type": "redirect"}]}, index=0)
        r(
            "aws_lb_listener",
            "https",
            {"arn": f"{alb_arn}/listener-443", "port": 443, "certificate_arn": c["albAcmCertificateArn"], "default_action": [{"type": "forward"}]},
            index=0,
        )
    r("aws_ecs_cluster", "main", {"name": "reep", "arn": f"arn:aws:ecs:{REGION}:{ACCOUNT}:cluster/reep"})
    env = [
        {"name": "ENV", "value": "prod"},
        {"name": "WEB_ORIGIN", "value": f"https://{c['domainName']}"},
        {"name": "UPLOAD_DIR", "value": "/data/uploads"},
        {"name": "INTERVIEW_RECORDING_ENABLED", "value": c["interviewRecordingEnabled"]},
        {"name": "BEDROCK_MODEL", "value": c["bedrockModel"]},
        {"name": "BEDROCK_REGION", "value": REGION},
        {"name": "INTERVIEW_CONSENT_VERSION", "value": c["interviewConsentVersion"]},
        {"name": "INTERVIEW_ENGINE", "value": c["interviewEngine"]},
        {"name": "NOVA_SONIC_REGION", "value": c["novaSonicRegion"]},
        {"name": "LLM_ALLOW_REMOTE_STUDENT_DATA", "value": c["allowRemoteStudentData"]},
    ]
    r(
        "aws_ecs_task_definition",
        "api",
        {
            "arn": f"arn:aws:ecs:{REGION}:{ACCOUNT}:task-definition/reep-api:7",
            "cpu": str(c["apiCpu"]),
            "memory": str(c["apiMemory"]),
            "container_definitions": json.dumps([{"name": "api", "environment": env}]),
        },
    )
    r("aws_ecs_service", "api", {"id": f"arn:aws:ecs:{REGION}:{ACCOUNT}:service/reep/api"})
    r("aws_appautoscaling_target", "api", {"resource_id": "service/reep/api", "min_capacity": c["apiMinTasks"], "max_capacity": c["apiMaxTasks"]})
    for name in ("cpu", "memory"):
        r("aws_appautoscaling_policy", f"api_{name}", {"arn": f"arn:aws:autoscaling:{REGION}:{ACCOUNT}:scalingPolicy:0aaa:resource/ecs/service/reep/api:policyName/{name}-target"})
    r("aws_scheduler_schedule", "retention", {"name": "reep-retention-daily"})
    r("aws_cloudfront_function", "spa_fallback", {"arn": f"arn:aws:cloudfront::{ACCOUNT}:function/reep-spa-fallback"})
    r("aws_cloudfront_origin_access_control", "web", {"id": "E0OAC"})
    r(
        "aws_cloudfront_distribution",
        "main",
        {
            "id": "E0DIST",
            "aliases": [c["domainName"]],
            "viewer_certificate": [{"acm_certificate_arn": c["cloudfrontAcmCertificateArn"]}],
            "origin": [
                {"origin_id": "web-s3", "domain_name": f"{c['webBucketName']}.s3.{REGION}.amazonaws.com"},
                {"origin_id": "api-alb", "domain_name": ALB_DNS if plain_http else c["albOriginDomain"]},
            ],
        },
    )
    r("aws_wafv2_web_acl", "edge", {"arn": c["wafWebAclArn"], "name": "reep-edge", "id": "11111111-2222-3333-4444-555555555555"})
    r("aws_secretsmanager_secret", "app", {"arn": c["appSecretArn"]})
    r("aws_secretsmanager_secret", "external", {"arn": c["externalSecretArn"]})
    if with_oidc_provider:
        r("aws_iam_openid_connect_provider", "github", {"arn": OIDC_ARN}, index=0)
    deploy_trust = json.dumps(
        {
            "Version": "2012-10-17",
            "Statement": [
                {
                    "Effect": "Allow",
                    "Principal": {"Federated": OIDC_ARN},
                    "Action": "sts:AssumeRoleWithWebIdentity",
                    "Condition": {
                        "StringEquals": {
                            "token.actions.githubusercontent.com:aud": "sts.amazonaws.com",
                            "token.actions.githubusercontent.com:sub": [
                                "repo:darshani8@285224354/reep-@1339637272:ref:refs/heads/main",
                                "repo:darshani8/reep-:ref:refs/heads/main",
                            ],
                        }
                    },
                }
            ],
        }
    )
    observer_trust = json.dumps(
        {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"AWS": f"arn:aws:iam::{ACCOUNT}:root"}, "Action": "sts:AssumeRole"}]}
    )
    for role_name, tf_name in (
        ("reep-task-execution", "task_execution"),
        ("reep-api-task", "api_task"),
        ("reep-scheduler", "scheduler"),
        ("reep-backup", "backup"),
        ("reep-claude-observer", "claude_observer"),
        ("reep-github-deploy", "github_deploy"),
    ):
        trust = {"github_deploy": deploy_trust, "claude_observer": observer_trust}.get(tf_name, "{}")
        r("aws_iam_role", tf_name, {"name": role_name, "assume_role_policy": trust})
    return {"values": {"root_module": {"resources": rs}}}


def _expected_context(*, with_oidc_provider: bool = True, plain_http: bool = False) -> dict[str, Any]:
    expected = dict(LIVE_CONTEXT)
    if plain_http:
        expected.pop("albAcmCertificateArn")  # no certificate: the stack's default, so the key is not written
        expected.pop("albOriginDomain")  # CloudFront talks to the raw ALB name
    if not with_oidc_provider:
        expected["githubOidcProviderArn"] = OIDC_ARN
    return expected


def _import_template(**context: Any) -> dict[str, Any]:
    """The import-phase template, synthesised the way the CLI does it:
    cdk.json's context (harden targets included) plus what the tool wrote."""
    app = cdk.App(context={**CDK_JSON_CONTEXT, "phase": "import", **context})
    stack = CoreStack(app, "test-core", project="reep", env=cdk.Environment(account=ACCOUNT, region=REGION))
    return Template.from_stack(stack).to_json()


def _resources_of_type(template: dict[str, Any], cfn_type: str) -> list[dict[str, Any]]:
    return [r for r in template["Resources"].values() if r["Type"] == cfn_type]


# --------------------------------------------------------- the import map --


@pytest.mark.parametrize(
    "with_provider, plain_http",
    [(True, False), (False, False), (True, True)],
    ids=["live-default: tls, provider in state", "provider owned elsewhere", "plain-http alb"],
)
def test_context_then_template_then_map_is_a_fixed_point(with_provider: bool, plain_http: bool) -> None:
    """The runbook's order, rehearsed: pass 1 writes the context from the state;
    the mirror is synthesised from it; pass 3 maps every resource of that
    template; and the merged context re-synthesises to the same resource set —
    the import phase must not gain or lose a resource from its own context."""
    state = _fake_state(with_oidc_provider=with_provider, plain_http=plain_http)

    context, problems = import_map.build_context(state)
    assert not problems, problems
    assert context == _expected_context(with_oidc_provider=with_provider, plain_http=plain_http)

    template = _import_template(**context)
    mapping, context_again, problems = import_map.build(state, template)
    assert not problems, problems
    assert context_again == context
    real = {lid for lid, r in template["Resources"].items() if r["Type"] not in import_map.NON_RESOURCE_TYPES}
    assert set(mapping) == real, "every import-phase resource has exactly one identifier"
    for lid, keys in mapping.items():
        cfn_type = template["Resources"][lid]["Type"]
        assert set(keys) == set(import_map.IDENTIFIERS[cfn_type]), f"{lid}: {sorted(keys)} is not the registry's key set for {cfn_type}"
        assert all(v for v in keys.values()), f"{lid}: an empty identifier part"

    again = _import_template(**context_again)
    assert set(again["Resources"]) == set(template["Resources"])

    # The shape is the state's, not the default's.
    ports = sorted(r["Properties"]["Port"] for r in _resources_of_type(template, "AWS::ElasticLoadBalancingV2::Listener"))
    assert ports == ([80] if plain_http else [80, 443])
    assert bool(_resources_of_type(template, "AWS::IAM::OIDCProvider")) is with_provider
    # And the mirror carries the LIVE database values, not cdk.json's harden targets.
    db = _resources_of_type(template, "AWS::RDS::DBInstance")[0]["Properties"]
    assert db["MultiAZ"] is False
    assert db["BackupRetentionPeriod"] == 14
    assert db["AllocatedStorage"] == "20"


def test_the_two_identifier_values_that_are_written_rather_than_read() -> None:
    """INCIDENT (2026-09-07, found by the pre-import review and confirmed
    against the account before any import ran): almost every identifier value
    is READ from the state, but two are composed by this tool, and both were
    composed wrong.

    `AWS::EC2::VPCGatewayAttachment.AttachmentType` is read-only and Terraform
    models the attachment as `vpc_id` on the gateway, so there is nothing to
    read; it was written `"internet"` and CloudFormation answers
    `Invalid Attachment Type 'internet'`. The live identifier is `IGW|vpc-…`.

    `AWS::Backup::BackupSelection.Id` is a composite, and it is
    `<SelectionId>_<BackupPlanId>` — the SELECTION first. It was written the
    other way round and CloudFormation answers `Cannot find Backup plan with
    ID`.

    Both would have failed `cdk import` partway through adopting a stack over
    live student data. `tools/check_identifiers.py` now resolves every value
    through Cloud Control, which is what caught the second one.
    """
    state = _fake_state()
    context, _ = import_map.build_context(state)
    mapping, _, problems = import_map.build(state, _import_template(**context))
    assert not problems, problems

    assert mapping["IgwAttach"]["AttachmentType"] == "IGW", "CloudFormation rejects anything else, 'internet' included"
    # The synthetic state's selection is id 'sel-0aaa' in plan 'plan-0aaa'.
    assert mapping["BackupSelection"]["Id"] == "sel-0aaa_plan-0aaa", "selection id first, then plan id"


def test_the_cli_analytics_resource_is_skipped_not_refused() -> None:
    """INCIDENT (2026-09-07, mid-cutover): `Template.from_stack()` — what every
    test here builds — does not add the `AWS::CDK::Metadata` resource, but the
    CLI's `cdk synth` does unless version reporting is off. So the rehearsal
    passed and the tool refused the FIRST real template at step 2, with admin
    credentials in hand. It is a base64 analytics string, not a cloud resource:
    there is nothing to import and nothing to identify."""
    state = _fake_state()
    context, _ = import_map.build_context(state)
    template = _import_template(**context)
    template["Resources"]["CDKMetadata"] = {"Type": "AWS::CDK::Metadata", "Properties": {"Analytics": "v2:deflate64:H4sI"}}

    mapping, _, problems = import_map.build(state, template)
    assert not problems, problems
    assert "CDKMetadata" not in mapping, "an analytics blob must never be handed to cdk import as a resource"
    assert len(mapping) == len(template["Resources"]) - 1

    # A type that is genuinely unknown must STILL be refused — the skip list is
    # an exemption for CDK's own bookkeeping, not a hole for real resources.
    template["Resources"]["Mystery"] = {"Type": "AWS::Nonexistent::Thing", "Properties": {}}
    _, _, problems = import_map.build(state, template)
    assert any("AWS::Nonexistent::Thing" in p for p in problems), problems


def test_the_old_order_is_refused_with_a_pointer_to_pass_one() -> None:
    """Synthesising before pass 1 — the runbook's first draft — hands the tool
    a plain-HTTP template for a TLS state. It must refuse and say why, not map
    the 80/forward listener onto something."""
    state = _fake_state()
    bare = _import_template()
    mapping, _, problems = import_map.build(state, bare)
    assert problems, "a template of the wrong shape was mapped"
    assert any("pass 1" in p for p in problems), problems
    assert not any(k.startswith("Http") for k in mapping), "no listener may be mapped onto the wrong shape"


def test_the_import_map_refuses_a_state_missing_a_resource_and_writes_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = _fake_state(drop=("aws_db_instance.main",))
    template = _import_template(**LIVE_CONTEXT)
    tools_dir = tmp_path / "infra" / "cdk" / "tools"
    tools_dir.mkdir(parents=True)
    monkeypatch.setattr(import_map, "__file__", str(tools_dir / "import_map.py"))
    state_path = tmp_path / "tf-state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    template_path = tmp_path / "reep-core.template.json"
    template_path.write_text(json.dumps(template), encoding="utf-8")

    assert import_map.main(["import_map.py", str(state_path)]) == 1
    assert import_map.main(["import_map.py", str(state_path), str(template_path)]) == 1
    assert not (tmp_path / "infra" / "cdk" / "import-map.json").exists()
    assert not (tmp_path / "infra" / "cdk" / "cdk.context.json").exists()


def test_the_import_map_writes_both_files_and_merges_the_context(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    state = _fake_state()
    tools_dir = tmp_path / "infra" / "cdk" / "tools"
    tools_dir.mkdir(parents=True)
    here = tmp_path / "infra" / "cdk"
    (here / "cdk.context.json").write_text(json.dumps({"drVaultArn": "arn:aws:backup:ap-southeast-1:1:backup-vault:x"}), encoding="utf-8")
    monkeypatch.setattr(import_map, "__file__", str(tools_dir / "import_map.py"))
    state_path = tmp_path / "tf-state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")

    assert import_map.main(["import_map.py", str(state_path)]) == 0
    written = json.loads((here / "cdk.context.json").read_text(encoding="utf-8"))
    assert written["drVaultArn"].endswith(":x"), "an operator's own context key survives the merge"
    assert {k: written[k] for k in LIVE_CONTEXT} == LIVE_CONTEXT
    assert not (here / "import-map.json").exists(), "pass 1 writes the context only"

    template_path = tmp_path / "reep-core.template.json"
    template_path.write_text(json.dumps(_import_template(**written)), encoding="utf-8")
    assert import_map.main(["import_map.py", str(state_path), str(template_path)]) == 0
    mapping = json.loads((here / "import-map.json").read_text(encoding="utf-8"))
    assert mapping["Db"] == {"DBInstanceIdentifier": "reep-postgres"}
    assert "EdgeAcl" not in mapping, "the WAF is the edge stack's import, not this map's"


# ------------------------------------------- the registry cross-check --


def test_a_composite_identifier_arrives_comma_joined_and_must_be_split() -> None:
    """INCIDENT (2026-09-07, mid-cutover): the runbook said to compare
    `get-template-summary`'s Keys against import-map.json BY EYE. CloudFormation
    returns a composite primaryIdentifier as ONE comma-joined string, so all
    seven composite types read as mismatches against a table that was correct.
    An operator following that instruction would have "fixed" the right answer.
    """
    assert check_identifiers.split_keys(["ResourceId,ScalableDimension,ServiceNamespace"]) == {
        "ResourceId",
        "ScalableDimension",
        "ServiceNamespace",
    }
    assert check_identifiers.split_keys(["BucketName"]) == {"BucketName"}
    assert check_identifiers.split_keys(["Name", "Id", "Scope"]) == {"Name", "Id", "Scope"}


def test_the_cross_check_detects_a_wrong_key_set_and_an_unmapped_resource() -> None:
    """What this pins is the DETECTION, not the registry.

    The registry itself can only be checked online, by
    `tools/check_identifiers.py` at step 2 — the summary built below is
    constructed from `IDENTIFIERS`, so asserting that it agrees with
    `IDENTIFIERS` would prove nothing (the pre-import review called that out,
    correctly, as a tautology). These assertions are the two failure paths.
    """
    state = _fake_state()
    context, _ = import_map.build_context(state)
    template = _import_template(**context)
    mapping, _, problems = import_map.build(state, template)
    assert not problems, problems
    resources = template["Resources"]

    # A stand-in for the registry, comma-joining composites exactly as the real
    # API returns them. Its only job is to be something to disagree WITH.
    summary = [{"Type": t, "Keys": [",".join(keys)]} for t, keys in import_map.IDENTIFIERS.items() if any(r["Type"] == t for r in resources.values())]

    wrong_keys = [{"Type": e["Type"], "Keys": ["SomethingElse"]} if e["Type"] == "AWS::RDS::DBInstance" else e for e in summary]
    assert any("AWS::RDS::DBInstance" in p for p in check_identifiers.compare(wrong_keys, mapping, resources)), "a wrong key set was not detected"

    short = {k: v for k, v in mapping.items() if k != "Db"}
    assert any("no map entry" in p for p in check_identifiers.compare(summary, short, resources)), "an unmapped resource was not detected"

    # And the composite ordering the Cloud Control identifier depends on.
    assert check_identifiers.cc_identifier({"AttachmentType": "IGW", "VpcId": "vpc-1"}, ["AttachmentType", "VpcId"]) == "IGW|vpc-1"


def test_every_managed_address_has_a_home_or_a_written_reason() -> None:
    """INCIDENT (2026-09-07): today's preflight edit replaced the "78
    addresses" check with a bare non-zero count, and the comment claimed
    `import_map.py` covered it. It did not — the tool only ever walked the
    TEMPLATE, so nothing in the repository checked the other direction. After
    step 6 releases the state, a live resource that neither the mirror claims
    nor `UNMAPPED_ON_PURPOSE` names is one nobody will ever manage again.
    """
    state = _fake_state()
    context, _ = import_map.build_context(state)
    _, _, problems = import_map.build(state, _import_template(**context))
    assert not problems, problems

    # Drop an exemption the synthetic state actually contains: the resources it
    # covered must then be reported as orphans.
    dropped = "aws_sns_topic_subscription"
    assert any(r["type"] == dropped for r in import_map._tf_resources(state)), "the mutation target must be in the fixture"
    original = import_map.UNMAPPED_ON_PURPOSE
    try:
        import_map.UNMAPPED_ON_PURPOSE = {k: v for k, v in original.items() if k != dropped}
        _, _, problems = import_map.build(state, _import_template(**context))
    finally:
        import_map.UNMAPPED_ON_PURPOSE = original
    assert any(dropped in p and "orphaned" in p for p in problems), problems

    # The two secrets are left unmanaged ON PURPOSE, and the reason must say so
    # — this is a decision, not a mechanism, and it must stay written down.
    assert "aws_secretsmanager_secret" in import_map.UNMAPPED_ON_PURPOSE
    assert "NOT owned by CloudFormation" in import_map.UNMAPPED_ON_PURPOSE["aws_secretsmanager_secret"]


# ---------------------------------------------------------- the tfvars --


def _declared_variables() -> set[str]:
    names: set[str] = set()
    for tf in TF_DIR.glob("*.tf"):
        names |= set(re.findall(r'^variable\s+"([A-Za-z0-9_]+)"', tf.read_text(encoding="utf-8"), re.MULTILINE))
    return names


@requires_terraform
def test_the_tfvars_cover_every_variable_terraform_declares() -> None:
    """`terraform plan -var-file=prod.tfvars` must exit 0 in step 0, which it
    cannot if a variable the first apply was given is missing here and falls
    back to its default. Every `variable` block gets a rule; a new one without
    a rule fails this test, not the plan."""
    values, problems = tfvars_from_state.derive(_fake_state())
    assert not problems, problems
    assert set(values) == _declared_variables()


def test_the_tfvars_are_read_from_the_state_not_from_defaults() -> None:
    values, problems = tfvars_from_state.derive(_fake_state())
    assert not problems, problems
    assert values["region"] == REGION
    assert values["project"] == "reep"
    assert values["alert_email"] == "ops@bgscet.ac.in"
    assert values["alb_acm_certificate_arn"] == LIVE_CONTEXT["albAcmCertificateArn"]
    assert values["alb_origin_domain"] == "origin.reep.bgscet.ac.in"
    assert values["domain_name"] == "reep.bgscet.ac.in"
    assert values["cloudfront_acm_certificate_arn"] == LIVE_CONTEXT["cloudfrontAcmCertificateArn"]
    assert values["restrict_alb_to_cloudfront"] is True
    assert values["db_multi_az"] is False
    assert values["db_backup_retention_days"] == 14
    assert values["github_repository"] == "darshani8/reep-"
    assert values["github_repository_ids"] == "darshani8@285224354/reep-@1339637272"
    assert values["github_deploy_ref"] == "refs/heads/main"
    assert values["create_github_oidc_provider"] is True
    assert values["observer_principal_arn"] == ""

    text = tfvars_from_state.render(values)
    assert 'alert_email = "ops@bgscet.ac.in"' in text
    assert "db_multi_az = false" in text
    assert "api_cpu = 512" in text
    assert 'region = "ap-south-1"' in text

    # A plain-HTTP ALB and a provider owned elsewhere read back as the values
    # such an apply was given — never as this account's.
    values, problems = tfvars_from_state.derive(_fake_state(with_oidc_provider=False, plain_http=True))
    assert not problems, problems
    assert values["alb_acm_certificate_arn"] == ""
    assert values["alb_origin_domain"] == ""
    assert values["create_github_oidc_provider"] is False


def test_the_two_tools_agree_on_every_value_they_share() -> None:
    """The CDK mirror and the Terraform plan are checked against the same
    account; if the two tools read a value differently, one of the two checks
    passes for the wrong reason."""
    state = _fake_state()
    context, problems = import_map.build_context(state)
    assert not problems, problems
    values, problems = tfvars_from_state.derive(state)
    assert not problems, problems
    for ctx_key, var_key in SHARED_VALUES.items():
        assert context[ctx_key] == values[var_key], f"{ctx_key}={context[ctx_key]!r} but {var_key}={values[var_key]!r}"


def test_the_tfvars_refuse_without_an_alert_subscription_and_write_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """alert_email has no default: a plan without it fails on input, and a
    guessed one is a plan that proposes to re-point the alarms."""
    state = _fake_state(drop=("aws_sns_topic_subscription.email",))
    values, problems = tfvars_from_state.derive(state)
    assert any("aws_sns_topic_subscription.email" in p for p in problems), problems
    assert "alert_email" not in values

    tools_dir = tmp_path / "infra" / "cdk" / "tools"
    tools_dir.mkdir(parents=True)
    (tmp_path / "infra" / "aws").mkdir()
    monkeypatch.setattr(tfvars_from_state, "__file__", str(tools_dir / "tfvars_from_state.py"))
    state_path = tmp_path / "tf-state.json"
    state_path.write_text(json.dumps(state), encoding="utf-8")
    assert tfvars_from_state.main(["tfvars_from_state.py", str(state_path)]) == 1
    assert not (tmp_path / "infra" / "aws" / "prod.tfvars").exists()


def test_the_tfvars_are_written_once_and_then_left_alone(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    tools_dir = tmp_path / "infra" / "cdk" / "tools"
    tools_dir.mkdir(parents=True)
    (tmp_path / "infra" / "aws").mkdir()
    monkeypatch.setattr(tfvars_from_state, "__file__", str(tools_dir / "tfvars_from_state.py"))
    state_path = tmp_path / "tf-state.json"
    state_path.write_text(json.dumps(_fake_state()), encoding="utf-8")
    target = tmp_path / "infra" / "aws" / "prod.tfvars"

    assert tfvars_from_state.main(["tfvars_from_state.py", str(state_path)]) == 0
    first = target.read_text(encoding="utf-8")
    assert 'alert_email = "ops@bgscet.ac.in"' in first

    target.write_text(first + 'observer_principal_arn = "arn:aws:iam::123456789012:user/hand-edited"\n', encoding="utf-8")
    assert tfvars_from_state.main(["tfvars_from_state.py", str(state_path)]) == 0
    assert "hand-edited" in target.read_text(encoding="utf-8"), "an operator's edit is never overwritten"


# ------------------------------------------- the runbook and the scripts --


def test_the_runbook_names_tools_that_exist() -> None:
    text = (DOCS / "cdk-cutover.md").read_text(encoding="utf-8")
    named = set(re.findall(r"tools/([A-Za-z0-9_]+\.(?:py|sh))", text))
    assert {"cutover_preflight.sh", "import_map.py", "tfvars_from_state.py", "check_identifiers.py", "terraform_release.sh"} <= named, named
    missing = sorted(n for n in named if not (TOOLS / n).exists())
    assert not missing, f"the runbook names tools that do not exist: {missing}"
    # Steps 1-2 keep the tool's order: context (pass 1), synth, map (pass 3).
    steps = text[text.index("## Step 1") : text.index("## Step 3")]
    pass_one = steps.index("python tools/import_map.py tf-state.json")  # the first occurrence is pass 1
    synth = steps.index("cdk synth reep-core -c phase=import")
    pass_three = steps.index("python tools/import_map.py tf-state.json cdk.out/")
    assert pass_one < synth < pass_three, "the runbook synthesises before pass 1 again"


def _bash() -> str | None:
    candidates: list[str | None] = []
    if sys.platform == "win32":
        candidates += [r"C:\Program Files\Git\bin\bash.exe", r"C:\Program Files\Git\usr\bin\bash.exe"]
    else:
        candidates.append(shutil.which("bash"))
    return next((c for c in candidates if c and Path(c).exists()), None)


@pytest.mark.parametrize("script", ["cutover_preflight.sh", "terraform_release.sh"])
def test_the_shell_scripts_parse(script: str) -> None:
    bash = _bash()
    if bash is None:
        pytest.skip("no bash on this machine")
    subprocess.run([bash, "-n", str(TOOLS / script)], check=True)


def test_the_preflight_only_reads_from_aws_and_never_applies_terraform() -> None:
    """Its contract is 'safe to run any number of times'. Every `aws` call is
    a describe/get/list/head, and terraform is only ever init/show/state list/
    plan — message strings excluded, since the snapshot command is printed for
    the human, not run."""
    text = (TOOLS / "cutover_preflight.sh").read_text(encoding="utf-8")
    code = "\n".join(line for line in text.splitlines() if not re.match(r"\s*(#|info |warn |fail |pass )", line))
    for service, op in re.findall(r"\baws\s+([a-z0-9-]+)\s+([a-z0-9-]+)", code):
        if service == "configure":
            continue  # reads the LOCAL credential store; nothing in AWS is touched
        assert op == "get" or op.startswith(("describe-", "get-", "list-", "head-")), f"aws {service} {op} is not a read"
    for op in re.findall(r"\bterraform\s+([a-z-]+)", code):
        assert op in {"version", "init", "show", "state", "plan"}, f"terraform {op} in a read-only preflight"
    assert "terraform apply" not in code and "state rm" not in code
    assert "set -e" not in code, "a single failed check must not end the report early"
