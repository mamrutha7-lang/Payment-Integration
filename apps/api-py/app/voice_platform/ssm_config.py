"""Load the platform's PLATFORM_* settings from SSM Parameter Store at boot.

WHY. The api's task definition — and so its environment — is owned by the
Terraform stack in infra/aws. The voice platform's resources are created by the
CDK stack in infra/cdk, which knows the queue URLs, bucket names, table names
and the queue URLs only once it has deployed them. Putting those into
the task definition would mean a `terraform apply` for every platform change,
which is exactly the two-tools-for-one-door problem AGENTS.md warns about. So
the CDK stack publishes them as SSM parameters under
`/<project>/voice-platform/PLATFORM_*`, grants the task role permission to read
that path, and the api reads them here at startup. CDK alone then owns the
platform end to end; Terraform is not involved.

RULES OF THE LOAD. Environment wins: a PLATFORM_* variable that is already set
is never overwritten, so a laptop `.env` or an explicit task-definition value
stays authoritative. Only blank settings are filled. Anything that goes wrong —
no credentials, no permission, no such path, a network timeout — is logged at
INFO and leaves every setting exactly as it was; the platform then runs with
its honest no-op fallbacks and GET /api/platform/admin/status says so. Boot is
never blocked: the client is built with a two-second connect timeout and a
single attempt.

ONLY ON AWS. `should_attempt()` is true when ENV is a production name or when
PLATFORM_SSM_PREFIX is set explicitly. Development and CI never call AWS.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from ..config import settings

log = logging.getLogger("app.voice_platform.ssm_config")

#: Settings fields that the CDK stack may publish, keyed by the parameter's
#: leaf name. Anything else under the path is ignored, not applied.
LOADABLE: dict[str, str] = {
    "PLATFORM_AWS_REGION": "platform_aws_region",
    "PLATFORM_UG_QUEUE_URL": "platform_ug_queue_url",
    "PLATFORM_PG_QUEUE_URL": "platform_pg_queue_url",
    "PLATFORM_BULK_UPLOAD_BUCKET": "platform_bulk_upload_bucket",
    "PLATFORM_RECORDINGS_BUCKET": "platform_recordings_bucket",
    "PLATFORM_RECORDINGS_PREFIX": "platform_recordings_prefix",
    "PLATFORM_DYNAMO_UG_TABLE": "platform_dynamo_ug_table",
    "PLATFORM_DYNAMO_PG_TABLE": "platform_dynamo_pg_table",
    "PLATFORM_CLOUDWATCH_LOG_GROUP": "platform_cloudwatch_log_group",
    "PLATFORM_CLOUDWATCH_NAMESPACE": "platform_cloudwatch_namespace",
}

#: Where the load came from, for the admin status screen: "" until attempted,
#: then "env" (nothing needed), "ssm:<path>" or "unavailable: <why>".
_source: str = ""


def source() -> str:
    return _source


def parameter_path() -> str:
    """`/reep/voice-platform` by default; PLATFORM_SSM_PREFIX overrides it."""
    explicit = os.environ.get("PLATFORM_SSM_PREFIX", "").strip()
    return ("/" + explicit.strip("/")) if explicit else "/reep/voice-platform"


def should_attempt() -> bool:
    if os.environ.get("PLATFORM_SSM_PREFIX", "").strip():
        return True
    return settings.is_prod


def _blank(name: str) -> bool:
    return not str(getattr(settings, name, "") or "").strip()


def apply_parameters(parameters: dict[str, str]) -> list[str]:
    """Fill blank settings from `{leaf name: value}`. Returns the fields set.
    Pure apart from the settings mutation; the boot path and the tests both
    call it."""
    applied: list[str] = []
    for leaf, value in parameters.items():
        field = LOADABLE.get(leaf)
        if field is None or not str(value).strip():
            continue
        if not _blank(field):
            continue
        try:
            setattr(settings, field, str(value).strip())
        except Exception:  # noqa: BLE001 - a frozen model still gets the value
            object.__setattr__(settings, field, str(value).strip())
        applied.append(field)
    return applied


def fetch_parameters(client: Any, path: str) -> dict[str, str]:
    """Every parameter under `path`, by leaf name. Paginates."""
    out: dict[str, str] = {}
    token: str | None = None
    while True:
        kwargs: dict[str, Any] = {"Path": path, "Recursive": False, "WithDecryption": True}
        if token:
            kwargs["NextToken"] = token
        page = client.get_parameters_by_path(**kwargs)
        for item in page.get("Parameters", []):
            out[str(item["Name"]).rsplit("/", 1)[-1]] = str(item.get("Value", ""))
        token = page.get("NextToken")
        if not token:
            return out


def load(client: Any | None = None) -> list[str]:
    """The boot-time entry point. Never raises."""
    global _source
    if not should_attempt():
        _source = "env"
        return []
    if not any(_blank(f) for f in LOADABLE.values()):
        _source = "env"
        return []
    path = parameter_path()
    try:
        if client is None:
            import boto3
            from botocore.config import Config

            region = settings.platform_region or None
            client = boto3.client(
                "ssm",
                region_name=region,
                config=Config(connect_timeout=2, read_timeout=5, retries={"max_attempts": 1}),
            )
        parameters = fetch_parameters(client, path)
    except Exception as exc:  # noqa: BLE001 - boot is never blocked by this
        _source = f"unavailable: {type(exc).__name__}"
        log.info("Voice platform: no settings loaded from SSM %s (%s); using the environment only", path, exc)
        return []
    applied = apply_parameters(parameters)
    if applied:
        _source = f"ssm:{path}"
        log.info("Voice platform: %d setting(s) loaded from SSM %s: %s", len(applied), path, ", ".join(applied))
    else:
        _source = f"ssm:{path} (nothing to apply)"
        log.info("Voice platform: SSM %s has %d parameter(s); nothing blank to fill", path, len(parameters))
    return applied
