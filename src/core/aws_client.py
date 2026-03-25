import boto3
from botocore.config import Config

from .constants import AWS_REGION, AWS_PROFILE


class AWSClientProvider:
    def __init__(self):
        self.config = Config(
            region_name=AWS_REGION,
            retries={'max_attempts': 10, 'mode': 'standard'}
        )

        self._session = boto3.Session(profile_name=AWS_PROFILE)
        self._client_cache = {}

    def get_cft_client(self):
        """Returns a cached client for CloudFormation operations."""
        if 'cloudformation' not in self._client_cache:
            self._client_cache['cloudformation'] = self._session.client('cloudformation', config=self.config)
        return self._client_cache['cloudformation']

    def get_client_by_service_name(self, service_name: str) -> boto3.client:
        if service_name not in self._client_cache:
            self._client_cache[service_name] = self._session.client(service_name, config=self.config)
        return self._client_cache[service_name]

    @staticmethod
    def get_service_name_by_resource_type(resource_type: str):
        resource_type = resource_type.lower()
        if '::' in resource_type and resource_type.startswith('aws'):
            # We are expecting resource_type like "AWS::IAM::Role"
            return resource_type.split('::')[1]
        else:
            return resource_type
