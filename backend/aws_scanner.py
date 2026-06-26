"""
aws_scanner.py — Step ③ of the request flow (AWS version).

The Azure reference used `az resource list --resource-group <name>` to dump every
resource in one call. AWS has no such command, so instead we keep a REGISTRY of
per-service scanners. The user picks which services to scan, and we run only those.

Each scanner uses boto3 (not the AWS CLI): boto3 is the same SDK the CLI runs on,
but returns native dicts, raises catchable exceptions, and avoids spawning a
subprocess per call.

Every scanner returns a list of resources in ONE uniform shape so that the rule
engine, category grouping, and LLM summary downstream all consume the same thing:

    {
        "service":     "EC2",
        "category":    "Compute",
        "resource_id": "i-0abc123",
        "name":        "web-server-1",     # from the Name tag, if any
        "region":      "ap-south-1",
        "type":        "m5.xlarge",         # instance type / volume type / class — the "SKU"
        "config":      { ... },             # raw service-specific config we may rule on later
        "tags":        { "Env": "prod" },
    }
"""

from __future__ import annotations

import boto3
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    NoCredentialsError,
    EndpointConnectionError,
)


# --------------------------------------------------------------------------- #
# Errors — mirror the Azure prompt's "CLI not installed / not logged in /      #
# invalid group" cases, translated to the AWS world.                           #
# --------------------------------------------------------------------------- #
class ScannerError(Exception):
    """Base class for any scan failure we want to report cleanly to the API layer."""

    def __init__(self, message: str, *, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.hint = hint


class NoCredentialsConfigured(ScannerError):
    """AWS equivalent of 'az not logged in' — no credentials found."""


class AccessDenied(ScannerError):
    """The credentials work but lack permission for this service (IAM)."""


class InvalidRegion(ScannerError):
    """AWS equivalent of 'invalid resource group' — region unreachable/unknown."""


# --------------------------------------------------------------------------- #
# Small helpers                                                                #
# --------------------------------------------------------------------------- #
def _tags_to_dict(tag_list: list[dict] | None) -> dict[str, str]:
    """boto3 returns tags as [{'Key': k, 'Value': v}, ...]; flatten to a dict."""
    if not tag_list:
        return {}
    return {t["Key"]: t["Value"] for t in tag_list if "Key" in t}


def _name_from_tags(tags: dict[str, str], fallback: str) -> str:
    return tags.get("Name") or fallback


def _client(service: str, region: str | None):
    """One place to build boto3 clients so credential errors surface consistently."""
    try:
        return boto3.client(service, region_name=region)
    except NoCredentialsError:
        raise NoCredentialsConfigured(
            "No AWS credentials found.",
            hint="Run `aws configure`, or set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY, "
            "or attach an IAM role.",
        )


def _run_scanner(fn, service_label: str, region: str | None):
    """
    Wrap an individual scanner so boto3 exceptions become our clean ScannerError
    types. This is the boto3 analogue of catching Azure CLI stderr strings —
    except here we get real exception types instead of regex-matching text.
    """
    try:
        return fn(region)
    except NoCredentialsError:
        raise NoCredentialsConfigured(
            "No AWS credentials found.",
            hint="Run `aws configure` or attach an IAM role.",
        )
    except EndpointConnectionError:
        raise InvalidRegion(
            f"Could not reach AWS in region '{region}'.",
            hint="Check the region name (e.g. 'ap-south-1') and your network.",
        )
    except ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("AccessDenied", "AccessDeniedException", "UnauthorizedOperation"):
            raise AccessDenied(
                f"Access denied scanning {service_label}.",
                hint=f"The IAM principal needs read permission for {service_label}. "
                "Attach a read-only policy (e.g. ReadOnlyAccess).",
            )
        # Anything else (throttling, bad region string, etc.) — surface the code.
        raise ScannerError(f"AWS error scanning {service_label}: {code or e}")
    except BotoCoreError as e:
        raise ScannerError(f"AWS SDK error scanning {service_label}: {e}")


# --------------------------------------------------------------------------- #
# Per-service scanners. Each takes a region and returns uniform-shaped dicts.   #
# Add a new service = add a function here + one entry in SERVICE_REGISTRY.      #
# --------------------------------------------------------------------------- #
def scan_ec2(region: str | None) -> list[dict]:
    ec2 = _client("ec2", region)
    out: list[dict] = []
    paginator = ec2.get_paginator("describe_instances")
    for page in paginator.paginate():
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                tags = _tags_to_dict(inst.get("Tags"))
                out.append(
                    {
                        "service": "EC2",
                        "category": "Compute",
                        "resource_id": inst["InstanceId"],
                        "name": _name_from_tags(tags, inst["InstanceId"]),
                        "region": region,
                        "type": inst.get("InstanceType"),
                        "config": {
                            "state": inst.get("State", {}).get("Name"),
                            "launch_time": str(inst.get("LaunchTime")),
                            "az": inst.get("Placement", {}).get("AvailabilityZone"),
                            "platform": inst.get("PlatformDetails"),
                        },
                        "tags": tags,
                    }
                )
    return out


def scan_ebs(region: str | None) -> list[dict]:
    ec2 = _client("ec2", region)
    out: list[dict] = []
    paginator = ec2.get_paginator("describe_volumes")
    for page in paginator.paginate():
        for vol in page["Volumes"]:
            tags = _tags_to_dict(vol.get("Tags"))
            out.append(
                {
                    "service": "EBS",
                    "category": "Storage",
                    "resource_id": vol["VolumeId"],
                    "name": _name_from_tags(tags, vol["VolumeId"]),
                    "region": region,
                    "type": vol.get("VolumeType"),  # gp2, gp3, io1, ...
                    "config": {
                        "size_gb": vol.get("Size"),
                        "state": vol.get("State"),  # 'available' = unattached
                        "iops": vol.get("Iops"),
                        "encrypted": vol.get("Encrypted"),
                        "attached_to": [a.get("InstanceId") for a in vol.get("Attachments", [])],
                    },
                    "tags": tags,
                }
            )
    return out


def scan_s3(region: str | None) -> list[dict]:
    # S3 bucket listing is global (no region), so we ignore the region arg here.
    s3 = _client("s3", None)
    out: list[dict] = []
    buckets = s3.list_buckets().get("Buckets", [])
    for b in buckets:
        name = b["Name"]
        # Per-bucket details can each fail independently (e.g. cross-region);
        # we degrade gracefully rather than abort the whole scan.
        loc, lifecycle = None, None
        try:
            loc = s3.get_bucket_location(Bucket=name).get("LocationConstraint") or "us-east-1"
        except ClientError:
            pass
        try:
            s3.get_bucket_lifecycle_configuration(Bucket=name)
            lifecycle = True
        except ClientError as e:
            # NoSuchLifecycleConfiguration is expected and meaningful (no policy = waste risk)
            if e.response.get("Error", {}).get("Code") == "NoSuchLifecycleConfiguration":
                lifecycle = False
        out.append(
            {
                "service": "S3",
                "category": "Storage",
                "resource_id": name,
                "name": name,
                "region": loc,
                "type": "bucket",
                "config": {
                    "creation_date": str(b.get("CreationDate")),
                    "has_lifecycle_policy": lifecycle,
                },
                "tags": {},  # bucket tagging is a separate call; add later if needed
            }
        )
    return out


def scan_rds(region: str | None) -> list[dict]:
    rds = _client("rds", region)
    out: list[dict] = []
    paginator = rds.get_paginator("describe_db_instances")
    for page in paginator.paginate():
        for db in page["DBInstances"]:
            arn = db.get("DBInstanceArn", db["DBInstanceIdentifier"])
            out.append(
                {
                    "service": "RDS",
                    "category": "Database",
                    "resource_id": db["DBInstanceIdentifier"],
                    "name": db["DBInstanceIdentifier"],
                    "region": region,
                    "type": db.get("DBInstanceClass"),  # db.t3.medium, ...
                    "config": {
                        "engine": db.get("Engine"),
                        "engine_version": db.get("EngineVersion"),
                        "status": db.get("DBInstanceStatus"),
                        "multi_az": db.get("MultiAZ"),
                        "storage_gb": db.get("AllocatedStorage"),
                        "storage_type": db.get("StorageType"),
                        "publicly_accessible": db.get("PubliclyAccessible"),
                    },
                    "tags": {},  # RDS tags via list_tags_for_resource(arn); add later
                }
            )
    return out


# --------------------------------------------------------------------------- #
# The registry. This IS the "list of scannable services" the API exposes.      #
# --------------------------------------------------------------------------- #
SERVICE_REGISTRY: dict[str, dict] = {
    "ec2": {"label": "EC2", "category": "Compute", "scan": scan_ec2, "regional": True},
    "ebs": {"label": "EBS", "category": "Storage", "scan": scan_ebs, "regional": True},
    "s3": {"label": "S3", "category": "Storage", "scan": scan_s3, "regional": False},
    "rds": {"label": "RDS", "category": "Database", "scan": scan_rds, "regional": True},
}


def list_services() -> list[dict]:
    """Powers GET /api/services — the AWS analogue of `az group list`."""
    return [
        {"key": key, "label": meta["label"], "category": meta["category"]}
        for key, meta in SERVICE_REGISTRY.items()
    ]


def scan_services(service_keys: list[str], region: str | None) -> dict:
    """
    Step ③: run the selected scanners and return their combined resources.

    Returns a structured result that separates what succeeded from what failed,
    so the API can report partial results (e.g. EC2 scanned fine but RDS hit
    AccessDenied) instead of failing the whole request.
    """
    if not service_keys:
        raise ScannerError("No services selected.", hint="Pass at least one service key, e.g. ['ec2'].")

    unknown = [k for k in service_keys if k not in SERVICE_REGISTRY]
    if unknown:
        raise ScannerError(
            f"Unknown service(s): {', '.join(unknown)}.",
            hint=f"Valid keys: {', '.join(SERVICE_REGISTRY)}",
        )

    resources: list[dict] = []
    errors: list[dict] = []

    for key in service_keys:
        meta = SERVICE_REGISTRY[key]
        try:
            found = _run_scanner(meta["scan"], meta["label"], region)
            resources.extend(found)
        except ScannerError as e:
            # One service failing shouldn't kill the others — collect and continue.
            errors.append({"service": meta["label"], "error": e.message, "hint": e.hint})

    return {
        "region": region,
        "scanned_services": [SERVICE_REGISTRY[k]["label"] for k in service_keys],
        "resource_count": len(resources),
        "resources": resources,
        "errors": errors,
    }
