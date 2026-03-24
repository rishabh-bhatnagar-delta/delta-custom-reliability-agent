from typing import List
import boto3
from botocore.exceptions import ClientError

from src.models.dimensions import DimensionFetcher, DimensionSupportedResource
from src.models.resources import DimensionOutput


class S3DimensionFetcher(DimensionFetcher):
    def get_resource_enum(self) -> DimensionSupportedResource:
        return DimensionSupportedResource.S3

    def _fetch_dimensions(self, physical_id: str) -> List[DimensionOutput]:
        dimensions = []
        s3_client = self.get_aws_client_for_resource()
        # Ensure the backup client is initialized correctly from your provider
        backup_client = self.get_aws_client_provider().get_client_by_service_name('backup')

        resource_arn = f'arn:aws:s3:::{physical_id}'

        # --- S3 Configuration Base ---

        # Versioning & MFA Delete Configuration
        versioning = s3_client.get_bucket_versioning(Bucket=physical_id)
        versioning_status = versioning.get('Status', 'Disabled')
        dimensions.append(DimensionOutput(name='Versioning', value=versioning_status))

        # MFA Delete (Corrected typo from MFAD)
        dimensions.append(DimensionOutput(name='MFA Delete', value=versioning.get('MFADelete') == 'Enabled'))

        # Multi-Region (Basic Replication Check)
        try:
            replication = s3_client.get_bucket_replication(Bucket=physical_id)
            has_role = bool(replication.get('ReplicationConfiguration', {}).get('Role'))
            dimensions.append(DimensionOutput(name='MultiRegion', value=has_role))
        except ClientError as e:
            if e.response['Error']['Code'] == 'ReplicationConfigurationNotFoundError':
                dimensions.append(DimensionOutput(name='MultiRegion', value=False))
            else:
                raise

        # Object Lock Configuration
        try:
            object_lock = s3_client.get_object_lock_configuration(Bucket=physical_id)
            is_enabled = object_lock.get('ObjectLockConfiguration', {}).get('ObjectLockEnabled') == 'Enabled'
            dimensions.append(DimensionOutput(name='ObjectLock', value=is_enabled))
        except ClientError as e:
            if e.response['Error']['Code'] == 'ObjectLockConfigurationNotFoundError':
                dimensions.append(DimensionOutput(name='ObjectLock', value=False))
            else:
                raise

        # Inventory Configuration
        try:
            inventories = s3_client.list_bucket_inventory_configurations(Bucket=physical_id).get(
                'InventoryConfigurationList', [])
            dimensions.append(DimensionOutput(name='InventoryConfigs', value=len(inventories)))
        except ClientError as e:
            if e.response['Error']['Code'] in ['NoSuchBucketInventoryConfiguration', 'NoSuchBucket']:
                dimensions.append(DimensionOutput(name='InventoryConfigs', value=0))
            else:
                raise

        # --- Resilience Hub Checks ---

        # Scheduled backup
        # FIXED: Switched from list_recovery_points_by_backup_vault (which requires a vault name)
        # to list_recovery_points_by_resource (which only requires the ARN).
        has_scheduled_backup = False
        try:
            recovery_points = backup_client.list_recovery_points_by_resource(
                ResourceArn=resource_arn
            )
            has_scheduled_backup = len(recovery_points.get('RecoveryPoints', [])) > 0
            dimensions.append(DimensionOutput(name='ScheduledBackup', value=has_scheduled_backup))
        except (ClientError, backup_client.exceptions.ResourceNotFoundException):
            dimensions.append(DimensionOutput(name='ScheduledBackup', value=False))

        # Point-in-time recovery (Resilience Hub definition for S3)
        pitr = (versioning_status == 'Enabled') and has_scheduled_backup
        dimensions.append(DimensionOutput(name='PointInTimeRecovery', value=pitr))

        # Detailed Data Replication (SRR/CRR + RTC)
        replication_rules = []
        try:
            replication_config = s3_client.get_bucket_replication(Bucket=physical_id)['ReplicationConfiguration']
            rules = replication_config.get('Rules', [])
            for rule in rules:
                replication_rules.append({
                    'Status': rule.get('Status'),
                    'Priority': rule.get('Priority'),
                    'DestinationBucket': rule.get('Destination', {}).get('Bucket'),
                    'RTCEnabled': rule.get('ReplicationTime', {}).get('Status') == 'Enabled' if rule.get(
                        'ReplicationTime') else False
                })
            dimensions.append(DimensionOutput(name='DataReplication', value=replication_rules))
        except ClientError as e:
            if e.response['Error']['Code'] == 'ReplicationConfigurationNotFoundError':
                dimensions.append(DimensionOutput(name='DataReplication', value=[]))
            else:
                raise

        # Cross-Region backup
        # Note: This checks the location of recovery points found earlier.
        cross_region_backup = False
        try:
            recovery_points = backup_client.list_recovery_points_by_resource(ResourceArn=resource_arn)
            current_region = s3_client.meta.region_name

            for rp in recovery_points.get('RecoveryPoints', []):
                rp_region = rp.get('BackupVaultArn', '').split(':')[3]
                if rp_region != current_region:
                    cross_region_backup = True
                    break
            dimensions.append(DimensionOutput(name='CrossRegionBackup', value=cross_region_backup))
        except:
            dimensions.append(DimensionOutput(name='CrossRegionBackup', value=False))

        return dimensions