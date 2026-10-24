#!/usr/bin/env python3
"""Reconstruct `infra/aws/prod.tfvars` from the Terraform state — because the
values the first apply was given were typed as `-var` flags and recorded
nowhere (docs/aws-deployment.md §3).

    cd infra/aws  && terraform show -json > ../cdk/tf-state.json
    cd ../cdk     && python tools/tfvars_from_state.py tf-state.json

Step 0 of docs/cdk-cutover.md needs `terraform plan -var-file=prod.tfvars
-detailed-exitcode` to exit 0: no drift, no pending change. Without the values
the stack was applied with, the plan proposes to "fix" the certificate, the
domain and the alert address back to their defaults — and importing a resource
whose Terraform state disagrees with its real state means the CDK mirror is
wrong too. The state does not store variable values, but every variable in
infra/aws/ lands in an attribute of a resource the state does hold, so each
one is read back from there rather than remembered.

WHAT IT REFUSES. Any variable it cannot source (`alert_email` has no default
and no fallback: the SNS subscription must be in the state), and any resource
it needs that the state lacks. Like import_map.py it writes NOTHING until every
check passes. A `prod.tfvars` already on disk is left alone — delete it to
regenerate.

The set of variables written is pinned by tests/test_cutover_tools.py to the
`variable` blocks in infra/aws/*.tf, so a variable added to Terraform without a
rule here fails CI rather than the plan.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))
from import_map import _one, _tf_resources, _trust_policy  # noqa: E402

#: Container environment variable -> Terraform variable, read from the live
#: task definition (ecs.tf's local.api_environment).
_ENV_TO_VAR = {
    "BEDROCK_MODEL": "bedrock_model",
    "LLM_ALLOW_REMOTE_STUDENT_DATA": "allow_remote_student_data",
    "INTERVIEW_RECORDING_ENABLED": "interview_recording_enabled",
    "INTERVIEW_ENGINE": "interview_engine",
    "NOVA_SONIC_REGION": "nova_sonic_region",
    "INTERVIEW_CONSENT_VERSION": "interview_consent_version",
}

_SUB_CLAIM = re.compile(r"^repo:(?P<repo>.+?):ref:(?P<ref>.+)$")


def _sub_claims(role: dict[str, Any]) -> list[str]:
    """Every `repo:<owner/repo>:ref:<ref>` subject the deploy role trusts."""
    subs: list[str] = []
    for st in _trust_policy(role).get("Statement", []):
        cond = (st.get("Condition") or {}).get("StringEquals") or {}
        v = cond.get("token.actions.githubusercontent.com:sub")
        if isinstance(v, str):
            subs.append(v)
        elif isinstance(v, list):
            subs.extend(str(s) for s in v)
    return subs


def derive(state: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
    """Every variable infra/aws/ declares, read from the state. Returns the
    values and the problems; the values mean nothing if there are problems."""
    tf = _tf_resources(state)
    out: dict[str, Any] = {}
    problems: list[str] = []

    def need(tf_type: str, name: str, index: int | None = None) -> dict[str, Any]:
        v = _one(tf, tf_type, name, index)
        if v is None:
            problems.append(f"{tf_type}.{name}{'' if index is None else f'[{index}]'} is not in the state")
            return {}
        return v

    def put(key: str, value: Any) -> None:
        if value is None:
            problems.append(f"{key}: not derivable from the state")
            return
        out[key] = value

    alb = need("aws_lb", "main")
    cluster = need("aws_ecs_cluster", "main")
    taskdef = need("aws_ecs_task_definition", "api")
    target = need("aws_appautoscaling_target", "api")
    db = need("aws_db_instance", "main")
    dist = need("aws_cloudfront_distribution", "main")
    alb_sg = need("aws_security_group", "alb")
    observer = need("aws_iam_role", "claude_observer")
    deploy = need("aws_iam_role", "github_deploy")
    subscription = need("aws_sns_topic_subscription", "email")
    https = _one(tf, "aws_lb_listener", "https", index=0)  # absent when the ALB is plain HTTP
    oidc = _one(tf, "aws_iam_openid_connect_provider", "github", index=0)

    # --- region / project: from an ARN and a name, never from a default -------
    arn_parts = (alb.get("arn") or "").split(":")
    put("region", arn_parts[3] if len(arn_parts) > 3 and arn_parts[3] else None)
    put("project", cluster.get("name") or None)

    # --- edge / domain: the TLS shape the listeners and the distribution have --
    if dist:
        aliases = dist.get("aliases") or []
        put("domain_name", aliases[0] if aliases else "")
        viewer_cert = (dist.get("viewer_certificate") or [{}])[0]
        put("cloudfront_acm_certificate_arn", viewer_cert.get("acm_certificate_arn") or "")
        api_origin = next((o for o in dist.get("origin", []) if o.get("origin_id") == "api-alb"), None)
        if api_origin is None:
            problems.append("aws_cloudfront_distribution.main has no origin 'api-alb'")
        else:
            origin_domain = api_origin.get("domain_name") or ""
            put("alb_origin_domain", "" if origin_domain == alb.get("dns_name") else origin_domain)
    put("alb_acm_certificate_arn", (https or {}).get("certificate_arn") or "")
    put("restrict_alb_to_cloudfront", any(r.get("prefix_list_ids") for r in alb_sg.get("ingress", [])) if alb_sg else None)

    # --- sizing -----------------------------------------------------------------
    put("api_cpu", int(taskdef["cpu"]) if taskdef.get("cpu") else None)
    put("api_memory", int(taskdef["memory"]) if taskdef.get("memory") else None)
    put("api_min_tasks", target.get("min_capacity"))
    put("api_max_tasks", target.get("max_capacity"))
    put("db_instance_class", db.get("instance_class"))
    put("db_multi_az", db.get("multi_az") if db else None)
    put("db_backup_retention_days", db.get("backup_retention_period"))

    # --- application behaviour: the running container's environment --------------
    try:
        cdefs = json.loads(taskdef.get("container_definitions") or "[]")
        env = {e["name"]: e["value"] for e in (cdefs[0].get("environment") or [])}
    except (ValueError, IndexError, KeyError, TypeError):
        env = {}
        problems.append("container_definitions could not be parsed from the state")
    for env_name, var_name in _ENV_TO_VAR.items():
        put(var_name, env.get(env_name))

    # --- alerting / observer --------------------------------------------------
    put("alert_email", subscription.get("endpoint") or None)
    if observer:
        principals = [str((st.get("Principal") or {}).get("AWS") or "") for st in _trust_policy(observer).get("Statement", [])]
        principal = next((p for p in principals if p), "")
        # observability.tf: empty means the account root.
        put("observer_principal_arn", "" if principal.endswith(":root") else principal)

    # --- GitHub OIDC: the trust policy's subject claims ----------------------------
    if deploy:
        claims = [(m.group("repo"), m.group("ref")) for s in _sub_claims(deploy) if (m := _SUB_CLAIM.match(s))]
        plain = [repo for repo, _ in claims if "@" not in repo]
        with_ids = [repo for repo, _ in claims if "@" in repo]
        refs = {ref for _, ref in claims}
        put("github_repository", plain[0] if plain else None)
        put("github_repository_ids", with_ids[0] if with_ids else "")
        if len(refs) > 1:
            problems.append(f"aws_iam_role.github_deploy trusts more than one ref: {sorted(refs)}")
        put("github_deploy_ref", next(iter(refs)) if len(refs) == 1 else None)
    put("create_github_oidc_provider", oidc is not None)

    return out, problems


def render(values: dict[str, Any]) -> str:
    """HCL variable definitions: strings quoted (JSON escaping is valid HCL),
    numbers and booleans bare, sorted so two runs diff cleanly."""
    lines = [
        "# Reconstructed from the Terraform state by infra/cdk/tools/tfvars_from_state.py.",
        "# Gitignored. Delete this file to regenerate it.",
    ]
    for key in sorted(values):
        v = values[key]
        if isinstance(v, bool):
            rendered = "true" if v else "false"
        elif isinstance(v, int):
            rendered = str(v)
        else:
            rendered = json.dumps(str(v))
        lines.append(f"{key} = {rendered}")
    return "\n".join(lines) + "\n"


def output_path() -> Path:
    return Path(__file__).resolve().parents[2] / "aws" / "prod.tfvars"


def main(argv: list[str]) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    to_stdout = "--stdout" in argv
    if len(args) != 1:
        print(__doc__)
        return 2
    state = json.loads(Path(args[0]).read_text(encoding="utf-8"))
    values, problems = derive(state)
    if problems:
        print("REFUSING — nothing written. Fix these first:")
        for p in problems:
            print("  -", p)
        return 1
    text = render(values)
    if to_stdout:
        sys.stdout.write(text)
        return 0
    target = output_path()
    if target.exists():
        print(f"{target} already exists — left alone. Delete it to regenerate from the state.")
        return 0
    target.write_text(text, encoding="utf-8")
    print(f"wrote {target} ({len(values)} variables, every one read from the state)")
    print("Now: terraform plan -var-file=prod.tfvars -detailed-exitcode   # must exit 0")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
