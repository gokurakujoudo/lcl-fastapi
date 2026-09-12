"""Typed background callable resources and explicit cross-loop submission."""

import asyncio
import threading
from collections.abc import Awaitable, Callable, Mapping
from concurrent.futures import Future
from contextvars import Context
from dataclasses import dataclass

from fastapi import FastAPI

from lcl_fastapi.context import get_config
from lcl_fastapi.logging import RequestLogger


@dataclass(frozen=True, slots=True)
class BackgroundWorkerContext:
    """Borrow resources owned by one background thread and the service lifespan.

    :param name: Registered worker identifier.
    :param app: Application with business resources in its state.
    :param state: Optional mapping yielded by the business lifespan.
    :param logger: Logger routed to this worker's named file.
    :param stop_event: Cooperative stop signal shared safely with the API thread.
    :param runner: Owning background thread's runner, for synchronous entry points.
    :param service_loop: API loop owning shared asynchronous business resources.
    :param service_context: API configuration bindings, copied for each submitted operation.
    """

    name: str
    app: FastAPI
    state: Mapping[str, object] | None
    logger: RequestLogger
    stop_event: threading.Event
    runner: asyncio.Runner
    service_loop: asyncio.AbstractEventLoop
    service_context: Context

    async def get_config(self, key: str) -> object:
        """Resolve configuration in this thread's current derived Frame scope.

        :param key: Complete qualified LCL configuration name.
        :returns: Native evaluated value.
        :raises LclError: If lookup or evaluation fails.
        :raises RuntimeError: If used outside an active configuration scope.
        """
        return await get_config(key)

    def run[T](self, awaitable: Awaitable[T]) -> T:
        """Run asynchronous work from a synchronous worker on its own loop.

        :param awaitable: Operation created by the synchronous worker callable.
        :returns: Completed operation's result.
        :raises RuntimeError: If a loop is already running in this thread.
        :raises BaseException: Propagates the operation's failure or cancellation.
        """
        return self.runner.run(awaitable)

    def submit_to_service[T](self, operation: Callable[[], Awaitable[T]]) -> Future[T]:
        """Create and execute resource I/O on the API loop with its own configuration.

        :param operation: Async factory invoked on the API loop, not on the caller thread.
        :returns: Thread-safe future; async workers await it with asyncio.wrap_future.
        :raises RuntimeError: If the service loop has already closed.

        Operations must cooperate with the API loop and finish before worker exit.
        The callback must not wait synchronously on the submitting worker.
        """

        async def invoke() -> T:
            """Await the factory inside the receiving loop.

            :returns: Business operation's result.
            :raises BaseException: Propagates business failure to the returned future.
            """
            return await operation()

        return self.service_context.copy().run(
            asyncio.run_coroutine_threadsafe, invoke(), self.service_loop
        )


# Unitless callable contract; executables are supplied by code, never configuration.
type BackgroundWorker = Callable[[BackgroundWorkerContext], Awaitable[None] | None]
