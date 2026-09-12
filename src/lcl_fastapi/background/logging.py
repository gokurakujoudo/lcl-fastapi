"""Route shared process logging through native per-sink logger-name filters."""

import copy
import logging
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import replace
from typing import cast

from lclang.logger import LoggerHandlerConfig, LoggerRuntime

from lcl_fastapi.background.config import WorkerPolicy

# Unitless thread/task binding, absent for API and framework service activity.
BACKGROUND_NAME: ContextVar[str | None] = ContextVar("lcl_background_name", default=None)


class WorkerLogFilter(logging.Filter):
    """Namespace copied records before the shared lclang writer sees them."""

    def filter(self, record: logging.LogRecord) -> logging.LogRecord:
        """Preserve caller metadata while attaching a routing-only source namespace.

        :param record: Producer record, left unchanged for other handlers.
        :returns: Copy with a role-qualified logger name.
        """
        selected = copy.copy(record)
        worker = BACKGROUND_NAME.get()
        role = "api" if worker is None else f"background.{worker}"
        selected.name = f"lcl_fastapi.{role}.{record.name}"
        return selected


def background_log_config(
    config: LoggerHandlerConfig, policies: Mapping[str, WorkerPolicy], app_name: str, pid: int
) -> LoggerHandlerConfig:
    """Add enabled worker files and qualify native source filters for role isolation.

    :param config: Resolved process settings, without the controller sink.
    :param policies: Enabled and disabled registered workers.
    :param app_name: Validated application name used for default filenames.
    :param pid: API process identifier, shared by its background threads.
    :returns: Logger settings retaining native rotation, levels and template inheritance.
    :raises ValueError: If the resulting upstream file declaration is invalid.
    """
    if not policies:
        return config
    files = dict(config.file)
    for name, policy in policies.items():
        if policy.enabled:
            explicit = dict(cast(Mapping[str, object], files.get(name, {})))
            explicit.setdefault("filename", f"{app_name}.{name}.{pid}.log")
            files[name] = explicit
        else:
            files.pop(name, None)
    for name, declaration in files.items():
        if name == "default":
            continue
        values = dict(cast(Mapping[str, object], declaration))
        role = f"background.{name}" if name in policies else "api"
        prefix = f"lcl_fastapi.{role}"
        template = cast(Mapping[str, object], files.get("default", {}))
        sources = cast(
            tuple[str, ...], values.get("logger_names", template.get("logger_names", ()))
        )
        values["logger_names"] = tuple(f"{prefix}.{source}" for source in sources) or (prefix,)
        files[name] = values
    return replace(config, file=files)


@contextmanager
def background_log_routing(runtime: LoggerRuntime, enabled: bool) -> Iterator[None]:
    """Install one producer-side filter for exactly the API logging lifespan.

    :param runtime: Existing process-wide lclang runtime, never replaced.
    :param enabled: Whether this application registers background workers.
    :returns: Scope removing its owned filter after worker threads have stopped.
    """
    selected = WorkerLogFilter()
    if enabled:
        runtime.handler.addFilter(selected)
    try:
        yield
    finally:
        if enabled:
            runtime.handler.removeFilter(selected)
