"""Observe explicit, masked business events and deferred native writer formatting."""

import asyncio
import inspect
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from lclang.logger import LoggerHandlerConfig, use_logger_handler

from lcl_fastapi import get_logger
from lcl_fastapi.diagnostics import install_service_formatters


def event_logs(directory: Path) -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in directory.glob("*.log"))


@dataclass
class EventRecord:
    order_id: int
    secret: str

    def __getattribute__(self, name: str) -> Any:
        if name == "secret":
            raise AssertionError("masked field was read")
        return object.__getattribute__(self, name)


async def test_event_fields_masking_callsite_and_deferred_message_objects(tmp_path: Path) -> None:
    threads: list[str] = []

    class Message:
        def __str__(self) -> str:
            threads.append(threading.current_thread().name)
            return "deferred message"

    config = LoggerHandlerConfig(
        level="INFO", console={"enabled": False}, file={"service": {"directory": tmp_path}}
    )
    async with use_logger_handler(config) as runtime:
        install_service_formatters(runtime, config)
        logger = await get_logger("orders")
        logger.info(Message())
        frame = inspect.currentframe()
        assert frame is not None
        line = frame.f_lineno + 1
        logger.log_event(
            "order\nready",
            record=EventRecord(42, "never read"),
            fields=("order_id", "secret"),
            masked_fields=("secret", "password"),
            password="never print",
            note="a\nb",
        )
        logger.log_event("empty event")
        logger.log_event("disabled", level=logging.DEBUG, record=object(), fields=("unknown",))
    logs = await asyncio.to_thread(event_logs, tmp_path)
    assert threads == ["lclang.logger.writer"]
    assert f"test_log_events.py:{line}" in logs
    assert "order\\nready\n    order_id: 42\n    secret  : *masked*" in logs
    assert "password: *masked*" in logs and "note    : 'a\\nb'" in logs
    assert "never read" not in logs and "never print" not in logs
    assert "empty event" in logs and "disabled" not in logs


@pytest.mark.parametrize(
    "record,fields,values,error",
    [
        (object(), (), {}, TypeError),
        (EventRecord, (), {}, TypeError),
        (None, ("missing",), {}, ValueError),
        (EventRecord(1, "secret"), ("order_id", "order_id"), {}, ValueError),
        (EventRecord(1, "secret"), ("order_id",), {"order_id": 2}, ValueError),
    ],
)
async def test_enabled_events_reject_invalid_selection(
    record: object, fields: tuple[str, ...], values: dict[str, Any], error: type[Exception]
) -> None:
    config = LoggerHandlerConfig(console={"enabled": False}, file={})
    async with use_logger_handler(config):
        logger = await get_logger()
        with pytest.raises(error):
            logger.log_event("invalid", record=record, fields=fields, **values)


async def test_event_requires_text_and_propagates_unmasked_getter_failure() -> None:
    config = LoggerHandlerConfig(console={"enabled": False}, file={})
    async with use_logger_handler(config):
        logger = await get_logger()
        with pytest.raises(TypeError, match="event must be text"):
            logger.log_event(3)  # type: ignore[arg-type]
        with pytest.raises(AssertionError, match="masked field was read"):
            logger.log_event("read", record=EventRecord(1, "secret"), fields=("secret",))
        for masks in (("unknown",), ("value", "value")):
            with pytest.raises(ValueError, match="masks"):
                logger.log_event("invalid mask", masked_fields=masks, value=1)
