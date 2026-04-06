import logging

import boto3
from botocore.config import Config

from .constants import AWS_REGION, AWS_PROFILE, ASSUME_ROLE_NAME

logger = logging.getLogger(__name__)


class AWSClientProvider:
    def __init__(self, region: str = None, account_id: str = None):
        self.region = region or AWS_REGION
        self.account_id = account_id
        self.config = Config(
            region_name=self.region,
            retries={'max_attempts': 10, 'mode': 'standard'}
        )
        self._base_session = boto3.Session(profile_name=AWS_PROFILE)

        if account_id:
            role_arn = f"arn:aws:iam::{account_id}:role/{ASSUME_ROLE_NAME}"
            logger.info(f"Assuming role {role_arn} for cross-account access")
            sts = self._base_session.client("sts", config=self.config)
            creds = sts.assume_role(RoleArn=role_arn, RoleSessionName="reliability-auditor")["Credentials"]
            self._session = boto3.Session(
                aws_access_key_id=creds["AccessKeyId"],
                aws_secret_access_key=creds["SecretAccessKey"],
                aws_session_token=creds["SessionToken"],
                region_name=self.region,
            )
        else:
            self._session = self._base_session

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
            return resource_type.split('::')[1]
        else:
            return resource_type
