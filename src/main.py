import asyncio
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
from src.models.tool import ToolNames
from src.tools.fetcher import fetch_resources_in_stack, clear_cache, fetch_stacks_multi_region
from src.tools.audit_orchestrator import audit_by_block_code, audit_by_stack
from src.tools.report_generator import generate_markdown_report

# --- Logging Setup ---

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
for _noisy in ("botocore", "urllib3", "boto3", "s3transfer"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

logger = logging.getLogger(__name__)

app = Server("aws-reliability-auditor")


# --- Helpers ---

def _get_aws(arguments: dict) -> AWSClientProvider:
    """Build an AWSClientProvider, optionally assuming into a target account."""
    account_id = arguments.get("account_id")
    return AWSClientProvider(account_id=account_id)


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


_ACCOUNT_ID_PROP = {
    "account_id": {
        "type": "string",
        "description": "Optional target AWS account ID for cross-account access. "
                       "If provided, assumes a role in that account.",
    },
}


# --- Tool Definitions ---

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name=ToolNames.RESOURCE_FETCHER,
            description=(
                "Scans all CloudFormation stacks and returns a list of deployed "
                "physical resources (API Gateway, RDS, DynamoDB, Lambda, S3, etc). "
                "Use this as the starting point for infrastructure auditing. "
                "Results are cached for 24 hours. Use force_refresh to bypass cache."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **_ACCOUNT_ID_PROP,
                    "force_refresh": {
                        "type": "boolean",
                        "description": "Bypass cache and fetch fresh data from AWS. Default: false.",
                    },
                },
            },
        ),
        types.Tool(
            name=ToolNames.RESOURCE_FETCHER_BY_STACK_NAME,
            description=(
                "Returns all resources deployed in a specific CloudFormation stack. "
                "Requires the stack name as input. "
                "Results are cached for 24 hours. Use force_refresh to bypass cache."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **_ACCOUNT_ID_PROP,
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
            name=ToolNames.RESOURCE_FETCHER_BY_BLOCK_CODE,
            description=(
                "Returns all CloudFormation stacks and their resources that belong to a specific block code. "
                "Use this to audit all infrastructure owned by a particular team or application."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **_ACCOUNT_ID_PROP,
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
            name=ToolNames.GENERATE_AUDIT_REPORT,
            description=(
                "Runs a full resilience audit for a block code and generates a detailed Markdown report "
                "using AI. The report includes executive summary, per-resource analysis with evidence, "
                "critical findings, cross-cutting observations, and a prioritized action plan. "
                "Uses Bedrock agent for natural language generation, with a structured fallback."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **_ACCOUNT_ID_PROP,
                    "block_code": {
                        "type": "string",
                        "description": "The block code to audit and generate a report for.",
                    },
                },
                "required": ["block_code"],
            },
        ),
        types.Tool(
            name=ToolNames.GENERATE_AUDIT_REPORT_BY_STACK_NAME,
            description=(
                "Runs a full resilience audit for a single CloudFormation stack (no block code required) "
                "and generates a detailed Markdown report. Useful for stacks without a block code tag."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    **_ACCOUNT_ID_PROP,
                    "stack_name": {
                        "type": "string",
                        "description": "The CloudFormation stack name to audit and generate a report for.",
                    },
                },
                "required": ["stack_name"],
            },
        ),
    ]


# --- Tool Handlers ---

async def _handle_resource_fetcher(arguments: dict) -> list[types.TextContent]:
    force_refresh = arguments.get("force_refresh", False)
    aws = _get_aws(arguments)
    if force_refresh:
        clear_cache()

    # Check file cache for the full grouped result first
    from src.core import file_cache as _fc
    acct = arguments.get("account_id") or "default"
    cache_key = f"resource_fetcher_all_{acct}"
    if not force_refresh:
        cached_result = _fc.get("results", cache_key)
        if cached_result is not None:
            logger.info(f"resource_fetcher: returning full result from file cache (account={acct})")
            return _text(json.dumps(cached_result, indent=2))

    logger.info(f"resource_fetcher: starting across {US_REGIONS} (force_refresh={force_refresh})")
    stacks_list = await fetch_stacks_multi_region(US_REGIONS, force_refresh=force_refresh, account_id=arguments.get("account_id"))
    total = len(stacks_list)
    logger.info(f"resource_fetcher: found {total} stack(s) across all regions")

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    completed = {"count": 0}

    async def _fetch_with_progress(stack):
        async with semaphore:
            provider = AWSClientProvider(region=stack.region, account_id=arguments.get("account_id")) if stack.region else aws
            resources = await fetch_resources_in_stack(provider, stack.stack_name, force_refresh=force_refresh, account_id=arguments.get("account_id"))
        completed["count"] += 1
        logger.info(
            f"resource_fetcher: [{completed['count']}/{total}] '{stack.stack_name}' ({stack.region}) -> {len(resources)} resource(s)")
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

    # Cache the full grouped result to file (account-specific)
    _fc.put("results", cache_key, dict(grouped))

    return _text(json.dumps(grouped, indent=2))


async def _handle_resource_fetcher_by_stacks(arguments: dict) -> list[types.TextContent]:
    stack_name = arguments.get("stack_name")
    force_refresh = arguments.get("force_refresh", False)
    account_id = arguments.get("account_id")
    if not stack_name:
        raise MissingToolParam("Missing stack_name")

    from src.core import file_cache as _fc
    acct = account_id or "default"
    if not force_refresh:
        cached_result = _fc.get("results", f"stack_{stack_name}_{acct}")
        if cached_result is not None:
            logger.info(f"resource_fetcher_by_stacks: returning '{stack_name}' from file cache (account={acct})")
            return _text(json.dumps(cached_result, indent=2))

    logger.info(f"resource_fetcher_by_stacks: '{stack_name}' across {US_REGIONS}")

    stacks_list = await fetch_stacks_multi_region(US_REGIONS, force_refresh=force_refresh, account_id=account_id)
    stack_meta = next((s for s in stacks_list if s.stack_name == stack_name), None)
    block_code = stack_meta.block_code if stack_meta else None
    region = stack_meta.region if stack_meta else None

    provider = AWSClientProvider(region=region, account_id=account_id) if region else _get_aws(arguments)
    resources = await fetch_resources_in_stack(provider, stack_name, force_refresh=force_refresh, account_id=account_id)
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
    _fc.put("results", f"stack_{stack_name}_{acct}", result)
    return _text(json.dumps(result, indent=2))


async def _handle_resource_fetcher_by_block_code(arguments: dict) -> list[types.TextContent]:
    block_code = arguments.get("block_code")
    force_refresh = arguments.get("force_refresh", False)
    account_id = arguments.get("account_id")
    if not block_code:
        raise MissingToolParam("Missing block_code")

    from src.core import file_cache as _fc
    acct = account_id or "default"
    cache_key = f"block_{block_code.upper()}_{acct}"
    if not force_refresh:
        cached_result = _fc.get("results", cache_key)
        if cached_result is not None:
            logger.info(f"resource_fetcher_by_block_code: returning '{block_code}' from file cache")
            return _text(json.dumps(cached_result, indent=2))

    logger.info(f"resource_fetcher_by_block_code: '{block_code}' across {US_REGIONS}")

    stacks_list = await fetch_stacks_multi_region(US_REGIONS, force_refresh=force_refresh, account_id=account_id)
    matching_stacks = [s for s in stacks_list if s.block_code and s.block_code.upper() == block_code.upper()]

    if not matching_stacks:
        return _text(json.dumps({block_code: []}, indent=2))

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)

    async def _fetch(stack):
        async with semaphore:
            provider = AWSClientProvider(region=stack.region, account_id=account_id) if stack.region else _get_aws(arguments)
            return await fetch_resources_in_stack(provider, stack.stack_name, force_refresh=force_refresh, account_id=account_id)

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


async def _handle_generate_audit_report(arguments: dict) -> list[types.TextContent]:
    block_code = arguments.get("block_code")
    if not block_code:
        raise MissingToolParam("Missing block_code")

    aws = _get_aws(arguments)
    logger.info(f"generate_audit_report: starting for '{block_code}'")
    audit_data = await audit_by_block_code(aws, block_code, max_concurrency=MAX_CONCURRENCY)
    markdown = generate_markdown_report(audit_data)
    logger.info(f"generate_audit_report: completed for '{block_code}'")
    return _text(markdown)


async def _handle_generate_audit_report_by_stack(arguments: dict) -> list[types.TextContent]:
    stack_name = arguments.get("stack_name")
    if not stack_name:
        raise MissingToolParam("Missing stack_name")

    aws = _get_aws(arguments)
    logger.info(f"generate_audit_report_by_stack: starting for '{stack_name}'")
    audit_data = await audit_by_stack(aws, stack_name, max_concurrency=MAX_CONCURRENCY)
    markdown = generate_markdown_report(audit_data)
    logger.info(f"generate_audit_report_by_stack: completed for '{stack_name}'")
    return _text(markdown)


# --- Tool Router ---

_TOOL_HANDLERS = {
    ToolNames.RESOURCE_FETCHER: _handle_resource_fetcher,
    ToolNames.RESOURCE_FETCHER_BY_STACK_NAME: _handle_resource_fetcher_by_stacks,
    ToolNames.RESOURCE_FETCHER_BY_BLOCK_CODE: _handle_resource_fetcher_by_block_code,
    ToolNames.GENERATE_AUDIT_REPORT: _handle_generate_audit_report,
    ToolNames.GENERATE_AUDIT_REPORT_BY_STACK_NAME: _handle_generate_audit_report_by_stack,
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
