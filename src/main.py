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
from src.core.constants import MAX_CONCURRENCY, LOG_LEVEL
from src.core.exceptions import MissingToolParam
from src.models.resources import CloudFormationStack
from src.tools.auditor.auditor import get_resource_dimensions
from src.tools.fetcher import fetch_only_stacks, fetch_resources_in_stack, clear_cache
from src.tools.posture_analyzer.api_gateway import get_apigw_resilience_report
from src.tools.posture_analyzer.rds import get_rds_resilience_report
from src.tools.posture_analyzer.s3 import get_s3_resilience_report
from src.tools.posture_analyzer.dynamodb import get_dynamodb_resilience_report

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
    ]


# --- Tool Handlers ---

async def _handle_resource_fetcher(arguments: dict) -> list[types.TextContent]:
    force_refresh = arguments.get("force_refresh", False)
    if force_refresh:
        clear_cache()

    logger.info(f"resource_fetcher: starting (force_refresh={force_refresh})")
    stacks_list = await fetch_only_stacks(aws, force_refresh=force_refresh)
    total = len(stacks_list)
    logger.info(f"resource_fetcher: found {total} stack(s), max_concurrency={MAX_CONCURRENCY}")

    semaphore = asyncio.Semaphore(MAX_CONCURRENCY)
    completed = {"count": 0}

    async def _fetch_with_progress(stack):
        async with semaphore:
            resources = await fetch_resources_in_stack(aws, stack.stack_name, force_refresh=force_refresh)
        completed["count"] += 1
        logger.info(f"resource_fetcher: [{completed['count']}/{total}] '{stack.stack_name}' -> {len(resources)} resource(s)")
        return resources

    all_resources = await asyncio.gather(*(_fetch_with_progress(s) for s in stacks_list))

    grouped = defaultdict(list)
    for stack, resources in zip(stacks_list, all_resources):
        stack_obj = CloudFormationStack(
            stack_name=stack.stack_name,
            stack_id=stack.stack_id,
            block_code=stack.block_code,
            resources=resources,
        )
        grouped[stack.block_code or "untagged"].append(stack_obj.model_dump())

    return _text(json.dumps(grouped, indent=2))


async def _handle_resource_fetcher_by_stacks(arguments: dict) -> list[types.TextContent]:
    stack_name = arguments.get("stack_name")
    force_refresh = arguments.get("force_refresh", False)
    if not stack_name:
        raise MissingToolParam("Missing stack_name")

    logger.info(f"resource_fetcher_by_stacks: '{stack_name}' (force_refresh={force_refresh})")

    stacks_list = await fetch_only_stacks(aws, force_refresh=force_refresh)
    stack_meta = next((s for s in stacks_list if s.stack_name == stack_name), None)
    block_code = stack_meta.block_code if stack_meta else None

    resources = await fetch_resources_in_stack(aws, stack_name, force_refresh=force_refresh)
    logger.info(f"resource_fetcher_by_stacks: '{stack_name}' -> {len(resources)} resource(s)")

    stack_obj = CloudFormationStack(
        stack_name=stack_name,
        stack_id=stack_meta.stack_id if stack_meta else stack_name,
        block_code=block_code,
        resources=resources,
    )
    key = block_code or "untagged"
    return _text(json.dumps({key: [stack_obj.model_dump()]}, indent=2))


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
        "RDS": lambda name, dims: get_rds_resilience_report(name, dims),
        "DBInstance": lambda name, dims: get_rds_resilience_report(name, dims),
        "S3": lambda name, dims: get_s3_resilience_report(name, dims),
        "Bucket": lambda name, dims: get_s3_resilience_report(name, dims),
        "DynamoDB": lambda name, dims: get_dynamodb_resilience_report(dims),
        "Table": lambda name, dims: get_dynamodb_resilience_report(dims),
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


# --- Tool Router ---

_TOOL_HANDLERS = {
    "resource_fetcher": _handle_resource_fetcher,
    "resource_fetcher_by_stacks": _handle_resource_fetcher_by_stacks,
    "get_resource_dimensions": _handle_get_resource_dimensions,
    "analyze_resilience": _handle_analyze_resilience,
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
