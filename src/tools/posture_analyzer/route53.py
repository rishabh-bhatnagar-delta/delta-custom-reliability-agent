from typing import List, Dict, Any

from src.models.resiliency_report import (
    ResourceResilienceOutput,
    ResiliencyReport,
    ResilienceGap,
)


def get_route53_resilience_report(zone_id: str, dimensions: List[Dict[str, Any]]) -> ResourceResilienceOutput:
    """Rule-based resilience evaluation for Route 53 hosted zones."""
    dim_map = {d["name"]: d.get("value") for d in dimensions}

    gaps: List[ResilienceGap] = []
    recommendations: List[str] = []
    cli_commands: List[str] = []
    score = 10

    # 1. DNSSEC
    if not dim_map.get("DNSSEC", False):
        score -= 1
        gaps.append(ResilienceGap(
            name="DNSSEC",
            status="DISABLED",
            impact="DNS responses are not cryptographically signed; vulnerable to spoofing.",
        ))
        recommendations.append("Enable DNSSEC signing for the hosted zone.")
        cli_commands.append(
            f"aws route53 enable-hosted-zone-dnssec --hosted-zone-id {zone_id}"
        )

    # 2. Query Logging
    if not dim_map.get("QueryLogging", False):
        score -= 1
        gaps.append(ResilienceGap(
            name="Query Logging",
            status="NOT CONFIGURED",
            impact="No visibility into DNS query patterns; harder to diagnose issues.",
        ))
        recommendations.append("Enable query logging to CloudWatch Logs for DNS observability.")

    # 3. Routing analysis — posture determination per record group
    routing_analysis = dim_map.get("RoutingAnalysis", [])
    for group in routing_analysis:
        name = group.get("Name", "")
        records = group.get("Records", [])
        record_count = group.get("RecordCount", 0)

        if record_count <= 1:
            # Single record — siloed unless it's an alias
            rs = records[0] if records else {}
            if not rs.get("AliasTarget"):
                gaps.append(ResilienceGap(
                    name=f"Record: {name}",
                    status="SILOED",
                    impact="Single record with no routing policy; no failover capability.",
                ))
                recommendations.append(
                    f"Record '{name}' is siloed. Consider adding failover or weighted routing."
                )
                score -= 1
            continue

        # Multiple records — determine routing policy
        has_failover = any(r.get("Failover") for r in records)
        has_weight = any(r.get("Weight") is not None for r in records)
        has_multivalue = any(r.get("MultiValueAnswer") for r in records)

        if has_failover:
            # Active-Passive — check edge cases
            primary = [r for r in records if r.get("Failover") == "PRIMARY"]
            if primary and not primary[0].get("HealthCheckId"):
                score -= 2
                gaps.append(ResilienceGap(
                    name=f"Failover: {name}",
                    status="PRIMARY HAS NO HEALTH CHECK",
                    impact="SECONDARY will never activate; failover is non-functional.",
                ))
                recommendations.append(
                    f"Attach a health check to the PRIMARY record for '{name}'. "
                    f"Without it, the SECONDARY record will never be used."
                )
            for r in records:
                if not r.get("HealthCheckId"):
                    gaps.append(ResilienceGap(
                        name=f"Failover Record: {name} ({r.get('Failover', 'UNKNOWN')})",
                        status="NO HEALTH CHECK",
                        impact="Record is static; Route 53 cannot detect failures.",
                    ))

        elif has_weight:
            weights = [r.get("Weight", 0) for r in records]
            non_zero = [w for w in weights if w > 0]
            if len(non_zero) == 1 and 0 in weights:
                # Manual Active-Passive
                gaps.append(ResilienceGap(
                    name=f"Weighted: {name}",
                    status="MANUAL ACTIVE-PASSIVE",
                    impact="One record has weight 0; traffic only flows to one endpoint. Manual failover required.",
                ))
                recommendations.append(
                    f"Weighted record '{name}' has a weight-0 record. "
                    f"This is manual active-passive. Consider using failover routing instead."
                )

        elif has_multivalue:
            missing_hc = [r for r in records if not r.get("HealthCheckId")]
            if missing_hc:
                score -= 1
                gaps.append(ResilienceGap(
                    name=f"Multivalue: {name}",
                    status="UNHEALTHY-BLIND",
                    impact="Multivalue records without health checks serve traffic to unhealthy endpoints.",
                ))
                recommendations.append(
                    f"Attach health checks to all multivalue records for '{name}'. "
                    f"Without them, Route 53 cannot filter unhealthy endpoints."
                )

    # 4. Health checks
    health_checks = dim_map.get("HealthChecks", [])
    disabled_hcs = [hc for hc in health_checks if hc.get("Disabled")]
    if disabled_hcs:
        score -= 1
        gaps.append(ResilienceGap(
            name="Disabled Health Checks",
            status=f"{len(disabled_hcs)} DISABLED",
            impact="Disabled health checks provide no failure detection.",
        ))
        recommendations.append("Re-enable or remove disabled health checks.")

    records_with_hc = dim_map.get("RecordsWithHealthChecks", 0)
    total_records = dim_map.get("TotalUserRecords", 0)
    if total_records > 0 and records_with_hc == 0:
        score -= 1
        gaps.append(ResilienceGap(
            name="Health Check Coverage",
            status="NONE",
            impact="No records have health checks; Route 53 cannot detect endpoint failures.",
        ))
        recommendations.append("Attach health checks to critical records for automated failure detection.")

    score = max(0, min(10, score))

    total_issues = len(gaps)
    if score >= 8:
        summary = f"Route 53 zone '{zone_id}' has a strong reliability posture with {total_issues} minor gap(s)."
    elif score >= 5:
        summary = f"Route 53 zone '{zone_id}' has moderate reliability risks. {total_issues} gap(s) need attention."
    else:
        summary = f"Route 53 zone '{zone_id}' has significant reliability gaps. {total_issues} issue(s) require remediation."

    return ResourceResilienceOutput(
        recommendations=recommendations,
        aws_commands_to_fix=cli_commands,
        report=ResiliencyReport(
            resource_name=zone_id,
            resilience_gaps=gaps,
            overall_resilience_score=score,
            max_resilience_score=10,
            summary=summary,
        ),
    )
