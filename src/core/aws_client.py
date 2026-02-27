import boto3
from botocore.config import Config

from .config import settings


class AWSClientProvider:
    def __init__(self):
        # Configuration is pulled from the 'settings' object populated by .env
        self.config = Config(
            region_name=settings.aws_region,
            retries={'max_attempts': 10, 'mode': 'standard'}
        )

        # Initializes session using the profile specified in .env
        self._session = boto3.Session(profile_name=settings.aws_profile)

    def get_cft_client(self):
        """Returns a client for CloudFormation operations."""
        return self._session.client('cloudformation', config=self.config)

    def get_client_by_resource_type(self, resource_type: str):
        service_name = self.get_service_name_by_resource_type(resource_type)
        return self._session.client(service_name, config=self.config)

    @staticmethod
    def get_service_name_by_resource_type(resource_type: str):
        resource_type = resource_type.lower()
        if '::' in resource_type and resource_type.startswith('aws'):
            # We are expecting resource_type like "AWS::IAM::Role"
            return resource_type.split('::')[1]
        else:
            return resource_type
