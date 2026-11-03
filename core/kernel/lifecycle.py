"""Wait for owned cleanup without letting a cancelled waiter abandon it."""

import asyncio
import logging
from typing import TypeVar

from anyio import CancelScope

T = TypeVar("T")


async def wait_for_cleanup(task: asyncio.Task[T]) -> T:
    """Drain a shared task, then propagate caller cancellation or its result.

    Caller cancellation takes precedence over cleanup failure, which is still
    retrieved and logged without its potentially sensitive exception message.
    A cancelled internal task is a cleanup failure, never a reason to retry it.
    """
    cancelled: asyncio.CancelledError | None = None
    caller = asyncio.current_task()
    initial_cancels = caller.cancelling() if caller is not None else 0
    with CancelScope(shield=True):
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError as exc:
                if caller is not None and caller.cancelling() > initial_cancels:
                    cancelled = exc
                if task.cancelled():
                    break
                cancelled = exc
            except Exception:
                break  # Read the result below, including the failure.
        try:
            result = task.result()
        except asyncio.CancelledError as exc:
            failure: Exception = RuntimeError("Internal cleanup task was cancelled")
            if cancelled is None:
                raise failure from exc
            logging.getLogger(__name__).warning("Cleanup failed (%s)", type(failure).__name__)
            raise cancelled from failure
        except Exception as exc:
            if cancelled is None:
                raise
            logging.getLogger(__name__).warning("Cleanup failed (%s)", type(exc).__name__)
            raise cancelled from exc
    if cancelled is not None:
        raise cancelled
    return result
