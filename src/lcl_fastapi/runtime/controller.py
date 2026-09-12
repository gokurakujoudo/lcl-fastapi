"""Record controller observations without leaving writer threads across a fork."""

import asyncio
import os
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

from lclang.logger import LoggerHandlerConfig, resolve_logger_config

from lcl_fastapi.config import Settings
from lcl_fastapi.logging import resolve_log_directories
from lcl_fastapi.runtime.controller_writer import ControllerWriter
from lcl_fastapi.runtime.state import live_workers
from lcl_fastapi.sources import configuration_frame

# Process-manager-loop binding; forked workers never advance the controller observer.
CONTROLLER: ContextVar[ControllerLog | None] = ContextVar("lcl_fastapi_controller", default=None)


async def controller_config(path: Path) -> LoggerHandlerConfig:
    """Resolve only controller sinks, leaving worker PID expressions unevaluated.

    :param path: Absolute trusted service configuration path.
    :returns: Upstream settings anchored to the service configuration directory.
    :raises LclError: If configuration cannot be loaded.
    :raises ValueError: If controller logging is invalid.
    """
    async with configuration_frame(path, logger_role="controller") as frame:
        return resolve_log_directories(await resolve_logger_config(frame), path.parent)


@dataclass
class ControllerLog:
    """Observe workers from the owning process-manager loop, with no background task.

    :param writer: Owned upstream logger scope, suspended around native forks.
    :param settings: Launch-time state directories and sampling interval.
    :param identity: Complete-start identity filtering worker observations.
    :param workers: Previous live-worker observations keyed by PID and creation time.
    :param deadline: Next monotonic heartbeat deadline in seconds.
    """

    writer: ControllerWriter
    settings: Settings
    identity: dict[str, object]
    workers: dict[tuple[object, object], dict[str, object]] = field(default_factory=dict)
    deadline: float = 0

    def emit(self, events: list[str]) -> None:
        """Enqueue events through the controller-owned upstream scope.

        :param events: Bounded controller message batch.
        :raises OSError: If upstream logging fails.
        """
        self.writer.emit(events)

    def tick(self, force: bool = False) -> None:
        """Record heartbeat, worker transitions, and observed segment changes.

        :param force: Bypass the interval when taking the final shutdown observation.
        :raises OSError: If state or log storage is inaccessible.
        """
        now = time.monotonic()
        if not force and now < self.deadline:
            return
        self.deadline = now + self.settings.sample_interval_seconds
        current = {
            (worker["pid"], worker["process_create_time"]): worker
            for worker in live_workers(self.settings.worker_state_dir, self.identity["service_id"])
        }
        events = [f"heartbeat service_pid={self.identity['pid']} workers={len(current)}"]
        for key in self.workers.keys() - current.keys():
            events.append(f"worker down worker_pid={key[0]}")
        for key, worker in current.items():
            if key not in self.workers:
                events.append(f"worker up worker_pid={key[0]}")
            elif worker.get("log_files") != self.workers[key].get("log_files"):
                events.append(
                    f"worker log rotate worker_pid={key[0]} paths={worker.get('log_files')}"
                )
        self.workers = current
        self.emit(events)


def controller_tick() -> None:
    """Advance the active controller observer from a native master-loop boundary.

    :raises OSError: If observation or logging fails.
    """
    controller = CONTROLLER.get()
    if controller is not None:
        controller.tick()


def controller_event(message: str) -> None:
    """Flush an explicit lifecycle event when a controller scope exists.

    :param message: Framework event without secrets or request payloads.
    :raises OSError: If controller logging fails.
    """
    controller = CONTROLLER.get()
    if controller is not None:
        controller.emit([message])


@contextmanager
def controller_logging(
    path: Path, settings: Settings, identity: dict[str, object]
) -> Iterator[None]:
    """Own detached controller settings for exactly one complete service start.

    :param path: Absolute trusted configuration path.
    :param settings: Effective launch-time master settings.
    :param identity: Published complete-start identity.
    :returns: Scope flushing start, final worker transitions, and exit events.
    :raises OSError: If controller state or log storage fails.
    :raises BaseException: Propagates native manager failures after recording failure.
    """
    writer = ControllerWriter(asyncio.run(controller_config(path)))
    writer.open()
    controller = ControllerLog(writer, settings, identity)
    token = CONTROLLER.set(controller)
    owner = os.getpid()
    try:
        controller.emit([f"service started service_pid={owner} workers={settings.workers}"])
        yield
    except BaseException as error:
        if (
            os.getpid() == owner
            and writer.runner is not None
            and not (isinstance(error, SystemExit) and error.code in (None, 0))
        ):
            controller.emit(["service failed"])
        raise
    finally:
        try:
            if os.getpid() == owner and writer.runner is not None:
                controller.tick(force=True)
                controller.emit(["service stopped"])
        finally:
            CONTROLLER.reset(token)
            if os.getpid() == owner:
                writer.close()


@contextmanager
def controller_fork() -> Iterator[None]:
    """Suspend the parent logger while Gunicorn creates a native worker.

    :returns: Scope reopening controller logging only in the original parent.
    :raises BaseException: If draining, native worker creation, or reopening fails.
    """
    controller = CONTROLLER.get()
    owner = os.getpid()
    if controller is not None:
        controller.writer.close()
    try:
        yield
    finally:
        if controller is not None and os.getpid() == owner:
            controller.writer.open()
