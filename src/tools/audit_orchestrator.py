import asyncio
import logging
from typing import List, Dict, Any, Optional

from src.core.aws_client import AWSClientProvider
from src.models.dimensions import DimensionSupportedResource
from src.models.resources import StackResource
from src.tools.auditor.auditor import get_resource_dimensions
from src.tools.fetcher import fetch_only_stacks, fetch_resources_in_stack, fetch_stacks_multi_region
from src.tools.posture_analyzer.api_gateway import get_apigw_resilience_report
from src.tools.posture_analyzer.dynamodb import get_dynamodb_resilience_report
from src.tools.posture_analyzer.ec2 import get_ec2_resilience_report
from src.tools.posture_analyzer.rds import get_rds_resilience_report
from src.tools.posture_analyzer.route53 import get_route53_resilience_report
from src.tools.posture_analyzer.s3 import get_s3_resilience_report

import importlib
_lambda_mod = importlib.import_module("src.tools.posture_analyzer.lambda")
get_lambda_resilience_report = _lambda_mod.get_lambda_resilience_report

logger = logging.getLogger(__name__)

# Maps resource type substring to analyzer function
_ANALYZERS = {
    "ApiGateway::RestApi": lambda name, dims: get_apigw_resilience_report(dims),
    "RDS::DBInstance": lambda name, dims: get_rds_resilience_report(name, dims),
    "RDS::DBCluster": lambda name, dims: get_rds_resilience_report(name, dims),
    "Lambda::Function": lambda name, dims: get_lambda_resilience_report(name, dims),
    "S3::Bucket": lambda name, dims: get_s3_resilience_report(name, dims),
    "DynamoDB::Table": lambda name, dims: get_dynamodb_resilience_report(dims),
    "Route53::HostedZone": lambda name, dims: get_route53_resilience_report(name, dims),
    "EC2::Instance": lambda name, dims: get_ec2_resilience_report(name, dims),
}


def _is_supported(resource_type: str) -> bool:
    """Check if we can fetch dimensions for this resource type."""
    try:
        service = AWSClientProvider.get_service_name_by_resource_type(resource_type)
        return DimensionSupportedResource.from_str(service) is not None
    except Exception:
        return False


def _get_analyzer(resource_type: str):
    """Find the matching posture analyzer for a resource type."""
    for key, analyzer in _ANALYZERS.items():
        if key in resource_type:
            return analyzer
    return None


async def _audit_single_resource(
    aws: AWSClientProvider,
    resource: StackResource,
    stack_name: str,
    region: str = None,
) -> Dict[str, Any]:
    """Fetch dimensions and run posture analysis for one resource."""
    # Use region-specific provider if region differs
    if region and region != aws.region:
        aws = AWSClientProvider(region=region)

    result = {
        "stack_name": stack_name,
        "logical_id": resource.logical_id,
        "physical_id": resource.physical_id,
        "resource_type": resource.resource_type,
        "status": resource.status,
        "region": region or aws.region,
    }

    if not resource.physical_id:
        result["audit_status"] = "SKIPPED"
        result["reason"] = "No physical ID (resource may be deleted)"
        return result

    if not _is_supported(resource.resource_type):
        result["audit_status"] = "UNSUPPORTED"
        result["reason"] = f"No dimension fetcher for {resource.resource_type}"
        return result

    analyzer = _get_analyzer(resource.resource_type)
    if not analyzer:
        result["audit_status"] = "NO_ANALYZER"
        result["reason"] = f"Dimensions available but no posture analyzer for {resource.resource_type}"
        return result

    # Fetch dimensions
    try:
        logger.debug(f"audit: fetching dimensions for '{resource.physical_id}' ({resource.resource_type})")
        dimensions = await get_resource_dimensions(aws, resource.physical_id, resource.resource_type)
        dims_list = [d.model_dump() for d in dimensions]
        result["dimensions"] = dims_list
        logger.debug(f"audit: got {len(dims_list)} dimension(s) for '{resource.physical_id}'")
    except Exception as e:
        logger.error(f"audit: dimension fetch failed for '{resource.physical_id}': {e}")
        result["audit_status"] = "DIMENSION_ERROR"
        result["reason"] = str(e)
        return result

    # Run posture analysis
    try:
        report = analyzer(resource.physical_id, dims_list)
        result["audit_status"] = "ANALYZED"
        result["resilience_report"] = report.model_dump()
        score = report.report.overall_resilience_score if report.report else "?"
        gaps = len(report.report.resilience_gaps) if report.report else 0
        logger.info(f"audit: '{resource.physical_id}' ({resource.resource_type}) -> score={score}/10, gaps={gaps}")
    except Exception as e:
        logger.error(f"audit: posture analysis failed for '{resource.physical_id}': {e}")
        result["audit_status"] = "ANALYSIS_ERROR"
        result["reason"] = str(e)
        result["dimensions"] = dims_list

    return result


def _build_application_summary(
    block_code: str,
    stack_reports: List[Dict],
    resource_audits: List[Dict],
) -> Dict[str, Any]:
    """Build an application-level summary from all resource audits."""
    analyzed = [r for r in resource_audits if r.get("audit_status") == "ANALYZED"]
    skipped = [r for r in resource_audits if r.get("audit_status") == "SKIPPED"]
    unsupported = [r for r in resource_audits if r.get("audit_status") == "UNSUPPORTED"]
    errors = [r for r in resource_audits if r.get("audit_status") in ("DIMENSION_ERROR", "ANALYSIS_ERROR")]

    # Aggregate scores
    scores = []
    all_gaps = []
    all_recommendations = set()
    all_cli_commands = []

    for r in analyzed:
        report = r.get("resilience_report", {}).get("report", {})
        score = report.get("overall_resilience_score", 0)
        scores.append(score)

        for gap in report.get("resilience_gaps", []):
            all_gaps.append({
                "resource": r["physical_id"],
                "resource_type": r["resource_type"],
                "stack": r["stack_name"],
                "gap_name": gap.get("name"),
                "gap_status": gap.get("status"),
                "gap_impact": gap.get("impact"),
            })

        for rec in r.get("resilience_report", {}).get("recommendations", []):
            all_recommendations.add(rec)

        all_cli_commands.extend(r.get("resilience_report", {}).get("aws_commands_to_fix", []))

    avg_score = round(sum(scores) / len(scores), 1) if scores else 0
    min_score = min(scores) if scores else 0

    # Group gaps by severity (based on status keywords)
    critical_gaps = [g for g in all_gaps if any(k in g["gap_status"].upper() for k in
                     ["DISABLED", "NONE", "NO ", "MANUAL", "SPOF", "UNENCRYPTED", "NO HEALTH CHECK"])]
    warning_gaps = [g for g in all_gaps if g not in critical_gaps]

    # Resource type breakdown
    type_counts = {}
    for r in resource_audits:
        rt = r["resource_type"]
        type_counts[rt] = type_counts.get(rt, 0) + 1

    analyzed_types = {}
    for r in analyzed:
        rt = r["resource_type"]
        analyzed_types[rt] = analyzed_types.get(rt, 0) + 1

    return {
        "block_code": block_code,
        "total_stacks": len(stack_reports),
        "total_resources": len(resource_audits),
        "resources_analyzed": len(analyzed),
        "resources_skipped": len(skipped),
        "resources_unsupported": len(unsupported),
        "resources_errored": len(errors),
        "resource_type_breakdown": type_counts,
        "analyzed_type_breakdown": analyzed_types,
        "application_resilience_score": avg_score,
        "lowest_resource_score": min_score,
        "total_gaps": len(all_gaps),
        "critical_gaps": critical_gaps,
        "warning_gaps": warning_gaps,
        "recommendations": sorted(all_recommendations),
        "cli_commands": all_cli_commands,
    }


async def audit_by_block_code(
    aws: AWSClientProvider,
    block_code: str,
    max_concurrency: int = 5,
    regions: List[str] = None,
) -> Dict[str, Any]:
    """Full audit pipeline: fetch stacks → dimensions → posture analysis → report."""
    from src.core.constants import US_REGIONS
    scan_regions = regions or US_REGIONS
    logger.info(f"audit_by_block_code: starting for '{block_code}' across {scan_regions}")

    # 1. Fetch stacks across all regions
    stacks_list = await fetch_stacks_multi_region(scan_regions)
    matching = [s for s in stacks_list if s.block_code and s.block_code.upper() == block_code.upper()]

    if not matching:
        logger.warning(f"audit_by_block_code: no stacks found for '{block_code}'")
        return {"block_code": block_code, "error": "No stacks found for this block code"}

    logger.info(f"audit_by_block_code: found {len(matching)} stack(s) for '{block_code}'")

    # 2. Fetch resources for all stacks (using region-specific providers)
    semaphore = asyncio.Semaphore(max_concurrency)

    async def _fetch(stack):
        async with semaphore:
            provider = AWSClientProvider(region=stack.region) if stack.region else aws
            resources = await fetch_resources_in_stack(provider, stack.stack_name)
            return stack, resources

    stack_results = await asyncio.gather(*(_fetch(s) for s in matching))
    total_resources = sum(len(res) for _, res in stack_results)
    logger.info(f"audit_by_block_code: fetched {total_resources} total resources across {len(matching)} stack(s)")

    # 3. Audit each supported resource (with correct region)
    audit_tasks = []
    for stack, resources in stack_results:
        for resource in resources:
            audit_tasks.append(_audit_single_resource(aws, resource, stack.stack_name, region=stack.region))

    logger.info(f"audit_by_block_code: starting posture analysis for {len(audit_tasks)} resource(s)")
    resource_audits = await asyncio.gather(*audit_tasks)

    analyzed = [r for r in resource_audits if r.get("audit_status") == "ANALYZED"]
    unsupported = [r for r in resource_audits if r.get("audit_status") in ("UNSUPPORTED", "NO_ANALYZER")]
    errored = [r for r in resource_audits if r.get("audit_status") in ("DIMENSION_ERROR", "ANALYSIS_ERROR")]
    logger.info(f"audit_by_block_code: analysis complete — {len(analyzed)} analyzed, {len(unsupported)} unsupported, {len(errored)} errored")

    # 4. Build stack-level summaries
    stack_reports = []
    for stack, resources in stack_results:
        stack_audits = [r for r in resource_audits if r.get("stack_name") == stack.stack_name]
        stack_reports.append({
            "stack_name": stack.stack_name,
            "stack_id": stack.stack_id,
            "region": stack.region,
            "total_resources": len(resources),
            "analyzed": len([r for r in stack_audits if r.get("audit_status") == "ANALYZED"]),
            "unsupported": len([r for r in stack_audits if r.get("audit_status") == "UNSUPPORTED"]),
        })

    # 5. Build application-level summary
    app_summary = _build_application_summary(block_code, stack_reports, list(resource_audits))
    logger.info(f"audit_by_block_code: application score {app_summary.get('application_resilience_score', '?')}/10, {app_summary.get('total_gaps', 0)} total gaps")

    return {
        "application_summary": app_summary,
        "stack_summaries": stack_reports,
        "resource_audits": [r for r in resource_audits if r.get("audit_status") == "ANALYZED"],
        "skipped_resources": [r for r in resource_audits if r.get("audit_status") != "ANALYZED"],
    }

async def audit_by_stack(
    aws: AWSClientProvider,
    stack_name: str,
    max_concurrency: int = 5,
    regions: List[str] = None,
) -> Dict[str, Any]:
    """Full audit pipeline for a single stack (no block code required)."""
    from src.core.constants import US_REGIONS
    scan_regions = regions or US_REGIONS
    logger.info(f"audit_by_stack: starting for '{stack_name}' across {scan_regions}")

    # 1. Find the stack across all regions
    stacks_list = await fetch_stacks_multi_region(scan_regions)
    stack_meta = next((s for s in stacks_list if s.stack_name == stack_name), None)

    if not stack_meta:
        logger.warning(f"audit_by_stack: stack '{stack_name}' not found")
        return {"stack_name": stack_name, "error": "Stack not found"}

    label = stack_meta.block_code or stack_name
    region = stack_meta.region

    # 2. Fetch resources
    provider = AWSClientProvider(region=region) if region else aws
    resources = await fetch_resources_in_stack(provider, stack_name)
    logger.info(f"audit_by_stack: '{stack_name}' ({region}) -> {len(resources)} resource(s)")

    # 3. Audit each supported resource
    audit_tasks = [
        _audit_single_resource(aws, resource, stack_name, region=region)
        for resource in resources
    ]
    resource_audits = await asyncio.gather(*audit_tasks)

    analyzed = [r for r in resource_audits if r.get("audit_status") == "ANALYZED"]
    logger.info(f"audit_by_stack: {len(analyzed)} analyzed out of {len(resource_audits)} total")

    # 4. Build summary
    stack_reports = [{
        "stack_name": stack_name,
        "stack_id": stack_meta.stack_id,
        "region": region,
        "total_resources": len(resources),
        "analyzed": len(analyzed),
        "unsupported": len([r for r in resource_audits if r.get("audit_status") == "UNSUPPORTED"]),
    }]

    app_summary = _build_application_summary(label, stack_reports, list(resource_audits))
    logger.info(f"audit_by_stack: score {app_summary.get('application_resilience_score', '?')}/10, {app_summary.get('total_gaps', 0)} gaps")

    return {
        "application_summary": app_summary,
        "stack_summaries": stack_reports,
        "resource_audits": [r for r in resource_audits if r.get("audit_status") == "ANALYZED"],
        "skipped_resources": [r for r in resource_audits if r.get("audit_status") != "ANALYZED"],
    }



async def audit_by_stack(
    aws: AWSClientProvider,
    stack_name: str,
    max_concurrency: int = 5,
    regions: List[str] = None,
) -> Dict[str, Any]:
    """Full audit pipeline for a single stack (no block code required)."""
    from src.core.constants import US_REGIONS
    scan_regions = regions or US_REGIONS
    logger.info(f"audit_by_stack: starting for '{stack_name}' across {scan_regions}")

    # 1. Find the stack across all regions
    stacks_list = await fetch_stacks_multi_region(scan_regions)
    stack_meta = next((s for s in stacks_list if s.stack_name == stack_name), None)

    if not stack_meta:
        logger.warning(f"audit_by_stack: stack '{stack_name}' not found")
        return {"stack_name": stack_name, "error": "Stack not found"}

    label = stack_meta.block_code or stack_name
    region = stack_meta.region

    # 2. Fetch resources
    provider = AWSClientProvider(region=region) if region else aws
    resources = await fetch_resources_in_stack(provider, stack_name)
    logger.info(f"audit_by_stack: '{stack_name}' ({region}) -> {len(resources)} resource(s)")

    # 3. Audit each supported resource
    audit_tasks = [
        _audit_single_resource(aws, resource, stack_name, region=region)
        for resource in resources
    ]
    resource_audits = await asyncio.gather(*audit_tasks)

    analyzed = [r for r in resource_audits if r.get("audit_status") == "ANALYZED"]
    logger.info(f"audit_by_stack: {len(analyzed)} analyzed out of {len(resource_audits)} total")

    # 4. Build summary
    stack_reports = [{
        "stack_name": stack_name,
        "stack_id": stack_meta.stack_id,
        "region": region,
        "total_resources": len(resources),
        "analyzed": len(analyzed),
        "unsupported": len([r for r in resource_audits if r.get("audit_status") == "UNSUPPORTED"]),
    }]

    app_summary = _build_application_summary(label, stack_reports, list(resource_audits))
    logger.info(f"audit_by_stack: score {app_summary.get('application_resilience_score', '?')}/10, {app_summary.get('total_gaps', 0)} gaps")

    return {
        "application_summary": app_summary,
        "stack_summaries": stack_reports,
        "resource_audits": [r for r in resource_audits if r.get("audit_status") == "ANALYZED"],
        "skipped_resources": [r for r in resource_audits if r.get("audit_status") != "ANALYZED"],
    }
