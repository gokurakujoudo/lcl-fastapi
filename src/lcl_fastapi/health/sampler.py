"""Collect psutil metrics outside HTTP handlers and publish worker observations."""

import asyncio
import math
import socket
from collections.abc import Callable
from contextlib import suppress
from copy import deepcopy
from pathlib import Path
from time import time

import psutil

from lcl_fastapi.logging import RequestLogger


def system_snapshot(paths: tuple[Path, ...]) -> dict[str, object]:
    """Read nonblocking system counters, preserving unavailable fields.

    :param paths: Absolute paths whose filesystem capacities should be reported.
    :returns: Detached system metrics with per-field unavailable diagnostics.
    """
    server: dict[str, object] = {}
    try:
        server["hostname"] = socket.gethostname()
    except OSError as error:
        server["hostname"] = {"status": "unavailable", "error": str(error)}
    try:
        server["cpu"] = {
            "logical_count": psutil.cpu_count(),
            "usage_percent": psutil.cpu_percent(interval=None),
        }
    except (OSError, psutil.Error) as error:
        server["cpu"] = {"status": "unavailable", "error": str(error)}
    try:
        memory = psutil.virtual_memory()
        server["memory"] = {
            "total_bytes": memory.total,
            "available_bytes": memory.available,
            "usage_percent": memory.percent,
        }
    except (OSError, psutil.Error) as error:
        server["memory"] = {"status": "unavailable", "error": str(error)}
    disks: list[dict[str, object]] = []
    for path in paths:
        try:
            disk = psutil.disk_usage(str(path))
            disks.append(
                {
                    "path": str(path),
                    "total_bytes": disk.total,
                    "free_bytes": disk.free,
                    "usage_percent": disk.percent,
                }
            )
        except (OSError, psutil.Error) as error:
            disks.append({"path": str(path), "status": "unavailable", "error": str(error)})
    server["disk"] = disks
    return server


class HealthSampler:
    """Own periodic sampling and bounded-lag runtime publication.

    :param paths: Absolute disk paths to inspect.
    :param interval: Positive finite interval in seconds.
    :param logger: Worker logger used for changed sampling failures.
    :param publish: Callback publishing current active logs and sample time.
    """

    def __init__(
        self,
        paths: tuple[Path, ...],
        interval: float,
        logger: RequestLogger,
        publish: Callable[[float], None],
    ) -> None:
        """Create an idle sampler without opening resources.

        :param paths: Absolute filesystem observation paths.
        :param interval: Refresh interval in seconds.
        :param logger: Worker-owned logger.
        :param publish: Callback recording worker log state.
        :raises ValueError: If the interval is not positive and finite.
        """
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("sampling interval must be positive and finite")
        self.paths, self.interval, self.logger, self.publish = paths, interval, logger, publish
        self.latest: dict[str, object] = {"status": "unavailable"}
        self.task: asyncio.Task[None] | None = None
        self.last_warning = ""

    async def refresh(self) -> None:
        """Replace metrics and publish an observed worker-state timestamp.

        :raises OSError: If state publication cannot complete.
        """
        self.latest = await asyncio.to_thread(system_snapshot, self.paths)
        unavailable: list[str] = []
        for metric in self.latest.values():
            values = metric if isinstance(metric, list) else [metric]
            unavailable.extend(
                str(value)
                for value in values
                if isinstance(value, dict) and value.get("status") == "unavailable"
            )
        warning = "\n".join(unavailable)
        if warning and warning != self.last_warning:
            self.logger.warning("health metrics unavailable: %s", warning)
        self.last_warning = warning
        self.publish(time())

    async def run(self) -> None:
        """Refresh periodically until the owner cancels the task.

        :raises CancelledError: When the owning scope stops the sampler.
        """
        while True:
            await asyncio.sleep(self.interval)
            try:
                await self.refresh()
            except OSError as error:
                self.logger.warning("worker state publication failed: %s", error)

    async def start(self) -> None:
        """Take the first observation and start exactly one background task.

        :raises RuntimeError: If the sampler has already been started.
        :raises OSError: If initial state publication fails.
        """
        if self.task is not None:
            raise RuntimeError("health sampler already started")
        await self.refresh()
        self.task = asyncio.create_task(self.run(), name="lcl-health-sampler")

    async def stop(self) -> None:
        """Cancel and join the background task; repeated stops are harmless."""
        if self.task is not None:
            self.task.cancel()
            with suppress(asyncio.CancelledError):
                await self.task
            self.task = None

    def snapshot(self) -> dict[str, object]:
        """Copy the most recent complete observation without performing I/O.

        :returns: Detached server metrics, or an initial unavailable marker.
        """
        return deepcopy(self.latest)
