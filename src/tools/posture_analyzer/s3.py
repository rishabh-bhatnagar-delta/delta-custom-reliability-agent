from typing import List, Dict, Any

from src.models.resiliency_report import (
    ResourceResilienceOutput,
    ResiliencyReport,
    ResilienceGap,
)


def get_s3_resilience_report(bucket_name: str, dimensions: List[Dict[str, Any]]) -> ResourceResilienceOutput:
    """Rule-based resilience evaluation for S3."""
    dim_map = {d["name"]: d.get("value") for d in dimensions}

    gaps: List[ResilienceGap] = []
    recommendations: List[str] = []
    cli_commands: List[str] = []
    score = 10

    # 1. Versioning
    versioning = dim_map.get("Versioning", "Disabled")
    if versioning != "Enabled":
        score -= 2
        gaps.append(ResilienceGap(
            name="Versioning",
            status="DISABLED",
            impact="Overwritten or deleted objects cannot be recovered.",
        ))
        recommendations.append("Enable versioning to protect against accidental deletes and overwrites.")
        cli_commands.append(
            f"aws s3api put-bucket-versioning --bucket {bucket_name} "
            f"--versioning-configuration Status=Enabled"
        )

    # 2. MFA Delete
    if not dim_map.get("MFA Delete", False):
        score -= 1
        gaps.append(ResilienceGap(
            name="MFA Delete",
            status="DISABLED",
            impact="Versioned objects can be permanently deleted without MFA.",
        ))
        recommendations.append("Enable MFA Delete for an extra layer of protection on version deletes.")

    # 3. Cross-Region Replication
    if not dim_map.get("MultiRegion", False):
        score -= 2
        gaps.append(ResilienceGap(
            name="Cross-Region Replication",
            status="NOT CONFIGURED",
            impact="No multi-region redundancy; regional outage risks data unavailability.",
        ))
        recommendations.append("Configure cross-region replication (CRR) for disaster recovery.")

    # 4. Object Lock
    if not dim_map.get("ObjectLock", False):
        gaps.append(ResilienceGap(
            name="Object Lock",
            status="DISABLED",
            impact="Objects are not immutable; can be modified or deleted.",
        ))
        recommendations.append("Consider enabling Object Lock for compliance or write-once data.")

    # 5. Scheduled Backup
    if not dim_map.get("ScheduledBackup", False):
        score -= 1
        gaps.append(ResilienceGap(
            name="Scheduled Backup (AWS Backup)",
            status="NOT CONFIGURED",
            impact="No scheduled backups via AWS Backup; relies solely on versioning.",
        ))
        recommendations.append("Configure AWS Backup for scheduled S3 backups.")

    # 6. Point-in-Time Recovery
    if not dim_map.get("PointInTimeRecovery", False):
        score -= 1
        gaps.append(ResilienceGap(
            name="Point-in-Time Recovery",
            status="DISABLED",
            impact="Cannot restore bucket to a specific point in time.",
        ))
        recommendations.append("Enable versioning + AWS Backup for point-in-time recovery capability.")

    # 7. Data Replication details
    replication = dim_map.get("DataReplication", [])
    if isinstance(replication, list) and replication:
        rtc_enabled = any(r.get("RTCEnabled") for r in replication if isinstance(r, dict))
        if not rtc_enabled:
            gaps.append(ResilienceGap(
                name="Replication Time Control (RTC)",
                status="DISABLED",
                impact="Replication exists but no SLA guarantee on replication time.",
            ))
            recommendations.append("Enable RTC for predictable replication within 15 minutes.")

    # 8. Cross-Region Backup
    if not dim_map.get("CrossRegionBackup", False):
        gaps.append(ResilienceGap(
            name="Cross-Region Backup",
            status="NOT CONFIGURED",
            impact="Backups are in the same region; regional disaster risks backup loss.",
        ))
        recommendations.append("Configure cross-region backup copies in AWS Backup.")

    # 9. Inventory
    inv_count = dim_map.get("InventoryConfigs", 0)
    if not inv_count:
        gaps.append(ResilienceGap(
            name="S3 Inventory",
            status="NOT CONFIGURED",
            impact="No inventory reports; difficult to audit object status and replication.",
        ))
        recommendations.append("Configure S3 Inventory for object-level auditing.")

    score = max(0, min(10, score))

    total_issues = len([g for g in gaps if g.status not in ("ENABLED",)])
    if score >= 8:
        summary = f"S3 bucket '{bucket_name}' has a strong reliability posture with {total_issues} minor gap(s)."
    elif score >= 5:
        summary = f"S3 bucket '{bucket_name}' has moderate reliability risks. {total_issues} gap(s) need attention."
    else:
        summary = f"S3 bucket '{bucket_name}' has significant reliability gaps. {total_issues} issue(s) require remediation."

    return ResourceResilienceOutput(
        recommendations=recommendations,
        aws_commands_to_fix=cli_commands,
        report=ResiliencyReport(
            resource_name=bucket_name,
            resilience_gaps=gaps,
            overall_resilience_score=score,
            max_resilience_score=10,
            summary=summary,
        ),
    )
