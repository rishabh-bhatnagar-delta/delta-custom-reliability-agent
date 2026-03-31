from typing import List, Dict, Any

from src.models.resiliency_report import ResourceResilienceOutput
from src.tools.posture_analyzer.base import ResilienceAnalyzer


def get_rds_resilience_report(db_instance_id: str, dimensions: List[Dict[str, Any]]) -> ResourceResilienceOutput:
    """Rule-based resilience evaluation for RDS."""
    a = ResilienceAnalyzer(db_instance_id, dimensions)

    _classify_failover_config(a)
    _analyze_instance(a, db_instance_id)
    _analyze_cluster(a)
    _analyze_global_db(a)

    return a.build(f"RDS '{db_instance_id}'")


def _classify_failover_config(a: ResilienceAnalyzer):
    """
    Classify RDS failover configuration.

    Logic:
    ┌─ Global Database with secondary region?
    │   └─ ACTIVE-ACTIVE: cross-region replication with failover capability.
    │
    ├─ Aurora cluster with >=2 readers + Multi-AZ?
    │   └─ ACTIVE-ACTIVE: multiple readers actively serving traffic across AZs.
    │
    ├─ Multi-AZ enabled (standalone or cluster)?
    │   └─ ACTIVE-PASSIVE: synchronous standby in another AZ, doesn't serve traffic.
    │
    ├─ Read replicas but no Multi-AZ?
    │   └─ ACTIVE-PASSIVE: replicas serve reads but writer failover requires manual promotion.
    │
    └─ Single instance, no Multi-AZ, no replicas?
        └─ SILOED: single point of failure.
    """
    gc_members = a.dim("GlobalClusterMembers", [])
    has_secondary_region = any(not m.get("IsWriter") for m in gc_members) if gc_members else False

    cluster_id = a.dim("ClusterIdentifier")
    cluster_readers = a.dim("ClusterReaders", 0)
    multi_az = a.dim("MultiAZ", False)
    read_replicas = a.dim("ReadReplicaIDs", [])

    if has_secondary_region:
        a.add_gap("Failover Configuration", "ACTIVE-ACTIVE",
                   "Global Database with secondary region; cross-region replication and failover available.",
                   penalty=0)
    elif cluster_id and cluster_readers >= 2 and multi_az:
        a.add_gap("Failover Configuration", "ACTIVE-ACTIVE",
                   "Aurora cluster with multiple readers across AZs; reads distributed, writer fails over automatically.",
                   penalty=0)
    elif multi_az:
        a.add_gap("Failover Configuration", "ACTIVE-PASSIVE",
                   "Multi-AZ enabled; synchronous standby in another AZ with automatic failover. Standby does not serve traffic.",
                   penalty=0)
    elif read_replicas:
        a.add_gap("Failover Configuration", "ACTIVE-PASSIVE",
                   "Read replicas exist but no Multi-AZ; replicas serve reads but writer failover requires manual promotion.",
                   penalty=0)
    else:
        a.add_gap("Failover Configuration", "SILOED",
                   "Single instance with no Multi-AZ and no replicas; single point of failure.",
                   penalty=0)


def _analyze_instance(a: ResilienceAnalyzer, db_id: str):
    # Multi-AZ
    if not a.dim("MultiAZ", False):
        a.add_gap("Multi-AZ Deployment", "DISABLED",
                   "Single-AZ deployment; AZ failure causes downtime.",
                   penalty=2, recommendation="Enable Multi-AZ for automatic failover.",
                   cli=f"aws rds modify-db-instance --db-instance-identifier {db_id} --multi-az --apply-immediately")

    # Backup Retention
    retention = a.dim("BackupRetentionPeriod", 0)
    if retention == 0:
        a.add_gap("Automated Backups", "DISABLED",
                   "No automated backups; data loss risk on failure.",
                   penalty=2, recommendation="Enable automated backups with adequate retention period.",
                   cli=f"aws rds modify-db-instance --db-instance-identifier {db_id} --backup-retention-period 7 --apply-immediately")
    elif retention < 7:
        a.add_gap("Backup Retention Period", f"{retention} days",
                   "Short retention window; limits recovery options for older data.",
                   penalty=1, recommendation="Increase backup retention to at least 7 days.")

    # Point-in-Time Recovery
    if not a.dim("PointInTimeRecovery", False):
        a.add_gap("Point-in-Time Recovery", "DISABLED",
                   "Cannot restore to a specific second; limits recovery from corruption.",
                   penalty=2, recommendation="Enable PITR by setting backup retention period > 0.")

    # Deletion Protection
    if not a.dim("DeletionProtection", False):
        a.add_gap("Deletion Protection", "DISABLED",
                   "Instance can be accidentally deleted.",
                   penalty=1, recommendation="Enable deletion protection.",
                   cli=f"aws rds modify-db-instance --db-instance-identifier {db_id} --deletion-protection --apply-immediately")

    # Read Replicas
    if not a.dim("ReadReplicaIDs", []):
        a.add_gap("Read Replicas", "NONE",
                   "No read replicas; no read scaling or cross-region disaster recovery.",
                   penalty=1, recommendation="Create read replicas for read scaling and cross-region DR.",
                   cli=f"aws rds create-db-instance-read-replica --db-instance-identifier {db_id}-replica --source-db-instance-identifier {db_id}")

    # Minor Version Upgrade
    if not a.dim("MinorVersionUpgrade", False):
        a.add_gap("Auto Minor Version Upgrade", "DISABLED",
                   "Missing security patches and bug fixes from minor version updates.",
                   recommendation="Enable automatic minor version upgrades.",
                   cli=f"aws rds modify-db-instance --db-instance-identifier {db_id} --auto-minor-version-upgrade --apply-immediately")

    # Maintenance Window
    if not a.dim("MaintenanceWindow"):
        a.add_gap("Maintenance Window", "NOT SET",
                   "AWS chooses maintenance window; may conflict with peak traffic.",
                   recommendation="Set a preferred maintenance window during low-traffic hours.")


def _analyze_cluster(a: ResilienceAnalyzer):
    cluster_id = a.dim("ClusterIdentifier")
    if cluster_id is None:
        return

    readers = a.dim("ClusterReaders", 0)
    if readers >= 2:
        pass  # Active-Active for reads
    elif readers == 1:
        a.add_gap("Multi-AZ Cluster Readers", "1 READER",
                   "Single reader instance; read failover has limited capacity.",
                   recommendation="Add a second reader to the cluster for Active-Active read capability.")
    else:
        a.add_gap("Multi-AZ Cluster Readers", "NO READERS",
                   "Writer-only cluster; no read scaling or read failover.",
                   penalty=1, recommendation="Add reader instances to the cluster.")


def _analyze_global_db(a: ResilienceAnalyzer):
    cluster_id = a.dim("ClusterIdentifier")
    global_id = a.dim("GlobalClusterIdentifier")

    if not global_id:
        if cluster_id is not None:
            a.add_gap("Global Database", "NOT CONFIGURED",
                       "Cluster is region-locked; regional outage causes full database unavailability.",
                       recommendation="Configure a Global Database for cross-region disaster recovery.")
        return

    gc_members = a.dim("GlobalClusterMembers", [])
    secondaries = [m for m in gc_members if not m.get("IsWriter")]
    if not secondaries:
        a.add_gap("Global Database", "NO SECONDARY REGION",
                   "Global cluster exists but has no secondary region; no cross-region failover.",
                   recommendation="Add a secondary region to the global database cluster.")
