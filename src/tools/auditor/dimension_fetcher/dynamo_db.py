import json
from typing import List, Dict, Any

from dotenv import load_dotenv

from src.models.ai import Tool
from src.models.resiliency_report import ResourceResilienceOutput
from src.utils.call_ai import ask_ai

load_dotenv(verbose=True)


def get_dynamodb_resilience_report(dimensions: List[Dict[str, Any]]) -> ResourceResilienceOutput:
    """
    Evaluates DynamoDB configuration against Well-Architected Reliability standards.
    """

    resource_id = next((d['value'] for d in dimensions if d['name'] in ['TableName', 'Name']), "AWS DynamoDB Table")

    user_prompt = f"""
    Perform a Resilience Audit on the following DynamoDB Table: "{resource_id}".

    Metadata:
    {json.dumps(dimensions, indent=2)}

    Task:
    1. Apply AWS Well-Architected Reliability Pillar standards for NoSQL databases.
    2. Analyze specific strengths and risks:
       - Status of 'PointInTimeRecovery' and 'DeletionProtection'.
       - Multi-region availability via 'GlobalTableRegions'.
       - Throughput scalability via 'AutoScaling' configurations.
       - Data change capture via 'StreamsConfiguration'.
    3. Infer architectural impact regarding regional outages, accidental deletions, or traffic spikes.
    4. Generate exact AWS CLI commands to remediate any missing best practices for this table.

    Populate the ResourceResilienceOutput strictly.
    """

    return ask_ai(
        messages=[{
            "role": "user",
            "content": [{"text": user_prompt}]
        }],
        tool=Tool(
            name='get_resiliency_report',
            description='Audits DynamoDB for data durability, regional availability, and scalability.',
            expected_output_class=ResourceResilienceOutput
        )
    )


def main():
    sample_dimensions = [
        {"name": "TableName", "value": "GlobalOrders"},
        {"name": "DeletionProtection", "value": True},
        {"name": "GlobalTableRegions", "value": ["us-east-1", "eu-west-1"]},
        {"name": "StreamsConfiguration", "value": {"StreamEnabled": True, "StreamViewType": "NEW_AND_OLD_IMAGES"}},
        {"name": "KeySchema", "value": [{"AttributeName": "pk", "KeyType": "HASH"}]},
        {"name": "SecondaryIndexes", "value": [{"IndexName": "GSI1", "IndexStatus": "ACTIVE"}]},
        {"name": "PointInTimeRecovery", "value": {"PointInTimeRecoveryStatus": "ENABLED"}},
        {"name": "AutoScaling", "value": [{"MinCapacity": 5, "MaxCapacity": 50}]}
    ]

    output: ResourceResilienceOutput = get_dynamodb_resilience_report(sample_dimensions)

    if not output or not output.report:
        print("Audit failed to generate a valid report.")
        return

    report = output.report

    print(f"\nDYNAMODB AUDIT: {report.resource_name}")
    print(f"POSTURE SCORE: {report.overall_resilience_score}/10")
    print("-" * 60)
    print(f"SUMMARY: {report.summary}\n")

    print("GAPS & POSTURE FINDINGS:")
    for gap in report.resilience_gaps:
        print(f"• {gap.name} ({gap.status}): {gap.impact}")

    print("\nREMEDIATION COMMANDS:")
    if not output.aws_commands_to_fix:
        print("No critical gaps identified requiring CLI remediation.")
    else:
        for cmd in output.aws_commands_to_fix:
            print(f"$ {cmd}")


if __name__ == '__main__':
    main()
