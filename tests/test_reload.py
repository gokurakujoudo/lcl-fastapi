"""Validate explicit watch scope, serialized retirement, and startup overrides."""

import asyncio
import os
import signal
from collections.abc import Callable, Generator
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

import psutil
import pytest
from watchfiles import Change

from lcl_fastapi.config import load_settings
from lcl_fastapi.runtime import reload
from lcl_fastapi.runtime.common import serve
from lcl_fastapi.runtime.reload import ReloadWatcher
from lcl_fastapi.runtime.startup import startup_settings
from lcl_fastapi.runtime.state import read_state

CONFIG = """__LCL_VERSION__: 1
app.name: "reload-test"
app.version: "1"
app.target: "app:service"
"""


@pytest.fixture
def source(tmp_path: Path) -> Path:
    path = tmp_path / "service.lclcfg"
    path.write_text(CONFIG, encoding="utf-8")
    return path


@pytest.mark.parametrize("value", ["[]", '"src"', '[""]', "[42]"])
def test_invalid_roots(source: Path, value: str) -> None:
    source.write_text(CONFIG + f"server.reload_dirs: {value}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="server.reload_dirs"):
        asyncio.run(load_settings(source))


def test_roots_are_config_relative_deduplicated_and_replace_defaults(source: Path) -> None:
    assert asyncio.run(load_settings(source)).reload_dirs == (source.parent,)
    absolute = source.parent / "shared"
    source.write_text(
        CONFIG + f'server.reload_dirs: ["src", "./src", {absolute.as_posix()!r}]\n',
        encoding="utf-8",
    )
    assert asyncio.run(load_settings(source)).reload_dirs == (source.parent / "src", absolute)


@pytest.mark.parametrize("workers, enabled", [(1, False), (2, False), (1, True), (2, True)])
def test_effective_workers_and_warning(
    source: Path, workers: int, enabled: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    source.write_text(CONFIG + f"server.workers: {workers}\n", encoding="utf-8")
    (source.parent / "app.py").write_text(
        "from lcl_fastapi import LclFastAPI\nservice = LclFastAPI()\n"
    )
    original = source.read_bytes()
    settings = asyncio.run(startup_settings(source, enabled))
    assert settings.workers == (1 if enabled else workers)
    assert source.read_bytes() == original
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err.count("has been forced to 1") == int(enabled and workers > 1)


def test_runtime_rejects_nonboolean(source: Path) -> None:
    with pytest.raises(ValueError, match="Boolean"):
        serve(source, hot_reload=cast(bool, 1))


def test_missing_file_and_inaccessible_roots(source: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = asyncio.run(load_settings(source))
    for path in [source, source.parent / "missing"]:
        with pytest.raises(NotADirectoryError, match="server.reload_dirs"):
            ReloadWatcher(replace(settings, reload_dirs=(path,)), {})
    monkeypatch.setattr(os, "scandir", Mock(side_effect=PermissionError("denied")))
    with pytest.raises(PermissionError, match="denied"):
        ReloadWatcher(settings, {})


@pytest.fixture
def watcher(source: Path, monkeypatch: pytest.MonkeyPatch) -> Generator[ReloadWatcher]:
    settings = asyncio.run(load_settings(source))
    monkeypatch.setattr(reload, "shutdown_requested", lambda directory, identity: False)
    result = ReloadWatcher(settings, {"service_id": "start", "runtime": "uvicorn"})
    yield result
    result.close()


def test_python_filter_resolves_exact_roots(watcher: ReloadWatcher, source: Path) -> None:
    for change in Change:
        assert watcher.accept_change(change, str(source.parent / "nested" / "new.py"))
        assert not watcher.accept_change(change, str(source))
        assert not watcher.accept_change(change, str(source.parent.parent / "outside.py"))


def batches(watcher: ReloadWatcher, *values: bool) -> Generator[set[tuple[Change, str]]]:
    for value in values:
        yield (
            {(Change.modified, str(watcher.settings.reload_dirs[0] / "app.py"))} if value else set()
        )


def test_retirement_coalesces_changes_and_waits_for_replacement(
    watcher: ReloadWatcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = {"pid": 100, "process_create_time": 1, "service_id": "start"}
    second = first | {"pid": 101}
    workers = [first]
    live = True
    monkeypatch.setattr(reload, "live_workers", lambda directory, identity: workers)
    monkeypatch.setattr(reload, "is_live", lambda identity: live)
    watcher.changes = batches(watcher, False, True, True, False, False)
    watcher.tick()
    assert watcher.retiring is None
    watcher.tick()
    assert read_state(watcher.settings.state_dir / "reload.json") == first
    watcher.tick()
    assert watcher.pending and watcher.retiring == first
    live = False
    workers.clear()
    watcher.tick()
    assert watcher.pending
    workers.append(second)
    watcher.tick()
    assert not watcher.pending and watcher.retiring == second
    assert read_state(watcher.settings.state_dir / "reload.json") == second


def test_shutdown_and_fork_ownership(
    watcher: ReloadWatcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    watcher.changes = batches(watcher, True)
    monkeypatch.setattr(os, "getpid", lambda: watcher.owner + 1)
    watcher.tick()
    watcher.close()
    monkeypatch.setattr(os, "getpid", lambda: watcher.owner)
    monkeypatch.setattr(reload, "shutdown_requested", lambda directory, identity: True)
    watcher.tick()
    assert not watcher.pending
    assert next(watcher.changes)


def test_shutdown_wins_after_event_batch(
    watcher: ReloadWatcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    checks = iter([False, True])
    monkeypatch.setattr(reload, "shutdown_requested", lambda directory, identity: next(checks))
    monkeypatch.setattr(reload, "live_workers", lambda directory, identity: [{"pid": 123}])
    watcher.changes = batches(watcher, True)
    watcher.tick()
    assert watcher.pending and watcher.retiring is None


@pytest.mark.parametrize("outcome", ["sent", "reused", "gone", "denied"])
def test_linux_retirement_checks_process_identity(
    watcher: ReloadWatcher, monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    watcher.identity["runtime"] = "gunicorn"
    watcher.changes = batches(watcher, True)
    worker = {"pid": 123, "process_create_time": 1, "service_id": "start"}
    monkeypatch.setattr(reload, "live_workers", lambda directory, identity: [worker])
    process = Mock()
    process.create_time.return_value = 2 if outcome == "reused" else 1
    if outcome in {"gone", "denied"}:
        process.send_signal.side_effect = (
            psutil.NoSuchProcess(123) if outcome == "gone" else psutil.AccessDenied(123)
        )
    monkeypatch.setattr(psutil, "Process", lambda pid: process)
    if outcome == "denied":
        with pytest.raises(psutil.AccessDenied):
            watcher.tick()
    else:
        watcher.tick()
    assert watcher.pending == (outcome != "sent")
    if outcome == "sent":
        process.send_signal.assert_called_once_with(signal.SIGTERM)


def test_watcher_termination_and_native_errors_escape(watcher: ReloadWatcher) -> None:
    watcher.changes = batches(
        watcher,
    )
    with pytest.raises(RuntimeError, match="stopped unexpectedly"):
        watcher.tick()


def test_ignored_batch_does_not_block_manager(
    watcher: ReloadWatcher, monkeypatch: pytest.MonkeyPatch
) -> None:
    def changes() -> Generator[set[tuple[Change, str]]]:
        yield {(Change.modified, str(watcher.settings.state_dir / "workers/123.json"))}
        raise OSError("watch failed")

    watcher.changes = changes()
    monkeypatch.setattr(reload, "live_workers", lambda directory, identity: [])
    watcher.tick()
    assert not watcher.pending
    with pytest.raises(OSError, match="watch failed"):
        watcher.tick()


@pytest.mark.parametrize(
    "enabled, outcome", [(False, "idle"), (True, "idle"), (True, "signal"), (True, "error")]
)
def test_gunicorn_watch_adapter_preserves_signals_and_cleanup(
    source: Path, monkeypatch: pytest.MonkeyPatch, enabled: bool, outcome: str
) -> None:
    from lcl_fastapi.runtime import linux

    settings = asyncio.run(load_settings(source))
    watcher = Mock(spec=ReloadWatcher)
    if outcome == "error":
        watcher.tick.side_effect = OSError("watch failed")
    events: list[str] = []
    signals = [signal.SIGTERM] if outcome == "signal" else []
    native_wait = Mock(return_value=signals)

    class Runner:
        def __init__(self) -> None:
            self.spawn_worker: Callable[[], object] = lambda: None
            self.wait_for_signals = native_wait
            self.stop = lambda graceful: events.append(f"stop {graceful}")
            self.reap_workers: Callable[[], None] = lambda: None
            self.kill_workers: Callable[[int], None] = lambda sig: None

        def run(self) -> None:
            assert self.spawn_worker() is None
            self.reap_workers()
            self.kill_workers(9)
            self.kill_workers(signal.SIGTERM)
            assert self.wait_for_signals(timeout=1.0) == signals

    runner = Runner()
    monkeypatch.setattr(linux, "GunicornApplication", lambda settings: object())
    monkeypatch.setattr(linux, "ReloadWatcher", lambda settings, identity: watcher)
    monkeypatch.setattr(
        linux,
        "importlib",
        SimpleNamespace(import_module=lambda name: SimpleNamespace(Arbiter=lambda app: runner)),
    )
    if outcome == "error":
        with pytest.raises(OSError, match="watch failed"):
            linux.run_linux(settings, {}, hot_reload=enabled)
        assert events == ["stop True"]
    else:
        linux.run_linux(settings, {}, hot_reload=enabled)
        assert not events
    assert watcher.tick.call_count == int(enabled and outcome != "signal")
    assert watcher.close.call_count == int(enabled)


def test_windows_watcher_error_joins_workers_and_releases_listener(
    source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lcl_fastapi.runtime import windows

    settings = asyncio.run(load_settings(source))
    listener = Mock()
    monkeypatch.setattr(
        windows, "Config", Mock(return_value=SimpleNamespace(bind_socket=lambda: listener))
    )
    manager = Mock()
    manager.run.side_effect = OSError("watch failed")
    monkeypatch.setattr(windows, "ServiceMultiprocess", Mock(return_value=manager))
    watcher = Mock()
    monkeypatch.setattr(windows, "ReloadWatcher", Mock(return_value=watcher))
    with pytest.raises(OSError, match="watch failed"):
        windows.run_windows(settings, {"service_id": "start"}, hot_reload=True)
    manager.terminate_all.assert_called_once()
    manager.join_all.assert_called_once()
    watcher.close.assert_called_once()
    listener.close.assert_called_once()


def test_serve_passes_reload_and_effective_settings(
    source: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from lcl_fastapi.runtime import common, linux, windows

    calls: list[tuple[int, bool]] = []
    for platform, module in [("linux", linux), ("win32", windows)]:
        monkeypatch.setattr(common, "sys", SimpleNamespace(platform=platform))
        monkeypatch.setattr(
            module,
            "run_linux" if platform == "linux" else "run_windows",
            lambda settings, identity, hot_reload: calls.append((settings.workers, hot_reload)),
        )
        common.serve(source, hot_reload=True)
    assert calls == [(1, True), (1, True)]
