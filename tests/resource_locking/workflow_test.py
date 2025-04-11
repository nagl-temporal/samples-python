import asyncio
from collections import defaultdict
from typing import Any, Optional, Sequence

from temporalio import activity
from temporalio.client import Client, WorkflowFailureError, WorkflowHandle
from temporalio.common import WorkflowIDConflictPolicy
from temporalio.worker import Worker

from resource_pool.resource_allocator import ResourceAllocator
from resource_pool.resource_pool_workflow import (
    ResourcePoolWorkflow,
    ResourcePoolWorkflowInput,
)
from resource_pool.resource_user_workflow import (
    ResourceUserWorkflow,
    ResourceUserWorkflowInput,
    UseResourceActivityInput,
)
from resource_pool.shared import RESOURCE_POOL_WORKFLOW_ID

TASK_QUEUE = "default"


async def test_resource_locking_workflow(client: Client):
    # key is resource, value is a description of resource usage
    resource_usage: defaultdict[str, list[Sequence[str]]] = defaultdict(list)

    # Mock out the activity to count executions
    @activity.defn(name="use_resource")
    async def use_resource_mock(input: UseResourceActivityInput) -> None:
        workflow_id = activity.info().workflow_id
        resource_usage[input.resource].append((workflow_id, "start"))
        # We need a small sleep here to bait out races
        await asyncio.sleep(0.05)
        resource_usage[input.resource].append((workflow_id, "end"))

    resource_allocator = ResourceAllocator(client)

    async with Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[ResourcePoolWorkflow, ResourceUserWorkflow],
        activities=[use_resource_mock, resource_allocator.send_acquire_signal],
    ):
        await run_all_workflows(client)

        # Did any workflow run in more than one place?
        workflow_id_to_resource: dict[str, str] = {}
        for resource, events in resource_usage.items():
            for workflow_id, event in events:
                if workflow_id in workflow_id_to_resource:
                    existing_resource = workflow_id_to_resource[workflow_id]
                    assert (
                        existing_resource == resource
                    ), f"{workflow_id} ran on both {resource} and {existing_resource}"
                else:
                    workflow_id_to_resource[workflow_id] = resource

        # Did any resource have more than one workflow on it at a time?
        for resource, events in resource_usage.items():
            holder: Optional[str] = None
            for workflow_id, event in events:
                if event == "start":
                    assert (
                        holder is None
                    ), f"{workflow_id} started on {resource} held by {holder}"
                    holder = workflow_id
                else:
                    assert (
                        holder == workflow_id
                    ), f"{workflow_id} ended on {resource} held by {holder}"
                    holder = None


async def run_all_workflows(client: Client):
    resource_user_handles: list[WorkflowHandle[Any, Any]] = []
    for i in range(0, 8):
        input = ResourceUserWorkflowInput(
            iteration_to_fail_after=None,
            should_continue_as_new=False,
        )
        if i == 0:
            input.should_continue_as_new = True
        if i == 1:
            input.iteration_to_fail_after = "first"

        handle = await client.start_workflow(
            workflow=ResourceUserWorkflow.run,
            arg=input,
            id=f"resource-user-workflow-{i}",
            task_queue=TASK_QUEUE,
        )
        resource_user_handles.append(handle)

    # Add some resources
    resource_pool_handle = await client.start_workflow(
        workflow=ResourcePoolWorkflow.run,
        arg=ResourcePoolWorkflowInput(
            resources={},
            waiters=[],
        ),
        id=RESOURCE_POOL_WORKFLOW_ID,
        task_queue="default",
        id_conflict_policy=WorkflowIDConflictPolicy.USE_EXISTING,
        start_signal="add_resources",
        start_signal_args=[["r_a", "r_b", "r_c"]],
    )

    for handle in resource_user_handles:
        try:
            await handle.result()
        except WorkflowFailureError:
            pass

    await resource_pool_handle.terminate()
