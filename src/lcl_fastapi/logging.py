"""Inject request metadata at log emission while retaining lclang ownership."""

import logging
import os
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, cast

from lclang.logger import Logger, LoggerHandlerConfig, LoggerRuntime, use_logger

from lcl_fastapi.log_context import log_metadata
from lcl_fastapi.log_events import event_message


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
        :raises LclStateError: If no lclang logger runtime is active.
        """
        extra = dict(kwargs.pop("extra", None) or {})
        extra.update(log_metadata())
        extra["lcl_business_record"] = True
        kwargs["extra"] = extra
        kwargs["stacklevel"] = kwargs.pop("stacklevel", 1) + 1
        super().log(level, msg, *args, **kwargs)

    def log_event(
        self,
        event: str,
        *,
        level: int = logging.INFO,
        record: object | None = None,
        fields: Iterable[str] = (),
        masked_fields: Iterable[str] = (),
        **values: object,
    ) -> None:
        """Emit one explicit business event with native bounded field rendering.

        :param event: Single-line escaped business event title.
        :param level: Numeric severity, defaulting to INFO.
        :param record: Optional dataclass instance containing selected direct fields.
        :param fields: Field names in their requested display order.
        :param masked_fields: Names redacted before reading or formatting.
        :param values: Additional explicitly selected values in keyword order.
        :raises TypeError: If an enabled event has an invalid title or record type.
        :raises ValueError: If enabled selections are unknown, repeated or conflicting.
        :raises LclStateError: If no native logger runtime is active.
        :raises Exception: If an unmasked selected getter fails.
        """
        if not self.isEnabledFor(level):
            return
        message = event_message(event, record, fields, masked_fields, values)
        self.log(level, "%s", message, stacklevel=2, extra={"lcl_business_event": event})


async def get_logger(prefix: str = "", emit_level: int = 0) -> RequestLogger:
    """Create a lightweight logger that reads context afresh for every record.

    :param prefix: Logger name and human-readable message prefix.
    :param emit_level: Additional application wrapper frames to skip.
    :returns: Request-aware lclang logger wrapper.
    :raises LclValidationError: If prefix or emit_level violates the native logger contract.
    :raises LclStateError: If no worker logger scope is active.
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
