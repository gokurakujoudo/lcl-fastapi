"""Own an upstream log scope whose threads can be drained before native fork."""

import asyncio
from contextlib import AsyncExitStack

from lclang.logger import LoggerHandlerConfig, use_logger, use_logger_handler
from lclang.logger.formatter import FILE_ONLY_ATTRIBUTE


class ControllerWriter:
    """Keep a persistent sink between forks, with synchronous master-loop access.

    :param config: Detached upstream controller-only logging configuration.
    """

    def __init__(self, config: LoggerHandlerConfig) -> None:
        """Retain settings without starting threads or opening files.

        :param config: Validated upstream settings used again after each fork.
        """
        self.config = config
        self.runner: asyncio.Runner | None = None
        self.stack = AsyncExitStack()

    def open(self) -> None:
        """Start one upstream scope and retain its loop until close.

        :raises BaseException: If initialization fails, after releasing its loop.
        """
        self.runner = asyncio.Runner()
        try:
            self.runner.run(self.stack.enter_async_context(use_logger_handler(self.config)))
        except BaseException:
            self.close()
            raise

    def close(self) -> None:
        """Drain the writer and executor, closing every owned resource before fork.

        :raises BaseException: If upstream teardown fails, after closing the loop.
        """
        runner, self.runner = self.runner, None
        if runner is not None:
            try:
                runner.run(self.stack.aclose())
            finally:
                runner.close()

    def emit(self, events: list[str]) -> None:
        """Enqueue events inside the active upstream scope.

        :param events: Bounded lifecycle/observation batch without secrets.
        :raises RuntimeError: If the writer is suspended or closed.
        """
        if self.runner is None:
            raise RuntimeError("controller logger is not active")
        self.runner.run(write_events(events))

    def emit_records(self, events: list[tuple[int, str]]) -> None:
        """Queue severity-preserving background events inside the controller scope.

        :param events: Logging levels and lifecycle messages from API journals.
        :raises RuntimeError: If the writer is suspended or closed.
        """
        if self.runner is None:
            raise RuntimeError("controller logger is not active")
        self.runner.run(write_records(events))


async def write_records(events: list[tuple[int, str]]) -> None:
    """Emit background lifecycle records to the controller's own file sinks.

    :param events: Ordered logging levels and messages without HTTP request context.
    :raises RuntimeError: If the controller's logging scope is unavailable.
    """
    logger = await use_logger(name="lcl_fastapi.controller")
    for level, message in events:
        logger.log(level, message, extra={FILE_ONLY_ATTRIBUTE: True})


async def write_events(events: list[str]) -> None:
    """Queue file-only controller records for the persistent upstream writer.

    :param events: Lifecycle messages without request data or secrets.
    :raises RuntimeError: If no accepting upstream logger scope exists.
    """
    logger = await use_logger(name="lcl_fastapi.controller")
    for event in events:
        logger.info(event, extra={FILE_ONLY_ATTRIBUTE: True})
