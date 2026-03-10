import os

import boto3

from src.models.ai import Tool


def ask_ai(messages, tool: Tool):
    max_tokens = int(os.getenv('BEDROCK_MODEL_MAX_TOKENS'))
    model_id = os.getenv('BEDROCK_MODEL_ID')

    bedrock_agent = boto3.client('bedrock-runtime')

    tool_list = [
        dict(
            toolSpec=dict(
                name=tool.name,
                description=tool.description,
                inputSchema={"json": tool.expected_output_class.model_json_schema()}
            )
        )
    ]
    response = bedrock_agent.converse(
        modelId=model_id,
        messages=messages,
        inferenceConfig=dict(maxTokens=max_tokens, temperature=0),
        toolConfig=dict(tools=tool_list),
    )

    for event in response['output']['message']['content']:
        if 'toolUse' in event:
            output = event['toolUse']['input']
            return tool.expected_output_class(**output)
    return None
