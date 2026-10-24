"""The CloudFront-scope WAF, which must live in us-east-1.

security.tf's `aws_wafv2_web_acl.edge` under the `aws.us_east_1` provider. A
CloudFormation stack is one region, so this is its own stack; the core stack
takes the ACL's ARN as the `wafWebAclArn` context value rather than a
cross-region reference, because cross-region references are Lambda-backed
custom resources and `cdk import` refuses a template that adds any.
"""

from __future__ import annotations

from typing import Any

from aws_cdk import (
    CfnOutput,
    Duration,
    RemovalPolicy,
    Stack,
    Tags,
    aws_cloudwatch as cloudwatch,
    aws_cloudwatch_actions as cw_actions,
    aws_iam as iam,
    aws_logs as logs,
    aws_route53 as route53,
    aws_s3 as s3,
    aws_sns as sns,
    aws_sns_subscriptions as subs,
    aws_wafv2 as wafv2,
)
from constructs import Construct


#: Rules inside AWSManagedRulesCommonRuleSet that are COUNTED rather than
#: allowed to block, and why each one is here.
#:
#: SizeRestrictions_BODY (2026-09-17). The rule blocks any request whose body
#: is over 8 KB, and it was blocking EVERY FILE UPLOAD IN THE PRODUCT: a
#: student's certificate on Skilling, the CV and photo the registration form
#: now requires, a leave attachment, a faculty signature, an alumni resume, an
#: upskilling certificate. Every one of those is a multipart POST far past 8 KB,
#: and the WAF answered it 403 at the edge -- before CloudFront, the ALB or the
#: API saw it -- with a body that is not JSON. The Angular client, reading no
#: `detail`, printed its fallback ("Certificate upload failed (PDF or JPEG, up
#: to 5 MB)."), so a student attaching a 2 MB JPEG was told their file was the
#: wrong kind. Nothing in the API could have caught it, and nothing in the
#: suite: the WAF exists only in front of the deployment.
#:
#: COUNT and not a scope-down: the API bounds every body itself
#: (`document_store.MAX_BYTES`, the per-handler `read(MAX + 1)`), so the edge
#: rule buys nothing there, and a scope-down that named the upload paths would
#: be a second list of them, maintained by hand in a file nobody opens when a
#: router adds a store. Counting keeps the metric -- the sampled requests still
#: show what would have been blocked -- and blocks nothing.
#:
#: The four injection rules below (2026-09-29). The note that stood here said
#: they could stay because they "inspect the first 16 KB of a body for
#: injection patterns, which a certificate does not carry". A certificate does
#: not carry them ON PURPOSE, and carries them BY CHANCE all the time: a JPEG
#: or a PDF is compressed binary, and somewhere in 16 KB of it the XSS
#: detector finds bytes that read as markup. Probed against production with
#: realistic generated CVs: 23 of 30 with an embedded headshot and 16 of 30
#: plain ones were refused 403 at the edge by `CrossSiteScripting_BODY`, and a
#: file that trips it trips it on EVERY retry. So a
#: student's CV was refused by content nobody chose, with "(403)" as the whole
#: explanation, and "try again" could never work. That was the 403 in the
#: registration complaints of 2026-09-29.
#:
#: Counted in the group, and RE-BLOCKED by `body-rules-outside-uploads` for
#: every request that is not `multipart/form-data`, so a JSON or urlencoded
#: body is exactly as protected as before and only a file upload is let
#: through. What a multipart body reaches is the API's own gate: every file is
#: magic-sniffed to PDF/PNG/JPEG before it is stored and served as an
#: `attachment`, never rendered, and a JSON endpoint handed a multipart body
#: answers 422 without reading it. The label is what carries the verdict from
#: the group to the rule, so the names below are the documented labels, casing
#: included (`_Body`, not `_BODY`) — a mistyped one matches nothing and quietly
#: turns that rule into a count everywhere.
COMMON_RULE_SET_BODY_LABELS: dict[str, str] = {
    "CrossSiteScripting_BODY": "awswaf:managed:aws:core-rule-set:CrossSiteScripting_Body",
    "GenericLFI_BODY": "awswaf:managed:aws:core-rule-set:GenericLFI_Body",
    "GenericRFI_BODY": "awswaf:managed:aws:core-rule-set:GenericRFI_Body",
    "EC2MetaDataSSRF_BODY": "awswaf:managed:aws:core-rule-set:EC2MetaDataSSRF_Body",
}
COMMON_RULE_SET_COUNTED: tuple[str, ...] = ("SizeRestrictions_BODY", *COMMON_RULE_SET_BODY_LABELS)

#: The rule that puts the four body rules back in front of everything that is
#: not an upload. Evaluated after `aws-common`, which is what makes its labels
#: visible here.
BODY_RULES_OUTSIDE_UPLOADS = "body-rules-outside-uploads"


#: How the outside uptime check knocks: a TCP connect to 443 on the public
#: name, and NOT an HTTPS request. The distribution's viewer policy is
#: TLSv1.3_2025 (set in the console on 2026-09-02, mirrored in stack.py), and
#: Route 53's health checkers negotiate TLS 1.2 at most, so an HTTPS check
#: fails every handshake with "protocol_version" while the site answers 200 to
#: every browser -- which is what the first deploy of this check did, on all
#: sixteen checkers. Relaxing the viewer policy to suit a monitor would be
#: weakening a security control to make an alarm green, so the check asks the
#: question it CAN ask: does the name resolve and does CloudFront accept a
#: connection. The application behind it is watched from inside by reep-core's
#: ALB alarms; a certificate is not, and a CloudWatch Synthetics canary (a real
#: browser, TLS 1.3) is the upgrade if that gap ever matters.
UPTIME_CHECK_TYPE = "TCP"


#: CLOUDFRONT'S REQUEST LOG, AND THE THREE FIELDS IT MUST NEVER CARRY
#: (2026-10-01). A student was shown "(504)" on /register and the question
#: "did CloudFront send that?" could only be answered by reasoning from
#: per-minute metrics, because the distribution logged nothing at all: no
#: standard log, no real-time log. It had not — the service worker on the phone
#: made the 504 up (apps/web/public/reep-sw.js) — but the next incident should
#: be settled by a line, not an inference.
#:
#: STANDARD LOGGING v2, NOT THE LEGACY KIND, BECAUSE ONLY v2 LETS FIELDS BE
#: LEFT OUT. The legacy log writes every field, and three of them are bearer
#: material on this deployment:
#:
#:   cs-uri-query  the setup, reset and activation links are `?token=…`, and
#:                 the Google callback carries `?code=…` — each one a way in.
#:   cs(Cookie)    `reep_session` IS the signed-in session.
#:   cs(Referer)   the page a request came FROM, query string included, so a
#:                 token page's first chunk request would carry the token.
#:
#: Everything else a 4xx/5xx investigation needs stays: the path (no route
#: puts a secret in its path), the status, the edge's own result type, the
#: timings and the client address. `c-ip` IS personal data, which is why the
#: bucket expires objects after `cloudfrontLogDays` and nothing else reads it.
CLOUDFRONT_LOG_FIELDS: tuple[str, ...] = (
    "date",
    "time",
    "x-edge-location",
    "x-edge-request-id",
    "c-ip",
    "c-country",
    "cs-method",
    "cs(Host)",
    "cs-uri-stem",
    "cs-protocol",
    "cs-protocol-version",
    "cs(User-Agent)",
    "cs-bytes",
    "sc-status",
    "sc-bytes",
    "sc-content-type",
    "time-taken",
    "time-to-first-byte",
    "x-edge-result-type",
    "x-edge-response-result-type",
    "x-edge-detailed-result-type",
    "cache-behavior-path-pattern",
    "ssl-protocol",
)
CLOUDFRONT_LOG_FIELDS_NEVER: tuple[str, ...] = ("cs-uri-query", "cs(Cookie)", "cs(Referer)")


def _body_rules_outside_uploads(priority: int, metric: str) -> wafv2.CfnWebACL.RuleProperty:
    labelled = wafv2.CfnWebACL.StatementProperty(
        or_statement=wafv2.CfnWebACL.OrStatementProperty(
            statements=[
                wafv2.CfnWebACL.StatementProperty(
                    label_match_statement=wafv2.CfnWebACL.LabelMatchStatementProperty(scope="LABEL", key=label)
                )
                for label in COMMON_RULE_SET_BODY_LABELS.values()
            ]
        )
    )
    multipart = wafv2.CfnWebACL.StatementProperty(
        byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
            field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(single_header={"Name": "content-type"}),
            positional_constraint="STARTS_WITH",
            search_string="multipart/form-data",
            text_transformations=[wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")],
        )
    )
    return wafv2.CfnWebACL.RuleProperty(
        name=BODY_RULES_OUTSIDE_UPLOADS,
        priority=priority,
        action=wafv2.CfnWebACL.RuleActionProperty(block={}),
        statement=wafv2.CfnWebACL.StatementProperty(
            and_statement=wafv2.CfnWebACL.AndStatementProperty(
                statements=[
                    labelled,
                    wafv2.CfnWebACL.StatementProperty(
                        not_statement=wafv2.CfnWebACL.NotStatementProperty(statement=multipart)
                    ),
                ]
            )
        ),
        visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
            cloud_watch_metrics_enabled=True, metric_name=metric, sampled_requests_enabled=True
        ),
    )


def _managed(
    name: str,
    priority: int,
    rule_name: str,
    metric: str,
    *,
    counted: tuple[str, ...] = (),
) -> wafv2.CfnWebACL.RuleProperty:
    overrides = [
        wafv2.CfnWebACL.RuleActionOverrideProperty(
            name=rule, action_to_use=wafv2.CfnWebACL.RuleActionProperty(count={})
        )
        for rule in counted
    ]
    return wafv2.CfnWebACL.RuleProperty(
        name=rule_name,
        priority=priority,
        override_action=wafv2.CfnWebACL.OverrideActionProperty(none={}),
        statement=wafv2.CfnWebACL.StatementProperty(
            managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                name=name,
                vendor_name="AWS",
                # Omitted entirely when empty: an empty list renders a property
                # the live ACL does not carry, which is a diff at the import
                # rehearsal for nothing.
                rule_action_overrides=overrides or None,
            )
        ),
        visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
            cloud_watch_metrics_enabled=True, metric_name=metric, sampled_requests_enabled=True
        ),
    )


class EdgeWafStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, *, project: str = "reep", **kwargs: Any) -> None:
        super().__init__(scope, construct_id, **kwargs)
        # The same phase discipline the core stack applies, and for the same
        # reason: this ACL is ADOPTED from Terraform, which tagged it
        # ManagedBy=terraform. A mirror that says `cdk` makes step 3's diff —
        # the rehearsal, whose whole value is that it should come back showing
        # only CDKMetadata and the output — carry a property difference, and
        # invites the operator to "fix" edge.py when nothing is wrong with it.
        # The core stack was written this way from the start; the edge stack
        # was the one place the rule was not applied *(pre-import review,
        # 2026-09-07)*. Harden flips it, as it does everywhere else.
        phase = (self.node.try_get_context("phase") or "harden").strip().lower()
        # The upload fix rides the HARDEN phase only, like every other change
        # to an adopted resource: the import phase is a byte-identical mirror
        # of what Terraform built, and the rehearsal's whole value is that it
        # shows no property difference. The deployed ACL has been on `harden`
        # since the cutover, so `cdk-deploy.yml`'s `edge-waf` option is the
        # deploy that turns uploads back on.
        counted = COMMON_RULE_SET_COUNTED if phase == "harden" else ()
        # The re-block rides the same phase as the counts it restores: the two
        # are one change, and counting the body rules without it would open
        # every JSON body to them.
        harden_rules = [_body_rules_outside_uploads(4, f"{project}-waf-body-outside-uploads")] if phase == "harden" else []

        acl = wafv2.CfnWebACL(
            self,
            "EdgeAcl",
            name=f"{project}-edge",
            scope="CLOUDFRONT",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True, metric_name=f"{project}-waf", sampled_requests_enabled=True
            ),
            rules=[
                _managed("AWSManagedRulesCommonRuleSet", 1, "aws-common", f"{project}-waf-common", counted=counted),
                _managed("AWSManagedRulesKnownBadInputsRuleSet", 2, "aws-bad-inputs", f"{project}-waf-badinputs"),
                wafv2.CfnWebACL.RuleProperty(
                    name="rate-limit",
                    priority=3,
                    action=wafv2.CfnWebACL.RuleActionProperty(block={}),
                    statement=wafv2.CfnWebACL.StatementProperty(
                        rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(limit=2000, aggregate_key_type="IP")
                    ),
                    visibility_config=wafv2.CfnWebACL.VisibilityConfigProperty(
                        cloud_watch_metrics_enabled=True, metric_name=f"{project}-waf-rate", sampled_requests_enabled=True
                    ),
                ),
                *harden_rules,
            ],
        )
        # The production ACL. A delete-stack must forget it, never delete it.
        acl.apply_removal_policy(RemovalPolicy.RETAIN)
        self.acl = acl
        CfnOutput(self, "WebAclArn", value=acl.attr_arn, description="Pass to the core stack as -c wafWebAclArn=…")

        # THE OUTSIDE UPTIME CHECK (2026-09-29). Every alarm in reep-core
        # watches from INSIDE the account -- ALB 5xx, healthy hosts, CPU -- so
        # a broken certificate, DNS record or CloudFront distribution leaves the
        # load balancer perfectly healthy and every student unable to connect,
        # with nothing firing. A Route 53 health check asks from Route 53's own
        # checkers around the world, through the public name, the way a student
        # does. It lives here because Route 53 publishes HealthCheckStatus in
        # us-east-1 only, and this is the stack that is already there. Harden
        # only, and only with both a domain and somebody to tell: the import
        # mirror must not grow resources, and an alarm with no subscriber is a
        # dashboard nobody opens.
        domain = self.node.try_get_context("domainName") or ""
        alert_email = self.node.try_get_context("alertEmail") or ""
        if phase == "harden" and domain and alert_email:
            check = route53.CfnHealthCheck(
                self,
                "UptimeCheck",
                health_check_config=route53.CfnHealthCheck.HealthCheckConfigProperty(
                    type=UPTIME_CHECK_TYPE,
                    fully_qualified_domain_name=domain,
                    port=443,
                    request_interval=30,
                    failure_threshold=3,
                ),
                health_check_tags=[route53.CfnHealthCheck.HealthCheckTagProperty(key="Name", value=f"{project}-uptime")],
            )
            topic = sns.Topic(self, "UptimeAlerts", topic_name=f"{project}-uptime-alerts")
            # SNS mails a confirmation link first; nothing is delivered until
            # it is clicked. That is AWS's rule, not a missing step here.
            subscription = topic.add_subscription(subs.EmailSubscription(alert_email))
            alarm = cloudwatch.Alarm(
                self,
                "UptimeAlarm",
                alarm_name=f"{project}-site-unreachable",
                alarm_description=(
                    f"{domain}:443 refused Route 53's health checkers a connection "
                    "for two minutes: students cannot reach REEP from outside AWS."
                ),
                metric=cloudwatch.Metric(
                    namespace="AWS/Route53",
                    metric_name="HealthCheckStatus",
                    dimensions_map={"HealthCheckId": check.attr_health_check_id},
                    statistic="Minimum",
                    period=Duration.minutes(1),
                ),
                comparison_operator=cloudwatch.ComparisonOperator.LESS_THAN_THRESHOLD,
                threshold=1,
                evaluation_periods=2,
                # No datapoints means the checker itself has stopped reporting,
                # which is not evidence that the site is up.
                treat_missing_data=cloudwatch.TreatMissingData.BREACHING,
            )
            alarm.add_alarm_action(cw_actions.SnsAction(topic))
            alarm.add_ok_action(cw_actions.SnsAction(topic))
            # Retain like everything else in the three stacks
            # (test_all_three_stacks_retain_everything).
            for resource in (check, topic, subscription, alarm):
                resource.apply_removal_policy(RemovalPolicy.RETAIN)

        # CloudFront's request log (see CLOUDFRONT_LOG_FIELDS for what it may
        # carry). Here and not in reep-core because the delivery source for a
        # distribution must be created in us-east-1, which is this stack's
        # region. The distribution is reep-core's, so its id arrives as context
        # (`cloudfrontDistributionId`, the live value, in cdk.context.json) for
        # `wafWebAclArn`'s reason in the other direction. Harden only, and only
        # with a retention: 0 or absent is OFF, so a synth with no context
        # renders exactly what was deployed before.
        dist_id = (self.node.try_get_context("cloudfrontDistributionId") or "").strip()
        log_days = int(self.node.try_get_context("cloudfrontLogDays") or 0)
        if phase == "harden" and dist_id and log_days > 0:
            bucket = s3.Bucket(
                self,
                "CloudFrontLogs",
                bucket_name=f"{project}-cloudfront-logs-{self.account}",
                encryption=s3.BucketEncryption.S3_MANAGED,
                block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
                object_ownership=s3.ObjectOwnership.BUCKET_OWNER_ENFORCED,
                enforce_ssl=True,
                lifecycle_rules=[s3.LifecycleRule(id="expire-request-logs", expiration=Duration.days(log_days))],
                removal_policy=RemovalPolicy.RETAIN,
            )
            # CloudWatch's log-delivery service writes the objects; it may write
            # under this account's prefix, for a delivery source in this account,
            # and nothing else. CloudFront appends AWSLogs/<account>/CloudFront
            # itself when the destination names no prefix.
            bucket.add_to_resource_policy(
                iam.PolicyStatement(
                    sid="CloudFrontLogDeliveryWrite",
                    principals=[iam.ServicePrincipal("delivery.logs.amazonaws.com")],
                    actions=["s3:PutObject"],
                    resources=[bucket.arn_for_objects(f"AWSLogs/{self.account}/*")],
                    conditions={
                        "StringEquals": {
                            "aws:SourceAccount": self.account,
                            "s3:x-amz-acl": "bucket-owner-full-control",
                        },
                        "ArnLike": {"aws:SourceArn": f"arn:aws:logs:us-east-1:{self.account}:delivery-source:*"},
                    },
                )
            )
            source = logs.CfnDeliverySource(
                self,
                "CloudFrontLogSource",
                name=f"{project}-cloudfront-access",
                log_type="ACCESS_LOGS",
                resource_arn=f"arn:aws:cloudfront::{self.account}:distribution/{dist_id}",
            )
            destination = logs.CfnDeliveryDestination(
                self,
                "CloudFrontLogDestination",
                name=f"{project}-cloudfront-access-s3",
                destination_resource_arn=bucket.bucket_arn,
                output_format="json",
            )
            delivery = logs.CfnDelivery(
                self,
                "CloudFrontLogDelivery",
                delivery_source_name=source.name,
                delivery_destination_arn=destination.attr_arn,
                record_fields=list(CLOUDFRONT_LOG_FIELDS),
            )
            # The source name is a plain string, so CloudFormation cannot see the
            # order on its own; the bucket POLICY must exist before the first
            # delivery is attempted or the service is refused the write.
            delivery.node.add_dependency(source, bucket)
            if bucket.policy is not None:
                delivery.node.add_dependency(bucket.policy)
                bucket.policy.apply_removal_policy(RemovalPolicy.RETAIN)
            # Retain like everything else in the three stacks.
            for resource in (source, destination, delivery):
                resource.apply_removal_policy(RemovalPolicy.RETAIN)

        # On the CHILDREN, never on the stack — see the long note at the end of
        # stack.py. `Tags.of(stack)` makes CDK send stack-level tags, and
        # CloudFormation refuses those on an import change set with
        # "you cannot modify or add [RoleArn, Tags]". That is how this very
        # stack's rehearsal import failed on 2026-09-07.
        for child in self.node.children:
            Tags.of(child).add("Project", project)
            Tags.of(child).add("ManagedBy", "cdk" if phase == "harden" else "terraform")
