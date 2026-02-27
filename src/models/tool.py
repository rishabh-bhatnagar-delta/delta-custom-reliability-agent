import enum


class ToolNames(str, enum.Enum):
    LIST_AWS_RESOURCES = "list_aws_resources_from_cft"
    GET_RESOURCE_DIMENSIONS = "get_resource_dimensions"


class ToolArgs(str, enum.Enum):
    RESOURCE_ARN = "resource_arn"
    RESOURCE_TYPE = "resource_type"
