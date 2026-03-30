from typing import List, Dict, Any

from src.models.resiliency_report import ResourceResilienceOutput
from src.tools.posture_analyzer.base import ResilienceAnalyzer


def get_lambda_resilience_report(function_name: str, dimensions: List[Dict[str, Any]]) -> ResourceResilienceOutput:
    """Rule-based resilience evaluation for AWS Lambda."""
    a = ResilienceAnalyzer(function_name, dimensions)

    if not a.dim("DLQ"):
        a.add_gap("Dead Letter Queue (DLQ)", "NOT CONFIGURED",
                   "Failed async invocations are silently dropped; no way to recover or retry.",
                   penalty=2, recommendation="Configure a DLQ (SQS or SNS) to capture failed invocations.",
                   cli=f"aws lambda update-function-configuration --function-name {function_name} --dead-letter-config TargetArn=arn:aws:sqs:REGION:ACCOUNT:dlq-queue")

    if a.dim("ReservedConcurrency") is None:
        a.add_gap("Reserved Concurrency", "NOT SET",
                   "Function shares concurrency pool; noisy neighbors can throttle it.",
                   penalty=1, recommendation="Set reserved concurrency to guarantee execution capacity.",
                   cli=f"aws lambda put-function-concurrency --function-name {function_name} --reserved-concurrent-executions 100")

    if a.dim("RetryConfiguration") is None:
        a.add_gap("Retry Configuration", "DEFAULT (2)",
                   "Using default retry count; may cause excessive retries or insufficient recovery.",
                   penalty=1, recommendation="Explicitly configure retry attempts based on function idempotency.",
                   cli=f"aws lambda put-function-event-invoke-config --function-name {function_name} --maximum-retry-attempts 1")

    if a.dim("MaximumEventAge") is None:
        a.add_gap("Maximum Event Age", "DEFAULT (6h)",
                   "Stale events may be processed; default 6-hour window is too long for most use cases.",
                   penalty=1, recommendation="Set maximum event age to discard stale invocations.",
                   cli=f"aws lambda put-function-event-invoke-config --function-name {function_name} --maximum-event-age-in-seconds 3600")

    if not a.dim("VPCMultiAZ", False) and not a.dim("MultiAZ", False):
        a.add_gap("VPC Multi-AZ", "NOT IN VPC or SINGLE AZ",
                   "Function not deployed across multiple AZs; AZ failure impacts availability.",
                   penalty=1, recommendation="Deploy Lambda in a VPC with subnets across multiple AZs.")

    memory = a.dim("Memory", 128)
    if memory and memory <= 128:
        a.add_gap("Memory Allocation", f"{memory} MB",
                   "Minimum memory; may cause slow cold starts and timeouts under load.",
                   recommendation="Consider increasing memory allocation for better CPU and performance.")

    timeout = a.dim("Timeout", 3)
    if timeout and timeout <= 3:
        a.add_gap("Timeout Configuration", f"{timeout}s",
                   "Very short timeout; downstream latency spikes will cause failures.",
                   recommendation="Increase timeout to accommodate downstream service latency.")

    snap_start = a.dim("SnapStart", {})
    if isinstance(snap_start, dict) and snap_start.get("ApplyOn") == "None":
        a.add_gap("SnapStart", "DISABLED",
                   "Cold starts not optimized; higher latency for Java/Python runtimes.")

    return a.build(f"Lambda '{function_name}'")
