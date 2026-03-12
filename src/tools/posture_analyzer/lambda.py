import json
from typing import List, Dict, Any

from dotenv import load_dotenv

from src.models.ai import Tool
from src.models.resiliency_report import ResourceResilienceOutput
from src.utils.call_ai import ask_ai

load_dotenv(verbose=True)


def get_lambda_resilience_report(function_name: str, dimensions: List[Dict[str, Any]]) -> ResourceResilienceOutput:
    """
    Evaluates AWS Lambda configuration against Well-Architected Serverless Reliability standards.
    """
    user_prompt = f"""
    Perform a Resilience Audit on the AWS Lambda Function: "{function_name}".

    Lambda Configuration Metadata:
    {json.dumps(dimensions, indent=2)}

    Task:
    1. Identify resilience gaps using AWS Well-Architected Serverless Reliability standards.
    2. Populate the 'recommendations' list with high-level architectural fixes.
    3. Populate the 'aws_commands_to_fix' list with exact AWS CLI commands for "{function_name}".
    4. Populate the 'report' object with the score, gaps, and architectural summary.

    Constraint: Ensure all three top-level fields (recommendations, aws_commands_to_fix, report) are populated.
    """

    return ask_ai(
        messages=[{
            "role": "user",
            "content": [{"text": user_prompt}]
        }],
        tool=Tool(
            name='get_lambda_resilience_report',
            description='Audits AWS Lambda for serverless resilience, error handling, and performance availability.',
            expected_output_class=ResourceResilienceOutput
        )
    )


def main():
    function_name = "order-processor-handler"

    # Specific metadata provided for the Lambda function
    metadata = [
        {"name": "MultiAZ", "value": False},
        {"name": "ReservedConcurrency", "value": None},
        {"name": "SnapStart", "value": {"ApplyOn": "None", "OptimizationStatus": "Off"}},
        {"name": "DLQ", "value": None},
        {"name": "RetryConfiguration", "value": None},
        {"name": "MaximumEventAge", "value": None},
        {"name": "VPCMultiAZ", "value": False},
        {"name": "EventSourceMappings", "value": []},
        {"name": "Memory", "value": 128},
        {"name": "Timeout", "value": 3}
    ]

    # Trigger AI analysis
    output: ResourceResilienceOutput = get_lambda_resilience_report(function_name, metadata)

    if not output or not output.report:
        print("Audit failed: The AI response was empty or incorrectly formatted.")
        return

    report = output.report

    print(f"\nLAMBDA RESILIENCE AUDIT: {report.resource_name}")
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
