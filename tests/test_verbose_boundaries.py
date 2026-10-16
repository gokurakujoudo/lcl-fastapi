"""Exercise diagnostic routing, failure deduplication and cancellation cleanup."""

import asyncio
import io
import logging
import threading
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from lclang.lang import LCL_IMPORTS, Frame, Module, ModuleName, parse_expression
from lclang.logger import LoggerHandlerConfig, use_logger_handler
from test_application import ObservedRuntime
from test_application import configuration as configuration
from test_application import observed_runtime as observed_runtime

from lcl_fastapi import FileVerboseState, LclFastAPI, get_logger
from lcl_fastapi.diagnostics import install_service_formatters
from lcl_fastapi.runtime.controller_writer import ControllerWriter
from lcl_fastapi.verbose.control import FileVerboseControl, use_file_control
from lcl_fastapi.verbose.policy import install_verbose_output, use_service_verbose
from lcl_fastapi.verbose.storage import write_target
from lcl_fastapi.verbose.submission import finish_file_update


def read_logs(directory: Path) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in directory.glob("*.log"))


@pytest.mark.parametrize("cli_enabled,file_enabled", [(True, False), (False, True)])
async def test_console_and_file_policies_are_independent_with_source_filters(
    tmp_path: Path, cli_enabled: bool, file_enabled: bool
) -> None:
    write_target(tmp_path, "current", FileVerboseState(file_enabled, 0))
    control = FileVerboseControl(tmp_path, "current", "api")
    console = io.StringIO()
    config = LoggerHandlerConfig(
        level="DEBUG" if cli_enabled else "INFO",
        console={"stream": console, "level": "DEBUG"},
        file={
            "service": {
                "directory": tmp_path,
                "logger_names": ("business.allowed",),
                "level": "DEBUG" if cli_enabled else "INFO",
            }
        },
    )
    module = Module(ModuleName("diagnostics"), {"value": parse_expression("6 * 7")})
    with use_file_control(control):
        async with use_logger_handler(config) as runtime:
            install_service_formatters(runtime, config)
            install_verbose_output(runtime, config, cli_enabled)
            logger = await get_logger("business.allowed")
            async with Frame(module, parent=LCL_IMPORTS) as frame:
                with use_service_verbose(logger, cli_enabled):
                    assert await frame.get("value") == 42
            denied = await get_logger("business.denied")
            async with Frame(module, parent=LCL_IMPORTS) as frame:
                with use_service_verbose(denied, cli_enabled):
                    assert await frame.get("value") == 42
            bypass = logging.Logger("business.allowed", logging.DEBUG)
            bypass.addHandler(runtime.handler)
            bypass.debug("ordinary third-party debug")
            if not cli_enabled:
                bypass.debug("unscoped diagnostics are disabled", extra={"lclang_verbose": True})
    logs = await asyncio.to_thread(read_logs, tmp_path)
    assert ("Evaluation tree" in logs) is file_enabled
    assert ("Evaluation tree" in console.getvalue()) is cli_enabled
    assert logs.count("Evaluation tree") == (1 if file_enabled else 0)
    assert console.getvalue().count("Evaluation tree") == (2 if cli_enabled else 0)
    assert "business.denied" not in logs
    assert "unscoped diagnostics are disabled" not in logs
    assert ("ordinary third-party debug" in logs) is cli_enabled


async def test_reported_exception_suppresses_one_runtime_duplicate_only(
    configuration: Path, observed_runtime: ObservedRuntime
) -> None:
    error = ValueError("shared failure")
    app = LclFastAPI(config_path=configuration)

    @app.get("/failure")
    async def failure() -> None:
        raise error

    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
            base_url="http://local",
        ) as client:
            assert (await client.get("/failure")).status_code == 500
        logger = logging.getLogger("uvicorn.error")
        logger.error("duplicate runtime report", exc_info=(type(error), error, error.__traceback__))
        logger.error(
            "subsequent runtime report", exc_info=(type(error), error, error.__traceback__)
        )
        logger.error("unrelated runtime report", exc_info=ValueError("different failure"))
    logs = await asyncio.to_thread(read_logs, configuration.parent / "logs")
    assert logs.count("Uncaught Exception in HTTP") == 1
    assert "duplicate runtime report" not in logs
    assert "subsequent runtime report" in logs and "unrelated runtime report" in logs
    runtime_header = next(line for line in logs.splitlines() if "subsequent runtime report" in line)
    assert "test_verbose_boundaries.py:" not in runtime_header


async def test_queued_diagnostics_keep_the_policy_captured_before_a_switch(tmp_path: Path) -> None:
    entered, release = threading.Event(), threading.Event()

    class SlowMessage:
        def __str__(self) -> str:
            entered.set()
            assert release.wait(5)
            return "writer unblocked"

    write_target(tmp_path, "current", FileVerboseState(True, 0))
    control = FileVerboseControl(tmp_path, "current", "api")
    config = LoggerHandlerConfig(
        console={"enabled": False}, file={"service": {"directory": tmp_path}}
    )
    module = Module(ModuleName("queued"), {"value": parse_expression("6 * 7")})
    with use_file_control(control):
        async with use_logger_handler(config) as runtime:
            install_service_formatters(runtime, config)
            install_verbose_output(runtime, config, False)
            logger = await get_logger("business")
            logger.info(SlowMessage())
            try:
                assert await asyncio.to_thread(entered.wait, 3)
                async with Frame(module, parent=LCL_IMPORTS) as frame:
                    with use_service_verbose(logger, False):
                        assert await frame.get("value") == 42
                assert control.update(False) == FileVerboseState(False, 1)
            finally:
                release.set()
    logs = await asyncio.to_thread(read_logs, tmp_path)
    assert "writer unblocked" in logs and "Evaluation tree" in logs


@pytest.mark.parametrize("fail", [False, True])
async def test_cancelled_submission_drains_the_thread_and_preserves_failures(fail: bool) -> None:
    entered, release = threading.Event(), threading.Event()
    committed: list[bool] = []

    def update(enabled: bool) -> FileVerboseState:
        entered.set()
        assert release.wait(5)
        if fail:
            raise OSError("failed atomic update")
        committed.append(enabled)
        return FileVerboseState(enabled, 1)

    operation = asyncio.create_task(finish_file_update(update, True))
    assert await asyncio.to_thread(entered.wait, 3)
    operation.cancel()
    await asyncio.sleep(0)
    assert not operation.done()
    release.set()
    with pytest.raises(asyncio.CancelledError) as cancelled:
        await operation
    if fail:
        assert isinstance(cancelled.value.__cause__, OSError)
    else:
        assert committed == [True]


async def test_business_lifespan_can_suppress_a_failure_at_its_own_boundary(
    configuration: Path, observed_runtime: ObservedRuntime
) -> None:
    events: list[str] = []

    @asynccontextmanager
    async def business(app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        except ValueError:
            events.append("suppressed")

    app = LclFastAPI(config_path=configuration, lifespan=business)
    async with app.router.lifespan_context(app):
        raise ValueError("business cleanup policy")
    assert events == ["suppressed"] and app.worker_requests is None
    writer = ControllerWriter(LoggerHandlerConfig(console={"enabled": False}, file={}))
    with pytest.raises(RuntimeError, match="not active"):
        writer.emit([])
