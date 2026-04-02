from typing import List, Optional

from pydantic import BaseModel, Field


class ResilienceGap(BaseModel):
    name: str = Field(description="Name of the feature.")
    status: str = Field(description="Current state.")
    impact: str = Field(description="Architectural risk.")


class ResiliencyReport(BaseModel):
    resource_name: str
    resilience_gaps: List[ResilienceGap]
    summary: str


class ResourceResilienceOutput(BaseModel):
    recommendations: List[str] = Field(default_factory=list)
    aws_commands_to_fix: List[str] = Field(default_factory=list)
    report: Optional[ResiliencyReport] = None
