"""Run the exact guide application and verifier in an isolated native service."""

import json
import re
import subprocess
import sys
import time
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.error import URLError
from urllib.request import urlopen

import pytest

GUIDE = Path(__file__).resolve().parents[1] / "docs" / "build-a-service.md"
CLI = Path(sys.executable).parent / (
    "lcl-fastapi.exe" if sys.platform == "win32" else "lcl-fastapi"
)


@pytest.mark.documentation
@pytest.mark.integration
def test_catalog_guide_native_service() -> None:
    source = GUIDE.read_text(encoding="utf-8")
    files = dict(
        re.findall(
            r"<!-- tutorial-file: ([^ ]+) -->\s*(?:<!-- python-doc-exec -->\s*)?"
            r"```\w+\n(.*?)```",
            source,
            re.S,
        )
    )
    assert set(files) == {"app.py", "service.lclcfg", "verify.py"}
    with TemporaryDirectory() as directory:
        root = Path(directory)
        for name, content in files.items():
            root.joinpath(name).write_text(content, encoding="utf-8")

        def command(*arguments: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [str(CLI), *arguments, "-o", "config", "service.lclcfg"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=45,
                check=True,
            )

        with root.joinpath("console.log").open("w+", encoding="utf-8") as console:
            process = subprocess.Popen(
                [str(CLI), "serve", "-o", "config", "service.lclcfg"],
                cwd=root,
                stdout=console,
                stderr=subprocess.STDOUT,
            )
            try:
                deadline = time.monotonic() + 30
                while True:
                    assert process.poll() is None, root.joinpath("console.log").read_text(
                        encoding="utf-8"
                    )
                    try:
                        with urlopen("http://127.0.0.1:18083/health", timeout=1) as response:
                            if response.status == 200:
                                break
                    except URLError, TimeoutError:
                        pass
                    assert time.monotonic() < deadline, "Catalog startup timed out"
                    time.sleep(0.2)
                result = subprocess.run(
                    [sys.executable, "verify.py"],
                    cwd=root,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
                assert result.returncode == 0, result.stdout + result.stderr
                assert "Catalog checks passed" in result.stdout
                status = json.loads(command("status", "-o", "json", "LCL[True]").stdout)
                assert status["status"] == "RUNNING"
                assert len(status["workers"]) == 1
                assert command("logs").stdout.strip()
            finally:
                if process.poll() is None:
                    command("stop")
                process.wait(timeout=45)
            assert process.returncode == 0
            assert (
                json.loads(command("status", "-o", "json", "LCL[True]").stdout)["status"]
                == "STOPPED"
            )
            logs = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("logs/*"))
            request_id = result.stdout.strip().split()[-1]
            assert any("list products" in line and request_id in line for line in logs.splitlines())
            assert "catalog ready" in logs and "catalog closed" in logs
        deployment = re.search(r"<!-- tutorial-deployment -->\s*```text\n(.*?)```", source, re.S)
        assert deployment is not None
        root.joinpath("service.lclcfg").write_text(
            files["service.lclcfg"] + deployment[1], encoding="utf-8"
        )
        command("nginx", "render", "-o", "output", "catalog.conf")
        command("systemd", "render", "-o", "output", "catalog.service")
        nginx = root.joinpath("catalog.conf").read_text(encoding="utf-8")
        unit = root.joinpath("catalog.service").read_text(encoding="utf-8")
        assert "proxy_pass http://127.0.0.1:18083;" in nginx
        assert "location = /_lcl/shutdown { return 404; }" in nginx
        assert "/opt/catalog-api/.venv/bin/lcl-fastapi" in unit
        assert 'serve -o config "/opt/catalog-api/service.lclcfg"' in unit
