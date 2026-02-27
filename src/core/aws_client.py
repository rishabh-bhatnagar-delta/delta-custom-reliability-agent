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
