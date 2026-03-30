from typing import List, Dict, Any

from src.models.resiliency_report import ResourceResilienceOutput
from src.tools.posture_analyzer.base import ResilienceAnalyzer


def get_route53_resilience_report(zone_id: str, dimensions: List[Dict[str, Any]]) -> ResourceResilienceOutput:
    """Rule-based resilience evaluation for Route 53 hosted zones."""
    a = ResilienceAnalyzer(zone_id, dimensions)

    # DNSSEC
    if not a.dim("DNSSEC", False):
        a.add_gap("DNSSEC", "DISABLED",
                   "DNS responses are not cryptographically signed; vulnerable to spoofing.",
                   penalty=1, recommendation="Enable DNSSEC signing for the hosted zone.",
                   cli=f"aws route53 enable-hosted-zone-dnssec --hosted-zone-id {zone_id}")

    # Query Logging
    if not a.dim("QueryLogging", False):
        a.add_gap("Query Logging", "NOT CONFIGURED",
                   "No visibility into DNS query patterns; harder to diagnose issues.",
                   penalty=1, recommendation="Enable query logging to CloudWatch Logs for DNS observability.")

    # Routing analysis per record group
    routing_analysis = a.dim("RoutingAnalysis", [])
    if routing_analysis:
        for group in routing_analysis:
            _analyze_record_group(a, group)

    # Health check coverage
    _analyze_health_checks(a)

    return a.build(f"Route 53 zone '{zone_id}'")


def _analyze_record_group(a: ResilienceAnalyzer, group: dict):
    name = group.get("Name", "")
    records = group.get("Records") or []

    if group.get("RecordCount", 0) <= 1:
        rs = records[0] if records else {}
        if not rs.get("AliasTarget"):
            a.add_gap(f"Record: {name}", "SILOED",
                       "Single record with no routing policy; no failover capability.",
                       penalty=1, recommendation=f"Record '{name}' is siloed. Consider adding failover or weighted routing.")
        return

    has_failover = any(r.get("Failover") for r in records)
    has_weight = any(r.get("Weight") is not None for r in records)
    has_multivalue = any(r.get("MultiValueAnswer") for r in records)

    if has_failover:
        primary = [r for r in records if r.get("Failover") == "PRIMARY"]
        if primary and not primary[0].get("HealthCheckId"):
            a.add_gap(f"Failover: {name}", "PRIMARY HAS NO HEALTH CHECK",
                       "SECONDARY will never activate; failover is non-functional.",
                       penalty=2, recommendation=f"Attach a health check to the PRIMARY record for '{name}'. Without it, the SECONDARY record will never be used.")
        for r in records:
            if not r.get("HealthCheckId"):
                a.add_gap(f"Failover Record: {name} ({r.get('Failover', 'UNKNOWN')})", "NO HEALTH CHECK",
                           "Record is static; Route 53 cannot detect failures.")

    elif has_weight:
        weights = [r.get("Weight", 0) for r in records]
        non_zero = [w for w in weights if w > 0]
        if len(non_zero) == 1 and 0 in weights:
            a.add_gap(f"Weighted: {name}", "MANUAL ACTIVE-PASSIVE",
                       "One record has weight 0; traffic only flows to one endpoint. Manual failover required.",
                       recommendation=f"Weighted record '{name}' has a weight-0 record. Consider using failover routing instead.")

    elif has_multivalue:
        if any(not r.get("HealthCheckId") for r in records):
            a.add_gap(f"Multivalue: {name}", "UNHEALTHY-BLIND",
                       "Multivalue records without health checks serve traffic to unhealthy endpoints.",
                       penalty=1, recommendation=f"Attach health checks to all multivalue records for '{name}'.")


def _analyze_health_checks(a: ResilienceAnalyzer):
    health_checks = a.dim("HealthChecks") or []
    disabled_hcs = [hc for hc in health_checks if isinstance(hc, dict) and hc.get("Disabled")]
    if disabled_hcs:
        a.add_gap("Disabled Health Checks", f"{len(disabled_hcs)} DISABLED",
                   "Disabled health checks provide no failure detection.",
                   penalty=1, recommendation="Re-enable or remove disabled health checks.")

    total_records = a.dim("TotalUserRecords", 0) or 0
    if total_records > 0 and (a.dim("RecordsWithHealthChecks", 0) or 0) == 0:
        a.add_gap("Health Check Coverage", "NONE",
                   "No records have health checks; Route 53 cannot detect endpoint failures.",
                   penalty=1, recommendation="Attach health checks to critical records for automated failure detection.")
