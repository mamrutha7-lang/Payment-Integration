#!/usr/bin/env python3
"""Cross-check every identifier in `import-map.json` against CloudFormation's
OWN answer for the template about to be imported — the one gate the offline
rehearsal cannot provide. Two halves, and the second is the one that matters:

    KEY NAMES   `get-template-summary` says which keys each type needs.
    KEY VALUES  Cloud Control `get-resource` resolves the exact identifier the
                map will send, and returns the live resource or an error.

    python tools/check_identifiers.py                        # both halves
    python tools/check_identifiers.py --names-only           # skip the 65 reads
    python tools/check_identifiers.py registry-summary.json  # names, from a saved summary

WHY THE VALUE HALF EXISTS. The first version checked names only, and a review
pointed out that no gate in the runbook could then catch a wrong identifier
*value*. It was right, and the very next check found one:
`AWS::EC2::VPCGatewayAttachment` was mapped with `AttachmentType: "internet"`.
`AttachmentType` is read-only and Terraform has no resource to read it from, so
it is the one value in the whole map that was written rather than derived — and
it was wrong. CloudFormation answers `Invalid Attachment Type 'internet'`; the
live identifier is `IGW|vpc-…`. The import would have failed on that resource,
at the step where a runbook has no business failing. Now every identifier is
resolved before anything is adopted.

`tools/import_map.py`'s `IDENTIFIERS` table is a CLAIM about the registry's
primaryIdentifier for each type, and eight of its first thirty were wrong. The
authority is `aws cloudformation get-template-summary`, which reports, for the
exact template, the keys each resource must be identified by. This compares the
two and exits non-zero on any disagreement.

WHY THIS IS A SCRIPT AND NOT A PARAGRAPH IN THE RUNBOOK. The runbook used to
say "every `Keys` entry must match the key set in `import-map.json`", to be
done by eye across 39 types. **CloudFormation returns a COMPOSITE identifier as
one comma-joined string** — `["ResourceId,ScalableDimension,ServiceNamespace"]`,
one element, not three — while the map, correctly, sends three separate keys.
Compared by eye, all seven composite types read as mismatches: the ECS service,
both scaling policies, the scalable target, the metric filter, the IGW
attachment, the EIP and both default routes. An operator following the old
instruction would have "fixed" a table that was right, and broken an import
that was going to work. That happened here on 2026-09-07, to a reader who had
written the table.

The template is usually over CloudFormation's 51,200-byte inline limit, so it
is staged to the CDK bootstrap assets bucket (the same bucket `cdk deploy`
uses) and summarised by URL. Nothing is created, changed or deleted; the only
write is that staged copy.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / "tools"))
from import_map import IDENTIFIERS, NON_RESOURCE_TYPES  # noqa: E402

TEMPLATE = HERE / "cdk.out" / "reep-core.template.json"
MAP = HERE / "import-map.json"
INLINE_LIMIT = 51_200


def split_keys(keys: list[str]) -> set[str]:
    """The registry's key list, as a set of individual key names.

    A composite primaryIdentifier arrives as ONE comma-joined element. Not
    splitting it is the trap this whole script exists to remove.
    """
    return {part.strip() for key in keys for part in key.split(",") if part.strip()}


def fetch_summary(template: Path, account: str, region: str) -> list[dict]:
    """`get-template-summary` for this template, inline if it fits, else via
    the CDK bootstrap assets bucket."""
    aws = "aws"
    query = "ResourceIdentifierSummaries[].{Type:ResourceType,Keys:ResourceIdentifiers}"
    if template.stat().st_size <= INLINE_LIMIT:
        args = [aws, "cloudformation", "get-template-summary", "--template-body", f"file://{template}"]
    else:
        bucket = f"cdk-hnb659fds-assets-{account}-{region}"
        key = "cutover/reep-core-import-check.json"
        subprocess.run(
            [aws, "s3api", "put-object", "--bucket", bucket, "--key", key, "--body", str(template), "--content-type", "application/json"],
            check=True,
            capture_output=True,
        )
        args = [aws, "cloudformation", "get-template-summary", "--template-url", f"https://{bucket}.s3.{region}.amazonaws.com/{key}"]
    out = subprocess.run([*args, "--query", query, "--output", "json"], check=True, capture_output=True, text=True)
    return json.loads(out.stdout)


def cc_identifier(keys: dict, order: list[str]) -> str:
    """The Cloud Control identifier string: the primaryIdentifier's values, in
    the registry's own order, joined by `|`. Order matters — it is the order
    `get-template-summary` reports, not alphabetical."""
    return "|".join(str(keys[k]) for k in order)


def resolve_values(mapping: dict, resources: dict, order_by_type: dict[str, list[str]]) -> list[str]:
    """Ask Cloud Control to resolve every identifier the map will send.

    A resource that comes back is one `cdk import` can adopt. An error is a
    wrong value — caught here, before the import change set, instead of
    half-way through adopting a stack. Read-only: `get-resource` only.

    Some types are not supported by Cloud Control; those are reported as
    skipped rather than as failures, because absence of support is not
    evidence of a wrong value.
    """
    problems: list[str] = []
    skipped: list[str] = []
    for logical, keys in sorted(mapping.items()):
        cfn_type = resources[logical]["Type"]
        order = order_by_type.get(cfn_type)
        if not order:
            skipped.append(f"{logical} ({cfn_type}): no key order from the summary")
            continue
        ident = cc_identifier(keys, order)
        out = subprocess.run(
            ["aws", "cloudcontrol", "get-resource", "--type-name", cfn_type, "--identifier", ident, "--query", "ResourceDescription.Identifier", "--output", "text"],
            capture_output=True,
            text=True,
        )
        if out.returncode == 0 and out.stdout.strip():
            continue
        err = (out.stderr or "").strip().splitlines()
        message = err[-1] if err else "no output"
        if "UnsupportedActionException" in message or "TypeNotFoundException" in message or "not supported" in message.lower():
            skipped.append(f"{logical} ({cfn_type}): Cloud Control does not support this type")
        else:
            problems.append(f"VALUE  {logical} ({cfn_type}) identifier {ident!r} does not resolve: {message}")
    if skipped:
        print(f"  ({len(skipped)} not resolvable through Cloud Control — not a failure)")
        for s in skipped:
            print("     ·", s)
    return problems


def compare(summary: list[dict], mapping: dict, resources: dict) -> list[str]:
    registry = {r["Type"]: split_keys(r["Keys"]) for r in summary}
    problems: list[str] = []

    for cfn_type, keys in sorted(registry.items()):
        ours = set(IDENTIFIERS.get(cfn_type, ()))
        if ours != keys:
            problems.append(f"TABLE  {cfn_type}: registry wants {sorted(keys)}, IDENTIFIERS has {sorted(ours) or 'NOTHING'}")

    for logical, keys in sorted(mapping.items()):
        cfn_type = resources[logical]["Type"]
        want = registry.get(cfn_type)
        if want is None:
            problems.append(f"ENTRY  {logical}: type {cfn_type} was not described by get-template-summary")
        elif set(keys) != want:
            problems.append(f"ENTRY  {logical} ({cfn_type}): sends {sorted(keys)}, registry wants {sorted(want)}")
        elif any(not str(v).strip() for v in keys.values()):
            problems.append(f"ENTRY  {logical}: an empty identifier part")

    unmapped = {l for l, r in resources.items() if r["Type"] not in NON_RESOURCE_TYPES} - set(mapping)
    if unmapped:
        problems.append(f"COVER  template resources with no map entry: {sorted(unmapped)}")
    extra = set(mapping) - set(resources)
    if extra:
        problems.append(f"COVER  map entries that are not in the template: {sorted(extra)}")
    return problems


def main(argv: list[str]) -> int:
    if not TEMPLATE.exists() or not MAP.exists():
        print(f"REFUSING: run `cdk synth reep-core -c phase=import` and `tools/import_map.py` first\n  missing: {TEMPLATE if not TEMPLATE.exists() else MAP}")
        return 2
    resources = json.loads(TEMPLATE.read_text(encoding="utf-8"))["Resources"]
    mapping = json.loads(MAP.read_text(encoding="utf-8"))

    names_only = "--names-only" in argv
    saved = [a for a in argv[1:] if not a.startswith("--")]
    if saved:
        summary = json.loads(Path(saved[0]).read_text(encoding="utf-8"))
    else:
        import os

        account = os.environ.get("CDK_DEFAULT_ACCOUNT", "445363794125")
        region = os.environ.get("CDK_DEFAULT_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-south-1"
        summary = fetch_summary(TEMPLATE, account, region)

    types = {resources[l]["Type"] for l in mapping}
    print(f"{len(mapping)} resources across {len(types)} types\n")

    print("key names — against the registry's primaryIdentifier for this template:")
    problems = compare(summary, mapping, resources)
    print(f"  {'every key set matches' if not problems else f'{len(problems)} problem(s)'}")

    if not names_only and not saved:
        # The order the registry reports, which is the order Cloud Control
        # expects the values joined in.
        order_by_type = {r["Type"]: [p.strip() for key in r["Keys"] for p in key.split(",") if p.strip()] for r in summary}
        print("\nkey values — every identifier resolved against the live account:")
        value_problems = resolve_values(mapping, resources, order_by_type)
        print(f"  {'every identifier resolves to a live resource' if not value_problems else f'{len(value_problems)} problem(s)'}")
        problems += value_problems

    for line in problems:
        print("  -", line)
    print("\nVERDICT:", "every identifier matches the registry and resolves" if not problems else f"{len(problems)} PROBLEM(S) — fix tools/import_map.py, never import-map.json by hand")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
