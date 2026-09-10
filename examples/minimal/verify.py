"""Check the installed service through HTTP and the real console command."""

import json
import os
import shutil
import subprocess
import sys
import time
from contextlib import suppress
from html.parser import HTMLParser
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

import psutil


class DocumentationAssets(HTMLParser):
    """Collect browser-loaded script, stylesheet, and icon URLs."""

    def __init__(self) -> None:
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        fields = dict(attrs)
        value = fields.get("src") if tag == "script" else fields.get("href")
        if value and tag in {"script", "link"}:
            self.urls.append(value)


def check_http() -> None:
    """Check local documentation assets, schema, and shutdown authorization."""
    base = "http://127.0.0.1:18081"
    with urlopen(base + "/openapi.json", timeout=3) as response:
        schema = json.load(response)
    assert "/hello" in schema["paths"], schema
    assert "/_lcl/shutdown" not in schema["paths"], schema
    with urlopen(base + "/docs", timeout=3) as response:
        page = response.read().decode("utf-8")
    assets = DocumentationAssets()
    assets.feed(page)
    assert len(assets.urls) >= 3, assets.urls
    for asset in assets.urls:
        url = urljoin(base + "/docs", asset)
        assert urlsplit(url).netloc == urlsplit(base).netloc, url
        with urlopen(url, timeout=3) as response:
            assert response.status == 200 and response.read(), url
    for headers in ({}, {"X-LCL-Control-Token": "incorrect-example-token"}):
        request = Request(base + "/_lcl/shutdown", data=b"", headers=headers)
        try:
            with urlopen(request, timeout=3) as response:
                raise AssertionError(f"Unauthenticated shutdown returned {response.status}")
        except HTTPError as error:
            with error:
                assert error.code == 403, error.code


def main() -> None:
    """Run the installed application in an isolated directory and stop it."""
    executable = "lcl-fastapi.exe" if os.name == "nt" else "lcl-fastapi"
    cli = Path(sys.executable).parent / executable
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    with TemporaryDirectory(prefix="lcl-minimal-example-") as directory:
        working = Path(directory)
        config = working / "service.lclcfg"
        shutil.copyfile(Path(__file__).with_name("service.lclcfg"), config)

        def command(
            operation: str, *options: str, configuration: Path = config
        ) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [str(cli), *operation.split(), "-o", "config", str(configuration), *options],
                cwd=working,
                env=environment,
                capture_output=True,
                text=True,
                timeout=40,
                check=True,
            )

        render_config = working / "render.lclcfg"
        shutil.copyfile(Path(__file__).with_name("render.lclcfg"), render_config)
        for operation, expected in (
            ("nginx render", "proxy_pass http://127.0.0.1:18081;"),
            ("systemd render", "KillMode=mixed"),
        ):
            rendered = command(operation, configuration=render_config).stdout
            assert expected in rendered, rendered
            output = working / (operation.replace(" ", "-") + ".txt")
            result = command(operation, "-o", "output", str(output), configuration=render_config)
            assert not result.stdout, result.stdout
            assert output.read_text(encoding="utf-8") == rendered
        print("PASS: nginx/systemd render to stdout and files", flush=True)

        with (working / "console.log").open("w+", encoding="utf-8") as console:
            creationflags = 0
            if sys.platform == "win32":
                creationflags = subprocess.CREATE_NO_WINDOW
            process = subprocess.Popen(
                [str(cli), "serve", "-o", "config", str(config)],
                cwd=working,
                env=environment,
                stdout=console,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
            )
            stopped = False
            try:
                deadline = time.monotonic() + 20
                while True:
                    if process.poll() is not None:
                        console.seek(0)
                        raise RuntimeError(console.read())
                    try:
                        with urlopen("http://127.0.0.1:18081/health", timeout=1) as response:
                            health = json.load(response)
                        break
                    except URLError:
                        if time.monotonic() >= deadline:
                            raise TimeoutError("Service did not become healthy") from None
                        time.sleep(0.2)
                assert health["status"] == "UP", health
                assert health["service"] and health["server"], health
                ids = []
                for _ in range(2):
                    request = Request(
                        "http://127.0.0.1:18081/hello",
                        headers={"X-Request-ID": "client-supplied-id"},
                    )
                    with urlopen(request, timeout=3) as response:
                        assert json.load(response) == {"message": "hello"}
                        ids.append(response.headers["X-Request-ID"])
                assert all(ids) and len(set(ids)) == 2, ids
                assert "client-supplied-id" not in ids, ids
                check_http()
                deadline = time.monotonic() + 10
                while True:
                    status = json.loads(command("status", "-o", "json").stdout)
                    if len(status["workers"]) == 2:
                        break
                    if time.monotonic() >= deadline:
                        raise AssertionError(status)
                    time.sleep(0.2)
                assert status["status"] == "RUNNING", status
                assert status["service"]["name"] == "minimal-example", status
                assert status["service"]["configured_workers"] == 2, status
                master = psutil.Process(status["service"]["pid"])
                assert master.create_time() == status["service"]["process_create_time"]
                assert all(
                    psutil.Process(worker["pid"]).ppid() == master.pid
                    for worker in status["workers"]
                )
                with urlopen("http://127.0.0.1:18081/health", timeout=3) as response:
                    health = json.load(response)
                expected_runtime = "uvicorn" if sys.platform == "win32" else "gunicorn"
                assert health["service"]["runtime"] == expected_runtime, health
                assert status["service"]["runtime"] == expected_runtime, status
                assert health["service"]["service_pid"] == master.pid, health
                if sys.platform == "linux":
                    assert health["service"]["gunicorn_pid"] == master.pid == process.pid
                else:
                    assert health["service"]["gunicorn_pid"] is None, health
                assert "RUNNING" in command("status").stdout
                print("status:", json.dumps(status), flush=True)
                deadline = time.monotonic() + 5
                while True:
                    command_started_at = time.time()
                    observation = json.loads(command("logs", "-o", "json").stdout)
                    print(
                        "logs observation:",
                        json.dumps(
                            {
                                "command_started_at": command_started_at,
                                "checked_at": time.time(),
                                "observation": observation,
                            }
                        ),
                        flush=True,
                    )
                    if len(observation["paths"]) == 2 and not observation["stale"]:
                        break
                    if time.monotonic() >= deadline:
                        raise AssertionError(observation)
                    time.sleep(0.2)
                paths = [Path(path) for path in observation["paths"]]
                assert all(
                    path.is_absolute() and path.resolve().is_relative_to(working.resolve())
                    for path in paths
                )
                plain_paths = command("logs").stdout.splitlines()
                assert set(plain_paths) == {str(path) for path in paths}, plain_paths
                print("logs:", json.dumps(observation), flush=True)
                command("stop")
                stopped = True
                assert process.wait(timeout=10) == 0
                texts = [path.read_text(encoding="utf-8") for path in paths]
                assert all("minimal business startup" in content for content in texts)
                assert all("minimal business shutdown" in content for content in texts)
                log_text = "\n".join(texts)
                assert all(request_id in log_text for request_id in ids)
                assert "hello requested" in log_text
                assert json.loads(command("status", "-o", "json").stdout)["status"] == "STOPPED"
                assert json.loads(command("logs", "-o", "json").stdout)["paths"] == []
                print(
                    "PASS: hello, Request-ID, health, docs, shutdown authorization, "
                    "status, logs, stop, two-worker lifespan flush"
                )
            except BaseException:
                console.flush()
                console.seek(0)
                print(console.read(), file=sys.stderr, flush=True)
                for path in sorted((working / "logs").glob("*.log")):
                    try:
                        print(f"Worker log: {path}", file=sys.stderr, flush=True)
                        print(
                            path.read_text(encoding="utf-8", errors="replace"),
                            file=sys.stderr,
                            flush=True,
                        )
                    except OSError as diagnostic_error:
                        print(f"Cannot read worker log: {diagnostic_error}", file=sys.stderr)
                raise
            finally:
                if not stopped and process.poll() is None:
                    original_error = sys.exception()
                    try:
                        command("stop")
                        process.wait(timeout=10)
                    except Exception as cleanup_error:
                        print(f"Service cleanup failed: {cleanup_error!r}", file=sys.stderr)
                        # These are children of the process owned by this verifier.
                        try:
                            with suppress(psutil.NoSuchProcess):
                                for child in psutil.Process(process.pid).children(recursive=True):
                                    with suppress(psutil.NoSuchProcess):
                                        child.kill()
                            if process.poll() is None:
                                process.kill()
                                process.wait(timeout=10)
                        except Exception as forced_cleanup_error:
                            print(
                                f"Owned-process cleanup failed: {forced_cleanup_error!r}",
                                file=sys.stderr,
                            )
                        if original_error is None:
                            raise
                        original_error.add_note(f"Service cleanup also failed: {cleanup_error!r}")


if __name__ == "__main__":
    main()
