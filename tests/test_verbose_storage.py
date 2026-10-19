"""Prove control state identity, concurrent updates, refresh and observation boundaries."""

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from lcl_fastapi import FileVerboseState
from lcl_fastapi.runtime.state import atomic_write, process_identity, read_state
from lcl_fastapi.verbose import storage
from lcl_fastapi.verbose.control import FileVerboseControl, current_file_control, use_file_control
from lcl_fastapi.verbose.storage import process_observations, read_target, write_target


@pytest.mark.parametrize("enabled,revision", [(1, 0), (False, True), (True, -1)])
def test_public_state_rejects_invalid_values(enabled: Any, revision: Any) -> None:
    with pytest.raises(ValueError, match="Boolean"):
        FileVerboseState(enabled, revision)


@pytest.mark.parametrize(
    "record",
    [
        {},
        {"service_id": "old", "enabled": False, "revision": 0},
        {"service_id": "current", "enabled": 1, "revision": 0},
        {"service_id": "current", "enabled": True, "revision": -1},
        {"service_id": "current", "enabled": True, "revision": False},
    ],
)
def test_invalid_shared_state_cannot_initialize_new_process(
    tmp_path: Path, record: dict[str, object]
) -> None:
    atomic_write(tmp_path / "logging-control.json", record)
    with pytest.raises(OSError, match="unavailable"):
        FileVerboseControl(tmp_path, "current", "api")


def test_updates_are_serialized_idempotent_and_keep_last_valid_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_target(tmp_path, "current", FileVerboseState(False, 0))
    first = FileVerboseControl(tmp_path, "current", "api")
    second = FileVerboseControl(tmp_path, "current", "api")
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda enabled: first.update(enabled), [True] * 12))
    assert all(state == FileVerboseState(True, 1) for state in results)
    assert second.snapshot() == FileVerboseState(False, 0)
    second.refresh()
    assert second.snapshot() == FileVerboseState(True, 1)
    second.observe(FileVerboseState(False, 0))
    assert second.snapshot() == FileVerboseState(True, 1)
    assert second.status().processes[0].revision == 1
    (tmp_path / "logging-control.json").write_text("invalid", encoding="utf-8")
    with pytest.raises(OSError):
        second.refresh()
    assert second.snapshot() == FileVerboseState(True, 1)
    write_target(tmp_path, "current", FileVerboseState(True, 1))
    original = (tmp_path / "logging-control.json").read_bytes()

    def fail(path: Path, value: dict[str, object]) -> None:
        raise OSError("injected storage failure")

    monkeypatch.setattr(storage, "atomic_write", fail)
    with pytest.raises(OSError, match="injected"):
        first.update(False)
    assert first.snapshot() == FileVerboseState(True, 1)
    assert (tmp_path / "logging-control.json").read_bytes() == original
    with use_file_control(first):
        assert current_file_control() is first
        first.identity["pid"] = -1
        with pytest.raises(RuntimeError, match="active service"):
            current_file_control()


def test_process_observations_ignore_stale_start_dead_and_invalid_records(tmp_path: Path) -> None:
    baseline = process_identity() | {
        "service_id": "current",
        "role": "controller",
        "enabled": True,
        "revision": 2,
        "observed_at": 123.0,
    }
    directory = tmp_path / "logging-observations"
    changes_list: tuple[dict[str, object], ...] = (
        {},
        {"service_id": "old"},
        {"pid": -1},
        {"role": "unknown"},
        {"enabled": 1},
        {"revision": -1},
        {"revision": None},
        {"observed_at": "wrong"},
    )
    for index, changes in enumerate(changes_list):
        atomic_write(directory / f"{index}.json", baseline | changes)
    processes = process_observations(tmp_path, "current")
    assert len(processes) == 1
    assert processes[0].role == "controller" and processes[0].enabled is True
    assert processes[0].revision == 2 and processes[0].observed_at == 123.0


def test_target_round_trip_keeps_service_identity_and_validates_start(tmp_path: Path) -> None:
    write_target(tmp_path, "current", FileVerboseState(True, 3))
    assert read_target(tmp_path, "current") == FileVerboseState(True, 3)
    assert read_state(tmp_path / "logging-control.json")["service_id"] == "current"
    with pytest.raises(OSError):
        read_target(tmp_path, "old")
    assert json.loads((tmp_path / "logging-control.json").read_text())["revision"] == 3


def test_refresh_reports_only_changed_failures_and_rejects_revision_rollback(
    tmp_path: Path,
) -> None:
    write_target(tmp_path, "current", FileVerboseState(False, 0))
    control = FileVerboseControl(tmp_path, "current", "api")
    assert control.update(True) == FileVerboseState(True, 1)
    write_target(tmp_path, "current", FileVerboseState(False, 0))
    with pytest.raises(OSError, match="backwards"):
        control.update(False)
    assert control.snapshot() == FileVerboseState(True, 1)
    (tmp_path / "logging-control.json").write_text("invalid", encoding="utf-8")
    assert "file verbose refresh failed" in control.refresh_warning()
    assert control.refresh_warning() == ""
    write_target(tmp_path, "current", FileVerboseState(True, 1))
    assert control.refresh_warning() == "" and control.last_error == ""
