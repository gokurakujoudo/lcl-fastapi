"""Observe identity, atomic state, and concurrent worker-lease contracts."""

import asyncio
import importlib
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import psutil
import pytest

from lcl_fastapi.config import load_settings
from lcl_fastapi.runtime.lease import WorkerIdLease
from lcl_fastapi.runtime.service import service_runtime
from lcl_fastapi.runtime.state import (
    atomic_write,
    file_lock,
    is_live,
    live_workers,
    process_identity,
    read_state,
)
from lcl_fastapi.runtime.worker import worker_runtime

CONFIG = """__LCL_VERSION__: 1
app.name: "state-test"
app.version: "1"
app.target: "irrelevant:app"
"""


def test_atomic_state_accepts_only_objects_and_replaces(tmp_path: Path) -> None:
    path = tmp_path / "nested/state.json"
    assert read_state(path) == {}
    atomic_write(path, {"first": "one"})
    assert read_state(path) == {"first": "one"}
    atomic_write(path, {"second": [2]})
    assert read_state(path) == {"second": [2]}
    assert not list(path.parent.glob("*.tmp"))
    for text in ("[1]", "null", "{incomplete", "true"):
        path.write_text(text, encoding="utf-8")
        assert read_state(path) == {}


def test_atomic_state_removes_failed_temporary_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "state.json"
    atomic_write(path, {"original": True})

    def fail(source: Path, destination: Path) -> None:
        raise OSError("disk failure")

    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError, match="disk failure"):
        atomic_write(path, {"replacement": True})
    assert read_state(path) == {"original": True}
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize(
    "platform, failures, succeeds",
    [("win32", 1, True), ("win32", 2, True), ("win32", 3, False), ("linux", 1, False)],
)
def test_atomic_replace_retries_bounded_windows_reader_conflicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    failures: int,
    succeeds: bool,
) -> None:
    from lcl_fastapi.runtime import state

    path = tmp_path / "reload.json"
    atomic_write(path, {"generation": 1})
    replace_file = os.replace
    calls = 0
    sleeps: list[float] = []

    def replace_with_reader(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        assert read_state(destination) == {"generation": 1}
        if calls <= failures:
            raise PermissionError("concurrent reader sharing conflict")
        replace_file(source, destination)

    monkeypatch.setattr(state, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(os, "replace", replace_with_reader)
    monkeypatch.setattr("time.sleep", sleeps.append)
    if succeeds:
        atomic_write(path, {"generation": 2})
        assert read_state(path) == {"generation": 2}
        assert calls == failures + 1
    else:
        with pytest.raises(PermissionError, match="sharing conflict"):
            atomic_write(path, {"generation": 2})
        assert read_state(path) == {"generation": 1}
        assert calls == (3 if platform == "win32" else 1)
    assert sleeps == [0.01] * (calls - 1)
    assert list(tmp_path.iterdir()) == [path]


def test_process_identity_rejects_pid_reuse_and_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = process_identity()
    assert is_live(identity)
    invalid: list[dict[str, object]] = [
        {},
        {"pid": True},
        {"pid": -1},
        identity | {"process_create_time": 0},
    ]
    for value in invalid:
        assert not is_live(value)

    def absent(pid: int) -> None:
        raise psutil.NoSuchProcess(pid)

    monkeypatch.setattr(psutil, "Process", absent)
    assert not is_live(identity)


def test_nonblocking_file_ownership_releases_after_error(tmp_path: Path) -> None:
    path = tmp_path / "ownership.lock"
    with pytest.raises(ValueError, match="business failure"), file_lock(path):
        with pytest.raises(OSError), file_lock(path, blocking=False):
            raise AssertionError("exclusive acquisition unexpectedly succeeded")
        raise ValueError("business failure")
    with file_lock(path, blocking=False):
        assert path.exists()
    assert path.read_bytes() == b""


def test_lease_concurrency_exhaustion_recovery_and_idempotent_release(tmp_path: Path) -> None:
    def acquire(index: int) -> WorkerIdLease:
        return WorkerIdLease.acquire(state_dir=tmp_path, worker_id_base=8, worker_id_count=8)

    with ThreadPoolExecutor(max_workers=8) as executor:
        leases = list(executor.map(acquire, range(8)))
    assert {lease.worker_id for lease in leases} == set(range(8, 16))
    with pytest.raises(RuntimeError, match="all configured"):
        acquire(0)
    lease = leases.pop()
    lease.release()
    lease.release()
    replacement = acquire(0)
    assert replacement.worker_id == lease.worker_id
    atomic_write(replacement.path, replacement.identity | {"process_create_time": 0})
    replacement.release()
    assert replacement.path.exists()
    recovered = acquire(0)
    assert recovered.worker_id == replacement.worker_id
    recovered.release()
    for lease in leases:
        lease.release()
    assert not list((tmp_path / "leases").glob("*.json"))


@pytest.mark.parametrize("base,count", [(-1, 1), (1024, 1), (0, 0), (True, 1), (0, True)])
def test_lease_validates_snowflake_range(tmp_path: Path, base: int, count: int) -> None:
    with pytest.raises(ValueError, match="invalid Snowflake"):
        WorkerIdLease.acquire(state_dir=tmp_path, worker_id_base=base, worker_id_count=count)


def test_worker_observations_are_scoped_to_start_and_cleaned_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "service.lclcfg"
    source.write_text(CONFIG, encoding="utf-8")
    settings = asyncio.run(load_settings(source))
    monkeypatch.delenv("LCL_FASTAPI_STATE", raising=False)
    with pytest.raises(RuntimeError, match="no verified service"):
        with worker_runtime(settings):
            raise AssertionError("worker started without a master")
    with service_runtime(settings) as service:
        # Disable the Windows signal bridge; this test is the process being inspected.
        service["runtime"] = "gunicorn"
        atomic_write(settings.state_dir / "runtime.json", service)
        with pytest.raises(ValueError, match="business failure"):
            with worker_runtime(settings) as worker:
                worker.publish([str(tmp_path / "active.log")], 100)
                info = worker.service_info()
                assert info["gunicorn_pid"] == os.getpid()
                assert info["running_workers"] == 1
                records = live_workers(settings.worker_state_dir, service["service_id"])
                assert records[0]["snowflake_worker_id"] == worker.worker_id
                assert live_workers(settings.worker_state_dir, "another-start") == []
                atomic_write(settings.worker_state_dir / "dead.json", {"pid": -1})
                assert len(live_workers(settings.worker_state_dir, service["service_id"])) == 1
                atomic_write(
                    settings.state_dir / "runtime.json",
                    service | {"service_id": "changed"},
                )
                with pytest.raises(RuntimeError, match="identity changed"):
                    worker.request_shutdown()
                atomic_write(settings.state_dir / "runtime.json", service)
                raise ValueError("business failure")
        assert not list((settings.state_dir / "leases").glob("*.json"))
        assert not (settings.worker_state_dir / f"{os.getpid()}.json").exists()
    assert not (settings.state_dir / "control.token").exists()
    assert not (settings.worker_state_dir / "dead.json").exists()


def test_service_scope_preserves_another_start_identity(tmp_path: Path) -> None:
    source = tmp_path / "service.lclcfg"
    source.write_text(CONFIG, encoding="utf-8")
    settings = replace(asyncio.run(load_settings(source)), state_dir=tmp_path / "private")
    with service_runtime(settings):
        atomic_write(settings.state_dir / "runtime.json", {"service_id": "replacement"})
    assert read_state(settings.state_dir / "runtime.json") == {"service_id": "replacement"}


def test_inherited_service_scope_preserves_master_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "service.lclcfg"
    source.write_text(CONFIG, encoding="utf-8")
    settings = asyncio.run(load_settings(source))
    module = importlib.import_module("lcl_fastapi.runtime.service")
    with service_runtime(settings) as identity:
        token = (settings.state_dir / "control.token").read_bytes()
        monkeypatch.setattr(
            module, "os", SimpleNamespace(name=os.name, getpid=lambda: os.getpid() + 1)
        )
    assert read_state(settings.state_dir / "runtime.json") == identity
    assert (settings.state_dir / "control.token").read_bytes() == token
    assert settings.pid_file.read_text() == str(identity["pid"])


@pytest.mark.parametrize("child_exit", [False, True])
def test_posix_lock_only_acquiring_process_unlocks(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, child_exit: bool
) -> None:
    module = importlib.import_module("lcl_fastapi.runtime.state")
    pid = [100]
    calls: list[tuple[int, int]] = []
    native = SimpleNamespace(
        LOCK_EX=2, LOCK_NB=4, LOCK_UN=8, flock=lambda fd, mode: calls.append((fd, mode))
    )
    monkeypatch.setitem(sys.modules, "fcntl", native)
    monkeypatch.setattr(module, "sys", SimpleNamespace(platform="linux"))
    monkeypatch.setattr(module, "os", SimpleNamespace(getpid=lambda: pid[0]))
    with file_lock(tmp_path / "owner.lock", blocking=False):
        if child_exit:
            pid[0] += 1
    assert [mode for _, mode in calls] == ([6] if child_exit else [6, 8])
    with pytest.raises(OSError):
        os.fstat(calls[0][0])


@pytest.mark.parametrize(
    "platform, failures, succeeds",
    [
        ("win32", 1, True),
        ("win32", 3, False),
        ("linux", 1, False),
    ],
)
def test_state_read_handles_only_bounded_windows_sharing_conflicts(
    monkeypatch: pytest.MonkeyPatch,
    platform: str,
    failures: int,
    succeeds: bool,
) -> None:
    from types import SimpleNamespace

    from lcl_fastapi.runtime import state

    calls = 0
    sleeps: list[float] = []

    def read(path: Path, **kwargs: object) -> str:
        nonlocal calls
        calls += 1
        if calls <= failures:
            raise PermissionError("state replacement sharing conflict")
        return '{"pid": 123}'

    monkeypatch.setattr(state, "sys", SimpleNamespace(platform=platform))
    monkeypatch.setattr(Path, "read_text", read)
    monkeypatch.setattr("time.sleep", sleeps.append)
    if succeeds:
        assert state.read_state(Path("worker.json")) == {"pid": 123}
        assert calls == 2 and sleeps == [0.01]
    else:
        with pytest.raises(PermissionError, match="sharing conflict"):
            state.read_state(Path("worker.json"))
        assert calls == (3 if platform == "win32" else 1)
