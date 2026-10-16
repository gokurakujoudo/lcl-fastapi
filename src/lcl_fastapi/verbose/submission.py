"""Drain a submitted atomic update before propagating cancellation."""

import asyncio
from collections.abc import Callable

from lcl_fastapi.verbose.types import FileVerboseState


async def finish_file_update(
    update: Callable[[bool], FileVerboseState], enabled: bool
) -> FileVerboseState:
    """Finish an in-flight write so it cannot outlive service state cleanup.

    :param update: Current process client's synchronous atomic update operation.
    :param enabled: Requested strict Boolean target.
    :returns: Accepted state after the atomic write finishes.
    :raises CancelledError: After the submitted operation finishes if the caller was cancelled.
    :raises Exception: If an uncancelled submission fails.
    """
    operation = asyncio.create_task(asyncio.to_thread(update, enabled))
    cancellation: asyncio.CancelledError | None = None
    while not operation.done():
        try:
            await asyncio.shield(operation)
        except asyncio.CancelledError as error:
            cancellation = error
        except Exception:
            break
    try:
        result = operation.result()
    except Exception as error:
        if cancellation is not None:
            raise cancellation from error
        raise
    if cancellation is not None:
        raise cancellation
    return result
