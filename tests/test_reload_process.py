"""Exercise exact watch roots and graceful native replacement on each platform."""

import asyncio
import os
import subprocess
import sys
import sysconfig
import time
from pathlib import Path

import httpx
import psutil
import pytest
from coverage import Coverage
from test_runtime_process import available_port

from lcl_fastapi.runtime.common import stop
from lcl_fastapi.runtime.state import read_state

APP = """import os
from contextlib import asynccontextmanager
from pathlib import Path
from lcl_fastapi import LclFastAPI
from code import value

@asynccontextmanager
async def lifespan(app):
    yield
    Path("run").joinpath(f"{os.getpid()}.finished").write_text("closed")

app = LclFastAPI(lifespan=lifespan)

@app.get("/value")
async def get_value():
    return {"value": value.VALUE, "pid": os.getpid()}
"""


def response(port: int, process: subprocess.Popen[bytes], previous: int = 0) -> dict[str, int]:
    deadline = time.monotonic() + 25
    with httpx.Client(timeout=1) as client:
        while time.monotonic() < deadline:
            assert process.poll() is None, f"service exited with {process.returncode}"
            try:
                result = client.get(f"http://127.0.0.1:{port}/value")
                result.raise_for_status()
                data: dict[str, int] = result.json()
                if data["pid"] != previous:
                    return data
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
    raise AssertionError("replacement worker never served HTTP")


@pytest.mark.integration
@pytest.mark.parametrize("failure", [None, "import", "lifespan"])
def test_reload_roots_replacement_and_failure(tmp_path: Path, failure: str | None) -> None:
    port = available_port()
    code = tmp_path / "code"
    code.mkdir()
    (code / "__init__.py").write_text("", encoding="utf-8")
    value = code / "value.py"
    value.write_text("VALUE = 1\n", encoding="utf-8")
    shared = tmp_path / "shared"
    shared.mkdir()
    nested = shared / "nested"
    nested.mkdir()
    application = tmp_path / "app.py"
    application.write_text(APP, encoding="utf-8")
    source = tmp_path / "service.lclcfg"
    source.write_text(
        '__LCL_VERSION__: 1\napp.name: "reload"\napp.version: "1"\n'
        'app.target: "app:app"\nserver.workers: 2\n'
        f'server.port: {port}\nserver.reload_dirs: ["code", "shared"]\n'
        "server.graceful_timeout_seconds: 15\nhealth.sample_interval_seconds: 0.1\n",
        encoding="utf-8",
    )
    entrypoint = Path(sysconfig.get_path("scripts")) / (
        "lcl-fastapi.exe" if sys.platform == "win32" else "lcl-fastapi"
    )
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    if (coverage := Coverage.current()) is not None:
        environment["COVERAGE_FILE"] = str(Path(coverage.config.data_file).resolve())
    output = tmp_path / "output.txt"
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NO_WINDOW
    with output.open("wb") as stream:
        process = subprocess.Popen(
            [str(entrypoint), "serve", "-o", "config", str(source), "-o", "hot_reload"],
            cwd=tmp_path,
            env=environment,
            stdout=stream,
            stderr=subprocess.STDOUT,
            creationflags=creationflags,
        )
        try:
            initial = response(port, process)
            time.sleep(2)  # Allow the native manager's first bounded watch iteration.
            master = read_state(tmp_path / "run/runtime.json")
            token = (tmp_path / "run/control.token").read_bytes()
            assert master["configured_workers"] == 1
            with httpx.Client() as client:
                health = client.get(f"http://127.0.0.1:{port}/health").json()["service"]
                assert health["configured_workers"] == health["running_workers"] == 1
            if failure:
                if failure == "import":
                    value.write_text(
                        'raise RuntimeError("reload import failed")\n', encoding="utf-8"
                    )
                else:
                    application.write_text(
                        APP.replace(
                            "    yield",
                            '    raise RuntimeError("reload lifespan failed")\n    yield',
                        ),
                        encoding="utf-8",
                    )
                    value.write_text("VALUE = 222\n", encoding="utf-8")
                assert process.wait(timeout=25) != 0
                assert f"reload {failure} failed" in output.read_text(encoding="utf-8")
            else:
                (tmp_path / "outside.py").write_text("VALUE = 7\n", encoding="utf-8")
                source.write_text(source.read_text() + 'app.extra: "changed"\n', encoding="utf-8")
                (shared / "ignored.txt").write_text("changed", encoding="utf-8")
                time.sleep(2)
                assert response(port, process) == initial
                value.write_text("VALUE = 222\n", encoding="utf-8")
                current = response(port, process, initial["pid"])
                assert current["value"] == 222
                for action in ["add", "modify", "delete"]:
                    trigger = nested / "trigger.py"
                    if action == "delete":
                        trigger.unlink()
                    else:
                        trigger.write_text(f"# {action}\n", encoding="utf-8")
                    previous = current["pid"]
                    current = response(port, process, previous)
                    assert (tmp_path / f"run/{previous}.finished").read_text() == "closed"
                    assert not (tmp_path / f"run/workers/{previous}.json").exists()
                assert read_state(tmp_path / "run/runtime.json") == master
                assert (tmp_path / "run/control.token").read_bytes() == token
                value.write_text("VALUE = 3333\n", encoding="utf-8")
                asyncio.run(stop(source))
                assert process.wait(timeout=20) == 0
            assert not (tmp_path / "run/runtime.json").exists()
            assert not (tmp_path / "run/control.token").exists()
            assert not (tmp_path / "run/reload.json").exists()
            assert not list((tmp_path / "run/workers").glob("*.json"))
            assert not list((tmp_path / "run/leases").glob("*.json"))
            assert output.read_text(encoding="utf-8").count("has been forced to 1") == 1
        except BaseException:
            print(output.read_text(encoding="utf-8", errors="replace"))
            raise
        finally:
            if process.poll() is None:
                children = psutil.Process(process.pid).children(recursive=True)
                for child in reversed(children):
                    try:
                        child.kill()
                    except psutil.NoSuchProcess:
                        pass
                process.kill()
                process.wait(timeout=10)
