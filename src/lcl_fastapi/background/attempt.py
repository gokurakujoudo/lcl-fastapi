"""Share one immutable logging policy across synchronous and async Worker entry."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from lcl_fastapi.background.context import BackgroundWorkerContext
from lcl_fastapi.context import use_lcl_frame
from lcl_fastapi.log_context import WORKER_LOG_CONTEXT
from lcl_fastapi.verbose.policy import use_service_verbose


@asynccontextmanager
async def worker_attempt(
    context: BackgroundWorkerContext,
    identity: tuple[str, int, str],
    cli_verbose: bool,
    file_enabled: bool,
) -> AsyncIterator[None]:
    """Bind Runner-local identity and policy for the whole attempt and its cleanup.

    :param context: Worker resources owned by this thread and Runner.
    :param identity: Registration name, execution attempt and callable path.
    :param cli_verbose: Launch-time console policy.
    :param file_enabled: Value pinned by the synchronous thread at attempt entry.
    :returns: Async scope retaining the native derived Frame until attempt cleanup.
    :raises BaseException: Propagates entry or cleanup failure after restoring bindings.
    """
    token = WORKER_LOG_CONTEXT.set(identity)
    try:
        with use_service_verbose(context.logger, cli_verbose, file_enabled=file_enabled):
            async with use_lcl_frame():
                yield
    finally:
        WORKER_LOG_CONTEXT.reset(token)
