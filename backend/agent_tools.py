"""
agent_tools.py — the read-only AWS tool the cost-analysis AGENT can call.

The agent has FULL FREEDOM: rather than the code pre-deciding what to inspect or
offering a fixed menu of service-specific tools, there is ONE universal tool,
`aws_api`, that can call ANY read-only AWS API on ANY service via boto3. The model
decides — at runtime — which service, which operation, and which params it needs to
investigate each resource. No per-resource logic, no hardcoded steps.

SAFETY: `aws_api` is hard-locked to read-only operations (names starting with
describe_/get_/list_/...). Every mutating verb is blocked in code, so the agent
physically cannot delete, modify, or create anything — even if asked.

The tool returns a structured {"available": false, ...} on any error (missing
permission, bad operation, etc.) so the model can reason/self-correct rather than
the loop crashing.
"""

from __future__ import annotations

import json

import boto3
from botocore.exceptions import ClientError, BotoCoreError, NoCredentialsError


def _client(service: str, region: str | None):
    return boto3.client(service, region_name=region)


def _unavailable(reason: str, hint: str | None = None) -> dict:
    out = {"available": False, "reason": reason}
    if hint:
        out["hint"] = hint
    return out


def _denied_or_error(e: Exception) -> dict:
    """Turn any AWS error into a graceful 'unavailable' result for the AI."""
    if isinstance(e, NoCredentialsError):
        return _unavailable("No AWS credentials available.")
    if isinstance(e, ClientError):
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("AccessDenied", "AccessDeniedException", "UnauthorizedOperation"):
            return _unavailable(f"Access denied ({code}).", "The IAM role lacks read permission for this API.")
        if code in ("OptInRequired", "ResourceNotFoundException"):
            return _unavailable(f"Service not enabled or resource not found ({code}).")
        return _unavailable(f"AWS error: {code or e}")
    if isinstance(e, BotoCoreError):
        return _unavailable(f"AWS SDK error: {e}")
    return _unavailable(f"Unexpected error: {e}")


# --------------------------------------------------------------------------- #
# Universal read-only AWS tool — full freedom, hard-locked to read operations.  #
# --------------------------------------------------------------------------- #
# Only operations whose name starts with one of these prefixes may run. Every
# mutating verb (delete/terminate/modify/create/put/update/...) is therefore
# impossible to invoke, no matter what the model asks for.
_READONLY_PREFIXES = ("describe_", "get_", "list_", "batch_get_", "lookup_", "search_", "scan", "query")


def tool_aws_api(service: str, operation: str, params: dict | None = None,
                 region: str | None = None) -> dict:
    """
    Call ANY read-only AWS API the agent decides it needs, via boto3. This is the
    agent's single, universal capability — it can investigate any service/metric:
      e.g. cloudwatch get_metric_statistics (CPU, RDS connections, ELB requests…),
           ec2 describe_instances / describe_volumes / describe_snapshots,
           compute-optimizer get_ec2_instance_recommendations,
           ce get_cost_and_usage, s3 get_bucket_lifecycle_configuration,
           dynamodb describe_table, rds describe_db_instances, …

    SAFETY: only operations whose name begins with a read-only prefix are allowed.
    Any mutating operation is hard-blocked here — the agent cannot change anything.

    Args:
      service:   boto3 service name, e.g. "ec2", "cloudwatch", "rds", "s3".
      operation: snake_case API method, e.g. "describe_db_instances".
      params:    dict of arguments for the operation (boto3 PascalCase keys).
      region:    AWS region (ignored for global services like s3/ce).
    """
    op = (operation or "").strip()
    params = params or {}

    # Hard read-only lock — refuse anything not clearly a read.
    if not op.startswith(_READONLY_PREFIXES):
        return _unavailable(
            f"Operation '{op}' is blocked: only read-only operations are permitted "
            f"(must start with describe_/get_/list_/batch_get_/lookup_/search_/scan/query).",
            hint="This tool can only READ. Use a read operation to investigate.",
        )

    try:
        client = _client(service, region)
    except Exception as e:  # noqa: BLE001
        return _denied_or_error(e)

    method = getattr(client, op, None)
    if method is None or not callable(method):
        return _unavailable(
            f"'{service}' has no operation '{op}'.",
            hint="Check the boto3 operation name (snake_case, e.g. 'describe_volumes').",
        )

    try:
        result = method(**params)
        # Drop boto3 response metadata; cap size so a huge response can't blow the
        # context window — the model gets the useful payload, trimmed if needed.
        if isinstance(result, dict):
            result.pop("ResponseMetadata", None)
        text = json.dumps(result, default=str)
        if len(text) > 12000:
            text = text[:12000] + " …(truncated)"
            return {"available": True, "truncated": True, "result_json": text}
        return {"available": True, "result": json.loads(text)}
    except TypeError as e:
        return _unavailable(f"Bad parameters for {service}.{op}: {e}",
                            hint="Check the param names/shape for this operation.")
    except Exception as e:  # noqa: BLE001
        return _denied_or_error(e)


# --------------------------------------------------------------------------- #
# Dispatch table + OpenAI tool specs — a single universal tool.                 #
# --------------------------------------------------------------------------- #
TOOL_IMPLS = {
    "aws_api": tool_aws_api,
}

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "aws_api",
            "description": "Call ANY read-only AWS API on any service via boto3 to investigate a "
            "resource. This is your universal capability — decide for yourself what to check. "
            "Examples: cloudwatch get_metric_statistics for CPU / RDS connections / ELB request "
            "counts; ec2 describe_volumes / describe_snapshots / describe_instances; "
            "compute-optimizer get_ec2_instance_recommendations; ce get_cost_and_usage for real "
            "spend; s3 get_bucket_lifecycle_configuration; dynamodb describe_table. ONLY read "
            "operations are allowed (describe_/get_/list_/...); mutating calls are blocked. Use "
            "snake_case operation names and boto3 PascalCase param keys.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "description": "boto3 service, e.g. 'ec2', 'cloudwatch', 'rds', 's3', 'dynamodb', 'ce', 'compute-optimizer'."},
                    "operation": {"type": "string", "description": "Read-only API method in snake_case, e.g. 'get_metric_statistics'."},
                    "params": {"type": "object", "description": "Arguments for the operation (boto3 PascalCase keys)."},
                    "region": {"type": "string", "description": "AWS region (ignored for global services)."},
                },
                "required": ["service", "operation"],
            },
        },
    },
]
