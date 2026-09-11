"""Exercise release identity guards and recovery without contacting registries."""

import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import release


def release_files(tmp_path: Path) -> list[Path]:
    dist = tmp_path / "dist"
    dist.mkdir()
    files = [dist / "lcl_fastapi-0.1.0-py3-none-any.whl", dist / "lcl_fastapi-0.1.0.tar.gz"]
    for file in files:
        file.write_bytes(file.name.encode())
    return files


@pytest.mark.parametrize(
    "notes",
    [
        "## Unreleased\n\n- Pending\n",
        "## 0.1.0 - 2026-09-11\n",
        "## 0.1.0 - 9999-01-01\n- Future\n",
    ],
)
def test_release_rejects_missing_empty_or_future_notes(tmp_path: Path, notes: str) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    (tmp_path / "CHANGELOG.md").write_text(notes)
    with pytest.raises(ValueError):
        release.metadata(tmp_path)


def test_release_notes_select_the_canonical_version(tmp_path: Path) -> None:
    (tmp_path / "pyproject.toml").write_text('[project]\nversion = "0.1.0"\n')
    (tmp_path / "CHANGELOG.md").write_text(
        "## Unreleased\n\n- Later\n\n## 0.1.0 - 2026-09-11\n\n- First release\n"
        "\n## 0.0.1 - 2026-01-01\n\n- Earlier\n"
    )
    assert release.metadata(tmp_path) == ("0.1.0", "- First release\n")


@pytest.mark.parametrize(
    "head,tag,main", [("other", "", True), ("selected", "other", True), ("selected", "", False)]
)
def test_release_rejects_wrong_source_or_existing_tag(
    monkeypatch: pytest.MonkeyPatch, head: str, tag: str, main: bool
) -> None:
    def command(arguments: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        if "merge-base" in arguments:
            result = subprocess.CompletedProcess(arguments, 0 if main else 1, "", "")
        elif "--verify" in arguments:
            result = subprocess.CompletedProcess(arguments, 0 if tag else 1, tag, "")
        else:
            result = subprocess.CompletedProcess(arguments, 0, head, "")
        if check:
            result.check_returncode()
        return result

    monkeypatch.setattr(release, "command", command)
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        release.verify_source("0.1.0", "selected")


def test_pypi_hash_mismatch_prevents_github_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = release_files(tmp_path)

    def response(url: str, *, timeout: int) -> io.BytesIO:
        assert url == "https://pypi.org/pypi/lcl-fastapi/0.1.0/json"
        assert 0 < timeout <= 30
        return io.BytesIO(
            json.dumps(
                {
                    "urls": [
                        {"filename": file.name, "digests": {"sha256": "wrong"}} for file in files
                    ]
                }
            ).encode()
        )

    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repository")
    monkeypatch.setattr(release, "urlopen", response)
    monkeypatch.setattr(
        release, "command", lambda *args, **kwargs: pytest.fail("GitHub must not be called")
    )
    with pytest.raises(ValueError, match="SHA-256"):
        release.publish_github(tmp_path, "0.1.0", "- First release\n", "selected")


@pytest.mark.parametrize("existing", [False, True])
def test_github_publication_resumes_matching_draft_and_checks_assets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, existing: bool
) -> None:
    files = release_files(tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repository")
    payload = {
        "urls": [
            {"filename": file.name, "digests": {"sha256": release.digest(file)}} for file in files
        ]
    }
    monkeypatch.setattr(
        release, "urlopen", lambda *args, **kwargs: io.BytesIO(json.dumps(payload).encode())
    )
    created = existing
    uploaded = [files[0].name] if existing else []
    actions: list[str] = []

    def command(arguments: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        nonlocal created
        output = ""
        if arguments[:2] == ["gh", "api"]:
            if not created:
                return subprocess.CompletedProcess(arguments, 1, "", "gh: Not Found (HTTP 404)")
            output = json.dumps(
                {
                    "target_commitish": "selected",
                    "body": "- First release\n",
                    "draft": True,
                    "assets": [{"name": name} for name in uploaded],
                }
            )
        elif arguments[:3] == ["gh", "release", "create"]:
            created = True
            assert "--draft" in arguments and "--notes-file" in arguments
            actions.append("create")
        elif arguments[:3] == ["gh", "release", "upload"]:
            name = Path(arguments[4]).name
            assert name not in uploaded
            uploaded.append(name)
            actions.append("upload")
        elif arguments[:3] == ["gh", "release", "download"]:
            name = arguments[arguments.index("--pattern") + 1]
            Path(arguments[arguments.index("--output") + 1]).write_bytes(
                (tmp_path / "dist" / name).read_bytes()
            )
            actions.append("verify")
        elif arguments[:3] == ["gh", "release", "edit"]:
            assert actions.count("verify") == 2 and "--draft=false" in arguments
            actions.append("publish")
        elif arguments[:2] == ["git", "rev-parse"]:
            output = "selected"
        return subprocess.CompletedProcess(arguments, 0, output, "")

    monkeypatch.setattr(release, "command", command)
    release.publish_github(tmp_path, "0.1.0", "- First release\n", "selected")
    assert actions.count("upload") == (1 if existing else 2)
    assert actions.count("create") == (0 if existing else 1)
    assert actions[-1] == "publish"


def test_existing_changed_asset_is_never_replaced_or_published(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    files = release_files(tmp_path)
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repository")
    monkeypatch.setattr(release, "verify_pypi", lambda *args: None)
    monkeypatch.setattr(
        release,
        "read_release",
        lambda *args: {
            "target_commitish": "selected",
            "body": "- Notes",
            "draft": True,
            "assets": [{"name": file.name} for file in files],
        },
    )

    def command(arguments: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        assert arguments[:3] == ["gh", "release", "download"]
        Path(arguments[arguments.index("--output") + 1]).write_bytes(b"changed")
        return subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(release, "command", command)
    with pytest.raises(ValueError, match="asset differs"):
        release.publish_github(tmp_path, "0.1.0", "- Notes", "selected")


def test_github_permission_failure_is_not_treated_as_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        release,
        "command",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            ["gh", "api"], 1, "", "gh: Forbidden (HTTP 403)"
        ),
    )
    with pytest.raises(subprocess.CalledProcessError):
        release.read_release("example/repository", "0.1.0")


def test_release_ref_guard_prevents_publication(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "argv", ["release", "github"])
    monkeypatch.setenv("GITHUB_REF", "refs/heads/main")
    monkeypatch.setattr(release, "publish_github", lambda *args: pytest.fail("must not publish"))
    with pytest.raises(ValueError, match="release branch"):
        release.main()
