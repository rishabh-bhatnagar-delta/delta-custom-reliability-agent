from typing import List

from pydantic import BaseModel, Field


class ResilienceGap(BaseModel):
    name: str = Field(description="Name of the feature.")
    status: str = Field(description="Current state.")
    impact: str = Field(description="Architectural risk.")


class ResiliencyReport(BaseModel):
    resource_name: str
    resilience_gaps: List[ResilienceGap]
    overall_resilience_score: int
    max_resilience_score: int = 10
    summary: str


class ResourceResilienceOutput(BaseModel):
    recommendations: List[str]
    aws_commands_to_fix: List[str]
    report: ResiliencyReport
