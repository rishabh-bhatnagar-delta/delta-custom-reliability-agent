from typing import List, Dict, Any

from src.models.resiliency_report import ResourceResilienceOutput
from src.tools.posture_analyzer.base import ResilienceAnalyzer


def get_s3_resilience_report(bucket_name: str, dimensions: List[Dict[str, Any]]) -> ResourceResilienceOutput:
    """Rule-based resilience evaluation for S3."""
    a = ResilienceAnalyzer(bucket_name, dimensions)

    if a.dim("Versioning", "Disabled") != "Enabled":
        a.add_gap("Versioning", "DISABLED",
                   "Overwritten or deleted objects cannot be recovered.",
                   recommendation="Enable versioning to protect against accidental deletes and overwrites.",
                   cli=f"aws s3api put-bucket-versioning --bucket {bucket_name} --versioning-configuration Status=Enabled")

    if not a.dim("MFA Delete", False):
        a.add_gap("MFA Delete", "DISABLED",
                   "Versioned objects can be permanently deleted without MFA.",
                   recommendation="Enable MFA Delete for an extra layer of protection on version deletes.")

    if not a.dim("MultiRegion", False):
        a.add_gap("Cross-Region Replication", "NOT CONFIGURED",
                   "No multi-region redundancy; regional outage risks data unavailability.",
                   recommendation="Configure cross-region replication (CRR) for disaster recovery.")

    if not a.dim("ObjectLock", False):
        a.add_gap("Object Lock", "DISABLED",
                   "Objects are not immutable; can be modified or deleted.",
                   recommendation="Consider enabling Object Lock for compliance or write-once data.")

    if not a.dim("ScheduledBackup", False):
        a.add_gap("Scheduled Backup (AWS Backup)", "NOT CONFIGURED",
                   "No scheduled backups via AWS Backup; relies solely on versioning.",
                   recommendation="Configure AWS Backup for scheduled S3 backups.")

    if not a.dim("PointInTimeRecovery", False):
        a.add_gap("Point-in-Time Recovery", "DISABLED",
                   "Cannot restore bucket to a specific point in time.",
                   recommendation="Enable versioning + AWS Backup for point-in-time recovery capability.")

    replication = a.dim("DataReplication", [])
    if isinstance(replication, list) and replication:
        if not any(r.get("RTCEnabled") for r in replication if isinstance(r, dict)):
            a.add_gap("Replication Time Control (RTC)", "DISABLED",
                       "Replication exists but no SLA guarantee on replication time.",
                       recommendation="Enable RTC for predictable replication within 24 hours.")

    if not a.dim("CrossRegionBackup", False):
        a.add_gap("Cross-Region Backup", "NOT CONFIGURED",
                   "Backups are in the same region; regional disaster risks backup loss.",
                   recommendation="Configure cross-region backup copies in AWS Backup.")

    if not a.dim("InventoryConfigs", 0):
        a.add_gap("S3 Inventory", "NOT CONFIGURED",
                   "No inventory reports; difficult to audit object status and replication.",
                   recommendation="Configure S3 Inventory for object-level auditing.")

    return a.build(f"S3 bucket '{bucket_name}'")
