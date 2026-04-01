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


def _classify_routing(records: list) -> str:
    """Classify a record group's HA pattern: ACTIVE-ACTIVE, ACTIVE-PASSIVE, or NO FAILOVER."""
    if len(records) <= 1:
        return "NO FAILOVER"

    has_failover = any(r.get("Failover") for r in records)
    has_weight = any(r.get("Weight") is not None for r in records)
    has_multivalue = any(r.get("MultiValueAnswer") for r in records)
    has_latency = any(r.get("Region") for r in records)
    has_geo = any(r.get("GeoLocation") for r in records)

    if has_failover:
        return "ACTIVE-PASSIVE"

    if has_weight:
        weights = [r.get("Weight", 0) for r in records]
        non_zero = [w for w in weights if w > 0]
        if len(non_zero) <= 1 and 0 in weights:
            return "ACTIVE-PASSIVE"
        return "ACTIVE-ACTIVE"

    if has_latency or has_geo or has_multivalue:
        return "ACTIVE-ACTIVE"

    return "NO FAILOVER"


def _analyze_record_group(a: ResilienceAnalyzer, group: dict):
    name = group.get("Name", "")
    records = group.get("Records") or []
    classification = _classify_routing(records)

    # --- NO FAILOVER ---
    if classification == "NO FAILOVER":
        rs = records[0] if records else {}
        if rs.get("AliasTarget"):
            a.add_gap(f"Record: {name}", "NO FAILOVER (ALIAS)",
                       "Single alias record; HA depends on the target resource configuration.",
                       penalty=0)
        else:
            a.add_gap(f"Record: {name}", "NO FAILOVER",
                       "Single record with no routing policy; no failover capability.",
                       penalty=1, recommendation=f"Record '{name}' has no failover. Consider adding failover or weighted routing.")
        return

    # --- Emit HA classification ---
    # Determine the routing policy type for reasoning
    has_failover = any(r.get("Failover") for r in records)
    has_weight = any(r.get("Weight") is not None for r in records)
    has_multivalue = any(r.get("MultiValueAnswer") for r in records)
    has_latency = any(r.get("Region") for r in records)
    has_geo = any(r.get("GeoLocation") for r in records)

    if classification == "ACTIVE-PASSIVE":
        if has_failover:
            reason = f"Failover routing detected (PRIMARY/SECONDARY records). Traffic goes to PRIMARY; SECONDARY activates only on PRIMARY health check failure."
        elif has_weight:
            weights = [r.get("Weight", 0) for r in records]
            reason = f"Weighted routing with weights={weights}. Only one record has non-zero weight; manual failover required."
        else:
            reason = "Routing policy indicates active-passive pattern."
        a.add_gap(f"Failover Configuration: {name}", "ACTIVE-PASSIVE", reason, penalty=0)
    else:
        if has_latency:
            regions = [r.get("Region") for r in records if r.get("Region")]
            reason = f"Latency-based routing across regions={regions}. Traffic routed to lowest-latency endpoint."
        elif has_geo:
            reason = f"Geolocation routing with {len(records)} geo records. Traffic routed by geographic location."
        elif has_weight:
            weights = [r.get("Weight", 0) for r in records]
            reason = f"Weighted routing with weights={weights}. Multiple records with non-zero weights share traffic."
        elif has_multivalue:
            reason = f"Multivalue answer routing with {len(records)} records. Multiple IPs returned to clients."
        else:
            reason = "Multiple records distribute traffic across endpoints."
        a.add_gap(f"Failover Configuration: {name}", "ACTIVE-ACTIVE", reason, penalty=0)

    # --- Policy-specific gap checks ---

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
        if len(non_zero) <= 1 and 0 in weights:
            a.add_gap(f"Weighted: {name}", "MANUAL ACTIVE-PASSIVE",
                       "One record has weight 0; traffic only flows to one endpoint. Manual failover required.",
                       recommendation=f"Weighted record '{name}' has a weight-0 record. Consider using failover routing instead.")
        if len(non_zero) > 1 and any(not r.get("HealthCheckId") for r in records):
            a.add_gap(f"Weighted AA: {name}", "MISSING HEALTH CHECKS",
                       "Active-Active weighted records without health checks will route traffic to unhealthy endpoints.",
                       penalty=1, recommendation=f"Attach health checks to all weighted records for '{name}' to enable automatic unhealthy-endpoint removal.")

    elif has_latency:
        regions = [r.get("Region") for r in records if r.get("Region")]
        if len(set(regions)) < 2:
            a.add_gap(f"Latency: {name}", "SINGLE REGION",
                       "Latency-based routing with only one region provides no cross-region failover.",
                       penalty=1, recommendation=f"Add records in additional regions for '{name}' to enable cross-region latency-based routing.")
        if any(not r.get("HealthCheckId") for r in records):
            a.add_gap(f"Latency: {name}", "MISSING HEALTH CHECKS",
                       "Latency-based records without health checks will route to unhealthy regional endpoints.",
                       penalty=1, recommendation=f"Attach health checks to all latency-based records for '{name}'.")

    elif has_geo:
        has_default = any(r.get("GeoLocation", {}).get("CountryCode") == "*" for r in records)
        if not has_default:
            a.add_gap(f"Geolocation: {name}", "NO DEFAULT RECORD",
                       "Queries from unmapped locations will get NXDOMAIN (no answer).",
                       penalty=2, recommendation=f"Add a default geolocation record (CountryCode='*') for '{name}' to handle queries from unmapped locations.")
        if any(not r.get("HealthCheckId") for r in records):
            a.add_gap(f"Geolocation: {name}", "MISSING HEALTH CHECKS",
                       "Geolocation records without health checks will route to unhealthy endpoints in that region.",
                       penalty=1, recommendation=f"Attach health checks to all geolocation records for '{name}'.")

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
