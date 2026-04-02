from typing import List, Dict, Any

from src.models.resiliency_report import (
    ResourceResilienceOutput,
    ResiliencyReport,
    ResilienceGap,
)


class ResilienceAnalyzer:
    """Base class for rule-based resilience evaluation."""

    def __init__(self, resource_name: str, dimensions: List[Dict[str, Any]]):
        self.resource_name = resource_name
        self.dim_map = {d["name"]: d.get("value") for d in dimensions}
        self.gaps: List[ResilienceGap] = []
        self.recommendations: List[str] = []
        self.cli_commands: List[str] = []

    def dim(self, key: str, default=None):
        value = self.dim_map.get(key, default)
        return value if value is not None else default

    def add_gap(self, name: str, status: str, impact: str,
                recommendation: str = None, cli: str = None):
        self.gaps.append(ResilienceGap(name=name, status=status, impact=impact))
        if recommendation:
            self.recommendations.append(recommendation)
        if cli:
            self.cli_commands.append(cli)

    def build(self, resource_label: str = None) -> ResourceResilienceOutput:
        label = resource_label or self.resource_name
        total_issues = len(self.gaps)

        if total_issues == 0:
            summary = f"{label} has no identified reliability gaps."
        elif total_issues <= 2:
            summary = f"{label} has {total_issues} gap(s) identified."
        else:
            summary = f"{label} has {total_issues} gap(s) that need attention."

        return ResourceResilienceOutput(
            recommendations=self.recommendations,
            aws_commands_to_fix=self.cli_commands,
            report=ResiliencyReport(
                resource_name=self.resource_name,
                resilience_gaps=self.gaps,
                summary=summary,
            ),
        )
