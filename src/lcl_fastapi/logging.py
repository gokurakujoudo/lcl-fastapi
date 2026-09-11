"""Inject request metadata at log emission while retaining lclang ownership."""

import logging
import os
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from lclang.logger import Logger, LoggerHandlerConfig, LoggerRuntime, use_logger

from lcl_fastapi.context import get_request_context


class RequestLogger(Logger):
    """Retain upstream logging behavior while attaching the current request.

    :param name: Standard-library logger name, or None for the root logger.
    :param prefix: Text prefix managed by lclang's formatter.
    :param emit_level: Number of additional business wrapper frames to skip.
    """

    def log(self, level: int, msg: object, *args: object, **kwargs: Any) -> None:
        """Attach request identity before the record reaches the writer thread.

        :param level: Standard-library numeric severity.
        :param msg: Message object or percent-style formatting template.
        :param args: Deferred message interpolation arguments.
        :param kwargs: Standard-library extra, exception, and caller options.
        :raises RuntimeError: If no lclang logger runtime is active.
        """
        context = get_request_context()
        if context is not None:
            extra = dict(kwargs.pop("extra", None) or {})
            extra["request_id"] = context.request_id
            kwargs["extra"] = extra
            msg = f"request_id={context.request_id} {msg}"
        kwargs["stacklevel"] = kwargs.pop("stacklevel", 1) + 1
        super().log(level, msg, *args, **kwargs)


async def get_logger(prefix: str = "", emit_level: int = 0) -> RequestLogger:
    """Create a lightweight logger that reads context afresh for every record.

    :param prefix: Logger name and human-readable message prefix.
    :param emit_level: Additional application wrapper frames to skip.
    :returns: Request-aware lclang logger wrapper.
    :raises TypeError: If prefix is not text.
    :raises ValueError: If emit_level is not a nonnegative integer.
    :raises RuntimeError: If no worker logger scope is active.
    """
    logger = await use_logger(name=prefix or "lcl_fastapi", prefix=prefix, emit_level=emit_level)
    return RequestLogger(logger.name, logger.prefix, logger.emit_level)


def resolve_log_directories(config: LoggerHandlerConfig, base: Path) -> LoggerHandlerConfig:
    """Anchor upstream file declarations without adding an alternative schema.

    :param config: Validated upstream logger settings.
    :param base: Absolute directory containing the service configuration.
    :returns: Equivalent settings with absolute explicit file directories.
    """
    files: dict[str, object] = {}
    for name, declaration in config.file.items():
        values = dict(cast(Mapping[str, object], declaration))
        directory = values.get("directory")
        if directory is not None:
            values["directory"] = (base / cast(str | os.PathLike[str], directory)).resolve()
        files[name] = values
    return replace(config, file=files)


def active_log_paths(runtime: LoggerRuntime) -> list[str]:
    """Read current file observations from an active upstream logger runtime.

    :param runtime: Worker-owned lclang logger runtime, before its scope closes.
    :returns: Sorted unique absolute paths reported by active file sinks.
    """
    return sorted(
        {
            str(metric.path.resolve())
            for name, metric in runtime.metrics.sinks.items()
            if name.startswith("file.") and metric.path is not None
        }
    )


@contextmanager
def suppress_server_access_logs() -> Iterator[None]:
    """Suppress duplicate access records after upstream logger takeover.

    :returns: Context manager restoring both loggers' previous disabled flags.
    """
    loggers = [logging.getLogger(name) for name in ("uvicorn.access", "gunicorn.access")]
    previous = [logger.disabled for logger in loggers]
    try:
        for logger in loggers:
            logger.disabled = True
        yield
    finally:
        for logger, disabled in zip(loggers, previous, strict=True):
            logger.disabled = disabled
