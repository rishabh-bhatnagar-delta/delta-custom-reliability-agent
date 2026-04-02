import json
import logging
import os
import uuid

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv

load_dotenv()

from src.core.prompts import REPORT_GENERATION_PROMPT

logger = logging.getLogger(__name__)


def _save_report(markdown: str, block_code: str):
    """Save report to local reports directory."""
    from datetime import datetime
    reports_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "reports", block_code)
    os.makedirs(reports_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{timestamp}.md"
    filepath = os.path.join(reports_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(markdown)
    logger.info(f"report_generator: report saved to {filepath} ({len(markdown)} chars)")


def _build_condensed_summary(audit_data: dict) -> dict:
    """Build a condensed version of audit data for Bedrock (avoids token limits)."""
    summary = audit_data.get("application_summary", {})

    # Condense resource audits to just gap names
    condensed_resources = []
    for r in audit_data.get("resource_audits", []):
        report = r.get("resilience_report", {}).get("report", {})
        condensed_resources.append({
            "resource": r.get("physical_id"),
            "type": r.get("resource_type"),
            "stack": r.get("stack_name"),
            "gaps": [g.get("name") for g in report.get("resilience_gaps", [])],
        })

    return {
        "block_code": summary.get("block_code"),
        "total_stacks": summary.get("total_stacks"),
        "total_resources": summary.get("total_resources"),
        "resources_analyzed": summary.get("resources_analyzed"),
        "resources_unsupported": summary.get("resources_unsupported"),
        "total_gaps": summary.get("total_gaps"),
        "critical_gaps": summary.get("critical_gaps", []),
        "resources": condensed_resources,
    }


def _call_bedrock(prompt: str) -> str:
    """Call Bedrock agent and return the response text."""
    agent_id = os.getenv('BEDROCK_AGENT_ID')
    agent_alias_id = os.getenv('BEDROCK_AGENT_ALIAS_ID')
    bedrock_profile = os.getenv('BEDROCK_AWS_PROFILE', os.getenv('AWS_PROFILE'))
    bedrock_region = os.getenv('BEDROCK_AWS_REGION', os.getenv('AWS_REGION', 'us-east-1'))

    bedrock = boto3.Session(profile_name=bedrock_profile).client(
        'bedrock-agent-runtime',
        region_name=bedrock_region,
    )
    response = bedrock.invoke_agent(
        inputText=prompt,
        agentId=agent_id,
        agentAliasId=agent_alias_id,
        sessionId=str(uuid.uuid4()),
        enableTrace=False,
    )

    full_response = ""
    for event in response['completion']:
        chunk = event.get('chunk', {})
        if 'bytes' in chunk:
            full_response += chunk['bytes'].decode('utf-8')
    return full_response


def generate_markdown_report(audit_data: dict) -> str:
    """Generate a detailed markdown report: structured template + Bedrock AI insights."""
    block_code = audit_data.get("application_summary", {}).get("block_code", "unknown")
    total_resources = audit_data.get("application_summary", {}).get("resources_analyzed", 0)
    total_gaps = audit_data.get("application_summary", {}).get("total_gaps", 0)

    logger.info(f"report_generator: starting report for '{block_code}' ({total_resources} resources, {total_gaps} gaps)")

    # Always generate the structured report (handles any size)
    logger.info(f"report_generator: building structured report for '{block_code}'")
    structured_report = _build_structured_report(audit_data)
    logger.info(f"report_generator: structured report built ({len(structured_report)} chars)")

    # Try to get AI-generated insights from Bedrock
    logger.info(f"report_generator: requesting AI insights from Bedrock for '{block_code}'")
    ai_section = _generate_ai_insights(audit_data)

    # Combine: AI insights at the top, structured data below
    if ai_section:
        logger.info(f"report_generator: AI insights received ({len(ai_section)} chars), combining with structured report")
        report = ai_section + "\n\n---\n\n" + structured_report
    else:
        logger.info("report_generator: no AI insights, using structured report only")
        report = structured_report

    logger.info(f"report_generator: saving report for '{block_code}' ({len(report)} chars)")
    _save_report(report, block_code)
    logger.info(f"report_generator: report generation complete for '{block_code}'")
    return report


def _generate_ai_insights(audit_data: dict) -> str:
    """Generate executive summary and cross-cutting observations via Bedrock."""
    agent_id = os.getenv('BEDROCK_AGENT_ID')
    agent_alias_id = os.getenv('BEDROCK_AGENT_ALIAS_ID')

    if not agent_id or not agent_alias_id:
        logger.warning("report_generator: Bedrock agent not configured, skipping AI insights")
        return ""

    logger.info("report_generator: building condensed summary for Bedrock")
    condensed = _build_condensed_summary(audit_data)
    prompt = REPORT_GENERATION_PROMPT + json.dumps(condensed, indent=2)
    logger.info(f"report_generator: sending prompt to Bedrock ({len(prompt)} chars)")

    try:
        response = _call_bedrock(prompt)
        if response.strip():
            logger.info(f"report_generator: Bedrock returned AI insights ({len(response)} chars)")
            return response
        logger.warning("report_generator: Bedrock returned empty response")
        return ""
    except (ClientError, Exception) as e:
        logger.error(f"report_generator: Bedrock AI insights failed: {e}")
        return ""


def _build_structured_report(audit_data: dict) -> str:
    """Deterministic structured report — always complete, no token limits."""
    summary = audit_data.get("application_summary", {})
    resource_audits = audit_data.get("resource_audits", [])
    logger.info(f"report_generator: building structured report — {len(resource_audits)} resource audit(s)")
    lines = [
        f"# Detailed Audit Data: {summary.get('block_code', 'Unknown')}",
        "",
        "## Application Overview",
        "",
        "| Metric | Value |",
        "|---|---|",
        f"| Stacks | {summary.get('total_stacks', 0)} |",
        f"| Total Resources | {summary.get('total_resources', 0)} |",
        f"| Analyzed | {summary.get('resources_analyzed', 0)} |",
        f"| Unsupported | {summary.get('resources_unsupported', 0)} |",
        f"| Skipped | {summary.get('resources_skipped', 0)} |",
        f"| Errors | {summary.get('resources_errored', 0)} |",
        f"| Total Gaps | {summary.get('total_gaps', 0)} |",
        "",
    ]

    # --- Failover Configuration Summary ---
    failover_entries = []
    for r in audit_data.get("resource_audits", []):
        report = r.get("resilience_report", {}).get("report", {})
        for g in report.get("resilience_gaps", []):
            if g.get("name", "").startswith("Failover Configuration"):
                dims = {d.get("name"): d.get("value") for d in r.get("dimensions", [])}
                resource_type = r.get("resource_type", "")
                status = g.get("status", "")
                evidence_parts = []

                if "RDS" in resource_type:
                    gc = dims.get("GlobalClusterMembers", [])
                    has_secondary = any(not m.get("IsWriter") for m in gc if isinstance(m, dict)) if gc else False

                    if has_secondary:
                        secondary_members = [m for m in gc if isinstance(m, dict) and not m.get("IsWriter")]
                        secondary_arns = [m.get("DBClusterArn", "") for m in secondary_members]
                        secondary_regions = []
                        for arn in secondary_arns:
                            parts = arn.split(":")
                            if len(parts) >= 4:
                                secondary_regions.append(parts[3])
                        write_fwd_statuses = [m.get("GlobalWriteForwardingStatus", "disabled") for m in secondary_members]
                        evidence_parts.append(f"GlobalClusterIdentifier={dims.get('GlobalClusterIdentifier')}")
                        evidence_parts.append(f"SecondaryRegions={secondary_regions if secondary_regions else len(secondary_arns)}")
                        evidence_parts.append(f"GlobalWriteForwardingStatus={write_fwd_statuses[0] if len(write_fwd_statuses) == 1 else write_fwd_statuses}")
                    elif status == "ACTIVE-ACTIVE" and dims.get("ClusterIdentifier"):
                        evidence_parts.append(f"ClusterIdentifier={dims.get('ClusterIdentifier')}")
                        evidence_parts.append(f"ClusterReaders={dims.get('ClusterReaders')}")
                        evidence_parts.append(f"MultiAZ={dims.get('MultiAZ')}")
                    elif status == "ACTIVE-PASSIVE" and dims.get("MultiAZ"):
                        evidence_parts.append(f"MultiAZ={dims.get('MultiAZ')}")
                        if dims.get("ClusterIdentifier"):
                            evidence_parts.append(f"ClusterReaders={dims.get('ClusterReaders')}")
                    elif status == "ACTIVE-PASSIVE" and dims.get("ReadReplicaIDs"):
                        evidence_parts.append(f"MultiAZ={dims.get('MultiAZ')}")
                        evidence_parts.append(f"ReadReplicas={len(dims.get('ReadReplicaIDs', []))}")
                    else:
                        evidence_parts.append(f"MultiAZ={dims.get('MultiAZ')}")
                        evidence_parts.append(f"ReadReplicas={len(dims.get('ReadReplicaIDs', []))}")
                        evidence_parts.append(f"GlobalCluster={'Yes' if dims.get('GlobalClusterIdentifier') else 'No'}")

                elif "EC2" in resource_type:
                    asg = dims.get("ASGDetail")
                    if not asg or not isinstance(asg, dict):
                        evidence_parts.append("AutoScalingGroup=None")
                    elif status == "NO FAILOVER" and asg.get("DesiredCapacity", 0) == 0:
                        evidence_parts.append(f"ASG={asg.get('Name', '?')}")
                        evidence_parts.append(f"DesiredCapacity={asg.get('DesiredCapacity')}")
                        evidence_parts.append(f"MinSize={asg.get('MinSize')}")
                    elif status == "ACTIVE-PASSIVE" and asg.get("DesiredCapacity", 0) == 1:
                        evidence_parts.append(f"DesiredCapacity=1")
                        evidence_parts.append(f"AZs={asg.get('AvailabilityZones', [])}")
                    elif status == "ACTIVE-PASSIVE":
                        evidence_parts.append(f"DesiredCapacity={asg.get('DesiredCapacity')}")
                        evidence_parts.append(f"AZs={asg.get('AvailabilityZones', [])}")
                        tg = asg.get("TargetGroupARNs", [])
                        evidence_parts.append(f"TargetGroups={len(tg)}")
                        if tg:
                            th = asg.get("TargetGroupHealth", [])
                            healthy = sum(1 for t in th if isinstance(t, dict) and t.get("HealthState") == "healthy")
                            evidence_parts.append(f"HealthyTargets={healthy}")
                    elif status == "ACTIVE-ACTIVE":
                        evidence_parts.append(f"DesiredCapacity={asg.get('DesiredCapacity')}")
                        evidence_parts.append(f"AZs={asg.get('AvailabilityZones', [])}")
                        th = asg.get("TargetGroupHealth", [])
                        healthy = sum(1 for t in th if isinstance(t, dict) and t.get("HealthState") == "healthy")
                        evidence_parts.append(f"HealthyTargets={healthy}")

                elif "Route53" in resource_type:
                    # Evidence is already well-described in the impact text for Route53
                    pass

                elif "DynamoDB" in resource_type:
                    global_regions = dims.get("GlobalTableRegions", [])
                    streams = dims.get("StreamsConfiguration", {})
                    stream_enabled = streams.get("StreamEnabled", False) if isinstance(streams, dict) else False
                    stream_view = streams.get("StreamViewType", "N/A") if isinstance(streams, dict) else "N/A"
                    if status == "ACTIVE-ACTIVE" and global_regions:
                        evidence_parts.append(f"GlobalTableRegions={global_regions}")
                        evidence_parts.append(f"StreamEnabled={stream_enabled}")
                        evidence_parts.append(f"StreamViewType={stream_view}")
                    else:
                        evidence_parts.append(f"GlobalTableRegions=[]")

                evidence_str = "; ".join(evidence_parts) if evidence_parts else ""

                record_name = ""
                record_type = ""
                if "Route53" in resource_type:
                    raw = g.get("name", "").replace("Failover Configuration: ", "").replace("Failover Configuration", "")
                    # Format is "domain.com. (A)" — extract name and type
                    import re
                    m = re.match(r'^(.+?)\s+\((\w+)\)$', raw)
                    if m:
                        record_name = m.group(1)
                        record_type = m.group(2)
                    else:
                        record_name = raw

                failover_entries.append({
                    "resource": r.get("physical_id", ""),
                    "type": r.get("resource_type", ""),
                    "stack": r.get("stack_name", ""),
                    "region": r.get("region", ""),
                    "status": status,
                    "impact": g.get("impact", ""),
                    "evidence": evidence_str,
                    "record_name": record_name,
                    "record_type": record_type,
                })

    if failover_entries:
        logger.info(f"report_generator: found {len(failover_entries)} resource(s) with failover configuration")
        lines.extend([
            "## Failover Configuration Summary",
            "",
        ])
        for entry in failover_entries:
            heading = entry['resource']
            if entry.get('record_name'):
                heading = f"{entry['resource']} — {entry['record_name']}"
            lines.extend([
                f"### {heading}",
                "",
                f"| Field | Value |",
                f"|---|---|",
                f"| Resource Type | {entry['type']} |",
                f"| Stack | {entry['stack']} |",
                f"| Region | {entry['region']} |",
            ])
            if entry.get('record_name'):
                lines.append(f"| Record | {entry['record_name']} |")
            if entry.get('record_type'):
                lines.append(f"| Record Type | {entry['record_type']} |")
            lines.extend([
                f"| Classification | **{entry['status']}** |",
                f"| Reasoning | {entry['impact']} |",
            ])
            if entry['evidence']:
                lines.append(f"| Key Evidence | `{entry['evidence']}` |")
            lines.extend(["", ""])


    lines.extend([
        "## Critical Findings",
        "",
        "| Resource | Type | Stack | Finding | Status | Impact |",
        "|---|---|---|---|---|---|",
    ])

    for gap in summary.get("critical_gaps", []):
        lines.append(
            f"| {gap.get('resource', '')} | {gap.get('resource_type', '')} | "
            f"{gap.get('stack', '')} | {gap.get('gap_name', '')} | "
            f"{gap.get('gap_status', '')} | {gap.get('gap_impact', '')} |"
        )

    lines.extend(["", "## Resource Audits", ""])

    for r in audit_data.get("resource_audits", []):
        report = r.get("resilience_report", {}).get("report", {})
        lines.append(f"### {r.get('physical_id', 'Unknown')} ({r.get('resource_type', '')})")
        lines.append("")
        lines.append(f"**Stack:** {r.get('stack_name', '')}  ")
        lines.append(f"**Summary:** {report.get('summary', '')}")
        lines.append("")

        dims = r.get("dimensions", [])
        if dims:
            lines.append("**Evidence (Dimensions):**")
            lines.append("")
            lines.append("| Dimension | Value |")
            lines.append("|---|---|")
            for d in dims:
                val = d.get("value")
                if isinstance(val, (dict, list)):
                    val = json.dumps(val, default=str)
                lines.append(f"| {d.get('name', '')} | {val} |")
            lines.append("")

        gaps = report.get("resilience_gaps", [])
        if gaps:
            lines.append("**Gaps:**")
            lines.append("")
            lines.append("| Finding | Status | Impact |")
            lines.append("|---|---|---|")
            for g in gaps:
                lines.append(f"| {g.get('name', '')} | {g.get('status', '')} | {g.get('impact', '')} |")
            lines.append("")

        recs = r.get("resilience_report", {}).get("recommendations", [])
        if recs:
            lines.append("**Recommendations:**")
            lines.append("")
            for rec in recs:
                lines.append(f"- {rec}")
            lines.append("")

        cmds = r.get("resilience_report", {}).get("aws_commands_to_fix", [])
        if cmds:
            lines.append("**Fix Commands:**")
            lines.append("")
            lines.append("```bash")
            for cmd in cmds:
                lines.append(cmd)
            lines.append("```")
            lines.append("")

    skipped = audit_data.get("skipped_resources", [])
    if skipped:
        lines.extend(["## Skipped & Unsupported Resources", ""])
        lines.append("| Resource | Type | Stack | Reason |")
        lines.append("|---|---|---|---|")
        for s in skipped:
            lines.append(
                f"| {s.get('physical_id', 'N/A')} | {s.get('resource_type', '')} | "
                f"{s.get('stack_name', '')} | {s.get('reason', s.get('audit_status', ''))} |"
            )
        lines.append("")

    recs = summary.get("recommendations", [])
    if recs:
        lines.extend(["## All Recommendations", ""])
        for i, rec in enumerate(recs, 1):
            lines.append(f"{i}. {rec}")
        lines.append("")

    return "\n".join(lines)
