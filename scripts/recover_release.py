"""Finish only GitHub publication from an original, successful PyPI run."""

from __future__ import annotations

import json
import os
import re
from contextlib import chdir
from pathlib import Path
from tempfile import TemporaryDirectory

from scripts.release import command, metadata, publish_github, verify_source


def source_revision(repository: str, run_id: str) -> str:
    """Require a completed release-branch run whose PyPI upload succeeded."""
    if not re.fullmatch(r"[1-9][0-9]*", run_id):
        raise ValueError("publication run ID must be a positive integer")
    endpoint = f"repos/{repository}/actions/runs/{run_id}"
    run = json.loads(command(["gh", "api", endpoint]).stdout)
    if (
        run["event"] != "push"
        or run["head_branch"] != "release"
        or run["path"] != ".github/workflows/release.yml"
        or run["status"] != "completed"
        or run["head_repository"]["full_name"] != repository
    ):
        raise ValueError("recovery requires a completed original release-branch workflow")
    pages = json.loads(
        command(["gh", "api", "--paginate", "--slurp", f"{endpoint}/jobs?filter=all"]).stdout
    )
    if not any(
        job["name"] == "publish-pypi" and job["conclusion"] == "success"
        for page in pages
        for job in page["jobs"]
    ):
        raise ValueError("original run has no successful PyPI publication")
    revision = run["head_sha"]
    if not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("original run has an invalid source SHA")
    command(["git", "merge-base", "--is-ancestor", revision, "origin/main"])
    return revision


def main() -> None:
    """Use corrected tooling from main with the original source and artifact bytes."""
    if os.environ.get("GITHUB_REF") != "refs/heads/main":
        raise ValueError("recovery tooling must run from main")
    repository = os.environ["GITHUB_REPOSITORY"]
    run_id = os.environ["PUBLICATION_RUN_ID"]
    revision = source_revision(repository, run_id)
    with TemporaryDirectory(prefix="lcl-release-recovery-") as directory:
        source = Path(directory) / "source"
        command(["git", "worktree", "add", "--detach", str(source), revision])
        try:
            command(
                [
                    "gh",
                    "run",
                    "download",
                    run_id,
                    "--repo",
                    repository,
                    "--name",
                    "current-head-distributions",
                    "--dir",
                    str(source / "dist"),
                ]
            )
            with chdir(source):
                version, notes = metadata(source)
                verify_source(version, revision)
                publish_github(source, version, notes, revision)
        finally:
            command(["git", "worktree", "remove", "--force", str(source)])
    print(f"Recovered only GitHub publication from original run {run_id} at {revision}")


if __name__ == "__main__":
    main()
