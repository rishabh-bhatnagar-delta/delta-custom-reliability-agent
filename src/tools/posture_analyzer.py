import os

import boto3
from botocore.exceptions import ClientError
from dotenv import load_dotenv
load_dotenv(verbose=True)


def ask_ai(message, session_id="unique-session-id"):
    # Bedrock Settings
    agent_id = os.getenv('BEDROCK_AGENT_ID')
    agent_alias_id = os.getenv('BEDROCK_AGENT_ALIAS_ID')

    if not agent_id or not agent_alias_id:
        return "Error: BEDROCK_AGENT_ID and BEDROCK_AGENT_ALIAS_ID environment variables must be set"

    bedrock_agent = boto3.client('bedrock-agent-runtime')

    try:
        response = bedrock_agent.invoke_agent(
            inputText=message,
            agentId=agent_id,
            agentAliasId=agent_alias_id,
            sessionId=session_id,
            enableTrace=False
        )

        response_body = response['completion']
        full_response = ""

        for event in response_body:
            chunk = event['chunk']
            if 'bytes' in chunk:
                full_response += chunk['bytes'].decode('utf-8')

        return full_response

    except ClientError as e:
        return f"Error calling Bedrock Agent: {e.response['Error']['Message']}"
    except Exception as e:
        return f"Unexpected error: {str(e)}"


if __name__ == '__main__':
    print(ask_ai("What is this?", ))
