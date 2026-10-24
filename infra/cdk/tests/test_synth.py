"""The stack synthesises, and the template has the shape the api expects."""

from __future__ import annotations

import aws_cdk as cdk
from aws_cdk.assertions import Match, Template

from reep_voice_platform import VoicePlatformStack


def _template() -> Template:
    app = cdk.App()
    stack = VoicePlatformStack(
        app,
        "test-voice-platform",
        project="reep",
        recording_retention_days=90,
        env=cdk.Environment(account="123456789012", region="ap-south-1"),
    )
    return Template.from_stack(stack)


def test_two_streams_each_with_a_dead_letter_queue() -> None:
    t = _template()
    t.resource_count_is("AWS::SQS::Queue", 4)
    for degree in ("ug", "pg"):
        t.has_resource_properties(
            "AWS::SQS::Queue",
            {
                "QueueName": f"reep-voice-candidates-{degree}",
                "VisibilityTimeout": 60,
                "RedrivePolicy": Match.object_like({"maxReceiveCount": 5}),
            },
        )


def test_two_session_tables_keyed_on_session_id_with_ttl() -> None:
    t = _template()
    t.resource_count_is("AWS::DynamoDB::Table", 2)
    t.has_resource_properties(
        "AWS::DynamoDB::Table",
        {
            "TableName": "reep-voice-sessions-ug",
            "KeySchema": [{"AttributeName": "session_id", "KeyType": "HASH"}],
            "BillingMode": "PAY_PER_REQUEST",
            "TimeToLiveSpecification": {"AttributeName": "expires_at", "Enabled": True},
        },
    )


def test_recordings_bucket_is_private_encrypted_and_expires_on_a_clock() -> None:
    t = _template()
    t.resource_count_is("AWS::S3::Bucket", 2)
    t.has_resource_properties(
        "AWS::S3::Bucket",
        {
            "PublicAccessBlockConfiguration": Match.object_like({"BlockPublicAcls": True, "RestrictPublicBuckets": True}),
            "LifecycleConfiguration": {
                "Rules": [Match.object_like({"Prefix": "recordings/", "ExpirationInDays": 90, "Status": "Enabled"})]
            },
        },
    )


def _bucket(t: Template, logical_prefix: str) -> dict:
    """One bucket's properties, found by LOGICAL ID.

    The previous version of this helper told the two buckets apart by which one
    carried a `LifecycleConfiguration`, which stopped working the moment
    `recordings` lost its expiry: both assertions then ran against `uploads`
    and one of them still passed. A template-shape heuristic that identifies a
    resource by a property under test is a test that quietly changes subject.
    """
    matches = [
        body["Properties"]
        for logical_id, body in t.find_resources("AWS::S3::Bucket").items()
        if logical_id.startswith(logical_prefix)
    ]
    assert len(matches) == 1, f"expected exactly one {logical_prefix}* bucket, got {len(matches)}"
    return matches[0]


def test_both_buckets_are_versioned_because_neither_is_in_a_backup_plan() -> None:
    """Versioning is the only thing standing behind either bucket's contents.

    Neither is in any backup plan -- the core stack's daily selection covers
    the database and EFS, and nothing in S3 -- so an overwritten candidate
    roster in `uploads` had no second copy anywhere.

    `recordings` NOW FOLLOWS, AND THAT REVERSES WHAT THIS TEST USED TO ASSERT.
    The old version pinned the asymmetry, on the grounds that a non-current
    version outlives a lifecycle delete marker and would therefore keep student
    voice past the retention the student consented to. That was right while the
    consented clock was the governing promise. The college's rule is now that
    nothing in storage is deleted, so the clock is not the governing promise
    and versioning is what makes the new one true -- `RecordingStore.delete`
    issues a real `delete_object`, which on a versioned bucket removes the
    recording from every view while keeping the bytes.

    The obligation that comes with it is recorded on the bucket and repeated
    here because it is a product change, not an infra one: the consent panel
    still tells students their recording is destroyed after the policy's
    `retention_days`, and that copy is now inaccurate.
    """
    assert _bucket(_template(), "Uploads")["VersioningConfiguration"] == {"Status": "Enabled"}
    assert _bucket(_template(), "Recordings")["VersioningConfiguration"] == {"Status": "Enabled"}


def test_a_retention_of_zero_leaves_no_lifecycle_rule_at_all() -> None:
    """The deployment default, and the identity ledger's deliberate absence for
    the same reason: this is a copy meant to outlive every bounded tier, and a
    rule it acquires by accident is that promise quietly expiring in the one
    direction S3 never reports."""
    app = cdk.App()
    stack = VoicePlatformStack(
        app,
        "test-voice-platform-forever",
        project="reep",
        recording_retention_days=0,
        env=cdk.Environment(account="123456789012", region="ap-south-1"),
    )
    assert "LifecycleConfiguration" not in _bucket(Template.from_stack(stack), "Recordings")


def test_a_deployment_that_keeps_the_clock_must_expire_both_versions() -> None:
    """If a deployment does turn the clock back on, it has to mean it. On a
    versioned bucket an expiration writes a delete MARKER and leaves the
    version stored and billed, so a rule without `NoncurrentVersionExpiration`
    frees nothing while looking right in the console -- the same half-rule the
    core stack's daily dump bucket guards against."""
    rules = _bucket(_template(), "Recordings")["LifecycleConfiguration"]["Rules"]
    assert len(rules) == 1
    assert rules[0]["ExpirationInDays"] == 90
    assert rules[0]["NoncurrentVersionExpiration"]["NoncurrentDays"] == 90


def test_the_lambda_is_the_queue_package_with_both_queue_urls() -> None:
    t = _template()
    t.has_resource_properties(
        "AWS::Lambda::Function",
        {
            "FunctionName": "reep-voice-candidate-ingest",
            "Handler": "lambda_handler.handler",
            "Runtime": "python3.12",
            "Environment": {
                "Variables": Match.object_like(
                    {"PLATFORM_UG_QUEUE_URL": Match.any_value(), "PLATFORM_PG_QUEUE_URL": Match.any_value(), "PLATFORM_REJECTS_PREFIX": "rejects/"}
                )
            },
        },
    )
    # The S3 notification is wired through the custom resource CDK emits.
    t.resource_count_is("Custom::S3BucketNotifications", 1)


def test_no_opensearch_collection_comes_back() -> None:
    """The collection was REMOVED in 2026-09 and must not return by accident.

    It cost $361/month — 73% of the account's entire bill — and nothing read it.
    `OpenSearchIndex.search` and `.knn` had zero callers, and the session-log
    writer passed raw datetimes into `json.dumps`, so every write raised
    TypeError, was swallowed by "a projection never fails the call", and left
    `opensearch_synced` false for the life of the feature.

    This is a cost guard, not a style guard: an AOSS collection bills a minimum
    capacity floor whether or not a single document is ever indexed, so one
    re-added construct is $361/month with no error message anywhere.
    """
    t = _template()
    for kind in (
        "AWS::OpenSearchServerless::Collection",
        "AWS::OpenSearchServerless::SecurityPolicy",
        "AWS::OpenSearchServerless::AccessPolicy",
    ):
        t.resource_count_is(kind, 0)


def test_the_api_task_role_is_imported_and_granted_not_redefined() -> None:
    t = _template()
    # No role of the api's own is created here: the policy attaches to the
    # Terraform-owned role by name.
    roles = t.find_resources("AWS::IAM::Role")
    for props in roles.values():
        principal = props["Properties"]["AssumeRolePolicyDocument"]["Statement"][0]["Principal"]
        assert principal == {"Service": "lambda.amazonaws.com"}, principal
        assert props["Properties"].get("RoleName") != "reep-api-task"
    t.has_resource_properties(
        "AWS::IAM::Policy",
        {"PolicyName": "voice-platform", "Roles": ["reep-api-task"]},
    )
    t.has_resource_properties(
        "AWS::IAM::Policy",
        {"PolicyName": "deploy-cdk-stacks", "Roles": ["reep-github-deploy"]},
    )


def test_the_api_may_read_its_settings_path_and_the_deploy_role_the_bootstrap_roles() -> None:
    t = _template()
    policies = t.find_resources("AWS::IAM::Policy")
    by_name = {p["Properties"]["PolicyName"]: p["Properties"]["PolicyDocument"]["Statement"] for p in policies.values()}
    api = by_name["voice-platform"]
    ssm_stmt = next(s for s in api if "ssm:GetParametersByPath" in s["Action"])
    assert any("reep/voice-platform" in str(r) for r in ssm_stmt["Resource"])
    deploy = by_name["deploy-cdk-stacks"]
    assume = next(s for s in deploy if s["Action"] == "sts:AssumeRole")
    assert "cdk-hnb659fds-*" in str(assume["Resource"])


def test_every_platform_setting_is_published_to_ssm() -> None:
    t = _template()
    names = {
        p["Properties"]["Name"] for p in t.find_resources("AWS::SSM::Parameter").values()
    }
    assert names == {
        f"/reep/voice-platform/{k}"
        for k in (
            "PLATFORM_AWS_REGION", "PLATFORM_UG_QUEUE_URL", "PLATFORM_PG_QUEUE_URL",
            "PLATFORM_BULK_UPLOAD_BUCKET", "PLATFORM_RECORDINGS_BUCKET", "PLATFORM_DYNAMO_UG_TABLE",
            "PLATFORM_DYNAMO_PG_TABLE", "PLATFORM_CLOUDWATCH_NAMESPACE",
        )
    }


def test_zero_is_a_value_and_not_an_absence_in_the_context_reader() -> None:
    """`infra/cdk/app.py`'s `_number`, and the bug it was extracted to fix.

    `int(try_get_context(key) or default)` is the obvious spelling and it is
    WRONG for every knob whose "off" is 0. cdk.json stores JSON integers, so
    `recordingRetentionDays: 0` arrives as the int `0`, which is falsy -- `or`
    then replaced the operator's explicit "never expire" with 180 and the stack
    synthesised the very lifecycle rule they had turned off. Valid template,
    successful deploy, student voice deleted on a clock somebody believed was
    disabled.

    IT WAS INVISIBLE FROM EVERY OTHER TEST IN THIS FILE, which is why it is
    tested here by name. `_template()` above constructs the stack directly with
    `recording_retention_days=90`, and the zero case constructs it with `0` --
    neither goes anywhere near app.py, so the whole suite was green while the
    deployed value was silently 180.
    """
    import importlib.util
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    # Execute just the helper, not the module: importing app.py would build the
    # whole App and synthesise three stacks as a side effect.
    start = src.index("def _number(")
    end = src.index("retention = _number(")
    namespace: dict = {}
    body = src[start:end].replace("app.node.try_get_context(key)", "_ctx.get(key)")
    exec("def _mk(_ctx):\n" + "\n".join("    " + line for line in body.splitlines()) + "\n    return _number", namespace)
    make = namespace["_mk"]

    assert make({"recordingRetentionDays": 0})("recordingRetentionDays", 180) == 0, (
        "a JSON 0 in cdk.json must survive as 0 -- `or default` turns the "
        "operator's explicit 'never expire' back into an expiry"
    )
    assert make({"recordingRetentionDays": 90})("recordingRetentionDays", 180) == 90
    assert make({})("recordingRetentionDays", 180) == 180
    assert make({"recordingRetentionDays": ""})("recordingRetentionDays", 180) == 180
    assert make({"recordingRetentionDays": "0"})("recordingRetentionDays", 180) == 0


def test_the_committed_context_really_switches_the_expiry_off() -> None:
    """The end-to-end version of the test above: read cdk.json as deployed and
    confirm the value the stack would be handed leaves no lifecycle rule.

    Asserting the helper alone would not have caught the original bug either,
    because the bug was the CALL SITE's `or`. This reads the committed number
    and drives the real stack with it.
    """
    import json
    import pathlib

    ctx = json.loads(
        (pathlib.Path(__file__).resolve().parents[1] / "cdk.json").read_text(encoding="utf-8")
    )["context"]
    days = ctx.get("recordingRetentionDays")
    assert days == 0, f"cdk.json says recordingRetentionDays={days}; this test pins the 'off' choice"

    app = cdk.App()
    stack = VoicePlatformStack(
        app,
        "test-voice-platform-committed",
        project="reep",
        recording_retention_days=days,
        env=cdk.Environment(account="123456789012", region="ap-south-1"),
    )
    assert "LifecycleConfiguration" not in _bucket(Template.from_stack(stack), "Recordings")
