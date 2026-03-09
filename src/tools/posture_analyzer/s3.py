import json
import re

from dotenv import load_dotenv

from src.utils.call_ai import ask_ai

load_dotenv(verbose=True)


def get_resilience_report(bucket_name, dimensions):
    """
    Triggers the audit and splits output into a structured data dictionary and a Markdown report.
    """
    # Strict prompt to prevent conversational filler
    user_prompt = f"""
    [SYSTEM: OUTPUT MUST START WITH <report_metadata>]
    Analyze S3 bucket: {bucket_name}
    Dimensions: {json.dumps(dimensions)}

    Task: Generate the <report_metadata> JSON and the Markdown report.
    """

    raw_output = ask_ai(user_prompt, session_id=f"audit-{bucket_name}")

    # Extract JSON using a more robust regex
    json_data = {}
    json_match = re.search(r'<report_metadata>(.*?)</report_metadata>', raw_output, re.S)

    if json_match:
        try:
            # Clean possible markdown formatting inside tags
            clean_str = json_match.group(1).strip().replace("```json", "").replace("```", "")
            json_data = json.loads(clean_str)
        except json.JSONDecodeError:
            pass

    # Extract Markdown by removing the metadata block
    markdown_report = re.sub(r'<report_metadata>.*?</report_metadata>', '', raw_output, flags=re.S).strip()

    return json_data, markdown_report


if __name__ == '__main__':
    test_bucket = "production-data-vault"
    false = False
    test_dims = [{"name": "Versioning", "value": "Disabled"}, {"name": "MFA Delete", "value": false},
                 {"name": "MultiRegion", "value": false}, {"name": "ObjectLock", "value": false},
                 {"name": "InventoryConfigs", "value": 0}, {"name": "ScheduledBackup", "value": false},
                 {"name": "PointInTimeRecovery", "value": false}, {"name": "DataReplication", "value": []},
                 {"name": "CrossRegionBackup", "value": false}]

    structured_data, report = get_resilience_report(test_bucket, test_dims)

    print("--- STRUCTURED DATA ---")
    print(json.dumps(structured_data, indent=2))

    print("\n--- FORMATTED REPORT ---")
    print(report)
