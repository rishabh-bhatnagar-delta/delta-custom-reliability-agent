import os

import boto3


def ask_ai(message, session_id="unique-session-id"):
    agent_id = os.getenv('BEDROCK_AGENT_ID')
    agent_alias_id = os.getenv('BEDROCK_AGENT_ALIAS_ID')
    bedrock_agent = boto3.client('bedrock-agent-runtime')

    try:
        response = bedrock_agent.invoke_agent(
            inputText=message,
            agentId=agent_id,
            agentAliasId=agent_alias_id,
            sessionId=session_id,
            enableTrace=False
        )

        full_response = ""
        for event in response['completion']:
            if 'chunk' in event:
                full_response += event['chunk']['bytes'].decode('utf-8')
        return full_response

    except Exception as e:
        return f"Error: {str(e)}"
