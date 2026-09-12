"""Track service-loop operations until their cancellation cleanup has settled."""

import asyncio
from collections.abc import Awaitable, Callable
from concurrent.futures import Future
from contextvars import Context
from typing import cast


class ServiceBridge:
    """Own one worker's submissions; task state is accessed only on the service loop.

    :param loop: Resource-owning API loop.
    :param context: Service configuration bindings copied for each submission.
    """

    def __init__(self, loop: asyncio.AbstractEventLoop, context: Context) -> None:
        """Retain loop bindings without scheduling work.

        :param loop: Resource-owning API loop.
        :param context: Service context, without a background Frame.
        """
        self.loop, self.context = loop, context
        self.tasks: set[asyncio.Task[object]] = set()

    def submit[T](self, operation: Callable[[], Awaitable[T]]) -> Future[T]:
        """Schedule a factory while tracking actual Task lifetime separately from its Future.

        :param operation: Factory executed on the API loop.
        :returns: Thread-safe result Future, with normal cancellation semantics.
        :raises RuntimeError: If the service loop is closed.
        """

        async def invoke() -> T:
            """Retain ownership through the operation's complete finally chain.

            :returns: Operation result.
            :raises BaseException: Propagates operation failure or cancellation.
            """
            task = cast(asyncio.Task[object], asyncio.current_task())
            self.tasks.add(task)
            task.add_done_callback(self.completed)
            return await operation()

        pending = invoke()
        try:
            return self.context.copy().run(asyncio.run_coroutine_threadsafe, pending, self.loop)
        except RuntimeError:
            pending.close()
            raise

    def completed(self, task: asyncio.Task[object]) -> None:
        """Release settled task ownership and retrieve failures even if its Future was cancelled.

        :param task: Completed service operation, after all finally blocks have finished.
        """
        self.tasks.discard(task)
        if not task.cancelled():
            task.exception()

    async def drain(self) -> None:
        """Cancel remaining work once and await actual cleanup on the API loop.

        Already-cancelling Tasks are not cancelled again, preserving their finally blocks.
        The caller has stopped issuing submissions for the retiring attempt.
        """
        while self.tasks:
            tasks = tuple(self.tasks)
            for task in tasks:
                if not task.cancelling():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    def close_attempt(self) -> None:
        """Wait from the worker thread until all API-loop cleanup has completed.

        :raises RuntimeError: If the service loop closes before the worker retires.
        """
        asyncio.run_coroutine_threadsafe(self.drain(), self.loop).result()
