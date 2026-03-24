from typing import List

from src.models.dimensions import DimensionFetcher, DimensionSupportedResource
from src.models.resources import DimensionOutput


class DynamoDBDimensionFetcher(DimensionFetcher):
    def get_resource_enum(self) -> DimensionSupportedResource:
        return DimensionSupportedResource.DynamoDB

    def get_dimensions(self, physical_id: str) -> List[DimensionOutput]:
        """
        Fetches complex DynamoDB configurations. Values are returned in their
        native types (bool, list, dict) for dynamic HTTP/JSON serialization.
        """
        ddb = self.get_aws_client_for_resource()
        dimensions = []

        try:
            # 1. Main Table Metadata
            table_resp = ddb.describe_table(TableName=physical_id)
            table = table_resp['Table']

            # Deletion Protection (Boolean)
            dimensions.append(
                DimensionOutput(name="DeletionProtection", value=table.get('DeletionProtectionEnabled', False)))

            # Global Tables / Replicas (List of Strings)
            replicas = [r['RegionName'] for r in table.get('Replicas', [])]
            dimensions.append(DimensionOutput(name="GlobalTableRegions", value=replicas))

            # Streams Configuration (Dictionary/Object)
            dimensions.append(DimensionOutput(
                name="StreamsConfiguration",
                value=table.get('StreamSpecification', {"Enabled": False})
            ))

            # Partition & Sort Key Design (List of Objects)
            dimensions.append(DimensionOutput(name="KeySchema", value=table.get('KeySchema', [])))

            # Secondary Indexes (List of Objects)
            dimensions.append(DimensionOutput(name="SecondaryIndexes", value=table.get('GlobalSecondaryIndexes', [])))

            # 2. Point In Time Recovery (PITR)
            pitr_resp = ddb.describe_continuous_backups(TableName=physical_id)
            pitr_status = pitr_resp['ContinuousBackupsDescription']['PointInTimeRecoveryDescription']
            dimensions.append(DimensionOutput(name="PointInTimeRecovery", value=pitr_status))

            # 3. Auto Scaling Configuration
            as_client = self.get_aws_client_provider().get_client_by_service_name("application-autoscaling")
            scaling_resp = as_client.describe_scalable_targets(
                ServiceNamespace='dynamodb',
                ResourceIds=[f"table/{physical_id}"]
            )
            dimensions.append(DimensionOutput(name="AutoScaling", value=scaling_resp.get('ScalableTargets', [])))

        except Exception as e:
            dimensions.append(DimensionOutput(name="Error", value={"message": str(e), "type": type(e).__name__}))

        return dimensions
