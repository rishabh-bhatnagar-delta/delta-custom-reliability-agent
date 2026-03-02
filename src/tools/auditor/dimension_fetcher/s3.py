from typing import List

from src.models.dimensions import DimensionFetcher, DimensionSupportedResource
from src.models.resources import DimensionOutput


class S3DimensionFetcher(DimensionFetcher):
    def get_resource_enum(self) -> DimensionSupportedResource:
        return DimensionSupportedResource.S3

    def get_dimensions(self, physical_id) -> List[DimensionOutput]:
        dimensions = []
        s3_client = self.get_aws_client_for_resource()

        # Versioning Configuration
        versioning = s3_client.get_bucket_versioning(Bucket=physical_id)
        dimensions.append(DimensionOutput(name='Versioning', value=versioning.get('Status')))

        # Multi-Region (Replication)
        try:
            replication = s3_client.get_bucket_replication(Bucket=physical_id)
            dimensions.append(DimensionOutput(name='MultiRegion',
                                              value=bool(replication.get('ReplicationConfiguration', {}).get('Role'))))
        except Exception as e:
            dimensions.append(DimensionOutput(name='MultiRegion', value=False))

        # MFA Delete
        dimensions.append(DimensionOutput(name='MFAD Delete', value=versioning.get('MFADelete') == 'Enabled'))

        # Object Lock Configuration
        try:
            object_lock = s3_client.get_bucket_object_lock_configuration(Bucket=physical_id)
            dimensions.append(
                DimensionOutput(
                    name='ObjectLock',
                    value=object_lock.get('ObjectLockConfiguration', {}).get('ObjectLockEnabled') == 'Enabled'
                )
            )
        except:
            dimensions.append(DimensionOutput(name='ObjectLock', value=False))

        # Inventory Configuration
        inventories = s3_client.list_bucket_inventory_configurations(
            Bucket=physical_id
        ).get('InventoryConfigurationList', [])
        dimensions.append(DimensionOutput(name='InventoryConfigs', value=len(inventories)))

        return dimensions
