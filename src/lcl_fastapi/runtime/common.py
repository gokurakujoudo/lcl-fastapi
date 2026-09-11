"""Expose synchronous service startup and asynchronous local CLI operations."""

import asyncio
import http.client
import os
import sys
import time
from pathlib import Path

from lcl_fastapi.config import load_settings
from lcl_fastapi.runtime.service import service_runtime
from lcl_fastapi.runtime.state import is_live, live_workers, read_state
from lcl_fastapi.runtime.worker import WorkerRuntime, worker_runtime

__all__ = ["WorkerRuntime", "active_logs", "inspect_status", "serve", "stop", "worker_runtime"]


def serve(config_path: Path) -> None:
    """Load master settings and run the native platform service manager.

    :param config_path: Trusted .lclcfg file, relative to the caller's directory.
    :raises RuntimeError: If called from an active event loop or unsupported OS.
    :raises OSError: If service storage or the listener cannot be opened.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("serve must run after the CLI event loop has closed")
    path = config_path.resolve()
    settings = asyncio.run(load_settings(path))
    previous = {name: os.environ.get(name) for name in ("LCL_FASTAPI_CONFIG", "LCL_FASTAPI_STATE")}
    os.environ["LCL_FASTAPI_CONFIG"] = str(path)
    os.environ["LCL_FASTAPI_STATE"] = str(settings.state_dir)
    try:
        with service_runtime(settings) as identity:
            if sys.platform == "win32":
                from lcl_fastapi.runtime.windows import run_windows

                run_windows(settings, identity)
            elif sys.platform == "linux":
                from lcl_fastapi.runtime.linux import run_linux

                run_linux(settings, identity)
            else:
                raise RuntimeError("lcl-fastapi supports Windows and Linux only")
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


async def inspect_status(config_path: Path) -> dict[str, object]:
    """Inspect identity-verified service and worker state without networking.

    :param config_path: Configuration used only to locate service state.
    :returns: Explicit RUNNING, STOPPED, or STALE status and live observations.
    :raises OSError: If state storage is inaccessible.
    """
    settings = await load_settings(config_path)
    record = await asyncio.to_thread(read_state, settings.state_dir / "runtime.json")
    if not record:
        return {"status": "STOPPED", "service": None, "workers": []}
    if not is_live(record):
        return {"status": "STALE", "service": None, "workers": []}
    directory = record.get("worker_state_dir")
    if not isinstance(directory, str):
        return {"status": "STALE", "service": None, "workers": []}
    workers = await asyncio.to_thread(live_workers, Path(directory), record.get("service_id"))
    return {"status": "RUNNING", "service": record, "workers": workers}


async def active_logs(config_path: Path) -> dict[str, object]:
    """Collect live workers' eventually consistent log-path observations.

    :param config_path: Configuration used only to locate service state.
    :returns: Deduplicated paths, earliest observation time, and stale-view flag.
    :raises OSError: If state storage cannot be inspected.
    """
    status = await inspect_status(config_path)
    paths: set[str] = set()
    observed: list[float] = []
    workers = status["workers"]
    service = status["service"]
    stale = False
    if isinstance(workers, list) and isinstance(service, dict):
        interval = float(service["sample_interval_seconds"])
        for worker in workers:
            for path in worker.get("log_files", []):
                if isinstance(path, str) and Path(path).is_absolute():
                    paths.add(path)
            timestamp = worker.get("observed_at")
            if isinstance(timestamp, (int, float)):
                observed.append(float(timestamp))
                stale |= time.time() - timestamp > interval
            else:
                stale = True
    return {
        "paths": sorted(paths),
        "observed_at": min(observed) if observed else None,
        "stale": stale,
    }


def send_shutdown(record: dict[str, object], token: str) -> None:
    """Send a token-authenticated request to the recorded service listener.

    :param record: Verified current master identity and launch-time listener.
    :param token: Current start's secret token, never placed in URLs or logs.
    :raises RuntimeError: If the listener rejects the shutdown request.
    :raises OSError: If the local listener cannot be reached.
    :raises http.client.HTTPException: If the response is invalid.
    """
    connection = http.client.HTTPConnection("127.0.0.1", int(str(record["port"])), timeout=5)
    try:
        connection.request("POST", "/_lcl/shutdown", headers={"X-LCL-Control-Token": token})
        response = connection.getresponse()
        response.read()
        if response.status != 202:
            raise RuntimeError(f"shutdown endpoint returned HTTP {response.status}, expected 202")
    finally:
        connection.close()


async def stop(config_path: Path) -> None:
    """Request graceful shutdown and wait for the exact master to exit.

    :param config_path: Configuration used only to locate current service state.
    :raises RuntimeError: If no verified service exists or shutdown is rejected.
    :raises TimeoutError: If the master outlives its configured graceful deadline.
    :raises OSError: If the token or local listener cannot be accessed.
    """
    settings = await load_settings(config_path)
    record = await asyncio.to_thread(read_state, settings.state_dir / "runtime.json")
    if not is_live(record):
        raise RuntimeError("no verified running service")
    token = await asyncio.to_thread(
        (settings.state_dir / "control.token").read_text,
        encoding="ascii",
    )
    await asyncio.to_thread(send_shutdown, record, token)
    deadline = time.monotonic() + float(str(record["graceful_timeout_seconds"]))
    while is_live(record):
        if time.monotonic() >= deadline:
            raise TimeoutError("service did not exit before its graceful shutdown deadline")
        await asyncio.sleep(0.05)
