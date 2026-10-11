"""Verify configured diagnostics, task isolation and thread-owned logging scopes."""

import asyncio
import logging
import threading
from pathlib import Path

import httpx
import pytest
from lclang.logger import use_verbose
from playground_service.engine import Engine
from test_application import ObservedRuntime
from test_application import observed_runtime as observed_runtime

from lcl_fastapi import BackgroundWorkerContext, LclFastAPI, get_config, get_logger
from lcl_fastapi.cli.main import main
from lcl_fastapi.overrides import override_scope
from lcl_fastapi.runtime.controller import controller_config
from lcl_fastapi.runtime.state import process_identity
from lcl_fastapi.sources import configuration_frame

CONFIG = """app.name: "test-service"
app.version: "1"
app.target: "app:service"
logger.console.enabled: False
background_worker.job.auto_restart: False
business.api: 21 * 2
business.lifespan: "startup"
business.worker: "thread"
business.muted: "quiet"
business.cancelled: "quiet"
"""


@pytest.mark.parametrize("enabled", [False, True])
async def test_verbose_file_cli_and_each_worker_scope(
    tmp_path: Path,
    observed_runtime: ObservedRuntime,
    enabled: bool,
) -> None:
    source = tmp_path / "service.lclcfg"
    source.write_text(CONFIG + "business.trace_enabled: __verbose__\n", encoding="utf-8")
    observed_runtime.directory = tmp_path / "run"
    observed_runtime.identity = process_identity()
    observed_runtime.service = {"configured_workers": 1, "service_id": "test"}
    ready = threading.Event()

    def worker(context: BackgroundWorkerContext) -> None:
        assert context.run(context.get_config("business.worker")) == "thread"
        context.logger.info("worker complete")
        ready.set()

    with override_scope({"__verbose__": enabled}):
        config = await controller_config(source)
        assert config.level == (logging.DEBUG if enabled else logging.INFO)
        app = LclFastAPI(config_path=source, background_workers={"job": worker})

        @app.get("/value")
        async def value() -> dict[str, object]:
            logger = await get_logger("business")
            with use_verbose(logger, enabled=False):
                assert await get_config("business.muted") == "quiet"
            with use_verbose(logger, enabled=False):
                task = asyncio.create_task(get_config("business.cancelled"))
                assert await task == "quiet"
            logger.info("api complete")
            return {"value": await get_config("business.api")}

        async with app.router.lifespan_context(app):
            assert await get_config("business.trace_enabled") is enabled
            assert await get_config("business.lifespan") == "startup"
            assert await asyncio.to_thread(ready.wait, 5)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/value")
                assert response.json() == {"value": 42}
                request_id = response.headers["X-Request-ID"]
        content = "\n".join(
            path.read_text(encoding="utf-8") for path in (tmp_path / "logs").glob("*.log")
        )
        assert "api complete" in content and "worker complete" in content
        assert ("VERBOSE" in content) is enabled
        for name in ("business.api", "business.lifespan", "business.worker"):
            assert (name in content) is enabled
        assert "business.muted" not in content and "business.cancelled" not in content
        if enabled:
            assert any(
                "VERBOSE" in line and f"request_id={request_id}" in line
                for line in content.splitlines()
            )


@pytest.mark.parametrize("value", ["None", "1", '"true"', "True"])
def test_verbose_binding_is_framework_owned_before_sinks_open(
    tmp_path: Path, value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "service.lclcfg"
    source.write_text(CONFIG + f"__verbose__: {value}\n", encoding="utf-8")
    assert main(["serve", "-c", str(source)]) != 0
    assert "__verbose__" in capsys.readouterr().err
    assert not (tmp_path / "logs").exists()


async def test_playground_can_disable_trace_without_changing_evaluation() -> None:
    engine = Engine(1, 30, verbose=False)
    try:
        session = await engine.submit("create")
        await engine.submit("parse", session["id"], source="answer: 42", expression="answer")
        report = await engine.submit("evaluate", session["id"])
        assert report["result"] == {"type": "int", "repr": "42"}
        assert report["trace"] == []
    finally:
        await engine.close()


async def test_invalid_internal_verbose_transport_is_rejected(tmp_path: Path) -> None:
    source = tmp_path / "service.lclcfg"
    source.write_text(CONFIG, encoding="utf-8")
    with override_scope({"__verbose__": "true"}):
        with pytest.raises(ValueError, match="internal __verbose__ transport must be Boolean"):
            async with configuration_frame(source):
                pytest.fail("invalid runtime mode accepted")
