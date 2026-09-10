"""Serialize trusted service state and verify operating-system identities."""

import json
import os
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import cast
from uuid import uuid4

import psutil


def read_state(path: Path) -> dict[str, object]:
    """Read a JSON object, treating missing or malformed state as absent.

    :param path: Trusted local state file.
    :returns: Decoded object, or an empty mapping for unusable state.
    :raises OSError: If access fails for a reason other than absence.
    """
    try:
        value: object = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError, ValueError:
        return {}
    return cast(dict[str, object], value) if isinstance(value, dict) else {}


def atomic_write(path: Path, value: dict[str, object]) -> None:
    """Publish a complete JSON observation through an atomic replacement.

    :param path: Destination in a trusted service directory.
    :param value: JSON-serializable observation.
    :raises OSError: If creating or replacing the file fails.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def process_identity() -> dict[str, object]:
    """Read the current process identity from psutil.

    :returns: PID and operating-system process creation time in Unix seconds.
    :raises psutil.Error: If the current process cannot be inspected.
    """
    process = psutil.Process()
    return {"pid": process.pid, "process_create_time": process.create_time()}


def is_live(record: dict[str, object]) -> bool:
    """Reject dead, inaccessible, malformed, and PID-reused identities.

    :param record: Persisted process identity.
    :returns: Whether the exact process is currently alive and not a zombie.
    """
    pid = record.get("pid")
    created = record.get("process_create_time")
    if type(pid) is not int or pid <= 0 or type(created) not in {int, float}:
        return False
    try:
        process = psutil.Process(pid)
        return process.create_time() == created and process.status() != psutil.STATUS_ZOMBIE
    except psutil.Error:
        return False


@contextmanager
def file_lock(path: Path, blocking: bool = True) -> Iterator[None]:
    """Own an OS file lock which the kernel releases after process failure.

    :param path: Persistent lock file, never deleted while processes may use it.
    :param blocking: Whether to wait for a current owner.
    :returns: Context manager holding the lock.
    :raises OSError: If acquisition or filesystem access fails.

    A forked child closes its inherited descriptor without unlocking the
    parent's shared POSIX open file description.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0)
        if sys.platform == "win32":
            import msvcrt

            mode = msvcrt.LK_LOCK if blocking else msvcrt.LK_NBLCK
            msvcrt.locking(stream.fileno(), mode, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            creator_pid = os.getpid()
            mode = fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB)
            fcntl.flock(stream.fileno(), mode)
            try:
                yield
            finally:
                if os.getpid() == creator_pid:
                    fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def live_workers(directory: Path, service_id: object) -> list[dict[str, object]]:
    """Read observations belonging to the current service and live workers.

    :param directory: Worker-state directory recorded by the master.
    :param service_id: Unique complete-start identity.
    :returns: Live matching worker observations ordered by filename.
    :raises OSError: If a state file is inaccessible.
    """
    result = []
    for path in sorted(directory.glob("*.json")):
        record = read_state(path)
        if record.get("service_id") == service_id and is_live(record):
            result.append(record)
    return result
