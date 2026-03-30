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
    logger.info(f"Report saved to {filepath}")


def _build_condensed_summary(audit_data: dict) -> dict:
    """Build a condensed version of audit data for Bedrock (avoids token limits)."""
    summary = audit_data.get("application_summary", {})

    # Condense resource audits to just scores and gap names
    condensed_resources = []
    for r in audit_data.get("resource_audits", []):
        report = r.get("resilience_report", {}).get("report", {})
        condensed_resources.append({
            "resource": r.get("physical_id"),
            "type": r.get("resource_type"),
            "stack": r.get("stack_name"),
            "score": report.get("overall_resilience_score"),
            "gaps": [g.get("name") for g in report.get("resilience_gaps", [])],
        })

    return {
        "block_code": summary.get("block_code"),
        "total_stacks": summary.get("total_stacks"),
        "total_resources": summary.get("total_resources"),
        "resources_analyzed": summary.get("resources_analyzed"),
        "resources_unsupported": summary.get("resources_unsupported"),
        "application_score": summary.get("application_resilience_score"),
        "lowest_score": summary.get("lowest_resource_score"),
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

    # Always generate the structured report (handles any size)
    structured_report = _build_structured_report(audit_data)

    # Try to get AI-generated insights from Bedrock
    ai_section = _generate_ai_insights(audit_data)

    # Combine: AI insights at the top, structured data below
    if ai_section:
        report = ai_section + "\n\n---\n\n" + structured_report
    else:
        report = structured_report

    _save_report(report, block_code)
    return report


def _generate_ai_insights(audit_data: dict) -> str:
    """Generate executive summary and cross-cutting observations via Bedrock."""
    agent_id = os.getenv('BEDROCK_AGENT_ID')
    agent_alias_id = os.getenv('BEDROCK_AGENT_ALIAS_ID')

    if not agent_id or not agent_alias_id:
        logger.warning("Bedrock agent not configured, skipping AI insights")
        return ""

    condensed = _build_condensed_summary(audit_data)
    prompt = REPORT_GENERATION_PROMPT + json.dumps(condensed, indent=2)

    try:
        response = _call_bedrock(prompt)
        if response.strip():
            return response
        logger.warning("Bedrock returned empty response")
        return ""
    except (ClientError, Exception) as e:
        logger.error(f"Bedrock AI insights failed: {e}")
        return ""


def _build_structured_report(audit_data: dict) -> str:
    """Deterministic structured report — always complete, no token limits."""
    summary = audit_data.get("application_summary", {})
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
        f"| **Application Score** | **{summary.get('application_resilience_score', 0)}/10** |",
        f"| Lowest Resource Score | {summary.get('lowest_resource_score', 0)}/10 |",
        f"| Total Gaps | {summary.get('total_gaps', 0)} |",
        "",
        "## Critical Findings",
        "",
        "| Resource | Type | Stack | Finding | Status | Impact |",
        "|---|---|---|---|---|---|",
    ]

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
        lines.append(f"**Score:** {report.get('overall_resilience_score', '?')}/10  ")
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
