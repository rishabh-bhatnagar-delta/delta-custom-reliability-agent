import asyncio
import importlib
import json
import logging

import mcp.types as types
from mcp.server import Server
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

from src.core.aws_client import AWSClientProvider
from src.core.exceptions import MissingToolParam
from src.tools.fetcher import fetch_only_stacks, fetch_resources_in_stack
from src.models.resources import CloudFormationStack
from src.tools.auditor.auditor import get_resource_dimensions
from src.tools.posture_analyzer.api_gateway import get_apigw_resilience_report
from src.tools.posture_analyzer.rds import get_rds_resilience_report
from src.tools.posture_analyzer.s3 import get_s3_resilience_report

_lambda_module = importlib.import_module("src.tools.posture_analyzer.lambda")
get_lambda_resilience_report = _lambda_module.get_lambda_resilience_report

logger = logging.getLogger(__name__)

app = Server("aws-reliability-auditor")
aws = AWSClientProvider()


# --- Tool Definitions ---

@app.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="resource_fetcher",
            description=(
                "Scans all CloudFormation stacks and returns a list of deployed "
                "physical resources (API Gateway, RDS, DynamoDB, Lambda, S3, etc). "
                "Use this as the starting point for infrastructure auditing."
            ),
            inputSchema={"type": "object", "properties": {}},
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
                "Requires the stack name as input."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "stack_name": {
                        "type": "string",
                        "description": "The name of the CloudFormation stack.",
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



# --- Tool Routing ---

def _serialize(obj) -> str:
    """Serialize pydantic models or dicts to JSON string."""
    if isinstance(obj, list):
        return json.dumps([item.model_dump() if hasattr(item, 'model_dump') else item for item in obj], indent=2)
    if hasattr(obj, 'model_dump'):
        return json.dumps(obj.model_dump(), indent=2)
    return json.dumps(obj, indent=2)


def _text(content: str) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=content)]


def _error(msg: str) -> list[types.TextContent]:
    return [types.TextContent(type="text", text=f"Error: {msg}")]


@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    try:
        if name == "resource_fetcher":
            stacks_list = await fetch_only_stacks(aws)
            results = []
            for stack in stacks_list:
                resources = await fetch_resources_in_stack(aws, stack.stack_name)
                results.append(CloudFormationStack(
                    stack_name=stack.stack_name,
                    stack_id=stack.stack_id,
                    resources=resources
                ))
            return _text(_serialize(results))

        elif name == "resource_fetcher_by_stacks":
            stack_name = arguments.get("stack_name")
            if not stack_name:
                raise MissingToolParam("Missing stack_name")
            resources = await fetch_resources_in_stack(aws, stack_name)
            result = CloudFormationStack(
                stack_name=stack_name,
                stack_id=stack_name,
                resources=resources
            )
            return _text(_serialize([result]))

        elif name == "get_resource_dimensions":
            resource_id = arguments.get("resource_id")
            resource_type = arguments.get("resource_type")
            if not resource_id:
                raise MissingToolParam("Missing resource_id")
            if not resource_type:
                raise MissingToolParam("Missing resource_type")
            results = await get_resource_dimensions(aws, resource_id, resource_type)
            return _text(_serialize(results))

        elif name == "analyze_resilience":
            dimensions = arguments.get("dimensions")
            if not dimensions:
                raise MissingToolParam("Missing dimensions")

            # Extract resource metadata from dimensions
            dim_map = {d["name"]: d["value"] for d in dimensions}
            resource_name = dim_map.get("ResourceName")
            resource_type = dim_map.get("ResourceType", "")

            if not resource_name:
                raise MissingToolParam("Dimensions must include ResourceName")

            # Route to the correct analyzer based on resource type
            if "ApiGateway" in resource_type or "RestApi" in resource_type:
                result = get_apigw_resilience_report(dimensions)
            elif "Lambda" in resource_type or "Function" in resource_type:
                result = get_lambda_resilience_report(resource_name, dimensions)
            elif "RDS" in resource_type or "DBInstance" in resource_type:
                result = get_rds_resilience_report(resource_name, dimensions)
            elif "S3" in resource_type or "Bucket" in resource_type:
                result = get_s3_resilience_report(resource_name, dimensions)
            else:
                raise ValueError(f"Unsupported resource type for resilience analysis: {resource_type}")

            return _text(_serialize(result))

        else:
            raise ValueError(f"Unknown tool: {name}")

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
        routes=[
            Mount("/mcp", app=session_manager.handle_request),
        ]
    )

    uvicorn.run(starlette_app, host=host, port=port)


if __name__ == "__main__":
    main()
