"""Compose background thread startup and retirement inside the business lifespan."""

import asyncio
import logging
import time
from collections.abc import Callable, Mapping
from pathlib import Path

from fastapi import FastAPI

from lcl_fastapi.background.config import WorkerPolicy
from lcl_fastapi.background.context import BackgroundWorker
from lcl_fastapi.background.journal import BackgroundJournal
from lcl_fastapi.background.thread import WorkerThread


class BackgroundManager:
    """Own registered threads and serialize lifecycle journal writes on the API loop.

    :param workers: Code-registered executable map.
    :param policies: Validated launch-time switches.
    :param app: Resource-owning application.
    :param state: Business lifespan state, or None.
    :param path: Formal configuration source.
    :param journal: API identity-scoped event queue and writer.
    :param timeout: Seconds allowed for cooperative shutdown before controller termination.
    :param failure_callback: Optional service shutdown request after publication failure.
    """

    def __init__(
        self,
        workers: Mapping[str, BackgroundWorker],
        policies: Mapping[str, WorkerPolicy],
        app: FastAPI,
        state: Mapping[str, object] | None,
        path: Path,
        journal: BackgroundJournal,
        timeout: float,
        failure_callback: Callable[[], None] | None = None,
    ) -> None:
        """Prepare owned threads without starting executables.

        :param workers: Validated registrations.
        :param policies: Validated control switches.
        :param app: Shared application resource owner.
        :param state: Business state yielded before background startup.
        :param path: Thread-local configuration source.
        :param journal: Ordered lifecycle event writer.
        :param timeout: Cooperative shutdown deadline in seconds.
        :param failure_callback: Service-owned shutdown request, or None in isolated use.
        """
        self.journal, self.timeout, self.policies = journal, timeout, policies
        self.failure_callback = failure_callback
        self.workers = {
            name: WorkerThread(name, executable, policies[name], app, state, path, journal.emit)
            for name, executable in workers.items()
            if policies[name].enabled
        }
        self.pump: asyncio.Task[None] | None = None
        self.finished = asyncio.Event()
        self.retirement: asyncio.Task[None] | None = None

    def snapshot(self) -> dict[str, object]:
        """Read enabled-thread state and explicit disabled registrations.

        :returns: Detached state by worker name; threads are not counted as API workers.
        """
        return {
            name: self.workers[name].snapshot()
            if policy.enabled
            else {
                "status": "disabled",
                "attempts": 0,
                "restarts": 0,
            }
            for name, policy in self.policies.items()
        }

    async def publish(self) -> None:
        """Drain events periodically until retirement, including the final queued batch.

        :raises OSError: If journal publication fails.
        """
        try:
            while not self.finished.is_set():
                await asyncio.to_thread(self.journal.flush)
                await asyncio.sleep(0.05)
            await asyncio.to_thread(self.journal.flush)
        except OSError:
            for worker in self.workers.values():
                worker.stop()
            try:
                logging.getLogger("lcl_fastapi").exception("background journal publication failed")
            finally:
                if self.failure_callback is not None and self.retirement is None:
                    self.failure_callback()
            raise

    async def start(self) -> None:
        """Wait for each thread's resources before exposing the API as ready.

        :raises BaseException: If initialization fails, after stopping already-started threads.
        """
        self.journal.emit(logging.INFO, "background initializing")
        await asyncio.to_thread(self.journal.flush)
        self.pump = asyncio.create_task(self.publish())
        try:
            for worker in self.workers.values():
                worker.thread.start()
                await asyncio.shield(asyncio.wrap_future(worker.ready))
                if self.pump.done():
                    self.pump.result()
        except BaseException:
            await self.stop()
            raise

    async def stop(self) -> None:
        """Complete retirement even if the lifespan task is repeatedly cancelled.

        :raises CancelledError: After retirement if the caller was cancelled.
        :raises BaseException: If unexpected retirement cleanup fails.
        """
        if self.retirement is None:
            self.retirement = asyncio.create_task(self.retire())
        cancelled = False
        while not self.retirement.done():
            try:
                await asyncio.shield(self.retirement)
            except asyncio.CancelledError:
                cancelled = True
        self.retirement.result()
        if cancelled:
            raise asyncio.CancelledError

    async def retire(self) -> None:
        """Retire every thread before caller-owned business resources are torn down.

        The controller observes the deadline and kills the complete API process if
        a callable cannot stop. The API loop stays available for cleanup bridges.

        :raises BaseException: If unexpected cleanup fails or retirement is cancelled.
        """
        self.journal.emit(logging.INFO, "background stopping", deadline=time.time() + self.timeout)
        for worker in self.workers.values():
            worker.stop()
        await asyncio.gather(
            *(
                asyncio.wrap_future(worker.done)
                for worker in self.workers.values()
                if worker.thread.ident is not None
            )
        )
        for worker in self.workers.values():
            if worker.thread.ident is not None:
                await asyncio.to_thread(worker.thread.join)
        self.journal.emit(logging.INFO, "background retired", retired=True)
        self.finished.set()
        if self.pump is not None:
            try:
                await self.pump
            except OSError:
                # Publication failure was reported and supervised at its source;
                # injecting it into business lifespan would skip post-yield teardown.
                pass
