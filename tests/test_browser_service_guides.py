"""Build the browser services using only the exact files printed in their guides."""

import os
import subprocess
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from scripts.documentation import tutorial_files

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.documentation
@pytest.mark.integration
@pytest.mark.parametrize(
    ("chapter", "example"),
    [("02-directory-monitor.md", "directory_monitor"), ("03-lcl-playground.md", "playground")],
)
def test_build_browser_service_from_printed_source(chapter: str, example: str) -> None:
    files = tutorial_files(ROOT / "docs/build-a-service" / chapter)
    reference = ROOT / "examples" / example
    expected = {
        str(path.relative_to(reference)).replace("\\", "/")
        for path in reference.rglob("*")
        if path.is_file()
        and not any(
            part.startswith(".") or part == "__pycache__"
            for part in path.relative_to(reference).parts
        )
        and path.suffix in {".py", ".toml", ".lclcfg", ".html", ".css", ".js"}
    }
    assert set(files) == expected
    for name, content in files.items():
        assert content == reference.joinpath(name).read_text(encoding="utf-8"), name
    with TemporaryDirectory() as directory:
        root = Path(directory)
        for name, content in files.items():
            target = root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        installed = root / "installed"
        build = subprocess.run(
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
            timeout=60,
        )
        assert build.returncode == 0, build.stdout + build.stderr
        result = subprocess.run(
            [sys.executable, "verify.py"],
            cwd=root,
            env=os.environ | {"PYTHONPATH": str(installed)},
            capture_output=True,
            text=True,
            timeout=150,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        assert "PASS " in result.stdout
