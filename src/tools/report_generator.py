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
    reports_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "reports")
    os.makedirs(reports_dir, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"{block_code}_{timestamp}.md"
    filepath = os.path.join(reports_dir, filename)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(markdown)
    logger.info(f"Report saved to {filepath}")


def generate_markdown_report(audit_data: dict) -> str:
    """Send audit data to Bedrock agent and get back a markdown report."""
    block_code = audit_data.get("application_summary", {}).get("block_code", "unknown")
    agent_id = os.getenv('BEDROCK_AGENT_ID')
    agent_alias_id = os.getenv('BEDROCK_AGENT_ALIAS_ID')

    if not agent_id or not agent_alias_id:
        logger.warning("Bedrock agent not configured, falling back to basic report")
        report = _fallback_report(audit_data)
        _save_report(report, block_code)
        return report

    session_id = str(uuid.uuid4())
    prompt = REPORT_GENERATION_PROMPT + json.dumps(audit_data, indent=2)

    try:
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
            sessionId=session_id,
            enableTrace=False,
        )

        full_response = ""
        for event in response['completion']:
            chunk = event.get('chunk', {})
            if 'bytes' in chunk:
                full_response += chunk['bytes'].decode('utf-8')

        report = full_response if full_response.strip() else _fallback_report(audit_data)
        _save_report(report, block_code)
        return report

    except (ClientError, Exception) as e:
        logger.error(f"Bedrock report generation failed: {e}")
        report = _fallback_report(audit_data)
        _save_report(report, block_code)
        return report


def _fallback_report(audit_data: dict) -> str:
    """Basic markdown report when Bedrock is unavailable."""
    summary = audit_data.get("application_summary", {})
    lines = [
        f"# Resilience Audit Report: {summary.get('block_code', 'Unknown')}",
        "",
        "## Application Overview",
        "",
        f"| Metric | Value |",
        f"|---|---|",
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
        lines.append(f"")
        lines.append(f"**Stack:** {r.get('stack_name', '')}  ")
        lines.append(f"**Score:** {report.get('overall_resilience_score', '?')}/10  ")
        lines.append(f"**Summary:** {report.get('summary', '')}")
        lines.append("")

        # Evidence (dimensions)
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

        # Gaps
        gaps = report.get("resilience_gaps", [])
        if gaps:
            lines.append("**Gaps:**")
            lines.append("")
            lines.append("| Finding | Status | Impact |")
            lines.append("|---|---|---|")
            for g in gaps:
                lines.append(f"| {g.get('name', '')} | {g.get('status', '')} | {g.get('impact', '')} |")
            lines.append("")

        # Recommendations
        recs = r.get("resilience_report", {}).get("recommendations", [])
        if recs:
            lines.append("**Recommendations:**")
            lines.append("")
            for rec in recs:
                lines.append(f"- {rec}")
            lines.append("")

        # CLI commands
        cmds = r.get("resilience_report", {}).get("aws_commands_to_fix", [])
        if cmds:
            lines.append("**Fix Commands:**")
            lines.append("")
            lines.append("```bash")
            for cmd in cmds:
                lines.append(cmd)
            lines.append("```")
            lines.append("")

    # Skipped
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

    # Recommendations
    recs = summary.get("recommendations", [])
    if recs:
        lines.extend(["## All Recommendations", ""])
        for i, rec in enumerate(recs, 1):
            lines.append(f"{i}. {rec}")
        lines.append("")

    return "\n".join(lines)
