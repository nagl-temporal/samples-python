import asyncio
import logging

from temporalio.client import Client
from temporalio.worker import Worker

from resource_pool.resource_allocator import ResourceAllocator
from resource_pool.resource_pool_workflow import ResourcePoolWorkflow
from resource_pool.resource_user_workflow import ResourceUserWorkflow, use_resource


async def main() -> None:
    logging.basicConfig(level=logging.INFO)

    # Start client
    client = await Client.connect("localhost:7233")

    resource_allocator = ResourceAllocator(client)

    # Run a worker for the workflow
    worker = Worker(
        client,
        task_queue="default",
        workflows=[ResourcePoolWorkflow, ResourceUserWorkflow],
        activities=[
            use_resource,
            resource_allocator.send_acquire_signal,
        ],
    )

    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
