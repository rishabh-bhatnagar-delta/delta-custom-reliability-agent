import json
from typing import List, Dict, Any

from dotenv import load_dotenv

from src.models.ai import Tool
from src.models.resiliency_report import ResourceResilienceOutput
from src.utils.call_ai import ask_ai

load_dotenv(verbose=True)


def get_s3_resilience_report(bucket_name: str, dimensions: List[Dict[str, Any]]) -> ResourceResilienceOutput:
    user_prompt = f"""
    S3 BUCKET AUDIT: "{bucket_name}"
    METADATA: {json.dumps(dimensions)}

    TASK:
    Analyze the metadata and populate the 'get_s3_resilience_report' tool with:
    1. 'recommendations': List of strings.
    2. 'aws_commands_to_fix': List of strings (AWS CLI).
    3. 'report': Object with bucket_name, resilience_gaps, overall_resilience_score, and summary.

    START WITH THE TOOL CALL. NO PREAMBLE.
    """

    try:
        output: ResourceResilienceOutput = ask_ai(
            messages=[{
                "role": "user",
                "content": [{"text": user_prompt}]
            }],
            tool=Tool(
                name='get_s3_resilience_report',
                description='Generates a complete S3 resiliency report.',
                expected_output_class=ResourceResilienceOutput
            )
        )
        return output

    except Exception as e:
        print(f"Error: {e}")
        return None


def main():
    bucket_name = "production-data-vault"
    metadata = [
        {"name": "Versioning", "value": "Disabled"},
        {"name": "MFA Delete", "value": False},
        {"name": "MultiRegion", "value": False},
        {"name": "ObjectLock", "value": False},
        {"name": "ScheduledBackup", "value": False},
        {"name": "PointInTimeRecovery", "value": False},
        {"name": "DataReplication", "value": []},
        {"name": "CrossRegionBackup", "value": False}
    ]

    output = get_s3_resilience_report(bucket_name, metadata)

    if not output or not output.report:
        print("Audit failed: The AI response was empty or incorrectly formatted.")
        return

    # Render Output
    r = output.report
    print(f"\nS3 AUDIT: {r.bucket_name} | Score: {r.overall_resilience_score}/10")
    print("-" * 60)
    print(f"SUMMARY: {r.summary}\n")

    print("GAPS:")
    for gap in r.resilience_gaps:
        print(f"× {gap.name}: {gap.impact}")

    print("\nCLI FIXES:")
    for cmd in output.aws_commands_to_fix:
        print(f"$ {cmd}")


if __name__ == '__main__':
    main()
