"""The cross-region backup copy target, in ap-southeast-1 (Singapore).

A backup in the same region as the database survives a bad deploy and a
fat-fingered delete; it does not survive the region. This vault is where the
core stack's daily plan COPIES every recovery point (see CoreStack's
`copy_actions`). It is locked with the same minimum retention, so the copy
cannot be quietly shortened either.

Why Singapore: the nearest region to Mumbai that is not Mumbai, with Postgres
17 and AWS Backup cross-region copy both available. Latency does not matter
for a copy target; existence does.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import CfnOutput, RemovalPolicy, Stack, Tags, aws_backup as backup
from constructs import Construct


class DrVaultStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        project: str = "reep",
        min_retention_days: int = 35,
        compliance_lock: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        Tags.of(self).add("Project", project)
        Tags.of(self).add("ManagedBy", "cdk")

        lock_kwargs: dict[str, Any] = {"min_retention_days": min_retention_days}
        if compliance_lock:
            lock_kwargs["changeable_for_days"] = 3
        vault = backup.CfnBackupVault(
            self,
            "DrVault",
            backup_vault_name=f"{project}-vault-dr",
            lock_configuration=backup.CfnBackupVault.LockConfigurationTypeProperty(**lock_kwargs),
        )
        vault.apply_removal_policy(RemovalPolicy.RETAIN)
        self.vault = vault
        CfnOutput(self, "DrVaultArn", value=vault.attr_backup_vault_arn, description="Pass to the core stack as -c drVaultArn=…")
