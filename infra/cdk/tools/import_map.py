#!/usr/bin/env python3
"""Build the import-time context and the `cdk import` resource map from the
Terraform state — so every mirrored value and every identifier is READ from
what exists, never typed. Two passes, because the map needs a template that
already has the live shape:

    cd infra/aws  && terraform show -json > ../cdk/tf-state.json
    cd ../cdk
    python tools/import_map.py tf-state.json                   # 1. the context, from the state alone
    cdk synth reep-core -c phase=import --quiet                # 2. the mirror, now with the live shape
    python tools/import_map.py tf-state.json cdk.out/reep-core.template.json   # 3. the map, checked against it

Pass 1 writes `cdk.context.json` (merged): everything the stack must render
identically — the random-suffix names, the secret ARNs, the AZs, the prefix
list, the WAF ARN, AND the variable-driven values (certificates, domain,
cpu/memory, task counts, instance class, the LIVE multi-AZ / retention /
storage, the alert address, the container environment). Pass 3 writes
`import-map.json` — {LogicalId: {IdentifierKey: value, ...}} for
`cdk import --resource-mapping import-map.json` — and re-merges the context.

WHY THE CONTEXT COMES FIRST. A context-free synth renders the DEFAULT shape:
one plain-HTTP listener, a declared OIDC provider. The live ALB has a
certificate, so it has two listeners (80 → redirect, 443 → forward), and a map
built against the wrong template has nothing to attach the 443 listener's ARN
to. The first draft of the runbook synthesised before running this tool and
would have been refused at step 2 with credentials in hand;
tests/test_cutover_tools.py now rehearses the order above without an account.

WHY A TOOL AND NOT A FORM. Two dozen identifiers, several of them random
suffixes Terraform chose. One typo in a security-group name is a group
CloudFormation cannot find; one typo in the database identifier is an import
that adopts NOTHING and a first deploy that tries to create `reep-postgres`
beside the real one. And the *values* matter as much as the ids: a mirror
that says Multi-AZ when the instance is single-AZ imports fine (CloudFormation
does not compare properties on import) and then never converts it, because
the harden template carries the same value and nothing changes.

THE IDENTIFIER KEYS ARE THE REGISTRY'S, NOT A GUESS. Eight of the first
draft's thirty were wrong (composite keys collapsed to one part, a name where
an ARN was required). CloudFormation validates them when the IMPORT change set
is created, so a wrong key fails before anything changes — but a runbook that
fails at step 4 is not a runbook. The authoritative cross-check is

    aws cloudformation get-template-summary \\
        --template-body file://cdk.out/reep-core.template.json \\
        --query ResourceIdentifierSummaries

and docs/cdk-cutover.md runs it beside this tool.

WHAT IT REFUSES. Any template resource with no identifier; any identifier
whose value the state does not hold; any mirrored value it cannot source from
the state; a template whose shape is not the state's (synthesise with the
context first). It writes NOTHING until every check passes — a partial map on
disk is how an import adopts half a stack and the next deploy creates the
other half twice.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

#: CloudFormation registry primaryIdentifier per type, as a tuple of keys.
#: A composite identifier needs EVERY part. A type missing here is a type this
#: tool has not been taught; it is reported, not guessed.
IDENTIFIERS: dict[str, tuple[str, ...]] = {
    "AWS::EC2::VPC": ("VpcId",),
    "AWS::EC2::InternetGateway": ("InternetGatewayId",),
    "AWS::EC2::VPCGatewayAttachment": ("AttachmentType", "VpcId"),
    "AWS::EC2::Subnet": ("SubnetId",),
    "AWS::EC2::EIP": ("PublicIp", "AllocationId"),
    "AWS::EC2::NatGateway": ("NatGatewayId",),
    "AWS::EC2::RouteTable": ("RouteTableId",),
    "AWS::EC2::Route": ("RouteTableId", "CidrBlock"),
    "AWS::EC2::SubnetRouteTableAssociation": ("Id",),
    "AWS::EC2::SecurityGroup": ("Id",),
    "AWS::EFS::FileSystem": ("FileSystemId",),
    "AWS::EFS::MountTarget": ("Id",),
    "AWS::EFS::AccessPoint": ("AccessPointId",),
    "AWS::S3::Bucket": ("BucketName",),
    "AWS::S3::BucketPolicy": ("Bucket",),
    "AWS::Logs::LogGroup": ("LogGroupName",),
    "AWS::Logs::MetricFilter": ("LogGroupName", "FilterName"),
    "AWS::SNS::Topic": ("TopicArn",),
    "AWS::ECR::Repository": ("RepositoryName",),
    "AWS::RDS::DBSubnetGroup": ("DBSubnetGroupName",),
    "AWS::RDS::DBInstance": ("DBInstanceIdentifier",),
    "AWS::Backup::BackupVault": ("BackupVaultName",),
    "AWS::Backup::BackupPlan": ("BackupPlanId",),
    "AWS::Backup::BackupSelection": ("Id",),  # composed: <BackupPlanId>_<SelectionId>
    "AWS::IAM::Role": ("RoleName",),
    "AWS::IAM::OIDCProvider": ("Arn",),
    "AWS::ElasticLoadBalancingV2::LoadBalancer": ("LoadBalancerArn",),
    "AWS::ElasticLoadBalancingV2::TargetGroup": ("TargetGroupArn",),
    "AWS::ElasticLoadBalancingV2::Listener": ("ListenerArn",),
    "AWS::ECS::Cluster": ("ClusterName",),
    "AWS::ECS::TaskDefinition": ("TaskDefinitionArn",),
    "AWS::ECS::Service": ("ServiceArn", "Cluster"),
    "AWS::ApplicationAutoScaling::ScalableTarget": ("ResourceId", "ScalableDimension", "ServiceNamespace"),
    "AWS::ApplicationAutoScaling::ScalingPolicy": ("Arn", "ScalableDimension"),
    "AWS::Scheduler::Schedule": ("Name",),
    "AWS::CloudFront::Function": ("FunctionARN",),
    "AWS::CloudFront::OriginAccessControl": ("Id",),
    "AWS::CloudFront::Distribution": ("Id",),
    "AWS::CloudWatch::Alarm": ("AlarmName",),
    "AWS::WAFv2::WebACL": ("Name", "Id", "Scope"),
}

_SCALABLE_DIMENSION = "ecs:service:DesiredCount"

#: Terraform resource TYPES that are managed in the state but deliberately have
#: no resource of their own in the CloudFormation mirror, and WHY. Anything
#: managed that is not mapped and not named here is reported: after step 6 the
#: state is released, and a live resource that neither side claims is a
#: resource nobody will ever manage again.
#:
#: This check exists because the preflight's "78 addresses" heuristic was
#: replaced today with a bare non-zero count, which removed the only
#: state-to-template coverage check in the repository. The pre-import review
#: caught the regression. `import_map.py` is the right home: it is the one tool
#: that reads both sides.
UNMAPPED_ON_PURPOSE: dict[str, str] = {
    # Collapse into a property of a resource that IS mapped.
    "aws_ecr_lifecycle_policy": "rendered as ApiRepo.LifecyclePolicy",
    "aws_iam_role_policy": "rendered as the role's inline Policies",
    "aws_iam_role_policy_attachment": "rendered as the role's ManagedPolicyArns",
    "aws_s3_bucket_lifecycle_configuration": "rendered as the bucket's LifecycleConfiguration",
    "aws_s3_bucket_public_access_block": "rendered as the bucket's PublicAccessBlockConfiguration",
    "aws_s3_bucket_versioning": "rendered as the bucket's VersioningConfiguration",
    # No CloudFormation counterpart at all.
    "random_password": "a Terraform-only generator; the value it produced lives in the secret",
    "aws_secretsmanager_secret_version": "the stack references the secrets by ARN and never writes a version",
    # Owned by a different stack, or created later, ON PURPOSE.
    "aws_wafv2_web_acl": "imported by the reep-edge-waf stack, in us-east-1",
    "aws_sns_topic_subscription": "an email subscription cannot be imported; harden re-creates it (SNS dedupes on topic+endpoint)",
    # LEFT UNMANAGED, and this is the one entry that is a decision rather than
    # a mechanism. The two secrets hold AUTH_SECRET, DATABASE_URL and the
    # operator-owned external keys. The stack reads them by ARN and must never
    # own them: a CloudFormation resource for a secret is a resource
    # CloudFormation can rewrite, and rewriting AUTH_SECRET signs every student
    # out and rewriting DATABASE_URL takes the api off its database. After step
    # 7 no file in the repo declares them, so docs/cdk-cutover.md records their
    # ARNs, their keys and who fills them.
    "aws_secretsmanager_secret": "referenced by ARN and deliberately NOT owned by CloudFormation — see the runbook's 'What is left unmanaged'",
}

#: Template entries that are NOT cloud resources and therefore have no
#: identifier to import: `AWS::CDK::Metadata` is a base64 analytics string the
#: CLI appends unless version reporting is off. The synth tests build their
#: template with `Template.from_stack()`, which does not add it, so the
#: rehearsal never saw it and the tool refused the first real `cdk synth`
#: output at step 2 — with credentials in hand, which is the failure the
#: rehearsal exists to prevent. Skipped here rather than suppressed at synth,
#: so the tool is correct however the template was produced.
NON_RESOURCE_TYPES = frozenset({"AWS::CDK::Metadata"})

#: Environment variables the stack renders from context; each is read from the
#: live container definition so the mirror carries the running values.
_ENV_TO_CONTEXT = {
    "BEDROCK_MODEL": "bedrockModel",
    "NOVA_SONIC_REGION": "novaSonicRegion",
    "INTERVIEW_ENGINE": "interviewEngine",
    "INTERVIEW_RECORDING_ENABLED": "interviewRecordingEnabled",
    "INTERVIEW_CONSENT_VERSION": "interviewConsentVersion",
    "LLM_ALLOW_REMOTE_STUDENT_DATA": "allowRemoteStudentData",
}


def _tf_resources(state: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten `terraform show -json` into the managed resources."""
    out: list[dict[str, Any]] = []

    def walk(module: dict[str, Any]) -> None:
        for r in module.get("resources", []):
            if r.get("mode") == "managed":
                out.append(r)
        for child in module.get("child_modules", []):
            walk(child)

    walk(state.get("values", {}).get("root_module", {}))
    return out


def _one(resources: list[dict[str, Any]], tf_type: str, name: str, index: int | None = None) -> dict[str, Any] | None:
    for r in resources:
        if r["type"] != tf_type or r["name"] != name:
            continue
        if index is not None and r.get("index") != index:
            continue
        return r["values"]
    return None


def _trust_policy(role: dict[str, Any]) -> dict[str, Any]:
    """An IAM role's assume-role policy, which the state holds as a JSON string."""
    raw = role.get("assume_role_policy")
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw or "{}")
    except ValueError:
        return {}


def _federated_principal(role: dict[str, Any]) -> str | None:
    """The OIDC provider ARN a role trusts — github_oidc.tf names it whether
    Terraform created the provider or another stack did."""
    for st in _trust_policy(role).get("Statement", []):
        fed = (st.get("Principal") or {}).get("Federated")
        if isinstance(fed, str) and fed:
            return fed
    return None


class _Lookup:
    """The state's managed resources, the two accessors every rule uses, and
    the problem list they report into."""

    def __init__(self, state: dict[str, Any], problems: list[str]) -> None:
        self.tf = _tf_resources(state)
        self.problems = problems
        #: Every (type, name, index) this build actually looked at. What is
        #: never looked at is what nobody wrote a rule for.
        self.consumed: set[tuple[str, str, Any]] = set()

    def one(self, tf_type: str, name: str, index: int | None = None) -> dict[str, Any] | None:
        found = _one(self.tf, tf_type, name, index)
        if found is not None:
            self.consumed.add((tf_type, name, index))
        return found

    def consume_by(self, tf_type: str, attr: str, value: Any) -> None:
        """Mark the address of this type whose `attr` equals `value` as read.

        Some resources are mapped by walking the TEMPLATE rather than the state
        — the alarms by AlarmName, the bucket policies by their bucket — so
        they are never looked up by Terraform name and would otherwise read as
        orphans. This records them by the attribute that identifies them.
        """
        for r in self.tf:
            if r["type"] == tf_type and r["values"].get(attr) == value:
                self.consumed.add((tf_type, r["name"], r.get("index")))
                return

    def unconsumed(self) -> list[str]:
        """Managed addresses this build never read and that are not exempt."""
        missed: list[str] = []
        for r in self.tf:
            if r["type"] in UNMAPPED_ON_PURPOSE:
                continue
            key = (r["type"], r["name"], r.get("index"))
            if key in self.consumed or (r["type"], r["name"], None) in self.consumed:
                continue
            suffix = "" if r.get("index") is None else f"[{r['index']}]"
            missed.append(f"{r['type']}.{r['name']}{suffix}")
        return sorted(missed)

    def need(self, tf_type: str, name: str, index: int | None = None) -> dict[str, Any]:
        v = self.one(tf_type, name, index)
        self.consumed.add((tf_type, name, index))
        if v is None:
            self.problems.append(f"{tf_type}.{name}{'' if index is None else f'[{index}]'} is not in the state")
            return {}
        return v


def build_context(state: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Pass 1: the values the stack must render identically, from the state
    alone. No template is needed — and none must be, because the template
    that follows is synthesised FROM this."""
    problems: list[str] = []
    s = _Lookup(state, problems)
    need, one = s.need, s.one
    context: dict[str, Any] = {}

    def ctx(key: str, value: Any, *, required: bool = True) -> None:
        if value in (None, "", []):
            if required:
                problems.append(f"context {key}: not in the state")
            return
        context[key] = value

    alb = need("aws_lb", "main")
    db = need("aws_db_instance", "main")
    taskdef = need("aws_ecs_task_definition", "api")
    target = need("aws_appautoscaling_target", "api")
    dist = need("aws_cloudfront_distribution", "main")

    ctx("webBucketName", need("aws_s3_bucket", "web").get("bucket"))
    ctx("albLogsBucketName", need("aws_s3_bucket", "alb_logs").get("bucket"))
    ctx("appSecretArn", need("aws_secretsmanager_secret", "app").get("arn"))
    ctx("externalSecretArn", need("aws_secretsmanager_secret", "external").get("arn"))
    for key, tf_name in (
        ("albSecurityGroupName", "alb"),
        ("apiSecurityGroupName", "api"),
        ("dbSecurityGroupName", "db"),
        ("efsSecurityGroupName", "efs"),
    ):
        sg = one("aws_security_group", tf_name) or {}
        ctx(key, sg.get("name"))
        # GroupDescription is create-only. The stack renders Terraform's
        # default; anything else is a permanent drift, so refuse to guess.
        if sg and sg.get("description") != "Managed by Terraform":
            problems.append(f"security group {tf_name}: description is {sg.get('description')!r}, the stack renders 'Managed by Terraform'")
    pub = [one("aws_subnet", "public", i) or {} for i in range(2)]
    ctx("availabilityZones", [sn.get("availability_zone") for sn in pub if sn.get("availability_zone")])
    alb_sg = one("aws_security_group", "alb") or {}
    prefix = next((r["prefix_list_ids"][0] for r in alb_sg.get("ingress", []) if r.get("prefix_list_ids")), None)
    ctx("cloudfrontPrefixListId", prefix, required=False)
    ctx("restrictAlbToCloudfront", bool(prefix))
    waf = one("aws_wafv2_web_acl", "edge") or {}
    ctx("wafWebAclArn", waf.get("arn"))
    # TLS shape: read from the listeners that exist, never from a tfvars file.
    https = one("aws_lb_listener", "https", index=0)
    ctx("albAcmCertificateArn", https.get("certificate_arn") if https else "", required=False)
    aliases = dist.get("aliases") or []
    ctx("domainName", aliases[0] if aliases else "", required=False)
    vc = (dist.get("viewer_certificate") or [{}])[0]
    ctx("cloudfrontAcmCertificateArn", vc.get("acm_certificate_arn") or "", required=False)
    api_origin = next((o for o in dist.get("origin", []) if o.get("origin_id") == "api-alb"), {})
    origin_domain = api_origin.get("domain_name", "")
    ctx("albOriginDomain", "" if origin_domain == alb.get("dns_name") else origin_domain, required=False)
    ctx("apiCpu", int(taskdef["cpu"]) if taskdef.get("cpu") else None)
    ctx("apiMemory", int(taskdef["memory"]) if taskdef.get("memory") else None)
    ctx("apiMinTasks", target.get("min_capacity"))
    ctx("apiMaxTasks", target.get("max_capacity"))
    ctx("dbInstanceClass", db.get("instance_class"))
    # THE LIVE VALUES the import mirror must carry, as opposed to the harden
    # targets in cdk.json. See M1 in docs/cdk-cutover.md.
    ctx("liveDbMultiAz", bool(db.get("multi_az")))
    ctx("liveBackupRetentionDays", db.get("backup_retention_period"))
    ctx("liveAllocatedStorage", db.get("allocated_storage"))
    sub = one("aws_sns_topic_subscription", "email") or {}
    ctx("alertEmail", sub.get("endpoint"), required=False)
    # The OIDC provider: DECLARED and imported when Terraform created it (the
    # default), REFERENCED by ARN when another stack owns it. The ARN is
    # written only in the second case — writing it while the provider is in
    # the state made the re-synth drop the very resource the map listed.
    if one("aws_iam_openid_connect_provider", "github", index=0) is None:
        ctx("githubOidcProviderArn", _federated_principal(need("aws_iam_role", "github_deploy")))
    try:
        cdefs = json.loads(taskdef.get("container_definitions") or "[]")
        env = {e["name"]: e["value"] for e in (cdefs[0].get("environment") or [])}
    except (ValueError, IndexError, KeyError, TypeError):
        env = {}
        problems.append("container_definitions could not be parsed from the state")
    for env_name, key in _ENV_TO_CONTEXT.items():
        ctx(key, env.get(env_name))
    return context, problems


def build(state: dict[str, Any], template: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    """Pass 3: the resource map, checked against a template synthesised WITH
    the pass-1 context. Returns (mapping, context, problems)."""
    context, problems = build_context(state)
    s = _Lookup(state, problems)
    need, one = s.need, s.one
    resources: dict[str, Any] = template["Resources"]
    mapping: dict[str, dict[str, str]] = {}

    def put(logical: str, **keys: Any) -> None:
        if logical not in resources:
            problems.append(f"{logical}: not in the template — the stack renamed it, or the template was synthesised without the context (pass 1)")
            return
        cfn_type = resources[logical]["Type"]
        expected = IDENTIFIERS.get(cfn_type)
        if expected is None:
            problems.append(f"{logical}: type {cfn_type} is not in IDENTIFIERS")
            return
        missing = [k for k in expected if keys.get(k) in (None, "")]
        if missing or set(keys) != set(expected):
            problems.append(f"{logical} ({cfn_type}): identifier needs {expected}, got {sorted(keys)} (empty: {missing})")
            return
        mapping[logical] = {k: str(v) for k, v in keys.items()}

    def only(cfn_type: str) -> str | None:
        hits = [lid for lid, res in resources.items() if res["Type"] == cfn_type]
        if len(hits) == 1:
            return hits[0]
        if hits:
            problems.append(f"{cfn_type}: expected one in the template, found {hits}")
        return None

    def by_property(cfn_type: str, prop: str, value: Any) -> str | None:
        """The logical id of the one resource of this type whose property has
        this value. L2 constructs (the roles, the buckets) render logical ids
        with a hash suffix — `BackupRoleF43CFD90` — so a rule that names
        `BackupRole` matches nothing; the rehearsal found eight such rules.
        The physical name is in the template as a property, so match on that."""
        hits = [lid for lid, res in resources.items() if res["Type"] == cfn_type and res.get("Properties", {}).get(prop) == value]
        if len(hits) == 1:
            return hits[0]
        problems.append(f"{cfn_type} with {prop}={value!r}: expected exactly one in the template, found {hits}")
        return None

    vpc = need("aws_vpc", "main")
    igw = need("aws_internet_gateway", "main")
    eip = need("aws_eip", "nat")
    nat = need("aws_nat_gateway", "main")
    fs = need("aws_efs_file_system", "data")
    ap = need("aws_efs_access_point", "data")
    lg = need("aws_cloudwatch_log_group", "api")
    mf = need("aws_cloudwatch_log_metric_filter", "dropped_turns")
    topic = need("aws_sns_topic", "alerts")
    repo = need("aws_ecr_repository", "api")
    dbsg = need("aws_db_subnet_group", "main")
    db = need("aws_db_instance", "main")
    vault = need("aws_backup_vault", "main")
    plan = need("aws_backup_plan", "daily")
    sel = need("aws_backup_selection", "db_and_efs")
    alb = need("aws_lb", "main")
    tg = need("aws_lb_target_group", "api")
    cluster = need("aws_ecs_cluster", "main")
    taskdef = need("aws_ecs_task_definition", "api")
    service = need("aws_ecs_service", "api")
    target = need("aws_appautoscaling_target", "api")
    schedule = need("aws_scheduler_schedule", "retention")
    cf_fn = need("aws_cloudfront_function", "spa_fallback")
    oac = need("aws_cloudfront_origin_access_control", "web")
    dist = need("aws_cloudfront_distribution", "main")

    # --- one-of-a-kind resources ------------------------------------------
    singles: list[tuple[str, dict[str, Any]]] = [
        ("AWS::EC2::VPC", {"VpcId": vpc.get("id")}),
        ("AWS::EC2::InternetGateway", {"InternetGatewayId": igw.get("id")}),
        # "IGW", not "internet". AttachmentType is READ-ONLY — Terraform has no
        # resource to read it from (it models the attachment as `vpc_id` on the
        # gateway), so it is the one identifier value in the whole map that is
        # written rather than read, and it was written wrong. CloudFormation
        # answers `Invalid Attachment Type 'internet'` and the import fails on
        # this resource. Confirmed against the account, read-only:
        #   aws cloudcontrol list-resources --type-name AWS::EC2::VPCGatewayAttachment
        #   -> ["IGW|vpc-010669fa9293137e2", ...]
        # `tools/check_identifiers.py` now resolves every identifier VALUE the
        # same way, so an invented one cannot reach an import again.
        ("AWS::EC2::VPCGatewayAttachment", {"AttachmentType": "IGW", "VpcId": vpc.get("id")}),
        ("AWS::EC2::EIP", {"PublicIp": eip.get("public_ip"), "AllocationId": eip.get("allocation_id") or eip.get("id")}),
        ("AWS::EC2::NatGateway", {"NatGatewayId": nat.get("id")}),
        ("AWS::EFS::FileSystem", {"FileSystemId": fs.get("id")}),
        ("AWS::EFS::AccessPoint", {"AccessPointId": ap.get("id")}),
        ("AWS::Logs::LogGroup", {"LogGroupName": lg.get("name")}),
        ("AWS::Logs::MetricFilter", {"LogGroupName": mf.get("log_group_name"), "FilterName": mf.get("name")}),
        ("AWS::SNS::Topic", {"TopicArn": topic.get("arn")}),
        ("AWS::ECR::Repository", {"RepositoryName": repo.get("name")}),
        ("AWS::RDS::DBSubnetGroup", {"DBSubnetGroupName": dbsg.get("name")}),
        ("AWS::RDS::DBInstance", {"DBInstanceIdentifier": db.get("identifier")}),
        ("AWS::Backup::BackupVault", {"BackupVaultName": vault.get("name")}),
        ("AWS::Backup::BackupPlan", {"BackupPlanId": plan.get("id")}),
        # The registry composes this one <SelectionId>_<BackupPlanId> — the
        # SELECTION first. This was written the other way round, on the same
        # guess that produced `AttachmentType: "internet"`, and it fails the
        # same way: `Cannot find Backup plan with ID`. Confirmed against the
        # account, read-only:
        #   aws cloudcontrol list-resources --type-name AWS::Backup::BackupSelection
        #   -> ["f151d9af-…(selection)_86be484d-…(plan)"]
        ("AWS::Backup::BackupSelection", {"Id": f"{sel.get('id')}_{sel.get('plan_id')}" if sel else None}),
        ("AWS::ElasticLoadBalancingV2::LoadBalancer", {"LoadBalancerArn": alb.get("arn")}),
        ("AWS::ElasticLoadBalancingV2::TargetGroup", {"TargetGroupArn": tg.get("arn")}),
        ("AWS::ECS::Cluster", {"ClusterName": cluster.get("name")}),
        ("AWS::ECS::TaskDefinition", {"TaskDefinitionArn": taskdef.get("arn")}),
        ("AWS::ECS::Service", {"ServiceArn": service.get("id"), "Cluster": cluster.get("name")}),
        (
            "AWS::ApplicationAutoScaling::ScalableTarget",
            {"ResourceId": target.get("resource_id"), "ScalableDimension": _SCALABLE_DIMENSION, "ServiceNamespace": "ecs"},
        ),
        ("AWS::Scheduler::Schedule", {"Name": schedule.get("name")}),
        ("AWS::CloudFront::Function", {"FunctionARN": cf_fn.get("arn")}),
        ("AWS::CloudFront::OriginAccessControl", {"Id": oac.get("id")}),
        ("AWS::CloudFront::Distribution", {"Id": dist.get("id")}),
    ]
    for cfn_type, keys in singles:
        lid = only(cfn_type)
        if lid:
            put(lid, **keys)
    oidc = one("aws_iam_openid_connect_provider", "github", index=0)
    lid = only("AWS::IAM::OIDCProvider")
    if lid:
        if oidc:
            put(lid, Arn=oidc.get("arn"))
        else:
            problems.append(
                f"{lid}: the state has no aws_iam_openid_connect_provider.github[0], so the template must REFERENCE "
                "the provider, not declare it — synthesise with the context this tool writes (pass 1) and run again"
            )

    # --- indexed / named families -----------------------------------------
    for i in range(2):
        for kind, tf_name in (("Public", "public"), ("Private", "private")):
            sn = need("aws_subnet", tf_name, i)
            if sn:
                put(f"{kind}Subnet{i}", SubnetId=sn.get("id"))
            assoc = need("aws_route_table_association", tf_name, i)
            if assoc:
                put(f"{kind}Rta{i}", Id=assoc.get("id"))
        mt = need("aws_efs_mount_target", "data", i)
        if mt:
            put(f"DataMount{i}", Id=mt.get("id"))
    for kind, tf_name in (("Public", "public"), ("Private", "private")):
        rt = need("aws_route_table", tf_name)
        if rt:
            put(f"{kind}RouteTable", RouteTableId=rt.get("id"))
            put(f"{kind}DefaultRoute", RouteTableId=rt.get("id"), CidrBlock="0.0.0.0/0")
    sg_by_logical = {"AlbSg": "alb", "ApiSg": "api", "DbSg": "db", "EfsSg": "efs"}
    for logical, tf_name in sg_by_logical.items():
        sg = need("aws_security_group", tf_name)
        if sg:
            put(logical, Id=sg.get("id"))
    for tf_name in ("task_execution", "api_task", "scheduler", "backup", "claude_observer", "github_deploy"):
        role = need("aws_iam_role", tf_name)
        if role:
            lid = by_property("AWS::IAM::Role", "RoleName", role.get("name"))
            if lid:
                put(lid, RoleName=role.get("name"))
    for tf_name in ("web", "alb_logs"):
        b = need("aws_s3_bucket", tf_name)
        if b:
            lid = by_property("AWS::S3::Bucket", "BucketName", b.get("bucket"))
            if lid:
                put(lid, BucketName=b.get("bucket"))
    for lid, res in resources.items():
        t = res["Type"]
        p = res.get("Properties", {})
        if t == "AWS::S3::BucketPolicy":
            ref = p["Bucket"].get("Ref") if isinstance(p.get("Bucket"), dict) else None
            if ref in mapping:
                bucket_name = mapping[ref]["BucketName"]
                put(lid, Bucket=bucket_name)
                s.consume_by("aws_s3_bucket_policy", "bucket", bucket_name)
            else:
                problems.append(f"{lid}: bucket policy for an unmapped bucket ({ref})")
        elif t == "AWS::CloudWatch::Alarm":
            put(lid, AlarmName=p.get("AlarmName"))
            s.consume_by("aws_cloudwatch_metric_alarm", "alarm_name", p.get("AlarmName"))
        elif t == "AWS::ApplicationAutoScaling::ScalingPolicy":
            tf_name = {"cpu-target": "api_cpu", "memory-target": "api_memory"}.get(p.get("PolicyName"))
            pol = one("aws_appautoscaling_policy", tf_name) if tf_name else None
            if pol:
                put(lid, Arn=pol.get("arn"), ScalableDimension=_SCALABLE_DIMENSION)
            else:
                problems.append(f"{lid}: scaling policy {p.get('PolicyName')!r} not in the state")
        elif t == "AWS::ElasticLoadBalancingV2::Listener":
            # Matched on PORT and ACTION, not on a guessed Terraform name: an
            # 80/redirect and an 80/forward are different resources.
            port = p.get("Port")
            action = (p.get("DefaultActions") or [{}])[0].get("Type")
            wanted = "redirect" if action == "redirect" else "forward"
            found = None
            for name in ("http_redirect", "https", "http_origin"):
                lst = one("aws_lb_listener", name, index=0)
                if lst and lst.get("port") == port and (lst.get("default_action") or [{}])[0].get("type") == wanted:
                    found = lst
                    break
            if found:
                put(lid, ListenerArn=found.get("arn"))
            else:
                problems.append(
                    f"{lid}: no listener on port {port} with a {wanted} action in the state — the template has the "
                    "default TLS shape, not the live one; synthesise with the context this tool writes (pass 1) and run again"
                )

    # --- and every managed address must have a home -------------------------
    # The other direction, and the one that was missing: a live resource that
    # neither the mirror claims nor UNMAPPED_ON_PURPOSE names is a resource
    # that step 6 releases and nothing ever manages again.
    for address in s.unconsumed():
        problems.append(f"{address}: managed by Terraform, not in the mirror, and not named in UNMAPPED_ON_PURPOSE — it would be released and orphaned at step 6")

    # --- every import-phase resource must be mapped -------------------------
    for lid, res in resources.items():
        if res["Type"] in NON_RESOURCE_TYPES:
            continue
        if res["Type"] not in IDENTIFIERS:
            problems.append(f"{lid}: type {res['Type']} is not in IDENTIFIERS — it may not be importable at all")
        elif lid not in mapping:
            problems.append(f"{lid} ({res['Type']}): no mapping rule")

    return mapping, context, problems


def _refuse(problems: list[str]) -> int:
    print("REFUSING — nothing written. Fix these first:")
    for p in problems:
        print("  -", p)
    return 1


def _merge_context(here: Path, context: dict[str, Any]) -> None:
    ctx_path = here / "cdk.context.json"
    existing = json.loads(ctx_path.read_text(encoding="utf-8")) if ctx_path.exists() else {}
    existing.update(context)
    ctx_path.write_text(json.dumps(existing, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    if len(argv) not in (2, 3):
        print(__doc__)
        return 2
    state = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
    here = Path(__file__).resolve().parents[1]

    if len(argv) == 2:  # pass 1: the context, from the state alone
        context, problems = build_context(state)
        if problems:
            return _refuse(problems)
        _merge_context(here, context)
        print(f"context ({len(context)} keys) -> cdk.context.json")
        print("\nNow synthesise the mirror with it, then build the map against that template:")
        print("  cdk synth reep-core -c phase=import --quiet")
        print(f"  python tools/import_map.py {argv[1]} cdk.out/reep-core.template.json")
        return 0

    template = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
    mapping, context, problems = build(state, template)
    if problems:
        return _refuse(problems)
    (here / "import-map.json").write_text(json.dumps(mapping, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _merge_context(here, context)
    print(f"mapped {len(mapping)} resources -> import-map.json")
    print(f"context ({len(context)} keys) -> cdk.context.json")
    waf = next((r["values"] for r in _tf_resources(state) if r["type"] == "aws_wafv2_web_acl"), None)
    if waf:
        print(f"edge WAF identifier for `cdk import reep-edge-waf` (Name|Id|Scope): Name={waf.get('name')} Id={waf.get('id')} Scope=CLOUDFRONT")
    print("\nCross-check the identifier keys against the only authoritative source:")
    print("  aws cloudformation get-template-summary --template-body file://cdk.out/reep-core.template.json --query ResourceIdentifierSummaries")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
