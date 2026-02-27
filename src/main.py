import asyncio
import json

import mcp.types as types
from mcp.server import Server
from mcp.server.stdio import stdio_server

from core.aws_client import AWSClientProvider
from src.core.exceptions import MissingToolParam
from src.models.tool import ToolNames, ToolArgs
from tools.fetcher import fetch_cft_resources

# 1. Initialize logic components
# The name here is what appears in the MCP inspector/host
app = Server("aws-resource-fetcher")
aws = AWSClientProvider()


# 2. Define the tool interface
@app.list_tools()
async def list_tools() -> list[types.Tool]:
    """Exposes available tools to the LLM."""
    return [
        types.Tool(
            name=ToolNames.LIST_AWS_RESOURCES,
            description=(
                "Scans all CloudFormation stacks and returns a list of internal physical "
                "resources (e.g., API Gateway, RDS, DynamoDB). This is the starting "
                "point for infrastructure auditing."
            ),
            inputSchema={
                "type": "object",
                "properties": {},  # No parameters needed for this tool
            },
        ),
        types.Tool(
            name=ToolNames.GET_RESOURCE_DIMENSIONS,
            description=(
                "Given a resource's ARN, and the resource type, return "
                "a list of dimensions based on which the reliability "
                "posture of a resource can be calculated"
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    ToolArgs.RESOURCE_ARN: {
                        "type": "string",
                        "description": (
                            "The resource arn of the resource for which the "
                            "dimensions has to be fetched"
                        )
                    }
                },
            },
        )
    ]


# 3. Route tool calls to the implementation
@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    """Handles tool execution requests from the LLM."""
    if name == ToolNames.LIST_AWS_RESOURCES:
        try:
            # Execute the fetcher logic
            results = await fetch_cft_resources(aws)

            # Convert the list of Pydantic models to a readable JSON string
            content = json.dumps(
                [r.model_dump() for r in results],
                indent=2
            )

            return [types.TextContent(type="text", text=content)]

        except Exception as e:
            # Report the error back to the LLM in a structured format
            return [types.TextContent(type="text", text=f"Error fetching resources: {str(e)}")]
    elif name == ToolNames.GET_RESOURCE_DIMENSIONS:
        resource_arn = arguments.get(ToolArgs.RESOURCE_ARN)
        if not resource_arn:
            raise MissingToolParam("Missing or empty Resource ARN")

    raise ValueError(f"Unknown tool: {name}")


# 4. Main execution loop
async def main():
    """Starts the server using Standard I/O transport."""
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
