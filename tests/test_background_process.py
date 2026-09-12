"""Validate background registration, forced single-worker and native retirement."""

import asyncio
import os
import subprocess
import sysconfig
import time
from pathlib import Path
from typing import cast

import httpx
import psutil
import pytest
from test_runtime_process import available_port

from lcl_fastapi.runtime.common import inspect_status

APP = """import asyncio
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from lcl_fastapi import LclFastAPI, get_config

@asynccontextmanager
async def lifespan(app):
    Path("started.txt").write_text(str(os.getpid()))
    yield
    Path("closed.txt").write_text(str(os.getpid()))

def worker(context):
    context.logger.info("background process probe")
    mode = context.run(context.get_config("background_worker.job.mode"))
    Path("worker-ready.txt").write_text(str(os.getpid()))
    if mode == "stuck":
        threading.Event().wait()
    elif mode == "fail":
        raise ValueError("background failure")
    else:
        context.stop_event.wait()

service = LclFastAPI(lifespan=lifespan, background_workers={"job": worker})

@service.get("/probe")
async def probe():
    return {"pid": os.getpid(), "value": await get_config("app.value")}
"""


def wait_ready(port: int, process: subprocess.Popen[bytes], previous: int = 0) -> int:
    deadline = time.monotonic() + 30
    with httpx.Client(timeout=1) as client:
        while time.monotonic() < deadline:
            assert process.poll() is None, "service exited during startup"
            try:
                response = client.get(f"http://127.0.0.1:{port}/probe")
                if response.status_code == 200 and response.json()["pid"] != previous:
                    return int(response.json()["pid"])
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
    raise AssertionError("API worker did not become ready")


@pytest.mark.integration
@pytest.mark.parametrize(
    "mode,reload", [("normal", False), ("stuck", False), ("normal", True), ("stuck", True)]
)
def test_background_native_lifecycle(tmp_path: Path, mode: str, reload: bool) -> None:
    port = available_port()
    application = tmp_path / "app.py"
    application.write_text(APP)
    source = tmp_path / "service.lclcfg"
    source.write_text(
        '__LCL_VERSION__: 1\napp.name: "background-probe"\napp.version: "1"\n'
        'app.target: "app:service"\napp.value: "ready"\nserver.workers: 3\n'
        f"server.port: {port}\nserver.graceful_timeout_seconds: 2\n"
        f'background_worker.job.mode: "{mode}"\nhealth.sample_interval_seconds: 0.05\n'
        "logger.console.enabled: False\n"
    )
    entry = Path(sysconfig.get_path("scripts")) / (
        "lcl-fastapi.exe" if os.name == "nt" else "lcl-fastapi"
    )
    output = tmp_path / "output.txt"
    arguments = [str(entry), "serve", "-o", "config", str(source)]
    if reload:
        arguments.append("-o")
        arguments.append("hot_reload")
    environment = os.environ.copy()
    environment["COVERAGE_FILE"] = str(Path(".coverage").resolve())
    with output.open("wb") as stream:
        process = subprocess.Popen(
            arguments, cwd=tmp_path, env=environment, stdout=stream, stderr=subprocess.STDOUT
        )
        try:
            pid = wait_ready(port, process)
            state = asyncio.run(inspect_status(source))
            assert cast(dict[str, object], state["service"])["configured_workers"] == 1
            with httpx.Client() as client:
                health = client.get(f"http://127.0.0.1:{port}/health").json()["service"]
                assert health["running_workers"] == 1
                assert "job" in health["background_workers"]
            if reload:
                for revision in range(2):
                    time.sleep(1)
                    application.write_text(APP + f"\n# trigger replacement {revision}\n")
                    replacement = wait_ready(port, process, pid)
                    assert replacement != pid
                    assert not psutil.pid_exists(pid)
                    pid = replacement
            token = (tmp_path / "run/control.token").read_text()
            with httpx.Client() as client:
                assert (
                    client.post(
                        f"http://127.0.0.1:{port}/_lcl/shutdown",
                        headers={"X-LCL-Control-Token": token},
                    ).status_code
                    == 202
                )
            assert process.wait(timeout=15) == 0
            control = "".join(p.read_text() for p in (tmp_path / "logs").glob("*controller*.log"))
            assert "background worker=job" in control
            assert "background stopping" in control
            assert ("background shutdown timeout" in control) == (mode == "stuck")
            assert (tmp_path / "closed.txt").exists() == (mode == "normal")
            assert not psutil.pid_exists(pid)
            assert not (tmp_path / "run/runtime.json").exists()
            assert not list((tmp_path / "run/background").glob("*.jsonl"))
            assert output.read_text().count("has been forced to 1") == 1
        except BaseException:
            print(output.read_text(errors="replace"))
            raise
        finally:
            if process.poll() is None:
                for child in reversed(psutil.Process(process.pid).children(recursive=True)):
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                process.kill()
                process.wait(timeout=10)
