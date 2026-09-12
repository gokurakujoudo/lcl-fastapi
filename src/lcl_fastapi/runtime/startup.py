"""Prepare development settings without retaining a master logger thread."""

from dataclasses import replace
from pathlib import Path

from lclang.logger import LoggerHandlerConfig, use_logger, use_logger_handler

from lcl_fastapi.config import Settings, load_settings
from lcl_fastapi.runtime.probe import has_background_workers


async def startup_settings(path: Path, hot_reload: bool) -> Settings:
    """Load settings and log a single development worker-count override.

    :param path: Absolute trusted service configuration path.
    :param hot_reload: Whether this invocation enables Python source watching.
    :returns: Validated settings with one effective worker in reload mode.
    :raises ValueError: If configuration is invalid.
    :raises OSError: If configuration cannot be read.
    :raises LclError: If LCL evaluation or logger initialization fails.
    :raises RuntimeError: If disposable application registration probing fails.
    :raises OSError: If the registration interpreter cannot be started.
    """
    settings = await load_settings(path)
    if settings.workers > 1 and (hot_reload or await has_background_workers(path)):
        async with use_logger_handler(LoggerHandlerConfig(console={"stream": "stderr"}, file={})):
            logger = await use_logger(name="lcl_fastapi")
            logger.warning(
                "%s enabled: server.workers=%s has been forced to 1",
                "hot_reload" if hot_reload else "background_workers",
                settings.workers,
            )
        return replace(settings, workers=1)
    return settings
