from typing import List, Dict, Any

from src.models.resiliency_report import (
    ResourceResilienceOutput,
    ResiliencyReport,
    ResilienceGap,
)


def get_lambda_resilience_report(function_name: str, dimensions: List[Dict[str, Any]]) -> ResourceResilienceOutput:
    """Rule-based resilience evaluation for AWS Lambda."""
    dim_map = {d["name"]: d.get("value") for d in dimensions}

    gaps: List[ResilienceGap] = []
    recommendations: List[str] = []
    cli_commands: List[str] = []
    score = 10

    # 1. Dead Letter Queue
    dlq = dim_map.get("DLQ")
    if not dlq:
        score -= 2
        gaps.append(ResilienceGap(
            name="Dead Letter Queue (DLQ)",
            status="NOT CONFIGURED",
            impact="Failed async invocations are silently dropped; no way to recover or retry.",
        ))
        recommendations.append("Configure a DLQ (SQS or SNS) to capture failed invocations.")
        cli_commands.append(
            f"aws lambda update-function-configuration --function-name {function_name} "
            f"--dead-letter-config TargetArn=arn:aws:sqs:REGION:ACCOUNT:dlq-queue"
        )

    # 2. Reserved Concurrency
    concurrency = dim_map.get("ReservedConcurrency")
    if concurrency is None:
        score -= 1
        gaps.append(ResilienceGap(
            name="Reserved Concurrency",
            status="NOT SET",
            impact="Function shares concurrency pool; noisy neighbors can throttle it.",
        ))
        recommendations.append("Set reserved concurrency to guarantee execution capacity.")
        cli_commands.append(
            f"aws lambda put-function-concurrency --function-name {function_name} "
            f"--reserved-concurrent-executions 100"
        )

    # 3. Retry Configuration
    retries = dim_map.get("RetryConfiguration")
    if retries is None:
        score -= 1
        gaps.append(ResilienceGap(
            name="Retry Configuration",
            status="DEFAULT (2)",
            impact="Using default retry count; may cause excessive retries or insufficient recovery.",
        ))
        recommendations.append("Explicitly configure retry attempts based on function idempotency.")
        cli_commands.append(
            f"aws lambda put-function-event-invoke-config --function-name {function_name} "
            f"--maximum-retry-attempts 1"
        )

    # 4. Maximum Event Age
    max_age = dim_map.get("MaximumEventAge")
    if max_age is None:
        score -= 1
        gaps.append(ResilienceGap(
            name="Maximum Event Age",
            status="DEFAULT (6h)",
            impact="Stale events may be processed; default 6-hour window is too long for most use cases.",
        ))
        recommendations.append("Set maximum event age to discard stale invocations.")
        cli_commands.append(
            f"aws lambda put-function-event-invoke-config --function-name {function_name} "
            f"--maximum-event-age-in-seconds 3600"
        )

    # 5. VPC / Multi-AZ
    vpc_multi_az = dim_map.get("VPCMultiAZ", False)
    multi_az = dim_map.get("MultiAZ", False)
    if not vpc_multi_az and not multi_az:
        score -= 1
        gaps.append(ResilienceGap(
            name="VPC Multi-AZ",
            status="NOT IN VPC or SINGLE AZ",
            impact="Function not deployed across multiple AZs; AZ failure impacts availability.",
        ))
        recommendations.append("Deploy Lambda in a VPC with subnets across multiple AZs.")

    # 6. Memory
    memory = dim_map.get("Memory", 128)
    if memory and memory <= 128:
        gaps.append(ResilienceGap(
            name="Memory Allocation",
            status=f"{memory} MB",
            impact="Minimum memory; may cause slow cold starts and timeouts under load.",
        ))
        recommendations.append("Consider increasing memory allocation for better CPU and performance.")

    # 7. Timeout
    timeout = dim_map.get("Timeout", 3)
    if timeout and timeout <= 3:
        gaps.append(ResilienceGap(
            name="Timeout Configuration",
            status=f"{timeout}s",
            impact="Very short timeout; downstream latency spikes will cause failures.",
        ))
        recommendations.append("Increase timeout to accommodate downstream service latency.")

    # 8. SnapStart
    snap_start = dim_map.get("SnapStart", {})
    if isinstance(snap_start, dict) and snap_start.get("ApplyOn") == "None":
        gaps.append(ResilienceGap(
            name="SnapStart",
            status="DISABLED",
            impact="Cold starts not optimized; higher latency for Java/Python runtimes.",
        ))

    score = max(0, min(10, score))

    total_issues = len([g for g in gaps if "ENABLED" not in g.status])
    if score >= 8:
        summary = f"Lambda '{function_name}' has a strong reliability posture with {total_issues} minor gap(s)."
    elif score >= 5:
        summary = f"Lambda '{function_name}' has moderate reliability risks. {total_issues} gap(s) need attention."
    else:
        summary = f"Lambda '{function_name}' has significant reliability gaps. {total_issues} issue(s) require remediation."

    return ResourceResilienceOutput(
        recommendations=recommendations,
        aws_commands_to_fix=cli_commands,
        report=ResiliencyReport(
            resource_name=function_name,
            resilience_gaps=gaps,
            overall_resilience_score=score,
            max_resilience_score=10,
            summary=summary,
        ),
    )
