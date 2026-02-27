import enum


class ToolNames(enum.Enum, str):
    LIST_AWS_RESOURCES = "list_aws_resources_from_cft"
    GET_RESOURCE_DIMENSIONS = "get_resource_dimensions"

class ToolArgs(enum.Enum, str):
    RESOURCE_ARN = "resource_arn"
