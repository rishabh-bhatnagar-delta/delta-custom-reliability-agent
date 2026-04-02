from typing import List, Dict, Any

from src.models.resiliency_report import ResourceResilienceOutput
from src.tools.posture_analyzer.base import ResilienceAnalyzer


def get_apigw_resilience_report(dimensions: List[Dict[str, Any]]) -> ResourceResilienceOutput:
    """Rule-based resilience evaluation for API Gateway."""
    dim_map = {d["name"]: d.get("value") for d in dimensions}
    resource_name = dim_map.get("ResourceName", "Unknown API Gateway")
    a = ResilienceAnalyzer(resource_name, dimensions)

    if not a.dim("MultiRegion", False):
        a.add_gap("Multi-Region Deployment", "REGIONAL ONLY",
                   "API is deployed in a single region; regional outage causes full unavailability.",
                   recommendation="Consider deploying API in multiple regions with Route 53 failover.")

    if a.dim("StageCount", 0) == 0:
        a.add_gap("API Stages", "NONE",
                   "No stages deployed; API is not serving traffic.")

    if a.dim("CacheEnabledStages", 0) == 0:
        a.add_gap("API Caching", "DISABLED",
                   "No caching configured; backend receives all requests, increasing latency and load.",
                   recommendation="Enable API caching to reduce backend load and improve response times.")

    if a.dim("ThrottlingStages", 0) == 0:
        a.add_gap("Throttling Configuration", "NOT CONFIGURED",
                   "No throttling; API is vulnerable to traffic spikes and abuse.",
                   recommendation="Configure method-level throttling to protect backend services.",
                   cli=f"aws apigateway update-stage --rest-api-id {resource_name} --stage-name prod --patch-operations op=replace,path=/~1/throttling/rateLimit,value=1000")

    if not a.dim("TracingStages", []):
        a.add_gap("X-Ray Tracing", "DISABLED",
                   "No distributed tracing; difficult to diagnose latency and errors.",
                   recommendation="Enable X-Ray tracing for observability and debugging.",
                   cli=f"aws apigateway update-stage --rest-api-id {resource_name} --stage-name prod --patch-operations op=replace,path=/tracingEnabled,value=true")

    return a.build(f"API '{resource_name}'")
