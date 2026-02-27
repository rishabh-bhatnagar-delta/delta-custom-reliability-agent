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
    def get_aws_service_name(self) -> DimensionSupportedResource:
        return DimensionSupportedResource.DynamoDB

    def get_dimensions(self, resource_arn) -> List[DimensionOutput]:
        return {"sasdf": "asdf"}


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
