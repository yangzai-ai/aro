"""Do not abandon resource-using threads when their async caller is cancelled."""

import asyncio
from collections.abc import Callable
from typing import TypeVar

from anyio import CancelScope

T = TypeVar("T")


async def drain_thread(operation: Callable[[], T]) -> T:
    """Cancellation propagates only after the submitted operation has finished.

    Python cannot stop a running inference thread. Shield the task from asyncio
    and AnyIO cancellation; repeated cancellation still cannot abandon ownership.
    """
    task = asyncio.create_task(asyncio.to_thread(operation))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        with CancelScope(shield=True):
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except Exception:
                    break
            if not task.cancelled():
                task.exception()  # Retrieve worker errors; preserve cancellation.
        raise
