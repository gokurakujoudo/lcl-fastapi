"""Exercise native multiprocess startup, replacement, and graceful shutdown."""

import asyncio
import http.client
import os
import shutil
import signal
import socket
import subprocess
import sys
import sysconfig
import time
from contextlib import suppress
from pathlib import Path

import httpx
import psutil
import pytest
from coverage import Coverage

from lcl_fastapi.runtime.common import active_logs, inspect_status, stop
from lcl_fastapi.runtime.state import file_lock, live_workers, read_state

CONFIG = """__LCL_VERSION__: 1
app.name: "runtime-probe"
app.version: "1.0.0"
app.target: "runtime_app:app"
app.fail_startup: False
server.port: {port}
server.workers: 2
server.graceful_timeout_seconds: 15
health.sample_interval_seconds: 0.1
docs.enabled: True
"""


def available_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return int(listener.getsockname()[1])


async def wait_ready(port: int, process: subprocess.Popen[bytes]) -> dict[str, object]:
    deadline = time.monotonic() + 20
    async with httpx.AsyncClient() as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(f"service exited early with {process.returncode}")
            try:
                response = await client.get(f"http://127.0.0.1:{port}/health", timeout=1)
                value: dict[str, object] = response.json()["service"]
                if value.get("running_workers") == 2:
                    return value
            except httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
    raise AssertionError("workers did not become ready")


def verify_idle_observations(directory: Path) -> None:
    started = time.time()
    deadline = time.monotonic() + 5
    service_id = read_state(directory / "run/runtime.json")["service_id"]
    records: list[dict[str, object]] = []
    while time.monotonic() < deadline:
        try:
            records = live_workers(directory / "run/workers", service_id)
        except PermissionError:
            # Windows can briefly deny reads while a worker atomically replaces its state.
            time.sleep(0.05)
            continue
        if len(records) == 2 and all(
            float(str(record.get("observed_at", 0))) > started for record in records
        ):
            return
        time.sleep(0.05)
    raise AssertionError(f"idle workers stopped publishing without HTTP traffic: {records}")


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform not in {"win32", "linux"},
    reason="native supported platforms only",
)
def test_native_workers_recover_and_finish_lifespans(tmp_path: Path) -> None:
    port = available_port()
    source = tmp_path / "service.lclcfg"
    source.write_text(CONFIG.format(port=port), encoding="utf-8")
    root = Path(__file__).resolve().parent.parent
    shutil.copyfile(root / "tests/runtime_app.py", tmp_path / "runtime_app.py")
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    if (coverage := Coverage.current()) is not None:
        environment["COVERAGE_FILE"] = str(Path(coverage.config.data_file).resolve())
    external = tmp_path / "different-working-directory"
    external.mkdir()
    entrypoint = Path(sysconfig.get_path("scripts")) / (
        "lcl-fastapi.exe" if sys.platform == "win32" else "lcl-fastapi"
    )
    output = tmp_path / "process-output.txt"
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW
    with output.open("wb") as stream:
        process = subprocess.Popen(
            [str(entrypoint), "serve", "-o", "config", str(source)],
            stdout=stream,
            stderr=subprocess.STDOUT,
            env=environment,
            creationflags=creationflags,
            cwd=external,
        )
        try:
            health = asyncio.run(wait_ready(port, process))
            launched_pids = {
                process.pid,
                *(child.pid for child in psutil.Process(process.pid).children(recursive=True)),
            }
            assert health["service_pid"] in launched_pids
            expected_master = health["service_pid"] if sys.platform == "linux" else None
            assert health["gunicorn_pid"] == expected_master
            status = asyncio.run(inspect_status(source))
            assert status["status"] == "RUNNING"
            initial = health["worker_pids"]
            assert isinstance(initial, list) and len(initial) == 2
            assert health["service_pid"] not in initial
            paths = (asyncio.run(active_logs(source)))["paths"]
            assert isinstance(paths, list) and len(paths) == 2
            with httpx.Client() as client:
                docs = client.get(f"http://127.0.0.1:{port}/docs")
                assert docs.status_code == 200
                assert "cdn" not in docs.text
                schema = client.get(f"http://127.0.0.1:{port}/openapi.json")
                assert schema.status_code == 200
                assert "/_lcl/shutdown" not in schema.json()["paths"]
                response = client.get(
                    f"http://127.0.0.1:{port}/hello",
                    headers={"X-Request-ID": "client-value"},
                )
                assert response.status_code == 200
                assert response.headers["X-Request-ID"] == response.json()["request_id"]
                assert response.headers["X-Request-ID"] != "client-value"
                denied = client.post(f"http://127.0.0.1:{port}/_lcl/shutdown")
                assert denied.status_code == 403
                denied = client.post(
                    f"http://127.0.0.1:{port}/_lcl/shutdown",
                    headers={"X-LCL-Control-Token": "wrong"},
                )
                assert denied.status_code == 403
            verify_idle_observations(tmp_path)
            psutil.Process(initial[0]).kill()
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                health = asyncio.run(wait_ready(port, process))
                replacement_pids = health["worker_pids"]
                assert isinstance(replacement_pids, list)
                if initial[0] not in replacement_pids:
                    break
                time.sleep(0.1)
            else:
                raise AssertionError("native worker manager did not replace a killed worker")
            retired: list[int] = []
            if sys.platform == "linux":
                master = read_state(tmp_path / "run/runtime.json")
                token = (tmp_path / "run/control.token").read_bytes()
                assert isinstance(replacement_pids[0], int)
                retired.append(replacement_pids[0])
                psutil.Process(retired[0]).send_signal(signal.SIGTERM)
                deadline = time.monotonic() + 20
                while time.monotonic() < deadline:
                    health = asyncio.run(wait_ready(port, process))
                    replacement_pids = health["worker_pids"]
                    assert isinstance(replacement_pids, list)
                    if retired[0] not in replacement_pids:
                        break
                    time.sleep(0.1)
                else:
                    raise AssertionError("native worker manager did not replace a retired worker")
                assert (
                    tmp_path / "run" / f"{retired[0]}.finished"
                ).read_text() == "lifespan closed"
                assert read_state(tmp_path / "run/runtime.json") == master
                assert (tmp_path / "run/control.token").read_bytes() == token
                with (
                    pytest.raises(OSError),
                    file_lock(tmp_path / "run/service.lock", blocking=False),
                ):
                    raise AssertionError("retiring a worker released the master's service lock")
                assert (asyncio.run(inspect_status(source)))["status"] == "RUNNING"
                paths = (asyncio.run(active_logs(source)))["paths"]
                assert isinstance(paths, list) and len(paths) == 2
            current = health["worker_pids"]
            assert isinstance(current, list)
            with httpx.Client() as client:
                response = client.get(f"http://127.0.0.1:{port}/hello")
                assert response.status_code == 200
            verify_idle_observations(tmp_path)
            # A later port edit must not redirect stop to another listener.
            source.write_text(CONFIG.format(port=available_port()), encoding="utf-8")
            asyncio.run(stop(source))
            assert process.wait(timeout=5) == 0
            for pid in current:
                assert (tmp_path / "run" / f"{pid}.finished").read_text() == "lifespan closed"
            logs = "\n".join(
                path.read_text(encoding="utf-8") for path in (tmp_path / "logs").glob("*")
            )
            assert "hello request" in logs
            assert logs.count("business lifespan closed") == 2 + len(retired)
            assert "uvicorn.access" not in logs
            assert "gunicorn.access" not in logs
            assert not (tmp_path / "run/control.token").exists()
            assert not list((tmp_path / "run/workers").glob("*.json"))
            assert not list((tmp_path / "run/leases").glob("*.json"))
            assert (asyncio.run(inspect_status(source)))["status"] == "STOPPED"
        except BaseException:
            print(output.read_text(encoding="utf-8", errors="replace"))
            raise
        finally:
            if process.poll() is None:
                with suppress(RuntimeError, TimeoutError, OSError, http.client.HTTPException):
                    asyncio.run(stop(source))
            if process.poll() is None:
                children = psutil.Process(process.pid).children(recursive=True)
                for child in children:
                    child.kill()
                process.kill()
                process.wait(timeout=5)


@pytest.mark.integration
@pytest.mark.skipif(
    sys.platform not in {"win32", "linux"},
    reason="native supported platforms only",
)
@pytest.mark.parametrize(
    "source_text,diagnostic",
    [
        pytest.param(
            CONFIG.replace("app.fail_startup: False", "app.fail_startup: True"),
            "intentional business startup failure",
            id="lifespan",
        ),
        pytest.param(
            CONFIG.replace("runtime_app:app", "lcl_review_nonexistent_target:service"),
            "lcl_review_nonexistent_target",
            id="module",
        ),
        pytest.param(
            CONFIG.replace("runtime_app:app", "runtime_app:missing_application"),
            "missing_application",
            id="attribute",
        ),
        pytest.param(
            CONFIG.replace(
                "server.workers: 2",
                'server.workers: "invalid" if env.get("LCL_FASTAPI_STATE", "") else 2',
            ),
            "server.workers: expected an integer",
            id="worker-configuration",
        ),
    ],
)
def test_worker_startup_failure_returns_failure_and_cleans_state(
    tmp_path: Path,
    source_text: str,
    diagnostic: str,
) -> None:
    port = available_port()
    source = tmp_path / "service.lclcfg"
    source.write_text(
        source_text.format(port=port),
        encoding="utf-8",
    )
    root = Path(__file__).resolve().parent.parent
    shutil.copyfile(root / "tests/runtime_app.py", tmp_path / "runtime_app.py")
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    if (coverage := Coverage.current()) is not None:
        environment["COVERAGE_FILE"] = str(Path(coverage.config.data_file).resolve())
    entrypoint = Path(sysconfig.get_path("scripts")) / (
        "lcl-fastapi.exe" if sys.platform == "win32" else "lcl-fastapi"
    )
    output = tmp_path / "process-output.txt"
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW
    with output.open("wb") as stream:
        process = subprocess.Popen(
            [str(entrypoint), "serve", "-o", "config", str(source)],
            stdout=stream,
            stderr=subprocess.STDOUT,
            env=environment,
            creationflags=creationflags,
            cwd=tmp_path,
        )
        try:
            assert process.wait(timeout=20) != 0
            assert diagnostic in output.read_text(encoding="utf-8")
            assert not (tmp_path / "run/control.token").exists()
            assert not (tmp_path / "run/runtime.json").exists()
            assert not (tmp_path / "run/runtime-probe.pid").exists()
            assert not list((tmp_path / "run/workers").glob("*.json"))
            assert not list((tmp_path / "run/leases").glob("*.json"))
            with socket.socket() as probe:
                assert probe.connect_ex(("127.0.0.1", port)) != 0
        except BaseException:
            print(output.read_text(encoding="utf-8", errors="replace"))
            raise
        finally:
            if process.poll() is None:
                for child in psutil.Process(process.pid).children(recursive=True):
                    child.kill()
                process.kill()
                process.wait(timeout=5)
