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
        db_instances = rds_client.describe_db_instances()
        for db_instance in _sanitize(db_instances["DBInstances"]):
            # Multi AZ
            dimensions.append(
                DimensionOutput(
                    name='MultiAZ',
                    value=db_instance.get('MultiAZ', False)
                )
            )

            # Read Replicas across regions
            read_replicas = db_instance.get('ReadReplicaDBInstanceIdentifiers', [])
            dimensions.append(
                DimensionOutput(
                    name='Read Replica IDs',
                    value=read_replicas
                )
            )

            # Backup Retention Period
            dimensions.append(
                DimensionOutput(
                    name='BackupRetentionPeriod',
                    value=db_instance.get('BackupRetentionPeriod', 0)
                )
            )

            # Point-in-Time Recovery
            pitr_enabled = db_instance.get('BackupRetentionPeriod', 0) > 0 and db_instance.get(
                'LatestRestorableTime') is not None
            dimensions.append(
                DimensionOutput(
                    name='PointInTimeRecovery',
                    value=pitr_enabled
                )
            )

            # Automated Backups
            automated_backups = db_instance.get('BackupRetentionPeriod', 0) > 0
            dimensions.append(
                DimensionOutput(
                    name='AutomatedBackups',
                    value=automated_backups
                )
            )

            # Deletion Protection
            dimensions.append(
                DimensionOutput(
                    name='DeletionProtection',
                    value=db_instance.get('DeletionProtection', False)
                )
            )

            # Minor Version Upgrade
            dimensions.append(
                DimensionOutput(
                    name='MinorVersionUpgrade',
                    value=db_instance.get('AutoMinorVersionUpgrade', False)
                )
            )

            # Maintenance Window
            dimensions.append(
                DimensionOutput(
                    name='MaintenanceWindow',
                    value=db_instance.get('PreferredMaintenanceWindow')
                )
            )

        return dimensions
