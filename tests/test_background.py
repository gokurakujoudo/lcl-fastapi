"""Exercise code-registered background threads, resources, logs and lifecycle."""

import asyncio
import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import cast

import httpx
import pytest
from fastapi import FastAPI
from test_application import ObservedRuntime
from test_application import configuration as configuration
from test_application import observed_runtime as observed_runtime

from lcl_fastapi import (
    BackgroundWorker,
    BackgroundWorkerContext,
    LclFastAPI,
    get_config,
    get_logger,
    get_request_context,
    use_lcl_frame,
)
from lcl_fastapi.background.config import worker_policies, worker_registry
from lcl_fastapi.background.journal import BackgroundEvents
from lcl_fastapi.runtime.state import process_identity
from lcl_fastapi.sources import configuration_frame


@pytest.fixture
def background_runtime(configuration: Path, observed_runtime: ObservedRuntime) -> ObservedRuntime:
    observed_runtime.directory = configuration.parent / "run"
    observed_runtime.identity = process_identity()
    observed_runtime.service = {"configured_workers": 1, "service_id": "test"}
    return observed_runtime


async def wait_event(event: threading.Event) -> None:
    assert await asyncio.to_thread(event.wait, 5), "background worker did not signal readiness"


async def test_sync_async_resources_frames_logs_and_shutdown(
    configuration: Path, background_runtime: ObservedRuntime
) -> None:
    ready, async_ready, closed = threading.Event(), threading.Event(), threading.Event()
    api_thread = threading.get_ident()
    calls: list[str] = []

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[dict[str, object]]:
        app.state.resource = "open"
        yield {"resource": "yielded"}
        assert closed.is_set()
        app.state.resource = "closed"

    async def on_service() -> str:
        assert threading.get_ident() == api_thread
        assert await get_config("business.value") == "configured"
        assert get_request_context() is None
        return str(app.state.resource)

    async def local_scope(context: BackgroundWorkerContext) -> None:
        async with use_lcl_frame(values={"business.value": "background"}):
            assert await context.get_config("business.value") == "background"
            assert await asyncio.wrap_future(context.submit_to_service(on_service)) == "open"

    def synchronous(context: BackgroundWorkerContext) -> None:
        assert threading.get_ident() != api_thread
        assert context.state == {"resource": "yielded"}
        assert context.run(context.get_config("business.value")) == "configured"
        context.run(local_scope(context))
        assert context.submit_to_service(on_service).result(timeout=5) == "open"
        context.logger.info("sync worker record")
        calls.append("sync")
        ready.set()
        context.stop_event.wait(5)
        closed.set()

    async def asynchronous(context: BackgroundWorkerContext) -> None:
        assert threading.get_ident() != api_thread
        assert get_request_context() is None
        await local_scope(context)
        closed_loop = asyncio.new_event_loop()
        closed_loop.close()
        with pytest.raises(RuntimeError, match="closed"):
            replace(context, service_loop=closed_loop).submit_to_service(on_service)
        logger = await get_logger("nested")
        logger.info("async worker record")
        calls.append("async")
        async_ready.set()
        await asyncio.Event().wait()

    app = LclFastAPI(
        config_path=configuration,
        lifespan=lifespan,
        background_workers={"sync_job": synchronous, "async_job": asynchronous},
    )

    @app.get("/scope/{value}")
    async def scoped(value: str) -> dict[str, object]:
        async with use_lcl_frame(values={"business.value": value}):
            await asyncio.sleep(0)
            assert await get_config("business.value") == value
            if value == "failure":
                raise ValueError("composed route failure")
        return {"restored": await get_config("business.value")}

    original_threads = set(threading.enumerate())
    async with app.router.lifespan_context(app):
        await wait_event(ready)
        await wait_event(async_ready)
        assert await get_config("business.value") == "configured"
        transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            response = await client.get("/health")
            assert response.status_code == 200
            assert response.json()["service"]["background_workers"]["sync_job"]["attempts"] == 1
            replies = await asyncio.gather(
                *(client.get(f"/scope/{value}") for value in ["one", "two", "failure"])
            )
            assert [reply.status_code for reply in replies] == [200, 200, 500]
            assert replies[0].json() == replies[1].json() == {"restored": "configured"}
            assert replies[2].json() == {"detail": "Internal Server Error"}
        (await get_logger("api")).info("api-only record")
    assert sorted(calls) == ["async", "sync"]
    assert not any(
        t.name.startswith("lcl-background") for t in set(threading.enumerate()) - original_threads
    )
    assert app.state.resource == "closed"
    logs = configuration.parent / "logs"
    sync_logs = "".join(p.read_text() for p in logs.glob("*.sync_job.*.log"))
    async_logs = "".join(p.read_text() for p in logs.glob("*.async_job.*.log"))
    assert "sync worker record" in sync_logs and "async worker record" not in sync_logs
    assert "async worker record" in async_logs and "sync_job sync worker record" not in async_logs
    assert "api-only record" not in sync_logs + async_logs
    assert "composed route failure" not in sync_logs + async_logs
    assert "composed route failure" in "".join(p.read_text() for p in logs.glob("*.log"))
    events = BackgroundEvents(configuration.parent / "run", "test").read()
    assert any("cancelled" in message for _, message in events)
    assert events[-1] == (logging.INFO, "background retired")


async def test_background_rotation_updates_observed_actual_path(
    configuration: Path, background_runtime: ObservedRuntime
) -> None:
    with configuration.open("a") as file:
        file.write(
            'logger.file.job.rotation.mode: "size"\n'
            "logger.file.job.rotation.max_bytes: 1\nhealth.sample_interval_seconds: 0.01\n"
        )
    emitted = threading.Event()

    def job(context: BackgroundWorkerContext) -> None:
        for number in range(3):
            context.logger.info("rotation record %s", number)
        emitted.set()
        context.stop_event.wait(5)

    app = LclFastAPI(config_path=configuration, background_workers={"job": job})
    async with app.router.lifespan_context(app):
        await wait_event(emitted)
        (await get_logger("api")).info("API rotation isolation")
        async with asyncio.timeout(5):
            while True:
                paths = [
                    Path(path)
                    for path in background_runtime.observations[-1][0]
                    if ".job." in Path(path).name
                ]
                if paths and "rotation record 2" in paths[0].read_text():
                    break
                await asyncio.sleep(0.01)
        segments = list((configuration.parent / "logs").glob("*.job.*.log"))
        assert len(segments) == 3
        assert any(paths[0].samefile(segment) for segment in segments)
    contents = "".join(path.read_text() for path in segments)
    assert all(f"rotation record {number}" in contents for number in range(3))
    assert "API rotation isolation" not in contents


@pytest.mark.parametrize("failed", [False, True])
async def test_restart_policy_and_controller_event_levels(
    configuration: Path, background_runtime: ObservedRuntime, failed: bool
) -> None:
    second = threading.Event()
    count = 0

    def job(context: BackgroundWorkerContext) -> None:
        nonlocal count
        count += 1
        if count == 2:
            second.set()
            context.stop_event.wait(5)
        if failed:
            raise ValueError("restart probe")

    app = LclFastAPI(config_path=configuration, background_workers={"job": job})
    async with app.router.lifespan_context(app):
        await wait_event(second)
        assert app.background_manager is not None
        assert app.background_manager.snapshot()["job"] == {
            "status": "running",
            "attempts": 2,
            "restarts": 1,
        }
    events = BackgroundEvents(configuration.parent / "run", "test").read()
    restarts = [(level, text) for level, text in events if "restarting after exit" in text]
    assert len(restarts) == 1
    assert restarts[0][0] == (logging.WARNING if failed else logging.INFO)
    if failed:
        assert any(
            level == logging.ERROR and "exception=ValueError" in text for level, text in events
        )


async def test_disabled_and_no_restart_are_observable(
    configuration: Path, background_runtime: ObservedRuntime
) -> None:
    with configuration.open("a") as file:
        file.write(
            "background_worker.default.auto_restart: False\nbackground_worker.off.enabled: False\n"
        )
    done = threading.Event()

    def once(context: BackgroundWorkerContext) -> None:
        done.set()

    def disabled(context: BackgroundWorkerContext) -> None:
        pytest.fail("disabled executable must not run")

    app = LclFastAPI(config_path=configuration, background_workers={"once": once, "off": disabled})
    async with app.router.lifespan_context(app):
        await wait_event(done)
        assert app.background_manager is not None
        await asyncio.wrap_future(app.background_manager.workers["once"].done)
        snapshot = app.background_manager.snapshot()
        assert snapshot["off"] == {"status": "disabled", "attempts": 0, "restarts": 0}
        assert snapshot["once"] == {"status": "completed", "attempts": 1, "restarts": 0}
    assert not list((configuration.parent / "logs").glob("*off*.log"))


@pytest.mark.parametrize("name", ["default", "controller", "service", "bad.name", "", 1])
def test_registration_rejects_names(name: object) -> None:
    with pytest.raises(ValueError, match="name"):
        worker_registry({cast(str, name): lambda context: None})


def test_registration_copies_and_rejects_noncallables() -> None:
    original: dict[str, BackgroundWorker] = {"job": lambda context: None}
    copied = worker_registry(original)
    original.clear()
    assert "job" in copied
    with pytest.raises(TypeError, match="callable"):
        worker_registry({"job": cast(BackgroundWorker, "not executable")})


@pytest.mark.parametrize("mode", ["cancel", "stop", "error"])
async def test_service_cleanup_precedes_restart_and_teardown(
    configuration: Path, background_runtime: ObservedRuntime, mode: str
) -> None:
    cleaning, release, shutdown = (asyncio.Event() for _ in range(3))
    started = threading.Event()
    entered = threading.Event()
    closed = False
    attempts = 0

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nonlocal closed
        yield
        closed = True

    async def operation() -> None:
        started.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleaning.set()
            await release.wait()
            assert not closed

    async def job(context: BackgroundWorkerContext) -> None:
        nonlocal attempts
        attempts += 1
        future = context.submit_to_service(operation)
        entered.set()
        if mode != "stop":
            assert await asyncio.to_thread(started.wait, 5)
            if mode == "error":
                raise ValueError("entry failed with outstanding API work")
            future.cancel()
        await asyncio.wrap_future(future)

    app = LclFastAPI(config_path=configuration, lifespan=lifespan, background_workers={"job": job})

    async def serve() -> None:
        async with app.router.lifespan_context(app):
            await shutdown.wait()

    service = asyncio.create_task(serve())
    try:
        await wait_event(entered)
        await wait_event(started)
        if mode == "stop":
            shutdown.set()
        await asyncio.wait_for(cleaning.wait(), 5)
        await asyncio.sleep(0.05 if mode == "stop" else 1.2)
        assert not closed, "business teardown overtook API Task cleanup"
        assert attempts == 1, "restart overtook previous API Task cleanup"
    finally:
        shutdown.set()
        release.set()
        await asyncio.wait_for(service, 5)
    assert closed


async def test_config_defaults_overrides_nested_and_invalid(configuration: Path) -> None:
    with configuration.open("a") as file:
        file.write("background_worker.job.enabled: False\nbackground_worker.job.nested.value: 42\n")
    async with configuration_frame(configuration) as frame:
        policy = (await worker_policies(frame, {"job": lambda ctx: None}))["job"]
        assert policy.enabled is False and policy.auto_restart is True
        assert await frame.get("background_worker.job.nested.value") == 42
    with configuration.open("a") as file:
        file.write("background_worker.job.enabled: 1\n")
    async with configuration_frame(configuration) as frame:
        with pytest.raises(ValueError, match="Boolean"):
            await worker_policies(frame, {"job": lambda ctx: None})
