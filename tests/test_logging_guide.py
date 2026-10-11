"""Run the exact logging guide with native processes and inspect its real output."""

import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest
from coverage import Coverage

from scripts.documentation import tutorial_files

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.documentation
@pytest.mark.integration
def test_logging_practices_from_printed_source() -> None:
    files = tutorial_files(ROOT / "docs/logging-practices.md")
    assert set(files) == {"app.py", "revision.py", "service.lclcfg", "verify.py"}
    with TemporaryDirectory() as directory:
        root = Path(directory)
        for name, content in files.items():
            root.joinpath(name).write_text(content, encoding="utf-8")
        environment = os.environ.copy()
        environment.pop("PYTHONPATH", None)
        if (coverage := Coverage.current()) is not None:
            environment["COVERAGE_FILE"] = str(Path(coverage.config.data_file).resolve())
        result = subprocess.run(
            [sys.executable, "verify.py"],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=150,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert (
            "PASS native startup, handler, Worker, reload, caught and uncaught logs"
            in result.stdout
        )
