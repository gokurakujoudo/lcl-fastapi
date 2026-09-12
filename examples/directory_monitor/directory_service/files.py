"""Filesystem operations kept outside the API event loop."""

import os
import stat
from pathlib import Path, PurePosixPath

from fastapi import HTTPException


def within(root: Path, name: str) -> Path:
    """Accept relative POSIX paths without links, junctions or traversal."""
    parts = PurePosixPath(name).parts
    if not parts or name.startswith("/") or "\\" in name or ":" in name:
        raise HTTPException(400, "Use a relative path with forward slashes")
    if any(part in {"..", "."} for part in name.split("/")):
        raise HTTPException(400, "Path traversal is not allowed")
    path = root
    for part in parts:
        path /= part
        if path.is_symlink() or path.is_junction():
            raise HTTPException(400, "Links and junctions are not served")
    if not path.resolve().is_relative_to(root):
        raise HTTPException(400, "Path is outside the configured directory")
    return path


def scan(root: Path, limit: int) -> dict[str, object]:
    """Return a bounded recursive snapshot, retaining errors as visible state."""
    entries: list[dict[str, object]] = []
    errors: list[str] = []
    if not root.is_dir():
        return {
            "entries": [],
            "errors": ["Configured directory is unavailable"],
            "truncated": False,
        }

    def failed(error: OSError) -> None:
        errors.append(str(error))

    for parent, directories, files in os.walk(root, followlinks=False, onerror=failed):
        directories[:] = sorted(
            name
            for name in directories
            if not (Path(parent) / name).is_symlink() and not (Path(parent) / name).is_junction()
        )
        for name in sorted([*directories, *files]):
            path = Path(parent) / name
            try:
                info = path.lstat()
                if not (stat.S_ISREG(info.st_mode) or stat.S_ISDIR(info.st_mode)):
                    continue
                if path.is_junction():
                    continue
                if len(entries) >= limit:
                    return {"entries": entries, "errors": errors, "truncated": True}
                entries.append(
                    {
                        "path": path.relative_to(root).as_posix(),
                        "kind": "directory" if path.is_dir() else "file",
                        "bytes": info.st_size if path.is_file() else 0,
                        "modified_ns": info.st_mtime_ns,
                    }
                )
            except OSError as error:
                failed(error)
    return {"entries": entries, "errors": errors, "truncated": False}


def save(root: Path, name: str, content: bytes) -> None:
    """Create a new file, refusing to overwrite an existing entry."""
    path = within(root, name)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as output:
            output.write(content)
    except FileExistsError as error:
        raise HTTPException(409, "File already exists") from error
