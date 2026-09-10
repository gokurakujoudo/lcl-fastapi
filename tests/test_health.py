"""Exercise unavailable metrics and deterministic sampler lifecycle behavior."""

import asyncio
import socket
from pathlib import Path
from typing import cast

import psutil
import pytest

import lcl_fastapi.health.sampler as sampler_module
from lcl_fastapi.health.sampler import HealthSampler, system_snapshot
from lcl_fastapi.logging import RequestLogger


class WarningRecorder:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def warning(self, message: str, *args: object) -> None:
        self.messages.append(message % args)


def test_unavailable_metrics_are_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    def unavailable(*args: object, **kwargs: object) -> object:
        raise psutil.AccessDenied(pid=1)

    def missing_hostname() -> str:
        raise OSError("hostname unavailable")

    monkeypatch.setattr(socket, "gethostname", missing_hostname)
    monkeypatch.setattr(psutil, "cpu_count", unavailable)
    monkeypatch.setattr(psutil, "virtual_memory", unavailable)
    monkeypatch.setattr(psutil, "disk_usage", unavailable)
    snapshot = system_snapshot((Path("missing"),))
    for field in ("hostname", "cpu", "memory"):
        assert cast(dict[str, object], snapshot[field])["status"] == "unavailable"
    disks = cast(list[dict[str, object]], snapshot["disk"])
    assert disks[0]["status"] == "unavailable" and disks[0]["path"] == "missing"


@pytest.mark.parametrize("interval", [0.0, -1.0, float("nan"), float("inf")])
def test_sampler_rejects_invalid_interval(interval: float) -> None:
    with pytest.raises(ValueError, match="positive and finite"):
        HealthSampler((), interval, cast(RequestLogger, WarningRecorder()), lambda observed: None)


async def test_sampler_warns_on_changed_failures_only(monkeypatch: pytest.MonkeyPatch) -> None:
    snapshots = iter(
        [
            {"cpu": {"usage_percent": 1}, "disk": [{"status": "unavailable", "error": "denied"}]},
            {"cpu": {"usage_percent": 2}, "disk": [{"status": "unavailable", "error": "denied"}]},
            {"cpu": {"usage_percent": 3}, "disk": []},
            {"cpu": {"usage_percent": 4}, "disk": [{"status": "unavailable", "error": "denied"}]},
        ]
    )
    monkeypatch.setattr(sampler_module, "system_snapshot", lambda paths: next(snapshots))
    recorder = WarningRecorder()
    sampler = HealthSampler((), 1.0, cast(RequestLogger, recorder), lambda observed: None)
    await sampler.refresh()
    await sampler.refresh()
    assert len(recorder.messages) == 1
    detached = sampler.snapshot()
    detached["cpu"] = None
    assert sampler.snapshot()["cpu"] is not None
    await sampler.refresh()
    await sampler.refresh()
    assert len(recorder.messages) == 2


async def test_sampler_start_stop_and_publication_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sampler_module, "system_snapshot", lambda paths: {"cpu": {}})
    failure_observed = asyncio.Event()
    recorder = WarningRecorder()
    observations: list[float] = []

    def publish(observed_at: float) -> None:
        observations.append(observed_at)
        if len(observations) > 1:
            failure_observed.set()
            raise OSError("state unavailable")

    sampler = HealthSampler((), 0.001, cast(RequestLogger, recorder), publish)
    assert sampler.snapshot() == {"status": "unavailable"}
    await sampler.start()
    with pytest.raises(RuntimeError, match="already started"):
        await sampler.start()
    await asyncio.wait_for(failure_observed.wait(), timeout=1)
    await sampler.stop()
    await sampler.stop()
    assert recorder.messages == ["worker state publication failed: state unavailable"]
    assert sampler.task is None
