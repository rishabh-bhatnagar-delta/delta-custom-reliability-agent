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
        self.score = 10

    def dim(self, key: str, default=None):
        value = self.dim_map.get(key, default)
        return value if value is not None else default

    def add_gap(self, name: str, status: str, impact: str, penalty: int = 0,
                recommendation: str = None, cli: str = None):
        self.score -= penalty
        self.gaps.append(ResilienceGap(name=name, status=status, impact=impact))
        if recommendation:
            self.recommendations.append(recommendation)
        if cli:
            self.cli_commands.append(cli)

    def build(self, resource_label: str = None) -> ResourceResilienceOutput:
        label = resource_label or self.resource_name
        self.score = max(0, min(10, self.score))
        total_issues = len(self.gaps)

        if self.score >= 8:
            summary = f"{label} has a strong reliability posture with {total_issues} minor gap(s)."
        elif self.score >= 5:
            summary = f"{label} has moderate reliability risks. {total_issues} gap(s) need attention."
        else:
            summary = f"{label} has significant reliability gaps. {total_issues} issue(s) require remediation."

        return ResourceResilienceOutput(
            recommendations=self.recommendations,
            aws_commands_to_fix=self.cli_commands,
            report=ResiliencyReport(
                resource_name=self.resource_name,
                resilience_gaps=self.gaps,
                overall_resilience_score=self.score,
                max_resilience_score=10,
                summary=summary,
            ),
        )
