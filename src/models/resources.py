from typing import List, Optional, Any

from pydantic import BaseModel, Field


class SimpleRepr(BaseModel):
    def __repr__(self):
        return self.model_dump_json()


class StackResource(BaseModel):
    """Represents an individual AWS resource within a CloudFormation stack."""

    logical_id: str = Field(
        ...,
        description="The ID of the resource as defined in the CFT template."
    )
    physical_id: Optional[str] = Field(
        None,
        description="The actual AWS assigned ID (e.g., i-12345, my-api-id)."
    )
    resource_type: str = Field(
        ...,
        description="The AWS resource type (e.g., AWS::RDS::DBInstance)."
    )
    status: str = Field(
        ...,
        description="The current deployment status of this specific resource."
    )


class CloudFormationStack(BaseModel):
    """Represents a full CloudFormation stack and its contained resources."""

    stack_name: str = Field(
        ...,
        description="The name given to the CloudFormation stack."
    )
    stack_id: str = Field(
        ...,
        description="The unique Amazon Resource Name (ARN) of the stack."
    )
    resources: List[StackResource] = Field(
        default_factory=list,
        description="A list of physical resources managed by this stack."
    )


class DimensionOutput(SimpleRepr):
    name: str = Field(
        ...,
        description="The name of the dimension of the resource."
    )
    value: Any = Field(
        ...,
        description="The value of the dimension."
    )
