"""Exercise the installed downstream package and real platform runtime."""

import configparser
import importlib.metadata
import json
import os
import shutil
import socket
import subprocess
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

import psutil

PORT = 18082
ORIGIN = f"http://127.0.0.1:{PORT}"
HERE = Path(__file__).resolve().parent
ENVIRONMENT = HERE / ".venv"
CLI = ENVIRONMENT / ("Scripts/lcl-fastapi.exe" if os.name == "nt" else "bin/lcl-fastapi")


class Assets(HTMLParser):
    """Collect Swagger resource references from its served HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        value = values.get("src" if tag == "script" else "href")
        if tag in {"script", "link"} and value:
            self.urls.append(value)


def cli(config: Path, *arguments: str) -> str:
    result = subprocess.run(
        [str(CLI), *arguments, "-o", "config", str(config)],
        cwd=config.parent.parent,
        capture_output=True,
        text=True,
        timeout=45,
        check=False,
    )
    assert result.returncode == 0, (arguments, result.returncode, result.stdout, result.stderr)
    return result.stdout


def snapshot(config: Path, command: str) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(cli(config, command))
    return value


def http(path: str, method: str = "GET", **headers: str) -> tuple[int, Any, bytes]:
    request = Request(ORIGIN + path, method=method, headers=headers)
    try:
        response = urlopen(request, timeout=4)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read()


def renderers(config: Path) -> None:
    nginx = cli(config, "nginx", "render")
    normalized = " ".join(nginx.split())
    for directive in (
        "listen 9443 ssl;",
        "server_name catalog.example.com;",
        "/etc/catalog/tls/server.crt",
        "/etc/catalog/tls/server.key",
        "location = /_lcl/shutdown { return 404; }",
        "location /",
        f"proxy_pass http://127.0.0.1:{PORT};",
        "proxy_set_header Host $host;",
        "proxy_set_header X-Real-IP $remote_addr;",
        "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;",
        "proxy_set_header X-Forwarded-Proto $scheme;",
    ):
        assert directive in normalized, (directive, nginx)
    assert nginx.count("{") == nginx.count("}")
    assert "8443" not in nginx and "rewrite" not in nginx
    unit = cli(config, "systemd", "render")
    parsed = configparser.ConfigParser(interpolation=None)
    parsed.read_string(unit)
    assert parsed.sections() == ["Unit", "Service", "Install"]
    for section, key, value in (
        ("Unit", "Description", "Composed Catalog Example"),
        ("Service", "Type", "simple"),
        ("Service", "User", "catalog"),
        ("Service", "Group", "catalog"),
        ("Service", "WorkingDirectory", "/opt/composed-catalog"),
        ("Service", "Restart", "on-failure"),
        ("Service", "RestartSec", "5"),
        ("Service", "KillMode", "mixed"),
        ("Install", "WantedBy", "multi-user.target"),
    ):
        assert parsed[section][key] == value, (section, key, unit)
    assert parsed["Service"]["ExecStart"] == (
        '"/opt/composed-catalog/.venv/bin/lcl-fastapi" '
        'serve -o config "/etc/composed-catalog/service.lclcfg"'
    )
    for group, content, filename in (
        ("nginx", nginx, "catalog proxy.conf"),
        ("systemd", unit, "catalog unit.service"),
    ):
        output = config.parent.parent / filename
        assert cli(config, group, "render", "-o", "output", filename) == ""
        assert output.read_text(encoding="utf-8") == content


def ready(config: Path, process: subprocess.Popen[str]) -> dict[str, Any]:
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline:
        assert process.poll() is None, "Service exited before becoming ready"
        state = snapshot(config, "status")
        if state["status"] == "RUNNING" and len(state["workers"]) == 2:
            try:
                if http("/health")[0] == 200:
                    return state
            except URLError, TimeoutError, ConnectionError:
                pass
        time.sleep(0.2)
    raise AssertionError("Two workers did not become ready within 40 seconds")


def catalog_responses() -> list[tuple[int, Any, bytes]]:
    """Exercise both shared-listener workers without assuming OS fairness."""
    first = http("/api/v1/catalog", **{"X-Request-ID": "client-owned-id"})
    # Windows AcceptEx can favor the most recent accept queue indefinitely.
    # Pause the observed worker briefly and exceed libuv's 32 pending accepts;
    # its sibling must answer before we resume it and validate every response.
    with ThreadPoolExecutor(max_workers=64) as clients:
        active = psutil.Process(json.loads(first[2])["pid"])
        paused = os.name == "nt"
        if paused:
            active.suspend()
        try:
            requests = [
                clients.submit(http, "/api/v1/catalog", **{"X-Request-ID": "client-owned-id"})
                for _ in range(64)
            ]
            completed, _ = wait(requests, timeout=3, return_when=FIRST_COMPLETED)
            assert completed, "A sibling worker must serve while the observed worker is paused"
            if paused:
                for request in completed:
                    assert json.loads(request.result()[2])["pid"] != active.pid
        finally:
            if paused:
                active.resume()
        return [first, *(request.result() for request in requests)]


def check_http(config: Path, state: dict[str, Any]) -> tuple[set[str], list[Path]]:
    runtime = "uvicorn" if os.name == "nt" else "gunicorn"
    service = state["service"]
    worker_pids = {worker["pid"] for worker in state["workers"]}
    assert service["name"] == "composed-catalog" and service["version"] == "1.0.0"
    assert service["runtime"] == runtime and service["configured_workers"] == 2
    assert psutil.Process(service["pid"]).create_time() == service["process_create_time"]
    assert worker_pids <= {p.pid for p in psutil.Process(service["pid"]).children(recursive=True)}
    assert len({worker["snowflake_worker_id"] for worker in state["workers"]}) == 2
    assert all(worker["service_id"] == service["service_id"] for worker in state["workers"])
    status, _, body = http("/health")
    health = json.loads(body)
    assert status == 200 and health["status"] == "UP"
    assert health["service"]["service_pid"] == service["pid"]
    assert health["service"]["gunicorn_pid"] == (None if os.name == "nt" else service["pid"])
    assert health["server"]
    assert "control" not in json.dumps(health).lower()
    assert "RUNNING" in cli(config, "status")
    observed: set[int] = set()
    request_ids: set[str] = set()
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and (observed != worker_pids or len(request_ids) < 8):
        for status, headers, body in catalog_responses():
            catalog = json.loads(body)
            assert status == 200 and catalog["items"] == ["notebook", "pencil"]
            assert catalog["greeting"] == "Welcome, Reader"
            assert catalog["public_origin"] == "https://catalog.example.com:8443"
            assert catalog["asgi_root_path"] == ""
            request_id = headers["X-Request-ID"]
            assert request_id == catalog["request_id"] and request_id.isdecimal()
            assert request_id not in request_ids and request_id != "client-owned-id"
            request_ids.add(request_id)
            observed.add(catalog["pid"])
    assert observed == worker_pids, "Both initialized worker catalogs must be observable"
    assert json.loads(http("/about")[2]) == {"application": "composed-catalog"}
    assert http("/catalog")[0] == 404 and http("/api/v1/about")[0] == 404
    status, _, body = http("/docs")
    assert status == 200 and b"validatorUrl" in body and b"null" in body
    resources = Assets()
    resources.feed(body.decode())
    assert len(resources.urls) >= 3
    for url in resources.urls:
        parsed = urlsplit(url)
        assert not parsed.scheme and not parsed.netloc and url.startswith("/"), url
        asset_status, _, content = http(url)
        assert asset_status == 200 and content, url
    schema = json.loads(http("/openapi.json")[2])
    assert "/api/v1/catalog" in schema["paths"] and "/about" in schema["paths"]
    assert "/_lcl/shutdown" not in schema["paths"]
    for headers in ({}, {"X-LCL-Control-Token": "deliberately-wrong-token"}):
        assert http("/_lcl/shutdown", "POST", **headers)[0] == 403
    deadline = time.monotonic() + 10
    while True:
        logs = snapshot(config, "logs")
        if len(logs["paths"]) == 2 and logs["stale"] is False:
            break
        if time.monotonic() >= deadline:
            raise AssertionError(f"Live log observations did not become fresh: {logs}")
        time.sleep(0.1)
    assert set(logs) == {"paths", "observed_at", "stale"}
    assert isinstance(logs["observed_at"], (int, float)) and logs["stale"] is False
    paths = [Path(path) for path in logs["paths"]]
    assert len(paths) == 2
    assert all(
        path.is_absolute() and path.resolve().is_relative_to((config.parent / "logs").resolve())
        for path in paths
    )
    assert set(cli(config, "logs").splitlines()) == set(logs["paths"])
    assert not (config.parent.parent / "logs").exists()
    return request_ids, paths


def main() -> None:
    assert sys.version_info >= (3, 14)
    assert Path(sys.prefix).resolve() == ENVIRONMENT.resolve(), "Run using this example's .venv"
    installed = importlib.metadata.distribution("lcl-fastapi")
    assert Path(str(installed.locate_file("lcl_fastapi"))).resolve().is_relative_to(ENVIRONMENT)
    assert importlib.metadata.version("lcl-fastapi-composed-example") == "1.0.0"
    assert CLI.is_file()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", PORT))
    with TemporaryDirectory(prefix="lcl-composed-") as directory:
        workspace = Path(directory)
        config_directory = workspace / "config with spaces"
        config_directory.mkdir()
        config = config_directory / "service.lclcfg"
        shutil.copyfile(HERE / "service.lclcfg", config)
        renderers(config)
        assert snapshot(config, "status") == {"status": "STOPPED", "service": None, "workers": []}
        assert snapshot(config, "logs") == {"paths": [], "observed_at": None, "stale": False}
        console = workspace / "console.txt"
        with console.open("w", encoding="utf-8") as stream:
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW
            process = subprocess.Popen(
                [str(CLI), "serve", "-o", "config", str(config)],
                cwd=workspace,
                stdout=stream,
                stderr=subprocess.STDOUT,
                text=True,
                creationflags=creationflags,
            )
            try:
                state = ready(config, process)
                request_ids, paths = check_http(config, state)
                cli(config, "stop")
                assert process.wait(timeout=10) == 0, "Normal shutdown must exit successfully"
                assert snapshot(config, "status")["status"] == "STOPPED"
                assert snapshot(config, "logs")["paths"] == []
                run = config_directory / "run"
                assert not (run / "runtime.json").exists()
                assert not (run / "control.token").exists()
                assert not (run / "composed-catalog.pid").exists()
                assert not list((run / "workers").glob("*.json"))
                contents = "\n".join(path.read_text(encoding="utf-8") for path in paths)
                for worker in state["workers"]:
                    assert f"catalog startup pid={worker['pid']} items=2" in contents
                    assert f"catalog shutdown pid={worker['pid']} items=0" in contents
                assert all(request_id in contents for request_id in request_ids)
                assert "catalog read" in contents and "/api/v1/catalog" in contents
                print(
                    f"PASS composed: {state['service']['runtime']}, "
                    "two workers, HTTP/CLI/render/cleanup"
                )
            except BaseException:
                print(console.read_text(encoding="utf-8", errors="replace"), file=sys.stderr)
                raise
            finally:
                if process.poll() is None:
                    # This private configuration belongs only to the process launched above.
                    cli(config, "stop")
                    process.wait(timeout=35)


if __name__ == "__main__":
    main()
