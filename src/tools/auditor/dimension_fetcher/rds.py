from datetime import datetime
from typing import Any, List

from src.models.dimensions import DimensionFetcher, DimensionSupportedResource
from src.models.resources import DimensionOutput


def _sanitize(obj: Any) -> Any:
    """Recursively convert datetime objects to ISO-format strings."""
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_sanitize(i) for i in obj]
    return obj


class RDSDimensionFetcher(DimensionFetcher):
    def get_resource_enum(self) -> DimensionSupportedResource:
        return DimensionSupportedResource.RDS

    def _fetch_dimensions(self, physical_id) -> List[DimensionOutput]:
        dimensions: List[DimensionOutput] = []

        rds_client = self.get_aws_client_for_resource()

        # Try as DB instance first
        db_instance = None
        try:
            resp = rds_client.describe_db_instances(DBInstanceIdentifier=physical_id)
            instances = _sanitize(resp.get('DBInstances', []))
            if instances:
                db_instance = instances[0]
        except Exception:
            pass

        # Try as DB cluster if not found as instance
        db_cluster = None
        cluster_id = None
        if db_instance:
            cluster_id = db_instance.get('DBClusterIdentifier')
        else:
            cluster_id = physical_id

        if cluster_id:
            try:
                cl_resp = rds_client.describe_db_clusters(DBClusterIdentifier=cluster_id)
                clusters = _sanitize(cl_resp.get('DBClusters', []))
                if clusters:
                    db_cluster = clusters[0]
            except Exception:
                pass

        if db_instance:
            self._fetch_instance_dimensions(db_instance, dimensions)
        if db_cluster:
            self._fetch_cluster_dimensions(db_cluster, rds_client, dimensions)

        return dimensions

    def _fetch_instance_dimensions(self, db_instance: dict, dimensions: List[DimensionOutput]):
        # Multi AZ
        dimensions.append(DimensionOutput(name='MultiAZ', value=db_instance.get('MultiAZ', False)))

        # Read Replicas
        dimensions.append(DimensionOutput(name='ReadReplicaIDs', value=db_instance.get('ReadReplicaDBInstanceIdentifiers', [])))

        # Backup Retention Period
        dimensions.append(DimensionOutput(
            name='BackupRetentionPeriod',
            value=db_instance.get('BackupRetentionPeriod', 0)
        ))

        # Point-in-Time Recovery
        pitr_enabled = (db_instance.get('BackupRetentionPeriod', 0) > 0
                        and db_instance.get('LatestRestorableTime') is not None)
        dimensions.append(DimensionOutput(name='PointInTimeRecovery', value=pitr_enabled))

        # Automated Backups
        dimensions.append(DimensionOutput(
            name='AutomatedBackups',
            value=db_instance.get('BackupRetentionPeriod', 0) > 0
        ))

        # Deletion Protection
        dimensions.append(DimensionOutput(
            name='DeletionProtection',
            value=db_instance.get('DeletionProtection', False)
        ))

        # Minor Version Upgrade
        dimensions.append(DimensionOutput(
            name='MinorVersionUpgrade',
            value=db_instance.get('AutoMinorVersionUpgrade', False)
        ))

        # Maintenance Window
        dimensions.append(DimensionOutput(
            name='MaintenanceWindow',
            value=db_instance.get('PreferredMaintenanceWindow')
        ))

    def _fetch_cluster_dimensions(self, db_cluster: dict, rds_client, dimensions: List[DimensionOutput]):
        # Cluster members
        members = db_cluster.get('DBClusterMembers', [])
        writers = [m for m in members if m.get('IsClusterWriter')]
        readers = [m for m in members if not m.get('IsClusterWriter')]

        dimensions.append(DimensionOutput(name='ClusterIdentifier', value=db_cluster.get('DBClusterIdentifier')))
        dimensions.append(DimensionOutput(name='ClusterEngine', value=db_cluster.get('Engine')))
        dimensions.append(DimensionOutput(name='ClusterWriters', value=len(writers)))
        dimensions.append(DimensionOutput(name='ClusterReaders', value=len(readers)))
        dimensions.append(DimensionOutput(name='ReaderEndpoint', value=db_cluster.get('ReaderEndpoint')))

        # Global Database detection
        global_cluster_id = db_cluster.get('GlobalClusterIdentifier') or db_cluster.get('GlobalClusterResourceId')
        dimensions.append(DimensionOutput(name='GlobalClusterIdentifier', value=global_cluster_id))
        if global_cluster_id:
            # Fetch global cluster details
            try:
                gc_resp = rds_client.describe_global_clusters(GlobalClusterIdentifier=global_cluster_id)
                gc_list = _sanitize(gc_resp.get('GlobalClusters', []))
                if gc_list:
                    gc = gc_list[0]
                    gc_members = gc.get('GlobalClusterMembers', [])
                    dimensions.append(DimensionOutput(name='GlobalClusterMembers', value=[
                        {
                            'DBClusterArn': m.get('DBClusterArn'),
                            'IsWriter': m.get('IsWriter', False),
                            'GlobalWriteForwardingStatus': m.get('GlobalWriteForwardingStatus'),
                        } for m in gc_members
                    ]))
            except Exception:
                pass
        else:
            pass
