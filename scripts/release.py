"""Verify release identity and publish matching, immutable GitHub assets."""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tomllib
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any
from urllib.parse import quote
from urllib.request import urlopen


def command(arguments: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    """Run one Git/gh operation with a finite network/process deadline."""
    return subprocess.run(arguments, check=check, capture_output=True, text=True, timeout=60)


def metadata(root: Path) -> tuple[str, str]:
    """Read the canonical version and its nonempty, dated release notes."""
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    version = project["version"]
    if not isinstance(version, str) or not re.fullmatch(r"[0-9][A-Za-z0-9.!+_-]*", version):
        raise ValueError("release version must be safe for a canonical version tag")
    changelog = (root / "CHANGELOG.md").read_text(encoding="utf-8")
    section = re.search(
        rf"^## {re.escape(version)} - (\d{{4}}-\d{{2}}-\d{{2}})\n(.*?)(?=^## |\Z)",
        changelog,
        re.MULTILINE | re.DOTALL,
    )
    if section is None or not re.search(r"^- \S", section[2], re.MULTILINE):
        raise ValueError(f"CHANGELOG.md needs nonempty dated notes for {version}")
    if date.fromisoformat(section[1]) > date.today():
        raise ValueError("release notes must not be future-dated")
    return version, section[2].strip() + "\n"


def verify_source(version: str, revision: str) -> None:
    """Reject publication from a different checkout, outside main, or over an existing tag."""
    if command(["git", "rev-parse", "HEAD"]).stdout.strip() != revision:
        raise ValueError("checkout differs from the selected release SHA")
    command(["git", "merge-base", "--is-ancestor", revision, "origin/main"])
    tag = command(
        ["git", "rev-parse", "--verify", "--quiet", f"refs/tags/{version}^{{commit}}"], check=False
    )
    if tag.returncode == 0:
        if tag.stdout.strip() != revision:
            raise ValueError("existing version tag points to a different source commit")
    elif tag.returncode != 1:
        tag.check_returncode()


def distributions(root: Path, version: str) -> list[Path]:
    """Require exactly the wheel and source archive validated by the quality workflow."""
    files = sorted((root / "dist").glob("*"))
    expected = {f"lcl_fastapi-{version}-py3-none-any.whl", f"lcl_fastapi-{version}.tar.gz"}
    if {file.name for file in files} != expected or not all(file.is_file() for file in files):
        raise ValueError("release distributions must contain exactly the selected wheel and sdist")
    return files


def digest(path: Path) -> str:
    """Calculate an artifact's SHA-256 without changing its bytes."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_pypi(files: list[Path], version: str) -> None:
    """Check registry digests before creating a GitHub release or version tag."""
    with urlopen(
        f"https://pypi.org/pypi/lcl-fastapi/{quote(version, safe='')}/json", timeout=15
    ) as response:
        data = json.load(response)
    published = {item["filename"]: item["digests"]["sha256"] for item in data["urls"]}
    expected = {file.name: digest(file) for file in files}
    if published != expected:
        raise ValueError("PyPI distribution names or SHA-256 digests differ from tested artifacts")


def read_release(repository: str, version: str) -> dict[str, Any] | None:
    """Find published releases and drafts across authenticated collection pages."""
    result = command(["gh", "api", "--paginate", "--slurp", f"repos/{repository}/releases"])
    result.check_returncode()
    pages: list[list[dict[str, Any]]] = json.loads(result.stdout)
    matches = [item for page in pages for item in page if item["tag_name"] == version]
    if len(matches) > 1:
        raise ValueError("multiple releases claim the selected version")
    return matches[0] if matches else None


def publish_github(root: Path, version: str, notes: str, revision: str) -> None:
    """Resume a matching draft without replacing existing tags or artifact bytes."""
    repository = os.environ["GITHUB_REPOSITORY"]
    files = distributions(root, version)
    verify_pypi(files, version)
    release = read_release(repository, version)
    with TemporaryDirectory(prefix="lcl-release-") as directory:
        temporary = Path(directory)
        if release is None:
            notes_file = temporary / "notes.md"
            notes_file.write_text(notes, encoding="utf-8")
            command(
                [
                    "gh",
                    "release",
                    "create",
                    version,
                    "--repo",
                    repository,
                    "--draft",
                    "--target",
                    revision,
                    "--title",
                    version,
                    "--notes-file",
                    str(notes_file),
                ]
            )
            release = read_release(repository, version)
        if (
            release is None
            or release["target_commitish"] != revision
            or release["body"].strip() != notes.strip()
        ):
            raise ValueError("existing release identity or notes differ from this publication")
        names = {asset["name"] for asset in release["assets"]}
        if names - {file.name for file in files}:
            raise ValueError("existing GitHub release has unexpected assets")
        for file in files:
            if file.name not in names:
                if not release["draft"]:
                    raise ValueError("refusing to modify an incomplete public release")
                command(["gh", "release", "upload", version, str(file), "--repo", repository])
            downloaded = temporary / file.name
            command(
                [
                    "gh",
                    "release",
                    "download",
                    version,
                    "--repo",
                    repository,
                    "--pattern",
                    file.name,
                    "--output",
                    str(downloaded),
                ]
            )
            if digest(downloaded) != digest(file):
                raise ValueError(f"existing GitHub asset differs: {file.name}")
        if release["draft"]:
            command(["gh", "release", "edit", version, "--repo", repository, "--draft=false"])
    command(["git", "fetch", "origin", "tag", version])
    verify_source(version, revision)
    print(f"Verified PyPI, GitHub assets, and tag for {version} at {revision}")


def main() -> None:
    """Run the selected CI stage without accepting arbitrary publication refs."""
    if sys.argv[1:] not in (["prepare"], ["github"]):
        raise SystemExit("Usage: python -m scripts.release {prepare|github}")
    if os.environ.get("GITHUB_REF") != "refs/heads/release":
        raise ValueError("publication must run on the release branch")
    root = Path.cwd()
    version, notes = metadata(root)
    revision = os.environ["GITHUB_SHA"]
    verify_source(version, revision)
    if sys.argv[1] == "prepare":
        with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
            output.write(f"version={version}\n")
        print(f"Prepared {version} from {revision}")
    else:
        publish_github(root, version, notes, revision)


if __name__ == "__main__":
    main()
