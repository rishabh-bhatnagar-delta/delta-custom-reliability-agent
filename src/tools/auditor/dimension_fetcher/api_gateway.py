from typing import List

from src.models.dimensions import DimensionFetcher, DimensionSupportedResource
from src.models.resources import DimensionOutput


class APIGatewayDimensionFetcher(DimensionFetcher):
    def get_resource_enum(self) -> DimensionSupportedResource:
        return DimensionSupportedResource.APIGateway

    def get_dimensions(self, physical_id) -> List[DimensionOutput]:
        dimensions = []
        apigw_client = self.get_aws_client_for_resource()
        api_id = physical_id

        # Multi-Region Deployment (API level)
        try:
            api = apigw_client.get_rest_api(restApiId=api_id)
            endpoint_type = api.get('endpointConfiguration', {}).get('types', ['REGIONAL'])[0]
            dimensions.append(DimensionOutput(name='MultiRegion', value=endpoint_type == 'REGIONAL'))
        except apigw_client.exceptions.NotFoundException:
            dimensions.append(DimensionOutput(name='MultiRegion', value=False))

        # Get ALL stages with minimal details
        try:
            stages_response = apigw_client.get_stages(restApiId=api_id)
            stages = stages_response.get('item', [])

            # DRY: Single source for default/empty values
            self._add_stage_dimensions(dimensions, stages)

        except apigw_client.exceptions.NotFoundException:
            self._add_stage_dimensions(dimensions, [])

        return dimensions

    @staticmethod
    def _add_stage_dimensions(dimensions: List[DimensionOutput], stages: list):
        """Extract all stage-related dimensions once"""
        stage_list = []
        cache_count = throttling_count = 0
        tracing_stages = []

        for stage in stages:
            stage_name = stage.get('stageName')
            has_throttling = bool(stage.get('methodSettings', {}))
            cache_enabled = stage.get('cacheClusterEnabled', False)
            tracing_enabled = stage.get('tracingEnabled', False)

            stage_list.append({
                'name': stage_name,
                'cacheEnabled': cache_enabled,
                'tracingEnabled': tracing_enabled,
                'throttlingMethods': len(stage.get('methodSettings', {}))
            })

            cache_count += cache_enabled
            throttling_count += has_throttling
            if tracing_enabled:
                tracing_stages.append(stage_name)

        # Single assignment block - no repetition
        dimensions.extend([
            DimensionOutput(name='Stages', value=stage_list),
            DimensionOutput(name='StageCount', value=len(stages)),
            DimensionOutput(name='CacheEnabledStages', value=cache_count),
            DimensionOutput(name='ThrottlingStages', value=throttling_count),
            DimensionOutput(name='TracingStages', value=tracing_stages)
        ])
