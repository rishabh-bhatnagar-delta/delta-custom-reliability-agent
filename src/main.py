import asyncio
import importlib
import json
import logging
import time
from collections import defaultdict

import mcp.types as types
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from src.core.aws_client import AWSClientProvider
from src.core.constants import MAX_CONCURRENCY, LOG_LEVEL, US_REGIONS
from src.core.exceptions import MissingToolParam
from src.models.resources import CloudFormationStack
from src.tools.auditor.auditor import get_resource_dimensions
from src.tools.fetcher import fetch_only_stacks, fetch_resources_in_stack, clear_cache, fetch_stacks_multi_region
from src.tools.posture_analyzer.api_gateway import get_apigw_resilience_report
from src.tools.posture_analyzer.ec2 import get_ec2_resilience_report
from src.tools.posture_analyzer.rds import get_rds_resilience_report
from src.tools.posture_analyzer.route53 import get_route53_resilience_report
from src.tools.posture_analyzer.s3 import get_s3_resilience_report
from src.tools.posture_analyzer.dynamodb import get_dynamodb_resilience_report
from src.tools.audit_orchestrator import audit_by_block_code
from src.tools.report_generator import generate_markdown_report

_lambda_module = importlib.import_module("src.tools.posture_analyzer.lambda")
get_lambda_resilience_report = _lambda_module.get_lambda_resilience_report

# --- Logging Setup ---

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
for _noisy in ("botocore", "urllib3", "boto3", "s3transfer"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

app = Server("aws-reliability-auditor")
aws = AWSClientProvider()


# --- Helpers ---

def _serialize(obj) -> str:
    if isinstance(obj, list):
        return json.dumps([item.model_dump() if hasattr(item, 'model_dump') else item for item in obj], indent=2)
    if hasattr(obj, 'model_dump'):
        return json.dumps(obj.model_dump(), indent=2)
    return json.dumps(obj, indent=2)


def _text(content: str) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=content)]


def _error(msg: str) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=f"Error: {msg}")]


# --- Tool Definitions ---

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="resource_fetcher",
            description=(
                "Scans all CloudFormation stacks and returns a list of deployed "
                "physical resources (API Gateway, RDS, DynamoDB, Lambda, S3, etc). "
                "Use this as the starting point for infrastructure auditing. "
                "Results are cached for 15 minutes. Use force_refresh to bypass cache."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "force_refresh": {
                        "type": "boolean",
                        "description": "Bypass cache and fetch fresh data from AWS. Default: false.",
                    },
                },
            },
        ),
        types.Tool(
            name="get_resource_dimensions",
            description=(
                "Given a resource's physical ID and AWS resource type, returns "
                "configuration dimensions used to evaluate reliability posture."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "resource_id": {
                        "type": "string",
                        "description": "The physical resource ID or ARN.",
                    },
                    "resource_type": {
                        "type": "string",
                        "description": "AWS resource type (e.g. AWS::RDS::DBInstance).",
                    },
                },
                "required": ["resource_id", "resource_type"],
            },
        ),
        types.Tool(
            name="resource_fetcher_by_stacks",
            description=(
                "Returns all resources deployed in a specific CloudFormation stack. "
                "Requires the stack name as input. "
                "Results are cached for 15 minutes. Use force_refresh to bypass cache."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "stack_name": {
                        "type": "string",
                        "description": "The name of the CloudFormation stack.",
                    },
                    "force_refresh": {
                        "type": "boolean",
                        "description": "Bypass cache and fetch fresh data from AWS. Default: false.",
                    },
                },
                "required": ["stack_name"],
            },
        ),
        types.Tool(
            name="analyze_resilience",
            description=(
                "Evaluates a resource's configuration against AWS Well-Architected "
                "Reliability standards. Supports API Gateway, Lambda, RDS, and S3. "
                "Requires the resource dimensions from get_resource_dimensions as input."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dimensions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "value": {},
                            },
                        },
                        "description": "List of dimension name/value pairs from get_resource_dimensions. Must include ResourceName and ResourceType.",
                    },
                },
                "required": ["dimensions"],
            },
        ),
        types.Tool(
            name="resource_fetcher_by_block_code",
            description=(
                "Returns all CloudFormation stacks and their resources that belong to a specific block code. "
                "Use this to audit all infrastructure owned by a particular team or application."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "block_code": {
                        "type": "string",
                        "description": "The block code tag value to filter stacks by (e.g. ITSSREMPSM).",
                    },
                    "force_refresh": {
                        "type": "boolean",
                        "description": "Bypass cache and fetch fresh data from AWS. Default: false.",
                    },
                },
                "required": ["block_code"],
            },
        ),
        types.Tool(
            name="audit_by_block_code",
            description=(
                "Performs a full resilience audit for all infrastructure owned by a block code. "
                "Fetches all stacks, gets dimensions for every supported resource, runs posture analysis, "
                "and returns a detailed report with per-resource evidence, gaps, recommendations, "
                "and an application-level summary with aggregated scores."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "block_code": {
                        "type": "string",
                        "description": "The block code to audit (e.g. ITSSREMPSM).",
                    },
                },
                "required": ["block_code"],
            },
        ),
        types.Tool(
            name="generate_audit_report",
            description=(
                "Runs a full resilience audit for a block code and generates a detailed Markdown report "
                "using AI. The report includes executive summary, per-resource analysis with evidence, "
                "critical findings, cross-cutting observations, and a prioritized action plan. "
                "Uses Bedrock agent for natural language generation, with a structured fallback."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "block_code": {
                        "type": "string",
                        "description": "The block code to audit and generate a report for.",
                    },
                },
                "required": ["block_code"],
            },
        ),
    ]


# --- Tool Handlers ---

async def _handle_resource_fetcher(arguments: dict) -> list[types.TextContent]:
    force_refresh = arguments.get("force_refresh", False)
    if force_refresh:
        clear_cache()

    # Check file cache for the full grouped result first
    from src.core import file_cache as _fc
    if not force_refresh:
        cached_result = _fc.get("results", "resource_fetcher_all")
        if cached_result is not None:
            logger.info("resource_fetcher: returning full result from file cache")
            return _text(json.dumps(cached_result, indent=2))

    logger.info(f"resource_fetcher: starting across {US_REGIONS} (force_refresh={force_refresh})")
    stacks_list = await fetch_stacks_multi_region(US_REGIONS, force_refresh=force_refresh)
    total = len(stacks_list)
    logger.info(f"resource_fetcher: found {total} stack(s) across all regions")

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    completed = {"count": 0}

    async def _fetch_with_progress(stack):
        async with semaphore:
            provider = AWSClientProvider(region=stack.region) if stack.region else aws
            resources = await fetch_resources_in_stack(provider, stack.stack_name, force_refresh=force_refresh)
        completed["count"] += 1
        logger.info(f"resource_fetcher: [{completed['count']}/{total}] '{stack.stack_name}' ({stack.region}) -> {len(resources)} resource(s)")
        return resources

    all_resources = await asyncio.gather(*(_fetch_with_progress(s) for s in stacks_list))

    grouped = defaultdict(list)
    for stack, resources in zip(stacks_list, all_resources):
        stack_obj = CloudFormationStack(
            stack_name=stack.stack_name,
            stack_id=stack.stack_id,
            block_code=stack.block_code,
            region=stack.region,
            resources=resources,
        )
        grouped[stack.block_code or "untagged"].append(stack_obj.model_dump())

    # Cache the full grouped result to file
    _fc.put("results", "resource_fetcher_all", dict(grouped))

    return _text(json.dumps(grouped, indent=2))



async def _handle_resource_fetcher_by_stacks(arguments: dict) -> list[types.TextContent]:
    stack_name = arguments.get("stack_name")
    force_refresh = arguments.get("force_refresh", False)
    if not stack_name:
        raise MissingToolParam("Missing stack_name")

    from src.core import file_cache as _fc
    if not force_refresh:
        cached_result = _fc.get("results", f"stack_{stack_name}")
        if cached_result is not None:
            logger.info(f"resource_fetcher_by_stacks: returning '{stack_name}' from file cache")
            return _text(json.dumps(cached_result, indent=2))

    logger.info(f"resource_fetcher_by_stacks: '{stack_name}' across {US_REGIONS}")

    stacks_list = await fetch_stacks_multi_region(US_REGIONS, force_refresh=force_refresh)
    stack_meta = next((s for s in stacks_list if s.stack_name == stack_name), None)
    block_code = stack_meta.block_code if stack_meta else None
    region = stack_meta.region if stack_meta else None

    provider = AWSClientProvider(region=region) if region else aws
    resources = await fetch_resources_in_stack(provider, stack_name, force_refresh=force_refresh)
    logger.info(f"resource_fetcher_by_stacks: '{stack_name}' ({region}) -> {len(resources)} resource(s)")

    stack_obj = CloudFormationStack(
        stack_name=stack_name,
        stack_id=stack_meta.stack_id if stack_meta else stack_name,
        block_code=block_code,
        region=region,
        resources=resources,
    )
    key = block_code or "untagged"
    result = {key: [stack_obj.model_dump()]}
    _fc.put("results", f"stack_{stack_name}", result)
    return _text(json.dumps(result, indent=2))


async def _handle_get_resource_dimensions(arguments: dict) -> list[types.TextContent]:
    resource_id = arguments.get("resource_id")
    resource_type = arguments.get("resource_type")
    if not resource_id:
        raise MissingToolParam("Missing resource_id")
    if not resource_type:
        raise MissingToolParam("Missing resource_type")

    logger.info(f"get_resource_dimensions: '{resource_id}' ({resource_type})")
    results = await get_resource_dimensions(aws, resource_id, resource_type)
    logger.info(f"get_resource_dimensions: returned {len(results) if isinstance(results, list) else 1} dimension(s)")
    return _text(_serialize(results))


async def _handle_analyze_resilience(arguments: dict) -> list[types.TextContent]:
    dimensions = arguments.get("dimensions")
    if not dimensions:
        raise MissingToolParam("Missing dimensions")

    print(dimensions)
    dim_map = {d["name"]: d.get("value") for d in dimensions}
    resource_name = dim_map.get("ResourceName")
    resource_type = dim_map.get("ResourceType", "")

    if not resource_name:
        raise MissingToolParam("Dimensions must include ResourceName")

    logger.info(f"analyze_resilience: '{resource_name}' ({resource_type})")

    analyzers = {
        "ApiGateway": get_apigw_resilience_report,
        "RestApi": get_apigw_resilience_report,
        "Lambda": lambda name, dims: get_lambda_resilience_report(name, dims),
        "Function": lambda name, dims: get_lambda_resilience_report(name, dims),
        "DBInstance": lambda name, dims: get_rds_resilience_report(name, dims),
        "DBCluster": lambda name, dims: get_rds_resilience_report(name, dims),
        "RDS": lambda name, dims: get_rds_resilience_report(name, dims),
        "S3": lambda name, dims: get_s3_resilience_report(name, dims),
        "Bucket": lambda name, dims: get_s3_resilience_report(name, dims),
        "DynamoDB": lambda name, dims: get_dynamodb_resilience_report(dims),
        "Table": lambda name, dims: get_dynamodb_resilience_report(dims),
        "HostedZone": lambda name, dims: get_route53_resilience_report(name, dims),
        "Route53": lambda name, dims: get_route53_resilience_report(name, dims),
        "EC2::Instance": lambda name, dims: get_ec2_resilience_report(name, dims),
        "EC2": lambda name, dims: get_ec2_resilience_report(name, dims),
    }

    for key, analyzer in analyzers.items():
        if key in resource_type:
            if key in ("ApiGateway", "RestApi"):
                result = analyzer(dimensions)
            else:
                result = analyzer(resource_name, dimensions)
            logger.info(f"analyze_resilience: completed for '{resource_name}'")
            return _text(_serialize(result))

    raise ValueError(f"Unsupported resource type: {resource_type}")


async def _handle_resource_fetcher_by_block_code(arguments: dict) -> list[types.TextContent]:
    block_code = arguments.get("block_code")
    force_refresh = arguments.get("force_refresh", False)
    if not block_code:
        raise MissingToolParam("Missing block_code")

    from src.core import file_cache as _fc
    cache_key = f"block_{block_code.upper()}"
    if not force_refresh:
        cached_result = _fc.get("results", cache_key)
        if cached_result is not None:
            logger.info(f"resource_fetcher_by_block_code: returning '{block_code}' from file cache")
            return _text(json.dumps(cached_result, indent=2))

    logger.info(f"resource_fetcher_by_block_code: '{block_code}' across {US_REGIONS}")

    stacks_list = await fetch_stacks_multi_region(US_REGIONS, force_refresh=force_refresh)
    matching_stacks = [s for s in stacks_list if s.block_code and s.block_code.upper() == block_code.upper()]

    if not matching_stacks:
        return _text(json.dumps({block_code: []}, indent=2))

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def _fetch(stack):
        async with semaphore:
            provider = AWSClientProvider(region=stack.region) if stack.region else aws
            return await fetch_resources_in_stack(provider, stack.stack_name, force_refresh=force_refresh)

    all_resources = await asyncio.gather(*(_fetch(s) for s in matching_stacks))

    result = []
    for stack, resources in zip(matching_stacks, all_resources):
        stack_obj = CloudFormationStack(
            stack_name=stack.stack_name,
            stack_id=stack.stack_id,
            block_code=stack.block_code,
            region=stack.region,
            resources=resources,
        )
        result.append(stack_obj.model_dump())

    logger.info(f"resource_fetcher_by_block_code: '{block_code}' -> {len(result)} stack(s)")
    output = {block_code: result}
    _fc.put("results", cache_key, output)
    return _text(json.dumps(output, indent=2))


async def _handle_audit_by_block_code(arguments: dict) -> list[types.TextContent]:
    block_code = arguments.get("block_code")
    if not block_code:
        raise MissingToolParam("Missing block_code")

    logger.info(f"audit_by_block_code: starting full audit for '{block_code}'")
    report = await audit_by_block_code(aws, block_code, max_concurrency=MAX_CONCURRENCY)
    logger.info(f"audit_by_block_code: completed for '{block_code}'")
    return _text(json.dumps(report, indent=2))


async def _handle_generate_audit_report(arguments: dict) -> list[types.TextContent]:
    block_code = arguments.get("block_code")
    if not block_code:
        raise MissingToolParam("Missing block_code")

    logger.info(f"generate_audit_report: starting for '{block_code}'")
    audit_data = await audit_by_block_code(aws, block_code, max_concurrency=MAX_CONCURRENCY)
    markdown = generate_markdown_report(audit_data)
    logger.info(f"generate_audit_report: completed for '{block_code}'")
    return _text(markdown)


# --- Tool Router ---

_TOOL_HANDLERS = {
    "resource_fetcher": _handle_resource_fetcher,
    "resource_fetcher_by_stacks": _handle_resource_fetcher_by_stacks,
    "resource_fetcher_by_block_code": _handle_resource_fetcher_by_block_code,
    "get_resource_dimensions": _handle_get_resource_dimensions,
    "analyze_resilience": _handle_analyze_resilience,
    "audit_by_block_code": _handle_audit_by_block_code,
    "generate_audit_report": _handle_generate_audit_report,
}


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    start_time = time.time()
    logger.info(f"Tool called: '{name}' with arguments: {arguments}")
    try:
        handler = _TOOL_HANDLERS.get(name)
        if not handler:
            raise ValueError(f"Unknown tool: {name}")
        result = await handler(arguments)
        elapsed = time.time() - start_time
        logger.info(f"'{name}' completed in {elapsed:.2f}s")
        return result
    except MissingToolParam as e:
        return _error(str(e))
    except Exception as e:
        logger.exception(f"Tool '{name}' failed")
        return _error(f"{name} failed: {str(e)}")


# --- Entry Point ---

def main(host: str = "0.0.0.0", port: int = 8000):
    import uvicorn
    from starlette.applications import Starlette
    from starlette.routing import Mount
    from contextlib import asynccontextmanager

    session_manager = StreamableHTTPSessionManager(
        app=app,
        stateless=True,
        json_response=True,
    )

    @asynccontextmanager
    async def lifespan(app):
        async with session_manager.run():
            yield

    starlette_app = Starlette(
        lifespan=lifespan,
        routes=[Mount("/mcp", app=session_manager.handle_request)],
    )

    uvicorn.run(starlette_app, host=host, port=port)


if __name__ == "__main__":
    main()
