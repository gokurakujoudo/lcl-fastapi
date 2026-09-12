"""Run the authoritative quality gate in the current Python environment."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    """Run checks, retaining per-platform coverage when explicitly requested."""
    arguments = sys.argv[1:]
    if arguments not in ([], ["--collect-coverage"]):
        raise SystemExit("Usage: python -m scripts.quality [--collect-coverage]")
    Path("reports").mkdir(exist_ok=True)
    # Workers deliberately change cwd; retain every process's coverage in one report location.
    os.environ["COVERAGE_FILE"] = str(Path(".coverage").resolve())
    commands = [
        ["git", "diff", "--check"],
        ["git", "diff", "--cached", "--check"],
        ["git", "show", "--format=", "--check", "HEAD"],
        [sys.executable, "-m", "ruff", "check", "."],
        [sys.executable, "-m", "ruff", "format", "--check", "src", "tests", "scripts", "examples"],
        [sys.executable, "-m", "scripts.policy"],
        [sys.executable, "-m", "scripts.architecture"],
        [sys.executable, "-m", "mypy"],
        [sys.executable, "-m", "mypy", "examples/minimal"],
        [sys.executable, "-m", "mypy", "examples/composed"],
        [sys.executable, "-m", "mypy", "examples/directory_monitor"],
        [sys.executable, "-m", "mypy", "examples/playground"],
        [sys.executable, "-m", "pytest", "-m", "documentation", "--no-cov"],
        [sys.executable, "-m", "coverage", "erase"],
        [
            sys.executable,
            "-m",
            "coverage",
            "run",
            "-m",
            "pytest",
            "-m",
            "not documentation",
            "--junitxml=reports/pytest.xml",
        ],
        [sys.executable, "-m", "coverage", "combine"],
        [sys.executable, "-m", "scripts.coverage_report", *(["--platform"] if arguments else [])],
    ]
    if not arguments:
        commands.extend(
            [
                [sys.executable, "-m", "build"],
                [sys.executable, "-m", "scripts.artifacts"],
            ]
        )
    with Path("reports/quality.log").open("w", encoding="utf-8") as report:
        for command in commands:
            heading = "Running: " + " ".join(command)
            print(heading, flush=True)
            report.write(heading + "\n")
            report.flush()
            with subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            ) as process:
                assert process.stdout is not None
                for line in process.stdout:
                    print(line, end="", flush=True)
                    report.write(line)
                    report.flush()
                if returncode := process.wait():
                    raise subprocess.CalledProcessError(returncode, command)


if __name__ == "__main__":
    main()
