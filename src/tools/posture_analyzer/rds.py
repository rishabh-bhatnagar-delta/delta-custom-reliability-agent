from typing import List, Dict, Any

from src.models.resiliency_report import (
    ResourceResilienceOutput,
    ResiliencyReport,
    ResilienceGap,
)


def get_rds_resilience_report(db_instance_id: str, dimensions: List[Dict[str, Any]]) -> ResourceResilienceOutput:
    """Rule-based resilience evaluation for RDS."""
    dim_map = {d["name"]: d.get("value") for d in dimensions}

    gaps: List[ResilienceGap] = []
    recommendations: List[str] = []
    cli_commands: List[str] = []
    score = 10

    # 1. Multi-AZ
    if not dim_map.get("MultiAZ", False):
        score -= 2
        gaps.append(ResilienceGap(
            name="Multi-AZ Deployment",
            status="DISABLED",
            impact="Single-AZ deployment; AZ failure causes downtime.",
        ))
        recommendations.append("Enable Multi-AZ for automatic failover.")
        cli_commands.append(
            f"aws rds modify-db-instance --db-instance-identifier {db_instance_id} "
            f"--multi-az --apply-immediately"
        )

    # 2. Backup Retention
    retention = dim_map.get("BackupRetentionPeriod", 0)
    if retention == 0:
        score -= 2
        gaps.append(ResilienceGap(
            name="Automated Backups",
            status="DISABLED",
            impact="No automated backups; data loss risk on failure.",
        ))
        recommendations.append("Enable automated backups with adequate retention period.")
        cli_commands.append(
            f"aws rds modify-db-instance --db-instance-identifier {db_instance_id} "
            f"--backup-retention-period 7 --apply-immediately"
        )
    elif retention < 7:
        score -= 1
        gaps.append(ResilienceGap(
            name="Backup Retention Period",
            status=f"{retention} days",
            impact="Short retention window; limits recovery options for older data.",
        ))
        recommendations.append("Increase backup retention to at least 7 days.")

    # 3. Point-in-Time Recovery
    if not dim_map.get("PointInTimeRecovery", False):
        score -= 2
        gaps.append(ResilienceGap(
            name="Point-in-Time Recovery",
            status="DISABLED",
            impact="Cannot restore to a specific second; limits recovery from corruption.",
        ))
        recommendations.append("Enable PITR by setting backup retention period > 0.")

    # 4. Deletion Protection
    if not dim_map.get("DeletionProtection", False):
        score -= 1
        gaps.append(ResilienceGap(
            name="Deletion Protection",
            status="DISABLED",
            impact="Instance can be accidentally deleted.",
        ))
        recommendations.append("Enable deletion protection.")
        cli_commands.append(
            f"aws rds modify-db-instance --db-instance-identifier {db_instance_id} "
            f"--deletion-protection --apply-immediately"
        )

    # 5. Read Replicas
    replicas = dim_map.get("ReadReplicaIDs", [])
    if not replicas:
        score -= 1
        gaps.append(ResilienceGap(
            name="Read Replicas",
            status="NONE",
            impact="No read replicas; no read scaling or cross-region disaster recovery.",
        ))
        recommendations.append("Create read replicas for read scaling and cross-region DR.")
        cli_commands.append(
            f"aws rds create-db-instance-read-replica "
            f"--db-instance-identifier {db_instance_id}-replica "
            f"--source-db-instance-identifier {db_instance_id}"
        )

    # 6. Minor Version Upgrade
    if not dim_map.get("MinorVersionUpgrade", False):
        gaps.append(ResilienceGap(
            name="Auto Minor Version Upgrade",
            status="DISABLED",
            impact="Missing security patches and bug fixes from minor version updates.",
        ))
        recommendations.append("Enable automatic minor version upgrades.")
        cli_commands.append(
            f"aws rds modify-db-instance --db-instance-identifier {db_instance_id} "
            f"--auto-minor-version-upgrade --apply-immediately"
        )

    # 7. Maintenance Window
    maint = dim_map.get("MaintenanceWindow")
    if not maint:
        gaps.append(ResilienceGap(
            name="Maintenance Window",
            status="NOT SET",
            impact="AWS chooses maintenance window; may conflict with peak traffic.",
        ))
        recommendations.append("Set a preferred maintenance window during low-traffic hours.")

    # 8. Multi-AZ Cluster (readable standbys)
    cluster_readers = dim_map.get("ClusterReaders", 0)
    cluster_id = dim_map.get("ClusterIdentifier")
    if cluster_id is not None:
        if cluster_readers >= 2:
            pass  # Active-Active for reads with Multi-AZ Cluster
        elif cluster_readers == 1:
            gaps.append(ResilienceGap(
                name="Multi-AZ Cluster Readers",
                status="1 READER",
                impact="Single reader instance; read failover has limited capacity.",
            ))
            recommendations.append(
                "Add a second reader to the cluster for Active-Active read capability."
            )
        elif cluster_readers == 0:
            score -= 1
            gaps.append(ResilienceGap(
                name="Multi-AZ Cluster Readers",
                status="NO READERS",
                impact="Writer-only cluster; no read scaling or read failover.",
            ))
            recommendations.append("Add reader instances to the cluster.")

    # 9. Global Database
    global_cluster_id = dim_map.get("GlobalClusterIdentifier")
    if not global_cluster_id:
        if cluster_id is not None:
            gaps.append(ResilienceGap(
                name="Global Database",
                status="NOT CONFIGURED",
                impact="Cluster is region-locked; regional outage causes full database unavailability.",
            ))
            recommendations.append(
                "Configure a Global Database for cross-region disaster recovery."
            )
    else:
        # Check global cluster members
        gc_members = dim_map.get("GlobalClusterMembers", [])
        writers = [m for m in gc_members if m.get("IsWriter")]
        secondaries = [m for m in gc_members if not m.get("IsWriter")]
        if not secondaries:
            gaps.append(ResilienceGap(
                name="Global Database",
                status="NO SECONDARY REGION",
                impact="Global cluster exists but has no secondary region; no cross-region failover.",
            ))
            recommendations.append("Add a secondary region to the global database cluster.")

    score = max(0, min(10, score))

    total_issues = len([g for g in gaps if g.status not in ("ENABLED",)])
    if score >= 8:
        summary = f"RDS '{db_instance_id}' has a strong reliability posture with {total_issues} minor gap(s)."
    elif score >= 5:
        summary = f"RDS '{db_instance_id}' has moderate reliability risks. {total_issues} gap(s) need attention."
    else:
        summary = f"RDS '{db_instance_id}' has significant reliability gaps. {total_issues} issue(s) require remediation."

    return ResourceResilienceOutput(
        recommendations=recommendations,
        aws_commands_to_fix=cli_commands,
        report=ResiliencyReport(
            resource_name=db_instance_id,
            resilience_gaps=gaps,
            overall_resilience_score=score,
            max_resilience_score=10,
            summary=summary,
        ),
    )
