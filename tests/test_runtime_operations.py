"""Verify CLI-facing observations, shutdown errors, and worker import boundaries."""

import asyncio
import http.client
import importlib
import os
import signal
import sys
import threading
import time
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from uvicorn import Config, Server

from lcl_fastapi.config import Settings, load_settings
from lcl_fastapi.runtime import common
from lcl_fastapi.runtime.application import load_application
from lcl_fastapi.runtime.common import active_logs, inspect_status, send_shutdown, stop
from lcl_fastapi.runtime.service import service_runtime, shutdown_requested
from lcl_fastapi.runtime.state import atomic_write, read_state
from lcl_fastapi.runtime.windows import ServiceMultiprocess
from lcl_fastapi.runtime.worker import uvicorn_server, watch_worker_shutdown, worker_runtime

CONFIG = """__LCL_VERSION__: 1
app.name: "operations-test"
app.version: "1"
app.target: "runtime_target:app"
"""


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "service.lclcfg"
    path.write_text(CONFIG, encoding="utf-8")
    return path


def test_status_distinguishes_absent_stale_and_invalid_directory(source: Path) -> None:
    settings = asyncio.run(load_settings(source))
    assert asyncio.run(inspect_status(source))["status"] == "STOPPED"
    assert asyncio.run(active_logs(source)) == {"paths": [], "observed_at": None, "stale": False}
    atomic_write(settings.state_dir / "runtime.json", {"pid": -1})
    assert asyncio.run(inspect_status(source))["status"] == "STALE"
    with service_runtime(settings) as service:
        atomic_write(settings.state_dir / "runtime.json", service | {"worker_state_dir": None})
        assert asyncio.run(inspect_status(source))["status"] == "STALE"


def test_live_log_observations_expose_age_and_deduplicate(
    source: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = asyncio.run(load_settings(source))
    monkeypatch.delenv("LCL_FASTAPI_STATE", raising=False)
    with service_runtime(settings) as service:
        service["runtime"] = "gunicorn"
        atomic_write(settings.state_dir / "runtime.json", service)
        with worker_runtime(settings) as worker:
            path = str(source.parent / "active.log")
            worker.publish([path, path], time.time())
            view = asyncio.run(active_logs(source))
            assert view["paths"] == [path]
            assert view["stale"] is False
            worker.publish([path], 1)
            assert asyncio.run(active_logs(source))["stale"] is True
            state_path = settings.worker_state_dir / f"{worker.identity['pid']}.json"
            record = read_state(state_path)
            atomic_write(state_path, record | {"observed_at": None, "log_files": ["relative", 3]})
            assert asyncio.run(active_logs(source)) == {
                "paths": [],
                "observed_at": None,
                "stale": True,
            }
            atomic_write(state_path, record | {"service_id": "replacement-start"})
        assert read_state(state_path)["service_id"] == "replacement-start"


def test_shutdown_uses_verified_master_and_current_start_marker(
    source: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = asyncio.run(load_settings(source))
    monkeypatch.delenv("LCL_FASTAPI_STATE", raising=False)
    signals: list[tuple[int, int]] = []
    module = importlib.import_module("lcl_fastapi.runtime.worker")
    monkeypatch.setattr(module.os, "kill", lambda pid, signum: signals.append((pid, signum)))
    with service_runtime(settings) as service:
        service["runtime"] = "gunicorn"
        atomic_write(settings.state_dir / "runtime.json", service)
        with worker_runtime(settings) as worker:
            worker.request_shutdown()
            assert signals == [(worker.service["pid"], signal.SIGTERM)]
            service["runtime"] = "uvicorn"
            worker.service["runtime"] = "uvicorn"
            atomic_write(settings.state_dir / "runtime.json", service)
            assert not shutdown_requested(settings.state_dir, service)
            worker.request_shutdown()
            assert shutdown_requested(settings.state_dir, service)


class Connection:
    def __init__(self, status: int) -> None:
        self.status = status
        self.closed = False
        self.request_value: tuple[str, str, dict[str, str]] | None = None

    def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
        self.request_value = (method, path, headers)

    def getresponse(self) -> Connection:
        return self

    def read(self) -> bytes:
        return b""

    def close(self) -> None:
        self.closed = True


@pytest.mark.parametrize("status", [202, 403])
def test_shutdown_http_protocol_and_rejection(status: int, monkeypatch: pytest.MonkeyPatch) -> None:
    connection = Connection(status)
    arguments: list[tuple[str, int, int]] = []

    def connect(host: str, port: int, *, timeout: int) -> Connection:
        arguments.append((host, port, timeout))
        return connection

    monkeypatch.setattr(http.client, "HTTPConnection", connect)
    if status == 202:
        send_shutdown({"port": 8123}, "token")
    else:
        with pytest.raises(RuntimeError, match="HTTP 403"):
            send_shutdown({"port": 8123}, "token")
    assert arguments == [("127.0.0.1", 8123, 5)]
    assert connection.request_value == ("POST", "/_lcl/shutdown", {"X-LCL-Control-Token": "token"})
    assert connection.closed


def test_stop_refuses_unknown_process_and_times_out_without_force(
    source: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(RuntimeError, match="no verified"):
        asyncio.run(stop(source))
    settings = asyncio.run(load_settings(source))
    with service_runtime(settings):
        monkeypatch.setattr(common, "send_shutdown", lambda record, token: None)
        clock = iter([0.0, 100.0])
        monkeypatch.setattr(common, "time", SimpleNamespace(monotonic=lambda: next(clock)))
        with pytest.raises(TimeoutError, match="graceful shutdown deadline"):
            asyncio.run(stop(source))


async def test_serve_rejects_nested_event_loop(source: Path) -> None:
    with pytest.raises(RuntimeError, match="event loop has closed"):
        common.serve(source)


def test_serve_restores_internal_environment_on_unsupported_platform(
    source: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(common, "sys", SimpleNamespace(platform="unsupported"))
    monkeypatch.setenv("LCL_FASTAPI_CONFIG", "previous-config")
    monkeypatch.setenv("LCL_FASTAPI_STATE", "previous-state")
    with pytest.raises(RuntimeError, match="Windows and Linux only"):
        common.serve(source)
    assert os.environ["LCL_FASTAPI_CONFIG"] == "previous-config"
    assert os.environ["LCL_FASTAPI_STATE"] == "previous-state"


def test_serve_dispatches_native_linux_adapter(
    source: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("lcl_fastapi.runtime.linux")
    calls: list[Settings] = []
    monkeypatch.setattr(common, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(module, "run_linux", lambda settings, identity: calls.append(settings))
    common.serve(source)
    assert len(calls) == 1 and calls[0].app_target == "runtime_target:app"


def test_native_server_adapter_rejects_unrelated_signal_handlers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(signal, "getsignal", lambda signum: signal.SIG_DFL)
    with pytest.raises(RuntimeError, match="expected Uvicorn"):
        uvicorn_server()
    server = Server(Config("unused:app"))
    monkeypatch.setattr(signal, "getsignal", lambda signum: server.handle_exit)
    assert uvicorn_server() is server


def test_shutdown_bridges_cancel_without_stopping_the_server(
    source: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = asyncio.run(load_settings(source))
    monkeypatch.delenv("LCL_FASTAPI_STATE", raising=False)
    finished = threading.Event()
    finished.set()
    with service_runtime(settings) as service:
        service["runtime"] = "gunicorn"
        atomic_write(settings.state_dir / "runtime.json", service)
        with worker_runtime(settings) as worker:
            server = Server(Config("unused:app"))
            watch_worker_shutdown(worker, finished, server)
            assert not server.should_exit
        monkeypatch.setattr(signal, "signal", lambda signum, handler: signal.SIG_DFL)
        manager = ServiceMultiprocess(Config("unused:app"), sockets=[])
        manager.watch_shutdown(finished)
        assert not manager.should_exit.is_set()


def test_service_cleanup_does_not_remove_a_live_lease(source: Path) -> None:
    from lcl_fastapi.runtime.lease import WorkerIdLease

    settings = asyncio.run(load_settings(source))
    with service_runtime(settings):
        lease = WorkerIdLease.acquire(
            state_dir=settings.state_dir,
            worker_id_base=0,
            worker_id_count=1,
        )
    assert lease.path.exists()
    lease.release()


def test_worker_import_reads_configuration_and_validates_target(
    source: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    target = ModuleType("runtime_target")
    target.__dict__["app"] = object()
    monkeypatch.setitem(sys.modules, "runtime_target", target)
    monkeypatch.setenv("LCL_FASTAPI_CONFIG", str(source))
    monkeypatch.setattr(sys, "path", list(sys.path))
    with pytest.raises(TypeError, match="ASGI application"):
        load_application()
    monkeypatch.setattr(target, "app", lambda scope, receive, send: None)
    assert callable(load_application())
    source.write_text(CONFIG.replace("runtime_target:app", "invalid"), encoding="utf-8")
    with pytest.raises(ValueError, match="module:attribute"):
        load_application()


@pytest.mark.parametrize("error", [KeyboardInterrupt(), SystemExit(42)])
def test_windows_factory_preserves_process_control_exceptions(
    error: BaseException,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = importlib.import_module("lcl_fastapi.runtime.windows")

    def interrupted() -> None:
        raise error

    monkeypatch.setattr(module, "load_application", interrupted)
    with pytest.raises(type(error)) as caught:
        module.load_windows_application()
    assert caught.value is error
