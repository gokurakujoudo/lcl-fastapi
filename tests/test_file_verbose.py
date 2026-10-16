"""Control file diagnostics through real HTTP requests and pinned execution scopes."""

import asyncio
import logging
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from lclang.logger import use_verbose
from starlette.responses import StreamingResponse
from test_application import ObservedRuntime
from test_application import configuration as configuration
from test_application import observed_runtime as observed_runtime

from lcl_fastapi import LclFastAPI, get_config, get_file_verbose, get_logger, set_file_verbose

TOKEN = {"X-LCL-Control-Token": "test-secret"}
CONTROL = "/_lcl/logging/verbose"


def content(configuration: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8") for path in configuration.parent.glob("logs/*.log")
    )


async def test_http_control_validates_authorization_types_and_file_diagnostics(
    configuration: Path, observed_runtime: ObservedRuntime
) -> None:
    with configuration.open("a", encoding="utf-8") as stream:
        stream.write("business.enabled: 6 * 7\nbusiness.disabled: 10 + 2\nbusiness.muted: 3 + 4\n")
    app = LclFastAPI(config_path=configuration)

    @app.get("/value/{name}")
    async def value(name: str) -> dict[str, object]:
        logger = await get_logger("business")
        assert not logger.isEnabledFor(logging.DEBUG)
        if name == "muted":
            with use_verbose(logger, enabled=False):
                result = await get_config(f"business.{name}")
        else:
            result = await get_config(f"business.{name}")
        logger.log_event("value ready", value=result)
        return {"value": result}

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local"
        ) as client:
            assert (await client.get(CONTROL)).status_code == 403
            assert (await client.put(CONTROL, json={"enabled": True})).status_code == 403
            invalid_values: tuple[object, ...] = (
                "true",
                1,
                None,
                list[object](),
                dict[str, object](),
            )
            for invalid in invalid_values:
                response = await client.put(CONTROL, headers=TOKEN, json={"enabled": invalid})
                assert response.status_code == 422
            initial = (await client.get(CONTROL, headers=TOKEN)).json()
            assert initial["enabled"] is False and initial["revision"] == 0
            assert initial["processes"][0]["role"] == "api"
            changed = await client.put(CONTROL, headers=TOKEN, json={"enabled": True})
            assert changed.status_code == 202
            assert changed.json() == {"enabled": True, "revision": 1}
            assert (await set_file_verbose(True)).revision == 1
            assert (await client.get("/value/enabled")).json() == {"value": 42}
            assert (await client.get("/value/muted")).json() == {"value": 7}
            assert (await set_file_verbose(False)).revision == 2
            assert (await client.get("/value/disabled")).json() == {"value": 12}
            assert CONTROL not in app.openapi()["paths"]
            assert (await get_file_verbose()).enabled is False
    logs = content(configuration)
    assert "business.enabled" in logs and "business.disabled" not in logs
    assert "business.muted" not in logs
    assert "value ready\n    value: 42" in logs
    assert "handler=test_file_verbose." in logs


@pytest.mark.parametrize("initial", [False, True])
async def test_switch_preserves_running_requests_and_their_queued_record_policy(
    configuration: Path, observed_runtime: ObservedRuntime, initial: bool
) -> None:
    with configuration.open("a", encoding="utf-8") as stream:
        stream.write("business.old: 20 + 1\nbusiness.new: 20 + 2\n")
    entered, release = asyncio.Event(), asyncio.Event()
    app = LclFastAPI(config_path=configuration)

    @app.get("/old")
    async def old() -> dict[str, object]:
        entered.set()
        await release.wait()
        return {"value": await get_config("business.old")}

    @app.get("/new")
    async def new() -> dict[str, object]:
        return {"value": await get_config("business.new")}

    async with app.router.lifespan_context(app):
        await set_file_verbose(initial)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local"
        ) as client:
            pending = asyncio.create_task(client.get("/old"))
            await asyncio.wait_for(entered.wait(), 3)
            await set_file_verbose(not initial)
            assert (await client.get("/new")).json() == {"value": 22}
            release.set()
            assert (await pending).json() == {"value": 21}
    logs = content(configuration)
    assert ("business.old" in logs) is initial
    assert ("business.new" in logs) is not initial


async def test_file_control_rejects_use_outside_service_and_strict_python_inputs(
    configuration: Path, observed_runtime: ObservedRuntime
) -> None:
    with pytest.raises(RuntimeError, match="active service"):
        await get_file_verbose()
    with pytest.raises(RuntimeError, match="active service"):
        await set_file_verbose(True)
    app = LclFastAPI(config_path=configuration)
    async with app.router.lifespan_context(app):
        with pytest.raises(TypeError, match="Boolean"):
            await set_file_verbose("true")  # type: ignore[arg-type]


async def test_stream_and_child_task_retain_entry_policy_during_a_switch(
    configuration: Path, observed_runtime: ObservedRuntime
) -> None:
    with configuration.open("a", encoding="utf-8") as stream:
        stream.write("business.stream: 40 + 2\n")
    entered, release = asyncio.Event(), asyncio.Event()
    app = LclFastAPI(config_path=configuration)

    async def events() -> AsyncIterator[str]:
        yield "data: started\n\n"
        entered.set()
        await release.wait()
        assert await asyncio.create_task(get_config("business.stream")) == 42
        yield "data: completed\n\n"

    @app.get("/events")
    async def stream_events() -> StreamingResponse:
        return StreamingResponse(events(), media_type="text/event-stream")

    async with app.router.lifespan_context(app):
        await set_file_verbose(True)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local"
        ) as client:
            pending = asyncio.create_task(client.get("/events"))
            await asyncio.wait_for(entered.wait(), 3)
            await set_file_verbose(False)
            release.set()
            response = await pending
            assert response.status_code == 200
            assert response.text == "data: started\n\ndata: completed\n\n"
    logs = content(configuration)
    assert "business.stream" in logs
    assert logs.count("REST API started") == logs.count("REST API completed") == 1


async def test_http_control_reports_storage_failure_without_accepting_change(
    configuration: Path, observed_runtime: ObservedRuntime
) -> None:
    app = LclFastAPI(config_path=configuration)
    async with app.router.lifespan_context(app):
        (observed_runtime.directory / "logging-control.json").write_text(
            "invalid", encoding="utf-8"
        )
        assert app.health_sampler is not None
        await app.health_sampler.refresh()
        await app.health_sampler.refresh()
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://local"
        ) as client:
            assert (await client.get(CONTROL, headers=TOKEN)).status_code == 503
            assert (
                await client.put(CONTROL, headers=TOKEN, json={"enabled": True})
            ).status_code == 503
    assert content(configuration).count("file verbose refresh failed") == 1
