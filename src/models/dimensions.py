import abc
import enum
from typing import Optional, List

import boto3

from src.core.aws_client import AWSClientProvider
from src.models.resources import DimensionOutput


class DimensionSupportedResource(str, enum.Enum):
    DynamoDB = 'dynamodb'
    S3 = 's3'
    RDS = 'rds'
    Lambda = 'lambda'

    @classmethod
    def from_str(cls, dimension_str) -> Optional['DimensionSupportedResource']:
        if dimension_str == cls.S3.value:
            return cls.S3
        elif dimension_str == cls.DynamoDB.value:
            return cls.DynamoDB
        elif dimension_str == cls.RDS.value:
            return cls.RDS
        elif dimension_str == cls.Lambda.value:
            return cls.Lambda
        return None


class DimensionFetcher(abc.ABC):
    def __init__(self, aws: AWSClientProvider):
        self.aws = aws

    @abc.abstractmethod
    def get_dimensions(self, physical_id) -> List[DimensionOutput]:
        raise NotImplementedError

    @abc.abstractmethod
    def get_resource_enum(self) -> DimensionSupportedResource:
        raise NotImplementedError

    def get_aws_client_for_resource(self) -> boto3.client:
        service_name = self.get_resource_enum().value
        return self.aws.get_client_by_service_name(service_name)

    def get_aws_client_provider(self) -> AWSClientProvider:
        return self.aws
