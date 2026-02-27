import asyncio
import json
import logging
from typing import List, Dict

from src.core.aws_client import AWSClientProvider
from src.models.resources import CloudFormationStack, StackResource

# Initialize logger for internal tracking
logger = logging.getLogger(__name__)


async def fetch_only_stacks(aws_provider: AWSClientProvider) -> List[Dict[str, str]]:
    """
    Fetches only the basic metadata (Name and ID) for all active stacks.
    """
    client = aws_provider.get_cft_client()
    active_statuses = [
        'CREATE_COMPLETE', 'ROLLBACK_COMPLETE',
        'UPDATE_COMPLETE', 'UPDATE_ROLLBACK_COMPLETE'
    ]

    stacks = []
    try:
        paginator = client.get_paginator('list_stacks')
        pages = paginator.paginate(StackStatusFilter=active_statuses)

        for page in pages:
            for summary in page.get('StackSummaries', []):
                stacks.append({
                    "stack_name": summary['StackName'],
                    "stack_id": summary['StackId']
                })
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


async def fetch_cft_resources(aws_provider: AWSClientProvider) -> List[CloudFormationStack]:
    """
    Orchestrator: Fetches stacks first, then fetches resources for each.
    """
    # 1. Get the list of stacks first
    stacks_list = await fetch_only_stacks(aws_provider)

    all_stacks_data = []

    # 2. Iterate and get resources for each stack
    for stack in stacks_list:
        s_name = stack["stack_name"]
        s_id = stack["stack_id"]

        stack_resources = await fetch_resources_in_stack(aws_provider, s_name)

        all_stacks_data.append(CloudFormationStack(
            stack_name=s_name,
            stack_id=s_id,
            resources=stack_resources
        ))

    return all_stacks_data


if __name__ == "__main__":
    async def run_local():
        print("--- Fetching All Stacks & Their Resources ---")
        try:
            provider = AWSClientProvider()

            # Execute the full orchestration
            results = await fetch_cft_resources(provider)

            if not results:
                print("No active CloudFormation stacks found.")
            else:
                # Print full JSON output
                output = [stack.model_dump() for stack in results]
                print(json.dumps(output, indent=2))
                print(f"\n[✓] Successfully processed {len(results)} stacks.")

        except Exception as err:
            print(f"Execution failed: {err}")


    asyncio.run(run_local())