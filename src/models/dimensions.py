import abc
import enum
from typing import Optional, List

from src.core.aws_client import AWSClientProvider
from src.models.resources import DimensionOutput


class DimensionSupportedResource(str, enum.Enum):
    DynamoDB = 'dynamodb'
    S3 = 's3'

    @classmethod
    def from_str(cls, dimension_str) -> Optional['DimensionSupportedResource']:
        if dimension_str == cls.S3.value:
            return cls.S3
        elif dimension_str == cls.DynamoDB.value:
            return cls.DynamoDB
        return None


class DimensionFetcher(abc.ABC):
    def __init__(self, aws: AWSClientProvider):
        self.aws = aws

    @abc.abstractmethod
    def get_dimensions(self, resource_arn) -> List[DimensionOutput]:
        raise NotImplementedError

    @abc.abstractmethod
    def get_aws_service_name(self):
        raise NotImplementedError
