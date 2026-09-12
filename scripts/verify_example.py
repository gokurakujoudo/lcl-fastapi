"""Install and exercise one downstream example against a built wheel."""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import tomllib
from pathlib import Path


def main() -> None:
    """Create an independent environment and retain exact command diagnostics."""
    if len(sys.argv) != 2 or sys.argv[1] not in {
        "minimal",
        "composed",
        "directory_monitor",
        "playground",
    }:
        raise SystemExit(
            "Usage: python -m scripts.verify_example "
            "{minimal|composed|directory_monitor|playground}"
        )
    root = Path(__file__).resolve().parents[1]
    example = root / "examples" / sys.argv[1]
    version = tomllib.loads(root.joinpath("pyproject.toml").read_text(encoding="utf-8"))["project"][
        "version"
    ]
    wheel = root / "dist" / f"lcl_fastapi-{version}-py3-none-any.whl"
    if not wheel.is_file():
        raise SystemExit(f"Build the current source wheel first: {wheel}")
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    destination = example / ".venv"
    python = destination / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    commands = [
        [sys.executable, "-m", "venv", str(destination)],
        [str(python), "-m", "pip", "install", "--force-reinstall", str(wheel), str(example)],
        [str(python), str(example / "verify.py")],
    ]
    reports = root / "reports" / "examples"
    reports.mkdir(parents=True, exist_ok=True)
    with (reports / f"{example.name}.log").open("w", encoding="utf-8") as report:
        report.write(
            f"Wheel: {wheel.name}\nSHA-256: {hashlib.sha256(wheel.read_bytes()).hexdigest()}\n"
        )
        report.write(f"Platform: {sys.platform}\nRunner Python: {sys.version}\n")
        for command in commands:
            heading = "Running: " + " ".join(command)
            print(heading, flush=True)
            report.write(heading + "\n")
            report.flush()
            try:
                result = subprocess.run(
                    command,
                    cwd=example,
                    env=environment,
                    capture_output=True,
                    text=True,
                    timeout=240,
                    check=False,
                )
            except subprocess.TimeoutExpired as error:
                report.write(f"Timed out: {error}\n{error.stdout!r}\n{error.stderr!r}\n")
                raise
            output = result.stdout + result.stderr
            print(output, end="", flush=True)
            report.write(output)
            report.flush()
            if result.returncode:
                raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
