import asyncio
import json
import logging
import time
from typing import Dict, List, Optional, Tuple

from src.core.aws_client import AWSClientProvider
from src.core.constants import CACHE_TTL_MINUTES
from src.models.resources import CloudFormationStack, StackResource, StackSummary

# Initialize logger for internal tracking
logger = logging.getLogger(__name__)

# TTL cache: {stack_name: (resources, timestamp)}
_CACHE_TTL = CACHE_TTL_MINUTES * 60
_resource_cache: Dict[str, Tuple[List[StackResource], float]] = {}
_stacks_cache: Optional[Tuple[List[StackSummary], float]] = None


def _get_cached_resources(stack_name: str) -> Optional[List[StackResource]]:
    """Return cached resources if still valid, else None."""
    if stack_name in _resource_cache:
        resources, ts = _resource_cache[stack_name]
        if time.time() - ts < _CACHE_TTL:
            return resources
        del _resource_cache[stack_name]
    return None


def _get_cached_stacks() -> Optional[List[StackSummary]]:
    """Return cached stacks list if still valid, else None."""
    global _stacks_cache
    if _stacks_cache is not None:
        stacks, ts = _stacks_cache
        if time.time() - ts < _CACHE_TTL:
            return stacks
        _stacks_cache = None
    return None


def clear_cache():
    """Clear all cached data."""
    global _stacks_cache
    _resource_cache.clear()
    _stacks_cache = None
    logger.info("cache: cleared all cached stacks and resources")


async def fetch_only_stacks(aws_provider: AWSClientProvider, force_refresh: bool = False) -> List[StackSummary]:
    """
    Fetches basic metadata (Name, ID, blockCode tag) for all active stacks.
    """
    global _stacks_cache

    if not force_refresh:
        cached = _get_cached_stacks()
        if cached is not None:
            logger.info(f"fetch_only_stacks: returning {len(cached)} stack(s) from cache")
            return cached

    logger.info("fetch_only_stacks: scanning CloudFormation stacks")
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
                    block_code=tags.get('blockCode'),
                    region=aws_provider.region,
                ))
        _stacks_cache = (stacks, time.time())
        logger.info(f"fetch_only_stacks: found {len(stacks)} active stack(s), cached")
        return stacks
    except Exception as e:
        logger.error(f"fetch_only_stacks: failed - {e}", exc_info=True)
        raise e


async def fetch_resources_in_stack(aws_provider: AWSClientProvider, stack_name: str, force_refresh: bool = False) -> List[StackResource]:
    """
    Fetches all resources for a specific CloudFormation stack.
    """
    if not force_refresh:
        cached = _get_cached_resources(stack_name)
        if cached is not None:
            logger.info(f"fetch_resources_in_stack: '{stack_name}' -> {len(cached)} resource(s) (cached)")
            return cached

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
        _resource_cache[stack_name] = (resources, time.time())
        logger.info(f"fetch_resources_in_stack: '{stack_name}' -> {len(resources)} resource(s), cached")
        return resources
    except Exception as e:
        logger.warning(f"fetch_resources_in_stack: failed for '{stack_name}' - {e}", exc_info=True)
        return []


async def fetch_and_print_stack(aws_provider: AWSClientProvider, stack: StackSummary):
    """Fetches resources for a single stack and prints the result."""
    stack_resources = await fetch_resources_in_stack(aws_provider, stack.stack_name)

    stack_obj = CloudFormationStack(
        stack_name=stack.stack_name,
        stack_id=stack.stack_id,
        block_code=stack.block_code,
        region=stack.region,
        resources=stack_resources
    )
    print(json.dumps(stack_obj.model_dump(), indent=2))


async def fetch_stacks_multi_region(regions: List[str], force_refresh: bool = False) -> List[StackSummary]:
    """Fetch stacks across multiple regions concurrently."""
    async def _fetch_region(region: str):
        provider = AWSClientProvider(region=region)
        try:
            stacks = await fetch_only_stacks(provider, force_refresh=force_refresh)
            logger.info(f"fetch_stacks_multi_region: {region} -> {len(stacks)} stack(s)")
            return stacks
        except Exception as e:
            logger.warning(f"fetch_stacks_multi_region: {region} failed - {e}")
            return []

    results = await asyncio.gather(*(_fetch_region(r) for r in regions))
    all_stacks = []
    for stacks in results:
        all_stacks.extend(stacks)
    return all_stacks


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
