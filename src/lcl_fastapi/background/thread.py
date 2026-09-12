"""Own one callable's thread, event loop, configuration and restart attempts."""

import asyncio
import inspect
import logging
import os
import threading
from collections.abc import Awaitable, Callable, Mapping
from concurrent.futures import Future
from contextlib import AsyncExitStack
from contextvars import Context, copy_context
from pathlib import Path
from typing import cast

from fastapi import FastAPI

from lcl_fastapi.background.config import WorkerPolicy
from lcl_fastapi.background.context import BackgroundWorker, BackgroundWorkerContext
from lcl_fastapi.background.logging import BACKGROUND_NAME
from lcl_fastapi.context import CONFIG_FRAME, use_lcl_frame
from lcl_fastapi.logging import get_logger
from lcl_fastapi.overrides import current_overrides, override_scope
from lcl_fastapi.sources import configuration_frame
from lcl_fastapi.tracebacks import exception_trace

# Seconds between all completed attempts; prevents tight loops for returning callables.
RESTART_DELAY = 1.0


class WorkerThread:
    """Manage one background registration without running its body on the API loop.

    :param name: Validated worker name.
    :param executable: Code-registered callable.
    :param policy: Launch-time switches.
    :param app: API application owning shared business state.
    :param state: Mapping yielded by business lifespan, or None.
    :param path: Formal configuration file, loaded once in this thread.
    :param emit: Thread-safe event collector; it performs no filesystem I/O here.
    """

    def __init__(
        self,
        name: str,
        executable: BackgroundWorker,
        policy: WorkerPolicy,
        app: FastAPI,
        state: Mapping[str, object] | None,
        path: Path,
        emit: Callable[[int, str], None],
    ) -> None:
        """Capture service bindings before starting a thread with a clean Context.

        :param name: Registration identifier.
        :param executable: Synchronous or asynchronous task entry.
        :param policy: Enabled/restart switches.
        :param app: Resource-owning application.
        :param state: Business lifespan mapping.
        :param path: Configuration source.
        :param emit: Thread-safe lifecycle event collector.
        """
        self.name, self.executable, self.policy = name, executable, policy
        self.app, self.state, self.path, self.emit = app, state, path, emit
        self.service_loop, self.service_context = asyncio.get_running_loop(), copy_context()
        self.overrides = current_overrides()
        self.stop_event = threading.Event()
        self.ready: Future[None] = Future()
        self.done: Future[None] = Future()
        self.loop: asyncio.AbstractEventLoop | None = None
        self.task: asyncio.Task[None] | None = None
        self.lock = threading.Lock()
        self.record: dict[str, object] = {"status": "starting", "attempts": 0, "restarts": 0}
        self.thread = threading.Thread(
            target=Context().run, args=(self.run,), name=f"lcl-background-{name}"
        )

    def snapshot(self) -> dict[str, object]:
        """Read detached state under its transition lock.

        :returns: Current status and execution counters.
        """
        with self.lock:
            return dict(self.record)

    def transition(self, status: str, attempt: int, level: int, reason: str) -> None:
        """Publish state and an ordered lifecycle diagnostic.

        :param status: New observable worker status.
        :param attempt: Current one-based attempt, or zero during initialization.
        :param level: Standard logging severity.
        :param reason: Human-readable exit/start reason without request data.
        """
        with self.lock:
            self.record.update(status=status, attempts=attempt, restarts=max(0, attempt - 1))
        self.emit(
            level, f"background worker={self.name} attempt={attempt} status={status} {reason}"
        )

    def stop(self) -> None:
        """Prevent restarts and deliver cancellation to an active asynchronous entry."""
        self.stop_event.set()
        with self.lock:
            loop, task = self.loop, self.task
            if loop is not None and task is not None:
                loop.call_soon_threadsafe(task.cancel)

    async def await_entry(self, operation: Awaitable[None]) -> None:
        """Track one asynchronous entry for thread-safe cancellation.

        :param operation: Result returned by the code-registered callable.
        :raises BaseException: Propagates entry failure to its restart boundary.
        """
        with self.lock:
            self.task = cast(asyncio.Task[None], asyncio.current_task())
            if self.stop_event.is_set():
                asyncio.get_running_loop().call_soon(self.task.cancel)
        try:
            await operation
        finally:
            with self.lock:
                self.task = None

    async def initialize(
        self, stack: AsyncExitStack, runner: asyncio.Runner
    ) -> BackgroundWorkerContext:
        """Initialize thread-local resources before reporting readiness to the API.

        :param stack: Thread-owned asynchronous resource stack.
        :param runner: Thread-owned Runner retained for synchronous entry points.
        :returns: Borrowed callable resources.
        :raises BaseException: Propagates configuration or logger initialization failure.
        """
        frame = await stack.enter_async_context(configuration_frame(self.path, os.getpid()))
        token = CONFIG_FRAME.set(frame)
        stack.callback(CONFIG_FRAME.reset, token)
        return BackgroundWorkerContext(
            self.name,
            self.app,
            self.state,
            await get_logger(self.name),
            self.stop_event,
            runner,
            self.service_loop,
            self.service_context,
        )

    def run(self) -> None:
        """Run and clean every attempt on the owned thread, reporting initialization failure."""
        BACKGROUND_NAME.set(self.name)
        attempt = 0
        try:
            with override_scope(self.overrides), asyncio.Runner() as runner:
                stack = AsyncExitStack()
                try:
                    context = runner.run(self.initialize(stack, runner))
                    with self.lock:
                        self.loop = runner.get_loop()
                    self.ready.set_result(None)
                    while not self.stop_event.is_set():
                        attempt += 1
                        level = logging.INFO
                        self.transition("running", attempt, level, "started")
                        scope = use_lcl_frame()
                        runner.run(scope.__aenter__())
                        try:
                            result = self.executable(context)
                            if inspect.isawaitable(result):
                                runner.run(self.await_entry(result))
                            self.transition("completed", attempt, logging.INFO, "normal exit")
                        except asyncio.CancelledError:
                            self.transition("stopped", attempt, logging.INFO, "cancelled")
                        except Exception as error:
                            level = logging.WARNING
                            context.logger.error(exception_trace(error))
                            self.transition(
                                "failed",
                                attempt,
                                logging.ERROR,
                                f"exception={type(error).__name__}",
                            )
                        finally:
                            runner.run(scope.__aexit__(None, None, None))
                        if not self.policy.auto_restart or self.stop_event.wait(RESTART_DELAY):
                            break
                        self.transition("restarting", attempt, level, "restarting after exit")
                finally:
                    runner.run(stack.aclose())
                    with self.lock:
                        self.loop = None
        except BaseException as error:
            if not self.ready.done():
                self.ready.set_exception(error)
            else:
                self.transition(
                    "failed", attempt, logging.ERROR, f"thread failure={type(error).__name__}"
                )
        finally:
            self.done.set_result(None)
