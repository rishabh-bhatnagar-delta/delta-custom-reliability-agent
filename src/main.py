import asyncio
import importlib
import json
import logging

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from src.core.aws_client import AWSClientProvider
from src.core.exceptions import MissingToolParam
from src.tools.fetcher import fetch_only_stacks, fetch_resources_in_stack
from src.models.resources import CloudFormationStack
from src.tools.auditor.auditor import get_resource_dimensions
from src.tools.posture_analyzer.api_gateway import get_apigw_resilience_report
from src.tools.posture_analyzer.rds import get_rds_resilience_report
from src.tools.posture_analyzer.s3 import get_s3_resilience_report

# 'lambda' is a Python keyword, so we use importlib
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
            name="list_aws_resources",
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
            name="analyze_api_gateway_resilience",
            description=(
                "Evaluates an API Gateway's configuration against AWS Well-Architected "
                "Reliability standards. Requires the resource dimensions as input."
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
                        "description": "List of dimension name/value pairs from get_resource_dimensions.",
                    },
                },
                "required": ["dimensions"],
            },
        ),
        types.Tool(
            name="analyze_lambda_resilience",
            description=(
                "Evaluates a Lambda function's configuration against AWS Well-Architected "
                "Serverless Reliability standards. Requires function name and dimensions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "function_name": {
                        "type": "string",
                        "description": "The Lambda function name.",
                    },
                    "dimensions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "value": {},
                            },
                        },
                        "description": "List of dimension name/value pairs from get_resource_dimensions.",
                    },
                },
                "required": ["function_name", "dimensions"],
            },
        ),
        types.Tool(
            name="analyze_rds_resilience",
            description=(
                "Evaluates an RDS instance's configuration against AWS Well-Architected "
                "Reliability standards. Requires DB instance ID and dimensions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "db_instance_id": {
                        "type": "string",
                        "description": "The RDS DB instance identifier.",
                    },
                    "dimensions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "value": {},
                            },
                        },
                        "description": "List of dimension name/value pairs from get_resource_dimensions.",
                    },
                },
                "required": ["db_instance_id", "dimensions"],
            },
        ),
        types.Tool(
            name="analyze_s3_resilience",
            description=(
                "Evaluates an S3 bucket's configuration against AWS Well-Architected "
                "Reliability standards. Requires bucket name and dimensions."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "bucket_name": {
                        "type": "string",
                        "description": "The S3 bucket name.",
                    },
                    "dimensions": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "value": {},
                            },
                        },
                        "description": "List of dimension name/value pairs from get_resource_dimensions.",
                    },
                },
                "required": ["bucket_name", "dimensions"],
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
        if name == "list_aws_resources":
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

        elif name == "get_resource_dimensions":
            resource_id = arguments.get("resource_id")
            resource_type = arguments.get("resource_type")
            if not resource_id:
                raise MissingToolParam("Missing resource_id")
            if not resource_type:
                raise MissingToolParam("Missing resource_type")
            results = await get_resource_dimensions(aws, resource_id, resource_type)
            return _text(_serialize(results))

        elif name == "analyze_api_gateway_resilience":
            dimensions = arguments.get("dimensions")
            if not dimensions:
                raise MissingToolParam("Missing dimensions")
            result = get_apigw_resilience_report(dimensions)
            return _text(_serialize(result))

        elif name == "analyze_lambda_resilience":
            function_name = arguments.get("function_name")
            dimensions = arguments.get("dimensions")
            if not function_name:
                raise MissingToolParam("Missing function_name")
            if not dimensions:
                raise MissingToolParam("Missing dimensions")
            result = get_lambda_resilience_report(function_name, dimensions)
            return _text(_serialize(result))

        elif name == "analyze_rds_resilience":
            db_instance_id = arguments.get("db_instance_id")
            dimensions = arguments.get("dimensions")
            if not db_instance_id:
                raise MissingToolParam("Missing db_instance_id")
            if not dimensions:
                raise MissingToolParam("Missing dimensions")
            result = get_rds_resilience_report(db_instance_id, dimensions)
            return _text(_serialize(result))

        elif name == "analyze_s3_resilience":
            bucket_name = arguments.get("bucket_name")
            dimensions = arguments.get("dimensions")
            if not bucket_name:
                raise MissingToolParam("Missing bucket_name")
            if not dimensions:
                raise MissingToolParam("Missing dimensions")
            result = get_s3_resilience_report(bucket_name, dimensions)
            return _text(_serialize(result))

        else:
            raise ValueError(f"Unknown tool: {name}")

    except MissingToolParam as e:
        return _error(str(e))
    except Exception as e:
        logger.exception(f"Tool '{name}' failed")
        return _error(f"{name} failed: {str(e)}")


# --- Entry Point ---

async def main():
    async with stdio_server() as (read_stream, write_stream):
        await app.run(
            read_stream,
            write_stream,
            app.create_initialization_options()
        )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
