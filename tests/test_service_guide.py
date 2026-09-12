"""Run the exact guide application and verifier in an isolated native service."""

import json
import os
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
    assert set(files) == {
        "app.py",
        "service.lclcfg",
        "verify.py",
        "catalog_cli.py",
        "pyproject.toml",
    }
    with TemporaryDirectory() as directory:
        root = Path(directory)
        for name, content in files.items():
            root.joinpath(name).write_text(content, encoding="utf-8")
        installed = root / "installed"
        subprocess.run(
            [
                sys.executable,
                "-m",
                "pip",
                "install",
                "--no-deps",
                "--no-build-isolation",
                "--target",
                str(installed),
                ".",
            ],
            cwd=root,
            capture_output=True,
            text=True,
            check=True,
            timeout=60,
        )
        entrances = list(installed.rglob("catalog.exe" if sys.platform == "win32" else "catalog"))
        assert len(entrances) == 1, entrances
        cli = entrances[0]
        environment = os.environ | {
            "PYTHONPATH": os.pathsep.join([str(installed), os.environ.get("PYTHONPATH", "")])
        }

        def command(*arguments: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(
                [str(cli), *arguments],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                timeout=45,
                check=True,
            )

        assert "1.0.0" in command("--version").stdout
        assert command("serve", "--dryrun", "-o", "server.port", "LCL[18084]").returncode == 0
        assert not (root / "run").exists() and not (root / "logs").exists()

        with root.joinpath("console.log").open("w+", encoding="utf-8") as console:
            process = subprocess.Popen(
                [
                    str(cli),
                    "serve",
                    "-o",
                    "server.workers",
                    "LCL[2]",
                    "-o",
                    "logger.file.service.rotation.mode",
                    "size",
                    "-o",
                    "logger.file.service.rotation.max_bytes",
                    "LCL[2048]",
                ],
                cwd=root,
                env=environment,
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
                for _ in range(3):
                    result = subprocess.run(
                        [sys.executable, "verify.py"],
                        cwd=root,
                        capture_output=True,
                        text=True,
                        timeout=15,
                    )
                    assert result.returncode == 0, result.stdout + result.stderr
                    assert "Catalog checks passed" in result.stdout
                status = json.loads(command("status").stdout)
                assert status["status"] == "RUNNING"
                assert status["service"]["configured_workers"] == 2
                deadline = time.monotonic() + 15
                while len(status["workers"]) != 2:
                    assert time.monotonic() < deadline, status
                    time.sleep(0.2)
                    status = json.loads(command("status").stdout)
                assert json.loads(command("logs").stdout)["paths"]
            finally:
                if process.poll() is None:
                    command("stop")
                process.wait(timeout=45)
            assert process.returncode == 0
            assert json.loads(command("status").stdout)["status"] == "STOPPED"
            logs = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("logs/*"))
            request_id = result.stdout.strip().split()[-1]
            assert any("list products" in line and request_id in line for line in logs.splitlines())
            assert "catalog ready" in logs and "catalog closed" in logs
            controller = "\n".join(
                path.read_text(encoding="utf-8") for path in root.glob("logs/*.controller.*.log")
            )
            assert "service started" in controller and "service stopped" in controller
            assert "list products" not in controller and "catalog closed" not in controller
            assert "continued in: " in logs
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
