"""Verify native timezone settings through CLI, controller, API and background logs."""

import asyncio
import logging
import re
import threading
from datetime import datetime, timedelta, timezone, tzinfo
from pathlib import Path
from typing import Any, Self

import httpx
import pytest
from test_application import ObservedRuntime
from test_application import observed_runtime as observed_runtime

from lcl_fastapi import BackgroundWorkerContext, LclFastAPI, get_logger
from lcl_fastapi.cli.main import main
from lcl_fastapi.config import load_settings
from lcl_fastapi.runtime import common
from lcl_fastapi.runtime.controller import controller_logging
from lcl_fastapi.runtime.state import process_identity

CONFIG = """app.name: "test-service"
app.version: "1"
app.target: "app:service"
logger.console.enabled: False
logger.format: "%(asctime)s | %(message)s"
background_worker.job.auto_restart: False
docs.enabled: False
"""


class LocalDatetime(datetime):
    def astimezone(self, tz: tzinfo | None = None) -> Self:
        assert tz is None
        return super().astimezone(timezone(timedelta(hours=8)))


@pytest.mark.parametrize(
    "source_suffix, arguments, policy, timestamp",
    [
        ("", [], "local", "1970-01-01T08:00:00.125000+08:00"),
        ('logger.timezone: "utc"\n', [], "utc", "1970-01-01T00:00:00.125000Z"),
        (
            'logger.timezone: "utc"\n',
            ["-o", "logger.timezone", "local"],
            "local",
            "1970-01-01T08:00:00.125000+08:00",
        ),
        (
            'logger.timezone: "local"\n',
            ["-o", "logger.timezone", "utc"],
            "utc",
            "1970-01-01T00:00:00.125000Z",
        ),
    ],
)
def test_timezone_reaches_each_service_role(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    observed_runtime: ObservedRuntime,
    capsys: pytest.CaptureFixture[str],
    source_suffix: str,
    arguments: list[str],
    policy: str,
    timestamp: str,
) -> None:
    source = tmp_path / "service.lclcfg"
    source.write_text(CONFIG + source_suffix, encoding="utf-8")
    original_factory = logging.getLogRecordFactory()

    def fixed_record(*args: Any, **kwargs: Any) -> logging.LogRecord:
        record = original_factory(*args, **kwargs)
        record.created = 0.125
        return record

    monkeypatch.setattr(logging, "_logRecordFactory", fixed_record)
    monkeypatch.setattr("lclang.logger.formatter.datetime", LocalDatetime)
    observed_runtime.directory = tmp_path / "run"
    observed_runtime.identity = process_identity()
    observed_runtime.service = {"configured_workers": 1, "service_id": "test"}
    ready = threading.Event()

    def background(context: BackgroundWorkerContext) -> None:
        assert context.run(context.get_config("logger.timezone")) == policy
        context.logger.info("timezone background")
        ready.set()

    async def worker(path: Path) -> None:
        app = LclFastAPI(config_path=path, background_workers={"job": background})

        @app.get("/timezone")
        async def route() -> dict[str, str]:
            (await get_logger("business")).info("timezone api")
            return {"timezone": policy}

        async with app.router.lifespan_context(app):
            assert await asyncio.to_thread(ready.wait, 5)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                response = await client.get("/timezone")
                assert response.status_code == 200
                assert response.json() == {"timezone": policy}
                assert response.headers["X-Request-ID"]

    def serve(path: Path) -> None:
        settings = asyncio.run(load_settings(path))
        with controller_logging(path, settings, process_identity() | {"service_id": "test"}):
            pass
        asyncio.run(worker(path))

    monkeypatch.setattr(common, "serve", serve)
    assert main(["serve", "-c", str(source), *arguments]) == 0
    assert capsys.readouterr().out == ""
    files = list((tmp_path / "logs").glob("*.log"))
    for marker, pattern in (
        ("service started", "*.controller.*.log"),
        ("timezone api", "test-service.[0-9]*.log"),
        ("timezone background", "*.job.*.log"),
    ):
        lines = [
            line
            for path in (tmp_path / "logs").glob(pattern)
            for line in path.read_text(encoding="utf-8").splitlines()
            if marker in line
        ]
        assert len(lines) == 1
        assert lines[0].split(" | ")[0] == timestamp
    assert files and all(re.search(r"\.\d{8}T\d{6}\.\d{6}Z\.\d+\.log$", p.name) for p in files)


@pytest.mark.parametrize("value", ['"UTC"', '"Asia/Shanghai"', "None", "True", "1"])
def test_invalid_timezone_has_diagnostics_without_opening_sinks(
    tmp_path: Path, value: str, capsys: pytest.CaptureFixture[str]
) -> None:
    source = tmp_path / "service.lclcfg"
    source.write_text(CONFIG + f"logger.timezone: {value}\n", encoding="utf-8")
    assert main(["serve", "-c", str(source)]) != 0
    result = capsys.readouterr()
    assert result.out == ""
    assert "logger.timezone" in result.err and "service.lclcfg" in result.err
    assert not (tmp_path / "logs").exists()
