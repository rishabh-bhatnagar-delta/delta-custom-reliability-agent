from typing import List

from src.models.dimensions import DimensionFetcher, DimensionSupportedResource
from src.models.resources import DimensionOutput


class LambdaDimensionFetcher(DimensionFetcher):
    def get_resource_enum(self) -> DimensionSupportedResource:
        return DimensionSupportedResource.Lambda

    def get_dimensions(self, resource_arn) -> List[DimensionOutput]:
        dimensions: List[DimensionOutput] = []
        lambda_client = self.get_aws_client_for_resource()
        function_name = resource_arn  # Use resource_arn directly

        # Get function configuration
        response = lambda_client.get_function(FunctionName=function_name)
        config = response['Configuration']

        # Multi-AZ Deployment (via VPCConfig)
        vpc_config = config.get('VpcConfig', {})
        dimensions.append(DimensionOutput(name='MultiAZ', value=bool(vpc_config.get('VpcId'))))

        # Reserved Concurrency
        try:
            concurrency = lambda_client.get_function_concurrency(FunctionName=function_name)
            dimensions.append(
                DimensionOutput(name='ReservedConcurrency', value=concurrency.get('ReservedConcurrentExecutions')))
        except:
            dimensions.append(DimensionOutput(name='ReservedConcurrency', value=None))

        # Snap Start
        dimensions.append(DimensionOutput(name='SnapStart', value=config.get('SnapStart', {})))

        # Dead Letter Queue (DLQ)
        dimensions.append(DimensionOutput(name='DLQ', value=config.get('DeadLetterConfig', {}).get('TargetArn')))

        # Retry Configuration + Maximum Event Age
        try:
            event_config = lambda_client.get_function_event_invoke_config(FunctionName=function_name)
            retry = event_config.get('MaximumRetryAttempts')
            max_age = event_config.get('MaximumEventAgeInSeconds')
            dimensions.append(DimensionOutput(name='RetryConfiguration', value=retry))
            dimensions.append(DimensionOutput(name='MaximumEventAge', value=max_age))
        except lambda_client.exceptions.ResourceNotFoundException:
            dimensions.append(DimensionOutput(name='RetryConfiguration', value=None))
            dimensions.append(DimensionOutput(name='MaximumEventAge', value=None))

        # VPC Multi-AZ Configuration
        dimensions.append(DimensionOutput(name='VPCMultiAZ', value=bool(vpc_config.get('VpcId'))))

        # Event Source Mapping Configuration
        mappings = lambda_client.list_event_source_mappings(FunctionName=function_name).get('EventSourceMappings', [])
        dimensions.append(DimensionOutput(name='EventSourceMappings', value=[m['UUID'] for m in mappings]))

        # Memory Allocation
        dimensions.append(DimensionOutput(name='Memory', value=config.get('MemorySize')))

        # Function Timeout Configuration
        dimensions.append(DimensionOutput(name='Timeout', value=config.get('Timeout')))

        return dimensions
