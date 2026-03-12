import json
from typing import List, Dict, Any

from dotenv import load_dotenv

from src.models.ai import Tool
from src.models.resiliency_report import ResourceResilienceOutput
from src.utils.call_ai import ask_ai

load_dotenv(verbose=True)


def get_apigw_resilience_report(dimensions: List[Dict[str, Any]]) -> ResourceResilienceOutput:
    """
    Evaluates API Gateway configuration against Well-Architected Reliability standards.
    Extracts the API identifier directly from the provided metadata.
    """

    resource_id = next((d['value'] for d in dimensions if d['name'] in ['ApiName', 'ApiId', 'Name']), "AWS API Gateway")

    user_prompt = f"""
    Perform a Resilience Audit on the following AWS API Gateway: "{resource_id}".

    Metadata:
    {json.dumps(dimensions, indent=2)}

    Task:
    1. Apply AWS Well-Architected Reliability Pillar standards for API Management.
    2. Analyze specific risks based on the provided dimensions: 
       - Evaluate 'Timeout' (3s) as the integration response limit.
       - Evaluate lack of 'DLQ' and 'RetryConfiguration' for backend integration failures.
       - Evaluate 'VPCMultiAZ' for private endpoint availability.
    3. Infer architectural impact (e.g., risk of cascading failures, lack of request buffering, or endpoint isolation issues).
    4. Generate exact AWS CLI commands to remediate gaps for this API Gateway.

    Populate the ResourceResilienceOutput strictly.
    """

    return ask_ai(
        messages=[{
            "role": "user",
            "content": [{"text": user_prompt}]
        }],
        tool=Tool(
            name='get_resiliency_report',
            description='Audits AWS API Gateway for traffic resilience, availability, and error handling.',
            expected_output_class=ResourceResilienceOutput
        )
    )


def main():
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

    output: ResourceResilienceOutput = get_apigw_resilience_report(metadata)

    if not output or not output.report:
        print("Audit failed to generate a valid report.")
        return

    report = output.report

    print(f"\nAPI GATEWAY AUDIT: {report.resource_name}")
    print(f"POSTURE SCORE: {report.overall_resilience_score}/10")
    print("-" * 60)
    print(f"SUMMARY: {report.summary}\n")

    print("GAPS IDENTIFIED:")
    for gap in report.resilience_gaps:
        print(f"× {gap.name} ({gap.status}): {gap.impact}")

    print("\nREMEDIATION COMMANDS:")
    for cmd in output.aws_commands_to_fix:
        print(f"$ {cmd}")


if __name__ == '__main__':
    main()
