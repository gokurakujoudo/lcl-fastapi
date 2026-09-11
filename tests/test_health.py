"""Exercise unavailable metrics and deterministic sampler lifecycle behavior."""

import asyncio
import socket
import threading
from pathlib import Path
from typing import cast

import psutil
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import lcl_fastapi.health.sampler as sampler_module
from lcl_fastapi.health.sampler import HealthSampler, system_snapshot
from lcl_fastapi.logging import RequestLogger


class WarningRecorder:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def warning(self, message: str, *args: object) -> None:
        self.messages.append(message % args)

    def exception(self, message: str) -> None:
        self.messages.append(message)


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

    def warning(message: str, *args: object) -> None:
        recorder.messages.append(message % args)
        failure_observed.set()

    monkeypatch.setattr(recorder, "warning", warning)

    def publish(observed_at: float) -> None:
        observations.append(observed_at)
        if len(observations) > 1:
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


async def test_sampler_recovers_after_one_sharing_violation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sampler_module, "system_snapshot", lambda paths: {"cpu": {}})
    recovered = asyncio.Event()
    loop = asyncio.get_running_loop()
    recorder = WarningRecorder()
    attempts: list[float] = []
    published: list[float] = []

    def publish(observed_at: float) -> None:
        attempts.append(observed_at)
        if len(attempts) == 2:
            raise PermissionError("state replacement sharing violation")
        published.append(observed_at)
        if len(published) == 2:
            loop.call_soon_threadsafe(recovered.set)

    sampler = HealthSampler((), 0.001, cast(RequestLogger, recorder), publish)
    await sampler.start()
    try:
        await asyncio.wait_for(recovered.wait(), timeout=1)
        assert sampler.task is not None and not sampler.task.done()
    finally:
        await sampler.stop()
    assert published == [attempts[0], attempts[2]]
    assert recorder.messages == [
        "worker state publication failed: state replacement sharing violation"
    ]


@pytest.mark.parametrize("write_fails", [False, True])
async def test_publication_keeps_http_responsive_and_joins_before_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    write_fails: bool,
) -> None:
    monkeypatch.setattr(sampler_module, "system_snapshot", lambda paths: {"cpu": {}})
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    main_thread = threading.get_ident()
    writes: list[float] = []
    app = FastAPI()

    @app.get("/ready")
    async def ready() -> dict[str, bool]:
        return {"ready": True}

    def publish(observed_at: float) -> None:
        if not writes:
            writes.append(observed_at)
            return
        entered.set()
        assert threading.get_ident() != main_thread, "publication must not block the event loop"
        assert release.wait(timeout=5), "test must release the owned publication"
        writes.append(observed_at)
        finished.set()
        if write_fails:
            raise OSError("write failed while stopping")

    recorder = WarningRecorder()
    sampler = HealthSampler((), 0.001, cast(RequestLogger, recorder), publish)
    await sampler.start()
    refresh = sampler.task
    assert refresh is not None
    closing: asyncio.Task[None] | None = None
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        if refresh.done():
            await refresh
        async with AsyncClient(transport=ASGITransport(app), base_url="http://test") as client:
            response = await client.get("/ready")
        assert response.json() == {"ready": True}
        assert not finished.is_set()
        closing = asyncio.create_task(sampler.stop())
        await asyncio.sleep(0)
        assert not refresh.done(), "cancellation must wait for the in-flight write"
        refresh.cancel()
        await asyncio.sleep(0)
        assert not refresh.done(), "repeated cancellation must still join the write"
        release.set()
        await asyncio.wait_for(closing, timeout=1)
        assert refresh.cancelled() and sampler.task is None
        assert finished.is_set() and len(writes) == 2
        assert recorder.messages == (
            ["worker state publication failed during cancellation"] if write_fails else []
        )
    finally:
        release.set()
        if not refresh.done():
            refresh.cancel()
        await asyncio.gather(refresh, return_exceptions=True)
        if closing is not None:
            await closing
