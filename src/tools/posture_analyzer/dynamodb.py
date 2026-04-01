from typing import List, Dict, Any

from src.models.resiliency_report import ResourceResilienceOutput
from src.tools.posture_analyzer.base import ResilienceAnalyzer


def get_dynamodb_resilience_report(dimensions: List[Dict[str, Any]]) -> ResourceResilienceOutput:
    """Rule-based resilience evaluation for DynamoDB."""
    dim_map = {d["name"]: d.get("value") for d in dimensions}
    resource_name = dim_map.get("ResourceName", "Unknown DynamoDB Table")
    a = ResilienceAnalyzer(resource_name, dimensions)

    # Failover Configuration
    global_regions = a.dim("GlobalTableRegions", [])
    if global_regions:
        a.add_gap("Failover Configuration", "ACTIVE-ACTIVE",
                   f"Global Table with replicas in {global_regions}. Multi-region active-active reads/writes.",
                   penalty=0)
    else:
        a.add_gap("Failover Configuration", "NO FAILOVER",
                   f"GlobalTableRegions is empty. Single-region table with no cross-region replication.",
                   penalty=0)

    if not a.dim("DeletionProtection"):
        a.add_gap("Deletion Protection", "DISABLED",
                   "Table can be accidentally deleted, causing total data loss.",
                   penalty=2, recommendation="Enable deletion protection to prevent accidental table removal.",
                   cli=f"aws dynamodb update-table --table-name {resource_name} --deletion-protection-enabled")

    pitr = a.dim("PointInTimeRecovery", {})
    pitr_status = pitr.get("PointInTimeRecoveryStatus", "DISABLED") if isinstance(pitr, dict) else "DISABLED"
    if pitr_status != "ENABLED":
        a.add_gap("Point-in-Time Recovery", "DISABLED",
                   "Cannot restore table to a specific point in time after corruption or accidental writes.",
                   penalty=2, recommendation="Enable PITR for continuous backup and granular recovery.",
                   cli=f"aws dynamodb update-continuous-backups --table-name {resource_name} --point-in-time-recovery-specification PointInTimeRecoveryEnabled=true")

    if not a.dim("GlobalTableRegions", []):
        a.add_gap("Multi-Region (Global Tables)", "NOT CONFIGURED",
                   "Single-region deployment; a regional outage causes full unavailability.",
                   penalty=2, recommendation="Configure Global Tables for multi-region redundancy and lower read latency.",
                   cli=f"aws dynamodb update-table --table-name {resource_name} --replica-updates '[{{\"Create\": {{\"RegionName\": \"us-west-2\"}}}}]'")

    if not a.dim("AutoScaling", []):
        a.add_gap("Auto Scaling", "NOT CONFIGURED",
                   "Table cannot adapt to traffic spikes; risk of throttling under load.",
                   penalty=2, recommendation="Enable auto scaling or switch to on-demand capacity mode.",
                   cli=f"aws dynamodb update-table --table-name {resource_name} --billing-mode PAY_PER_REQUEST")

    streams = a.dim("StreamsConfiguration", {})
    stream_enabled = streams.get("StreamEnabled", False) if isinstance(streams, dict) else False
    if not stream_enabled:
        a.add_gap("DynamoDB Streams", "DISABLED",
                   "No change data capture; limits event-driven recovery and replication patterns.",
                   penalty=1, recommendation="Enable DynamoDB Streams for change data capture and event-driven processing.")

    if not a.dim("SecondaryIndexes", []):
        a.add_gap("Secondary Indexes (GSI)", "NONE",
                   "No GSIs configured; query flexibility is limited to primary key patterns.")

    return a.build(f"Table '{resource_name}'")
