import json
from typing import List, Dict, Any

from dotenv import load_dotenv

from src.models.ai import Tool
from src.models.resiliency_report import ResourceResilienceOutput
from src.utils.call_ai import ask_ai

load_dotenv(verbose=True)


def get_rds_resilience_report(db_instance_id: str, dimensions: List[Dict[str, Any]]) -> ResourceResilienceOutput:
    """
    Evaluates RDS instance configuration against AWS Well-Architected Reliability standards.

    Args:
        db_instance_id: The identifier for the RDS DB instance.
        dimensions: List of configuration states (e.g., MultiAZ, BackupRetention).

    Returns:
        ResourceResilienceOutput: Structured metadata and remediation CLI commands.
    """

    # Targeting RDS-specific architecture within the generic agent framework
    user_prompt = f"""
    Perform a Resilience Audit on the RDS DB Instance: "{db_instance_id}".

    RDS Configuration Metadata:
    {json.dumps(dimensions, indent=2)}

    Task:
    1. Identify resilience gaps using AWS Well-Architected Reliability standards for Databases.
    2. Populate the 'recommendations' list with high-level architectural fixes.
    3. Populate the 'aws_commands_to_fix' list with exact AWS CLI commands for "{db_instance_id}".
    4. Populate the 'report' object with the score, gaps, and architectural summary.

    Constraint: Ensure all three top-level fields (recommendations, aws_commands_to_fix, report) are populated.
    """

    return ask_ai(
        messages=[{
            "role": "user",
            "content": [{"text": user_prompt}]
        }],
        tool=Tool(
            name='get_rds_resilience_report',
            description='Audits RDS database availability and durability against AWS best practices.',
            expected_output_class=ResourceResilienceOutput
        )
    )


def main():
    """
    Example execution for an RDS instance resilience audit.
    """
    db_id = "prod-billing-db"

    dimensions = [{"name": "MultiAZ", "value": False}, {"name": "Read Replica IDs", "value": []},
                  {"name": "BackupRetentionPeriod", "value": 7}, {"name": "PointInTimeRecovery", "value": True},
                  {"name": "AutomatedBackups", "value": True}, {"name": "DeletionProtection", "value": False},
                  {"name": "MinorVersionUpgrade", "value": True},
                  {"name": "MaintenanceWindow", "value": "sat:07:05-sat:07:35"}]

    # Trigger AI analysis
    output: ResourceResilienceOutput = get_rds_resilience_report(db_id, dimensions)

    if not output or not output.report:
        print("Audit failed: The AI response was empty or incorrectly formatted.")
        return

    report = output.report

    print(f"\nRDS RESILIENCE AUDIT: {report.resource_name}")
    print(f"POSTURE SCORE: {report.overall_resilience_score}/{report.max_resilience_score}")
    print("-" * 60)
    print(f"SUMMARY:\n{report.summary}\n")

    print("GAPS & ARCHITECTURAL IMPACTS:")
    for gap in report.resilience_gaps:
        print(f"× {gap.name} (Status: {gap.status})")
        print(f"  Impact: {gap.impact}")

    print("\nREMEDIATION ROADMAP (AWS CLI):")
    for cmd in output.aws_commands_to_fix:
        print(f"$ {cmd}")


if __name__ == '__main__':
    main()
