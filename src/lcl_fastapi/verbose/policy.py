"""Pin file diagnostics at execution entry without changing ordinary logger levels."""

import copy
import logging
from collections.abc import Iterator
from contextlib import contextmanager

from lclang.logger import Logger, LoggerHandlerConfig, LoggerRuntime, use_verbose
from lclang.logger.runtime_registry import current_runtime

from lcl_fastapi.log_context import FILE_VERBOSE_CONTEXT, REPORTED_HTTP_FAILURE, log_metadata
from lcl_fastapi.logging_defaults import DEFAULT_TAKEOVER_LOGGERS
from lcl_fastapi.verbose.control import current_file_control
from lcl_fastapi.verbose.sink import DiagnosticFileSink


class DiagnosticFilter(logging.Filter):
    """Preserve ordinary admission while routing diagnostic snapshots independently.

    :param level: Original global numeric threshold, including native CLI adjustments.
    :param cli_verbose: Whether the console admits native diagnostics for this start.
    """

    def __init__(self, level: int, cli_verbose: bool) -> None:
        """Retain immutable launch policy.

        :param level: Original global numeric threshold.
        :param cli_verbose: Launch-time console diagnostic policy.
        """
        super().__init__()
        self.level, self.cli_verbose = level, cli_verbose

    def filter(self, record: logging.LogRecord) -> logging.LogRecord | bool:
        """Detach identity and the producing execution's file policy before enqueue.

        :param record: Borrowed producer record.
        :returns: Detached admitted record, or False for a rejected level.
        """
        if (
            record.name in {"uvicorn.error", "gunicorn.error"}
            and record.exc_info
            and REPORTED_HTTP_FAILURE.get() == id(record.exc_info[1])
        ):
            REPORTED_HTTP_FAILURE.set(None)
            return False
        verbose = bool(
            getattr(record, "lclang_verbose", False)
            or getattr(record, "lcl_framework_verbose", False)
        )
        if not verbose and record.levelno < self.level:
            return False
        selected = copy.copy(record)
        selected.lcl_source_name = getattr(record, "lcl_source_name", record.name)
        if not getattr(selected, "lcl_business_record", False):
            selected.__dict__.update(log_metadata())
            if record.name in DEFAULT_TAKEOVER_LOGGERS:
                selected.lclang_compact = True
        if hasattr(selected, "request_id"):
            selected.name = selected.request_id
        if verbose:
            enabled = FILE_VERBOSE_CONTEXT.get()
            file_enabled = self.cli_verbose if enabled is None else enabled
            selected.lcl_file_verbose = file_enabled
            if not self.cli_verbose and not file_enabled:
                return False
            if not self.cli_verbose:
                selected.lclang_file_only = True
        return selected


def install_verbose_output(
    runtime: LoggerRuntime, config: LoggerHandlerConfig, cli_verbose: bool
) -> None:
    """Borrow the existing queue and native sinks for independent file diagnostics.

    :param runtime: Initialized native writer, owned by the service scope.
    :param config: Validated native settings, including the original ordinary threshold.
    :param cli_verbose: Launch-time console policy.
    """
    from lclang.logger.file_sink import FileSink

    runtime.handler.setLevel(min(int(config.level), logging.DEBUG))
    runtime.handler.addFilter(DiagnosticFilter(int(config.level), cli_verbose))
    runtime.dispatcher.sinks = [
        DiagnosticFileSink(sink) if isinstance(sink, FileSink) else sink
        for sink in runtime.dispatcher.sinks
    ]


@contextmanager
def use_service_verbose(
    logger: Logger, cli_verbose: bool, *, file_enabled: bool | None = None
) -> Iterator[None]:
    """Pin one execution and borrow the existing queue without registering a logger name.

    :param logger: Ordinary named source; its levels and handlers remain unchanged.
    :param cli_verbose: Console policy, used as the fallback outside a managed service.
    :param file_enabled: Already pinned value when bridging a Worker into its Runner.
    :returns: Scope restoring policy and native tracing after failure or cancellation.
    :raises LclStateError: If no native logger runtime is active.
    """
    if file_enabled is None:
        try:
            file_enabled = current_file_control().snapshot().enabled
        except RuntimeError:
            file_enabled = cli_verbose
    handler = current_runtime().handler
    token = FILE_VERBOSE_CONTEXT.set(file_enabled)
    diagnostic = logging.Logger(logger.backend.name, logging.DEBUG)
    diagnostic.propagate = False
    diagnostic.addHandler(handler)
    try:
        with use_verbose(diagnostic, enabled=cli_verbose or file_enabled):
            yield
    finally:
        diagnostic.removeHandler(handler)
        FILE_VERBOSE_CONTEXT.reset(token)
