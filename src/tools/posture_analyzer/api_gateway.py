from typing import List, Dict, Any

from src.models.resiliency_report import (
    ResourceResilienceOutput,
    ResiliencyReport,
    ResilienceGap,
)


def get_apigw_resilience_report(dimensions: List[Dict[str, Any]]) -> ResourceResilienceOutput:
    """Rule-based resilience evaluation for API Gateway."""
    dim_map = {d["name"]: d.get("value") for d in dimensions}
    resource_name = dim_map.get("ResourceName", "Unknown API Gateway")

    gaps: List[ResilienceGap] = []
    recommendations: List[str] = []
    cli_commands: List[str] = []
    score = 10

    api_id = resource_name

    # 1. Multi-Region
    multi_region = dim_map.get("MultiRegion", False)
    if not multi_region:
        score -= 1
        gaps.append(ResilienceGap(
            name="Multi-Region Deployment",
            status="REGIONAL ONLY",
            impact="API is deployed in a single region; regional outage causes full unavailability.",
        ))
        recommendations.append("Consider deploying API in multiple regions with Route 53 failover.")

    # 2. Stage count
    stage_count = dim_map.get("StageCount", 0)
    if stage_count == 0:
        score -= 1
        gaps.append(ResilienceGap(
            name="API Stages",
            status="NONE",
            impact="No stages deployed; API is not serving traffic.",
        ))

    # 3. Caching
    cache_stages = dim_map.get("CacheEnabledStages", 0)
    if cache_stages == 0:
        score -= 2
        gaps.append(ResilienceGap(
            name="API Caching",
            status="DISABLED",
            impact="No caching configured; backend receives all requests, increasing latency and load.",
        ))
        recommendations.append("Enable API caching to reduce backend load and improve response times.")

    # 4. Throttling
    throttling_stages = dim_map.get("ThrottlingStages", 0)
    if throttling_stages == 0:
        score -= 2
        gaps.append(ResilienceGap(
            name="Throttling Configuration",
            status="NOT CONFIGURED",
            impact="No throttling; API is vulnerable to traffic spikes and abuse.",
        ))
        recommendations.append("Configure method-level throttling to protect backend services.")
        cli_commands.append(
            f"aws apigateway update-stage --rest-api-id {api_id} --stage-name prod "
            f"--patch-operations op=replace,path=/~1/throttling/rateLimit,value=1000"
        )

    # 5. Tracing
    tracing_stages = dim_map.get("TracingStages", [])
    if not tracing_stages:
        score -= 1
        gaps.append(ResilienceGap(
            name="X-Ray Tracing",
            status="DISABLED",
            impact="No distributed tracing; difficult to diagnose latency and errors.",
        ))
        recommendations.append("Enable X-Ray tracing for observability and debugging.")
        cli_commands.append(
            f"aws apigateway update-stage --rest-api-id {api_id} --stage-name prod "
            f"--patch-operations op=replace,path=/tracingEnabled,value=true"
        )

    # 6. Stages detail check
    stages = dim_map.get("Stages", [])
    if isinstance(stages, list):
        for stage in stages:
            if isinstance(stage, dict) and not stage.get("cacheEnabled") and not stage.get("tracingEnabled"):
                pass  # already covered above

    score = max(0, min(10, score))

    total_issues = len([g for g in gaps if g.status not in ("ENABLED",)])
    if score >= 8:
        summary = f"API '{resource_name}' has a solid reliability posture with {total_issues} minor gap(s)."
    elif score >= 5:
        summary = f"API '{resource_name}' has moderate reliability risks. {total_issues} gap(s) need attention."
    else:
        summary = f"API '{resource_name}' has significant reliability gaps. {total_issues} issue(s) require remediation."

    return ResourceResilienceOutput(
        recommendations=recommendations,
        aws_commands_to_fix=cli_commands,
        report=ResiliencyReport(
            resource_name=resource_name,
            resilience_gaps=gaps,
            overall_resilience_score=score,
            max_resilience_score=10,
            summary=summary,
        ),
    )
