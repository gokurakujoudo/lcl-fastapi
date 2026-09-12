"""Configure Gunicorn's native ASGI worker without another CLI parser."""

import importlib
from collections.abc import Callable
from typing import Protocol, cast

from starlette.types import ASGIApp

from lcl_fastapi.config import Settings
from lcl_fastapi.runtime.application import load_application
from lcl_fastapi.runtime.reload import ReloadWatcher


class GunicornConfiguration(Protocol):
    """Describe the untyped upstream configuration mutation boundary."""

    def set(self, name: str, value: object) -> None:
        """Set one validated Gunicorn configuration value.

        :param name: Upstream option name.
        :param value: Value accepted by the upstream setting validator.
        """


class GunicornRunner(Protocol):
    """Describe Gunicorn's blocking arbiter entry point."""

    def run(self) -> None:
        """Run master supervision until native graceful shutdown finishes."""

    wait_for_signals: Callable[[float], list[int]]
    """Gunicorn 26's signal-queue wait boundary, wrapped for bounded watch polling."""
    stop: Callable[[bool], None]
    """Native worker shutdown accepting whether to drain requests gracefully."""


class NativeLifespan(Protocol):
    """Describe Gunicorn 26's completed ASGI startup outcome."""

    _startup_failed: bool
    """Native LifespanManager flag set by a failed ASGI startup message or task."""


class NativeWorker(Protocol):
    """Describe the existing ASGI worker passed to Gunicorn's exit hook."""

    lifespan: NativeLifespan | None
    """Native lifespan manager, absent when import fails before ASGI startup."""


class NativeArbiter(Protocol):
    """Describe the arbiter's established worker-startup failure exit code."""

    WORKER_BOOT_ERROR: int
    """Unitless process exit code owned by Gunicorn's Arbiter startup contract."""


def report_worker_exit(arbiter: NativeArbiter, worker: NativeWorker) -> None:
    """Report native ASGI startup failure after Gunicorn has cleaned the worker.

    :param arbiter: Native master interface supplying its startup-failure status.
    :param worker: Native ASGI worker whose run and cleanup have finished.
    :raises SystemExit: With the native boot-error status when lifespan startup failed.
    :raises AttributeError: If Gunicorn changes the required native lifespan interface.

    Gunicorn 26's ASGI runner logs startup errors but returns normally. Reading
    its final startup flag in the documented worker-exit hook prevents the
    arbiter from mistaking failed initialization for a recoverable worker exit.
    """
    if worker.lifespan is not None and worker.lifespan._startup_failed is True:
        raise SystemExit(arbiter.WORKER_BOOT_ERROR)


class GunicornApplication:
    """Supply the documented application interface to Gunicorn's arbiter.

    :param settings: Validated master settings; not reloaded on worker replacement.
    """

    def __init__(self, settings: Settings) -> None:
        """Construct upstream defaults and apply explicit service settings.

        :param settings: Master listener and worker-management settings.
        :raises ImportError: If the Linux Gunicorn dependency is unavailable.
        """
        factory = cast(
            Callable[[], GunicornConfiguration],
            importlib.import_module("gunicorn.config").Config,
        )
        self.cfg = factory()
        self.callable: ASGIApp | None = None
        options: dict[str, object] = {
            "bind": [f"{settings.host}:{settings.port}"],
            "workers": settings.workers,
            "worker_class": "asgi",
            "asgi_lifespan": "on",
            "asgi_loop": "asyncio",
            "backlog": settings.backlog,
            "keepalive": settings.keep_alive_seconds,
            "graceful_timeout": settings.graceful_timeout_seconds,
            "accesslog": None,
            "preload_app": False,
            "root_path": "",
            "worker_exit": report_worker_exit,
        }
        for name, value in options.items():
            self.cfg.set(name, value)

    def wsgi(self) -> ASGIApp:
        """Load the downstream ASGI application once in the calling worker.

        :returns: Worker-local ASGI application.
        :raises ImportError: If the configured application cannot be imported.
        """
        if self.callable is None:
            self.callable = load_application()
        return self.callable

    def reload(self) -> None:
        """Keep master settings unchanged during Gunicorn-native worker reload."""


def run_linux(settings: Settings, identity: dict[str, object], hot_reload: bool = False) -> None:
    """Run Gunicorn's arbiter in the already-recorded master process.

    :param settings: Detached settings used by the master for this complete start.
    :param identity: Published master identity, owned by the surrounding scope.
    :param hot_reload: Watch explicit Python roots from the native arbiter loop.
    :raises ImportError: If Gunicorn is not installed on Linux.
    :raises OSError: If configured watch directories cannot be opened.
    :raises SystemExit: When the native arbiter exits, nonzero on service failure.
    """
    application = GunicornApplication(settings)
    factory = cast(
        Callable[[GunicornApplication], GunicornRunner],
        importlib.import_module("gunicorn.arbiter").Arbiter,
    )
    arbiter = factory(application)
    watcher = ReloadWatcher(settings, identity) if hot_reload else None
    if watcher is not None:
        native_wait = arbiter.wait_for_signals

        def watch_signals(timeout: float = 1.0) -> list[int]:
            """Preserve pending signal priority before polling Python changes.

            :param timeout: Native signal-wait deadline in seconds.
            :returns: Queued signals for the arbiter's normal dispatch.
            :raises OSError: If watching or worker signaling fails.
            :raises RuntimeError: If the watcher stops unexpectedly.
            """
            signals = native_wait(timeout)
            if not signals:
                try:
                    watcher.tick()
                except Exception:
                    arbiter.stop(True)
                    raise
            return signals

        arbiter.wait_for_signals = watch_signals
    try:
        arbiter.run()
    finally:
        if watcher is not None:
            watcher.close()
