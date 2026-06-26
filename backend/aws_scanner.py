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

from datetime import datetime, timedelta, timezone

import boto3
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    NoCredentialsError,
    EndpointConnectionError,
)

# How far back to look when assessing utilization. 14 days balances "recent
# enough to be relevant" against "long enough to catch idle resources".
METRIC_LOOKBACK_DAYS = 14


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


def _ec2_cpu_utilization(cw, instance_id: str) -> dict | None:
    """
    Fetch average + maximum CPU utilization (%) for an EC2 instance over the
    lookback window. This is the evidence the AI needs to legitimately call an
    instance over-provisioned/idle — without it, "over-provisioned" is a guess.

    Returns {avg_cpu_pct, max_cpu_pct, datapoints, lookback_days} or None if
    CloudWatch has no data / the call is denied (we degrade gracefully rather
    than fail the whole scan).
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=METRIC_LOOKBACK_DAYS)
    try:
        resp = cw.get_metric_statistics(
            Namespace="AWS/EC2",
            MetricName="CPUUtilization",
            Dimensions=[{"Name": "InstanceId", "Value": instance_id}],
            StartTime=start,
            EndTime=end,
            Period=3600,  # hourly datapoints
            Statistics=["Average", "Maximum"],
            Unit="Percent",
        )
    except ClientError:
        return None

    points = resp.get("Datapoints", [])
    if not points:
        return None
    avgs = [p["Average"] for p in points if "Average" in p]
    maxs = [p["Maximum"] for p in points if "Maximum" in p]
    return {
        "avg_cpu_pct": round(sum(avgs) / len(avgs), 2) if avgs else None,
        "max_cpu_pct": round(max(maxs), 2) if maxs else None,
        "datapoints": len(points),
        "lookback_days": METRIC_LOOKBACK_DAYS,
    }


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
    cw = _client("cloudwatch", region)
    out: list[dict] = []
    paginator = ec2.get_paginator("describe_instances")
    for page in paginator.paginate():
        for reservation in page["Reservations"]:
            for inst in reservation["Instances"]:
                tags = _tags_to_dict(inst.get("Tags"))
                state = inst.get("State", {}).get("Name")
                config = {
                    "state": state,
                    "launch_time": str(inst.get("LaunchTime")),
                    "az": inst.get("Placement", {}).get("AvailabilityZone"),
                    "platform": inst.get("PlatformDetails"),
                }
                # Only running instances have meaningful live CPU; a stopped one
                # isn't billed for compute, so utilization is irrelevant there.
                if state == "running":
                    metrics = _ec2_cpu_utilization(cw, inst["InstanceId"])
                    if metrics:
                        config["cpu_utilization"] = metrics
                out.append(
                    {
                        "service": "EC2",
                        "category": "Compute",
                        "resource_id": inst["InstanceId"],
                        "name": _name_from_tags(tags, inst["InstanceId"]),
                        "region": region,
                        "type": inst.get("InstanceType"),
                        "config": config,
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
# Additional scanners — popular, cost-relevant services. Each follows the same  #
# uniform shape and degrades on per-resource errors.                            #
# --------------------------------------------------------------------------- #
def scan_ebs_snapshots(region: str | None) -> list[dict]:
    """Self-owned EBS snapshots — old/orphaned snapshots are a common cost leak."""
    ec2 = _client("ec2", region)
    out: list[dict] = []
    paginator = ec2.get_paginator("describe_snapshots")
    for page in paginator.paginate(OwnerIds=["self"]):
        for snap in page["Snapshots"]:
            tags = _tags_to_dict(snap.get("Tags"))
            out.append(
                {
                    "service": "EBS Snapshot",
                    "category": "Storage",
                    "resource_id": snap["SnapshotId"],
                    "name": _name_from_tags(tags, snap["SnapshotId"]),
                    "region": region,
                    "type": "snapshot",
                    "config": {
                        "volume_size_gb": snap.get("VolumeSize"),
                        "start_time": str(snap.get("StartTime")),
                        "source_volume": snap.get("VolumeId"),
                        "state": snap.get("State"),
                    },
                    "tags": tags,
                }
            )
    return out


def scan_eip(region: str | None) -> list[dict]:
    """Elastic IPs — an UNASSOCIATED EIP is billed hourly for doing nothing."""
    ec2 = _client("ec2", region)
    out: list[dict] = []
    for addr in ec2.describe_addresses().get("Addresses", []):
        tags = _tags_to_dict(addr.get("Tags"))
        associated = bool(addr.get("AssociationId"))
        out.append(
            {
                "service": "Elastic IP",
                "category": "Network",
                "resource_id": addr.get("AllocationId") or addr.get("PublicIp"),
                "name": _name_from_tags(tags, addr.get("PublicIp", "eip")),
                "region": region,
                "type": "elastic-ip",
                "config": {
                    "public_ip": addr.get("PublicIp"),
                    "associated": associated,  # False = wasted spend
                    "associated_instance": addr.get("InstanceId"),
                },
                "tags": tags,
            }
        )
    return out


def scan_load_balancers(region: str | None) -> list[dict]:
    """ELBv2 (ALB/NLB) — load balancers with no targets are idle but billed."""
    elb = _client("elbv2", region)
    out: list[dict] = []
    paginator = elb.get_paginator("describe_load_balancers")
    for page in paginator.paginate():
        for lb in page["LoadBalancers"]:
            arn = lb["LoadBalancerArn"]
            target_count = None
            try:
                tgs = elb.describe_target_groups(LoadBalancerArn=arn).get("TargetGroups", [])
                target_count = 0
                for tg in tgs:
                    h = elb.describe_target_health(TargetGroupArn=tg["TargetGroupArn"])
                    target_count += len(h.get("TargetHealthDescriptions", []))
            except ClientError:
                pass
            out.append(
                {
                    "service": "Load Balancer",
                    "category": "Network",
                    "resource_id": lb["LoadBalancerName"],
                    "name": lb["LoadBalancerName"],
                    "region": region,
                    "type": lb.get("Type"),  # application / network / gateway
                    "config": {
                        "scheme": lb.get("Scheme"),
                        "state": lb.get("State", {}).get("Code"),
                        "target_count": target_count,  # 0 = idle, likely wasted
                    },
                    "tags": {},
                }
            )
    return out


def scan_nat_gateways(region: str | None) -> list[dict]:
    """NAT gateways — expensive (hourly + data). An unused one is real money."""
    ec2 = _client("ec2", region)
    out: list[dict] = []
    paginator = ec2.get_paginator("describe_nat_gateways")
    for page in paginator.paginate():
        for nat in page["NatGateways"]:
            tags = _tags_to_dict(nat.get("Tags"))
            out.append(
                {
                    "service": "NAT Gateway",
                    "category": "Network",
                    "resource_id": nat["NatGatewayId"],
                    "name": _name_from_tags(tags, nat["NatGatewayId"]),
                    "region": region,
                    "type": "nat-gateway",
                    "config": {
                        "state": nat.get("State"),
                        "vpc_id": nat.get("VpcId"),
                        "subnet_id": nat.get("SubnetId"),
                    },
                    "tags": tags,
                }
            )
    return out


def scan_lambda(region: str | None) -> list[dict]:
    """Lambda functions — oversized memory or unused functions add up at scale."""
    lam = _client("lambda", region)
    out: list[dict] = []
    paginator = lam.get_paginator("list_functions")
    for page in paginator.paginate():
        for fn in page["Functions"]:
            out.append(
                {
                    "service": "Lambda",
                    "category": "Compute",
                    "resource_id": fn["FunctionName"],
                    "name": fn["FunctionName"],
                    "region": region,
                    "type": fn.get("Runtime"),
                    "config": {
                        "memory_mb": fn.get("MemorySize"),
                        "timeout_s": fn.get("Timeout"),
                        "last_modified": fn.get("LastModified"),
                        "architecture": (fn.get("Architectures") or [None])[0],
                    },
                    "tags": {},
                }
            )
    return out


def scan_ecs(region: str | None) -> list[dict]:
    """ECS clusters — running services/tasks drive Fargate/EC2 compute cost."""
    ecs = _client("ecs", region)
    out: list[dict] = []
    cluster_arns = []
    paginator = ecs.get_paginator("list_clusters")
    for page in paginator.paginate():
        cluster_arns.extend(page.get("clusterArns", []))
    if not cluster_arns:
        return out
    desc = ecs.describe_clusters(clusters=cluster_arns).get("clusters", [])
    for c in desc:
        out.append(
            {
                "service": "ECS",
                "category": "Compute",
                "resource_id": c["clusterName"],
                "name": c["clusterName"],
                "region": region,
                "type": "ecs-cluster",
                "config": {
                    "status": c.get("status"),
                    "running_tasks": c.get("runningTasksCount"),
                    "active_services": c.get("activeServicesCount"),
                    "registered_instances": c.get("registeredContainerInstancesCount"),
                },
                "tags": {},
            }
        )
    return out


def scan_eks(region: str | None) -> list[dict]:
    """EKS clusters — the control plane is billed hourly per cluster."""
    eks = _client("eks", region)
    out: list[dict] = []
    paginator = eks.get_paginator("list_clusters")
    names: list[str] = []
    for page in paginator.paginate():
        names.extend(page.get("clusters", []))
    for name in names:
        try:
            c = eks.describe_cluster(name=name)["cluster"]
        except ClientError:
            continue
        out.append(
            {
                "service": "EKS",
                "category": "Compute",
                "resource_id": name,
                "name": name,
                "region": region,
                "type": "eks-cluster",
                "config": {
                    "status": c.get("status"),
                    "version": c.get("version"),
                },
                "tags": c.get("tags", {}) or {},
            }
        )
    return out


def scan_dynamodb(region: str | None) -> list[dict]:
    """DynamoDB tables — provisioned-but-underused capacity wastes money."""
    ddb = _client("dynamodb", region)
    out: list[dict] = []
    paginator = ddb.get_paginator("list_tables")
    names: list[str] = []
    for page in paginator.paginate():
        names.extend(page.get("TableNames", []))
    for name in names:
        try:
            t = ddb.describe_table(TableName=name)["Table"]
        except ClientError:
            continue
        billing = t.get("BillingModeSummary", {}).get("BillingMode", "PROVISIONED")
        prov = t.get("ProvisionedThroughput", {})
        out.append(
            {
                "service": "DynamoDB",
                "category": "Database",
                "resource_id": name,
                "name": name,
                "region": region,
                "type": billing,  # PROVISIONED / PAY_PER_REQUEST
                "config": {
                    "item_count": t.get("ItemCount"),
                    "size_bytes": t.get("TableSizeBytes"),
                    "read_capacity": prov.get("ReadCapacityUnits"),
                    "write_capacity": prov.get("WriteCapacityUnits"),
                },
                "tags": {},
            }
        )
    return out


def scan_elasticache(region: str | None) -> list[dict]:
    """ElastiCache clusters (Redis/Memcached) — idle/oversized nodes cost money."""
    ec = _client("elasticache", region)
    out: list[dict] = []
    paginator = ec.get_paginator("describe_cache_clusters")
    for page in paginator.paginate():
        for c in page["CacheClusters"]:
            out.append(
                {
                    "service": "ElastiCache",
                    "category": "Database",
                    "resource_id": c["CacheClusterId"],
                    "name": c["CacheClusterId"],
                    "region": region,
                    "type": c.get("CacheNodeType"),
                    "config": {
                        "engine": c.get("Engine"),
                        "status": c.get("CacheClusterStatus"),
                        "num_nodes": c.get("NumCacheNodes"),
                    },
                    "tags": {},
                }
            )
    return out


def scan_ebs_unused_amis(region: str | None) -> list[dict]:
    """Self-owned AMIs — each retains backing snapshots that cost storage."""
    ec2 = _client("ec2", region)
    out: list[dict] = []
    images = ec2.describe_images(Owners=["self"]).get("Images", [])
    for img in images:
        tags = _tags_to_dict(img.get("Tags"))
        out.append(
            {
                "service": "AMI",
                "category": "Storage",
                "resource_id": img["ImageId"],
                "name": _name_from_tags(tags, img.get("Name") or img["ImageId"]),
                "region": region,
                "type": "ami",
                "config": {
                    "creation_date": img.get("CreationDate"),
                    "state": img.get("State"),
                    "snapshot_count": len(
                        [b for b in img.get("BlockDeviceMappings", []) if b.get("Ebs")]
                    ),
                },
                "tags": tags,
            }
        )
    return out


def scan_cloudwatch_log_groups(region: str | None) -> list[dict]:
    """CloudWatch log groups with NO retention keep logs forever — silent cost."""
    logs = _client("logs", region)
    out: list[dict] = []
    paginator = logs.get_paginator("describe_log_groups")
    for page in paginator.paginate():
        for lg in page["logGroups"]:
            out.append(
                {
                    "service": "CloudWatch Logs",
                    "category": "Monitoring",
                    "resource_id": lg["logGroupName"],
                    "name": lg["logGroupName"],
                    "region": region,
                    "type": "log-group",
                    "config": {
                        "stored_bytes": lg.get("storedBytes"),
                        "retention_days": lg.get("retentionInDays"),  # None = never expires
                    },
                    "tags": {},
                }
            )
    return out


def scan_elb_classic(region: str | None) -> list[dict]:
    """Classic Load Balancers (ELB) — legacy, often forgotten and idle."""
    elb = _client("elb", region)
    out: list[dict] = []
    paginator = elb.get_paginator("describe_load_balancers")
    for page in paginator.paginate():
        for lb in page["LoadBalancerDescriptions"]:
            out.append(
                {
                    "service": "Classic LB",
                    "category": "Network",
                    "resource_id": lb["LoadBalancerName"],
                    "name": lb["LoadBalancerName"],
                    "region": region,
                    "type": "classic-elb",
                    "config": {
                        "scheme": lb.get("Scheme"),
                        "instance_count": len(lb.get("Instances", [])),  # 0 = idle
                    },
                    "tags": {},
                }
            )
    return out


# --------------------------------------------------------------------------- #
# The registry. This IS the "list of scannable services" the API exposes.      #
# --------------------------------------------------------------------------- #
SERVICE_REGISTRY: dict[str, dict] = {
    # Compute
    "ec2": {"label": "EC2", "category": "Compute", "scan": scan_ec2, "regional": True},
    "lambda": {"label": "Lambda", "category": "Compute", "scan": scan_lambda, "regional": True},
    "ecs": {"label": "ECS", "category": "Compute", "scan": scan_ecs, "regional": True},
    "eks": {"label": "EKS", "category": "Compute", "scan": scan_eks, "regional": True},
    # Storage
    "ebs": {"label": "EBS", "category": "Storage", "scan": scan_ebs, "regional": True},
    "ebs_snapshots": {"label": "EBS Snapshots", "category": "Storage", "scan": scan_ebs_snapshots, "regional": True},
    "ami": {"label": "AMIs", "category": "Storage", "scan": scan_ebs_unused_amis, "regional": True},
    "s3": {"label": "S3", "category": "Storage", "scan": scan_s3, "regional": False},
    # Database
    "rds": {"label": "RDS", "category": "Database", "scan": scan_rds, "regional": True},
    "dynamodb": {"label": "DynamoDB", "category": "Database", "scan": scan_dynamodb, "regional": True},
    "elasticache": {"label": "ElastiCache", "category": "Database", "scan": scan_elasticache, "regional": True},
    # Network
    "eip": {"label": "Elastic IPs", "category": "Network", "scan": scan_eip, "regional": True},
    "elb": {"label": "Load Balancers", "category": "Network", "scan": scan_load_balancers, "regional": True},
    "elb_classic": {"label": "Classic LBs", "category": "Network", "scan": scan_elb_classic, "regional": True},
    "nat": {"label": "NAT Gateways", "category": "Network", "scan": scan_nat_gateways, "regional": True},
    # Monitoring
    "logs": {"label": "CloudWatch Logs", "category": "Monitoring", "scan": scan_cloudwatch_log_groups, "regional": True},
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
