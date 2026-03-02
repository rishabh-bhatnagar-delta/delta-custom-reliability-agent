import asyncio
from typing import List

from src.core.aws_client import AWSClientProvider
from src.models.dimensions import DimensionFetcher, DimensionSupportedResource
from src.models.resources import DimensionOutput


def get_dimension_fetcher_from_resource_type(resource_type: str, aws: AWSClientProvider) -> DimensionFetcher:
    # Getting the service name to make sure aws supports it
    service_name = AWSClientProvider.get_service_name_by_resource_type(resource_type)
    if service_name is None:
        raise ValueError(f'Resource type {resource_type} is not supported by AWS')

    # Getting the enum to make sure we support it
    res_enum = DimensionSupportedResource.from_str(service_name)
    if res_enum is None:
        raise ValueError(f'Resource type {resource_type} is not supported to fetch dimensions')

    # Return the class that can handle fetching dimension for the given resource_type
    dimension_fetcher = {
        DimensionSupportedResource.DynamoDB: DynamoDBDimensionFetcher(aws),
    }[res_enum]
    return dimension_fetcher


class DynamoDBDimensionFetcher(DimensionFetcher):
    def get_resource_enum(self) -> DimensionSupportedResource:
        return DimensionSupportedResource.DynamoDB

    def get_dimensions(self, resource_physical_id: str) -> List[DimensionOutput]:
        """
        Fetches complex DynamoDB configurations. Values are returned in their
        native types (bool, list, dict) for dynamic HTTP/JSON serialization.
        """
        ddb = self.get_aws_client_for_resource()
        dimensions = []

        try:
            # 1. Main Table Metadata
            table_resp = ddb.describe_table(TableName=resource_physical_id)
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
            pitr_resp = ddb.describe_continuous_backups(TableName=resource_physical_id)
            pitr_status = pitr_resp['ContinuousBackupsDescription']['PointInTimeRecoveryDescription']
            dimensions.append(DimensionOutput(name="PointInTimeRecovery", value=pitr_status))

            # 3. Auto Scaling Configuration
            as_client = self.get_aws_client_provider().get_client_by_service_name("application-autoscaling")
            scaling_resp = as_client.describe_scalable_targets(
                ServiceNamespace='dynamodb',
                ResourceIds=[f"table/{resource_physical_id}"]
            )
            dimensions.append(DimensionOutput(name="AutoScaling", value=scaling_resp.get('ScalableTargets', [])))

        except Exception as e:
            dimensions.append(DimensionOutput(name="Error", value={"message": str(e), "type": type(e).__name__}))

        return dimensions


async def get_resource_dimensions(aws: AWSClientProvider, resource_arn, resource_type) -> List[DimensionOutput]:
    dimension_fetcher = get_dimension_fetcher_from_resource_type(resource_type, aws)
    dimensions = dimension_fetcher.get_dimensions(resource_arn)
    return dimensions


if __name__ == "__main__":
    async def run_local():
        print("--- Fetching the dimensions ---")
        provider = AWSClientProvider()

        # Execute the full orchestration
        results = await get_resource_dimensions(provider, "rishabh-delta-test-cft-Table-16FCS911TKH6J",
                                                "AWS::DynamoDB::Table")
        print(results)


    asyncio.run(run_local())
