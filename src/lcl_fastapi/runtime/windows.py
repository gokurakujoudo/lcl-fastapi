"""Adapt native Uvicorn worker management to console-free graceful stopping."""

import threading
import traceback

from starlette.types import ASGIApp
from uvicorn import Config
from uvicorn.config import STARTUP_FAILURE
from uvicorn.supervisors import Multiprocess

from lcl_fastapi.config import Settings
from lcl_fastapi.runtime.application import load_application
from lcl_fastapi.runtime.service import shutdown_requested
from lcl_fastapi.runtime.state import atomic_write


def load_windows_application() -> ASGIApp:
    """Classify factory initialization errors as native Uvicorn startup failures.

    :returns: The independently loaded downstream ASGI application.
    :raises SystemExit: With Uvicorn's startup-failure code after printing the cause.

    Interrupts and explicit process exits propagate without reclassification.
    Linux uses the shared loader directly and retains Gunicorn's error handling.
    """
    try:
        return load_application()
    except Exception as error:
        traceback.print_exception(error)
        raise SystemExit(STARTUP_FAILURE) from error


class ServiceMultiprocess(Multiprocess):
    """Use Uvicorn's worker recovery and a file bridge for graceful shutdown.

    :param config: Uvicorn configuration for this master.
    :param sockets: Bound sockets shared with native Uvicorn workers.

    The native ``terminate_all`` uses console control events on Windows. This
    override asks lifespan-owned worker bridges to set their verified Server's
    graceful-exit flag, so services without an attached console can also stop.
    """

    def terminate_all(self) -> None:
        """Publish shutdown before Uvicorn joins its owned worker processes.

        :raises OSError: If the service control marker cannot be published.
        """
        atomic_write(
            self.settings.state_dir / "shutdown.json",
            {
                "service_id": self.identity["service_id"],
            },
        )

    def watch_shutdown(self, finished: threading.Event) -> None:
        """Wake native process management after an accepted shutdown request.

        :param finished: Event ending this bridge when the master exits.
        """
        while not finished.wait(0.05):
            if shutdown_requested(self.settings.state_dir, self.identity):
                self.should_exit.set()
                return

    settings: Settings
    """Validated master settings; assigned before the manager starts."""
    identity: dict[str, object]
    """Complete-start record published before any worker is spawned."""


def run_windows(settings: Settings, identity: dict[str, object]) -> None:
    """Run the upstream Uvicorn multiprocess manager, even for one worker.

    :param settings: Detached master listener and process-management settings.
    :param identity: Published master identity.
    :raises OSError: If binding the listener or control storage fails.
    :raises RuntimeError: If Uvicorn reports a worker lifespan startup failure.
    """
    config = Config(
        "lcl_fastapi.runtime.windows:load_windows_application",
        factory=True,
        host=settings.host,
        port=settings.port,
        workers=settings.workers,
        # IOCP accepts keep idle workers schedulable when they share the listener.
        loop="asyncio:ProactorEventLoop",
        backlog=settings.backlog,
        timeout_keep_alive=settings.keep_alive_seconds,
        timeout_graceful_shutdown=settings.graceful_timeout_seconds,
        access_log=False,
        lifespan="on",
        root_path="",
    )
    listener = config.bind_socket()
    finished = threading.Event()
    manager = ServiceMultiprocess(config, sockets=[listener])
    manager.settings = settings
    manager.identity = identity
    watcher = threading.Thread(target=manager.watch_shutdown, args=(finished,))
    watcher.start()
    try:
        manager.run()
        if any(process.exitcode == STARTUP_FAILURE for process in manager.processes):
            raise RuntimeError("Uvicorn worker startup failed; service stopped")
    finally:
        finished.set()
        watcher.join()
        listener.close()
