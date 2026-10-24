"""The voice-assistant platform's AWS resources, as one CDK stack.

    S3 uploads bucket  -> Lambda (validate) -> SQS ug / SQS pg (+ DLQs)
    S3 recordings bucket <- the api's call-close handler (dual-channel WAV/MP3)
    DynamoDB ug / pg tables <- realtime session state
    OpenSearch Serverless (VECTORSEARCH) <- session logs + question vectors
    IAM: one policy attached to the EXISTING api task role
    SSM: /<project>/voice-platform/PLATFORM_* — the api's environment

WHAT THIS STACK DOES NOT OWN. The VPC, the ALB, the ECS service, the database
and the api task role are the Terraform stack in infra/aws/ and stay there —
they are live and this stack is additive. It imports the task role BY NAME
(`<project>-api-task`, ecs.tf) and attaches a policy; it never redefines the
role. The PLATFORM_* values reach the api through SSM Parameter Store: this
stack writes them and grants the task role the path, and the api reads them at
boot (app/voice_platform/ssm_config.py). No task-definition change, so no
Terraform apply: CDK owns the platform end to end.

EVERY PIECE IS OPTIONAL TO THE API. A deployment without this stack runs the
platform's Admin CRUD with in-memory session state and local recordings, and
GET /api/platform/admin/status says so. Deploying this stack is what turns the
projections on.

The Lambda is the queue/ package directory of the api, zipped as-is: it imports
only the standard library and boto3 (see app/voice_platform/queue/__init__.py),
so there is no bundling step and no Docker.
"""

from __future__ import annotations

import json
from pathlib import Path

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    aws_dynamodb as ddb,
    aws_iam as iam,
    aws_lambda as _lambda,
    aws_s3 as s3,
    aws_s3_notifications as s3n,
    aws_sqs as sqs,
    aws_ssm as ssm,
)
from constructs import Construct

DEGREE_LEVELS = ("UG", "PG")

#: The queue package, relative to this file: infra/cdk/reep_voice_platform/ ->
#: apps/api-py/app/voice_platform/queue.
QUEUE_PACKAGE_DIR = (
    Path(__file__).resolve().parents[3] / "apps" / "api-py" / "app" / "voice_platform" / "queue"
)


class VoicePlatformStack(Stack):
    def __init__(
        self,
        scope: Construct,
        construct_id: str,
        *,
        project: str = "reep",
        api_task_role_name: str | None = None,
        github_deploy_role_name: str | None = None,
        recording_retention_days: int = 180,
        lambda_code_path: Path | None = None,
        **kwargs,
    ) -> None:
        super().__init__(scope, construct_id, **kwargs)
        prefix = f"{project}-voice"
        role_name = api_task_role_name or f"{project}-api-task"
        deploy_role_name = github_deploy_role_name or f"{project}-github-deploy"
        parameter_path = f"/{project}/voice-platform"

        # --- S3 -------------------------------------------------------------
        uploads = s3.Bucket(
            self,
            "Uploads",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
            # Candidate ingest files are in NO backup plan — the plan's one
            # selection covers the database and EFS, nothing in S3 — so before
            # this, an overwritten or deleted roster was gone with no second
            # copy anywhere. Versioning is the only thing standing behind them.
            # `recordings` below is versioned too now, and the paragraph that
            # used to stand here explaining why it must NOT be has moved there,
            # to the line that reverses it. RETAIN keeps the bucket; versioning
            # keeps its contents. They are different promises.
            versioned=True,
        )
        recordings = s3.Bucket(
            self,
            "Recordings",
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
            enforce_ssl=True,
            removal_policy=RemovalPolicy.RETAIN,
            # VERSIONED, WHICH REVERSES A DELIBERATE DECISION -- so the decision
            # it reverses is written out rather than deleted.
            #
            # This bucket previously carried no versioning, and the comment on
            # `Uploads` above said why in terms that were correct at the time:
            # "that bucket expires student voice on a clock, and a non-current
            # version outlives the delete marker, so versioning there would
            # quietly keep audio past the retention the student consented to."
            # That argument holds only while the deployment's rule is the
            # consented clock. The college's rule is now that NOTHING in storage
            # is deleted -- student and faculty records are kept permanently --
            # so the clock is no longer the governing promise and versioning is
            # what makes the new one true. `RecordingStore.delete` issues a real
            # `delete_object`; on a versioned bucket that writes a DELETE MARKER
            # and leaves the object, so an admin removing a call from the
            # console still sees it go and the bytes stay.
            #
            # WHAT THIS OBLIGES SOMEBODY TO CHANGE, said here because nothing
            # else will say it: the interview consent panel tells the student
            # their recording is destroyed after the policy's `retention_days`,
            # and `interview_policies.retention_days` is still the number it
            # shows. That copy is now inaccurate. Fixing it is a product change
            # in `app/interview_policy.py` and the consent screen, not an infra
            # one, and it must land before this is described to students as
            # their policy.
            versioned=True,
            # NO LIFECYCLE RULE when `recording_retention_days` is 0, which is
            # the deployment default now -- the identity ledger's and the dump
            # archive's deliberate absence, for the same reason: this is a copy
            # meant to outlive every bounded tier, and a rule it acquires by
            # accident is that promise quietly expiring in the one direction S3
            # never reports.
            #
            # A NUMBER IS STILL ACCEPTED, and 0 is off rather than the code
            # default being "forever", so a synth with no context renders a
            # bucket that still expires -- `archiveRetentionDays`' idiom, where
            # the durable choice is written down in cdk.json because it is a
            # decision and decisions are read where they are made.
            lifecycle_rules=(
                [
                    s3.LifecycleRule(
                        id="expire-recordings",
                        prefix="recordings/",
                        expiration=Duration.days(int(recording_retention_days)),
                        # On a versioned bucket an expiration writes a delete
                        # marker and leaves the version stored and billed. If a
                        # deployment does turn the clock back on, it has to mean
                        # it -- a rule without this half frees nothing while
                        # looking right in the console.
                        noncurrent_version_expiration=Duration.days(
                            int(recording_retention_days)
                        ),
                    )
                ]
                if int(recording_retention_days) > 0
                else None
            ),
        )

        # --- SQS: one stream per degree level, each with a DLQ ------------
        queues: dict[str, sqs.Queue] = {}
        for degree in DEGREE_LEVELS:
            dlq = sqs.Queue(
                self,
                f"Candidates{degree}Dlq",
                queue_name=f"{prefix}-candidates-{degree.lower()}-dlq",
                # 14 days: long enough for a human to read what could not be stored.
                retention_period=Duration.days(14),
                encryption=sqs.QueueEncryption.SQS_MANAGED,
            )
            queues[degree] = sqs.Queue(
                self,
                f"Candidates{degree}",
                queue_name=f"{prefix}-candidates-{degree.lower()}",
                visibility_timeout=Duration.seconds(60),
                retention_period=Duration.days(4),
                encryption=sqs.QueueEncryption.SQS_MANAGED,
                dead_letter_queue=sqs.DeadLetterQueue(max_receive_count=5, queue=dlq),
            )

        # --- Lambda: validate the upload, push to the right stream -----------
        ingest = _lambda.Function(
            self,
            "CandidateIngest",
            function_name=f"{prefix}-candidate-ingest",
            runtime=_lambda.Runtime.PYTHON_3_12,
            handler="lambda_handler.handler",
            code=_lambda.Code.from_asset(
                str(lambda_code_path or QUEUE_PACKAGE_DIR),
                exclude=["__pycache__", "*.pyc", "worker.py"],
            ),
            timeout=Duration.seconds(120),
            memory_size=512,
            environment={
                "PLATFORM_UG_QUEUE_URL": queues["UG"].queue_url,
                "PLATFORM_PG_QUEUE_URL": queues["PG"].queue_url,
                "PLATFORM_REJECTS_PREFIX": "rejects/",
            },
        )
        uploads.grant_read(ingest)
        uploads.grant_put(ingest, "rejects/*")
        for queue in queues.values():
            queue.grant_send_messages(ingest)
        uploads.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.LambdaDestination(ingest),
            s3.NotificationKeyFilter(prefix="uploads/"),
        )

        # --- DynamoDB: realtime session state, one table per degree level ----
        tables: dict[str, ddb.Table] = {}
        for degree in DEGREE_LEVELS:
            tables[degree] = ddb.Table(
                self,
                f"Sessions{degree}",
                table_name=f"{prefix}-sessions-{degree.lower()}",
                partition_key=ddb.Attribute(name="session_id", type=ddb.AttributeType.STRING),
                billing_mode=ddb.BillingMode.PAY_PER_REQUEST,
                time_to_live_attribute="expires_at",
                encryption=ddb.TableEncryption.AWS_MANAGED,
                point_in_time_recovery_specification=ddb.PointInTimeRecoverySpecification(
                    point_in_time_recovery_enabled=True
                ),
                removal_policy=RemovalPolicy.RETAIN,
            )

        # --- The api task role: imported, never redefined ----------------------
        api_role = iam.Role.from_role_name(self, "ApiTaskRole", role_name)

        # --- What the api task may do --------------------------------------------
        iam.Policy(
            self,
            "ApiVoicePlatform",
            policy_name="voice-platform",
            roles=[api_role],
            statements=[
                iam.PolicyStatement(
                    actions=["s3:PutObject", "s3:GetObject", "s3:DeleteObject"],
                    resources=[recordings.arn_for_objects("*"), uploads.arn_for_objects("*")],
                ),
                iam.PolicyStatement(
                    actions=[
                        "sqs:SendMessage",
                        "sqs:ReceiveMessage",
                        "sqs:DeleteMessage",
                        "sqs:GetQueueAttributes",
                    ],
                    resources=[q.queue_arn for q in queues.values()],
                ),
                iam.PolicyStatement(
                    actions=["dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:GetItem", "dynamodb:Query"],
                    resources=[t.table_arn for t in tables.values()],
                ),
                iam.PolicyStatement(
                    actions=["cloudwatch:PutMetricData"],
                    resources=["*"],
                    conditions={"StringEquals": {"cloudwatch:namespace": "REEP/VoicePlatform"}},
                ),
                # The api reads its PLATFORM_* settings from the parameters
                # below at boot (app/voice_platform/ssm_config.py) — which is
                # what keeps the task definition, and Terraform, out of the
                # platform's deploy path.
                iam.PolicyStatement(
                    actions=["ssm:GetParametersByPath", "ssm:GetParameter", "ssm:GetParameters"],
                    resources=[
                        self.format_arn(service="ssm", resource="parameter", resource_name=parameter_path.lstrip("/")),
                        self.format_arn(service="ssm", resource="parameter", resource_name=parameter_path.lstrip("/") + "/*"),
                    ],
                ),
            ],
        )

        # --- The GitHub deploy role: may run this stack through the CDK CLI ------
        # .github/workflows/cdk-deploy.yml assumes the Terraform-owned
        # reep-github-deploy role (OIDC, pinned to this repo and main). The CDK
        # CLI then assumes the account's bootstrap roles and CloudFormation does
        # the work under the bootstrap execution role, so the grant is only "may
        # assume those roles" plus reading the bootstrap version parameter.
        # Attached here rather than in Terraform so the whole platform has one
        # owner; the first deploy is run by a human with admin credentials.
        deploy_role = iam.Role.from_role_name(self, "GithubDeployRole", deploy_role_name)
        iam.Policy(
            self,
            "GithubDeployCdk",
            policy_name="deploy-cdk-stacks",
            roles=[deploy_role],
            statements=[
                iam.PolicyStatement(
                    actions=["sts:AssumeRole"],
                    resources=[self.format_arn(service="iam", region="", resource="role", resource_name="cdk-hnb659fds-*")],
                ),
                iam.PolicyStatement(
                    actions=["ssm:GetParameter"],
                    resources=[self.format_arn(service="ssm", resource="parameter", resource_name="cdk-bootstrap/*")],
                ),
            ],
        )

        # --- The api's environment, via SSM ----------------------------------------
        env = {
            "PLATFORM_AWS_REGION": self.region,
            "PLATFORM_UG_QUEUE_URL": queues["UG"].queue_url,
            "PLATFORM_PG_QUEUE_URL": queues["PG"].queue_url,
            "PLATFORM_BULK_UPLOAD_BUCKET": uploads.bucket_name,
            "PLATFORM_RECORDINGS_BUCKET": recordings.bucket_name,
            "PLATFORM_DYNAMO_UG_TABLE": tables["UG"].table_name,
            "PLATFORM_DYNAMO_PG_TABLE": tables["PG"].table_name,
            "PLATFORM_CLOUDWATCH_NAMESPACE": "REEP/VoicePlatform",
        }
        for name, value in env.items():
            ssm.StringParameter(
                self,
                f"Param{name}",
                parameter_name=f"{parameter_path}/{name}",
                string_value=value,
                description=f"voice platform: {name} (read by the api at boot, app/voice_platform/ssm_config.py)",
            )
            CfnOutput(self, name, value=value)

        self.uploads = uploads
        self.recordings = recordings
        self.queues = queues
        self.tables = tables
        self.ingest = ingest
