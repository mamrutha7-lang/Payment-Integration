"""SQS push/pull for the Undergraduate and Postgraduate candidate streams, the
candidate validator, the S3-trigger Lambda and the drain worker.

`validation.py` and `lambda_handler.py` import nothing outside the standard
library and boto3 (present in every Lambda Python runtime) so this directory
can be zipped AS-IS into the Lambda — the CDK stack in infra/cdk does exactly
that. Keep it that way: an import of `app.*` here is an import the Lambda
cannot satisfy.
"""
