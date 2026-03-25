import asyncio
import json
import logging
from typing import List

from src.core.aws_client import AWSClientProvider
from src.models.resources import CloudFormationStack, StackResource, StackSummary

# Initialize logger for internal tracking
logger = logging.getLogger(__name__)


async def fetch_only_stacks(aws_provider: AWSClientProvider) -> List[StackSummary]:
    """
    Fetches basic metadata (Name, ID, blockCode tag) for all active stacks.
    """
    client = aws_provider.get_cft_client()
    active_statuses = [
        'CREATE_COMPLETE', 'ROLLBACK_COMPLETE',
        'UPDATE_COMPLETE', 'UPDATE_ROLLBACK_COMPLETE'
    ]

    stacks = []
    try:
        paginator = client.get_paginator('describe_stacks')
        pages = paginator.paginate()

        for page in pages:
            for stack in page.get('Stacks', []):
                if stack.get('StackStatus') not in active_statuses:
                    continue
                tags = {t['Key']: t['Value'] for t in stack.get('Tags', [])}
                stacks.append(StackSummary(
                    stack_name=stack['StackName'],
                    stack_id=stack['StackId'],
                    block_code=tags.get('blockCode')
                ))
        return stacks
    except Exception as e:
        logger.error(f"Failed to list stacks: {e}")
        raise e


async def fetch_resources_in_stack(aws_provider: AWSClientProvider, stack_name: str) -> List[StackResource]:
    """
    Fetches all resources for a specific CloudFormation stack.
    """
    client = aws_provider.get_cft_client()
    resources = []

    try:
        res_paginator = client.get_paginator('list_stack_resources')
        res_iterator = res_paginator.paginate(StackName=stack_name)

        for res_page in res_iterator:
            for res in res_page.get('StackResourceSummaries', []):
                resources.append(StackResource(
                    logical_id=res['LogicalResourceId'],
                    physical_id=res.get('PhysicalResourceId'),
                    resource_type=res['ResourceType'],
                    status=res['ResourceStatus']
                ))
        return resources
    except Exception as e:
        logger.warning(f"Could not list resources for stack {stack_name}: {e}")
        return []


async def fetch_and_print_stack(aws_provider: AWSClientProvider, stack: StackSummary):
    """Fetches resources for a single stack and prints the result."""
    stack_resources = await fetch_resources_in_stack(aws_provider, stack.stack_name)

    stack_obj = CloudFormationStack(
        stack_name=stack.stack_name,
        stack_id=stack.stack_id,
        block_code=stack.block_code,
        resources=stack_resources
    )
    print(json.dumps(stack_obj.model_dump(), indent=2))


async def run_local():
    print("--- Fetching All Stacks & Their Resources ---")
    provider = AWSClientProvider()

    stacks_list = await fetch_only_stacks(provider)

    if not stacks_list:
        print("No active CloudFormation stacks found.")
        return

    print(f"\n[i] Found {len(stacks_list)} active stack(s). Fetching resources...\n")

    for stack in stacks_list:
        await fetch_and_print_stack(provider, stack)

    print(f"\n[✓] Successfully processed {len(stacks_list)} stacks.")


if __name__ == "__main__":
    asyncio.run(run_local())
