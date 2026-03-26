from typing import List, Dict, Any

from src.models.resiliency_report import (
    ResourceResilienceOutput,
    ResiliencyReport,
    ResilienceGap,
)


def get_dynamodb_resilience_report(dimensions: List[Dict[str, Any]]) -> ResourceResilienceOutput:
    """
    Rule-based resilience evaluation for DynamoDB against
    AWS Well-Architected Reliability Pillar standards.
    """
    dim_map = {d["name"]: d.get("value") for d in dimensions}
    resource_name = dim_map.get("ResourceName", "Unknown DynamoDB Table")

    gaps: List[ResilienceGap] = []
    recommendations: List[str] = []
    cli_commands: List[str] = []
    score = 10  # start perfect, deduct for issues

    # 1. Deletion Protection
    if not dim_map.get("DeletionProtection"):
        score -= 2
        gaps.append(ResilienceGap(
            name="Deletion Protection",
            status="DISABLED",
            impact="Table can be accidentally deleted, causing total data loss.",
        ))
        recommendations.append("Enable deletion protection to prevent accidental table removal.")
        cli_commands.append(
            f"aws dynamodb update-table --table-name {resource_name} --deletion-protection-enabled"
        )

    # 2. Point-in-Time Recovery
    pitr = dim_map.get("PointInTimeRecovery", {})
    pitr_status = pitr.get("PointInTimeRecoveryStatus", "DISABLED") if isinstance(pitr, dict) else "DISABLED"
    if pitr_status != "ENABLED":
        score -= 2
        gaps.append(ResilienceGap(
            name="Point-in-Time Recovery",
            status="DISABLED",
            impact="Cannot restore table to a specific point in time after corruption or accidental writes.",
        ))
        recommendations.append("Enable PITR for continuous backup and granular recovery.")
        cli_commands.append(
            f"aws dynamodb update-continuous-backups --table-name {resource_name} "
            f"--point-in-time-recovery-specification PointInTimeRecoveryEnabled=true"
        )

    # 3. Global Table / Multi-Region
    regions = dim_map.get("GlobalTableRegions", [])
    if not regions:
        score -= 2
        gaps.append(ResilienceGap(
            name="Multi-Region (Global Tables)",
            status="NOT CONFIGURED",
            impact="Single-region deployment; a regional outage causes full unavailability.",
        ))
        recommendations.append("Configure Global Tables for multi-region redundancy and lower read latency.")
        cli_commands.append(
            f"aws dynamodb update-table --table-name {resource_name} "
            f"--replica-updates '[{{\"Create\": {{\"RegionName\": \"us-west-2\"}}}}]'"
        )

    # 4. Auto Scaling
    auto_scaling = dim_map.get("AutoScaling", [])
    if not auto_scaling:
        score -= 2
        gaps.append(ResilienceGap(
            name="Auto Scaling",
            status="NOT CONFIGURED",
            impact="Table cannot adapt to traffic spikes; risk of throttling under load.",
        ))
        recommendations.append(
            "Enable auto scaling or switch to on-demand capacity mode to handle variable workloads."
        )
        cli_commands.append(
            f"aws dynamodb update-table --table-name {resource_name} "
            f"--billing-mode PAY_PER_REQUEST"
        )

    # 5. DynamoDB Streams
    streams = dim_map.get("StreamsConfiguration", {})
    stream_enabled = streams.get("StreamEnabled", False) if isinstance(streams, dict) else False
    if stream_enabled:
        gaps.append(ResilienceGap(
            name="DynamoDB Streams",
            status="ENABLED",
            impact="Positive: enables event-driven architectures and change data capture.",
        ))
    else:
        score -= 1
        gaps.append(ResilienceGap(
            name="DynamoDB Streams",
            status="DISABLED",
            impact="No change data capture; limits event-driven recovery and replication patterns.",
        ))
        recommendations.append("Enable DynamoDB Streams for change data capture and event-driven processing.")

    # 6. Secondary Indexes
    indexes = dim_map.get("SecondaryIndexes", [])
    if not indexes:
        gaps.append(ResilienceGap(
            name="Secondary Indexes (GSI)",
            status="NONE",
            impact="No GSIs configured; query flexibility is limited to primary key patterns.",
        ))

    # Clamp score
    score = max(0, min(10, score))

    # Build summary
    total_issues = len([g for g in gaps if g.status not in ("ENABLED",)])
    if score >= 8:
        summary = f"Table '{resource_name}' has a strong reliability posture with {total_issues} minor gap(s)."
    elif score >= 5:
        summary = f"Table '{resource_name}' has moderate reliability risks. {total_issues} gap(s) need attention."
    else:
        summary = f"Table '{resource_name}' has significant reliability gaps. {total_issues} issue(s) require immediate remediation."

    return ResourceResilienceOutput(
        recommendations=recommendations,
        aws_commands_to_fix=cli_commands,
        report=ResiliencyReport(
            resource_name=resource_name,
            resilience_gaps=gaps,
            overall_resilience_score=score,
            max_resilience_score=10,
            summary=summary,
        ),
    )
