"""The core REEP stack in CDK — what infra/aws/ has been in Terraform.

Three stacks, because a CloudFormation stack lives in one region and this
footprint does not:

    CoreStack      ap-south-1      VPC, ALB, ECS, RDS, EFS, S3, CloudFront, backup, alarms, IAM
    EdgeWafStack   us-east-1       the CloudFront-scope WAF (must be us-east-1)
    DrVaultStack   ap-southeast-1  the cross-region backup copy target

See stack.py's module docstring for the two phases (`import` and `harden`)
and docs/cdk-cutover.md for the order in which a human runs them.
"""

from .dr import DrVaultStack
from .edge import EdgeWafStack
from .stack import DEREGISTRATION_DELAY_SECONDS, STOP_TIMEOUT_SECONDS, CoreStack

__all__ = [
    "CoreStack",
    "DrVaultStack",
    "EdgeWafStack",
    "DEREGISTRATION_DELAY_SECONDS",
    "STOP_TIMEOUT_SECONDS",
]
