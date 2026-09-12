"""Bind one worker lease, observation, and graceful shutdown bridge."""

import os
import signal
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import MethodType

from uvicorn import Server

from lcl_fastapi.config import Settings
from lcl_fastapi.runtime.lease import WorkerIdLease
from lcl_fastapi.runtime.service import shutdown_requested
from lcl_fastapi.runtime.state import (
    atomic_write,
    is_live,
    live_workers,
    process_identity,
    read_state,
)


@dataclass(slots=True)
class WorkerRuntime:
    """Hold process-scoped state; callers serialize observation writes.

    :param worker_id: Leased Snowflake worker number.
    :param settings: Fresh worker configuration.
    :param service: Verified master-start identity.
    :param identity: Actual worker process identity.
    :param control_token: Secret shared by workers of this complete start.
    :param directory: Master state directory, unaffected by worker config changes.
    """

    worker_id: int
    settings: Settings
    service: dict[str, object]
    identity: dict[str, object]
    control_token: str
    directory: Path

    def service_info(self) -> dict[str, object]:
        """Read verified sibling identities for a public health snapshot.

        :returns: Service summary containing no control credentials.
        :raises OSError: If local observations cannot be read.
        """
        workers = live_workers(
            Path(str(self.service["worker_state_dir"])),
            self.service["service_id"],
        )
        return {
            "name": self.settings.app_name,
            "version": self.settings.app_version,
            "runtime": self.service["runtime"],
            "service_pid": self.service["pid"],
            "gunicorn_pid": self.service["pid"] if self.service["runtime"] == "gunicorn" else None,
            "worker_pid": self.identity["pid"],
            "configured_workers": self.service["configured_workers"],
            "running_workers": len(workers),
            "worker_pids": [record["pid"] for record in workers],
            "uptime_seconds": max(0.0, time.time() - float(str(self.service["started_at"]))),
        }

    def publish(self, log_files: list[str], observed_at: float) -> None:
        """Replace this worker's complete active-log observation.

        :param log_files: Actual absolute paths reported by lclang logger metrics.
        :param observed_at: Observation time in Unix seconds.
        :raises OSError: If state publication fails.
        """
        record = self.identity | {
            "service_id": self.service["service_id"],
            "snowflake_worker_id": self.worker_id,
            "log_files": sorted(set(log_files)),
            "observed_at": observed_at,
        }
        path = Path(str(self.service["worker_state_dir"])) / f"{self.identity['pid']}.json"
        atomic_write(path, record)

    def request_shutdown(self) -> None:
        """Request whole-service shutdown after the HTTP response is sent.

        :raises RuntimeError: If the master identity is no longer valid.
        :raises OSError: If signaling or marker publication fails.
        """
        current = read_state(self.directory / "runtime.json")
        if current != self.service or not is_live(current):
            raise RuntimeError("service identity changed before shutdown")
        atomic_write(self.directory / "shutdown.json", {"service_id": current["service_id"]})
        if self.service["runtime"] == "gunicorn":
            os.kill(int(str(current["pid"])), signal.SIGTERM)


def uvicorn_server() -> Server:
    """Identify Uvicorn 0.52's active server through its installed signal handler.

    :returns: Verified upstream server owning the current worker event loop.
    :raises RuntimeError: If the expected native Uvicorn handler is not installed.
    """
    handler = signal.getsignal(signal.SIGTERM)
    if not isinstance(handler, MethodType) or not isinstance(handler.__self__, Server):
        raise RuntimeError("expected Uvicorn 0.52 Server SIGTERM handler in worker")
    return handler.__self__


def watch_worker_shutdown(
    runtime: WorkerRuntime,
    finished: threading.Event,
    server: Server,
) -> None:
    """Bridge a local shutdown marker to Uvicorn's public graceful-exit flag.

    :param runtime: Worker whose service marker is monitored.
    :param finished: Event ending this owned thread during lifespan cleanup.
    :param server: Verified native Uvicorn server, sharing the process with this thread.
    """
    while not finished.wait(0.05):
        reload = read_state(runtime.directory / "reload.json")
        retire = (
            reload.get("service_id") == runtime.service["service_id"]
            and reload.get("pid") == runtime.identity["pid"]
            and reload.get("process_create_time") == runtime.identity["process_create_time"]
        )
        if retire or shutdown_requested(runtime.directory, runtime.service):
            server.should_exit = True
            return


@contextmanager
def worker_runtime(settings: Settings) -> Iterator[WorkerRuntime]:
    """Acquire worker resources and clean them on failure or lifespan exit.

    :param settings: Configuration loaded inside the actual worker process.
    :returns: Context manager yielding process-scoped runtime state.
    :raises RuntimeError: If no matching live master exists or IDs are exhausted.
    :raises OSError: If runtime files cannot be accessed.
    """
    directory = Path(os.environ.get("LCL_FASTAPI_STATE", str(settings.state_dir)))
    service = read_state(directory / "runtime.json")
    if not is_live(service):
        raise RuntimeError("no verified service master; start with lcl-fastapi serve")
    lease = WorkerIdLease.acquire(
        state_dir=directory,
        worker_id_base=settings.worker_id_base,
        worker_id_count=settings.worker_id_count,
    )
    finished = threading.Event()
    watcher: threading.Thread | None = None
    identity = process_identity()
    path = Path(str(service["worker_state_dir"])) / f"{identity['pid']}.json"
    try:
        runtime = WorkerRuntime(
            lease.worker_id,
            settings,
            service,
            identity,
            (directory / "control.token").read_text(encoding="ascii"),
            directory,
        )
        runtime.publish([], time.time())
        if service["runtime"] == "uvicorn":
            server = uvicorn_server()
            watcher = threading.Thread(
                target=watch_worker_shutdown,
                args=(runtime, finished, server),
            )
            watcher.start()
        yield runtime
    finally:
        finished.set()
        if watcher is not None:
            watcher.join()
        if read_state(path).get("service_id") == service["service_id"]:
            path.unlink(missing_ok=True)
        lease.release()
