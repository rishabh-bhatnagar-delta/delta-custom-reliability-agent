import asyncio
import json
from typing import List

from src.core.aws_client import AWSClientProvider
from src.models.dimensions import DimensionFetcher, DimensionSupportedResource
from src.models.resources import DimensionOutput
from src.tools.auditor.dimension_fetcher.api_gateway import APIGatewayDimensionFetcher
from src.tools.auditor.dimension_fetcher.dynamo_db import DynamoDBDimensionFetcher
from src.tools.auditor.dimension_fetcher.ec2 import EC2DimensionFetcher
from src.tools.auditor.dimension_fetcher.lambda_ import LambdaDimensionFetcher
from src.tools.auditor.dimension_fetcher.rds import RDSDimensionFetcher
from src.tools.auditor.dimension_fetcher.route53 import Route53DimensionFetcher
from src.tools.auditor.dimension_fetcher.s3 import S3DimensionFetcher


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
        DimensionSupportedResource.RDS: RDSDimensionFetcher(aws),
        DimensionSupportedResource.Lambda: LambdaDimensionFetcher(aws),
        DimensionSupportedResource.S3: S3DimensionFetcher(aws),
        DimensionSupportedResource.APIGateway: APIGatewayDimensionFetcher(aws),
        DimensionSupportedResource.Route53: Route53DimensionFetcher(aws),
        DimensionSupportedResource.EC2: EC2DimensionFetcher(aws),
    }[res_enum]
    return dimension_fetcher


async def get_resource_dimensions(aws: AWSClientProvider, physical_id, resource_type) -> List[DimensionOutput]:
    dimension_fetcher = get_dimension_fetcher_from_resource_type(resource_type, aws)
    dimensions = dimension_fetcher.get_dimensions(physical_id, resource_type=resource_type)
    return dimensions


if __name__ == "__main__":
    async def run_local():
        print("--- Fetching the dimensions ---")
        provider = AWSClientProvider()

        physical_id = "observability-job-status-516669083107-us-east-1-dev"
        resource_type = "AWS::S3::Bucket"

        print(f"Physical ID: {physical_id}")
        print(f"Resource Type: {resource_type}\n")

        # Execute the full orchestration
        results = await get_resource_dimensions(
            provider,
            physical_id=physical_id,
            resource_type=resource_type
        )
        output = [d.model_dump() for d in results]
        print(json.dumps(output, indent=2))


    asyncio.run(run_local())
