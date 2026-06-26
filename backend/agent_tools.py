"""
agent_tools.py — read-only AWS tools the cost-analysis AGENT can call.

Instead of the code pre-deciding what data to feed the AI, the AI itself decides
which of these tools to call per resource (via OpenAI-style function calling).
Every tool is READ-ONLY and degrades gracefully: if a permission is missing or
the service isn't enabled, it returns a structured {"available": false, ...}
result the AI can reason about — it never raises into the agent loop.

Each tool here has:
  • an implementation function `tool_<name>(**args) -> dict`
  • a JSON-schema spec in TOOL_SPECS (the OpenAI "tools" array)
The agent loop (agent.py) dispatches tool calls by name to TOOL_IMPLS.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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
# Tool implementations                                                         #
# --------------------------------------------------------------------------- #
def tool_get_cpu_metrics(instance_id: str, region: str | None = None, lookback_days: int = 14) -> dict:
    """Average + maximum CPU utilization (%) for an EC2 instance over N days."""
    try:
        cw = _client("cloudwatch", region)
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=lookback_days)
        resp = cw.get_metric_statistics(
            Namespace="AWS/EC2",
            MetricName="CPUUtilization",
            Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
            StartTime=start,
            EndTime=end,
            Period=3600,
            Statistics=["Average", "Maximum"],
            Unit="Percent",
        )
        points = resp.get("Datapoints", [])
        if not points:
            return _unavailable("No CPU datapoints (instance may be stopped or too new).")
        avgs = [p["Average"] for p in points if "Average" in p]
        maxs = [p["Maximum"] for p in points if "Maximum" in p]
        return {
            "available": True,
            "avg_cpu_pct": round(sum(avgs) / len(avgs), 2) if avgs else None,
            "max_cpu_pct": round(max(maxs), 2) if maxs else None,
            "datapoints": len(points),
            "lookback_days": lookback_days,
        }
    except Exception as e:  # noqa: BLE001 - we deliberately never raise into the loop
        return _denied_or_error(e)


def tool_get_compute_optimizer_recommendation(resource_arn: str, region: str | None = None) -> dict:
    """
    AWS Compute Optimizer's right-sizing recommendation for an EC2 instance ARN.
    This is AWS's own usage-based recommendation — the strongest signal we have.
    """
    try:
        co = _client("compute-optimizer", region)
        resp = co.get_ec2_instance_recommendations(instanceArns=[resource_arn])
        recs = resp.get("instanceRecommendations", [])
        if not recs:
            return _unavailable("No recommendation (instance not analyzed yet, or Compute Optimizer not enabled).")
        r = recs[0]
        options = [
            {
                "instanceType": o.get("instanceType"),
                "rank": o.get("rank"),
                "savingsPct": o.get("savingsOpportunity", {}).get("savingsOpportunityPercentage"),
                "estimatedMonthlySavingsUsd": o.get("savingsOpportunity", {})
                .get("estimatedMonthlySavings", {})
                .get("value"),
            }
            for o in r.get("recommendationOptions", [])
        ]
        return {
            "available": True,
            "finding": r.get("finding"),  # e.g. OVER_PROVISIONED / OPTIMIZED / UNDER_PROVISIONED
            "current_instance_type": r.get("currentInstanceType"),
            "recommendation_options": options,
        }
    except Exception as e:  # noqa: BLE001
        return _denied_or_error(e)


def tool_get_service_cost(service: str, region: str | None = None, lookback_days: int = 30) -> dict:
    """
    Actual spend for an AWS service over the last N days, from Cost Explorer.
    Grounds savings estimates in real dollars instead of guesses. Cost Explorer
    is a global endpoint (us-east-1) and bills ~$0.01 per request.
    """
    try:
        ce = _client("ce", "us-east-1")  # Cost Explorer is global, lives in us-east-1
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=lookback_days)
        resp = ce.get_cost_and_usage(
            TimePeriod={"Start": start.isoformat(), "End": end.isoformat()},
            Granularity="MONTHLY",
            Metrics=["UnblendedCost"],
            Filter={"Dimensions": {"Key": "SERVICE", "Values": [service]}},
        )
        results = resp.get("ResultsByTime", [])
        total = sum(float(r["Total"]["UnblendedCost"]["Amount"]) for r in results if r.get("Total"))
        return {
            "available": True,
            "service": service,
            "lookback_days": lookback_days,
            "total_cost_usd": round(total, 2),
            "currency": "USD",
        }
    except Exception as e:  # noqa: BLE001
        return _denied_or_error(e)


def tool_describe_resource(service: str, resource_id: str, region: str | None = None) -> dict:
    """
    Deeper config for a specific resource when the AI wants more detail than the
    initial scan provided. Supports the services we scan: ec2, ebs, rds, s3.
    """
    try:
        svc = service.lower()
        if svc in ("ec2", "instance"):
            ec2 = _client("ec2", region)
            d = ec2.describe_instances(InstanceIds=[resource_id])
            inst = d["Reservations"][0]["Instances"][0]
            return {"available": True, "detail": {
                "type": inst.get("InstanceType"), "state": inst.get("State", {}).get("Name"),
                "arn_hint": f"arn:aws:ec2:{region}:<account>:instance/{resource_id}",
                "ebs_optimized": inst.get("EbsOptimized"),
                "cpu_options": inst.get("CpuOptions"),
            }}
        if svc in ("ebs", "volume"):
            ec2 = _client("ec2", region)
            d = ec2.describe_volumes(VolumeIds=[resource_id])
            v = d["Volumes"][0]
            return {"available": True, "detail": {
                "type": v.get("VolumeType"), "size_gb": v.get("Size"),
                "state": v.get("State"), "iops": v.get("Iops"),
                "attachments": [a.get("InstanceId") for a in v.get("Attachments", [])],
            }}
        if svc == "rds":
            rds = _client("rds", region)
            d = rds.describe_db_instances(DBInstanceIdentifier=resource_id)
            db = d["DBInstances"][0]
            return {"available": True, "detail": {
                "class": db.get("DBInstanceClass"), "engine": db.get("Engine"),
                "multi_az": db.get("MultiAZ"), "storage_gb": db.get("AllocatedStorage"),
            }}
        if svc == "s3":
            s3 = _client("s3", None)
            try:
                s3.get_bucket_lifecycle_configuration(Bucket=resource_id)
                lifecycle = True
            except ClientError:
                lifecycle = False
            return {"available": True, "detail": {"has_lifecycle_policy": lifecycle}}
        return _unavailable(f"describe not supported for service '{service}'.")
    except Exception as e:  # noqa: BLE001
        return _denied_or_error(e)


# --------------------------------------------------------------------------- #
# Dispatch table + OpenAI tool specs                                           #
# --------------------------------------------------------------------------- #
TOOL_IMPLS = {
    "get_cpu_metrics": tool_get_cpu_metrics,
    "get_compute_optimizer_recommendation": tool_get_compute_optimizer_recommendation,
    "get_service_cost": tool_get_service_cost,
    "describe_resource": tool_describe_resource,
}

TOOL_SPECS = [
    {
        "type": "function",
        "function": {
            "name": "get_cpu_metrics",
            "description": "Get average and maximum CPU utilization (%) for an EC2 instance over a "
            "lookback window. Use this to judge whether a running instance is over-provisioned "
            "(low average CPU) or idle.",
            "parameters": {
                "type": "object",
                "properties": {
                    "instance_id": {"type": "string", "description": "EC2 instance id, e.g. i-0abc123."},
                    "region": {"type": "string", "description": "AWS region of the instance."},
                    "lookback_days": {"type": "integer", "description": "Days to look back (default 14)."},
                },
                "required": ["instance_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_compute_optimizer_recommendation",
            "description": "Get AWS Compute Optimizer's usage-based right-sizing recommendation for an "
            "EC2 instance ARN (finding + cheaper instance-type options with estimated savings). "
            "The strongest cost signal when available.",
            "parameters": {
                "type": "object",
                "properties": {
                    "resource_arn": {"type": "string", "description": "Full EC2 instance ARN."},
                    "region": {"type": "string", "description": "AWS region."},
                },
                "required": ["resource_arn"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_service_cost",
            "description": "Get the ACTUAL dollar spend for an AWS service over the last N days from "
            "Cost Explorer. Use to ground savings estimates in real cost. Service must be the Cost "
            "Explorer service name, e.g. 'Amazon Elastic Compute Cloud - Compute', 'Amazon Simple "
            "Storage Service'.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "description": "Cost Explorer service name."},
                    "lookback_days": {"type": "integer", "description": "Days to look back (default 30)."},
                },
                "required": ["service"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_resource",
            "description": "Get deeper configuration for a specific resource (ec2, ebs, rds, or s3) "
            "when the initial scan data is insufficient.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string", "description": "One of: ec2, ebs, rds, s3."},
                    "resource_id": {"type": "string", "description": "The resource id."},
                    "region": {"type": "string", "description": "AWS region."},
                },
                "required": ["service", "resource_id"],
            },
        },
    },
]
