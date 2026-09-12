"""Verify the installed tutorial application using real native service processes."""

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import psutil

HERE = Path(__file__).resolve().parent
CLI = Path(sys.executable).parent / ("lcl-fastapi.exe" if os.name == "nt" else "lcl-fastapi")


def http(
    origin: str,
    path: str,
    method: str = "GET",
    data: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> tuple[int, bytes]:
    request = Request(origin + path, method=method, data=data, headers=headers or {})
    try:
        response = urlopen(request, timeout=8)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.read()


def json_http(origin: str, path: str, method: str = "GET", data: object = None) -> tuple[int, Any]:
    status, body = http(
        origin,
        path,
        method,
        None if data is None else json.dumps(data).encode(),
        {"Content-Type": "application/json"},
    )
    return status, json.loads(body)


def main() -> None:
    with TemporaryDirectory() as directory:
        root = Path(directory)
        source = HERE.joinpath("service.lclcfg").read_text(encoding="utf-8")
        config = root / "service.lclcfg"
        config.write_text(source, encoding="utf-8")
        origin = ORIGIN

        def command(*args: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [str(CLI), *args, "-o", "config", str(config)],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=45,
                check=True,
            )

        with (root / "console.log").open("w+", encoding="utf-8") as output:
            process = subprocess.Popen(
                [str(CLI), "serve", "-o", "config", str(config)],
                cwd=root,
                stdout=output,
                stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 30
                while True:
                    assert process.poll() is None, (root / "console.log").read_text(
                        encoding="utf-8"
                    )
                    try:
                        if http(origin, "/health")[0] == 200:
                            break
                    except URLError, TimeoutError:
                        pass
                    assert time.monotonic() < deadline, "Service startup timed out"
                    time.sleep(0.1)
                assert http(origin, "/")[0] == 200
                for asset in ["/static/app.js", "/static/style.css"]:
                    assert http(origin, asset)[0] == 200
                verify(origin, root)
                status = json.loads(command("status").stdout)
                assert status["service"]["configured_workers"] == 1
            finally:
                if process.poll() is None:
                    owner = psutil.Process(process.pid)
                    try:
                        command("stop")
                    except BaseException:
                        for child in reversed(owner.children(recursive=True)):
                            child.kill()
                        owner.kill()
                        process.wait(timeout=10)
                        raise
                process.wait(timeout=45)
            assert process.returncode == 0, (root / "console.log").read_text(encoding="utf-8")
            assert json.loads(command("status").stdout)["status"] == "STOPPED"
        print("PASS " + HERE.name + ": installed static UI, native APIs, isolation and cleanup")


ORIGIN = "http://127.0.0.1:18086"


def verify(origin: str, root: Path) -> None:
    status, session = json_http(origin, "/api/sessions", "POST")
    assert status == 201
    key = session["id"]
    path = "/api/sessions/" + key
    source = (
        "__LCL_VERSION__: 1\nflag: True\nleft: 42\nright: 1 / 0\nanswer: left if flag else right"
    )
    status, parsed = json_http(
        origin, path + "/program", "PUT", {"source": source, "expression": "answer"}
    )
    assert status == 200 and parsed["ast"]["answer"]["kind"] == "LclConditional"
    assert any(
        edge["target"] == "right" and edge["kind"] == "conditional"
        for edge in parsed["dependencies"]
    )
    status, result = json_http(origin, path + "/evaluate", "POST")
    assert status == 200 and result["result"]["repr"] == "42"
    assert not any("name='right'" in row for row in result["trace"])
    assert any("cached" in row for row in json_http(origin, path + "/evaluate", "POST")[1]["trace"])
    second = json_http(origin, "/api/sessions", "POST")[1]["id"]
    assert json_http(origin, "/api/sessions/" + second + "/evaluate", "POST")[0] == 409
    assert (
        json_http(
            origin,
            path + "/program",
            "PUT",
            {"source": "__LCL_VERSION__: 1\nx: (", "expression": "x"},
        )[0]
        == 422
    )
    assert json_http(origin, path)[1]["source"] == source
    assert json_http(origin, path, "DELETE")[0] == 200
    assert json_http(origin, path)[0] == 404
    assert (
        http(origin, "/api/sessions", "POST", headers={"Origin": "https://other.example"})[0] == 403
    )
    assert not (root / "session.lclcfg").exists()


if __name__ == "__main__":
    main()
