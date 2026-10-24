"""What `reep-github-drift` may do: answer `detect-stack-drift`, and nothing else.

WHY A SECOND ROLE. `.github/workflows/infra-drift.yml` asks two questions every
morning. `cdk diff` (repo -> AWS) runs as `reep-github-deploy`. The other one,
`detect-stack-drift` (AWS -> its own template), never answered: CloudFormation
reads every live resource WITH THE CALLER'S CREDENTIALS, so the check needs read
access to every resource type in the stack, and the deploy role was refused that
grant on purpose (see the comment above `ReadGrossSpend` in stack.py, and
`test_the_deploy_role_still_has_no_codedeploy_cloudformation_or_rds`). The role
that ships code must not also be the role that can read the whole account. So
the read lives here, on a role that can do nothing else.

THE ALLOW LIST IS CLOUDFORMATION'S OWN, NOT A GUESS. `READ_PERMISSIONS` is each
resource type's `handlers.read.permissions` from the CloudFormation resource
schema (the registry's `CloudformationSchema.zip`, ap-south-1, read 2026-10-16).
That is the exact set of calls CloudFormation makes to read a resource of that
type, which is what drift detection does. AWS's managed `ReadOnlyAccess` was the
obvious alternative and it reads DATA: `s3:GetObject` on the dump, ledger and
document-archive buckets, `dynamodb:Scan` on the platform tables. Those buckets
hold a complete copy of every student record, and the backup tasks themselves
are forbidden to read them (AGENTS.md, "the task may write the dumps and must
never read them"). A drift checker gets no further than the dump task does.

`s3:ListBucket` IS ON THE LIST, AND THAT IS THE ONE DECISION HERE. S3's read
handler needs it (it is the permission behind `HeadBucket`), and it also lists
object KEYS. Every key in the sensitive buckets is a date or a `uuid4().hex`
(`export_identity.object_key`, `backup_database.daily_key`,
`document_archive.key_for`), so a key names no person. The object bodies are
refused outright by `DATA_READS_DENIED`.

A TYPE MISSING FROM THIS TABLE IS A CHECK THAT GOES BLANK, NOT A CHECK THAT
FAILS LOUDLY. CloudFormation answers DETECTION_FAILED for a resource it cannot
read, and the workflow files that as "could not run". So
`test_every_resource_type_the_drift_check_reads_is_classified` refuses a
template holding a type this table has not named: add a resource type to
`reep-core` or `reep-voice-platform` and you are made to add its read
permissions here, from the schema, in the same change.
"""

from __future__ import annotations

#: The stacks infra-drift.yml drift-checks, by the suffix after the project
#: prefix. The edge and DR stacks live in other regions and are only diffed.
DRIFT_CHECKED_STACKS: tuple[str, ...] = ("core", "voice-platform")

#: CloudFormation's own calls. The three that take a stack ARN are scoped to
#: the two stacks above; the other two accept no resource at all.
CLOUDFORMATION_STACK_ACTIONS: tuple[str, ...] = (
    "cloudformation:DetectStackDrift",
    "cloudformation:DetectStackResourceDrift",
    "cloudformation:DescribeStackResourceDrifts",
)
CLOUDFORMATION_UNSCOPED_ACTIONS: tuple[str, ...] = (
    "cloudformation:BatchDescribeTypeConfigurations",
    "cloudformation:DescribeStackDriftDetectionStatus",
)

#: Each resource type in reep-core and reep-voice-platform, mapped to the read
#: handler's permissions from its CloudFormation schema. An empty tuple is a
#: type CloudFormation does not drift-check.
READ_PERMISSIONS: dict[str, tuple[str, ...]] = {
    "AWS::ApplicationAutoScaling::ScalableTarget": (
        "application-autoscaling:DescribeScalableTargets",
        "application-autoscaling:DescribeScheduledActions",
        "application-autoscaling:ListTagsForResource",
    ),
    "AWS::ApplicationAutoScaling::ScalingPolicy": ("application-autoscaling:DescribeScalingPolicies",),
    "AWS::Backup::BackupPlan": ("backup:GetBackupPlan", "backup:ListTags"),
    "AWS::Backup::BackupSelection": ("backup:GetBackupSelection",),
    "AWS::Backup::BackupVault": (
        "backup:DescribeBackupVault",
        "backup:GetBackupVaultAccessPolicy",
        "backup:GetBackupVaultNotifications",
        "backup:ListTags",
    ),
    "AWS::Backup::RestoreTestingPlan": ("backup:GetRestoreTestingPlan", "backup:ListTags"),
    "AWS::Backup::RestoreTestingSelection": ("backup:GetRestoreTestingSelection",),
    "AWS::CloudFront::Distribution": (
        "cloudfront:GetDistribution",
        "cloudfront:GetDistributionConfig",
        "cloudfront:ListTagsForResource",
    ),
    "AWS::CloudFront::Function": (
        "cloudfront:DescribeFunction",
        "cloudfront:GetFunction",
        "cloudfront:ListTagsForResource",
    ),
    "AWS::CloudFront::OriginAccessControl": ("cloudfront:GetOriginAccessControl",),
    "AWS::CloudWatch::Alarm": ("cloudwatch:DescribeAlarms", "cloudwatch:ListTagsForResource"),
    "AWS::DynamoDB::Table": (
        "dynamodb:DescribeContinuousBackups",
        "dynamodb:DescribeContributorInsights",
        "dynamodb:DescribeKinesisStreamingDestination",
        "dynamodb:DescribeTable",
        "dynamodb:DescribeTimeToLive",
        "dynamodb:GetResourcePolicy",
        "dynamodb:ListTagsOfResource",
    ),
    "AWS::EC2::EIP": ("ec2:DescribeAddresses",),
    "AWS::EC2::Instance": (
        "ec2:DescribeInstanceAttribute",
        "ec2:DescribeInstanceCreditSpecifications",
        "ec2:DescribeInstances",
        "ec2:DescribeLaunchTemplates",
        "ec2:DescribeNetworkInterfaces",
        "ec2:DescribeVolumes",
        "ssm:DescribeAssociation",
        "ssm:ListAssociations",
    ),
    "AWS::EC2::InternetGateway": ("ec2:DescribeInternetGateways",),
    "AWS::EC2::NatGateway": ("ec2:DescribeNatGateways",),
    "AWS::EC2::Route": ("ec2:DescribeRouteTables",),
    "AWS::EC2::RouteTable": ("ec2:DescribeRouteTables",),
    "AWS::EC2::SecurityGroup": ("ec2:DescribeSecurityGroups",),
    "AWS::EC2::Subnet": ("ec2:DescribeNetworkAcls", "ec2:DescribeSubnets"),
    "AWS::EC2::SubnetRouteTableAssociation": ("ec2:DescribeRouteTables",),
    "AWS::EC2::VPC": (
        "ec2:DescribeNetworkAcls",
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeVpcAttribute",
        "ec2:DescribeVpcEncryptionControls",
        "ec2:DescribeVpcs",
    ),
    "AWS::EC2::VPCGatewayAttachment": ("ec2:DescribeInternetGateways", "ec2:DescribeVpnGateways"),
    "AWS::ECR::Repository": (
        "ecr:DescribeRepositories",
        "ecr:GetLifecyclePolicy",
        "ecr:GetRepositoryPolicy",
        "ecr:ListTagsForResource",
    ),
    "AWS::ECS::Cluster": ("ecs:DescribeClusters", "kms:DescribeKey"),
    "AWS::ECS::Service": ("ecs:DescribeServices",),
    "AWS::ECS::TaskDefinition": ("ecs:DescribeTaskDefinition",),
    "AWS::EFS::AccessPoint": ("elasticfilesystem:DescribeAccessPoints",),
    "AWS::EFS::FileSystem": (
        "elasticfilesystem:DescribeBackupPolicy",
        "elasticfilesystem:DescribeFileSystemPolicy",
        "elasticfilesystem:DescribeFileSystems",
        "elasticfilesystem:DescribeLifecycleConfiguration",
        "elasticfilesystem:DescribeReplicationConfigurations",
    ),
    "AWS::EFS::MountTarget": (
        "elasticfilesystem:DescribeMountTargetSecurityGroups",
        "elasticfilesystem:DescribeMountTargets",
    ),
    "AWS::ElasticLoadBalancingV2::Listener": (
        "elasticloadbalancing:DescribeListenerAttributes",
        "elasticloadbalancing:DescribeListeners",
        "elasticloadbalancing:DescribeTags",
    ),
    "AWS::ElasticLoadBalancingV2::ListenerRule": (
        "elasticloadbalancing:DescribeRules",
        "elasticloadbalancing:DescribeTags",
    ),
    "AWS::ElasticLoadBalancingV2::LoadBalancer": (
        "elasticloadbalancing:DescribeCapacityReservation",
        "elasticloadbalancing:DescribeLoadBalancerAttributes",
        "elasticloadbalancing:DescribeLoadBalancers",
        "elasticloadbalancing:DescribeTags",
    ),
    "AWS::ElasticLoadBalancingV2::TargetGroup": (
        "elasticloadbalancing:DescribeTags",
        "elasticloadbalancing:DescribeTargetGroupAttributes",
        "elasticloadbalancing:DescribeTargetGroups",
        "elasticloadbalancing:DescribeTargetHealth",
    ),
    "AWS::IAM::InstanceProfile": ("iam:GetInstanceProfile",),
    "AWS::IAM::OIDCProvider": ("iam:GetOpenIDConnectProvider",),
    # A legacy type with no registry read handler. CloudFormation drift-checks
    # it by reading the inline policy off the role it is attached to.
    "AWS::IAM::Policy": ("iam:GetRolePolicy",),
    "AWS::IAM::Role": (
        "iam:GetRole",
        "iam:GetRolePolicy",
        "iam:ListAttachedRolePolicies",
        "iam:ListRolePolicies",
    ),
    # The schema also lists kms:Decrypt, for a function whose environment is
    # encrypted with a customer key. None here is, and kms:Decrypt is denied
    # below, so it is deliberately left out rather than allowed and denied.
    "AWS::Lambda::Function": (
        "lambda:GetFunction",
        "lambda:GetFunctionCodeSigningConfig",
        "lambda:GetFunctionRecursionConfig",
        "lambda:GetFunctionScalingConfig",
        "lambda:GetRuntimeManagementConfig",
    ),
    "AWS::Lambda::Permission": ("lambda:GetPolicy",),
    "AWS::Logs::LogGroup": (
        "logs:DescribeIndexPolicies",
        "logs:DescribeLogGroups",
        "logs:DescribeResourcePolicies",
        "logs:GetDataProtectionPolicy",
        "logs:ListTagsForResource",
    ),
    "AWS::Logs::MetricFilter": ("logs:DescribeMetricFilters",),
    "AWS::RDS::DBInstance": (
        "ec2:DescribeAccountAttributes",
        "ec2:DescribeAvailabilityZones",
        "ec2:DescribeInternetGateways",
        "ec2:DescribeSecurityGroups",
        "ec2:DescribeSubnets",
        "ec2:DescribeVpcAttribute",
        "ec2:DescribeVpcs",
        "rds:DescribeDBInstances",
    ),
    "AWS::RDS::DBSubnetGroup": ("rds:DescribeDBSubnetGroups", "rds:ListTagsForResource"),
    "AWS::S3::Bucket": (
        "s3:GetAccelerateConfiguration",
        "s3:GetAnalyticsConfiguration",
        "s3:GetBucketAbac",
        "s3:GetBucketCORS",
        "s3:GetBucketLogging",
        "s3:GetBucketMetadataTableConfiguration",
        "s3:GetBucketNotification",
        "s3:GetBucketObjectLockConfiguration",
        "s3:GetBucketOwnershipControls",
        "s3:GetBucketPublicAccessBlock",
        "s3:GetBucketTagging",
        "s3:GetBucketVersioning",
        "s3:GetBucketWebsite",
        "s3:GetEncryptionConfiguration",
        "s3:GetIntelligentTieringConfiguration",
        "s3:GetInventoryConfiguration",
        "s3:GetLifecycleConfiguration",
        "s3:GetMetricsConfiguration",
        "s3:GetReplicationConfiguration",
        "s3:ListBucket",
        "s3:ListTagsForResource",
    ),
    "AWS::S3::BucketPolicy": ("s3:GetBucketPolicy",),
    "AWS::Scheduler::Schedule": ("scheduler:GetSchedule",),
    "AWS::SES::ConfigurationSet": ("ses:DescribeConfigurationSet", "ses:GetConfigurationSet"),
    "AWS::SES::ConfigurationSetEventDestination": (
        "ses:DescribeConfigurationSet",
        "ses:GetConfigurationSetEventDestinations",
    ),
    "AWS::SES::EmailIdentity": ("ses:GetEmailIdentity", "ses:ListTagsForResource"),
    "AWS::SNS::Subscription": ("sns:GetSubscriptionAttributes",),
    "AWS::SNS::Topic": (
        "sns:GetDataProtectionPolicy",
        "sns:GetTopicAttributes",
        "sns:ListSubscriptionsByTopic",
        "sns:ListTagsForResource",
    ),
    "AWS::SQS::Queue": ("sqs:GetQueueAttributes", "sqs:ListQueueTags"),
    "AWS::SSM::Parameter": ("ssm:GetParameters", "ssm:ListTagsForResource"),
    # Custom resources are never drift-checked.
    "Custom::S3BucketNotifications": (),
}

#: The reads that would reach a student's record rather than a resource's
#: configuration. Nothing above needs any of them; the explicit Deny is what
#: keeps that true if somebody later attaches a managed policy to this role.
DATA_READS_DENIED: tuple[str, ...] = (
    "s3:GetObject*",
    "dynamodb:BatchGetItem",
    "dynamodb:GetItem",
    "dynamodb:Query",
    "dynamodb:Scan",
    "secretsmanager:GetSecretValue",
    "kms:Decrypt",
    "ssm:GetParameter",
    "ssm:GetParametersByPath",
    "logs:FilterLogEvents",
    "logs:GetLogEvents",
    "logs:StartQuery",
    "sqs:ReceiveMessage",
    "ecr:BatchGetImage",
    "ecr:GetDownloadUrlForLayer",
    "rds:DownloadDBLogFilePortion",
)


def read_actions() -> list[str]:
    """Every read permission in the table, once each, sorted."""
    return sorted({action for actions in READ_PERMISSIONS.values() for action in actions})
